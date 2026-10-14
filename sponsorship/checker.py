"""
Sponsorship checker: verifies whether a UK employer is licensed to sponsor
Skilled Worker visas and whether a specific vacancy mentions sponsorship.

DISCLAIMER: This tool is for informational purposes only and does NOT
constitute legal advice. Always verify sponsorship eligibility directly
with the employer and consult a qualified immigration solicitor before
making decisions. Sponsor register data may be out of date.
"""

import csv
import io
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Official UK government register of licensed Skilled Worker sponsors
SPONSOR_REGISTER_URL = (
    "https://assets.publishing.service.gov.uk/media/"
    "register-of-licensed-sponsors-workers.csv"
)

# Cache location and expiry
DEFAULT_CACHE_PATH = "storage/sponsor_register_cache.csv"
CACHE_MAX_AGE_DAYS = 7

# Keywords that positively indicate sponsorship availability
POSITIVE_KEYWORDS = [
    "visa sponsorship",
    "skilled worker visa",
    "tier 2",
    "tier-2",
    "certificate of sponsorship",
    "cos",
    "we sponsor",
    "sponsorship available",
    "sponsorship provided",
    "relocation and sponsorship",
    "we provide sponsorship",
    "will sponsor",
    "can sponsor",
    "eligible to sponsor",
    "sponsorship considered",
    "sponsorship is available",
    "skilled worker sponsorship",
    "we are able to sponsor",
]

# Keywords that negatively indicate sponsorship is NOT available
NEGATIVE_KEYWORDS = [
    "no sponsorship",
    "no visa sponsorship",
    "sponsorship not available",
    "unable to sponsor",
    "cannot sponsor",
    "will not sponsor",
    "do not sponsor",
    "does not sponsor",
    "must have right to work",
    "must already have the right to work",
    "right to work in the uk required",
    "unrestricted right to work",
    "no right to work support",
    "we are unable to provide sponsorship",
    "unfortunately we are unable to sponsor",
    "we do not offer sponsorship",
]


