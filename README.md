# UK Job Scraper

Automated UK job search tool for **Java / Spring Boot** engineers requiring **Skilled Worker visa sponsorship**. Runs daily via GitHub Actions and delivers a prioritised Excel report to your inbox (via Actions artifact download).

> **Legal disclaimer**: This tool is for informational purposes only. It does not constitute immigration or legal advice. Always verify sponsorship eligibility directly with employers and consult a qualified immigration solicitor before making decisions based on this output.

---

## Overview

The scraper:

1. **Searches** Reed.co.uk and Adzuna for Java/Spring Boot/backend engineering roles across the UK
2. **Scores** each role against the candidate's profile using TF-IDF keyword matching (no AI required)
3. **Checks sponsorship** against the official UK government Skilled Worker sponsor register
4. **Generates** Excel, CSV, and Markdown reports sorted by relevance score
5. **Persists** a SQLite database across daily runs so you can track new listings

---

## Quick Start (Local)

### Prerequisites

- Python 3.11+
- `pip install -r requirements.txt`
- (Optional) Reed and Adzuna API keys for live job data; see **Configuration** below.

### Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Test with sample data (no API keys needed)
python main.py run --dry-run

# Full run with API keys set in environment
export REED_API_KEY="your_key_here"
export ADZUNA_APP_ID="your_app_id"
export ADZUNA_API_KEY="your_api_key"
python main.py run

# See help for all options
python main.py --help
python main.py run --help
```

Reports appear in the `output/` directory:
- `output/jobs_YYYY-MM-DD.xlsx`
- `output/jobs_YYYY-MM-DD.csv`
- `output/jobs_YYYY-MM-DD.md`

---

## Configuration

### Candidate Profile (`config/candidate_profile.yaml`)

Edit this file to reflect your own skills, experience, and preferences.

Key fields to update:
- `candidate.contact` — your actual email and LinkedIn URL
- `candidate.skills` — add or remove technologies you know
- `salary_expectation_gbp_min` — your minimum acceptable salary (used for scoring)
- `location_preferences` — cities you are willing to work in
- `primary_job_targets` — job titles to search for

### Employer List (`config/employers.csv`)

The CSV contains 50+ UK employers. You can:
- Add employers: just append a row with the correct careers URL
- Remove employers: delete the row
- Update `sponsorship_known` to `yes`/`no`/`unknown` as you learn more

CSV columns:
| Column | Description |
|--------|-------------|
| `employer_name` | Full employer name |
| `careers_url` | Direct URL to the careers/jobs page |
| `ats_type` | ATS system (workday, greenhouse, lever, etc.) |
| `sponsorship_known` | yes / no / unknown |
| `notes` | Any notes |

### API Keys

Both APIs are optional. Without them, only the careers-page scraper runs (which is free but slower).

| API | Where to get it | Environment variable |
|-----|-----------------|----------------------|
| Reed.co.uk | https://www.reed.co.uk/developers/jobseeker | `REED_API_KEY` |
| Adzuna | https://developer.adzuna.com/ | `ADZUNA_APP_ID` and `ADZUNA_API_KEY` |

Both Reed and Adzuna offer free developer API keys with reasonable rate limits.

---

## GitHub Actions Deployment Guide

Follow these steps to run the scraper automatically every morning on GitHub's free infrastructure.

### Step a: Run locally first via Claude Code (optional)

Before deploying, test the full pipeline locally to verify it works:

```bash
python main.py run --dry-run
```

This runs without network access and produces sample reports in `output/`.

### Step b: Create a private GitHub repository

1. Go to https://github.com/new
2. Set repository name (e.g. `uk-job-scraper`)
3. Set visibility to **Private** — this is important to protect your personal data
4. Do not initialise with a README (you will push the existing project)

### Step c: Upload the project (do NOT commit your CV)

```bash
# Initialise git in the project directory
git init
git add .
git commit -m "Initial commit: UK job scraper"

