"""Stage 2: Rule-based qualification screening.

Parses job descriptions for experience requirements, GPA requirements,
and entry-level signals. No LLM involved — fast and deterministic.

Score values:
  2 = entry-level / new-grad explicitly confirmed in listing
  1 = no disqualifying requirements found
  0 = disqualified (1+ years required, GPA req, senior title, internship, MBA)
"""

import re
import structlog
from datetime import datetime
from pathlib import Path

import httpx
import yaml

from src.db import Job, SessionLocal

log = structlog.get_logger(__name__)

# Board-id lookup so we can hit the Greenhouse API for custom-domain URLs (gh_jid=)
def _load_greenhouse_boards() -> dict[str, str]:
    try:
        config = yaml.safe_load(
            (Path(__file__).parent.parent / "config" / "target_companies.yaml").read_text()
        )
        return {
            c["name"]: c["board_id"]
            for c in config.get("companies", [])
            if c.get("ats") == "greenhouse" and c.get("board_id") and c["board_id"] != "TODO"
        }
    except Exception:
        return {}

_GREENHOUSE_BOARDS = _load_greenhouse_boards()

# ── Description enrichment ────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s{2,}")


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _fetch_greenhouse_description(url: str, company: str, client: httpx.Client) -> str:
    """Greenhouse boards API detail endpoint returns full job content as HTML.

    Handles both job-boards.greenhouse.io URLs and custom-domain gh_jid= URLs.
    """
    # Standard: job-boards.greenhouse.io/{board}/jobs/{id}
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if m:
        board_id, job_id = m.groups()
    else:
        # Custom domain: ?gh_jid=12345 — look up board_id from config
        m2 = re.search(r"[?&]gh_jid=(\d+)", url)
        if not m2:
            return ""
        job_id = m2.group(1)
        board_id = _GREENHOUSE_BOARDS.get(company, "")
        if not board_id:
            return ""
    try:
        resp = client.get(
            f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs/{job_id}",
            timeout=15.0,
        )
        resp.raise_for_status()
        return _strip_tags(resp.json().get("content", ""))
    except Exception:
        return ""


