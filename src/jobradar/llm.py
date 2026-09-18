"""Optional Claude pass: category, fit score and a one-line reason per posting."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import Config
from .models import Posting, RunReport

SYSTEM = """You screen internship postings for one student and return strict JSON.

For each posting decide:
- category: exactly one of {categories}
- fit: 0-100, how well this role matches the student's goals and background (not how prestigious it is)
- eligible: false only if the posting clearly excludes this student (wrong graduation year, wrong degree level,
  requires work authorization or location the student can't meet, not actually an internship/part-time student role)
- reason: at most 20 words, specific to this student and this role. No generic praise.

Respond with only a JSON array, one object per posting, in the same order:
[{{"id": "...", "category": "...", "fit": 0, "eligible": true, "reason": "..."}}]"""


class Verdict(BaseModel):
    id: str
    category: str
    fit: float = Field(ge=0, le=100)
    eligible: bool = True
    reason: str = ""


def _profile_text(cfg: Config) -> str:
    p = cfg.profile
    return (
        f"Student: {p.school or ''} {', '.join(p.majors)} {p.class_standing}, graduating {p.grad_year}, "
        f"degree: {p.degree}.\nGoals: {p.blurb.strip()}\n"
        f"Preferred locations: {', '.join(cfg.locations.preferred + cfg.locations.accepted)}."
    )


def _posting_text(p: Posting) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "company": p.company,
        "location": p.location_label(),
        "season": p.season,
        "description": (p.description or "")[:1500],
    }


def _parse(text: str, ids: list[str], categories: list[str]) -> dict[str, Verdict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in response")
    items = json.loads(m.group(0))
    out: dict[str, Verdict] = {}
    for item in items:
        v = Verdict.model_validate(item)
        if v.id not in ids:
            continue
        if v.category not in categories:
            v.category = "Other"
        v.reason = " ".join(v.reason.split()[:20])
        out[v.id] = v
    return out


async def _score_batch(client, cfg: Config, batch: list[Posting], categories: list[str]) -> dict[str, Verdict]:
    ids = [p.id for p in batch]
    user = _profile_text(cfg) + "\n\nPostings:\n" + json.dumps([_posting_text(p) for p in batch], ensure_ascii=False)
    last_err: Exception | None = None
    for _ in range(2):
        try:
            resp = await client.messages.create(
                model=cfg.ranking.model,
                max_tokens=4000,
                system=SYSTEM.format(categories=" | ".join(categories)),
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return _parse(text, ids, categories)
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            last_err = e
    raise RuntimeError(f"bad LLM output: {last_err}")


def llm_available(cfg: Config) -> bool:
    return cfg.ranking.llm and bool(os.environ.get("ANTHROPIC_API_KEY"))


async def llm_rank(postings: list[Posting], cfg: Config, report: RunReport) -> list[Posting]:
    """Blend LLM fit into scores. Returns postings the model judged ineligible (to be dropped)."""
    if not postings or not llm_available(cfg):
        return []
    try:
        import anthropic
    except ImportError:
        report.notes.append("LLM ranking skipped: install the 'llm' extra (anthropic).")
        return []

    cap = cfg.ranking.llm_max_postings
    ordered = sorted(postings, key=lambda p: p.rule_score, reverse=True)
    to_score, rest = ordered[:cap], ordered[cap:]
    if rest:
        report.notes.append(
            f"LLM ranked the top {cap} by rule score; {len(rest)} more are ranked by rule score only."
        )
    categories = cfg.categories()
    client = anthropic.AsyncAnthropic()
    size = cfg.ranking.llm_batch_size
    batches = [to_score[i : i + size] for i in range(0, len(to_score), size)]
    sem = asyncio.Semaphore(4)

    async def run(batch):
        async with sem:
            try:
                return await _score_batch(client, cfg, batch, categories)
            except Exception as e:  # noqa: BLE001 - any failure falls back to rule score
                report.notes.append(f"LLM batch failed ({type(e).__name__}); used rule score for {len(batch)} postings.")
                return {}

    results = await asyncio.gather(*(run(b) for b in batches))
    verdicts: dict[str, Verdict] = {}
    for r in results:
        verdicts.update(r)

    rejected = []
    w = cfg.ranking.rule_weight
    for p in to_score:
        v = verdicts.get(p.id)
        if not v:
            continue
        p.llm_fit = v.fit
        p.category = v.category if v.category in categories else p.category
        p.reason = v.reason or p.reason
        p.score = round(w * p.rule_score + (1 - w) * v.fit, 1)
        if not v.eligible:
            report.drop(p, "llm_ineligible", v.reason)
            rejected.append(p)
    return rejected
