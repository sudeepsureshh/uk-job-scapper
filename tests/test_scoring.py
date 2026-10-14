"""
Tests for the JobScorer matching engine.
"""
import pytest
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching.scorer import JobScorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candidate_profile() -> dict:
    """Minimal but realistic candidate profile for testing."""
    return {
        "candidate": {
            "name": "Sudeep S",
            "work_experience": [
                {
                    "company": "Centrico",
                    "role": "Java Developer",
                    "years": 3.0,
                    "domain": "Banking",
                }
            ],
            "skills": {
                "languages": {
                    "primary": ["Java", "Python"],
                    "secondary": ["C#", "JavaScript", "Kotlin"],
                },
                "frameworks": {
                    "backend": ["Spring Boot", "Spring MVC", "REST APIs", "Microservices"],
                    "frontend": ["React", "Angular"],
                },
                "databases": ["PostgreSQL", "Oracle", "MongoDB"],
                "devops_cloud": ["Docker", "Kubernetes", "Jenkins", "AWS", "Azure", "CI/CD"],
                "testing": ["JUnit", "Mockito"],
                "tools": ["Maven", "Git", "Bitbucket"],
            },
        },
        "primary_job_targets": [
            "Java Developer", "Spring Boot Developer", "Backend Engineer"
        ],
        "secondary_job_targets": [
            "Software Engineer", "Python Developer", "Graduate Software Engineer"
        ],
        "location_preferences": {
            "primary": ["Sheffield", "London", "Manchester"],
            "acceptable": ["Remote (UK)", "Hybrid (UK)", "United Kingdom"],
        },
        "salary_expectation_gbp_min": 40000,
    }


@pytest.fixture
def scorer(candidate_profile) -> JobScorer:
    """Initialised JobScorer for use in tests."""
    return JobScorer(candidate_profile)


@pytest.fixture
def java_job() -> dict:
    """A typical Java/Spring Boot job that should match well."""
    return {
        "job_id": "test_001",
        "title": "Senior Java Backend Engineer",
        "employer": "Revolut",
        "location": "London, UK (Hybrid)",
        "salary_min": 70000,
        "salary_max": 90000,
        "salary_currency": "GBP",
        "description": (
            "We are looking for a Senior Java Backend Engineer to join our Payments team. "
            "You will build microservices using Java 17, Spring Boot, Spring MVC, and Kafka. "
            "Experience with PostgreSQL, MongoDB, Docker, Kubernetes required. "
            "CI/CD pipeline experience (Jenkins/GitHub Actions) a strong advantage. "
            "5+ years of Java development experience. REST API design skills essential. "
            "Visa sponsorship available for the right candidate."
        ),
        "url": "https://revolut.com/test_001",
        "apply_url": "https://revolut.com/test_001/apply",
        "source": "test",
        "scraped_at": "2026-10-14T00:00:00",
    }


@pytest.fixture
def marketing_job() -> dict:
    """A marketing role that should score very low for a Java engineer."""
    return {
        "job_id": "test_002",
        "title": "Digital Marketing Manager",
        "employer": "Generic Retail",
        "location": "Manchester, UK",
        "salary_min": 35000,
        "salary_max": 45000,
        "salary_currency": "GBP",
        "description": (
            "We are hiring a Digital Marketing Manager to lead our brand strategy. "
            "You will manage social media campaigns, email marketing, and SEO. "
            "Experience with Google Analytics, HubSpot CRM, and paid social required. "
            "Strong copywriting and creative skills needed. "
            "No technical or engineering background required. "
            "Must have right to work in the UK — no sponsorship available."
        ),
        "url": "https://genericretail.co.uk/test_002",
        "apply_url": "https://genericretail.co.uk/test_002/apply",
        "source": "test",
        "scraped_at": "2026-10-14T00:00:00",
    }


