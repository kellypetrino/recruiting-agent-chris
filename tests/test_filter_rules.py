"""Tests for Stage 1 rule-based pre-filter."""

from datetime import datetime, timedelta

import pytest

from src.filter_rules import apply_filter, _title_passes, _location_passes, _age_passes

# Shared filter config matching config/target_companies.yaml
FILTER_CONFIG = {
    "title_keywords_include": [
        "product manager", "product", "PM", "GTM", "go-to-market",
        "deployment", "forward deployed", "solutions", "applied", "technical program",
    ],
    "title_keywords_exclude": [
        "director", "VP", "vice president", "head of", "principal", "intern", "junior",
    ],
    "locations_include": ["new york", "nyc", "ny", "remote", "remote - us", "remote (us)", "united states"],
    "max_age_days": 14,
}

NOW = datetime.utcnow()


# ── Title filtering ───────────────────────────────────────────────────────────

class TestTitleFilter:
    def test_passes_product_manager(self):
        ok, _ = _title_passes("Product Manager, Foundation Models", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_gtm(self):
        ok, _ = _title_passes("GTM Lead, Enterprise", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_deployment(self):
        ok, _ = _title_passes("Deployment Engineer", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_solutions(self):
        ok, _ = _title_passes("Solutions Engineer", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_fails_no_keywords(self):
        ok, reason = _title_passes("Software Engineer, Infrastructure", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_director(self):
        ok, reason = _title_passes("Director of Product", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok
        assert "director" in reason

    def test_fails_vp(self):
        ok, _ = _title_passes("VP of Product Management", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_head_of(self):
        ok, _ = _title_passes("Head of GTM", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_principal(self):
        ok, _ = _title_passes("Principal PM", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_intern(self):
        ok, _ = _title_passes("Product Manager Intern", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_case_insensitive_exclude(self):
        ok, _ = _title_passes("DIRECTOR of Engineering", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_exclude_beats_include(self):
        # "VP of Product" — 'product' would include but 'VP' should exclude
        ok, _ = _title_passes("VP of Product", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok


# ── Location filtering ────────────────────────────────────────────────────────

class TestLocationFilter:
    def test_passes_nyc(self):
        ok, _ = _location_passes("New York, NY", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_nyc_abbreviation(self):
        ok, _ = _location_passes("NYC", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_remote(self):
        ok, _ = _location_passes("Remote", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_remote_us(self):
        ok, _ = _location_passes("Remote - US", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_remote_friendly(self):
        ok, _ = _location_passes("Remote-Friendly, United States", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_multi_city_with_ny(self):
        # Common Greenhouse format
        ok, _ = _location_passes("San Francisco, CA | New York City, NY", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_multi_city_pipe_separated(self):
        ok, _ = _location_passes("San Francisco, CA | New York City, NY | Seattle, WA", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_multi_city_semicolon(self):
        ok, _ = _location_passes("San Francisco, CA; New York, NY", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_united_states(self):
        ok, _ = _location_passes("United States", FILTER_CONFIG["locations_include"])
        assert ok

    def test_fails_paris_only(self):
        ok, reason = _location_passes("Paris", FILTER_CONFIG["locations_include"])
        assert not ok

    def test_fails_london_only(self):
        ok, _ = _location_passes("London, UK", FILTER_CONFIG["locations_include"])
        assert not ok

    def test_fails_sf_only(self):
        ok, _ = _location_passes("San Francisco, CA", FILTER_CONFIG["locations_include"])
        assert not ok

    def test_passes_empty_location(self):
        # Unknown location — pass through to LLM
        ok, _ = _location_passes("", FILTER_CONFIG["locations_include"])
        assert ok


# ── Age filtering ─────────────────────────────────────────────────────────────

class TestAgeFilter:
    def test_passes_recent(self):
        ok, _ = _age_passes(NOW - timedelta(days=3), 14)
        assert ok

    def test_passes_within_cutoff(self):
        ok, _ = _age_passes(NOW - timedelta(days=13, hours=23), 14)
        assert ok

    def test_fails_too_old(self):
        ok, reason = _age_passes(NOW - timedelta(days=30), 14)
        assert not ok
        assert "30d" in reason

    def test_passes_none_date(self):
        ok, _ = _age_passes(None, 14)
        assert ok


# ── Integration: apply_filter ─────────────────────────────────────────────────

class TestApplyFilter:
    def test_good_job_passes(self):
        result = apply_filter(
            title="Product Manager, AI Platform",
            location="New York, NY",
            posted_date=NOW - timedelta(days=5),
            config=FILTER_CONFIG,
        )
        assert result.passed

    def test_director_fails(self):
        result = apply_filter(
            title="Director of Product",
            location="New York, NY",
            posted_date=NOW - timedelta(days=5),
            config=FILTER_CONFIG,
        )
        assert not result.passed

    def test_paris_only_fails(self):
        result = apply_filter(
            title="Product Manager",
            location="Paris",
            posted_date=NOW - timedelta(days=5),
            config=FILTER_CONFIG,
        )
        assert not result.passed

    def test_old_job_fails(self):
        result = apply_filter(
            title="Product Manager",
            location="New York, NY",
            posted_date=NOW - timedelta(days=60),
            config=FILTER_CONFIG,
        )
        assert not result.passed

    def test_good_remote_job_passes(self):
        result = apply_filter(
            title="GTM Lead",
            location="Remote - US",
            posted_date=NOW - timedelta(days=2),
            config=FILTER_CONFIG,
        )
        assert result.passed

    def test_reason_included_in_result(self):
        result = apply_filter(
            title="VP of Product",
            location="New York, NY",
            posted_date=NOW - timedelta(days=1),
            config=FILTER_CONFIG,
        )
        assert not result.passed
        assert result.reason
