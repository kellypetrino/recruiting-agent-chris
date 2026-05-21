"""Streamlit dashboard for browsing and tracking jobs."""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/jobs.db")

st.set_page_config(page_title="Job Pipeline", page_icon="🔍", layout="wide")

# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_jobs() -> pd.DataFrame:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    df = pd.read_sql(
        """
        SELECT
            company, title, location, remote_policy,
            posted_date, url, source,
            passed_prefilter, score, score_rationale, score_flags,
            scored_at, fetched_at
        FROM jobs
        ORDER BY score DESC NULLS LAST, posted_date DESC
        """,
        engine,
    )
    if not df.empty:
        for col in ("posted_date", "scored_at", "fetched_at"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# ── Sidebar filters ───────────────────────────────────────────────────────────

st.sidebar.title("Filters")

df = load_jobs()

companies = sorted(df["company"].unique().tolist())
selected_companies = st.sidebar.multiselect("Company", companies, default=companies)

view = st.sidebar.radio(
    "Show",
    ["Strong (7+)", "Possible (5+)", "All passed filter", "Everything"],
    index=1,
)

min_age = st.sidebar.slider("Posted within (days)", 1, 90, 60)

# ── Apply filters ─────────────────────────────────────────────────────────────

filtered = df[df["company"].isin(selected_companies)].copy()

age_cutoff = datetime.utcnow() - timedelta(days=min_age)
filtered = filtered[
    filtered["posted_date"].isna() | (filtered["posted_date"] >= age_cutoff)
]

if view == "Strong (7+)":
    filtered = filtered[filtered["score"] >= 7]
elif view == "Possible (5+)":
    filtered = filtered[filtered["score"] >= 5]
elif view == "All passed filter":
    filtered = filtered[filtered["passed_prefilter"] == 1]

# ── Header metrics ────────────────────────────────────────────────────────────

st.title("Job Pipeline")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total ingested", len(df))
col2.metric("Passed filter", int(df["passed_prefilter"].sum()))
col3.metric("Scored", int(df["score"].notna().sum()))
col4.metric("Showing", len(filtered))

st.divider()

# ── Job cards ─────────────────────────────────────────────────────────────────

if filtered.empty:
    st.info("No jobs match current filters.")
else:
    for _, row in filtered.iterrows():
        score = row["score"]
        if pd.notna(score) and score >= 7:
            score_str = f"**{int(score)}/10**"
            border = "#16a34a"
        elif pd.notna(score) and score >= 5:
            score_str = f"**{int(score)}/10**"
            border = "#2563eb"
        elif pd.notna(score) and score > 0:
            score_str = f"{int(score)}/10"
            border = "#d1d5db"
        elif pd.notna(score):
            score_str = "✗"
            border = "#d1d5db"
        else:
            score_str = "—"
            border = "#d1d5db"

        with st.container():
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(
                    f"**[{row['title']}]({row['url']})** &nbsp; `{row['company']}` &nbsp; {row['location']}"
                )
                if pd.notna(row["score_rationale"]):
                    st.caption(f"💬 {row['score_rationale']}")
                if pd.notna(row["score_flags"]):
                    st.caption(f"⚠️ {row['score_flags']}")
            with c2:
                st.markdown(score_str)
                if pd.notna(row["posted_date"]):
                    age = (datetime.utcnow() - row["posted_date"]).days
                    st.caption(f"{age}d ago")

            st.divider()

# ── Raw table (expandable) ────────────────────────────────────────────────────

with st.expander("Raw table"):
    display_cols = ["company", "title", "location", "score", "posted_date", "url"]
    st.dataframe(
        filtered[display_cols].rename(columns={"score_rationale": "rationale"}),
        use_container_width=True,
        hide_index=True,
    )
