"""Command line: jobradar run | backfill | validate-config | validate-watchlist | preview-email | add-company."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import REPO_ROOT, Config, ConfigError, WatchlistEntry, load_config
from .models import RawPosting, RunReport, SourceResult


def _print_report(report: RunReport) -> None:
    print(f"\nmode: {report.mode}   lookback since: {report.lookback_since:%Y-%m-%d %H:%M} UTC" if report.lookback_since else "")
    for r in report.sources:
        status = f"ok   {r.count:>5} postings" if r.ok else f"FAIL {r.error}"
        print(f"  {r.name:<18} {status}  ({r.seconds}s){'  warnings: ' + str(len(r.warnings)) if r.warnings else ''}")
        for w in r.warnings[:5]:
            print(f"      - {w}")
        if len(r.warnings) > 5:
            print(f"      … {len(r.warnings) - 5} more")
    print(f"\n{report.collected} collected → {report.unique} unique → {report.new} new → {report.matched} matched → "
          f"{report.emailed} emailed (+{report.overflow} overflow)")
    if report.drop_counts:
        print("drops: " + ", ".join(f"{k}={v}" for k, v in sorted(report.drop_counts.items(), key=lambda x: -x[1])))
    for n in report.notes:
        print(f"note: {n}")


def cmd_run(args: argparse.Namespace, mode: str = "run") -> int:
    from .emailer import DeliveryError
    from .pipeline import run_pipeline

    cfg = load_config(args.config)
    preview = Path(args.preview) if args.dry_run else None
    try:
        result = asyncio.run(
            run_pipeline(
                cfg,
                Path(args.state_dir),
                mode="backfill" if (mode == "backfill" or getattr(args, "backfill", False)) else "run",
                dry_run=args.dry_run,
                use_llm=not args.no_llm,
                preview_path=preview,
            )
        )
    except DeliveryError as e:
        print(f"email failed: {e}\nState was not updated, so these postings will be retried next run.", file=sys.stderr)
        return 2
    _print_report(result.report)
    if result.email:
        print(f"\nsubject: {result.email.subject}")
        if args.dry_run and preview:
            print(f"preview written to {preview} (and {preview.with_suffix('.txt')})")
        elif result.report.email_sent:
            print("email sent")
    else:
        print("\nno email this run (nothing new; send_when_empty says skip)")
    return 1 if result.report.all_sources_failed else 0


def cmd_validate_config(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    from .filters import LocationRules

    problems = []
    try:
        LocationRules.from_config(cfg)
    except ValueError as e:
        problems.append(str(e))
    watch = cfg.all_watchlist()
    print(f"config OK: {len(cfg.role_families)} role families, {len(watch)} watchlist companies "
          f"({len(cfg.watchlist)} yours + seed), {len(cfg.github_lists)} GitHub lists")
    if cfg.sources.jobspy.enabled:
        print("note: jobspy is enabled. It scrapes sites whose terms prohibit automated access.")
    if args.online:
        import httpx

        for repo in cfg.github_lists:
            r = httpx.get(f"https://api.github.com/repos/{repo}", timeout=15)
            if r.status_code == 404:
                problems.append(f"GitHub list {repo} not found (renamed for the new cycle?)")
            elif r.status_code < 300:
                print(f"  github list {repo}: ok")
    for p in problems:
        print(f"problem: {p}")
    return 1 if problems else 0


async def _check_watchlist(cfg: Config, only: str | None) -> list[tuple[WatchlistEntry, int | None, str | None]]:
    from .sources import WATCHLIST_SOURCES, Context, Http

    http = Http()
    now = datetime.now(timezone.utc)
    ctx = Context(cfg=cfg, http=http, now=now, since=now)
    entries = [e for e in cfg.all_watchlist() if not only or only.lower() in e.name.lower()]

    async def one(e: WatchlistEntry):
        try:
            posts = await asyncio.wait_for(WATCHLIST_SOURCES[e.ats].fetch_board(ctx, e), 120)
            return e, len(posts), None
        except Exception as ex:  # noqa: BLE001
            from .sources.base import describe_error

            return e, None, describe_error(ex)

    try:
        return list(await asyncio.gather(*(one(e) for e in entries)))
    finally:
        await http.aclose()


def cmd_validate_watchlist(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    results = asyncio.run(_check_watchlist(cfg, args.only))
    dead = 0
    for e, n, err in sorted(results, key=lambda x: (x[2] is None, x[0].name.lower())):
        if err:
            dead += 1
            print(f"  DEAD  {e.name:<28} {e.ats:<16} {err}")
        elif args.verbose:
            print(f"  ok    {e.name:<28} {e.ats:<16} {n} postings")
    print(f"\n{len(results) - dead} ok, {dead} dead")
    return 1 if dead else 0


SAMPLE_FILE = REPO_ROOT / "data" / "sample_postings.json"


def cmd_preview_email(args: argparse.Namespace) -> int:
    from .emailer.render import render_digest
    from .pipeline import process
    from .state import SeenStore

    cfg = load_config(args.config)
    raws = [RawPosting.model_validate(r) for r in json.loads(SAMPLE_FILE.read_text())]
    now = datetime.now(timezone.utc)
    report = RunReport(started_at=now, mode="preview")
    report.sources = [
        SourceResult(name="greenhouse", count=214), SourceResult(name="ashby", count=388),
        SourceResult(name="workday", count=57, warnings=["Example Corp: 404 not found"]),
        SourceResult(name="github_lists", count=15221),
        SourceResult(name="jobspy:linkedin", ok=False, error="429 rate-limited"),
    ]
    matched, _, _ = asyncio.run(process(raws, cfg, SeenStore(Path("/nonexistent/seen.json")), report, now, use_llm=False))
    cap = cfg.ranking.max_per_digest
    report.emailed, report.overflow = len(matched[:cap]), len(matched[cap:])
    email = render_digest(matched[:cap], cfg, report, now, overflow=len(matched[cap:]), kind=args.kind,
                          total_matched=len(matched))
    out = Path(args.out)
    out.write_text(email.html)
    out.with_suffix(".txt").write_text(f"Subject: {email.subject}\n\n{email.text}")
    print(f"subject: {email.subject}\nwrote {out} and {out.with_suffix('.txt')}  ({len(matched)} sample postings matched)")
    return 0


ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)")),
    ("oracle", re.compile(r"([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/sites/(\w+)")),
    ("eightfold", re.compile(r"([\w-]+\.eightfold\.ai)")),
]
SKIP_SLUGS = {"embed", "api", "v1", "jobs", "careers", "j", "en-US", "job_board"}


def detect_ats(text: str, name: str) -> WatchlistEntry | None:
    for ats, pat in ATS_PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            if ats == "workday":
                if g[2] in SKIP_SLUGS or g[2] == "wday":
                    continue
                return WatchlistEntry(name=name, ats="workday", tenant=g[0], wd=g[1], site=g[2])
            if ats == "oracle":
                return WatchlistEntry(name=name, ats="oracle", host=g[0], site=g[1])
            if ats == "eightfold":
                dom = re.search(r"domain=([\w.-]+)", text)
                domain = dom.group(1) if dom else f"{g[0].split('.')[0]}.com"
                return WatchlistEntry(name=name, ats="eightfold", host=g[0], domain=domain)
            if g[0] in SKIP_SLUGS:
                continue
            return WatchlistEntry(name=name, ats=ats, slug=g[0])
    return None


def cmd_add_company(args: argparse.Namespace) -> int:
    import httpx

    cfg = load_config(args.config)
    name = args.name or re.sub(r"^www\.", "", re.split(r"[/:]", re.sub(r"^https?://", "", args.url))[0]).split(".")[0].title()
    entry = detect_ats(args.url, name)
    if entry is None:
        try:
            html = httpx.get(args.url, follow_redirects=True, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (JobRadar add-company)"}).text
        except httpx.HTTPError as e:
            print(f"couldn't fetch {args.url}: {e}")
            return 1
        entry = detect_ats(html, name)
    if entry is None:
        print("Couldn't find a supported job board (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, "
              "Oracle HCM, Eightfold) on that page. Try the URL of an actual job posting.")
        return 1
    cfg.watchlist = [entry]
    cfg.seed_watchlist = False
    [(e, n, err)] = asyncio.run(_check_watchlist(cfg, None))
    line = yaml.safe_dump([entry.model_dump(exclude_none=True)], default_flow_style=True, sort_keys=False).strip()
    line = line.strip("[]")
    if err:
        print(f"Detected {entry.ats}, but the board didn't respond: {err}")
        print(f"Entry (check the slug): - {line}")
        return 1
    print(f"Found {entry.name} on {entry.ats} with {n} open postings. Add this under `watchlist:` in config.yaml:\n")
    print(f"  - {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobradar", description="Daily internship digest.")
    ap.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def run_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--state-dir", default="state", help="directory holding seen.json and runs/ (default: ./state)")
        p.add_argument("--dry-run", action="store_true", help="don't send or persist; write preview.html")
        p.add_argument("--no-llm", action="store_true", help="skip the Claude ranking pass")
        p.add_argument("--preview", default="preview.html")

    p = sub.add_parser("run", help="full pipeline, sends email")
    run_flags(p)
    p.add_argument("--backfill", action="store_true", help="seed state and send a capped backfill digest")
    p = sub.add_parser("backfill", help="seed state and send a capped backfill digest")
    run_flags(p)
    p = sub.add_parser("validate-config", help="schema check with readable errors")
    p.add_argument("--online", action="store_true", help="also check that GitHub list repos exist")
    p = sub.add_parser("validate-watchlist", help="hit each watchlist company once and report dead ones")
    p.add_argument("--only", help="only companies whose name contains this")
    p.add_argument("-v", "--verbose", action="store_true", help="also list healthy companies")
    p = sub.add_parser("preview-email", help="render the digest from sample data")
    p.add_argument("--out", default="preview.html")
    p.add_argument("--kind", default="digest", choices=["digest", "backfill", "empty", "failure"])
    p = sub.add_parser("add-company", help="detect a company's job board from a careers URL")
    p.add_argument("url")
    p.add_argument("--name")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "backfill":
            return cmd_run(args, mode="backfill")
        return {
            "validate-config": cmd_validate_config,
            "validate-watchlist": cmd_validate_watchlist,
            "preview-email": cmd_preview_email,
            "add-company": cmd_add_company,
        }[args.cmd](args)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
