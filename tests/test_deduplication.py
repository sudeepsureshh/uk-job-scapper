"""
Tests for the JobDatabase deduplication and retrieval logic.
Uses an in-memory SQLite database via tmp_path to avoid side effects.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import JobDatabase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> JobDatabase:
    """Return a fresh JobDatabase pointing at a temp file."""
    db_path = str(tmp_path / "test_jobs.db")
    database = JobDatabase(db_path=db_path)
    yield database
    database.close()


@pytest.fixture
def sample_job() -> dict:
    """A minimal job dict for testing."""
    return {
        "job_id": "test_dedup_001",
        "title": "Java Developer",
        "employer": "Test Bank PLC",
        "location": "London, UK",
        "salary_min": 55000,
        "salary_max": 70000,
        "salary_currency": "GBP",
        "description": "Java Spring Boot developer role.",
        "url": "https://testbank.com/jobs/java-developer-001",
        "apply_url": "https://testbank.com/jobs/java-developer-001/apply",
        "posted_date": "2026-10-14",
        "closing_date": None,
        "source": "test",
        "scraped_at": datetime.utcnow().isoformat(),
        "relevance_score": 75.0,
        "matched_skills": ["Java", "Spring Boot"],
        "missing_skills": ["Kafka"],
        "match_explanation": "Good match.",
        "score_breakdown": {"skills_match": 30},
        "sponsorship_status": "confirmed_role",
        "sponsorship_evidence": ["visa sponsorship"],
        "sponsorship_notes": "Confirmed in job ad.",
    }


def _make_job(
    job_id: str,
    url: str,
    title: str = "Java Dev",
    employer: str = "Corp",
    score: float = 50.0,
) -> dict:
    """Helper to create a minimal job dict for testing."""
    return {
        "job_id": job_id,
        "title": title,
        "employer": employer,
        "location": "UK",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "GBP",
        "description": "Java developer role.",
        "url": url,
        "apply_url": url + "/apply",
        "posted_date": "2026-10-14",
        "closing_date": None,
        "source": "test",
        "scraped_at": datetime.utcnow().isoformat(),
        "relevance_score": score,
        "matched_skills": ["Java"],
        "missing_skills": [],
        "sponsorship_status": "unable_to_verify",
    }


# ---------------------------------------------------------------------------
# Tests: upsert_job
# ---------------------------------------------------------------------------


class TestUpsertJob:
    """Tests for the upsert_job deduplication logic."""

    def test_new_job_returns_true(self, db: JobDatabase, sample_job: dict) -> None:
        """First insert of a job should return True (is new)."""
        result = db.upsert_job(sample_job)
        assert result is True, "Expected True for first-time job insert."

    def test_duplicate_url_returns_false(
        self, db: JobDatabase, sample_job: dict
    ) -> None:
        """Second insert of the same URL should return False (already seen)."""
        db.upsert_job(sample_job)
        result = db.upsert_job(sample_job)
        assert result is False, "Expected False for duplicate URL insert."

    def test_different_url_returns_true(
        self, db: JobDatabase
    ) -> None:
        """Two jobs with different URLs should both return True."""
        job_a = _make_job("id_a", "https://company.com/job-a")
        job_b = _make_job("id_b", "https://company.com/job-b")
        assert db.upsert_job(job_a) is True
        assert db.upsert_job(job_b) is True

    def test_database_stores_job_correctly(
        self, db: JobDatabase, sample_job: dict
    ) -> None:
        """Job data should be retrievable after insert."""
        db.upsert_job(sample_job)
        jobs = db.get_all_jobs()
        assert len(jobs) == 1
        stored = jobs[0]
        assert stored["title"] == sample_job["title"]
        assert stored["employer"] == sample_job["employer"]
        assert stored["url"] == sample_job["url"]

    def test_upsert_updates_existing_data(
        self, db: JobDatabase, sample_job: dict
    ) -> None:
        """Re-upserting should update mutable fields like relevance_score."""
        db.upsert_job(sample_job)
        updated = dict(sample_job)
        updated["relevance_score"] = 88.0
        db.upsert_job(updated)
        jobs = db.get_all_jobs()
        assert len(jobs) == 1
        assert jobs[0]["relevance_score"] == 88.0

    def test_list_fields_serialised_and_deserialised(
        self, db: JobDatabase, sample_job: dict
    ) -> None:
        """matched_skills and missing_skills should survive round-trip as lists."""
        db.upsert_job(sample_job)
        jobs = db.get_all_jobs()
        assert isinstance(jobs[0]["matched_skills"], list)
        assert isinstance(jobs[0]["missing_skills"], list)


# ---------------------------------------------------------------------------
# Tests: get_new_jobs
# ---------------------------------------------------------------------------


class TestGetNewJobs:
    """Tests for the get_new_jobs date filtering."""

    def test_get_new_jobs_after_cutoff(self, db: JobDatabase) -> None:
        """Jobs inserted after cutoff should be returned."""
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=1)

        job = _make_job("id_new", "https://company.com/new-job")
        db.upsert_job(job)

        new_jobs = db.get_new_jobs(since=cutoff)
        assert len(new_jobs) >= 1
        urls = [j["url"] for j in new_jobs]
        assert "https://company.com/new-job" in urls

    def test_jobs_before_cutoff_not_returned(self, db: JobDatabase) -> None:
        """Jobs from before the cutoff should not be in get_new_jobs results."""
        # Insert a job, then set a future cutoff
        job = _make_job("id_old", "https://company.com/old-job")
        db.upsert_job(job)

        future_cutoff = datetime.utcnow() + timedelta(hours=1)
        new_jobs = db.get_new_jobs(since=future_cutoff)
        assert len(new_jobs) == 0

    def test_get_new_jobs_with_multiple_jobs(self, db: JobDatabase) -> None:
        """get_new_jobs should return all jobs inserted since cutoff."""
        cutoff = datetime.utcnow() - timedelta(seconds=5)

        for i in range(3):
            db.upsert_job(
                _make_job(f"id_{i}", f"https://company.com/job-{i}")
            )

        new_jobs = db.get_new_jobs(since=cutoff)
        assert len(new_jobs) == 3


# ---------------------------------------------------------------------------
# Tests: scrape run tracking
# ---------------------------------------------------------------------------


class TestScrapeRunTracking:
    """Tests for scrape run lifecycle methods."""

    def test_mark_run_start_returns_id(self, db: JobDatabase) -> None:
        """mark_run_start should return a positive integer run_id."""
        run_id = db.mark_run_start()
        assert isinstance(run_id, int)
        assert run_id > 0

    def test_mark_run_complete_updates_record(self, db: JobDatabase) -> None:
        """mark_run_complete should update the run record with results."""
        run_id = db.mark_run_start()
        db.mark_run_complete(run_id, jobs_found=50, new_jobs=10, status="success")

        last_run = db.get_last_run()
        assert last_run is not None
        assert last_run["jobs_found"] == 50
        assert last_run["new_jobs"] == 10
        assert last_run["status"] == "success"

    def test_get_last_run_returns_none_when_no_runs(self, db: JobDatabase) -> None:
        """get_last_run should return None when no runs have been recorded."""
        last_run = db.get_last_run()
        assert last_run is None

    def test_multiple_runs_tracked(self, db: JobDatabase) -> None:
        """Multiple runs should be trackable and retrievable."""
        run1 = db.mark_run_start()
        db.mark_run_complete(run1, 10, 5, "success")

        run2 = db.mark_run_start()
        db.mark_run_complete(run2, 8, 0, "success")

        history = db.get_run_history(limit=5)
        assert len(history) == 2


# ---------------------------------------------------------------------------
# Tests: get_all_jobs filtering
# ---------------------------------------------------------------------------


class TestGetAllJobs:
    """Tests for get_all_jobs filtering options."""

    def test_filter_by_min_score(self, db: JobDatabase) -> None:
        """Jobs below min_score should be excluded."""
        db.upsert_job(_make_job("id_high", "https://a.com/high", score=80.0))
        db.upsert_job(_make_job("id_low", "https://a.com/low", score=30.0))

        results = db.get_all_jobs(filters={"min_score": 60.0})
        assert len(results) == 1
        assert results[0]["url"] == "https://a.com/high"

    def test_filter_by_employer(self, db: JobDatabase) -> None:
        """Jobs from other employers should be excluded."""
        db.upsert_job(
            _make_job("id_rev", "https://revolut.com/job", employer="Revolut")
        )
        db.upsert_job(
            _make_job("id_bar", "https://barclays.com/job", employer="Barclays")
        )

        results = db.get_all_jobs(filters={"employer": "Revolut"})
        assert len(results) == 1
        assert results[0]["employer"] == "Revolut"

    def test_no_filters_returns_all(self, db: JobDatabase) -> None:
        """Without filters, all jobs are returned."""
        for i in range(5):
            db.upsert_job(_make_job(f"id_{i}", f"https://a.com/job-{i}"))

        results = db.get_all_jobs()
        assert len(results) == 5

    def test_sorted_by_relevance_score_descending(self, db: JobDatabase) -> None:
        """Results should be sorted by relevance_score descending."""
        db.upsert_job(_make_job("id_med", "https://a.com/med", score=50.0))
        db.upsert_job(_make_job("id_high", "https://a.com/high", score=90.0))
        db.upsert_job(_make_job("id_low", "https://a.com/low", score=20.0))

        results = db.get_all_jobs()
        scores = [r["relevance_score"] for r in results]
        assert scores == sorted(scores, reverse=True), (
            f"Expected descending order, got: {scores}"
        )
