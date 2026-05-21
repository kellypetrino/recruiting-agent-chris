"""Daily email digest builder and sender.

Groups jobs into tiers based on content-based scoring (0–10):
  Tier 1 (score 7+): strong match — right role, right location, right industry
  Tier 2 (score 5–6): possible match — good role or location but not both
  Score 0 = disqualified (experience req / GPA / MBA / senior title), excluded.
Falls back gracefully if no jobs have been scored yet.
"""

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, datetime, timedelta
from typing import NamedTuple

import structlog

from src.db import Job, SessionLocal, init_db

log = structlog.get_logger(__name__)

SCORE_TIER1 = 7   # strong match
SCORE_TIER2 = 5   # possible match
MIN_SCORE = 5     # below this: too weak to surface (or disqualified)

_digest_to_raw = os.getenv("DIGEST_TO_EMAIL", "petrinochris@gmail.com,richardpetrino1@comcast.net")
DIGEST_TO = [e.strip() for e in _digest_to_raw.split(",") if e.strip()]
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


class DigestJob(NamedTuple):
    company: str
    title: str
    location: str
    score: float
    rationale: str
    flags: str | None
    url: str
    posted_date: datetime | None


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_digest_jobs(since_days: int = 1) -> tuple[list[DigestJob], list[DigestJob]]:
    """Return (tier1_jobs, tier2_jobs) scored within the last `since_days` days."""
    init_db()
    cutoff = datetime.utcnow() - timedelta(days=since_days)

    with SessionLocal() as session:
        jobs = (
            session.query(Job)
            .filter(
                Job.scored_at != None,  # noqa: E711
                Job.score >= MIN_SCORE,
                Job.scored_at >= cutoff,
            )
            .order_by(Job.score.desc())
            .all()
        )

    tier1, tier2 = [], []
    for j in jobs:
        dj = DigestJob(
            company=j.company,
            title=j.title,
            location=j.location,
            score=j.score,
            rationale=j.score_rationale or "",
            flags=j.score_flags,
            url=j.url,
            posted_date=j.posted_date,
        )
        if j.score >= SCORE_TIER1:
            tier1.append(dj)
        else:
            tier2.append(dj)

    return tier1, tier2


def fetch_all_scored_jobs() -> tuple[list[DigestJob], list[DigestJob]]:
    """Return all scored jobs (any date). Used for testing the digest."""
    init_db()
    with SessionLocal() as session:
        jobs = (
            session.query(Job)
            .filter(Job.scored_at != None, Job.score >= MIN_SCORE)  # noqa: E711
            .order_by(Job.score.desc())
            .all()
        )

    tier1, tier2 = [], []
    for j in jobs:
        dj = DigestJob(
            company=j.company,
            title=j.title,
            location=j.location,
            score=j.score,
            rationale=j.score_rationale or "",
            flags=j.score_flags,
            url=j.url,
            posted_date=j.posted_date,
        )
        if j.score >= SCORE_TIER1:
            tier1.append(dj)
        else:
            tier2.append(dj)

    return tier1, tier2


# ── Email rendering ───────────────────────────────────────────────────────────

_JOB_ROW = """
<tr>
  <td style="padding:12px 16px;border-bottom:1px solid #f0f0f0;">
    <div style="display:flex;align-items:baseline;gap:8px;">
      <span style="font-size:20px;font-weight:700;color:{score_color};">{score}</span>
      <span style="font-size:15px;font-weight:600;color:#111;">
        <a href="{url}" style="color:#111;text-decoration:none;">{title}</a>
      </span>
    </div>
    <div style="font-size:13px;color:#555;margin-top:3px;">
      {company} &nbsp;·&nbsp; {location}
      {posted_str}
    </div>
    <div style="font-size:13px;color:#333;margin-top:6px;">{rationale}</div>
    {flags_html}
  </td>
</tr>
"""

_FLAGS_HTML = '<div style="font-size:12px;color:#c0392b;margin-top:4px;">⚠ {flags}</div>'

_SECTION = """
<h2 style="font-size:16px;font-weight:700;color:#fff;background:{bg};padding:10px 16px;
           margin:24px 0 0 0;border-radius:6px 6px 0 0;">{heading}</h2>
<table style="width:100%;border-collapse:collapse;background:#fff;
              border:1px solid #e8e8e8;border-top:none;border-radius:0 0 6px 6px;">
  {rows}
</table>
"""

_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#f5f5f5;margin:0;padding:24px;">
<div style="max-width:680px;margin:0 auto;">
  <h1 style="font-size:22px;color:#111;margin-bottom:4px;">Chris's Job Digest — {date}</h1>
  <p style="font-size:13px;color:#888;margin-top:0;">{summary_line}</p>
  {body}
  <p style="font-size:12px;color:#aaa;margin-top:32px;text-align:center;">
    recruiting-agent · <a href="https://github.com" style="color:#aaa;">dashboard</a>
  </p>
