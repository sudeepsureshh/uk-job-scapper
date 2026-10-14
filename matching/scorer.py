"""
Job scoring engine using TF-IDF-style keyword matching.
No LLM is used — scoring is purely rule-based and reproducible.
"""
import logging
import math
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "skills_match": 35,
    "experience_match": 25,
    "responsibilities_match": 15,
    "location_match": 10,
    "salary_match": 10,
    "sponsorship_bonus": 5,
}

TOTAL_POSSIBLE = sum(SCORE_WEIGHTS.values())  # 100

# Seniority level keywords mapped to approximate years of experience
SENIORITY_YEARS = {
    "junior": 1,
    "entry": 1,
    "graduate": 0,
    "associate": 1,
    "mid": 3,
    "intermediate": 3,
    "senior": 5,
    "lead": 7,
    "principal": 8,
    "staff": 8,
    "architect": 8,
    "manager": 6,
    "head": 8,
    "director": 10,
}

# Functional competencies mapped to keywords that signal them
FUNCTIONAL_COMPETENCIES = {
    "api_development": [
        "rest api", "restful", "api design", "api development",
        "web services", "openapi", "swagger", "endpoint",
    ],
    "microservices": [
        "microservices", "microservice", "distributed systems",
        "service mesh", "event driven", "event-driven",
    ],
    "banking_payments": [
        "banking", "payments", "payment", "financial", "fintech",
        "pos", "transaction", "swift", "iso 20022", "open banking",
    ],
    "backend_development": [
        "backend", "back-end", "back end", "server-side", "server side",
        "java", "spring boot", "spring", "jvm",
    ],
    "cloud_devops": [
        "docker", "kubernetes", "k8s", "cloud", "aws", "azure", "gcp",
        "ci/cd", "pipeline", "jenkins", "devops",
    ],
    "database": [
        "postgresql", "oracle", "sql", "mysql", "mongodb",
        "database", "rdbms", "nosql",
    ],
    "agile": [
        "agile", "scrum", "kanban", "sprint", "jira",
    ],
}


