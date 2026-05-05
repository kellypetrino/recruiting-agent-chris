"""Tests for the normalization layer — most important layer to test per spec."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.normalize import (
    JobPosting,
    from_ashby,
    from_greenhouse,
    from_lever,
    infer_remote_policy,
    _strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def greenhouse_raw() -> dict:
    return json.loads((FIXTURES / "greenhouse_job.json").read_text())


@pytest.fixture
def lever_raw() -> dict:
    return json.loads((FIXTURES / "lever_job.json").read_text())


@pytest.fixture
def ashby_raw() -> dict:
    return {
        "id": "abc-123",
        "title": "Product Manager, AI Platform",
        "locationName": "New York, NY",
        "publishedAt": "2026-04-20T10:00:00Z",
        "isRemote": False,
        "jobUrl": "https://jobs.ashbyhq.com/example/abc-123",
        "descriptionHtml": "<p>Build AI products.</p>",
    }


# ── JobPosting.make_id ────────────────────────────────────────────────────────

def test_make_id_is_deterministic():
    id1 = JobPosting.make_id("greenhouse", "12345")
    id2 = JobPosting.make_id("greenhouse", "12345")
    assert id1 == id2


def test_make_id_differs_by_source():
    id_gh = JobPosting.make_id("greenhouse", "12345")
    id_lv = JobPosting.make_id("lever", "12345")
    assert id_gh != id_lv


def test_make_id_length():
    assert len(JobPosting.make_id("greenhouse", "abc")) == 32


# ── infer_remote_policy ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "location,expected",
    [
        ("Remote", "remote"),
        ("Remote - US", "remote"),
        ("New York, NY (Hybrid)", "hybrid"),
        ("Remote or Hybrid", "hybrid"),
        ("New York, NY", "unknown"),
        ("On-site, San Francisco", "onsite"),
        ("In-office, NYC", "onsite"),
    ],
)
def test_infer_remote_policy(location: str, expected: str):
    assert infer_remote_policy(location) == expected


# ── Greenhouse mapper ─────────────────────────────────────────────────────────

def test_greenhouse_maps_required_fields(greenhouse_raw: dict):
    posting = from_greenhouse(greenhouse_raw, "Anthropic")
    assert posting.company == "Anthropic"
    assert posting.title
    assert posting.url.startswith("https://")
    assert posting.source == "greenhouse"
    assert len(posting.job_id) == 32


def test_greenhouse_location(greenhouse_raw: dict):
    posting = from_greenhouse(greenhouse_raw, "Anthropic")
    assert isinstance(posting.location, str)


def test_greenhouse_posted_date(greenhouse_raw: dict):
    posting = from_greenhouse(greenhouse_raw, "Anthropic")
    # Greenhouse returns updated_at or created_at; either is valid
    assert posting.posted_date is None or isinstance(posting.posted_date, datetime)


# ── Lever mapper ──────────────────────────────────────────────────────────────

def test_lever_maps_required_fields(lever_raw: dict):
    posting = from_lever(lever_raw, "Mistral")
    assert posting.company == "Mistral"
    assert posting.title
    assert posting.url.startswith("https://") or posting.url == ""
    assert posting.source == "lever"
    assert len(posting.job_id) == 32


def test_lever_description_strips_html(lever_raw: dict):
    posting = from_lever(lever_raw, "Mistral")
    assert "<" not in posting.description_raw


def test_lever_posted_date(lever_raw: dict):
    posting = from_lever(lever_raw, "Mistral")
    assert isinstance(posting.posted_date, datetime)


def test_lever_location_from_categories(lever_raw: dict):
    posting = from_lever(lever_raw, "Mistral")
    assert isinstance(posting.location, str)


# ── Ashby mapper ──────────────────────────────────────────────────────────────

def test_ashby_maps_required_fields(ashby_raw: dict):
    posting = from_ashby(ashby_raw, "Example Co")
    assert posting.company == "Example Co"
    assert posting.title == "Product Manager, AI Platform"
    assert posting.location == "New York, NY"
    assert posting.source == "ashby"
    assert posting.url == "https://jobs.ashbyhq.com/example/abc-123"


def test_ashby_description_strips_html(ashby_raw: dict):
    posting = from_ashby(ashby_raw, "Example Co")
    assert "<" not in posting.description_raw
    assert "Build AI products" in posting.description_raw


def test_ashby_remote_flag_overrides_location():
    raw = {
        "id": "xyz",
        "title": "PM",
        "locationName": "San Francisco, CA",
        "isRemote": True,
        "jobUrl": "https://example.com",
    }
    posting = from_ashby(raw, "Co")
    assert posting.remote_policy == "remote"


def test_ashby_posted_date_parsed(ashby_raw: dict):
    posting = from_ashby(ashby_raw, "Example Co")
    assert isinstance(posting.posted_date, datetime)
    assert posting.posted_date.year == 2026


# ── _strip_html ───────────────────────────────────────────────────────────────

def test_strip_html_removes_tags():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_collapses_whitespace():
    assert "  " not in _strip_html("<p>   too   many   spaces   </p>")


def test_strip_html_empty():
    assert _strip_html("") == ""