</div>
</body>
</html>"""

_EMPTY_BODY = """
<div style="padding:32px;text-align:center;color:#888;background:#fff;
            border:1px solid #e8e8e8;border-radius:6px;">
  No new jobs scored {score}+ today. Check back tomorrow.
</div>
"""


def _render_job(j: DigestJob) -> str:
    score_color = "#16a34a" if j.score >= SCORE_TIER1 else "#2563eb"
    posted_str = ""
    if j.posted_date:
        age = (datetime.utcnow() - j.posted_date).days
        posted_str = f'&nbsp;·&nbsp; {age}d ago'
    flags_html = _FLAGS_HTML.format(flags=j.flags) if j.flags else ""
    return _JOB_ROW.format(
        score=int(j.score),
        score_color=score_color,
        url=j.url,
        title=j.title,
        company=j.company,
        location=j.location,
        posted_str=posted_str,
        rationale=j.rationale,
        flags_html=flags_html,
    )


def render_html(tier1: list[DigestJob], tier2: list[DigestJob]) -> str:
    today = date.today().strftime("%B %-d, %Y")
    total = len(tier1) + len(tier2)

    if total == 0:
        summary = f"No new roles scored {MIN_SCORE}+ today."
        body = _EMPTY_BODY.format(score=MIN_SCORE)
    else:
        summary = (
            f"{total} role{'s' if total != 1 else ''} — "
            f"{len(tier1)} strong match{'es' if len(tier1) != 1 else ''}, "
            f"{len(tier2)} possible."
        )
        sections = []
        if tier1:
            rows = "".join(_render_job(j) for j in tier1)
            sections.append(_SECTION.format(
                heading=f"Strong matches — score 7+ ({len(tier1)})",
                bg="#16a34a",
                rows=rows,
            ))
        if tier2:
            rows = "".join(_render_job(j) for j in tier2)
            sections.append(_SECTION.format(
                heading=f"Possible matches — score 5–6 ({len(tier2)})",
                bg="#2563eb",
                rows=rows,
            ))
        body = "\n".join(sections)

    return _TEMPLATE.format(date=today, summary_line=summary, body=body)


def render_text(tier1: list[DigestJob], tier2: list[DigestJob]) -> str:
    """Plain-text fallback."""
    lines = [f"Job Digest — {date.today().strftime('%B %-d, %Y')}", ""]
    total = len(tier1) + len(tier2)

    if total == 0:
        lines.append(f"No new jobs scored {MIN_SCORE}+ today.")
        return "\n".join(lines)

    if tier1:
        lines.append(f"STRONG MATCHES (score 7+) — {len(tier1)} role(s)")
        lines.append("-" * 50)
        for j in tier1:
            lines.append(f"[{int(j.score)}] {j.title} — {j.company}")
            lines.append(f"    {j.location}")
            lines.append(f"    {j.rationale}")
            if j.flags:
                lines.append(f"    ⚠ {j.flags}")
            lines.append(f"    {j.url}")
            lines.append("")

    if tier2:
        lines.append(f"POSSIBLE MATCHES (score 5–6) — {len(tier2)} role(s)")
        lines.append("-" * 50)
        for j in tier2:
            lines.append(f"[{int(j.score)}] {j.title} — {j.company}")
            lines.append(f"    {j.location}")
            lines.append(f"    {j.rationale}")
            if j.flags:
                lines.append(f"    ⚠ {j.flags}")
            lines.append(f"    {j.url}")
            lines.append("")

    return "\n".join(lines)


# ── Sending ───────────────────────────────────────────────────────────────────

def send_digest(tier1: list[DigestJob], tier2: list[DigestJob]) -> str:
    """Send the digest email via Gmail SMTP. Returns a status string."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD must be set")

    total = len(tier1) + len(tier2)
    subject = (
        f"Chris's job digest: {total} new role{'s' if total != 1 else ''} "
        f"({len(tier1)} strong match{'es' if len(tier1) != 1 else ''})"
        if total else "Chris's job digest: no new roles today"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(DIGEST_TO)
    msg.attach(MIMEText(render_text(tier1, tier2), "plain"))
    msg.attach(MIMEText(render_html(tier1, tier2), "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, DIGEST_TO, msg.as_string())

    log.info("digest.sent", to=DIGEST_TO, total=total)
    return f"sent to {DIGEST_TO}"


def run_digest(all_time: bool = True) -> dict:
    """Fetch jobs, render, and send. Returns stats dict."""
    if all_time:
        tier1, tier2 = fetch_all_scored_jobs()
    else:
        tier1, tier2 = fetch_digest_jobs(since_days=3)

    log.info("digest.fetched", tier1=len(tier1), tier2=len(tier2))

    if not (tier1 or tier2):
        log.info("digest.empty")
        return {"tier1": 0, "tier2": 0, "sent": False}

    message_id = send_digest(tier1, tier2)
    return {"tier1": len(tier1), "tier2": len(tier2), "sent": True, "message_id": message_id}