class JobScorer:
    """
    Scores job listings against a candidate profile.

    Uses TF-IDF-inspired keyword matching without any LLM calls.
    All scoring is deterministic and fast.
    """

    def __init__(self, candidate_profile: dict) -> None:
        """
        Initialise the scorer with the candidate's profile.

        Args:
            candidate_profile: Parsed candidate_profile.yaml content.
        """
        self.profile = candidate_profile
        self.candidate_skills = self._collect_candidate_skills()
        self.salary_min = self._get_salary_min()
        self.location_prefs = self._get_location_prefs()
        self.years_of_experience = float(
            self.profile.get("candidate", {}).get(
                "work_experience", [{}]
            )[0].get("years", 3)
            if self.profile.get("candidate", {}).get("work_experience")
            else 3
        )

        # Build candidate's primary and secondary target keywords
        self.primary_keywords = set(
            kw.lower()
            for kw in self.profile.get("primary_job_targets", [])
        )
        self.secondary_keywords = set(
            kw.lower()
            for kw in self.profile.get("secondary_job_targets", [])
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, job: dict) -> dict:
        """
        Score a job against the candidate profile.

        Args:
            job: Job dict (conforms to BaseJobScraper.RESULT_SCHEMA).

        Returns:
            Job dict with added scoring fields:
              - relevance_score (int 0-100)
              - score_breakdown (dict)
              - matched_skills (list[str])
              - missing_skills (list[str])
              - match_explanation (str)
        """
        description = (job.get("description") or "").lower()
        title = (job.get("title") or "").lower()
        full_text = f"{title} {description}"

        requirements = self.extract_requirements(full_text)

        # Score each dimension
        skills_pts, matched, missing = self._score_skills(
            requirements, full_text
        )
        exp_pts = self._score_experience(requirements)
        resp_pts = self._score_responsibilities(full_text)
        loc_pts = self._score_location(job)
        sal_pts = self._score_salary(job)
        sponsor_pts = self._score_sponsorship(full_text)

        breakdown = {
            "skills_match": round(skills_pts),
            "experience_match": round(exp_pts),
            "responsibilities_match": round(resp_pts),
            "location_match": round(loc_pts),
            "salary_match": round(sal_pts),
            "sponsorship_bonus": round(sponsor_pts),
        }

        total = sum(breakdown.values())
        total = min(100, max(0, total))

        explanation = self.generate_explanation(job, matched, missing, total)

        scored_job = dict(job)
        scored_job["relevance_score"] = total
        scored_job["score_breakdown"] = breakdown
        scored_job["matched_skills"] = matched
        scored_job["missing_skills"] = missing
        scored_job["match_explanation"] = explanation

        return scored_job

    def extract_requirements(self, text: str) -> dict:
        """
        Extract structured requirements from job description text.

        Args:
            text: Lowercased job description text.

        Returns:
            Dict with keys: skills (list), years_required (int|None),
            seniority (str|None), keywords (list[str]).
        """
        # Extract skills mentioned in the text
        mentioned_skills: list[str] = []
        for skill in self._all_known_skills():
            pattern = r"\b" + re.escape(skill.lower()) + r"\b"
            if re.search(pattern, text):
                mentioned_skills.append(skill)

        # Extract years of experience requirement
        years_required = _extract_years_required(text)

        # Detect seniority level
        seniority = _detect_seniority(text)

        # Tokenise text for keyword matching
        tokens = re.findall(r"[a-z][a-z0-9#+.\-]{1,}", text)
        # Remove common stop words
        stop_words = {
            "the", "and", "for", "are", "with", "you", "our", "will",
            "have", "this", "that", "not", "from", "your", "they",
            "what", "work", "team", "role", "based", "able", "must",
            "join", "about", "across", "within", "etc", "such", "including",
        }
        keywords = [t for t in tokens if t not in stop_words and len(t) > 2]

        return {
            "skills": mentioned_skills,
            "years_required": years_required,
            "seniority": seniority,
            "keywords": keywords,
        }

    def generate_explanation(
        self,
        job: dict,
        matched: list[str],
        missing: list[str],
        score: int,
    ) -> str:
        """
        Generate a human-readable match explanation.

        Args:
            job: The scored job dict.
            matched: Skills found in both profile and job description.
            missing: Skills in job description but not in profile.
            score: Final relevance score.

        Returns:
            Multi-sentence explanation string.
        """
        title = job.get("title", "Role")
        employer = job.get("employer", "Employer")

        parts = [f"Score: {score}/100 for {title} at {employer}."]

        if matched:
            top_matched = matched[:8]
            parts.append(
                f"Matched skills: {', '.join(top_matched)}"
                + (f" (+{len(matched)-8} more)" if len(matched) > 8 else "")
                + "."
            )

        if missing:
            top_missing = missing[:5]
            parts.append(
                f"Skills to highlight or develop: {', '.join(top_missing)}"
                + (f" (+{len(missing)-5} more)" if len(missing) > 5 else "")
                + "."
            )

        sponsor_status = job.get("sponsorship_status", "")
        if sponsor_status == "confirmed_role":
            parts.append("Sponsorship confirmed in job listing.")
        elif sponsor_status == "licensed_employer_unconfirmed":
            parts.append("Employer is a licensed sponsor; confirm in application.")

        if score >= 80:
            parts.append("Excellent match — strongly recommended to apply.")
        elif score >= 60:
            parts.append("Good match — worth applying.")
        elif score >= 40:
            parts.append("Partial match — tailor your CV to highlight relevant skills.")
        else:
            parts.append("Weak match — consider whether this role suits your profile.")

        return " ".join(parts)

    # ------------------------------------------------------------------
    # Private scoring methods
    # ------------------------------------------------------------------

    def _score_skills(
        self, requirements: dict, full_text: str
    ) -> tuple[float, list[str], list[str]]:
        """
        Score skills match (max 35 points).

        Returns:
            (points_earned, matched_skills, missing_skills)
        """
        required_skills = requirements.get("skills", [])
        if not required_skills:
            # No specific skills detected; use keyword overlap
            return SCORE_WEIGHTS["skills_match"] * 0.5, [], []

        required_lower = {s.lower() for s in required_skills}
        candidate_lower = {s.lower() for s in self.candidate_skills}

        matched = [s for s in required_lower if s in candidate_lower]
        missing = [s for s in required_lower if s not in candidate_lower]

        if not required_lower:
            ratio = 0.5
        else:
            ratio = len(matched) / len(required_lower)

        # TF-IDF-inspired: weight by how many times the skill appears in text
        weighted_ratio = 0.0
        total_tf_weight = 0.0
        for skill in required_lower:
            tf = full_text.count(skill)
            idf = 1.0  # Simplified; all skills treated equally
            weight = (1 + math.log(tf + 1)) * idf
            total_tf_weight += weight
            if skill in candidate_lower:
                weighted_ratio += weight

        if total_tf_weight > 0:
            tfidf_ratio = weighted_ratio / total_tf_weight
        else:
            tfidf_ratio = ratio

        # Blend simple ratio with TF-IDF ratio
        final_ratio = (ratio * 0.4) + (tfidf_ratio * 0.6)

        points = SCORE_WEIGHTS["skills_match"] * final_ratio

        return (
            points,
            sorted(set(matched)),  # type: ignore[arg-type]
            sorted(set(missing)),  # type: ignore[arg-type]
        )

    def _score_experience(self, requirements: dict) -> float:
        """Score experience match (max 25 points)."""
        years_required = requirements.get("years_required")
        seniority = requirements.get("seniority")

        if years_required is None and seniority is None:
            # No requirement extractable → neutral score (60%)
            return SCORE_WEIGHTS["experience_match"] * 0.6

        # Determine effective years required
        effective_required: float = 0.0
        if years_required is not None:
            effective_required = float(years_required)
        elif seniority:
            effective_required = float(SENIORITY_YEARS.get(seniority, 3))

        candidate_years = self.years_of_experience

        if effective_required == 0:
            ratio = 1.0
        elif candidate_years >= effective_required:
            # Over-qualified by at most 2x → full marks; 3x+ slight penalty
            overshoot = candidate_years / effective_required
            ratio = 1.0 if overshoot <= 2.0 else max(0.7, 1.0 - (overshoot - 2.0) * 0.1)
        else:
            # Under-qualified
            ratio = candidate_years / effective_required

        return SCORE_WEIGHTS["experience_match"] * ratio

    def _score_responsibilities(self, full_text: str) -> float:
        """Score responsibilities / functional area match (max 15 points)."""
        total_areas = len(FUNCTIONAL_COMPETENCIES)
        matched_areas = 0

        for _competency, keywords in FUNCTIONAL_COMPETENCIES.items():
            for kw in keywords:
                if kw in full_text:
                    matched_areas += 1
                    break

        if total_areas == 0:
            return 0.0

        ratio = matched_areas / total_areas
        return SCORE_WEIGHTS["responsibilities_match"] * ratio

    def _score_location(self, job: dict) -> float:
        """Score location match (max 10 points)."""
        location = (job.get("location") or "").lower()
        description = (job.get("description") or "").lower()
        combined = f"{location} {description}"

        # Remote / hybrid → full marks
        if re.search(r"\b(remote|hybrid|flexible)\b", combined):
            return SCORE_WEIGHTS["location_match"] * 1.0

        # Preferred UK cities
        pref_cities = [c.lower() for c in self.location_prefs.get("primary", [])]
        for city in pref_cities:
            if city in combined:
                return SCORE_WEIGHTS["location_match"] * 1.0

        # Anywhere in UK
        if re.search(r"\b(united kingdom|uk|england|britain)\b", combined):
            return SCORE_WEIGHTS["location_match"] * 0.8

        if location:
            return SCORE_WEIGHTS["location_match"] * 0.5

        return SCORE_WEIGHTS["location_match"] * 0.5

    def _score_salary(self, job: dict) -> float:
        """Score salary match (max 10 points)."""
        sal_min = job.get("salary_min")
        sal_max = job.get("salary_max")

        if not sal_min and not sal_max:
            # No salary info → neutral (50%)
            return SCORE_WEIGHTS["salary_match"] * 0.5

        effective_min = sal_min or sal_max
        effective_max = sal_max or sal_min

        candidate_min = self.salary_min

        if not candidate_min:
            return SCORE_WEIGHTS["salary_match"] * 0.5

        if effective_max is not None and effective_max >= candidate_min:
            return SCORE_WEIGHTS["salary_match"] * 1.0
        elif effective_min is not None and effective_min >= candidate_min * 0.9:
            return SCORE_WEIGHTS["salary_match"] * 0.8
        elif effective_min is not None and effective_min >= candidate_min * 0.7:
            return SCORE_WEIGHTS["salary_match"] * 0.5
        else:
            return SCORE_WEIGHTS["salary_match"] * 0.2

    def _score_sponsorship(self, full_text: str) -> float:
        """Score sponsorship bonus (max 5 points)."""
        positive = [
            "visa sponsorship", "skilled worker", "tier 2", "we sponsor",
            "sponsorship available", "certificate of sponsorship",
            "relocation support", "sponsorship provided",
        ]
        negative = [
            "no sponsorship", "right to work", "no visa",
            "must have right to work", "cannot sponsor",
        ]

        for kw in negative:
            if kw in full_text:
                return 0.0

        for kw in positive:
            if kw in full_text:
                return float(SCORE_WEIGHTS["sponsorship_bonus"])

        return 0.0

    # ------------------------------------------------------------------
    # Profile helpers
    # ------------------------------------------------------------------

    def _collect_candidate_skills(self) -> list[str]:
        """Flatten all skills from the candidate profile into one list."""
        candidate = self.profile.get("candidate", {})
        skills_section = candidate.get("skills", {})
        all_skills: list[str] = []

        for _category, skill_list in skills_section.items():
            if isinstance(skill_list, list):
                all_skills.extend(skill_list)
            elif isinstance(skill_list, dict):
                for _sub, sub_list in skill_list.items():
                    if isinstance(sub_list, list):
                        all_skills.extend(sub_list)

        # Also include certifications
        for cert in candidate.get("certifications", []):
            if isinstance(cert, dict):
                all_skills.append(cert.get("name", ""))
            else:
                all_skills.append(str(cert))

        return [s for s in all_skills if s]

    def _all_known_skills(self) -> list[str]:
        """
        Return all skills the scorer knows about (profile + extended list).
        Used to detect skills mentioned in job descriptions.
        """
        extended = [
            # Java ecosystem
            "Java", "Spring Boot", "Spring MVC", "Spring Framework",
            "Spring Security", "Spring Data", "Hibernate", "JPA", "Maven",
            "Gradle", "JUnit", "Mockito", "Kafka", "RabbitMQ", "ActiveMQ",
            "Quarkus", "Micronaut", "JVM", "Jakarta EE", "Java EE",
            # Python
            "Python", "Django", "FastAPI", "Flask", "pandas", "NumPy",
            # JavaScript/TypeScript
            "JavaScript", "TypeScript", "Node.js", "React", "Angular",
            "Vue.js", "Express", "NestJS",
            # Mobile
            "Kotlin", "Jetpack Compose", "Android", "Swift", "iOS",
            # Databases
            "PostgreSQL", "MySQL", "Oracle", "SQL Server", "MongoDB",
            "Redis", "Elasticsearch", "Cassandra", "DynamoDB",
            # DevOps / Cloud
            "Docker", "Kubernetes", "Helm", "Terraform", "Ansible",
            "Jenkins", "GitHub Actions", "GitLab CI", "CircleCI",
            "AWS", "Azure", "GCP", "Google Cloud", "Lambda", "ECS", "EKS",
            "Azure DevOps", "Bicep",
            # Observability
            "Grafana", "Prometheus", "Datadog", "New Relic", "Splunk",
            "OpenTelemetry",
            # Protocols & Patterns
            "REST", "GraphQL", "gRPC", "SOAP", "OAuth", "JWT",
            "Microservices", "Event-Driven", "CQRS", "DDD",
            # Tools
            "Git", "Jira", "Confluence", "Bitbucket", "GitHub",
            # Data / AI
            "Spark", "Hadoop", "Databricks", "Snowflake",
            "Machine Learning", "TensorFlow", "PyTorch",
            # Other languages
            "Go", "Rust", "Scala", "C#", ".NET", "C++", "Ruby",
        ]

        combined = list(self.candidate_skills) + extended
        # Deduplicate (case-insensitive)
        seen: set[str] = set()
        unique: list[str] = []
        for s in combined:
            if s.lower() not in seen:
                seen.add(s.lower())
                unique.append(s)
        return unique

    def _get_salary_min(self) -> float | None:
        """Extract candidate's minimum salary expectation from profile."""
        val = self.profile.get("salary_expectation_gbp_min")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        return 40000.0  # Default

    def _get_location_prefs(self) -> dict:
        """Extract location preferences from profile."""
        return self.profile.get("location_preferences", {
            "primary": ["Sheffield", "London", "Manchester"],
            "acceptable": ["Remote (UK)", "Hybrid (UK)", "United Kingdom"],
        })


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _extract_years_required(text: str) -> int | None:
    """
    Extract years-of-experience requirement from job description text.

    Args:
        text: Lowercased job description text.

    Returns:
        Integer years required, or None if not found.
    """
    # Patterns like "3+ years", "at least 5 years", "minimum 2 years"
    patterns = [
        r"(\d+)\+?\s*years?(?:\s+of)?\s+(?:professional\s+)?experience",
        r"(\d+)\+?\s*years?(?:\s+of)?\s+(?:commercial|industry|relevant)\s+experience",
        r"minimum\s+(\d+)\s+years?",
        r"at\s+least\s+(\d+)\s+years?",
        r"(\d+)\s+to\s+(\d+)\s+years?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (IndexError, ValueError):
                pass

    return None


def _detect_seniority(text: str) -> str | None:
    """
    Detect the seniority level from job description text.

    Args:
        text: Lowercased job description text.

    Returns:
        Seniority string or None.
    """
    for level in [
        "junior", "entry", "graduate", "associate",
        "senior", "lead", "principal", "staff", "architect",
        "manager", "head", "director", "mid",
    ]:
        if re.search(r"\b" + level + r"\b", text):
            return level
    return None
