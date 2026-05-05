"""Stage 2: LLM-based relevance scoring via Claude Haiku 4.5.

Scores each job 0-10 against Chris's target profile. Results are cached in the
DB by job_id — a job is never re-scored.
"""

import json
import re
import structlog
from datetime import datetime

import anthropic

from src.db import Job, SessionLocal

log = structlog.get_logger(__name__)

MODEL = "claude-haiku-4-5-20251001"

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
  - Ideal: "entry-level", "new grad", "analyst", "associate", "rotational program", 0–2 years required
  - Acceptable: roles stating 1–3 years (slight mismatch, common for analyst tracks)
  - Penalize: roles requiring 3–5 years (drop 2 points)
  - Hard reject: roles explicitly requiring 5+ years (score ≤ 3)
  - If no experience requirement is stated, don't penalize — assume it's fine

SCORING GUIDE (use these anchors):
  9–10: Perfect fit. Financial Analyst, FP&A Analyst, Operations Analyst, Business Analyst, or \
        Program/Project Manager at a FinTech, finance, or tech company in NYC/NJ or remote. \
        Entry-level, 0–2 years. Clear match to his Bilt background.
        Example: "Financial Analyst, FP&A – New York" at a FinTech startup.
  7–8:  Strong fit. Similar role in a slightly different industry, or right industry but more \
        generalist. Rotational analyst programs and analyst development programs are great here.
        Example: "Operations Associate" at a bank, or "Business Analyst" at a consulting firm.
  5–6:  Possible fit. Analyst role in a tangential industry, or right industry but experience \
        requirement is 2–3 years, or role is in the right direction but vague/generic title.
  3–4:  Weak fit. Requires 3–5 years explicitly, or wrong function (marketing, HR, pure engineering).
  1–2:  Poor fit. Requires deep technical expertise, is a people-manager role, or completely off-base.
  0:    No fit. Wrong function, too senior, international only, or executive-level.

HARD NEGATIVES (always score ≤ 3):
  - Roles explicitly requiring 5+ years of experience
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
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
    return _parse_response(raw)


def run_scoring(min_score_threshold: float = 0.0, dry_run: bool = False) -> dict:
    """Score all jobs that passed Stage 1 filter but haven't been scored yet.

    Returns summary stats.
    """
    client = anthropic.Anthropic()
    stats = {"scored": 0, "skipped_cached": 0, "errors": 0, "total_passed_filter": 0}

    with SessionLocal() as session:
        jobs = (
            session.query(Job)
            .filter(Job.passed_prefilter == 1, Job.scored_at == None)  # noqa: E711
            .all()
        )
        stats["total_passed_filter"] = len(jobs)
        log.info("score.start", jobs_to_score=len(jobs))

        for job in jobs:
            try:
                result = score_job(job, client)
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
