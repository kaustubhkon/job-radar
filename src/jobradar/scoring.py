"""Rule-based score (0-100). Always on; the LLM pass blends with it when enabled."""

from __future__ import annotations

import re
from datetime import datetime

from .config import Config
from .models import Posting

UNPAID = re.compile(r"\b(unpaid|volunteer|for academic credit only|no compensation)\b", re.I)
PAID = re.compile(r"(\$\s?\d|\bpaid\b|\bstipend\b|\bhourly rate\b|\bper hour\b|/hr\b|\bcompensation\b)", re.I)


def rule_score(p: Posting, cfg: Config, now: datetime) -> float:
    fam = cfg.role_families.get(p.family or "")
    weight = fam.weight if fam else 0.5
    score = (40 if p.family_match == "title" else 18) * weight

    if p.on_watchlist:
        score += 12
    score += {"preferred": 15, "accepted": 8, "unclear": 3}.get(p.location_tier or "", 0)

    if p.posted_at:
        age = (now - p.posted_at).total_seconds() / 86400
        score += 10 if age <= 3 else 6 if age <= 7 else 3 if age <= 30 else 0
    else:
        score += 4

    score += {"summer_internship": 5, "offcycle_internship": 4, "part_time": 4, "new_grad": 0}.get(p.posting_type or "", 0)

    text = f"{p.compensation or ''} {p.description or ''}"
    if UNPAID.search(text):
        score -= 15
    elif p.compensation or PAID.search(text):
        score += 5

    score += p.class_year_adj
    return max(0.0, min(100.0, round(score, 1)))


def apply_rule_scores(postings: list[Posting], cfg: Config, now: datetime, watchlist_names: set[str]) -> None:
    from .normalize import normalize_company

    for p in postings:
        p.on_watchlist = normalize_company(p.company) in watchlist_names
        p.rule_score = rule_score(p, cfg, now)
        p.score = p.rule_score
        p.category = cfg.category_label(p.family)
        if not p.reason and p.class_year_note:
            p.reason = p.class_year_note