# Add your remote and push
git remote add origin https://github.com/YOUR_USERNAME/uk-job-scraper.git
git branch -M main
git push -u origin main
```

**Security note**: Never commit your CV, passport details, or API keys to the repository. The `.gitignore` below will help:

Create a `.gitignore` file:
```
# Secrets and personal files
*.env
.env.local
storage/jobs.db
logs/
output/
storage/sponsor_register_cache.csv

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
*.egg-info/
```

### Step d: Configure repository secrets (API keys)

1. Go to your repository on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** for each key:

| Secret name | Value |
|-------------|-------|
| `REED_API_KEY` | Your Reed API key |
| `ADZUNA_APP_ID` | Your Adzuna app ID |
| `ADZUNA_API_KEY` | Your Adzuna API key |

If you do not have API keys, skip this step — the scraper will still run using careers-page scraping.

### Step e: Understanding persistent storage

The scraper uses GitHub Actions **cache** to persist the SQLite database (`storage/jobs.db`) across runs:

- Each run saves the database to the cache under a key like `jobs-db-<run_id>`
- The next run restores the most recent cache using the prefix `jobs-db-`
- This allows the scraper to track which jobs are **new** vs. already seen
- The cache is retained for 7 days by GitHub (free tier); the database grows incrementally

**Important**: GitHub Actions cache is not a permanent backup. If the cache expires (after 7 days of no runs), the database is reset and all jobs will be treated as new. This is fine for daily runs.

### Step f: Enabling scheduled workflows

GitHub Actions scheduled workflows may be disabled on new repositories:

1. Go to your repository's **Actions** tab
2. If you see a message about workflows being disabled, click **Enable**
3. The workflow `.github/workflows/daily-jobs.yml` will then run at 06:15 UTC daily

### Step g: Running the first manual scan

Trigger the workflow manually to test everything:

1. Go to **Actions** tab → **Daily UK Job Scraper**
2. Click **Run workflow** → **Run workflow** (green button)
3. Optionally check "Only show new jobs" or enter an employer filter
4. Wait 5-10 minutes for the run to complete

### Step h: Downloading your Excel report

After a successful run:

1. Go to **Actions** tab → click the completed run
2. Scroll down to **Artifacts**
3. Click `job-reports-<run_id>-<run_number>` to download
4. Extract the ZIP — find `output/jobs_YYYY-MM-DD.xlsx`
5. Open in Excel or LibreOffice Calc

The report has four sheets:
- **All Jobs**: every job found, sorted by relevance score
- **Shortlist**: top 10 matches
- **New Today**: jobs seen for the first time in this run
- **Confirmed Sponsorship**: roles that explicitly mention visa sponsorship

### Step i: Checking logs and troubleshooting

**View run logs:**
1. Actions tab → click any run → click the `scrape` job → expand steps

**Common issues:**

| Issue | Solution |
|-------|----------|
| No jobs found | Check that API keys are set as secrets; run `--dry-run` to verify the pipeline |
| Rate limit errors | Increase `--rate-limit-delay`; default is 2 seconds |
| Excel file empty | Run `python main.py status` to check database contents |
| robots.txt blocked | Some employer sites block scrapers; this is expected and logged |
| Sponsor register download fails | Temporary gov.uk outage; stale cache is used automatically |

---

## CLI Reference

```
python main.py run                          Run a full scrape + report
python main.py run --dry-run               Test with sample data (no API needed)
python main.py run --new-only              Report only jobs new to this run
python main.py run --employer "Barclays"   Scrape only one employer
python main.py run --timeout 60            Set HTTP timeout to 60 seconds
python main.py run --retries 5             Set HTTP retry count to 5
python main.py run --rate-limit-delay 3.0  Minimum 3s between requests
python main.py report                      Generate reports from existing DB
python main.py status                      Show last run stats
```

---

## Scoring Explanation

Each job is scored out of 100 across six dimensions:

| Dimension | Max Points | How it is calculated |
|-----------|-----------|----------------------|
| Skills Match | 35 | Overlap between your profile skills and skills mentioned in the JD, weighted by TF-IDF frequency |
| Experience Match | 25 | Your years of experience vs. what the JD requires |
| Responsibilities Match | 15 | Overlap between the JD and your functional competencies (API dev, banking, microservices, cloud, etc.) |
| Location Match | 10 | Remote/hybrid/preferred UK cities |
| Salary Match | 10 | Advertised salary vs. your minimum expectation |
| Sponsorship Bonus | 5 | Explicit mention of visa sponsorship in the JD |

Scores are deterministic and reproducible. No LLM is used — all matching is rule-based.

**Score interpretation:**
- 80-100: Excellent match — strongly recommended to apply
- 60-79: Good match — worth applying
- 40-59: Partial match — tailor your CV to highlight relevant skills
- 0-39: Weak match — consider whether this role is a good fit

---

## Sponsorship Classification

Each job receives one of four sponsorship classifications:

| Status | Meaning |
|--------|---------|
| `confirmed_role` | The job description explicitly mentions visa sponsorship, Skilled Worker visa, Certificate of Sponsorship, or similar |
| `licensed_employer_unconfirmed` | The employer is on the UK government Skilled Worker sponsor register, but the JD does not explicitly mention sponsorship — contact the employer to confirm |
| `sponsorship_unavailable` | The JD contains explicit negative signals (e.g. "no sponsorship", "must have right to work") |
| `unable_to_verify` | No information available — neither the JD nor the register clarifies the situation |

The sponsor register is downloaded from:
https://assets.publishing.service.gov.uk/media/register-of-licensed-sponsors-workers.csv

It is cached locally for 7 days and refreshed automatically.

**Important**: Being on the sponsor register means the employer *can* sponsor — it does not mean they *will* sponsor for any given role. Always confirm directly with the employer.

---

## Limitations

- **Legal disclaimer**: This tool provides information only. It is not immigration or legal advice. Consult a qualified solicitor before making decisions.
- **Sponsor register lag**: The register may be up to 7 days out of date. Employers can be added or removed at any time.
- **Job description parsing**: Sponsorship keywords are detected by text matching. Employers may offer sponsorship without using standard keywords.
- **Careers page scraping**: Many employer ATS systems (Workday, Greenhouse, etc.) load jobs dynamically via JavaScript. The scraper may not extract all jobs from every careers page — API-based scrapers (Reed, Adzuna) are more reliable.
- **No legal rights**: Information from this tool does not constitute a job offer, guarantee of employment, or any form of legal entitlement.
- **robots.txt**: The scraper respects robots.txt. Some employer sites will be skipped.
- **API rate limits**: Reed and Adzuna free tiers have request limits. The scraper adds delays to avoid exceeding them.

---

## Project Structure

```
uk-job-scraper/
├── config/
│   ├── candidate_profile.yaml     # Your CV / skills profile
│   └── employers.csv              # UK employer list with careers URLs
├── scrapers/
│   ├── __init__.py                # ScraperOrchestrator
│   ├── base.py                    # Abstract BaseJobScraper
│   ├── public_jobs.py             # Reed + Adzuna API scrapers
│   └── careers_page.py            # HTML careers page scraper
├── matching/
│   ├── __init__.py
│   └── scorer.py                  # TF-IDF job scorer
├── sponsorship/
│   ├── __init__.py
│   └── checker.py                 # Sponsor register + JD keyword checker
├── storage/
│   ├── __init__.py
│   └── database.py                # SQLite job database
├── reporting/
│   ├── __init__.py
│   └── generator.py               # Excel, CSV, Markdown report generator
├── tests/
│   ├── test_scoring.py
│   ├── test_sponsorship.py
│   ├── test_deduplication.py
│   └── test_reporting.py
├── .github/workflows/
│   └── daily-jobs.yml             # GitHub Actions workflow
├── main.py                        # CLI entry point
├── requirements.txt
└── README.md
```

---

*Built for Sudeep S — Java/Spring Boot engineer, MSc Advanced Computer Science, University of Sheffield.*
*Requires Skilled Worker visa sponsorship.*
