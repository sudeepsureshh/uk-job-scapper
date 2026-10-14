"""
Tests for the SponsorshipChecker.
HTTP calls are mocked with unittest.mock to avoid network access.
"""
import os
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sponsorship.checker import SponsorshipChecker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def checker(tmp_path: Path) -> SponsorshipChecker:
    """SponsorshipChecker with a temporary cache directory."""
    cache_path = str(tmp_path / "sponsor_register_cache.csv")
    return SponsorshipChecker(cache_path=cache_path)


@pytest.fixture
def sample_register_csv() -> str:
    """Minimal sponsor register CSV content."""
    return (
        "Organisation Name,Town/City,County,Type & Rating,Route\n"
        "Barclays Bank PLC,London,Greater London,Worker,Skilled Worker\n"
        "HSBC UK Bank PLC,London,Greater London,Worker,Skilled Worker\n"
        "Amazon UK Services Ltd,London,Greater London,Worker,Skilled Worker\n"
        "TCS (Tata Consultancy Services),London,Greater London,Worker,Skilled Worker\n"
    )


@pytest.fixture
def job_with_positive_sponsorship() -> dict:
    """Job whose description explicitly mentions visa sponsorship."""
    return {
        "job_id": "test_pos_01",
        "title": "Java Developer",
        "employer": "Test Corp",
        "description": (
            "Join our backend team. Java, Spring Boot required. "
            "We offer visa sponsorship for skilled worker applicants. "
            "Certificate of Sponsorship available."
        ),
        "url": "https://testcorp.com/jobs/java-dev",
    }


@pytest.fixture
def job_with_negative_sponsorship() -> dict:
    """Job whose description explicitly denies sponsorship."""
    return {
        "job_id": "test_neg_01",
        "title": "Software Engineer",
        "employer": "No Sponsor Ltd",
        "description": (
            "We need a talented Software Engineer. Python, React skills. "
            "No sponsorship available. Must have right to work in the UK."
        ),
        "url": "https://nosponsor.co.uk/jobs/sw-eng",
    }


@pytest.fixture
def job_with_no_mention() -> dict:
    """Job with no sponsorship mention at all."""
    return {
        "job_id": "test_unk_01",
        "title": "DevOps Engineer",
        "employer": "Silent Corp",
        "description": (
            "We need a DevOps Engineer skilled in Kubernetes, Terraform, and AWS. "
            "Competitive salary. Great benefits package."
        ),
        "url": "https://silentcorp.com/jobs/devops",
    }


@pytest.fixture
def licensed_employer_status() -> dict:
    """Employer status for a known licensed sponsor."""
    return {
        "is_licensed": True,
        "licence_type": "Worker",
        "confidence": "high",
        "source": "test",
    }


@pytest.fixture
def unlicensed_employer_status() -> dict:
    """Employer status for an employer not on the register."""
    return {
        "is_licensed": False,
        "licence_type": "none_found",
        "confidence": "medium",
        "source": "test",
    }


# ---------------------------------------------------------------------------
# Tests: check_vacancy
# ---------------------------------------------------------------------------


class TestCheckVacancy:
    """Tests for the check_vacancy method."""

    def test_positive_keywords_detected(
        self,
        checker: SponsorshipChecker,
        job_with_positive_sponsorship: dict,
        licensed_employer_status: dict,
    ) -> None:
        """JD with 'visa sponsorship' → status should indicate sponsorship available."""
        result = checker.check_vacancy(
            job_with_positive_sponsorship, licensed_employer_status
        )
        assert result["status"] in ("confirmed_role",), (
            f"Expected confirmed_role, got {result['status']}"
        )
        assert len(result["evidence"]) > 0, "Expected evidence list to be non-empty."

    def test_negative_keywords_detected(
        self,
        checker: SponsorshipChecker,
        job_with_negative_sponsorship: dict,
        unlicensed_employer_status: dict,
    ) -> None:
        """JD with 'no sponsorship' → status should be sponsorship_unavailable."""
        result = checker.check_vacancy(
            job_with_negative_sponsorship, unlicensed_employer_status
        )
        assert result["status"] == "sponsorship_unavailable", (
            f"Expected sponsorship_unavailable, got {result['status']}"
        )
        assert len(result["evidence"]) > 0

    def test_unable_to_verify_with_no_mention(
        self,
        checker: SponsorshipChecker,
        job_with_no_mention: dict,
        unlicensed_employer_status: dict,
    ) -> None:
        """JD with no sponsorship mention and unlicensed employer → unable_to_verify."""
        result = checker.check_vacancy(
            job_with_no_mention, unlicensed_employer_status
        )
        assert result["status"] == "unable_to_verify", (
            f"Expected unable_to_verify, got {result['status']}"
        )

    def test_licensed_unconfirmed_when_no_mention_but_licensed(
        self,
        checker: SponsorshipChecker,
        job_with_no_mention: dict,
        licensed_employer_status: dict,
    ) -> None:
        """No mention in JD but employer is licensed → licensed_employer_unconfirmed."""
        result = checker.check_vacancy(
            job_with_no_mention, licensed_employer_status
        )
        assert result["status"] == "licensed_employer_unconfirmed", (
            f"Expected licensed_employer_unconfirmed, got {result['status']}"
        )

    def test_result_has_required_keys(
        self,
        checker: SponsorshipChecker,
        job_with_positive_sponsorship: dict,
        licensed_employer_status: dict,
    ) -> None:
        """check_vacancy result should contain all required keys."""
        result = checker.check_vacancy(
            job_with_positive_sponsorship, licensed_employer_status
        )
        for key in ("status", "evidence", "confidence", "notes"):
            assert key in result, f"Missing key '{key}' in check_vacancy result."

    def test_negative_overrides_licensed_employer(
        self,
        checker: SponsorshipChecker,
        job_with_negative_sponsorship: dict,
        licensed_employer_status: dict,
    ) -> None:
        """Explicit 'no sponsorship' in JD overrides a licensed employer status."""
        result = checker.check_vacancy(
            job_with_negative_sponsorship, licensed_employer_status
        )
        assert result["status"] == "sponsorship_unavailable"


