"""
Abstract base class for all job scrapers.
"""
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from urllib import robotparser
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class BaseJobScraper(ABC):
    """Abstract base class defining the interface for all job scrapers."""

    # Standard result schema keys
    RESULT_SCHEMA = {
        "job_id": None,
        "title": None,
        "employer": None,
        "location": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "GBP",
        "description": None,
        "url": None,
        "apply_url": None,
        "posted_date": None,
        "closing_date": None,
        "source": None,
        "scraped_at": None,
        "raw_data": None,
    }

    def __init__(self, config: dict) -> None:
        """
        Initialise the scraper with configuration.

        Args:
            config: Dictionary containing scraper configuration.
                    Expected keys: rate_limit_delay (float), timeout (int),
                    retries (int), respect_robots (bool)
        """
        self.config = config
        self.rate_limit_delay: float = config.get("rate_limit_delay", 2.0)
        self.timeout: int = config.get("timeout", 30)
        self.retries: int = config.get("retries", 3)
        self.respect_robots: bool = config.get("respect_robots", True)
        self._last_request_time: float = 0.0
        self._robots_cache: dict[str, robotparser.RobotFileParser] = {}

    @abstractmethod
    def scrape(
        self,
        keywords: list[str],
        location: str = "United Kingdom",
    ) -> list[dict]:
        """
        Scrape job listings for the given keywords and location.

        Args:
            keywords: List of search keywords/job titles.
            location: Location string for the job search.

        Returns:
            List of job dicts conforming to RESULT_SCHEMA.
        """
        ...

    def _respect_rate_limit(self) -> None:
        """
        Pause execution if needed to respect the configured rate limit.
        Ensures at least `rate_limit_delay` seconds between requests.
        """
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - elapsed
            logger.debug("Rate limiting: sleeping %.2fs", sleep_time)
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _check_robots_txt(self, url: str) -> bool:
        """
        Check whether crawling the given URL is permitted by robots.txt.

        Args:
            url: The URL to check.

        Returns:
            True if crawling is allowed, False otherwise.
        """
        if not self.respect_robots:
            return True

        try:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            robots_url = f"{base_url}/robots.txt"

            if base_url not in self._robots_cache:
                rp = robotparser.RobotFileParser()
                rp.set_url(robots_url)
                try:
                    rp.read()
                    self._robots_cache[base_url] = rp
                    logger.debug("Loaded robots.txt from %s", robots_url)
                except Exception as exc:
                    logger.debug("Could not fetch robots.txt from %s: %s", robots_url, exc)
                    # If we cannot fetch robots.txt, assume allowed
                    return True

            rp = self._robots_cache[base_url]
            allowed = rp.can_fetch("*", url)
            if not allowed:
                logger.warning("robots.txt disallows crawling %s", url)
            return allowed

        except Exception as exc:
            logger.warning("Error checking robots.txt for %s: %s", url, exc)
            return True  # Fail open

    def _make_result(self, **kwargs: Any) -> dict:
        """
        Create a result dict pre-populated with the standard schema defaults.

        Args:
            **kwargs: Fields to set in the result.

        Returns:
            Dict conforming to RESULT_SCHEMA with the provided values set.
        """
        result = dict(self.RESULT_SCHEMA)
        result["scraped_at"] = datetime.utcnow().isoformat()
        result.update(kwargs)
        return result

    def _normalise_salary(
        self, raw_salary: str | None
    ) -> tuple[float | None, float | None, str]:
        """
        Attempt to extract min/max salary figures from a free-text salary string.

        Args:
            raw_salary: Salary string such as "£40,000 - £55,000 per annum".

        Returns:
            Tuple of (salary_min, salary_max, currency).
        """
        if not raw_salary:
            return None, None, "GBP"

        import re

        currency = "GBP"
        if "€" in raw_salary or "EUR" in raw_salary.upper():
            currency = "EUR"
        elif "$" in raw_salary or "USD" in raw_salary.upper():
            currency = "USD"

        # Extract all numbers (handle commas and k suffixes)
        numbers = []
        for match in re.finditer(r"[\d,]+(?:\.\d+)?k?", raw_salary, re.IGNORECASE):
            token = match.group().replace(",", "")
            if token.lower().endswith("k"):
                try:
                    numbers.append(float(token[:-1]) * 1000)
                except ValueError:
                    pass
            else:
                try:
                    val = float(token)
                    if val >= 1000:  # Ignore day/hour rates that look tiny
                        numbers.append(val)
                except ValueError:
                    pass

        if not numbers:
            return None, None, currency
        if len(numbers) == 1:
            return numbers[0], numbers[0], currency
        return min(numbers), max(numbers), currency
