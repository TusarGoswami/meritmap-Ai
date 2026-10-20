"""
Unit tests for MeritMap Pydantic v2 schemas and Evidence Ledger logic.
"""
import pytest
from datetime import datetime
from backend.schemas.models import (
    EvidenceField,
    StudentProfile,
    Opportunity,
    OpportunityType,
    TrueCost,
    FitBreakdown,
    MatchResult,
    SkepticResult,
    SkepticVerdict,
    Roadmap,
    RoadmapTask,
    AuditReport,
    AuditIssue,
)


def test_evidence_field_fog():
    # Unverified field should register as fog
    f_unverified = EvidenceField(value="Nov 15, 2026", verified=False)
    assert f_unverified.is_fog() is True

    # Verified field with provenance should not be fog
    f_verified = EvidenceField(
        value="Dec 1, 2026",
        source_url="https://daad.de/scholarships",
        exact_quote_snippet="Deadline for winter intake is December 1",
        verified=True,
    )
    assert f_verified.is_fog() is False
    assert f_verified.confidence == 0.5


def test_student_profile_model():
    profile = StudentProfile(
        name="Rahul Sharma",
        degree="B.Tech",
        field_of_study="Computer Science",
        cgpa=8.2,
        cgpa_scale=10.0,
        cgpa_normalized_4=3.28,
        cgpa_normalized_10=8.2,
        skills=["Python", "PyTorch", "Docker"],
        target_countries=["Germany", "Canada"],
        needs_full_funding=True,
    )
    assert profile.name == "Rahul Sharma"
    assert profile.cgpa_normalized_4 == 3.28
    assert profile.needs_full_funding is True
    assert "PyTorch" in profile.skills


def test_true_cost_model():
    cost = TrueCost(
        tuition_yearly_local=0.0,
        living_cost_yearly_local=11208.0,
        currency="EUR",
        out_of_pocket_yearly_local=0.0,
        out_of_pocket_yearly_inr=0.0,
        exchange_rate_used=93.0,
        is_fully_funded=True,
    )
    assert cost.is_fully_funded is True
    assert cost.out_of_pocket_yearly_inr == 0.0


def test_skeptic_result_model():
    res = SkepticResult(
        opportunity_id="opp-101",
        verdict=SkepticVerdict.REJECTED,
        objections=["Deadline passed in 2023"],
        evidence=["Snippet indicates 2023 cycle"],
    )
    assert res.verdict == SkepticVerdict.REJECTED
    assert len(res.objections) == 1
