"""
SQLite-backed job database.
Tracks job listings, deduplicates, and records scrape run history.
"""
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# SQLite schema version – increment if schema changes
SCHEMA_VERSION = 1


class JobDatabase:
    """
    Persistent SQLite store for job listings.

    Provides:
    - Upsert with deduplication by URL
    - Scrape run history
    - Filtering and retrieval
    - JSON export for GitHub Actions artifact persistence
    """

    def __init__(self, db_path: str = "storage/jobs.db") -> None:
        """
        Initialise the database, creating tables if required.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazy-initialise and return the database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent read performance
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "JobDatabase":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create tables if they do not already exist."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id          TEXT PRIMARY KEY,
                title           TEXT,
                employer        TEXT,
                location        TEXT,
                salary_min      REAL,
                salary_max      REAL,
                salary_currency TEXT DEFAULT 'GBP',
                description     TEXT,
                url             TEXT UNIQUE,
                apply_url       TEXT,
                posted_date     TEXT,
                closing_date    TEXT,
                source          TEXT,
                scraped_at      TEXT,
                relevance_score REAL,
                matched_skills  TEXT,
                missing_skills  TEXT,
                match_explanation TEXT,
                score_breakdown TEXT,
                sponsorship_status TEXT,
                sponsorship_evidence TEXT,
                sponsorship_notes TEXT,
                is_new          INTEGER DEFAULT 1,
                first_seen      TEXT,
                last_seen       TEXT,
                url_hash        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs (url);
            CREATE INDEX IF NOT EXISTS idx_jobs_relevance ON jobs (relevance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs (first_seen);
            CREATE INDEX IF NOT EXISTS idx_jobs_sponsorship ON jobs (sponsorship_status);

            CREATE TABLE IF NOT EXISTS scrape_runs (
                run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                jobs_found  INTEGER DEFAULT 0,
                new_jobs    INTEGER DEFAULT 0,
                status      TEXT DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    def upsert_job(self, job: dict) -> bool:
        """
        Insert a new job or update an existing one (matched by URL).

        Args:
            job: Job dict conforming to BaseJobScraper.RESULT_SCHEMA,
                 possibly enriched with scoring and sponsorship fields.

        Returns:
            True if this is a new job (first time seen), False if updated.
        """
        url = (job.get("url") or "").strip()
        if not url:
            # Generate a pseudo-URL from job_id or title+employer
            url = f"no-url:{job.get('job_id', '')}:{job.get('title', '')}:{job.get('employer', '')}"

        url_hash = hashlib.sha256(url.lower().encode()).hexdigest()
        now_iso = datetime.utcnow().isoformat()

        # Check existence
        existing = self.conn.execute(
            "SELECT job_id, first_seen FROM jobs WHERE url = ?",
            (url,),
        ).fetchone()

        is_new = existing is None
        first_seen = now_iso if is_new else existing["first_seen"]

        job_id = job.get("job_id") or url_hash[:16]

        # Serialise list/dict fields
        matched_skills = _json_or_null(job.get("matched_skills"))
        missing_skills = _json_or_null(job.get("missing_skills"))
        score_breakdown = _json_or_null(job.get("score_breakdown"))
        sponsorship_evidence = _json_or_null(job.get("sponsorship_evidence"))

        try:
            self.conn.execute(
                """
                INSERT INTO jobs (
                    job_id, title, employer, location,
                    salary_min, salary_max, salary_currency,
                    description, url, apply_url,
                    posted_date, closing_date, source, scraped_at,
                    relevance_score, matched_skills, missing_skills,
                    match_explanation, score_breakdown,
                    sponsorship_status, sponsorship_evidence, sponsorship_notes,
                    is_new, first_seen, last_seen, url_hash
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?
                )
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    employer = excluded.employer,
                    location = excluded.location,
                    salary_min = excluded.salary_min,
                    salary_max = excluded.salary_max,
                    description = excluded.description,
                    scraped_at = excluded.scraped_at,
                    relevance_score = excluded.relevance_score,
                    matched_skills = excluded.matched_skills,
                    missing_skills = excluded.missing_skills,
                    match_explanation = excluded.match_explanation,
                    score_breakdown = excluded.score_breakdown,
                    sponsorship_status = excluded.sponsorship_status,
                    sponsorship_evidence = excluded.sponsorship_evidence,
                    sponsorship_notes = excluded.sponsorship_notes,
                    is_new = CASE WHEN jobs.first_seen IS NULL THEN 1 ELSE 0 END,
                    last_seen = excluded.last_seen
                """,
                (
                    job_id,
                    job.get("title"),
                    job.get("employer"),
                    job.get("location"),
                    job.get("salary_min"),
                    job.get("salary_max"),
                    job.get("salary_currency", "GBP"),
                    job.get("description"),
                    url,
                    job.get("apply_url"),
                    job.get("posted_date"),
                    job.get("closing_date"),
                    job.get("source"),
                    job.get("scraped_at", now_iso),
                    job.get("relevance_score"),
                    matched_skills,
                    missing_skills,
                    job.get("match_explanation"),
                    score_breakdown,
                    job.get("sponsorship_status"),
                    sponsorship_evidence,
                    job.get("sponsorship_notes"),
                    1 if is_new else 0,
                    first_seen,
                    now_iso,
                    url_hash,
                ),
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            logger.error("Database error upserting job %s: %s", job_id, exc)
            return False

        return is_new

    def get_new_jobs(self, since: datetime) -> list[dict]:
        """
        Return jobs that were first seen after the given datetime.

        Args:
            since: Cutoff datetime (UTC).

        Returns:
            List of job dicts, sorted by relevance_score descending.
        """
        since_iso = since.isoformat()
        rows = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE first_seen >= ?
            ORDER BY relevance_score DESC, first_seen DESC
            """,
            (since_iso,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_all_jobs(self, filters: Optional[dict] = None) -> list[dict]:
        """
        Retrieve all jobs, optionally filtered.

        Args:
            filters: Dict with optional keys:
                - sponsorship_status (str): Filter by sponsorship status.
                - min_score (float): Minimum relevance score.
                - employer (str): Filter by employer name (partial match).

        Returns:
            List of job dicts sorted by relevance_score descending.
        """
        query = "SELECT * FROM jobs WHERE 1=1"
        params: list[Any] = []

        if filters:
            if filters.get("sponsorship_status"):
                query += " AND sponsorship_status = ?"
                params.append(filters["sponsorship_status"])

            if filters.get("min_score") is not None:
                query += " AND relevance_score >= ?"
                params.append(float(filters["min_score"]))

            if filters.get("employer"):
                query += " AND employer LIKE ?"
                params.append(f"%{filters['employer']}%")

        query += " ORDER BY relevance_score DESC, first_seen DESC"

        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_job_count(self) -> int:
        """Return total number of jobs in the database."""
        row = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Scrape run tracking
    # ------------------------------------------------------------------

    def mark_run_start(self) -> int:
        """
        Record the start of a new scrape run.

        Returns:
            The run_id for the new run.
        """
        cursor = self.conn.execute(
            "INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')",
            (datetime.utcnow().isoformat(),),
        )
        self.conn.commit()
        run_id = cursor.lastrowid
        logger.info("Started scrape run id=%d", run_id)
        return run_id  # type: ignore[return-value]

    def mark_run_complete(
        self,
        run_id: int,
        jobs_found: int,
        new_jobs: int,
        status: str = "success",
    ) -> None:
        """
        Update the scrape run record on completion.

        Args:
            run_id: The run_id returned by mark_run_start().
            jobs_found: Total number of jobs scraped.
            new_jobs: Number of newly discovered jobs.
            status: "success" | "partial" | "failed".
        """
        self.conn.execute(
            """
            UPDATE scrape_runs
            SET finished_at = ?, jobs_found = ?, new_jobs = ?, status = ?
            WHERE run_id = ?
            """,
            (datetime.utcnow().isoformat(), jobs_found, new_jobs, status, run_id),
        )
        self.conn.commit()
        logger.info(
            "Completed scrape run id=%d: %d found, %d new, status=%s",
            run_id, jobs_found, new_jobs, status,
        )

    def get_last_run(self) -> Optional[dict]:
        """Return the most recent completed scrape run, or None."""
        row = self.conn.execute(
            """
            SELECT * FROM scrape_runs
            ORDER BY run_id DESC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_run_history(self, limit: int = 10) -> list[dict]:
        """Return the last N scrape runs."""
        rows = self.conn.execute(
            "SELECT * FROM scrape_runs ORDER BY run_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_to_github_artifact(self, output_dir: str = "output") -> str:
        """
        Export the database contents as JSON for use as a GitHub Actions artifact.

        The JSON file can be committed or uploaded as an artifact to persist
        state across workflow runs.

        Args:
            output_dir: Directory to write the export file.

        Returns:
            Path to the exported JSON file.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        export_path = Path(output_dir) / "jobs_export.json"

        jobs = self.get_all_jobs()
        runs = self.get_run_history(20)

        export_data = {
            "exported_at": datetime.utcnow().isoformat(),
            "total_jobs": len(jobs),
            "jobs": jobs,
            "scrape_runs": runs,
        }

        export_path.write_text(
            json.dumps(export_data, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Exported %d jobs to %s", len(jobs), export_path)
        return str(export_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a regular dict, deserialising JSON fields."""
    d = dict(row)
    for key in ("matched_skills", "missing_skills", "score_breakdown", "sponsorship_evidence"):
        raw = d.get(key)
        if raw and isinstance(raw, str):
            try:
                d[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _json_or_null(value: Any) -> Optional[str]:
    """Serialise a value to JSON string, or return None if empty."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
