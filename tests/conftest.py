from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from jobradar.config import REPO_ROOT, Config
from jobradar.models import RawPosting
from jobradar.normalize import normalize

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc)  # a Friday


def load_fixture(name: str):
    return json.loads((FIXTURES / "sources" / name).read_text())


@pytest.fixture
def cfg() -> Config:
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text())
    raw["seed_watchlist"] = False
    raw["ranking"]["llm"] = False
    return Config.model_validate(raw)


def make(title: str, company: str = "Acme", location: str | None = "Austin, TX", source: str = "greenhouse",
         description: str | None = None, url: str | None = None, **kw) -> RawPosting:
    return RawPosting(
        title=title,
        company=company,
        location=location,
        source=source,
        description=description,
        url=url or f"https://job-boards.greenhouse.io/acme/jobs/{abs(hash((title, company, location))) % 10**8}",
        **kw,
    )


def posting(title: str, **kw):
    return normalize(make(title, **kw), NOW)
