"""Ashby ATS public API client.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{org}
No auth required. Returns all published job postings.
"""

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

import httpx

from src.normalize import JobPosting, from_ashby

log = structlog.get_logger(__name__)

_BASE = "https://api.ashbyhq.com/posting-api/job-board"
_TIMEOUT = 30.0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_jobs(board_id: str, company: str, client: httpx.Client) -> list[JobPosting]:
    """Fetch all published jobs from an Ashby board and return normalized postings."""
    url = f"{_BASE}/{board_id}"
    log.info("ashby.fetch", board_id=board_id, url=url)

    response = client.get(url, timeout=_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    # Ashby returns 'jobPostings' on older boards and 'jobs' on newer ones
    raw_jobs = data.get("jobPostings") or data.get("jobs", [])
    log.info("ashby.fetched", board_id=board_id, count=len(raw_jobs))

    postings = []
    for raw in raw_jobs:
        try:
            postings.append(from_ashby(raw, company))
        except Exception:
            log.exception("ashby.parse_error", board_id=board_id, raw_id=raw.get("id"))
    return postings


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": "recruiting-agent/0.1 (personal job search tool)"})
