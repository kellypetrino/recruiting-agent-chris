"""Eightfold AI ATS public API client.

Endpoint: GET https://{tenant}.eightfold.ai/api/pcsx/search
Params: domain={domain}&query=analyst&location={location}&start={start}&num={page_size}
No auth required for most tenants.

Confirmed working tenants: paypal (paypal.com), morganstanley (morganstanley.com)
"""

from datetime import datetime

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.normalize import JobPosting, infer_remote_policy

log = structlog.get_logger(__name__)

_PAGE_SIZE = 100
_TIMEOUT = 30.0
_LOCATIONS = ["New York", "New Jersey"]


def _make_url(tenant: str) -> str:
    return f"https://{tenant}.eightfold.ai/api/pcsx/search"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(client: httpx.Client, tenant: str, domain: str, location: str, start: int) -> list[dict]:
    url = _make_url(tenant)
    resp = client.get(
        url,
        params={
            "domain": domain,
            "location": location,
            "start": start,
            "num": _PAGE_SIZE,
        },
        headers={
            "Accept": "application/json",
            "Referer": f"https://{tenant}.eightfold.ai/careers",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("positions", [])


def _from_eightfold(raw: dict, company: str, tenant: str) -> JobPosting | None:
    native_id = str(raw.get("id", ""))
    if not native_id:
        return None

    title = (raw.get("name") or "").strip()
    if not title:
        return None

    locations = raw.get("locations") or raw.get("standardizedLocations") or []
    location = "; ".join(locations) if locations else ""

    work_option = raw.get("workLocationOption", "")
    if work_option == "remote":
        remote_policy = "remote"
    elif work_option == "remote_local":
        remote_policy = "hybrid"
    else:
        remote_policy = infer_remote_policy(location)

    posted_ts = raw.get("postedTs")
    posted_date = datetime.utcfromtimestamp(posted_ts) if posted_ts else None

    position_url = raw.get("positionUrl", "")
    if position_url.startswith("/"):
        url = f"https://{tenant}.eightfold.ai{position_url}"
    elif position_url.startswith("http"):
        url = position_url
    else:
        url = f"https://{tenant}.eightfold.ai/careers/job/{native_id}"

    return JobPosting(
        job_id=JobPosting.make_id("eightfold", f"{tenant}:{native_id}"),
        company=company,
        title=title,
        location=location,
        remote_policy=remote_policy,
        posted_date=posted_date,
        url=url,
        description_raw="",
        salary_range=None,
        source="eightfold",
    )


def fetch_jobs(board_id: str, company: str, client: httpx.Client) -> list[JobPosting]:
    """Fetch jobs from an Eightfold AI board.

    board_id format: "{tenant}/{domain}" e.g. "paypal/paypal.com"
    """
    parts = board_id.split("/", 1)
    tenant = parts[0]
    domain = parts[1] if len(parts) > 1 else f"{tenant}.com"

    log.info("eightfold.fetch", company=company, tenant=tenant, domain=domain)

    seen_ids: set[str] = set()
    postings: list[JobPosting] = []

    for location in _LOCATIONS:
        start = 0
        while True:
            try:
                raw_jobs = _fetch_page(client, tenant, domain, location, start)
            except Exception:
                log.exception("eightfold.fetch_error", company=company, tenant=tenant, location=location, start=start)
                break

            if not raw_jobs:
                break

            for raw in raw_jobs:
                p = _from_eightfold(raw, company, tenant)
                if p and p.job_id not in seen_ids:
                    seen_ids.add(p.job_id)
                    postings.append(p)

            if len(raw_jobs) < _PAGE_SIZE:
                break
            start += _PAGE_SIZE

    log.info("eightfold.done", company=company, count=len(postings))
    return postings
