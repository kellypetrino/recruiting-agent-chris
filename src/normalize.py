"""Canonical job schema and mappers from each ATS format."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class JobPosting:
    job_id: str
    company: str
    title: str
    location: str
    remote_policy: str  # onsite | hybrid | remote | unknown
    posted_date: datetime | None
    url: str
    description_raw: str
    salary_range: str | None
    source: str  # greenhouse | lever | ashby | ...
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def make_id(cls, source: str, native_id: str) -> str:
        raw = f"{source}:{native_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── Remote policy detection ──────────────────────────────────────────────────

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\bon.?site\b|\bin.?office\b|\bin person\b", re.IGNORECASE)


def infer_remote_policy(location: str, title: str = "", description: str = "") -> str:
    text = f"{location} {title} {description[:500]}"
    if _REMOTE_RE.search(text) and _HYBRID_RE.search(text):
        return "hybrid"
    if _REMOTE_RE.search(text):
        return "remote"
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _ONSITE_RE.search(text):
        return "onsite"
    return "unknown"


# ── Greenhouse ───────────────────────────────────────────────────────────────

def from_greenhouse(raw: dict, company: str) -> JobPosting:
    """Map a single Greenhouse job dict to JobPosting."""
    native_id = str(raw["id"])
    location = raw.get("location", {}).get("name", "") or ""
    posted_at_str = raw.get("updated_at") or raw.get("created_at")
    posted_date = _parse_iso(posted_at_str)

    return JobPosting(
        job_id=JobPosting.make_id("greenhouse", native_id),
        company=company,
        title=raw.get("title", ""),
        location=location,
        remote_policy=infer_remote_policy(location),
        posted_date=posted_date,
        url=raw.get("absolute_url", ""),
        description_raw="",  # detail endpoint required for full description
        salary_range=None,
        source="greenhouse",
    )


# ── Lever ────────────────────────────────────────────────────────────────────

def from_lever(raw: dict, company: str) -> JobPosting:
    """Map a single Lever posting dict to JobPosting."""
    native_id = raw.get("id", "")
    categories = raw.get("categories", {})
    location = categories.get("location", "") or raw.get("workplaceType", "") or ""
    commitment = categories.get("commitment", "")
    created_at = raw.get("createdAt")
    posted_date = datetime.utcfromtimestamp(created_at / 1000) if created_at else None

    description_raw = _strip_html(raw.get("descriptionPlain", "") or raw.get("description", ""))

    return JobPosting(
        job_id=JobPosting.make_id("lever", native_id),
        company=company,
        title=raw.get("text", ""),
        location=location,
        remote_policy=infer_remote_policy(location, description=description_raw),
        posted_date=posted_date,
        url=raw.get("hostedUrl", ""),
        description_raw=description_raw,
        salary_range=None,
        source="lever",
    )


# ── Ashby ────────────────────────────────────────────────────────────────────

def from_ashby(raw: dict, company: str) -> JobPosting:
    """Map a single Ashby job posting dict to JobPosting."""
    native_id = raw.get("id", "")
    location = raw.get("locationName") or raw.get("location", "") or ""
    published_at = raw.get("publishedAt")
    posted_date = _parse_iso(published_at)
    is_remote = raw.get("isRemote", False)
    employment_type = raw.get("employmentType", "")
    description_raw = _strip_html(raw.get("descriptionHtml", "") or raw.get("description", ""))

    if is_remote:
        remote_policy = "remote"
    else:
        remote_policy = infer_remote_policy(location, description=description_raw)

    return JobPosting(
        job_id=JobPosting.make_id("ashby", native_id),
        company=company,
        title=raw.get("title", ""),
        location=location,
        remote_policy=remote_policy,
        posted_date=posted_date,
        url=raw.get("jobUrl", ""),
        description_raw=description_raw,
        salary_range=None,
        source="ashby",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s{2,}")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()
