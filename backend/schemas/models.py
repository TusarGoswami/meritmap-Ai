"""
MeritMap Schemas — Pydantic v2 models for the entire pipeline.
Every extracted fact uses the Evidence Ledger pattern: value + source + confidence.
"""
from __future__ import annotations

from datetime import datetime, date
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ─── Evidence Ledger ─────────────────────────────────────────────────────

class EvidenceField(BaseModel):
    """A single piece of evidence with provenance tracking."""
    value: Any = None
    source_url: Optional[str] = None
    exact_quote_snippet: Optional[str] = None
    fetched_at: Optional[datetime] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verified: bool = False

    def is_fog(self) -> bool:
        """Returns True if this fact is unverified ('fog' status)."""
        return not self.verified


# ─── Student Profile ──────────────────────────────────────────────────────

class TestScore(BaseModel):
    __test__ = False  # Avoid pytest trying to collect this model as a test suite
    test_name: str  # e.g. "IELTS", "GRE", "TOEFL"
    score: Optional[float] = None
    subsections: Optional[dict[str, float]] = None
    date_taken: Optional[str] = None


class Project(BaseModel):
    name: str
    description: Optional[str] = None
    technologies: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    stars: Optional[int] = None


class StudentProfile(BaseModel):
    """Comprehensive student profile extracted by CartographerAgent."""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    nationality: Optional[str] = None
    current_country: Optional[str] = None

    # Academic
    degree: Optional[str] = None  # e.g. "B.Tech", "M.Sc"
    field_of_study: Optional[str] = None  # e.g. "Computer Science"
    university: Optional[str] = None
    graduation_year: Optional[int] = None
    cgpa: Optional[float] = None
    cgpa_scale: Optional[float] = None  # e.g. 10.0 or 4.0
    cgpa_normalized_4: Optional[float] = None  # Normalized to 4.0 scale
    cgpa_normalized_10: Optional[float] = None  # Normalized to 10.0 scale
    percentage: Optional[float] = None

    # Skills & Experience
    skills: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    work_experience: list[str] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)

    # Test scores
    test_scores: list[TestScore] = Field(default_factory=list)

    # Preferences (from form or inferred)
    target_degree_level: Optional[str] = None  # "Masters", "PhD", etc.
    target_countries: list[str] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    budget_max_yearly_usd: Optional[float] = None
    needs_full_funding: bool = False

    # GitHub enrichment
    github_username: Optional[str] = None
    github_repos: list[Project] = Field(default_factory=list)
    github_languages: list[str] = Field(default_factory=list)
    github_total_stars: int = 0

    # Profile URLs
    extra_urls: list[str] = Field(default_factory=list)

    # Quality
    missing_fields: list[str] = Field(default_factory=list)
    extraction_confidence: float = 0.5
    source_documents: list[str] = Field(default_factory=list)


# ─── Opportunity / Scholarship ────────────────────────────────────────────

class OpportunityType(str, Enum):
    SCHOLARSHIP = "scholarship"
    FELLOWSHIP = "fellowship"
    GRANT = "grant"
    ASSISTANTSHIP = "assistantship"
    PROGRAM = "program"
    OTHER = "other"


class Opportunity(BaseModel):
    """A discovered scholarship/program opportunity with evidence ledger fields."""
    id: str = ""
    name: EvidenceField = Field(default_factory=EvidenceField)
    institution: EvidenceField = Field(default_factory=EvidenceField)
    country: Optional[str] = None
    city: Optional[str] = None
    url: Optional[str] = None
    opportunity_type: OpportunityType = OpportunityType.SCHOLARSHIP

    # Eligibility
    eligible_nationalities: EvidenceField = Field(default_factory=EvidenceField)
    eligible_degrees: EvidenceField = Field(default_factory=EvidenceField)
    eligible_fields: EvidenceField = Field(default_factory=EvidenceField)
    min_gpa: EvidenceField = Field(default_factory=EvidenceField)
    required_tests: EvidenceField = Field(default_factory=EvidenceField)
    other_requirements: EvidenceField = Field(default_factory=EvidenceField)

    # Funding
    funding_type: EvidenceField = Field(default_factory=EvidenceField)  # full, partial, tuition-only
    tuition_covered: EvidenceField = Field(default_factory=EvidenceField)
    stipend: EvidenceField = Field(default_factory=EvidenceField)
    other_benefits: EvidenceField = Field(default_factory=EvidenceField)

    # Cost
    tuition_yearly: EvidenceField = Field(default_factory=EvidenceField)
    living_cost_yearly: EvidenceField = Field(default_factory=EvidenceField)
    currency: str = "USD"

    # Dates
    deadline: EvidenceField = Field(default_factory=EvidenceField)
    program_start: EvidenceField = Field(default_factory=EvidenceField)

    # Status
    application_link: EvidenceField = Field(default_factory=EvidenceField)
    how_to_apply: EvidenceField = Field(default_factory=EvidenceField)

    # Discovery
    discovered_via: Optional[str] = None  # "tavily", "duckduckgo", etc.


# ─── True Cost ────────────────────────────────────────────────────────────

class TrueCost(BaseModel):
    """True out-of-pocket cost calculation for an opportunity."""
    tuition_yearly_local: Optional[float] = None
    living_cost_yearly_local: Optional[float] = None
    funding_coverage_local: Optional[float] = None
    currency: str = "USD"

    out_of_pocket_yearly_local: Optional[float] = None
    out_of_pocket_yearly_inr: Optional[float] = None
    exchange_rate_used: Optional[float] = None
    is_fully_funded: bool = False
    cost_confidence: str = "estimate"  # "official", "estimate", "unknown"
    notes: list[str] = Field(default_factory=list)


# ─── Matching ──────────────────────────────────────────────────────────────