@pytest.fixture
def kafka_job() -> dict:
    """A job requiring Kafka, which is NOT in the candidate's profile."""
    return {
        "job_id": "test_003",
        "title": "Java Kafka Streams Developer",
        "employer": "Barclays",
        "location": "London, UK",
        "salary_min": 65000,
        "salary_max": 80000,
        "salary_currency": "GBP",
        "description": (
            "Barclays is seeking a Java Kafka Streams Developer. "
            "Mandatory skills: Java, Spring Boot, Kafka, Kafka Streams, Avro, Schema Registry. "
            "Desirable: Flink, Spark, event-driven architecture, PostgreSQL. "
            "3+ years experience. Hybrid working."
        ),
        "url": "https://jobs.barclays.com/test_003",
        "apply_url": "https://jobs.barclays.com/test_003/apply",
        "source": "test",
        "scraped_at": "2026-10-14T00:00:00",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJobScorer:
    """Tests for the JobScorer class."""

    def test_java_role_scores_high(self, scorer: JobScorer, java_job: dict) -> None:
        """A Java/Spring Boot JD should score 70 or above."""
        result = scorer.score(java_job)
        assert result["relevance_score"] >= 70, (
            f"Expected score >= 70 for Java role, got {result['relevance_score']}. "
            f"Breakdown: {result.get('score_breakdown')}"
        )

    def test_unrelated_role_scores_low(
        self, scorer: JobScorer, marketing_job: dict
    ) -> None:
        """A marketing role should score well below a relevant Java role.
        Location and salary dimensions can still contribute even for unrelated roles,
        but skills/responsibilities match should keep the overall score under 55.
        """
        result = scorer.score(marketing_job)
        assert result["relevance_score"] < 55, (
            f"Expected score < 55 for marketing role, got {result['relevance_score']}. "
            f"Breakdown: {result.get('score_breakdown')}"
        )

    def test_missing_skills_identified(
        self, scorer: JobScorer, kafka_job: dict
    ) -> None:
        """Kafka appears in the JD but is not in the candidate profile → should be in missing_skills."""
        result = scorer.score(kafka_job)
        missing = [s.lower() for s in (result.get("missing_skills") or [])]
        assert "kafka" in missing, (
            f"Expected 'kafka' in missing_skills, got: {result.get('missing_skills')}"
        )

    def test_matched_skills_identified(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """Java appears in both the JD and the candidate profile → should be in matched_skills."""
        result = scorer.score(java_job)
        matched = [s.lower() for s in (result.get("matched_skills") or [])]
        assert "java" in matched, (
            f"Expected 'java' in matched_skills, got: {result.get('matched_skills')}"
        )

    def test_spring_boot_in_matched_skills(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """Spring Boot in JD and profile → should be in matched_skills."""
        result = scorer.score(java_job)
        matched = [s.lower() for s in (result.get("matched_skills") or [])]
        assert "spring boot" in matched, (
            f"Expected 'spring boot' in matched_skills, got: {matched}"
        )

    def test_score_breakdown_sums_to_score(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """Sum of score_breakdown values should equal relevance_score."""
        result = scorer.score(java_job)
        breakdown = result.get("score_breakdown") or {}
        breakdown_sum = sum(breakdown.values())
        score = result["relevance_score"]
        # Allow ±1 for rounding
        assert abs(breakdown_sum - score) <= 1, (
            f"Score breakdown sum {breakdown_sum} != relevance_score {score}. "
            f"Breakdown: {breakdown}"
        )

    def test_score_breakdown_has_all_dimensions(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """score_breakdown should contain all expected dimension keys."""
        result = scorer.score(java_job)
        breakdown = result.get("score_breakdown") or {}
        expected_keys = {
            "skills_match",
            "experience_match",
            "responsibilities_match",
            "location_match",
            "salary_match",
            "sponsorship_bonus",
        }
        assert expected_keys.issubset(set(breakdown.keys())), (
            f"Missing keys: {expected_keys - set(breakdown.keys())}"
        )

    def test_score_is_capped_at_100(self, scorer: JobScorer, java_job: dict) -> None:
        """relevance_score should never exceed 100."""
        result = scorer.score(java_job)
        assert result["relevance_score"] <= 100

    def test_score_is_non_negative(
        self, scorer: JobScorer, marketing_job: dict
    ) -> None:
        """relevance_score should never be negative."""
        result = scorer.score(marketing_job)
        assert result["relevance_score"] >= 0

    def test_match_explanation_is_string(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """match_explanation should be a non-empty string."""
        result = scorer.score(java_job)
        explanation = result.get("match_explanation", "")
        assert isinstance(explanation, str) and len(explanation) > 0

    def test_sponsorship_bonus_java_role(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """Java role explicitly mentions visa sponsorship → sponsorship_bonus > 0."""
        result = scorer.score(java_job)
        breakdown = result.get("score_breakdown") or {}
        assert breakdown.get("sponsorship_bonus", 0) > 0, (
            "Expected sponsorship bonus for job mentioning visa sponsorship."
        )

    def test_no_sponsorship_bonus_for_no_sponsorship_job(
        self, scorer: JobScorer, marketing_job: dict
    ) -> None:
        """Job explicitly saying no sponsorship → sponsorship_bonus = 0."""
        result = scorer.score(marketing_job)
        breakdown = result.get("score_breakdown") or {}
        assert breakdown.get("sponsorship_bonus", 0) == 0, (
            "Expected zero sponsorship bonus for job explicitly denying sponsorship."
        )

    def test_extract_requirements_returns_expected_structure(
        self, scorer: JobScorer
    ) -> None:
        """extract_requirements should return dict with expected keys."""
        text = "we need a senior java developer with 5 years of spring boot experience"
        result = scorer.extract_requirements(text)
        assert "skills" in result
        assert "years_required" in result
        assert "seniority" in result
        assert "keywords" in result

    def test_extract_requirements_detects_years(self, scorer: JobScorer) -> None:
        """Years of experience should be extracted from text."""
        text = "minimum 3 years of java development experience required"
        result = scorer.extract_requirements(text)
        assert result["years_required"] == 3

    def test_extract_requirements_detects_seniority(self, scorer: JobScorer) -> None:
        """Seniority level should be detected from text."""
        text = "we are looking for a senior java developer to join our team"
        result = scorer.extract_requirements(text)
        assert result["seniority"] == "senior"

    def test_original_job_dict_not_mutated(
        self, scorer: JobScorer, java_job: dict
    ) -> None:
        """score() should not mutate the original job dict."""
        original_keys = set(java_job.keys())
        scorer.score(java_job)
        assert set(java_job.keys()) == original_keys, (
            "scorer.score() mutated the original job dict."
        )
