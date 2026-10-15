"""
Scraper for employer career pages using requests + BeautifulSoup4.
Handles various page structures with graceful degradation.
"""
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

from .base import BaseJobScraper

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (compatible; UK-Job-Scraper/1.0; "
            "+https://github.com/sudeep-s/uk-job-scraper)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.5",
    }
)


def _make_job_id(*parts: str) -> str:
    """Generate a stable job ID from identifying parts."""
    combined = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


class CareersPageScraper(BaseJobScraper):
    """
    Scrapes job listings directly from employer careers pages.

    Attempts several extraction strategies in order:
      1. JSON-LD structured data (schema.org JobPosting)
      2. Common CSS class patterns used by ATS widgets
      3. Link-text heuristics (anchors mentioning job/role/position)

    Rate limits to at least 3 seconds between requests.
    Respects robots.txt.
    Returns partial results rather than crashing on any single failure.
    """

    # Minimum delay between requests (overrides base class default)
    DEFAULT_RATE_LIMIT = 3.0

    # CSS selectors to try when looking for job listing containers
    JOB_CONTAINER_SELECTORS = [
        # Generic job board classes
        ".job-listing",
        ".job-card",
        ".job-result",
        ".job-item",
        ".job-post",
        ".vacancy",
        ".vacancy-item",
        ".vacancy-card",
        ".opening",
        ".opening-item",
        ".role",
        ".position",
        ".career-item",
        ".careers-job",
        # List-based layouts
        "ul.jobs li",
        "ul.vacancies li",
        "ul.openings li",
        "ul.positions li",
        # Article/section based
        "article.job",
        "article.vacancy",
        "div[class*='job']",
        "div[class*='vacancy']",
        "div[class*='career']",
        "div[class*='opening']",
        "div[class*='position']",
        # Table rows
        "table.jobs tr",
        # Common ATS widget patterns
        ".jv-job-list-entry",
        ".lever-job-listing",
        ".gh-job-listing",
        ".workday-job",
    ]

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # Override rate limit to be more conservative for direct page scraping
        self.rate_limit_delay = max(
            self.DEFAULT_RATE_LIMIT, config.get("rate_limit_delay", 3.0)
        )

    def scrape(
        self,
        keywords: list[str],
        location: str = "United Kingdom",
    ) -> list[dict]:
        """
        Scrape careers pages for all configured employers.

        Args:
            keywords: Keywords to filter results (checked against job title).
            location: Not used directly but passed to results.

        Returns:
            List of job dicts found across all employer pages.
        """
        employers: list[dict] = self.config.get("employers", [])
        if not employers:
            logger.info("CareersPageScraper: no employers configured.")
            return []

        all_jobs: list[dict] = []
        for employer in employers:
            name = employer.get("employer_name", "")
            careers_url = employer.get("careers_url", "")
            if not careers_url:
                logger.warning("Skipping employer %r: no careers_url", name)
                continue

            try:
                jobs = self._scrape_employer(employer, keywords)
                logger.info(
                    "CareersPage: found %d jobs at %s", len(jobs), name
                )
                all_jobs.extend(jobs)
            except Exception as exc:
                logger.error(
                    "CareersPage: unhandled error scraping %s: %s", name, exc
                )

        return all_jobs

    def _scrape_employer(
        self, employer: dict, keywords: list[str]
    ) -> list[dict]:
        """
        Attempt to extract job listings from a single employer page.

        Args:
            employer: Employer config dict with employer_name, careers_url.
            keywords: Filter keywords (applied to job title).

        Returns:
            List of job dicts for that employer.
        """
        name = employer.get("employer_name", "")
        url = employer.get("careers_url", "")
        ats_type = employer.get("ats_type", "").lower()

        # Strategy 0: ATS public APIs (no HTML scraping needed)
        if ats_type == "greenhouse":
            jobs = self._try_greenhouse_api(name, url, keywords)
            if jobs:
                return jobs
        elif ats_type == "lever":
            jobs = self._try_lever_api(name, url, keywords)
            if jobs:
                return jobs

        if not self._check_robots_txt(url):
            logger.info("robots.txt disallows %s, skipping.", url)
            return []

        self._respect_rate_limit()

        try:
            response = _SESSION.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Could not fetch %s: %s", url, exc)
            return []

        soup = BeautifulSoup(response.text, "lxml")

        # Strategy 1: JSON-LD
        jobs = self._extract_json_ld(soup, url, name)
        if jobs:
            logger.debug("%s: extracted %d jobs via JSON-LD", name, len(jobs))
            return self._filter_by_keywords(jobs, keywords)

        # Strategy 2: CSS class patterns
        jobs = self._extract_by_css(soup, url, name)
        if jobs:
            logger.debug(
                "%s: extracted %d jobs via CSS selectors", name, len(jobs)
            )
            return self._filter_by_keywords(jobs, keywords)

        # Strategy 3: Link heuristics
        jobs = self._extract_by_links(soup, url, name)
        if jobs:
            logger.debug(
                "%s: extracted %d jobs via link heuristics", name, len(jobs)
            )
            return self._filter_by_keywords(jobs, keywords)

        logger.info("CareersPage: no jobs extracted from %s (%s)", name, url)
        return []

    # ------------------------------------------------------------------
    # ATS public API helpers
    # ------------------------------------------------------------------

    def _greenhouse_slug_candidates(self, name: str, url: str) -> list[str]:
        """Derive likely Greenhouse board slugs from employer name and URL."""
        from urllib.parse import urlparse as _urlparse
        slugs = []
        # From URL hostname: careers.revolut.com → revolut
        parsed = _urlparse(url)
        host = parsed.hostname or ""
        host_clean = host.replace("www.", "").replace("careers.", "").replace("jobs.", "")
        domain_slug = host_clean.split(".")[0]
        if domain_slug:
            slugs.append(domain_slug)
        # From employer name: "Monzo Bank" → "monzo", "Funding Circle" → "fundingcircle"
        import re as _re
        name_slug = _re.sub(r"[^a-z0-9]", "", name.lower())
        if name_slug and name_slug not in slugs:
            slugs.append(name_slug)
        # "Funding Circle" → "funding-circle"
        name_hyphen = _re.sub(r"\s+", "-", name.lower().strip())
        name_hyphen = _re.sub(r"[^a-z0-9-]", "", name_hyphen)
        if name_hyphen and name_hyphen not in slugs:
            slugs.append(name_hyphen)
        return slugs

    def _try_greenhouse_api(
        self, name: str, url: str, keywords: list[str]
    ) -> list[dict]:
        """Query the Greenhouse public job board API."""
        skill_words = _extract_skill_words(keywords)
        for slug in self._greenhouse_slug_candidates(name, url):
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            try:
                resp = _SESSION.get(api_url, timeout=self.timeout)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                jobs_raw = data.get("jobs", [])
                if not jobs_raw:
                    continue
                jobs = [
                    self._greenhouse_to_job(j, name)
                    for j in jobs_raw
                    if j.get("title")
                ]
                logger.info(
                    "CareersPage/Greenhouse: %d jobs at %s (slug=%s)",
                    len(jobs), name, slug,
                )
                return self._filter_by_keywords(jobs, skill_words)
            except Exception as exc:
                logger.debug("Greenhouse API failed for %s (slug=%s): %s", name, slug, exc)
        return []

    def _try_lever_api(
        self, name: str, url: str, keywords: list[str]
    ) -> list[dict]:
        """Query the Lever public postings API."""
        from urllib.parse import urlparse as _urlparse
        import re as _re
        skill_words = _extract_skill_words(keywords)
        parsed = _urlparse(url)
        host = (parsed.hostname or "").replace("www.", "").replace("jobs.", "")
        slug = host.split(".")[0]
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=250"
        try:
            resp = _SESSION.get(api_url, timeout=self.timeout)
            if resp.status_code == 200:
                jobs_raw = resp.json()
                if isinstance(jobs_raw, list) and jobs_raw:
                    jobs = [self._lever_to_job(j, name) for j in jobs_raw if j.get("text")]
                    logger.info("CareersPage/Lever: %d jobs at %s", len(jobs), name)
                    return self._filter_by_keywords(jobs, skill_words)
        except Exception as exc:
            logger.debug("Lever API failed for %s: %s", name, exc)
        return []

    def _greenhouse_to_job(self, item: dict, employer: str) -> dict:
        """Convert a Greenhouse API job item to the standard job schema."""
        title = item.get("title", "")
        job_url = item.get("absolute_url", "")
        loc_name = (item.get("location") or {}).get("name") or "United Kingdom"
        description = item.get("content") or f"{title} at {employer}"
        # Strip HTML from description
        try:
            desc_soup = BeautifulSoup(description, "lxml")
            description = desc_soup.get_text(" ", strip=True)[:2000]
        except Exception:
            pass
        return self._make_result(
            job_id=_make_job_id("greenhouse", employer, str(item.get("id", "")), job_url),
            title=title,
            employer=employer,
            location=loc_name,
            salary_min=None,
            salary_max=None,
            description=description,
            url=job_url,
            apply_url=job_url,
            posted_date=item.get("first_published") or item.get("updated_at") or "",
            closing_date="",
            source="greenhouse",
        )

    def _lever_to_job(self, item: dict, employer: str) -> dict:
        """Convert a Lever API posting to the standard job schema."""
        title = item.get("text", "")
        job_url = item.get("hostedUrl") or item.get("applyUrl") or ""
        categories = item.get("categories") or {}
        location = categories.get("location") or "United Kingdom"
        description_raw = (item.get("description") or "") + " " + " ".join(
            (l.get("content") or "") for l in (item.get("lists") or [])
        )
        try:
            desc_soup = BeautifulSoup(description_raw, "lxml")
            description = desc_soup.get_text(" ", strip=True)[:2000]
        except Exception:
            description = description_raw[:2000]
        return self._make_result(
            job_id=_make_job_id("lever", employer, item.get("id", ""), job_url),
            title=title,
            employer=employer,
            location=location,
            salary_min=None,
            salary_max=None,
            description=description,
            url=job_url,
            apply_url=job_url,
            posted_date="",
            closing_date="",
            source="lever",
        )

    # ------------------------------------------------------------------
    # Extraction strategies
    # ------------------------------------------------------------------

    def _extract_json_ld(
        self, soup: BeautifulSoup, page_url: str, employer: str
    ) -> list[dict]:
        """Extract jobs from JSON-LD structured data (schema.org JobPosting)."""
        results = []
        scripts = soup.find_all("script", type="application/ld+json")

        for script in scripts:
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue

            # Handle both single object and @graph array
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@graph"):
                    items = items + item["@graph"]
                    continue

                job_type = (item.get("@type") or "").lower()
                if job_type not in ("jobposting", "job"):
                    continue

                title = item.get("title") or item.get("name") or ""
                job_url = item.get("url") or page_url
                description = item.get("description") or ""

                org = item.get("hiringOrganization")
                emp_name = employer
                if isinstance(org, dict):
                    emp_name = org.get("name", employer)

                loc = item.get("jobLocation")
                location_str = ""
                if isinstance(loc, dict):
                    addr = loc.get("address", {})
                    if isinstance(addr, dict):
                        location_str = ", ".join(
                            filter(
                                None,
                                [
                                    addr.get("addressLocality", ""),
                                    addr.get("addressRegion", ""),
                                    addr.get("addressCountry", ""),
                                ],
                            )
                        )
                    else:
                        location_str = str(addr)
                elif isinstance(loc, str):
                    location_str = loc

                sal = item.get("baseSalary")
                sal_min = sal_max = None
                if isinstance(sal, dict):
                    val = sal.get("value", {})
                    if isinstance(val, dict):
                        sal_min = _safe_float(val.get("minValue"))
                        sal_max = _safe_float(val.get("maxValue"))
                    else:
                        sal_min = sal_max = _safe_float(val)

                posted = item.get("datePosted") or ""
                closing = item.get("validThrough") or ""

                results.append(
                    self._make_result(
                        job_id=_make_job_id("careers", employer, title, job_url),
                        title=title,
                        employer=emp_name,
                        location=location_str,
                        salary_min=sal_min,
                        salary_max=sal_max,
                        description=description,
                        url=job_url,
                        apply_url=item.get("directApply") or job_url,
                        posted_date=posted[:10] if posted else None,
                        closing_date=closing[:10] if closing else None,
                        source="careers_page",
                        raw_data=item,
                    )
                )

        return results

    def _extract_by_css(
        self, soup: BeautifulSoup, page_url: str, employer: str
    ) -> list[dict]:
        """Extract jobs by trying common CSS selector patterns."""
        for selector in self.JOB_CONTAINER_SELECTORS:
            try:
                elements = soup.select(selector)
            except Exception:
                continue

            if not elements:
                continue

            jobs = []
            for el in elements:
                job = self._element_to_job(el, page_url, employer)
                if job and job.get("title"):
                    jobs.append(job)

            if jobs:
                return jobs

        return []

    def _extract_by_links(
        self, soup: BeautifulSoup, page_url: str, employer: str
    ) -> list[dict]:
        """
        Fall-back: find anchor tags that look like job links.
        Uses href patterns and surrounding text to identify jobs.
        """
        job_link_patterns = re.compile(
            r"(job|career|career|position|role|vacancy|opening|requisition)",
            re.IGNORECASE,
        )

        jobs = []
        seen_hrefs: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not href or href.startswith("#"):
                continue

            # Build absolute URL
            abs_href = urljoin(page_url, href)

            # Only follow links on the same domain
            if urlparse(abs_href).netloc != urlparse(page_url).netloc:
                continue

            if abs_href in seen_hrefs:
                continue

            # Match on href path or link text
            link_text = anchor.get_text(strip=True)
            matches_href = bool(job_link_patterns.search(href))
            matches_text = len(link_text) > 10  # Avoid nav links with short text

            if not (matches_href and matches_text):
                continue

            seen_hrefs.add(abs_href)
            jobs.append(
                self._make_result(
                    job_id=_make_job_id("careers", employer, abs_href),
                    title=link_text[:200],
                    employer=employer,
                    location="United Kingdom",
                    url=abs_href,
                    apply_url=abs_href,
                    source="careers_page",
                    raw_data={"href": abs_href, "text": link_text},
                )
            )

            if len(jobs) >= 50:  # Safety cap
                break

        return jobs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _element_to_job(
        self, el: Any, page_url: str, employer: str
    ) -> dict | None:
        """Convert a BeautifulSoup element to a job dict."""
        try:
            # Find the title — look for heading tags or strong/span
            title_el = (
                el.find(["h1", "h2", "h3", "h4", "h5"])
                or el.find("strong")
                or el.find("span", class_=re.compile(r"title|name|role", re.I))
            )
            title = title_el.get_text(strip=True) if title_el else el.get_text(strip=True)[:120]

            # Find the link
            link_el = el.find("a", href=True)
            if link_el:
                job_url = urljoin(page_url, link_el["href"])
            else:
                job_url = page_url

            # Find location hint
            loc_el = el.find(class_=re.compile(r"location|loc|place", re.I))
            location_str = loc_el.get_text(strip=True) if loc_el else "United Kingdom"

            # Grab full text for description
            description = el.get_text(" ", strip=True)

            return self._make_result(
                job_id=_make_job_id("careers", employer, job_url, title),
                title=title,
                employer=employer,
                location=location_str,
                description=description[:2000],
                url=job_url,
                apply_url=job_url,
                source="careers_page",
                raw_data={"html_snippet": str(el)[:1000]},
            )
        except Exception as exc:
            logger.debug("Could not parse element for %s: %s", employer, exc)
            return None

    def _filter_by_keywords(
        self, jobs: list[dict], keywords: list[str]
    ) -> list[dict]:
        """
        Keep jobs whose title or description contains at least one keyword.
        If keywords list is empty, all jobs are returned.
        """
        if not keywords:
            return jobs

        filtered = []
        for job in jobs:
            text = (
                (job.get("title") or "").lower()
                + " "
                + (job.get("description") or "").lower()
            )
            if any(kw.lower() in text for kw in keywords):
                filtered.append(job)

        return filtered


def _safe_float(value: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# Stop words that carry no technical signal when isolated
_FILTER_STOP = {
    "developer", "engineer", "senior", "junior", "graduate", "associate",
    "lead", "staff", "full", "stack", "back", "front", "end", "and", "or",
    "the", "for", "with", "in", "at", "of", "to", "a", "an", "local",
    "public", "sector", "authority",
}


def _extract_skill_words(keywords: list[str]) -> list[str]:
    """
    Extract individual technical skill words from multi-word keyword phrases.

    "Java Developer" → ["java"]
    "Spring Boot Developer" → ["spring", "boot"]
    "FinTech Developer" → ["fintech"]

    Used so Greenhouse/Lever job descriptions are matched on skill tokens
    rather than requiring the exact job-title phrase.
    """
    import re as _re
    words = set()
    for kw in keywords:
        parts = _re.split(r"\s+", kw.lower().strip())
        for part in parts:
            clean = _re.sub(r"[^a-z0-9]", "", part)
            if clean and clean not in _FILTER_STOP and len(clean) > 3:
                words.add(clean)
    # Always include core Java/Spring terms even if not in keywords
    words.update({"java", "spring", "kafka", "springboot", "fintech", "payments"})
    return sorted(words)