class SponsorshipChecker:
    """
    Checks whether a UK employer is a licensed Skilled Worker sponsor
    and whether a specific vacancy mentions visa sponsorship.

    DISCLAIMER: This information is provided for research purposes only.
    It does not constitute immigration or legal advice. Always verify
    directly with the employer and seek professional legal guidance.
    """

    def __init__(
        self,
        cache_path: str = DEFAULT_CACHE_PATH,
        cache_max_age_days: int = CACHE_MAX_AGE_DAYS,
    ) -> None:
        """
        Initialise the checker.

        Args:
            cache_path: Path for the sponsor register cache CSV.
            cache_max_age_days: Number of days before cache is refreshed.
        """
        self.cache_path = Path(cache_path)
        self.cache_max_age_days = cache_max_age_days
        self._sponsor_data: Optional[dict[str, dict]] = None
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_employer(self, employer_name: str) -> dict:
        """
        Check whether an employer is on the UK Skilled Worker sponsor register.

        The register is downloaded from gov.uk and cached locally for 7 days.

        Args:
            employer_name: Employer name to search for.

        Returns:
            Dict with keys:
              - is_licensed (bool): Whether employer is on the register.
              - licence_type (str): Type of licence (e.g. "Worker").
              - last_checked (str): ISO date of the check.
              - source (str): Source URL.
              - confidence (str): "high" | "low" depending on match quality.
        """
        data = self._load_sponsor_register()
        now_str = datetime.utcnow().date().isoformat()

        if data is None:
            return {
                "is_licensed": False,
                "licence_type": "unknown",
                "last_checked": now_str,
                "source": SPONSOR_REGISTER_URL,
                "confidence": "low",
                "notes": "Could not load sponsor register; verify manually.",
            }

        # Try exact match, then fuzzy match
        match = self._find_employer_in_register(employer_name, data)

        if match:
            return {
                "is_licensed": True,
                "licence_type": match.get("Type & Rating", "Worker"),
                "last_checked": now_str,
                "source": SPONSOR_REGISTER_URL,
                "confidence": "high",
                "register_entry": match.get("Organisation Name", employer_name),
                "notes": (
                    "DISCLAIMER: Register data may be up to 7 days old. "
                    "Verify at gov.uk before relying on this information."
                ),
            }
        else:
            return {
                "is_licensed": False,
                "licence_type": "none_found",
                "last_checked": now_str,
                "source": SPONSOR_REGISTER_URL,
                "confidence": "medium",
                "notes": (
                    "Not found on register. Employer may still offer sponsorship "
                    "via an umbrella company or may have recently been added. "
                    "DISCLAIMER: Not legal advice — verify directly with employer."
                ),
            }

    def check_vacancy(
        self, job: dict, employer_sponsor_status: dict
    ) -> dict:
        """
        Scan a job listing for sponsorship keywords.

        Args:
            job: Job dict with at least a 'description' field.
            employer_sponsor_status: Result from check_employer().

        Returns:
            Dict with keys:
              - status (str): Classification (see STATUS_* constants below).
              - evidence (list[str]): Matching keyword phrases found.
              - confidence (str): "high" | "medium" | "low".
              - notes (str): Human-readable notes.
        """
        description = (job.get("description") or "").lower()
        title = (job.get("title") or "").lower()
        full_text = f"{title} {description}"

        positive_found = [kw for kw in POSITIVE_KEYWORDS if kw in full_text]
        negative_found = [kw for kw in NEGATIVE_KEYWORDS if kw in full_text]

        if negative_found:
            return {
                "status": "sponsorship_unavailable",
                "evidence": negative_found,
                "confidence": "high",
                "notes": (
                    f"Job description contains negative sponsorship signals: "
                    f"{', '.join(negative_found[:3])}"
                ),
            }

        if positive_found:
            return {
                "status": "confirmed_role",
                "evidence": positive_found,
                "confidence": "high",
                "notes": (
                    f"Job description mentions visa sponsorship: "
                    f"{', '.join(positive_found[:3])}"
                ),
            }

        # No explicit mention
        if employer_sponsor_status.get("is_licensed"):
            return {
                "status": "licensed_employer_unconfirmed",
                "evidence": [],
                "confidence": "medium",
                "notes": (
                    "No explicit sponsorship mention in job ad, but employer "
                    "is on the licensed sponsor register. Contact employer to confirm."
                ),
            }

        return {
            "status": "unable_to_verify",
            "evidence": [],
            "confidence": "low",
            "notes": (
                "No sponsorship information found. Employer is not on the "
                "licensed sponsor register and job ad is silent on sponsorship. "
                "Contact employer directly to ask."
            ),
        }

    def classify(
        self,
        employer_status: dict,
        vacancy_status: dict,
    ) -> str:
        """
        Produce a final sponsorship classification string.

        Args:
            employer_status: Result from check_employer().
            vacancy_status: Result from check_vacancy().

        Returns:
            One of: "confirmed_role", "licensed_employer_unconfirmed",
            "sponsorship_unavailable", "unable_to_verify".
        """
        vacancy_classification = vacancy_status.get("status", "unable_to_verify")

        if vacancy_classification == "sponsorship_unavailable":
            return "sponsorship_unavailable"

        if vacancy_classification == "confirmed_role":
            return "confirmed_role"

        if vacancy_classification == "licensed_employer_unconfirmed":
            if employer_status.get("is_licensed"):
                return "licensed_employer_unconfirmed"
            return "unable_to_verify"

        return "unable_to_verify"

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _load_sponsor_register(self) -> Optional[dict[str, dict]]:
        """
        Load the sponsor register, using cache when available and fresh.

        Returns:
            Dict mapping normalised employer names to register row dicts,
            or None if the register could not be loaded.
        """
        if self._sponsor_data is not None:
            return self._sponsor_data

        if self._cache_is_valid():
            logger.info("Loading sponsor register from cache: %s", self.cache_path)
            data = self._parse_csv_file(self.cache_path)
            if data is not None:
                self._sponsor_data = data
                return data

        # Download fresh copy
        logger.info("Downloading sponsor register from gov.uk...")
        downloaded = self._download_sponsor_register()
        if downloaded:
            self._sponsor_data = downloaded
            return downloaded

        # Fall back to stale cache if download failed
        if self.cache_path.exists():
            logger.warning(
                "Download failed; using stale cache: %s", self.cache_path
            )
            return self._parse_csv_file(self.cache_path)

        logger.error("Could not load sponsor register from any source.")
        return None

    def _cache_is_valid(self) -> bool:
        """Return True if the cache file exists and is younger than max age."""
        if not self.cache_path.exists():
            return False
        mtime = datetime.utcfromtimestamp(self.cache_path.stat().st_mtime)
        age = datetime.utcnow() - mtime
        return age < timedelta(days=self.cache_max_age_days)

    def _download_sponsor_register(self) -> Optional[dict[str, dict]]:
        """
        Download the sponsor register CSV from gov.uk and cache it.

        Returns:
            Parsed sponsor data dict, or None on failure.
        """
        try:
            response = requests.get(
                SPONSOR_REGISTER_URL, timeout=60, stream=True
            )
            response.raise_for_status()
            content = response.content

            # Save to cache
            self.cache_path.write_bytes(content)
            logger.info(
                "Sponsor register downloaded and cached at %s", self.cache_path
            )

            return self._parse_csv_content(content.decode("utf-8", errors="replace"))

        except requests.RequestException as exc:
            logger.error("Failed to download sponsor register: %s", exc)
            return None
        except Exception as exc:
            logger.error("Error saving sponsor register cache: %s", exc)
            return None

    def _parse_csv_file(self, path: Path) -> Optional[dict[str, dict]]:
        """Parse a cached CSV file into a dict keyed by normalised employer name."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return self._parse_csv_content(content)
        except Exception as exc:
            logger.error("Error reading sponsor register cache: %s", exc)
            return None

    def _parse_csv_content(self, content: str) -> dict[str, dict]:
        """
        Parse sponsor register CSV content.

        The CSV has columns like:
          Organisation Name, Town/City, County, Type & Rating, Route

        Returns:
            Dict mapping normalised names to row dicts.
        """
        result: dict[str, dict] = {}
        try:
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                name = row.get("Organisation Name") or row.get("organisation_name") or ""
                if name:
                    key = _normalise_employer_name(name)
                    result[key] = dict(row)
        except Exception as exc:
            logger.error("Error parsing sponsor register CSV: %s", exc)

        logger.info("Parsed %d sponsor register entries.", len(result))
        return result

    def _find_employer_in_register(
        self, employer_name: str, data: dict[str, dict]
    ) -> Optional[dict]:
        """
        Look up an employer in the register with fuzzy matching.

        Args:
            employer_name: Employer name to search for.
            data: Dict mapping normalised names to register rows.

        Returns:
            Matching register row dict, or None.
        """
        normalised = _normalise_employer_name(employer_name)

        # Exact match
        if normalised in data:
            return data[normalised]

        # Substring match (employer name contains or is contained in register entry)
        for key, row in data.items():
            if normalised in key or key in normalised:
                if len(normalised) >= 5 and len(key) >= 5:
                    return row

        # Tokenised match: all significant tokens of employer appear in entry
        tokens = set(_tokenise(normalised)) - {"ltd", "limited", "plc", "group", "uk", "the"}
        if len(tokens) >= 2:
            for key in data:
                key_tokens = set(_tokenise(key))
                if tokens.issubset(key_tokens):
                    return data[key]

        return None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _normalise_employer_name(name: str) -> str:
    """Lowercase, strip common legal suffixes, normalise whitespace."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [
        " limited", " ltd", " plc", " llp", " llc",
        " inc", " corporation", " corp",
    ]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    # Remove punctuation except hyphens and spaces
    name = re.sub(r"[^\w\s\-]", "", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _tokenise(text: str) -> list[str]:
    """Split text into lowercase word tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())
