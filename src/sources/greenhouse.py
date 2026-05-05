"""Greenhouse ATS public API client.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs
No auth required. Returns all open jobs for a given board.
"""

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

import httpx

from src.normalize import JobPosting, from_greenhouse

log = structlog.get_logger(__name__)

_BASE = "https://boards-api.greenhouse.io/v1/boards"
_TIMEOUT = 30.0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_jobs(board_id: str, company: str, client: httpx.Client) -> list[JobPosting]:
    """Fetch all open jobs from a Greenhouse board and return normalized postings."""
    url = f"{_BASE}/{board_id}/jobs"
    log.info("greenhouse.fetch", board_id=board_id, url=url)

    response = client.get(url, timeout=_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])
    log.info("greenhouse.fetched", board_id=board_id, count=len(raw_jobs))

    postings = []
    for raw in raw_jobs:
        try:
            postings.append(from_greenhouse(raw, company))
        except Exception:
            log.exception("greenhouse.parse_error", board_id=board_id, raw_id=raw.get("id"))
    return postings


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": "recruiting-agent/0.1 (personal job search tool)"})