class FitBreakdown(BaseModel):
    """Transparent scoring breakdown for a match."""
    academic_fit: float = 0  # 0-25
    field_fit: float = 0  # 0-20
    funding_fit: float = 0  # 0-20
    eligibility_fit: float = 0  # 0-15
    deadline_feasibility: float = 0  # 0-10
    affordability: float = 0  # 0-10
    total: float = 0  # 0-100
    reasoning: str = ""


class MatchResult(BaseModel):
    """A scored match between student and opportunity."""
    opportunity: Opportunity
    fit_score: float = 0
    fit_breakdown: FitBreakdown = Field(default_factory=FitBreakdown)
    true_cost: TrueCost = Field(default_factory=TrueCost)
    why_it_fits: str = ""
    missing_requirements: list[str] = Field(default_factory=list)
    eligibility_gaps: list[str] = Field(default_factory=list)


# ─── Skeptic ──────────────────────────────────────────────────────────────

class SkepticVerdict(str, Enum):
    SURVIVES = "survives"
    WEAKENED = "weakened"
    REJECTED = "rejected"


class SkepticResult(BaseModel):
    """Result of adversarial verification by SkepticAgent."""
    opportunity_id: str
    verdict: SkepticVerdict = SkepticVerdict.SURVIVES
    objections: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ─── Pathfinder ────────────────────────────────────────────────────────────

class RoadmapTask(BaseModel):
    """A single task in the gap-closing roadmap."""
    title: str
    description: str = ""
    due_date: Optional[str] = None  # ISO date string
    category: str = "general"  # "test", "document", "application", "financial"
    related_opportunity: Optional[str] = None
    priority: str = "medium"  # "high", "medium", "low"


class Roadmap(BaseModel):
    """Complete roadmap from PathfinderAgent."""
    tasks: list[RoadmapTask] = Field(default_factory=list)
    summary: str = ""


# ─── What-If ──────────────────────────────────────────────────────────────

class WhatIfParams(BaseModel):
    """Parameters for What-If re-scoring."""
    ielts_score: Optional[float] = None
    gre_score: Optional[float] = None
    cgpa: Optional[float] = None
    add_publication: Optional[str] = None
    target_countries: Optional[list[str]] = None
    additional_skills: Optional[list[str]] = None


# ─── Scribe (SOP) ─────────────────────────────────────────────────────────

class SOPDraft(BaseModel):
    """Statement of Purpose draft from ScribeAgent."""
    target_opportunity: str
    sop_text: str
    document_checklist: list[str] = Field(default_factory=list)
    application_steps: list[str] = Field(default_factory=list)
    placeholders_used: list[str] = Field(default_factory=list)


# ─── Audit ─────────────────────────────────────────────────────────────────

class AuditIssue(BaseModel):
    """An issue found by the AuditorAgent."""
    severity: str  # "error", "warning", "info"
    agent: str
    message: str
    field: Optional[str] = None


class AuditReport(BaseModel):
    """Report from the AuditorAgent."""
    passed: bool = False
    issues: list[AuditIssue] = Field(default_factory=list)
    summary: str = ""


# ─── Source Trust ──────────────────────────────────────────────────────────

class SourceTrust(BaseModel):
    """Trust rating for a recommendation's sources."""
    score: float = Field(default=0.5, ge=0.0, le=1.0)  # 0-1
    is_official_domain: bool = False
    multiple_confirmations: bool = False
    page_recency: Optional[str] = None  # "recent", "stale", "unknown"
    reasons: list[str] = Field(default_factory=list)


# ─── Events ────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    START = "start"
    PROGRESS = "progress"
    TOOL_CALL = "tool_call"
    WARNING = "warning"
    RECOVERED = "recovered"
    DEBATE = "debate"
    RESULT = "result"
    ERROR = "error"
    DONE = "done"
    AUDIT = "audit"


class AgentEvent(BaseModel):
    """Event emitted by an agent during execution."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent: str
    type: EventType
    message: str
    data: Optional[dict[str, Any]] = None


# ─── Pipeline State ───────────────────────────────────────────────────────

class PipelineState(BaseModel):
    """Shared state for the LangGraph pipeline."""
    run_id: str = ""
    profile: Optional[StudentProfile] = None
    search_queries: list[str] = Field(default_factory=list)
    raw_candidates: list[Opportunity] = Field(default_factory=list)
    verified_candidates: list[Opportunity] = Field(default_factory=list)
    match_results: list[MatchResult] = Field(default_factory=list)
    skeptic_results: list[SkepticResult] = Field(default_factory=list)
    top_matches: list[MatchResult] = Field(default_factory=list)
    roadmap: Optional[Roadmap] = None
    sop: Optional[SOPDraft] = None
    audit: Optional[AuditReport] = None
    events: list[AgentEvent] = Field(default_factory=list)
    source_trusts: dict[str, SourceTrust] = Field(default_factory=dict)

    # Control
    chaos_mode: bool = False
    navigator_iterations: int = 0
    skeptic_rounds: int = 0
    errors: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending, running, completed, failed


# ─── API Models ────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    """API request to start a run."""
    github_username: Optional[str] = None
    extra_urls: list[str] = Field(default_factory=list)
    target_countries: list[str] = Field(default_factory=list)
    target_degree_level: Optional[str] = None
    target_fields: list[str] = Field(default_factory=list)
    budget_max_yearly_usd: Optional[float] = None
    needs_full_funding: bool = False
    chaos_mode: bool = False
    # Additional form overrides
    name: Optional[str] = None
    nationality: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    cgpa: Optional[float] = None
    cgpa_scale: Optional[float] = None
    test_scores: list[TestScore] = Field(default_factory=list)


class RunResponse(BaseModel):
    """API response when a run is started."""
    run_id: str
    status: str = "started"
