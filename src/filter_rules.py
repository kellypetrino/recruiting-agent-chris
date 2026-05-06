"""Stage 1: cheap rule-based pre-filter.

Drops ~90% of jobs before any LLM call. Operates on the canonical JobPosting
(or Job ORM row) and returns a (passed: bool, reason: str) pair.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "target_companies.yaml"


@dataclass
class FilterResult:
    passed: bool
    reason: str


def load_filter_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f).get("filters", {})


# ── Title filtering ───────────────────────────────────────────────────────────

def _title_passes(title: str, include_kws: list[str], exclude_kws: list[str]) -> tuple[bool, str]:
    lower = title.lower()

    for kw in exclude_kws:
        if kw.lower() in lower:
            return False, f"title contains excluded keyword '{kw}'"

    for kw in include_kws:
        if kw.lower() in lower:
            return True, f"title matches '{kw}'"

    return False, "title matches no include keywords"


# ── Location filtering ────────────────────────────────────────────────────────

# Patterns we accept — NYC/NJ variants and US-remote signals
_NYC_NJ_RE = re.compile(
    r"\b(new york|nyc|manhattan|brooklyn|queens|bronx"
    r"|new jersey|secaucus|jersey city|newark|hoboken|parsippany|tinton falls|basking ridge"
    r"|roseland|morris plains|camden)\b",
    re.IGNORECASE,
)
_NY_ABBREV_RE = re.compile(r"\bny\b", re.IGNORECASE)
_NJ_ABBREV_RE = re.compile(r"\bnj\b", re.IGNORECASE)
_REMOTE_US_RE = re.compile(
    r"\b(remote[\s\-]?(us|united states|usa)?|remote.friendly)\b", re.IGNORECASE
)


def _location_passes(location: str, allowed_patterns: list[str]) -> tuple[bool, str]:
    """Return (passed, reason).

    Accept if location includes NYC/NJ or US-remote signal.
    Pass through if no location data — let the LLM decide.
    """
    if not location:
        return True, "no location data, passing through"

    if _NYC_NJ_RE.search(location):
        return True, "location includes NYC or NJ"

    if _NY_ABBREV_RE.search(location):
        return True, "location includes NY"

    if _NJ_ABBREV_RE.search(location):
        return True, "location includes NJ"

    if _REMOTE_US_RE.search(location):
        return True, "location includes US remote"

    # "United States" without a city = could have NY/NJ office; let LLM decide
    if re.search(r"\bunited states\b", location, re.IGNORECASE):
        return True, "location is United States (unspecified city)"

    return False, f"location '{location[:60]}' not NYC/NJ or US-remote"


# ── Age filtering ─────────────────────────────────────────────────────────────

def _age_passes(posted_date: datetime | None, max_age_days: int) -> tuple[bool, str]:
    if posted_date is None:
        return True, "no posted date, passing through"
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    if posted_date >= cutoff:
        return True, f"posted {(datetime.utcnow() - posted_date).days}d ago"
    return False, f"posted {(datetime.utcnow() - posted_date).days}d ago (>{max_age_days}d)"


# ── Main entry point ──────────────────────────────────────────────────────────

def apply_filter(
    title: str,
    location: str,
    posted_date: datetime | None,
    config: dict | None = None,
) -> FilterResult:
    if config is None:
        config = load_filter_config()

    include_kws = config.get("title_keywords_include", [])
    exclude_kws = config.get("title_keywords_exclude", [])
    max_age = config.get("max_age_days", 14)
    allowed_locs = config.get("locations_include", [])

    # Exclusion check first (fast reject)
    title_ok, title_reason = _title_passes(title, include_kws, exclude_kws)
    if not title_ok:
        return FilterResult(passed=False, reason=title_reason)

    loc_ok, loc_reason = _location_passes(location, allowed_locs)
    if not loc_ok:
        return FilterResult(passed=False, reason=loc_reason)

    age_ok, age_reason = _age_passes(posted_date, max_age)
    if not age_ok:
        return FilterResult(passed=False, reason=age_reason)

    return FilterResult(passed=True, reason=f"{title_reason}; {loc_reason}; {age_reason}")
