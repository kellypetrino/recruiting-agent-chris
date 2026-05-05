"""Workday undocumented internal API client.

Many large companies expose an unauthenticated JSON endpoint that powers their
careers pages. The pattern is consistent across all Workday tenants:
  POST https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs

board_id format in config: "tenant/instance/board" e.g. "mastercard/wd1/CorporateCareers"
"""

import re
import structlog
from datetime import datetime, timedelta

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.normalize import JobPosting, infer_remote_policy

log = structlog.get_logger(__name__)
_TIMEOUT = 30.0
_PAGE_SIZE = 20  # Workday rejects limit > 20

# Workday returns strings like "Posted 3 Days Ago", "Posted Today", "Posted 30+ Days Ago"
_POSTED_RE = re.compile(
    r"Posted\s+(?:(\d+)\+?\s+Days?\s+Ago|(\d+)\+?\s+Months?\s+Ago|Today|Yesterday)",
    re.IGNORECASE,
)


def _parse_posted_on(text: str) -> datetime | None:
    if not text:
        return None
    m = _POSTED_RE.search(text)
    if not m:
        return None
    now = datetime.utcnow()
    if "today" in text.lower():
        return now
    if "yesterday" in text.lower():
        return now - timedelta(days=1)
    days, months = m.group(1), m.group(2)
    if days:
        return now - timedelta(days=int(days))
    if months:
        return now - timedelta(days=int(months) * 30)
    return None


def _make_job_posting(raw: dict, company: str, tenant: str, instance: str, board: str) -> JobPosting:
    external_path = raw.get("externalPath", "")
    native_id = raw.get("jobPostingId") or raw.get("bulletFields", [None])[0] or external_path
    location = raw.get("locationsText", "")
    title = raw.get("title", "")
    url = f"https://{tenant}.{instance}.myworkdayjobs.com/{board}{external_path}"

    return JobPosting(
        job_id=JobPosting.make_id("workday", f"{tenant}/{native_id}"),
        company=company,
        title=title,
        location=location,
        remote_policy=infer_remote_policy(location, title=title),
        posted_date=_parse_posted_on(raw.get("postedOn", "")),
        url=url,
        description_raw="",  # list endpoint doesn't include descriptions
        salary_range=None,
        source="workday",
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(client: httpx.Client, api_url: str, offset: int) -> dict:
    response = client.post(
        api_url,
        json={"appliedFacets": {}, "limit": _PAGE_SIZE, "offset": offset, "searchText": ""},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_jobs(board_id: str, company: str, client: httpx.Client) -> list[JobPosting]:
    """Fetch all published jobs from a Workday board.

    board_id format: "tenant/instance/board_name"
    e.g. "mastercard/wd1/CorporateCareers"
    """
    parts = board_id.split("/", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid Workday board_id: {board_id!r}. Expected tenant/wdN/board_name")
    tenant, instance, board = parts

    api_url = f"https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    log.info("workday.fetch", company=company, url=api_url)

    postings, offset = [], 0
    while True:
        data = _fetch_page(client, api_url, offset)
        raw_jobs = data.get("jobPostings", [])
        total = data.get("total", 0)

        for raw in raw_jobs:
            try:
                postings.append(_make_job_posting(raw, company, tenant, instance, board))
            except Exception:
                log.exception("workday.parse_error", company=company, title=raw.get("title"))

        offset += len(raw_jobs)
        if not raw_jobs or offset >= total:
            break

    log.info("workday.fetched", company=company, count=len(postings), total=total)
    return postings


def make_client() -> httpx.Client:
    # Browser User-Agent — some Workday tenants reject bot-looking requests
    # Don't set Content-Type here; httpx sets it automatically for json= requests
    return httpx.Client(headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    })
