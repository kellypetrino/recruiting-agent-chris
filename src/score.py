"""Stage 2: LLM-based relevance scoring via Claude Haiku 4.5.

Scores each job 0-10 against Chris's target profile. Results are cached in the
DB by job_id — a job is never re-scored.
"""

import json
import re
import structlog
from datetime import datetime

import anthropic
import httpx

from src.db import Job, SessionLocal

log = structlog.get_logger(__name__)

MODEL = "claude-haiku-4-5-20251001"

# ── Description enrichment ────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s{2,}")


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _fetch_greenhouse_description(url: str, client: httpx.Client) -> str:
    """Greenhouse boards API detail endpoint returns full job content as HTML."""
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if not m:
        return ""
    board_id, job_id = m.groups()
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
    api_url = (
        f"https://{tenant}.{instance}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{board}/jobs{external_path}"
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
        return _fetch_greenhouse_description(job.url, client)
    if job.source == "workday":
        return _fetch_workday_description(job.url, client)
    return ""


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a recruiting assistant helping Christopher (Chris) Petrino, a May 2026 Boston College \
graduate (BS in Management, concentrations in Finance & Operations Management), evaluate job \
postings. He interned at Bilt (a $10B FinTech company) in Finance & Operations. He is targeting \
entry-level roles in financial analysis, FP&A, operations, business analysis, and \
program/project management. He strongly prefers New York City or New Jersey; US-remote roles \
are acceptable. He wants to start as soon as possible (May/June 2026).

Background highlights:
- Finance & Operations coursework: Corporate Finance, Financial Accounting, Excel Analytics, \
  Operations Management, Supply Chain Management
- Bilt internship: financial modeling, FP&A support, cross-functional operations, data analysis, \
  executive-facing deliverables (CFO briefs, partner contract summaries)
- Technical skills: Excel (advanced), Python, RStudio, AI tools (Claude, ChatGPT, Gemini)
- Strong interest in FinTech, payments, and tech-adjacent finance roles
- Currently interviewing with the NBA for a Workflow Development role — sports, media, and \
  entertainment organizations are also of interest

Your job: read a job posting and score its fit for Chris on a 0–10 scale.

EXPERIENCE LEVEL (new grad, 0 years full-time experience):
  - Ideal: "entry-level", "new grad", "analyst", "associate", "rotational program", 0 years required, or no requirement stated
  - Acceptable: roles stating "0-1 years" where the description clearly welcomes new grads
  - Hard reject: any role explicitly requiring 1+ years of professional experience (score ≤ 3)
  - Hard reject: any role with a minimum GPA requirement (score ≤ 3)
  - If no experience requirement is stated, assume entry-level and don't penalize

LOCATION PRIORITY:
  - Top priority: New York City, NYC, Manhattan, Brooklyn, Queens — these are ideal
  - Strong: New Jersey (NJ), Secaucus, Jersey City, Newark — these are great
  - Acceptable: US-remote or "Remote (US)" roles
  - Penalize: Boston-only roles (drop 1 point — less preferred but not a hard reject)
  - Hard reject: international-only, or US cities far from NYC/NJ/Boston with no remote option

SCORING GUIDE (use these anchors):
  9–10: Perfect fit. Financial Analyst, FP&A Analyst, Operations Analyst, Business Analyst, or \
        Program/Project Manager at a FinTech, finance, or tech company in NYC/NJ. \
        Entry-level with no experience requirement. Clear match to his Bilt background.
        Example: "Financial Analyst, FP&A – New York" at a FinTech startup.
  7–8:  Strong fit. Right role/industry in NYC/NJ but slightly more generalist title, \
        OR right role in US-remote, OR rotational analyst/development program anywhere.
        Example: "Operations Associate" at a NYC bank, or "Business Analyst – Remote" at a tech firm.
  5–6:  Possible fit. Good role but Boston-only, or right direction but vague/generic title \
        in NYC/NJ, or analyst role in a tangential industry.
  3–4:  Weak fit. Requires any experience, has a GPA requirement, wrong function \
        (marketing, HR, pure engineering), or wrong location with no remote option.
  1–2:  Poor fit. Requires deep technical expertise, people-manager role, or completely off-base.
  0:    No fit. Wrong function, too senior, international only, or executive-level.

HARD NEGATIVES (always score ≤ 3):
  - Any explicit experience requirement (e.g., "1+ years", "2 years required", "minimum 1 year")
  - GPA requirement above 3.2
  - Director, VP, Head of, C-suite, or people-manager titles
  - International-only locations (outside US)
  - Pure engineering or deep coding roles
  - Internships (Chris needs full-time, not another internship)
  - MBA required (he is a BC undergrad, not an MBA)

Respond ONLY with a JSON object — no explanation outside the JSON:
{
  "score": <integer 0-10>,
  "rationale": "<one sentence explaining why this fits or doesn't>",
  "flags": "<one sentence on any red flags, or null if none>"
}"""

_USER_TEMPLATE = """\
Company: {company}
Title: {title}
Location: {location}
Remote policy: {remote_policy}

Job description:
{description}"""


# ── Scoring ───────────────────────────────────────────────────────────────────

def _build_prompt(job: Job) -> str:
    description = (job.description_raw or "")[:4000]  # Haiku context is ample; cap for cost
    if not description:
        description = "(No description available — score based on title and location only)"
    return _USER_TEMPLATE.format(
        company=job.company,
        title=job.title,
        location=job.location,
        remote_policy=job.remote_policy,
        description=description,
    )


def _parse_response(text: str) -> dict:
    """Extract JSON from the model response, tolerating minor formatting issues."""
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object in the text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def score_job(job: Job, client: anthropic.Anthropic) -> dict:
    """Score a single job. Returns parsed dict with score/rationale/flags."""
    prompt = _build_prompt(job)
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
    return _parse_response(raw)


def run_scoring(min_score_threshold: float = 0.0, dry_run: bool = False) -> dict:
    """Score all jobs that passed Stage 1 filter but haven't been scored yet.

    Returns summary stats.
    """
    claude = anthropic.Anthropic()
    stats = {
        "scored": 0,
        "skipped_cached": 0,
        "errors": 0,
        "total_passed_filter": 0,
        "descriptions_fetched": 0,
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
                    result = score_job(job, claude)
                    score = result.get("score", 0)
                    if not dry_run:
                        job.score = float(score)
                        job.score_rationale = result.get("rationale")
                        job.score_flags = result.get("flags")
                        job.scored_at = datetime.utcnow()
                        session.add(job)
                        session.commit()
                    stats["scored"] += 1
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
