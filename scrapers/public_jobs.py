"""
Scrapers for public job board APIs: Reed.co.uk and Adzuna.
Both scrapers degrade gracefully when API keys are absent.
"""
import hashlib
import logging
import os
from datetime import datetime
from typing import Any

import requests

from .base import BaseJobScraper

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "UK-Job-Scraper/1.0 (academic/personal project; "
            "contact: sudeep.s@example.com)"
        )
    }
)


def _make_job_id(*parts: str) -> str:
    """Generate a stable job ID from identifying parts."""
    combined = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Reed.co.uk scraper
# ---------------------------------------------------------------------------


class ReedScraper(BaseJobScraper):
    """
    Scraper using the Reed.co.uk Job Search API.

    API docs: https://www.reed.co.uk/developers/jobseeker
    Requires REED_API_KEY environment variable (Basic-auth username).
    Degrades gracefully (returns empty list) if key is absent.
    """

    BASE_URL = "https://www.reed.co.uk/api/1.0/search"
    DETAIL_URL = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = os.environ.get("REED_API_KEY", "")
        if not self.api_key:
            logger.warning(
                "REED_API_KEY not set. ReedScraper will return empty results."
            )

    def scrape(
        self,
        keywords: list[str],
        location: str = "United Kingdom",
    ) -> list[dict]:
        """
        Scrape Reed.co.uk for jobs matching the given keywords.

        Args:
            keywords: Job title / keyword strings to search for.
            location: Location filter (default UK-wide).

        Returns:
            List of normalised job dicts.
        """
        if not self.api_key:
            logger.info("ReedScraper skipped: no API key configured.")
            return []

        all_jobs: list[dict] = []

        for keyword in keywords:
            try:
                jobs = self._search_keyword(keyword, location)
                all_jobs.extend(jobs)
                logger.info(
                    "Reed: found %d jobs for keyword '%s'", len(jobs), keyword
                )
            except Exception as exc:
                logger.error(
                    "Reed: error searching keyword '%s': %s", keyword, exc
                )

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_jobs = []
        for job in all_jobs:
            url = job.get("url") or ""
            if url not in seen_urls:
                seen_urls.add(url)
                unique_jobs.append(job)

        logger.info("Reed: returning %d unique jobs", len(unique_jobs))
        return unique_jobs

    def _search_keyword(self, keyword: str, location: str) -> list[dict]:
        """Execute a single keyword search against the Reed API."""
        params: dict[str, Any] = {
            "keywords": keyword,
            "locationName": location,
            "resultsToTake": 100,
            "resultsToSkip": 0,
        }

        self._respect_rate_limit()
        try:
            response = _SESSION.get(
                self.BASE_URL,
                params=params,
                auth=(self.api_key, ""),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Reed API request failed: %s", exc)
            return []

        data = response.json()
        raw_jobs = data.get("results", [])

        return [self._parse_job(job) for job in raw_jobs]

    def _parse_job(self, raw: dict) -> dict:
        """Convert a Reed API job object into the standard schema."""
        job_id_raw = str(raw.get("jobId", ""))
        url = raw.get("jobUrl", f"https://www.reed.co.uk/jobs/{job_id_raw}")

        salary_text = ""
        if raw.get("minimumSalary") or raw.get("maximumSalary"):
            salary_text = (
                f"£{raw.get('minimumSalary', '')} - £{raw.get('maximumSalary', '')}"
            )

        sal_min, sal_max, currency = self._normalise_salary(salary_text)
        if raw.get("minimumSalary"):
            sal_min = float(raw["minimumSalary"])
        if raw.get("maximumSalary"):
            sal_max = float(raw["maximumSalary"])

        posted_raw = raw.get("date", "")
        posted_date = _parse_date(posted_raw)

        return self._make_result(
            job_id=_make_job_id("reed", job_id_raw),
            title=raw.get("jobTitle", ""),
            employer=raw.get("employerName", ""),
            location=raw.get("locationName", ""),
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=currency,
            description=raw.get("jobDescription", ""),
            url=url,
            apply_url=url,
            posted_date=posted_date,
            source="reed.co.uk",
            raw_data=raw,
        )


# ---------------------------------------------------------------------------
# Adzuna scraper
# ---------------------------------------------------------------------------


class AdzunaScraper(BaseJobScraper):
    """
    Scraper using the Adzuna Job Search API.

    API docs: https://developer.adzuna.com/
    Requires ADZUNA_APP_ID and ADZUNA_API_KEY environment variables.
    Degrades gracefully (returns empty list) if keys are absent.
    """

    BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.app_id = os.environ.get("ADZUNA_APP_ID", "")
        self.api_key = os.environ.get("ADZUNA_API_KEY", "")
        if not self.app_id or not self.api_key:
            logger.warning(
                "ADZUNA_APP_ID or ADZUNA_API_KEY not set. "
                "AdzunaScraper will return empty results."
            )

    def scrape(
        self,
        keywords: list[str],
        location: str = "United Kingdom",
    ) -> list[dict]:
        """
        Scrape Adzuna for jobs matching the given keywords.

        Args:
            keywords: Job title / keyword strings to search for.
            location: Location filter string.

        Returns:
            List of normalised job dicts.
        """
        if not self.app_id or not self.api_key:
            logger.info("AdzunaScraper skipped: no API credentials configured.")
            return []

        all_jobs: list[dict] = []

        for keyword in keywords:
            try:
                jobs = self._search_keyword(keyword, location)
                all_jobs.extend(jobs)
                logger.info(
                    "Adzuna: found %d jobs for keyword '%s'", len(jobs), keyword
                )
            except Exception as exc:
                logger.error(
                    "Adzuna: error searching keyword '%s': %s", keyword, exc
                )

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_jobs = []
        for job in all_jobs:
            url = job.get("url") or ""
            if url not in seen_urls:
                seen_urls.add(url)
                unique_jobs.append(job)

        logger.info("Adzuna: returning %d unique jobs", len(unique_jobs))
        return unique_jobs

    def _search_keyword(self, keyword: str, location: str) -> list[dict]:
        """Execute a single keyword search against the Adzuna API."""
        all_jobs: list[dict] = []
        page = 1
        max_pages = 3  # Fetch up to 3 pages (50 results each = 150 max per keyword)

        while page <= max_pages:
            self._respect_rate_limit()
            url = self.BASE_URL.format(page=page)
            params: dict[str, Any] = {
                "app_id": self.app_id,
                "app_key": self.api_key,
                "what": keyword,
                "where": location,
                "results_per_page": 50,
                "content-type": "application/json",
            }

            try:
                response = _SESSION.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error("Adzuna API request failed (page %d): %s", page, exc)
                break

            data = response.json()
            raw_jobs = data.get("results", [])

            if not raw_jobs:
                break

            all_jobs.extend(self._parse_job(job) for job in raw_jobs)

            # Stop if we have fetched all available results
            total_results = data.get("count", 0)
            if page * 50 >= total_results:
                break
            page += 1

        return all_jobs

    def _parse_job(self, raw: dict) -> dict:
        """Convert an Adzuna API job object into the standard schema."""
        job_id_raw = str(raw.get("id", ""))
        redirect_url = raw.get("redirect_url", "")
        canonical_url = raw.get("adref", redirect_url)

        salary_text = ""
        if raw.get("salary_min") or raw.get("salary_max"):
            salary_text = (
                f"£{raw.get('salary_min', '')} - £{raw.get('salary_max', '')}"
            )

        sal_min, sal_max, currency = self._normalise_salary(salary_text)
        if raw.get("salary_min"):
            try:
                sal_min = float(raw["salary_min"])
            except (ValueError, TypeError):
                pass
        if raw.get("salary_max"):
            try:
                sal_max = float(raw["salary_max"])
            except (ValueError, TypeError):
                pass

        posted_raw = raw.get("created", "")
        posted_date = _parse_date(posted_raw)

        company = raw.get("company", {})
        employer_name = company.get("display_name", "") if isinstance(company, dict) else ""

        location_obj = raw.get("location", {})
        if isinstance(location_obj, dict):
            display_loc = location_obj.get("display_name", "")
        else:
            display_loc = str(location_obj)

        return self._make_result(
            job_id=_make_job_id("adzuna", job_id_raw),
            title=raw.get("title", ""),
            employer=employer_name,
            location=display_loc,
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=currency,
            description=raw.get("description", ""),
            url=canonical_url or redirect_url,
            apply_url=redirect_url,
            posted_date=posted_date,
            source="adzuna.co.uk",
            raw_data=raw,
        )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _parse_date(raw: str) -> str | None:
    """
    Parse a date string from various formats to ISO 8601.

    Args:
        raw: Raw date string.

    Returns:
        ISO 8601 date string or None.
    """
    if not raw:
        return None

    formats = [
        "%d/%m/%Y",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y",
        "%B %d, %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except (ValueError, TypeError):
            continue

    # Try dateutil as fallback if available
    try:
        from dateutil import parser as dateutil_parser
        return dateutil_parser.parse(raw).date().isoformat()
    except Exception:
        pass

    logger.debug("Could not parse date: %r", raw)
    return None
