#!/usr/bin/env python3
"""
UK Job Scraper — main entry point.

Usage:
    python main.py run                          # Full scrape run
    python main.py run --new-only               # Only new jobs
    python main.py run --employer "Barclays"    # Single employer
    python main.py run --dry-run                # Test with sample data
    python main.py report                       # Generate report from DB
    python main.py status                       # Show last run stats
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Logging setup — must happen before any other imports
# ---------------------------------------------------------------------------


def _setup_logging(log_dir: str = "logs") -> logging.Logger:
    """Configure logging to stdout and a daily log file."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"scraper_{datetime.now().strftime('%Y-%m-%d')}.log"

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    return logging.getLogger("uk_job_scraper")


logger = _setup_logging()

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------

from matching.scorer import JobScorer
from reporting.generator import ReportGenerator
from scrapers import ScraperOrchestrator
from sponsorship.checker import SponsorshipChecker
from storage.database import JobDatabase


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def load_candidate_profile(path: str = "config/candidate_profile.yaml") -> dict:
    """Load and return the candidate profile YAML."""
    profile_path = Path(path)
    if not profile_path.exists():
        logger.error("Candidate profile not found at %s", path)
        sys.exit(1)
    with open(profile_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_employers(path: str = "config/employers.csv") -> list[dict]:
    """Load employer list from CSV."""
    employers_path = Path(path)
    if not employers_path.exists():
        logger.warning("Employers CSV not found at %s; using empty list.", path)
        return []
    employers = []
    with open(employers_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            employers.append(dict(row))
    logger.info("Loaded %d employers from %s", len(employers), path)
    return employers


def get_search_keywords(profile: dict) -> list[str]:
    """Extract primary and secondary search keywords from the profile."""
    primary = profile.get("primary_job_targets", [])
    secondary = profile.get("secondary_job_targets", [])
    # Use primary + a few secondary to avoid too many API calls
    return list(dict.fromkeys(primary + secondary[:5]))


# ---------------------------------------------------------------------------
# Dry-run sample data
# ---------------------------------------------------------------------------

DRY_RUN_JOBS = [
    {
        "job_id": "dry_001",
        "title": "Java Backend Engineer",
        "employer": "Revolut",
        "location": "London, UK (Hybrid)",
        "salary_min": 70000,
        "salary_max": 90000,
        "salary_currency": "GBP",
        "description": (
            "We are looking for a passionate Java Backend Engineer to join our Payments tribe. "
            "You will design and build high-throughput microservices using Java 17, Spring Boot, "
            "and Kafka. Experience with PostgreSQL, Docker, and Kubernetes is required. "
            "3+ years of professional Java experience preferred. "
            "We provide visa sponsorship for skilled candidates. "
            "Tier 2 / Skilled Worker sponsorship available."
        ),
        "url": "https://www.revolut.com/careers/java-backend-engineer-dry-001",
        "apply_url": "https://www.revolut.com/careers/java-backend-engineer-dry-001/apply",
        "posted_date": datetime.utcnow().date().isoformat(),
        "closing_date": None,
        "source": "dry_run",
        "scraped_at": datetime.utcnow().isoformat(),
        "raw_data": {},
    },
    {
        "job_id": "dry_002",
        "title": "Senior Spring Boot Developer",
        "employer": "Barclays",
        "location": "Northampton, UK (Hybrid)",
        "salary_min": 65000,
        "salary_max": 80000,
        "salary_currency": "GBP",
        "description": (
            "Barclays Technology is hiring a Senior Spring Boot Developer for our "
            "Digital Banking team. You will build RESTful APIs, implement microservices "
            "architecture, and work with Oracle and MongoDB databases. "
            "CI/CD experience with Jenkins and Docker required. "
            "5+ years of Spring Boot / Java experience. Azure AZ-900 a plus. "
            "Barclays is a licensed skilled worker sponsor."
        ),
        "url": "https://jobs.barclays.com/senior-spring-boot-developer-dry-002",
        "apply_url": "https://jobs.barclays.com/senior-spring-boot-developer-dry-002/apply",
        "posted_date": datetime.utcnow().date().isoformat(),
        "closing_date": None,
        "source": "dry_run",
        "scraped_at": datetime.utcnow().isoformat(),
        "raw_data": {},
    },
    {
        "job_id": "dry_003",
        "title": "Backend Software Engineer (Payments)",
        "employer": "Monzo",
        "location": "London, UK (Remote-first)",
        "salary_min": 75000,
        "salary_max": 95000,
        "salary_currency": "GBP",
        "description": (
            "Monzo is building the bank of the future. We are looking for a Backend "
            "Software Engineer to join our Payments team. "
            "You will work with Java, Go, and Python microservices deployed on AWS/Kubernetes. "
            "Experience with financial systems, REST APIs, and distributed systems required. "
            "We actively support visa sponsorship for all engineer roles."
        ),
        "url": "https://monzo.com/careers/backend-software-engineer-dry-003",
        "apply_url": "https://monzo.com/careers/backend-software-engineer-dry-003/apply",
        "posted_date": datetime.utcnow().date().isoformat(),
        "closing_date": None,
        "source": "dry_run",
        "scraped_at": datetime.utcnow().isoformat(),
        "raw_data": {},
    },
    {
        "job_id": "dry_004",
        "title": "Java Developer",
        "employer": "Capgemini",
        "location": "Sheffield, UK",
        "salary_min": 40000,
        "salary_max": 55000,
        "salary_currency": "GBP",
        "description": (
            "Capgemini is recruiting Java Developers to work on enterprise banking projects. "
            "You will develop Spring Boot applications, RESTful APIs, and integrate "
            "with third-party banking systems. "
            "Required: Java, Spring MVC, Maven, JUnit, Git. "
            "Beneficial: Kafka, Docker, Azure. 2-4 years of Java development experience. "
            "Candidates must have the right to work in the UK or be sponsored. "
            "We do offer skilled worker visa sponsorship."
        ),
        "url": "https://www.capgemini.com/gb-en/careers/java-developer-dry-004",
        "apply_url": "https://www.capgemini.com/gb-en/careers/java-developer-dry-004/apply",
        "posted_date": datetime.utcnow().date().isoformat(),
        "closing_date": None,
        "source": "dry_run",
        "scraped_at": datetime.utcnow().isoformat(),
        "raw_data": {},
    },
    {
        "job_id": "dry_005",
        "title": "Marketing Manager",
        "employer": "Generic Retail Co",
        "location": "Manchester, UK",
        "salary_min": 35000,
        "salary_max": 45000,
        "salary_currency": "GBP",
        "description": (
            "We are looking for an experienced Marketing Manager to lead our brand campaigns. "
            "You will manage social media, email marketing, and paid advertising. "
            "CRM experience with Salesforce required. "
            "Must have right to work in the UK — no sponsorship available."
        ),
        "url": "https://genericretail.co.uk/careers/marketing-manager-dry-005",
        "apply_url": "https://genericretail.co.uk/careers/marketing-manager-dry-005/apply",
        "posted_date": datetime.utcnow().date().isoformat(),
        "closing_date": None,
        "source": "dry_run",
        "scraped_at": datetime.utcnow().isoformat(),
        "raw_data": {},
    },
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    profile: dict,
    employers: list[dict],
    scraper_config: dict,
    employer_filter: Optional[str] = None,
    dry_run: bool = False,
    new_only: bool = False,
) -> tuple[list[dict], dict]:
    """
    Full scrape → score → sponsorship → store pipeline.

    Args:
        profile: Candidate profile dict.
        employers: List of employer dicts.
        scraper_config: Scraper configuration dict.
        employer_filter: If set, only scrape this employer.
        dry_run: If True, use sample data instead of live scraping.
        new_only: If True, only return new jobs.

    Returns:
        Tuple of (scored_jobs, run_summary).
    """
    db = JobDatabase()
    run_id = db.mark_run_start()

    try:
        # Step 1: Scrape
        if dry_run:
            logger.info("DRY RUN: using %d sample jobs", len(DRY_RUN_JOBS))
            raw_jobs = list(DRY_RUN_JOBS)
        elif employer_filter:
            orchestrator = ScraperOrchestrator(scraper_config, employers)
            keywords = get_search_keywords(profile)
            raw_jobs = orchestrator.run_single_employer(employer_filter, keywords)
        else:
            orchestrator = ScraperOrchestrator(scraper_config, employers)
            keywords = get_search_keywords(profile)
            raw_jobs = orchestrator.run_all(keywords)

        logger.info("Scraped %d raw jobs", len(raw_jobs))

        # Step 2: Score
        scorer = JobScorer(profile)
        scored_jobs = []
        for job in raw_jobs:
            try:
                scored = scorer.score(job)
                scored_jobs.append(scored)
            except Exception as exc:
                logger.warning("Scoring failed for job %s: %s", job.get("job_id"), exc)
                scored_jobs.append(job)

        logger.info("Scored %d jobs", len(scored_jobs))

        # Step 3: Sponsorship check
        checker = SponsorshipChecker()

        # Filter employers that have known sponsorship=yes in config
        employer_sponsorship_map: dict[str, bool] = {}
        for emp in employers:
            if emp.get("sponsorship_known", "").lower() == "yes":
                employer_sponsorship_map[emp.get("employer_name", "").lower()] = True

        enriched_jobs = []
        for job in scored_jobs:
            try:
                employer_name = job.get("employer", "")
                # Use known status from CSV if available
                is_known_sponsor = employer_sponsorship_map.get(
                    employer_name.lower(), None
                )
                if is_known_sponsor is True:
                    employer_status = {
                        "is_licensed": True,
                        "licence_type": "Worker",
                        "confidence": "high",
                        "source": "employers.csv",
                    }
                else:
                    employer_status = checker.check_employer(employer_name)

                vacancy_status = checker.check_vacancy(job, employer_status)
                final_status = checker.classify(employer_status, vacancy_status)

                job = dict(job)
                job["sponsorship_status"] = final_status
                job["sponsorship_evidence"] = vacancy_status.get("evidence", [])
                job["sponsorship_notes"] = vacancy_status.get("notes", "")
                enriched_jobs.append(job)
            except Exception as exc:
                logger.warning(
                    "Sponsorship check failed for job %s: %s",
                    job.get("job_id"),
                    exc,
                )
                enriched_jobs.append(job)

        logger.info("Sponsorship check completed for %d jobs", len(enriched_jobs))

        # Step 4: Store
        new_count = 0
        for job in enriched_jobs:
            is_new = db.upsert_job(job)
            if is_new:
                new_count += 1
                job["is_new"] = 1
            else:
                job["is_new"] = job.get("is_new", 0)

        logger.info("Stored %d jobs (%d new)", len(enriched_jobs), new_count)

        # Filter to new-only if requested
        output_jobs = enriched_jobs
        if new_only:
            output_jobs = [j for j in enriched_jobs if j.get("is_new")]
            logger.info("--new-only: returning %d new jobs", len(output_jobs))

        run_summary = {
            "run_id": run_id,
            "started_at": datetime.utcnow().isoformat(),
            "jobs_found": len(enriched_jobs),
            "new_jobs": new_count,
            "status": "success",
        }

        db.mark_run_complete(run_id, len(enriched_jobs), new_count, "success")
        return output_jobs, run_summary

    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        db.mark_run_complete(run_id, 0, 0, "failed")
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> None:
    """Execute the run command."""
    logger.info("=" * 60)
    logger.info("UK Job Scraper — starting run")
    logger.info("Dry run: %s", args.dry_run)
    logger.info("New only: %s", args.new_only)
    logger.info("Employer filter: %s", args.employer)
    logger.info("=" * 60)

    profile = load_candidate_profile()
    employers = load_employers()

    scraper_config = {
        "rate_limit_delay": args.rate_limit_delay,
        "timeout": args.timeout,
        "retries": args.retries,
        "respect_robots": True,
    }

    jobs, run_summary = run_pipeline(
        profile=profile,
        employers=employers,
        scraper_config=scraper_config,
        employer_filter=args.employer,
        dry_run=args.dry_run,
        new_only=args.new_only,
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    reporter = ReportGenerator()
    outputs = reporter.generate_all(jobs, date_str, run_summary)

    logger.info("=" * 60)
    logger.info("Run complete. Reports generated:")
    for fmt, path in outputs.items():
        logger.info("  %s: %s", fmt.upper(), path)
    logger.info(
        "Total: %d jobs | New: %d",
        run_summary["jobs_found"],
        run_summary["new_jobs"],
    )
    logger.info("=" * 60)

    # Print summary to stdout for GitHub Actions log visibility
    print(f"\nRun Summary:")
    print(f"  Total jobs: {run_summary['jobs_found']}")
    print(f"  New jobs:   {run_summary['new_jobs']}")
    print(f"  Reports:")
    for fmt, path in outputs.items():
        print(f"    {fmt}: {path}")


def cmd_report(args: argparse.Namespace) -> None:
    """Generate report from existing database."""
    db = JobDatabase()
    jobs = db.get_all_jobs()
    last_run = db.get_last_run()
    db.close()

    if not jobs:
        print("No jobs in database. Run 'python main.py run' first.")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    reporter = ReportGenerator()
    run_summary = last_run or {}
    outputs = reporter.generate_all(jobs, date_str, run_summary)

    print(f"\nReports generated from {len(jobs)} jobs in database:")
    for fmt, path in outputs.items():
        print(f"  {fmt}: {path}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of the last scrape run."""
    db = JobDatabase()
    last_run = db.get_last_run()
    history = db.get_run_history(5)
    total_jobs = db.get_job_count()
    db.close()

    print("\n=== UK Job Scraper Status ===\n")
    print(f"Total jobs in database: {total_jobs}")

    if last_run:
        print(f"\nLast run:")
        print(f"  Run ID:     {last_run.get('run_id')}")
        print(f"  Started:    {last_run.get('started_at')}")
        print(f"  Finished:   {last_run.get('finished_at')}")
        print(f"  Jobs found: {last_run.get('jobs_found')}")
        print(f"  New jobs:   {last_run.get('new_jobs')}")
        print(f"  Status:     {last_run.get('status')}")
    else:
        print("\nNo scrape runs recorded.")

    if len(history) > 1:
        print(f"\nRun history (last {len(history)}):")
        print(f"  {'ID':>4}  {'Started':<22}  {'Found':>6}  {'New':>5}  {'Status'}")
        print(f"  {'-'*4}  {'-'*22}  {'-'*6}  {'-'*5}  {'-'*8}")
        for run in history:
            print(
                f"  {run.get('run_id', ''):>4}  "
                f"{str(run.get('started_at', ''))[:22]:<22}  "
                f"{run.get('jobs_found', 0):>6}  "
                f"{run.get('new_jobs', 0):>5}  "
                f"{run.get('status', '')}"
            )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="UK Job Scraper — finds Java/Spring Boot jobs in the UK with sponsorship filtering.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    subparsers.required = True

    # -- run --
    run_parser = subparsers.add_parser(
        "run", help="Scrape jobs, score, check sponsorship, and generate reports."
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Use sample data instead of live scraping (no API keys required).",
    )
    run_parser.add_argument(
        "--new-only",
        action="store_true",
        default=False,
        help="Only include jobs discovered in this run.",
    )
    run_parser.add_argument(
        "--employer",
        metavar="EMPLOYER_NAME",
        default=None,
        help="Limit scraping to a single employer (by name from employers.csv).",
    )
    run_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP request timeout in seconds (default: 30).",
    )
    run_parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries on HTTP failure (default: 3).",
    )
    run_parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=2.0,
        dest="rate_limit_delay",
        help="Minimum seconds between requests (default: 2.0).",
    )
    run_parser.set_defaults(func=cmd_run)

    # -- report --
    report_parser = subparsers.add_parser(
        "report", help="Generate reports from the existing database."
    )
    report_parser.set_defaults(func=cmd_report)

    # -- status --
    status_parser = subparsers.add_parser(
        "status", help="Show statistics from the last scrape run."
    )
    status_parser.set_defaults(func=cmd_status)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
