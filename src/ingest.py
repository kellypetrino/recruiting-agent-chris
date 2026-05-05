"""Orchestrates fetching from all configured ATS sources and writing to the DB."""

from pathlib import Path

import httpx
import structlog
import yaml
from sqlalchemy.dialects.sqlite import insert

from src import sources
from src.db import Job, SessionLocal, init_db
from src.normalize import JobPosting
from src.sources import ashby, greenhouse, lever, workday

log = structlog.get_logger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "target_companies.yaml"

_ATS_HANDLERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
    "ashby": ashby.fetch_jobs,
    "workday": workday.fetch_jobs,
}


def load_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def upsert_postings(postings: list[JobPosting], session) -> tuple[int, int]:
    """Insert new jobs, skip duplicates. Returns (inserted, skipped)."""
    inserted = skipped = 0
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
                fetched_at=p.fetched_at,
            )
            .on_conflict_do_nothing(index_elements=["job_id"])
        )
        result = session.execute(stmt)
        if result.rowcount:
            inserted += 1
        else:
            skipped += 1
    session.commit()
    return inserted, skipped


def run_ingest(config_path: Path = _CONFIG_PATH) -> dict:
    init_db()
    config = load_config(config_path)
    companies = config.get("companies", [])

    stats: dict[str, dict] = {}

    workday_client = workday.make_client()
    default_client = httpx.Client(
        headers={"User-Agent": "recruiting-agent/0.1 (personal job search tool)"}
    )

    with default_client, workday_client:
        with SessionLocal() as session:
            for company_cfg in companies:
                name = company_cfg["name"]
                ats = company_cfg.get("ats", "")
                board_id = company_cfg.get("board_id", "")

                if ats == "custom" or board_id == "TODO" or not board_id:
                    log.info("ingest.skip", company=name, reason="custom or unconfigured ATS")
                    continue

                handler = _ATS_HANDLERS.get(ats)
                if not handler:
                    log.warning("ingest.unknown_ats", company=name, ats=ats)
                    continue

                client = workday_client if ats == "workday" else default_client
                try:
                    postings = handler(board_id, name, client)
                    inserted, skipped = upsert_postings(postings, session)
                    stats[name] = {"fetched": len(postings), "inserted": inserted, "skipped": skipped}
                    log.info(
                        "ingest.done",
                        company=name,
                        fetched=len(postings),
                        inserted=inserted,
                        skipped=skipped,
                    )
                except Exception:
                    log.exception("ingest.error", company=name)
                    stats[name] = {"error": True}

    return stats
