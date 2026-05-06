"""Tests for Stage 1 rule-based pre-filter."""

from datetime import datetime, timedelta

import pytest

from src.filter_rules import apply_filter, _title_passes, _location_passes, _age_passes

# Shared filter config matching config/target_companies.yaml
FILTER_CONFIG = {
    "title_keywords_include": [
        "analyst", "financial analyst", "finance", "operations", "business analyst",
        "program manager", "project manager", "associate", "rotational", "workflow",
        "coordinator", "fp&a", "planning", "reporting", "accounting",
    ],
    "title_keywords_exclude": [
        "director", "VP", "vice president", "head of", "principal", "intern",
    ],
    "locations_include": [
        "new york", "nyc", "ny", "new jersey", "nj", "secaucus",
        "remote", "remote - us", "remote (us)", "united states",
    ],
    "max_age_days": 14,
}

NOW = datetime.utcnow()


# ── Title filtering ───────────────────────────────────────────────────────────

class TestTitleFilter:
    def test_passes_financial_analyst(self):
        ok, _ = _title_passes("Financial Analyst, FP&A", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_operations_analyst(self):
        ok, _ = _title_passes("Operations Analyst", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_business_analyst(self):
        ok, _ = _title_passes("Business Analyst, Technology", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_program_manager(self):
        ok, _ = _title_passes("Program Manager, Workflow Development", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_associate(self):
        ok, _ = _title_passes("Finance Associate", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_passes_rotational(self):
        ok, _ = _title_passes("Rotational Analyst Program", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert ok

    def test_fails_no_keywords(self):
        ok, reason = _title_passes("Software Engineer, Infrastructure", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_director(self):
        ok, reason = _title_passes("Director of Finance", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok
        assert "director" in reason

    def test_fails_vp(self):
        ok, _ = _title_passes("VP of Operations", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_head_of(self):
        ok, _ = _title_passes("Head of Finance", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_principal(self):
        ok, _ = _title_passes("Principal Analyst", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_fails_intern(self):
        ok, _ = _title_passes("Financial Analyst Intern", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_case_insensitive_exclude(self):
        ok, _ = _title_passes("DIRECTOR of Operations", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
        assert not ok

    def test_exclude_beats_include(self):
        # "VP of Finance" — 'finance' would include but 'VP' should exclude
        ok, _ = _title_passes("VP of Finance", FILTER_CONFIG["title_keywords_include"], FILTER_CONFIG["title_keywords_exclude"])
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

    def test_passes_new_jersey(self):
        ok, _ = _location_passes("New Jersey, United States", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_nj_abbreviation(self):
        ok, _ = _location_passes("Secaucus, NJ", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_nj_city(self):
        ok, _ = _location_passes("Jersey City, NJ", FILTER_CONFIG["locations_include"])
        assert ok

    def test_passes_tinton_falls(self):
        ok, _ = _location_passes("Tinton Falls, NJ", FILTER_CONFIG["locations_include"])
        assert ok

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
    def test_good_nyc_job_passes(self):
        result = apply_filter(
            title="Financial Analyst, FP&A",
            location="New York, NY",
            posted_date=NOW - timedelta(days=5),
            config=FILTER_CONFIG,
        )
        assert result.passed

    def test_good_nj_job_passes(self):
        result = apply_filter(
            title="Operations Analyst",
            location="Secaucus, NJ",
            posted_date=NOW - timedelta(days=3),
            config=FILTER_CONFIG,
        )
        assert result.passed

    def test_director_fails(self):
        result = apply_filter(
            title="Director of Finance",
            location="New York, NY",
            posted_date=NOW - timedelta(days=5),
            config=FILTER_CONFIG,
        )
        assert not result.passed

    def test_paris_only_fails(self):
        result = apply_filter(
            title="Business Analyst",
            location="Paris",
            posted_date=NOW - timedelta(days=5),
            config=FILTER_CONFIG,
        )
        assert not result.passed

    def test_old_job_fails(self):
        result = apply_filter(
            title="Financial Analyst",
            location="New York, NY",
            posted_date=NOW - timedelta(days=60),
            config=FILTER_CONFIG,
        )
        assert not result.passed

    def test_good_remote_job_passes(self):
        result = apply_filter(
            title="Operations Analyst",
            location="Remote - US",
            posted_date=NOW - timedelta(days=2),
            config=FILTER_CONFIG,
        )
        assert result.passed

    def test_reason_included_in_result(self):
        result = apply_filter(
            title="VP of Finance",
            location="New York, NY",
            posted_date=NOW - timedelta(days=1),
            config=FILTER_CONFIG,
        )
        assert not result.passed
        assert result.reason
