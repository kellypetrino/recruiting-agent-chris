"""CLI entry points."""

import argparse
import json
import sys
from pathlib import Path

# Load .env before anything else so all modules pick up secrets
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

log = structlog.get_logger(__name__)


def cmd_ingest(args: argparse.Namespace) -> None:
    from src.ingest import run_ingest

    log.info("cli.ingest.start")
    stats = run_ingest()
    print(json.dumps(stats, indent=2))
    errors = [k for k, v in stats.items() if v.get("error")]
    if errors:
        log.error("cli.ingest.errors", companies=errors)
        sys.exit(1)


def cmd_filter(args: argparse.Namespace) -> None:
    """Run Stage 1 rule-based filter on all jobs not yet filtered."""
    from src.db import Job, SessionLocal, init_db
    from src.filter_rules import apply_filter, load_filter_config

    init_db()
    config = load_filter_config()
    passed = failed = skipped = 0

    with SessionLocal() as session:
        jobs = session.query(Job).filter(Job.passed_prefilter == None).all()  # noqa: E711
        log.info("filter.start", unfiltered=len(jobs))

        for job in jobs:
            result = apply_filter(job.title, job.location, job.posted_date, config)
            job.passed_prefilter = 1 if result.passed else 0
            session.add(job)
            if result.passed:
                passed += 1
            else:
                failed += 1

        session.commit()

    stats = {"passed": passed, "failed": failed, "skipped_already_filtered": skipped}
    print(json.dumps(stats, indent=2))
    log.info("filter.done", **stats)


def cmd_score(args: argparse.Namespace) -> None:
    """Run Stage 2 LLM scoring on jobs that passed Stage 1."""
    from src.score import run_scoring

    log.info("cli.score.start")
    stats = run_scoring(dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))
    if stats.get("errors"):
        log.warning("cli.score.had_errors", errors=stats["errors"])


def cmd_digest(args: argparse.Namespace) -> None:
    """Render and send the daily email digest."""
    from src.digest import run_digest

    log.info("cli.digest.start", all_time=args.all_time, preview=args.preview)

    if args.preview:
        from src.digest import fetch_all_scored_jobs, fetch_digest_jobs, render_html, render_text
        if args.all_time:
            tier1, tier2 = fetch_all_scored_jobs()
        else:
            tier1, tier2 = fetch_digest_jobs(since_days=1)
        print(render_text(tier1, tier2))
        return

    stats = run_digest(all_time=args.all_time)
    print(json.dumps(stats, indent=2))


def cmd_dump(args: argparse.Namespace) -> None:
    """Dump jobs to stdout as JSON lines. Use --scored to show only scored jobs."""
    from src.db import Job, SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        q = session.query(Job)
        if args.scored:
            q = q.filter(Job.scored_at != None).order_by(Job.score.desc())  # noqa: E711
        elif args.passed:
            q = q.filter(Job.passed_prefilter == 1)
        jobs = q.all()

        for j in jobs:
            row = {
                "job_id": j.job_id,
                "company": j.company,
                "title": j.title,
                "location": j.location,
                "remote_policy": j.remote_policy,
                "posted_date": j.posted_date.isoformat() if j.posted_date else None,
                "url": j.url,
                "source": j.source,
                "passed_prefilter": j.passed_prefilter,
                "score": j.score,
                "rationale": j.score_rationale,
                "flags": j.score_flags,
            }
            print(json.dumps(row))
    log.info("cli.dump.done", total=len(jobs))


def main() -> None:
    parser = argparse.ArgumentParser(description="Recruiting agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="Fetch jobs from all configured ATS sources")

    sub.add_parser("filter", help="Run Stage 1 rule-based pre-filter")

    score_p = sub.add_parser("score", help="Run Stage 2 LLM scoring on filtered jobs")
    score_p.add_argument("--dry-run", action="store_true", help="Score but don't write to DB")

    digest_p = sub.add_parser("digest", help="Send daily email digest via Resend")
    digest_p.add_argument("--all-time", action="store_true", help="Include all scored jobs, not just today's")
    digest_p.add_argument("--preview", action="store_true", help="Print digest to stdout instead of sending")

    dump_p = sub.add_parser("dump", help="Dump jobs to stdout as JSON lines")
    dump_group = dump_p.add_mutually_exclusive_group()
    dump_group.add_argument("--scored", action="store_true", help="Only scored jobs, sorted by score desc")
    dump_group.add_argument("--passed", action="store_true", help="Only Stage 1 passed jobs")

    args = parser.parse_args()
    {
        "ingest": cmd_ingest,
        "filter": cmd_filter,
        "score": cmd_score,
        "digest": cmd_digest,
        "dump": cmd_dump,
    }[args.command](args)


if __name__ == "__main__":
    main()
