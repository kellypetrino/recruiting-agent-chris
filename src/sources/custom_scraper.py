"""Career page scraper for companies without public ATS APIs.

Uses Playwright (headless Chromium) to render JavaScript-heavy career pages,
then Claude Haiku to extract job listings from the rendered content.

Playwright actually executes JavaScript and waits for the page to stabilize,
which is required for modern SPA-based career sites (JPMorgan, Citi, etc.).
"""

import json
import re
import structlog
from datetime import datetime
from urllib.parse import urlparse

import anthropic
import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from src.normalize import JobPosting, infer_remote_policy

log = structlog.get_logger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_PAGE_TIMEOUT_MS = 30_000   # max wait for initial page load
_NETWORK_IDLE_MS = 10_000   # wait for network to go quiet after load (JS-heavy SPAs need more)

_EXTRACT_SYSTEM = """\
You are a job listing extractor. Given the text/markdown content of a company's careers page, \
extract all visible job listings. For each job return:
- title: the job title (string)
- url: direct link to the job posting (absolute URL preferred; relative path is OK)
- location: the location as listed (string, or "" if unknown)
- posted_date: ISO date YYYY-MM-DD if shown, otherwise null

Return ONLY a JSON array — no explanation, no markdown fences. If no jobs are visible, return [].

Example:
[
  {"title": "Financial Analyst", "url": "https://example.com/jobs/123", "location": "New York, NY", "posted_date": null},
  {"title": "Operations Associate", "url": "/careers/456", "location": "Remote (US)", "posted_date": "2026-04-28"}
]"""


def _fetch_with_playwright(url: str) -> str:
    """Launch headless Chromium, load the page, wait for network idle, return full text."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
            # Wait for network to go quiet so SPA data loads finish
            try:
                page.wait_for_load_state("networkidle", timeout=_NETWORK_IDLE_MS)
            except PlaywrightTimeout:
                pass  # Network never truly idled — use whatever rendered so far
            content = page.inner_text("body")
        finally:
            browser.close()
    return content


def _extract_jobs(content: str, company: str, career_url: str, claude: anthropic.Anthropic) -> list[dict]:
    """Use Claude Haiku to extract job listings from rendered page text."""
    truncated = content[:10000]
    resp = claude.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_EXTRACT_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Company: {company}\nCareer page: {career_url}\n\nPage content:\n{truncated}",
        }],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to pull the JSON array out even if there's trailing text
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        log.warning("custom_scraper.extract_parse_error", company=company, snippet=raw[:200])
        return []


def _make_posting(raw: dict, company: str, career_url: str) -> JobPosting | None:
    title = (raw.get("title") or "").strip()
    url = (raw.get("url") or "").strip()
    location = (raw.get("location") or "").strip()

    if not title or not url:
        return None

    if url.startswith("/"):
        parsed = urlparse(career_url)
        url = f"{parsed.scheme}://{parsed.netloc}{url}"
    elif not url.startswith("http"):
        # Try prepending the base origin
        parsed = urlparse(career_url)
        url = f"{parsed.scheme}://{parsed.netloc}/{url.lstrip('/')}"

    posted_date = None
    if raw.get("posted_date"):
        try:
            posted_date = datetime.fromisoformat(raw["posted_date"])
        except (ValueError, TypeError):
            pass

    return JobPosting(
        job_id=JobPosting.make_id("custom", f"{company}:{url}"),
        company=company,
        title=title,
        location=location,
        remote_policy=infer_remote_policy(location, title=title),
        posted_date=posted_date,
        url=url,
        description_raw="",
        salary_range=None,
        source="custom",
    )


def fetch_jobs(
    career_url: str,
    company: str,
    client: httpx.Client,
    claude: anthropic.Anthropic,
) -> list[JobPosting]:
    """Scrape a career page with a real browser and extract job postings."""
    log.info("custom_scraper.fetch", company=company, url=career_url)

    try:
        content = _fetch_with_playwright(career_url)
    except Exception:
        log.exception("custom_scraper.fetch_error", company=company, url=career_url)
        return []

    log.info("custom_scraper.rendered", company=company, content_len=len(content))

    raw_jobs = _extract_jobs(content, company, career_url, claude)
    log.info("custom_scraper.extracted", company=company, raw_count=len(raw_jobs))

    postings = []
    for raw in raw_jobs:
        p = _make_posting(raw, company, career_url)
        if p:
            postings.append(p)

    log.info("custom_scraper.done", company=company, count=len(postings))
    return postings
