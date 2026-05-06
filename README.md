# recruiting-agent-chris

A personal daily job-scraping pipeline for Chris Petrino — targeting Financial Analyst,
Operations Analyst, Business Analyst, and Program/Project Manager roles. Pulls from
Greenhouse, Lever, Ashby, Workday, and Eightfold AI APIs, filters by title/location/age,
scores with Claude Haiku, and delivers a ranked email digest every morning to both Chris
and his dad.

**Profile:** Boston College, Carroll School of Management, May 2026. Finance & Operations
Management. FinTech intern at Bilt. Targeting NYC/NJ or US-remote, ASAP start.

## How it works

1. **Ingest** — pulls open jobs from configured ATS boards (no auth required)
2. **Filter** — drops irrelevant roles by title keyword, location, and posting age
3. **Score** — Claude Haiku 4.5 scores each remaining job 0–10 against Chris's profile
4. **Digest** — sends a ranked HTML email via Gmail at 7am ET daily

## Setup

```bash
conda activate recruiting-agent
pip install -e ".[dev]"
cp .env.example .env
# fill in ANTHROPIC_API_KEY, GMAIL_USER, and GMAIL_APP_PASSWORD in .env
```

## Usage

```bash
# Run the full pipeline manually
python -m src.cli ingest
python -m src.cli filter
python -m src.cli score
python -m src.cli digest --preview   # preview email in terminal
python -m src.cli digest             # send it

# Inspect data
python -m src.cli dump --scored      # scored jobs, sorted by score
python -m src.cli dump --passed      # all jobs that passed Stage 1 filter

# Dashboard
streamlit run dashboard/app.py
```

## Automation

Runs daily at 7am ET via GitHub Actions. Requires four repository secrets:
- `ANTHROPIC_API_KEY`
- `GMAIL_USER` — Gmail address used to send the digest (e.g. `petrinokelly@gmail.com`)
- `GMAIL_APP_PASSWORD` — [Google App Password](https://myaccount.google.com/apppasswords) (not your regular password)
- `DIGEST_TO_EMAIL` — comma-separated recipients: `petrinochris@gmail.com,richardpetrino1@comcast.net`

Set these in: **Settings → Secrets and variables → Actions**

## Target companies

Configured in [config/target_companies.yaml](config/target_companies.yaml).

**Have API access (auto-scraped daily):**
- Greenhouse: FanDuel, Brex, SoFi, Peloton, Commvault, Affirm, Toast, HubSpot, Klaviyo, Self Financial
- Lever: Olo
- Ashby: Ramp, Clay, Campus
- Workday: Capital One, Mastercard, Visa, Wells Fargo, Fidelity, State Street, Accenture, PwC, Fiserv, Broadridge, Prudential, Northern Trust, Verizon
- Eightfold AI: PayPal, Morgan Stanley

**Custom ATS — set up email job alerts manually:**
- Bilt (careers.biltrewards.com) — Chris interned here
- NBA (nba.com/careers) — currently interviewing
- American Express, JPMorgan, Citi, Google, Deloitte, KPMG
- ADP, Honeywell, Cognizant, Rivian
- BlackRock, BNY Mellon, Scotiabank, Santander
- Booking.com, Expedia, DraftKings, Wayfair, and others

## Stack

Python 3.11 · httpx · SQLite + SQLAlchemy · Claude Haiku 4.5 · Gmail SMTP · Streamlit · GitHub Actions
