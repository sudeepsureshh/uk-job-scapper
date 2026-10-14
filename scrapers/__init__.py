"""
Scrapers package for the UK Job Scraper application.

Exports:
    ReedScraper: Scraper for Reed.co.uk API.
    AdzunaScraper: Scraper for Adzuna API.
    CareersPageScraper: Scraper for employer careers pages.
    ScraperOrchestrator: Orchestrates all scrapers and deduplicates results.
"""

import logging
from typing import Optional

from .careers_page import CareersPageScraper
from .public_jobs import AdzunaScraper, ReedScraper

logger = logging.getLogger(__name__)

__all__ = [
    "ReedScraper",
    "AdzunaScraper",
    "CareersPageScraper",
    "ScraperOrchestrator",
]


class ScraperOrchestrator:
    """
    Runs all configured scrapers and deduplicates the combined results.

    Individual scraper failures do not stop other scrapers from running.
    Results are deduplicated by URL (case-insensitive).
    """

    def __init__(self, config: dict, employers: Optional[list[dict]] = None) -> None:
        """
        Initialise all scrapers.

        Args:
            config: Shared scraper configuration dict.
            employers: List of employer dicts loaded from employers.csv.
        """
        self.config = config
        self.employers = employers or []

        # Add employers list to config so CareersPageScraper can access them
        careers_config = dict(config)
        careers_config["employers"] = self.employers

        self.scrapers = {
            "reed": ReedScraper(config),
            "adzuna": AdzunaScraper(config),
            "careers": CareersPageScraper(careers_config),
        }

    def run_all(
        self,
        keywords: list[str],
        location: str = "United Kingdom",
    ) -> list[dict]:
        """
        Run all scrapers and return deduplicated job listings.

        Args:
            keywords: List of search keywords.
            location: Location string to pass to all scrapers.

        Returns:
            Deduplicated list of job dicts, ordered by scraper priority
            (Reed, Adzuna, then careers pages).
        """
        all_jobs: list[dict] = []

        for name, scraper in self.scrapers.items():
            try:
                logger.info("Running scraper: %s", name)
                jobs = scraper.scrape(keywords, location)
                logger.info("Scraper %s returned %d jobs", name, len(jobs))
                all_jobs.extend(jobs)
            except Exception as exc:
                logger.error(
                    "Scraper %s raised an unhandled exception: %s", name, exc
                )

        # Deduplicate by URL (normalise to lowercase, strip trailing slashes)
        seen_urls: set[str] = set()
        unique_jobs: list[dict] = []

        for job in all_jobs:
            url = (job.get("url") or "").strip().rstrip("/").lower()
            if not url:
                # Fall back to title+employer hash for jobs with no URL
                title = (job.get("title") or "").lower()
                employer = (job.get("employer") or "").lower()
                url = f"no-url:{employer}:{title}"

            if url not in seen_urls:
                seen_urls.add(url)
                unique_jobs.append(job)

        logger.info(
            "Orchestrator: %d total jobs -> %d after deduplication",
            len(all_jobs),
            len(unique_jobs),
        )
        return unique_jobs

    def run_single_employer(
        self,
        employer_name: str,
        keywords: list[str],
        location: str = "United Kingdom",
    ) -> list[dict]:
        """
        Run only the careers-page scraper for a single employer.

        Args:
            employer_name: Name to match against employer list (case-insensitive).
            keywords: Keywords to filter results.
            location: Location string.

        Returns:
            List of job dicts for that employer.
        """
        matching = [
            e
            for e in self.employers
            if e.get("employer_name", "").lower() == employer_name.lower()
        ]

        if not matching:
            logger.warning(
                "No employer found matching %r in configured employer list.",
                employer_name,
            )
            return []

        careers_config = dict(self.config)
        careers_config["employers"] = matching
        scraper = CareersPageScraper(careers_config)

        try:
            return scraper.scrape(keywords, location)
        except Exception as exc:
            logger.error(
                "Error scraping employer %s: %s", employer_name, exc
            )
            return []
