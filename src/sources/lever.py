"""Lever ATS public API client.

Endpoint: https://api.lever.co/v0/postings/{board_id}
No auth required. Returns all published postings.
"""

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

import httpx

from src.normalize import JobPosting, from_lever

log = structlog.get_logger(__name__)

_BASE = "https://api.lever.co/v0/postings"
_TIMEOUT = 30.0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_jobs(board_id: str, company: str, client: httpx.Client) -> list[JobPosting]:
    """Fetch all published jobs from a Lever board and return normalized postings."""
    url = f"{_BASE}/{board_id}"
    log.info("lever.fetch", board_id=board_id, url=url)

    response = client.get(url, params={"mode": "json"}, timeout=_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    if isinstance(data, dict) and not data.get("ok", True):
        log.warning("lever.not_found", board_id=board_id, error=data.get("error"))
        return []

    raw_jobs = data if isinstance(data, list) else []
    log.info("lever.fetched", board_id=board_id, count=len(raw_jobs))

    postings = []
    for raw in raw_jobs:
        try:
            postings.append(from_lever(raw, company))
        except Exception:
            log.exception("lever.parse_error", board_id=board_id, raw_id=raw.get("id"))
    return postings


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": "recruiting-agent/0.1 (personal job search tool)"})