# ---------------------------------------------------------------------------
# Tests: classify
# ---------------------------------------------------------------------------


class TestClassify:
    """Tests for the classify method."""

    def test_classification_confirmed(
        self,
        checker: SponsorshipChecker,
        licensed_employer_status: dict,
    ) -> None:
        """Licensed employer + confirmed_role vacancy → final status is confirmed_role."""
        vacancy_status = {
            "status": "confirmed_role",
            "evidence": ["visa sponsorship"],
            "confidence": "high",
        }
        result = checker.classify(licensed_employer_status, vacancy_status)
        assert result == "confirmed_role"

    def test_classification_unavailable(
        self,
        checker: SponsorshipChecker,
        licensed_employer_status: dict,
    ) -> None:
        """sponsorship_unavailable vacancy → final status is sponsorship_unavailable."""
        vacancy_status = {
            "status": "sponsorship_unavailable",
            "evidence": ["no sponsorship"],
            "confidence": "high",
        }
        result = checker.classify(licensed_employer_status, vacancy_status)
        assert result == "sponsorship_unavailable"

    def test_classification_licensed_unconfirmed(
        self,
        checker: SponsorshipChecker,
        licensed_employer_status: dict,
    ) -> None:
        """Licensed employer + unconfirmed vacancy → licensed_employer_unconfirmed."""
        vacancy_status = {
            "status": "licensed_employer_unconfirmed",
            "evidence": [],
            "confidence": "medium",
        }
        result = checker.classify(licensed_employer_status, vacancy_status)
        assert result == "licensed_employer_unconfirmed"

    def test_classification_unlicensed_unconfirmed(
        self,
        checker: SponsorshipChecker,
        unlicensed_employer_status: dict,
    ) -> None:
        """Unlicensed employer + unconfirmed vacancy → unable_to_verify."""
        vacancy_status = {
            "status": "licensed_employer_unconfirmed",
            "evidence": [],
            "confidence": "low",
        }
        result = checker.classify(unlicensed_employer_status, vacancy_status)
        assert result == "unable_to_verify"


# ---------------------------------------------------------------------------
# Tests: check_employer (mocked HTTP)
# ---------------------------------------------------------------------------


class TestCheckEmployer:
    """Tests for check_employer with mocked HTTP calls."""

    def test_employer_found_in_register(
        self,
        checker: SponsorshipChecker,
        sample_register_csv: str,
    ) -> None:
        """Employer present in mocked register CSV → is_licensed=True."""
        with mock.patch("requests.get") as mock_get:
            mock_response = mock.MagicMock()
            mock_response.content = sample_register_csv.encode("utf-8")
            mock_response.raise_for_status = mock.MagicMock()
            mock_get.return_value = mock_response

            result = checker.check_employer("Barclays Bank PLC")

        assert result["is_licensed"] is True, (
            f"Expected is_licensed=True for Barclays, got: {result}"
        )

    def test_employer_not_found_in_register(
        self,
        checker: SponsorshipChecker,
        sample_register_csv: str,
    ) -> None:
        """Employer absent from mocked register → is_licensed=False."""
        with mock.patch("requests.get") as mock_get:
            mock_response = mock.MagicMock()
            mock_response.content = sample_register_csv.encode("utf-8")
            mock_response.raise_for_status = mock.MagicMock()
            mock_get.return_value = mock_response

            result = checker.check_employer("Random Unknown Company Ltd")

        assert result["is_licensed"] is False

    def test_register_download_failure_returns_result(
        self,
        checker: SponsorshipChecker,
    ) -> None:
        """If the register cannot be downloaded and no cache exists, result is graceful."""
        import requests as req

        with mock.patch("requests.get") as mock_get:
            mock_get.side_effect = req.RequestException("Network error")

            result = checker.check_employer("Any Employer Ltd")

        # Should not raise; should return a dict
        assert isinstance(result, dict)
        assert "is_licensed" in result

    def test_result_has_required_keys(
        self,
        checker: SponsorshipChecker,
        sample_register_csv: str,
    ) -> None:
        """check_employer result should always contain required keys."""
        with mock.patch("requests.get") as mock_get:
            mock_response = mock.MagicMock()
            mock_response.content = sample_register_csv.encode("utf-8")
            mock_response.raise_for_status = mock.MagicMock()
            mock_get.return_value = mock_response

            result = checker.check_employer("Barclays Bank PLC")

        for key in ("is_licensed", "licence_type", "last_checked", "source"):
            assert key in result, f"Missing key '{key}' in check_employer result."
