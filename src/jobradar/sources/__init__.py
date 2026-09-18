"""Source registry. To add a source, implement `Source` and list it here."""

from __future__ import annotations

from ..config import Config
from .ats import Ashby, Greenhouse, Lever, SmartRecruiters, Workable
from .base import Context, Http, Source, run_sources
from .enterprise import Eightfold, OracleHCM, Workday
from .github_lists import GithubLists
from .gmail_alerts import GmailAlerts
from .jobspy_source import JobSpySource

WATCHLIST_SOURCES = {
    s.ats: s for s in (Greenhouse(), Lever(), Ashby(), SmartRecruiters(), Workable(), Workday(), OracleHCM(), Eightfold())
}


def all_sources(cfg: Config) -> list[Source]:
    sources: list[Source] = list(WATCHLIST_SOURCES.values())
    sources.append(GithubLists())
    sources += [JobSpySource(site) for site in cfg.sources.jobspy.sites]
    sources.append(GmailAlerts())
    return sources


def enabled_sources(cfg: Config) -> list[Source]:
    return [s for s in all_sources(cfg) if s.enabled(cfg)]


__all__ = ["Context", "Http", "Source", "WATCHLIST_SOURCES", "all_sources", "enabled_sources", "run_sources"]
