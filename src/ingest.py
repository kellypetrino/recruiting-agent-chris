"""Orchestrates fetching from all configured ATS sources and writing to the DB."""

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import structlog
import yaml
from sqlalchemy.dialects.sqlite import insert

import anthropic

from src.db import Job, SessionLocal, init_db
from src.normalize import JobPosting
from src.sources import ashby, custom_scraper, eightfold, greenhouse, lever, workday

log = structlog.get_logger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "target_companies.yaml"

_ATS_HANDLERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
    "ashby": ashby.fetch_jobs,
    "workday": workday.fetch_jobs,
    "eightfold": eightfold.fetch_jobs,
}


def load_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def upsert_postings(postings: list[JobPosting], session) -> tuple[int, int]:
    """Insert new jobs; update fetched_at for existing ones. Returns (inserted, refreshed)."""
    if not postings:
        return 0, 0

    now = datetime.utcnow()
    incoming_ids = {p.job_id for p in postings}

    # Which of these job_ids already exist?
    existing_ids = {
        row[0]
        for row in session.execute(
            Job.__table__.select().with_only_columns(Job.__table__.c.job_id)
            .where(Job.__table__.c.job_id.in_(incoming_ids))
        )
    }

    inserted = refreshed = 0
    for p in postings:
        stmt = (
            insert(Job)
            .values(
                job_id=p.job_id,
                company=p.company,
                title=p.title,
                location=p.location,
                remote_policy=p.remote_policy,
                posted_date=p.posted_date,
                url=p.url,
                description_raw=p.description_raw,
                salary_range=p.salary_range,
                source=p.source,
                fetched_at=now,
            )
            .on_conflict_do_update(
                index_elements=["job_id"],
                set_={"fetched_at": now},
            )
        )
        session.execute(stmt)
        if p.job_id in existing_ids:
            refreshed += 1
        else:
            inserted += 1

    session.commit()
    return inserted, refreshed


def run_ingest(config_path: Path = _CONFIG_PATH) -> dict:
    init_db()
    config = load_config(config_path)
    companies = config.get("companies", [])

    stats: dict[str, dict] = {}

    workday_client = workday.make_client()
    default_client = httpx.Client(
        headers={"User-Agent": "recruiting-agent/0.1 (personal job search tool)"}
    )
    claude_client = anthropic.Anthropic()

    with default_client, workday_client:
        with SessionLocal() as session:
            for company_cfg in companies:
                name = company_cfg["name"]
                ats = company_cfg.get("ats", "")
                board_id = company_cfg.get("board_id", "")
                career_url = company_cfg.get("career_url", "")

                # Custom ATS: use career page scraper if a URL is configured
                if ats == "custom":
                    if not career_url:
                        log.info("ingest.skip", company=name, reason="custom ATS, no career_url set")
                        continue
                    try:
                        postings = custom_scraper.fetch_jobs(career_url, name, default_client, claude_client)
                        inserted, refreshed = upsert_postings(postings, session)
                        stats[name] = {"fetched": len(postings), "inserted": inserted, "refreshed": refreshed}
                        log.info(
                            "ingest.done",
                            company=name,
                            fetched=len(postings),
                            inserted=inserted,
                            refreshed=refreshed,
                        )
                    except Exception:
                        log.exception("ingest.error", company=name)
                        stats[name] = {"error": True}
                    continue

                if board_id == "TODO" or not board_id:
                    log.info("ingest.skip", company=name, reason="unconfigured board_id")
                    continue

                handler = _ATS_HANDLERS.get(ats)
                if not handler:
                    log.warning("ingest.unknown_ats", company=name, ats=ats)
                    continue

                client = workday_client if ats == "workday" else default_client
                try:
                    postings = handler(board_id, name, client)
                    inserted, refreshed = upsert_postings(postings, session)
                    stats[name] = {"fetched": len(postings), "inserted": inserted, "refreshed": refreshed}
                    log.info(
                        "ingest.done",
                        company=name,
                        fetched=len(postings),
                        inserted=inserted,
                        refreshed=refreshed,
                    )
                except Exception:
                    log.exception("ingest.error", company=name)
                    stats[name] = {"error": True}

    # Purge jobs not seen in any recent fetch — they've been closed at the source.
    # Only purge API-backed sources (greenhouse, workday, lever, ashby, eightfold)
    # where fetched_at is reliably updated every run. Custom-scraped jobs aren't
    # always re-fetched fully, so leave them alone.
    with SessionLocal() as session:
        cutoff = datetime.utcnow() - timedelta(days=45)
        api_sources = list(_ATS_HANDLERS.keys())
        result = session.execute(
            Job.__table__.delete().where(
                Job.__table__.c.fetched_at < cutoff,
                Job.__table__.c.source.in_(api_sources),
            )
        )
        purged = result.rowcount
        session.commit()
    if purged:
        log.info("ingest.purged_stale", count=purged, cutoff_days=45)
        stats["__purged__"] = purged

    return stats
