"""Tests for the digest rendering layer (no email sending, no DB)."""

from datetime import datetime, timedelta

import pytest

from src.digest import DigestJob, render_html, render_text, SCORE_TIER1, SCORE_TIER2

NOW = datetime.utcnow()


def make_job(
    score: float = 9.0,
    company: str = "Anthropic",
    title: str = "Product Manager, AI Platform",
    location: str = "New York, NY",
    rationale: str = "Strong PM role at top AI company in target location.",
    flags: str | None = None,
    url: str = "https://example.com/job/1",
    posted_date: datetime | None = None,
) -> DigestJob:
    return DigestJob(
        company=company,
        title=title,
        location=location,
        score=score,
        rationale=rationale,
        flags=flags,
        url=url,
        posted_date=posted_date or NOW - timedelta(days=3),
    )


# ── render_text ───────────────────────────────────────────────────────────────

class TestRenderText:
    def test_empty_returns_no_roles_message(self):
        text = render_text([], [])
        assert "No new jobs" in text

    def test_tier1_section_header(self):
        text = render_text([make_job(score=9)], [])
        assert "STRONG MATCHES" in text

    def test_tier2_section_header(self):
        text = render_text([], [make_job(score=7)])
        assert "POSSIBLE MATCHES" in text

    def test_job_title_appears(self):
        text = render_text([make_job(title="PM, Foundation Models")], [])
        assert "PM, Foundation Models" in text

    def test_score_appears(self):
        text = render_text([make_job(score=9)], [])
        assert "[9]" in text

    def test_flags_appear_when_present(self):
        text = render_text([make_job(flags="Requires 8+ years experience")], [])
        assert "Requires 8+ years" in text

    def test_no_flags_section_when_absent(self):
        text = render_text([make_job(flags=None)], [])
        assert "⚠" not in text

    def test_url_appears(self):
        text = render_text([make_job(url="https://jobs.example.com/abc")], [])
        assert "https://jobs.example.com/abc" in text

    def test_both_tiers_render(self):
        text = render_text([make_job(score=9)], [make_job(score=6)])
        assert "STRONG MATCHES" in text
        assert "POSSIBLE MATCHES" in text

    def test_multiple_jobs_in_tier(self):
        jobs = [make_job(title=f"PM Role {i}") for i in range(3)]
        text = render_text(jobs, [])
        assert "PM Role 0" in text
        assert "PM Role 1" in text
        assert "PM Role 2" in text


# ── render_html ───────────────────────────────────────────────────────────────

class TestRenderHtml:
    def test_returns_html_string(self):
        html = render_html([make_job()], [])
        assert html.startswith("<!DOCTYPE html>")

    def test_job_title_in_html(self):
        html = render_html([make_job(title="GTM Lead, Enterprise")], [])
        assert "GTM Lead, Enterprise" in html

    def test_url_linked_in_html(self):
        html = render_html([make_job(url="https://jobs.example.com/xyz")], [])
        assert 'href="https://jobs.example.com/xyz"' in html

    def test_empty_shows_no_roles_message(self):
        html = render_html([], [])
        assert "No new jobs" in html

    def test_summary_line_counts_roles(self):
        html = render_html([make_job(), make_job()], [make_job(score=6)])
        assert "3 new roles" in html

    def test_flags_rendered_with_warning(self):
        html = render_html([make_job(flags="Requires 10+ years")], [])
        assert "Requires 10+ years" in html

    def test_score_color_green_for_tier1(self):
        html = render_html([make_job(score=9)], [])
        assert "#16a34a" in html  # green for 8+

    def test_score_color_blue_for_tier2(self):
        html = render_html([], [make_job(score=6)])
        assert "#2563eb" in html  # blue for 6-7

    def test_date_in_html(self):
        from datetime import date
        html = render_html([make_job()], [])
        assert str(date.today().year) in html
