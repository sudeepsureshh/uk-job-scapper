"""
Scrapers for public job boards: Welcome to the Jungle and Hiring Cafe.

Both boards load content via JavaScript, so we use their public search
APIs/endpoints where available, with graceful fallback on failure.

LinkedIn: not scraped directly (ToS). Adzuna and Reed already surface
many LinkedIn-mirrored roles.
"""

import logging
import time
import hashlib
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, quote

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseJobScraper

logger = logging.getLogger(__name__)


class WelcomeToJungleScraper(BaseJobScraper):
    """
    Scrapes Welcome to the Jungle (wttj.com) public job search.

    WTTJ uses Algolia for search (app ID: CSEKHVMS53) but restricts their
    public API key to requests originating from their own domain. Direct
    programmatic access requires OAuth or a headless browser (Playwright).
    The scraper attempts their REST API and gracefully returns 0 on failure.

    To enable this scraper properly, you'd need to add Playwright to the
    GitHub Actions workflow and render the search page in a real browser.
    Rate limit: 3 seconds between requests.
    """

    # WTTJ REST API — requires session auth from their frontend
    API_BASE_URL = "https://api.welcometothejungle.com"
    SITE_URL = "https://www.welcometothejungle.com"
    SOURCE = "welcometothejungle"

    def __init__(self, config: dict):
        super().__init__(config)
        self.rate_limit_delay = max(config.get("rate_limit_delay", 3.0), 3.0)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-GB,en;q=0.9",
            "Referer": "https://www.welcometothejungle.com/en/jobs",
        })

    def scrape(self, keywords: list[str], location: str = "United Kingdom") -> list[dict]:
        jobs = []
        # Search in two passes: primary keywords, then secondary
        search_terms = [
            "java spring boot backend",
            "software engineer java",
            "backend developer",
        ]
        seen_urls: set[str] = set()

        for term in search_terms:
            try:
                batch = self._search(term, location)
                for job in batch:
                    if job.get("url") not in seen_urls:
                        seen_urls.add(job.get("url", ""))
                        jobs.append(job)
                self._respect_rate_limit()
            except Exception as exc:
                logger.warning("WelcomeToJungle search '%s' failed: %s", term, exc)
                continue

        logger.info("WelcomeToJungle: found %d jobs", len(jobs))
        return jobs

    def _search(self, query: str, location: str) -> list[dict]:
        """
        Attempt the WTTJ REST API. Falls back to HTML scrape if unavailable.

        WTTJ is protected by AWS WAF; direct requests receive a 202 challenge
        page instead of job data. The HTML fallback also fails for the same
        reason. Both attempts are made but 0 results is the expected outcome
        without a headless browser (Playwright) setup in the runner.
        """
        params = {
            "query": query,
            "country_code": "GB",
            "page": 1,
            "per_page": 30,
        }
        api_url = f"{self.API_BASE_URL}/jobs?{urlencode(params)}"

        try:
            resp = self.session.get(api_url, timeout=self.timeout)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    data = resp.json()
                    return self._parse_api_response(data)
        except Exception:
            pass

        # Fallback: scrape the public search HTML page (blocked by WAF in CI)
        return self._scrape_html(query, location)

    def _parse_api_response(self, data: dict) -> list[dict]:
        jobs = []
        items = data.get("jobs", data.get("results", data.get("data", [])))
        if not isinstance(items, list):
            return jobs

        for item in items:
            try:
                job = self._normalise(item)
                if job:
                    jobs.append(job)
            except Exception as exc:
                logger.debug("WTTJ parse error: %s", exc)
        return jobs

    def _scrape_html(self, query: str, location: str) -> list[dict]:
        """HTML fallback: parse the WTTJ search results page."""
        params = {
            "query": query,
            "aroundQuery": location,
        }
        url = f"https://www.welcometothejungle.com/en/jobs?{urlencode(params)}"

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("WTTJ HTML fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []

        # WTTJ renders cards; look for common job card selectors
        selectors = [
            "li[data-testid='job-search-results-list-item']",
            "article[data-testid='job-card']",
            "div.ais-InfiniteHits-item",
        ]
        cards = []
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                break

        for card in cards:
            try:
                title_el = card.find(["h3", "h4", "h2"])
                title = title_el.get_text(strip=True) if title_el else None
                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else None
                if not title or not href:
                    continue
                full_url = href if href.startswith("http") else f"{self.SITE_URL}{href}"
                employer_el = card.find(attrs={"data-testid": "company-name"})
                employer = employer_el.get_text(strip=True) if employer_el else "Unknown"
                location_el = card.find(attrs={"data-testid": "job-location"})
                loc = location_el.get_text(strip=True) if location_el else "United Kingdom"

                jobs.append(self._build_job(
                    title=title,
                    employer=employer,
                    location=loc,
                    url=full_url,
                    description=f"{title} at {employer}",
                ))
            except Exception:
                continue

        return jobs

    def _normalise(self, item: dict) -> Optional[dict]:
        """Convert a WTTJ API item to the standard job schema."""
        title = item.get("name") or item.get("title") or item.get("job_title")
        if not title:
            return None

        company = (item.get("organization") or {}).get("name") or item.get("company_name", "")
        location_data = item.get("office") or {}
        loc = location_data.get("city") or item.get("location", "United Kingdom")
        slug = item.get("slug") or item.get("reference")
        org_slug = (item.get("organization") or {}).get("slug") or ""
        if slug and org_slug:
            url = f"https://www.welcometothejungle.com/en/companies/{org_slug}/jobs/{slug}"
        else:
            url = f"https://www.welcometothejungle.com/en/jobs"

        description = item.get("description") or item.get("profile") or f"{title} at {company}"
        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")

        return self._build_job(
            title=title,
            employer=company,
            location=loc,
            url=url,
            description=description,
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
        )

    def _build_job(
        self,
        title: str,
        employer: str,
        location: str,
        url: str,
        description: str,
        salary_min: Optional[float] = None,
        salary_max: Optional[float] = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        job_id = hashlib.md5(url.encode()).hexdigest()
        return {
            "job_id": job_id,
            "title": title,
            "employer": employer,
            "location": location,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "GBP",
            "description": description,
            "url": url,
            "apply_url": url,
            "posted_date": None,
            "closing_date": None,
            "source": self.SOURCE,
            "scraped_at": now,
            "raw_data": {},
        }

    def _respect_rate_limit(self):
        time.sleep(self.rate_limit_delay)


class HiringCafeScraper(BaseJobScraper):
    """
    Scrapes Hiring Cafe (hiring.cafe) — a job aggregator that surfaces
    roles from many UK employers including startups.

    Hiring Cafe blocks direct requests with 403. Their internal API
    (`/api/search`) returns 404 — the actual endpoint is not public.
    This scraper returns 0 results gracefully until their public API
    becomes available or an alternative access method is identified.
    Rate limit: 3 seconds between requests.
    """

    SEARCH_URL = "https://hiring.cafe/api/v1/jobs/search"
    SITE_URL = "https://hiring.cafe"
    SOURCE = "hiringcafe"

    def __init__(self, config: dict):
        super().__init__(config)
        self.rate_limit_delay = max(config.get("rate_limit_delay", 3.0), 3.0)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*",
            "Content-Type": "application/json",
            "Referer": "https://hiring.cafe/",
            "Origin": "https://hiring.cafe",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
        })

    def scrape(self, keywords: list[str], location: str = "United Kingdom") -> list[dict]:
        jobs = []
        seen_urls: set[str] = set()
        search_terms = [
            "java spring boot",
            "backend engineer java",
            "software engineer",
        ]

        for term in search_terms:
            try:
                batch = self._search(term)
                for job in batch:
                    url = job.get("url", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        jobs.append(job)
                self._respect_rate_limit()
            except Exception as exc:
                logger.warning("HiringCafe search '%s' failed: %s", term, exc)
                continue

        logger.info("HiringCafe: found %d jobs", len(jobs))
        return jobs

    def _search(self, query: str) -> list[dict]:
        """POST to Hiring Cafe search API; fall back to HTML parse."""
        payload = {
            "query": query,
            "location": "United Kingdom",
            "remote": False,
            "page": 0,
            "size": 25,
        }

        try:
            resp = self.session.post(
                self.SEARCH_URL,
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_response(data)
        except Exception:
            pass

        # Fallback: GET search page and parse HTML
        return self._scrape_html(query)

    def _parse_response(self, data: dict) -> list[dict]:
        jobs = []
        items = data.get("jobs", data.get("results", data.get("hits", [])))
        if not isinstance(items, list):
            return jobs

        for item in items:
            try:
                job = self._normalise(item)
                if job:
                    jobs.append(job)
            except Exception as exc:
                logger.debug("HiringCafe parse error: %s", exc)
        return jobs

    def _scrape_html(self, query: str) -> list[dict]:
        """HTML fallback for Hiring Cafe."""
        url = f"{self.SITE_URL}/?q={quote(query)}&location=United+Kingdom"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("HiringCafe HTML fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []

        # Hiring Cafe renders job cards — look for common patterns
        for card in soup.select("div[class*='job'], article[class*='job'], li[class*='job']"):
            try:
                title_el = card.find(["h2", "h3", "h4"])
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else None
                if not href:
                    continue
                full_url = href if href.startswith("http") else f"{self.SITE_URL}{href}"
                employer_el = card.find(attrs={"class": lambda c: c and "company" in c})
                employer = employer_el.get_text(strip=True) if employer_el else "Unknown"
                location_el = card.find(attrs={"class": lambda c: c and "location" in c})
                loc = location_el.get_text(strip=True) if location_el else "United Kingdom"

                jobs.append(self._build_job(title, employer, loc, full_url, title))
            except Exception:
                continue

        return jobs

    def _normalise(self, item: dict) -> Optional[dict]:
        title = (
            item.get("title")
            or item.get("job_title")
            or item.get("name")
        )
        if not title:
            return None

        employer = (
            item.get("company")
            or item.get("employer")
            or item.get("organization", "")
        )
        location = item.get("location") or item.get("city") or "United Kingdom"
        url = item.get("url") or item.get("link") or item.get("apply_url") or ""
        if not url:
            job_id_val = item.get("id") or item.get("job_id", "")
            url = f"{self.SITE_URL}/jobs/{job_id_val}" if job_id_val else self.SITE_URL
        description = item.get("description") or item.get("summary") or f"{title} at {employer}"
        salary_min = item.get("salary_min") or item.get("min_salary")
        salary_max = item.get("salary_max") or item.get("max_salary")

        return self._build_job(
            title=title,
            employer=str(employer),
            location=str(location),
            url=url,
            description=str(description),
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
        )

    def _build_job(
        self,
        title: str,
        employer: str,
        location: str,
        url: str,
        description: str,
        salary_min: Optional[float] = None,
        salary_max: Optional[float] = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        job_id = hashlib.md5(url.encode()).hexdigest()
        return {
            "job_id": job_id,
            "title": title,
            "employer": employer,
            "location": location,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "GBP",
            "description": description,
            "url": url,
            "apply_url": url,
            "posted_date": None,
            "closing_date": None,
            "source": self.SOURCE,
            "scraped_at": now,
            "raw_data": {},
        }

    def _respect_rate_limit(self):
        time.sleep(self.rate_limit_delay)