def _fetch_workday_description(url: str, client: httpx.Client) -> str:
    """Workday undocumented detail endpoint mirrors the list endpoint with the job path appended."""
    m = re.match(
        r"https://([^.]+)\.([^.]+)\.myworkdayjobs\.com/([^/]+)(/.+)",
        url,
    )
    if not m:
        return ""
    tenant, instance, board, external_path = m.groups()
    # external_path starts with /job/... — omit the /jobs prefix
    api_url = (
        f"https://{tenant}.{instance}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{board}{external_path}"
    )
    try:
        resp = client.get(api_url, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        desc = (
            data.get("jobPostingInfo", {}).get("jobDescription", "")
            or data.get("jobDescription", "")
        )
        return _strip_tags(desc)
    except Exception:
        return ""


def enrich_description(job: Job, client: httpx.Client) -> str:
    """Fetch a job description from its ATS API. Returns empty string on failure."""
    if job.source == "greenhouse":
        return _fetch_greenhouse_description(job.url, job.company, client)
    if job.source == "workday":
        return _fetch_workday_description(job.url, client)
    return ""


# ── Qualification parsing ─────────────────────────────────────────────────────

_YEARS_PATTERNS = [
    re.compile(r'(\d+)\s*\+\s*years?\s+(?:of\s+)?(?:relevant\s+|professional\s+|work\s+|prior\s+)?experience', re.I),
    re.compile(r'(\d+)\s+or\s+more\s+years?\s+(?:of\s+)?(?:relevant\s+|professional\s+)?experience', re.I),
    re.compile(r'minimum\s+(?:of\s+)?(\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:experience|work)', re.I),
    re.compile(r'at\s+least\s+(\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:experience|work)', re.I),
    re.compile(r'(\d+)\s*[-–]\s*\d+\s*years?\s+(?:of\s+)?(?:relevant\s+|professional\s+|work\s+)?experience', re.I),
    re.compile(r'(\d+)\s*years?\s+of\s+(?:relevant\s+|professional\s+|full[- ]time\s+|work\s+|prior\s+)?experience', re.I),
    re.compile(r'requires?\s+(\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:experience|work)', re.I),
    re.compile(r'experience\s*[:\-–]\s*(\d+)\s*\+?\s*years?', re.I),
]

_GPA_PATTERNS = [
    re.compile(r'(?:minimum\s+)?gpa\s+(?:of\s+|requirement\s+(?:of\s+)?)(\d+\.\d+)', re.I),
    re.compile(r'(\d+\.\d+)\s+(?:minimum\s+)?(?:cumulative\s+)?gpa', re.I),
    re.compile(r'gpa[:\s]+(\d+\.\d+)', re.I),
    re.compile(r'grade\s+point\s+average\s+(?:of\s+)?(\d+\.\d+)', re.I),
]

_ENTRY_LEVEL_SIGNALS = [
    "new grad",
    "new graduate",
    "recent grad",
    "recent graduate",
    "entry level",
    "entry-level",
    "0-1 year",
    "0 to 1 year",
    "zero to one year",
    "no experience required",
    "no prior experience",
    "recent college graduate",
    "recent university graduate",
    "graduating in 2025",
    "graduating in 2026",
    "class of 2025",
    "class of 2026",
    "class of 2027",
    "rotational program",
    "development program",
    "analyst program",
    "associate program",
    "early career",
    "early-career",
    "campus hire",
    "campus recruiting",
    "university recruiting",
    "new college graduate",
]

_SENIOR_TITLE_WORDS = [
    "director",
    "vice president",
    " vp ",
    "head of",
    "principal",
    "staff engineer",
    "managing director",
    "c-suite",
    " cto",
    " cfo",
    " coo",
    " ceo",
    "svp",
    "evp",
    "avp",
]


def _extract_years_required(text: str) -> int | None:
    for pattern in _YEARS_PATTERNS:
        m = pattern.search(text)
        if m:
            val = int(m.group(1))
            if val >= 1:
                return val
    return None


def _extract_gpa_required(text: str) -> float | None:
    for pattern in _GPA_PATTERNS:
        m = pattern.search(text)
        if m:
            val = float(m.group(1))
            if 2.0 <= val <= 4.0:
                return val
    return None


def evaluate_job(job: Job) -> dict:
    """
    Evaluate a job based on its description content.

    Returns dict with score (0/1/2), rationale, flags.
    """
    title = (job.title or "").lower()
    text = (job.description_raw or "").lower()

    # Hard reject: senior/management title
    for word in _SENIOR_TITLE_WORDS:
        if word in title:
            return {
                "score": 0,
                "rationale": f"Senior or management role ('{word.strip()}' in title)",
                "flags": "Title indicates seniority — not entry-level",
            }

    # Hard reject: internship
    if "intern" in title and "internal" not in title:
        return {
            "score": 0,
            "rationale": "Internship position, not full-time",
            "flags": "Internship",
        }

    # Hard reject: explicit experience requirement in description
    years = _extract_years_required(text)
    if years is not None:
        return {
            "score": 0,
            "rationale": f"Requires {years}+ years of experience",
            "flags": f"Experience requirement: {years}+ years",
        }

    # Hard reject: GPA requirement above threshold
    gpa = _extract_gpa_required(text)
    if gpa is not None and gpa > 3.2:
        return {
            "score": 0,
            "rationale": f"GPA requirement of {gpa:.1f}",
            "flags": f"GPA requirement: {gpa:.1f}",
        }

    # Hard reject: MBA required/preferred
    if re.search(r'\bmba\b.{0,50}(?:required|preferred|must\b)', text, re.I) or \
       re.search(r'(?:required|preferred|must\b).{0,50}\bmba\b', text, re.I):
        return {
            "score": 0,
            "rationale": "MBA required or preferred",
            "flags": "MBA requirement",
        }

    # Positive: explicitly entry-level / new grad friendly
    for signal in _ENTRY_LEVEL_SIGNALS:
        if signal in text or signal in title:
            return {
                "score": 2,
                "rationale": f"Entry-level confirmed (\"{signal}\" in listing)",
                "flags": None,
            }

    # Neutral: no disqualifying requirements found
    if not text:
        note = "No description available — not disqualified on title/location alone"
    else:
        note = "No experience or GPA requirements found in job description"
    return {
        "score": 1,
        "rationale": note,
        "flags": None,
    }


# ── Scoring runner ────────────────────────────────────────────────────────────

def run_scoring(dry_run: bool = False) -> dict:
    """Evaluate all jobs that passed Stage 1 filter but haven't been scored yet.

    Fetches descriptions first, then applies rule-based qualification checks.
    Returns summary stats.
    """
    stats = {
        "scored": 0,
        "skipped_cached": 0,
        "errors": 0,
        "total_passed_filter": 0,
        "descriptions_fetched": 0,
        "disqualified": 0,
        "entry_level_confirmed": 0,
        "no_requirements": 0,
    }

    with httpx.Client(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        }
    ) as http:
        with SessionLocal() as session:
            jobs = (
                session.query(Job)
                .filter(Job.passed_prefilter == 1, Job.scored_at == None)  # noqa: E711
                .all()
            )
            stats["total_passed_filter"] = len(jobs)
            log.info("score.start", jobs_to_score=len(jobs))

            for job in jobs:
                # Fetch description before scoring if not already stored
                if not job.description_raw:
                    desc = enrich_description(job, http)
                    if desc:
                        stats["descriptions_fetched"] += 1
                        log.info(
                            "score.description_fetched",
                            company=job.company,
                            title=job.title[:50],
                            chars=len(desc),
                        )
                        if not dry_run:
                            job.description_raw = desc
                            session.add(job)
                            session.commit()
                        else:
                            job.description_raw = desc  # in-memory only for dry run

                try:
                    result = evaluate_job(job)
                    score = result["score"]
                    if not dry_run:
                        job.score = float(score)
                        job.score_rationale = result.get("rationale")
                        job.score_flags = result.get("flags")
                        job.scored_at = datetime.utcnow()
                        session.add(job)
                        session.commit()
                    stats["scored"] += 1
                    if score == 0:
                        stats["disqualified"] += 1
                    elif score == 2:
                        stats["entry_level_confirmed"] += 1
                    else:
                        stats["no_requirements"] += 1
                    log.info(
                        "score.job",
                        company=job.company,
                        title=job.title[:50],
                        score=score,
                        rationale=result.get("rationale", "")[:80],
                    )
                except Exception:
                    log.exception("score.error", job_id=job.job_id, title=job.title[:50])
                    stats["errors"] += 1

    return stats
