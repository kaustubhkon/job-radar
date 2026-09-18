"""Loading and validating config.yaml."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_WATCHLIST = REPO_ROOT / "data" / "seed_watchlist.yaml"

ATS = Literal["greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "oracle", "eightfold"]
PostingType = Literal["summer_internship", "offcycle_internship", "part_time", "new_grad"]
Standing = Literal["freshman", "sophomore", "junior", "senior", "graduate"]

DEFAULT_CATEGORY_LABELS = {
    "finance": "Finance",
    "startup_ops": "Startup Ops",
    "growth_gtm": "Growth/GTM",
    "consulting": "Consulting",
    "pm": "PM",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(Strict):
    name: str
    email_to: str | None = None  # defaults to the sending account (SMTP_USER) when blank
    school: str | None = None
    majors: list[str] = Field(default_factory=list)
    grad_year: int
    class_standing: Standing
    degree: Literal["bachelors", "masters", "mba", "phd"] = "bachelors"
    blurb: str = ""


class Locations(Strict):
    preferred: list[str] = Field(default_factory=list)
    accepted: list[str] = Field(default_factory=list)
    part_time_locations: list[str] = Field(default_factory=list)
    # What to do with postings whose location can't be parsed ("Multiple Locations", missing, etc.)
    unknown: Literal["keep", "drop"] = "keep"


class RoleFamily(Strict):
    weight: float = 1.0
    keywords: list[str]
    label: str | None = None


class WatchlistEntry(Strict):
    name: str
    ats: ATS
    slug: str | None = None  # greenhouse, lever, ashby, smartrecruiters, workable
    tenant: str | None = None  # workday
    wd: str | None = None  # workday, e.g. "wd1"
    site: str | None = None  # workday career site, oracle site number (CX_1001)
    host: str | None = None  # oracle (jpmc.fa.oraclecloud.com) / eightfold (morganstanley.eightfold.ai)
    domain: str | None = None  # eightfold, e.g. "morganstanley.com"

    @field_validator("slug", "tenant", "wd", "site", "host", "domain", mode="before")
    @classmethod
    def _as_str(cls, v):
        return str(v) if isinstance(v, (int, float)) else v  # e.g. Citi's Workday site is "2"

    @model_validator(mode="after")
    def _required_fields(self) -> WatchlistEntry:
        needs = {
            "workday": ("tenant", "wd", "site"),
            "oracle": ("host", "site"),
            "eightfold": ("host", "domain"),
        }.get(self.ats, ("slug",))
        missing = [f for f in needs if not getattr(self, f)]
        if missing:
            raise ValueError(f"{self.name}: ats '{self.ats}' needs {', '.join(missing)}")
        return self

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.slug or self.tenant or self.host}:{self.site or ''}"


class JobSpyConfig(Strict):
    enabled: bool = False
    sites: list[Literal["indeed", "linkedin", "google", "glassdoor", "zip_recruiter"]] = Field(
        default_factory=lambda: ["indeed"]
    )
    search_terms: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=lambda: ["United States"])
    results_wanted: int = 25
    # Minimum lookback. The real window grows to cover the time since the last successful run.
    hours_old: int = 36


class GmailAlertsConfig(Strict):
    enabled: bool = False
    query: str = "from:(joinhandshake.com OR jobalerts-noreply@linkedin.com)"


class Sources(Strict):
    greenhouse: bool = True
    lever: bool = True
    ashby: bool = True
    workday: bool = True
    smartrecruiters: bool = True
    workable: bool = True
    oracle: bool = True
    eightfold: bool = True
    github_lists: bool = True
    jobspy: JobSpyConfig = Field(default_factory=JobSpyConfig)
    gmail_alerts: GmailAlertsConfig = Field(default_factory=GmailAlertsConfig)


class Ranking(Strict):
    llm: bool = True
    model: str = "claude-haiku-4-5"
    top_pick_threshold: int = 75
    top_picks_max: int = 5
    max_per_digest: int = 30
    llm_max_postings: int = 150
    llm_batch_size: int = 15
    rule_weight: float = 0.4  # final = rule_weight * rule + (1 - rule_weight) * llm_fit
    backfill_cap: int = 40


class Email(Strict):
    provider: Literal["smtp", "resend"] = "smtp"
    send_when_empty: Literal["always", "never", "mondays"] = "mondays"
    from_name: str = "Job Radar"
    from_address: str | None = None  # resend only; must be on your verified domain
    timezone: str = "America/Chicago"  # for dates shown in the email


class Lookback(Strict):
    default_hours: int = 36  # used on the very first run
    buffer_hours: int = 6  # overlap added to "time since last successful run"
    max_days: int = 7


class Config(Strict):
    profile: Profile
    posting_types: list[PostingType] = Field(
        default_factory=lambda: ["summer_internship", "offcycle_internship", "part_time"]
    )
    seasons: list[str] = Field(default_factory=list)  # empty = any season
    locations: Locations = Field(default_factory=Locations)
    role_families: dict[str, RoleFamily]
    exclude_title_keywords: list[str] = Field(default_factory=list)
    seed_watchlist: bool = True
    watchlist: list[WatchlistEntry] = Field(default_factory=list)
    github_lists: list[str] = Field(default_factory=list)
    sources: Sources = Field(default_factory=Sources)
    ranking: Ranking = Field(default_factory=Ranking)
    email: Email = Field(default_factory=Email)
    lookback: Lookback = Field(default_factory=Lookback)
    prune_days: int = 120

    @field_validator("watchlist", "github_lists", "exclude_title_keywords", "seasons", "posting_types", mode="before")
    @classmethod
    def _empty_list(cls, v):
        return [] if v is None else v

    @field_validator("seasons")
    @classmethod
    def _season_format(cls, v: list[str]) -> list[str]:
        import re

        for s in v:
            if not re.fullmatch(r"(summer|fall|winter|spring)-20\d\d|year-round", s):
                raise ValueError(f"season '{s}' should look like 'summer-2027' or 'year-round'")
        return v

    @field_validator("github_lists")
    @classmethod
    def _repo_format(cls, v: list[str]) -> list[str]:
        for r in v:
            if r.count("/") != 1:
                raise ValueError(f"github list '{r}' should look like 'owner/repo'")
        return v

    def category_label(self, family: str | None) -> str:
        if not family:
            return "Other"
        fam = self.role_families.get(family)
        if fam and fam.label:
            return fam.label
        return DEFAULT_CATEGORY_LABELS.get(family, family.replace("_", " ").title())

    def categories(self) -> list[str]:
        labels = [self.category_label(f) for f in self.role_families]
        return labels + (["Other"] if "Other" not in labels else [])

    def all_watchlist(self) -> list[WatchlistEntry]:
        entries = list(self.watchlist)
        if self.seed_watchlist and SEED_WATCHLIST.exists():
            entries += load_watchlist_file(SEED_WATCHLIST)
        seen: set[str] = set()
        out = []
        for e in entries:
            if e.key not in seen:
                seen.add(e.key)
                out.append(e)
        return out

    def filter_hash(self) -> str:
        """Hash of everything that affects which postings match. Used to re-check old rejects."""
        relevant = self.model_dump(
            include={"profile", "posting_types", "seasons", "locations", "role_families", "exclude_title_keywords"}
        )
        relevant["profile"].pop("email_to", None)
        return hashlib.sha1(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:12]


class ConfigError(Exception):
    pass


def load_watchlist_file(path: Path) -> list[WatchlistEntry]:
    data = yaml.safe_load(path.read_text()) or []
    if isinstance(data, dict):
        data = data.get("watchlist", [])
    return [WatchlistEntry.model_validate(e) for e in data]


def _format_errors(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        where = ".".join(str(p) for p in e["loc"]) or "(top level)"
        msg = e["msg"].removeprefix("Value error, ")
        if e["type"] == "extra_forbidden":
            msg = "unknown setting (typo?)"
        lines.append(f"  - {where}: {msg}")
    return "\n".join(lines)


def resolve_config_path(path: str | Path | None) -> Path:
    if path:
        return Path(path)
    for candidate in (Path("config.yaml"), REPO_ROOT / "config.yaml"):
        if candidate.exists():
            return candidate
    for candidate in (Path("config.example.yaml"), REPO_ROOT / "config.example.yaml"):
        if candidate.exists():
            print("warning: config.yaml not found, using config.example.yaml. Copy it to config.yaml and edit it.")
            return candidate
    raise ConfigError("config.yaml not found. Copy config.example.yaml to config.yaml and edit it.")


def load_config(path: str | Path | None = None) -> Config:
    p = resolve_config_path(path)
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{p} is not valid YAML:\n{e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} is empty or not a mapping")
    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{p} has problems:\n{_format_errors(e)}") from e
