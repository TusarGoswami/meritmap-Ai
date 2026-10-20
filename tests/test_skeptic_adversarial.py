"""
Tests for SkepticAgent adversarial scrutiny and deal-breaker identification.
"""
import pytest
from backend.schemas.models import (
    StudentProfile,
    Opportunity,
    EvidenceField,
    MatchResult,
    SkepticVerdict,
)
from backend.agents.skeptic import evaluate_adversarially


def test_skeptic_rejects_expired_deadline():
    profile = StudentProfile(
        name="Rahul Sharma",
        nationality="Indian",
        field_of_study="Computer Science",
    )
    opp = Opportunity(
        id="opp-old",
        name=EvidenceField(value="Commonwealth Fellowship 2022"),
        deadline=EvidenceField(value="October 15, 2022", verified=True),
    )
    match = MatchResult(opportunity=opp, fit_score=85.0)

    res = evaluate_adversarially(profile, match)
    assert res.verdict == SkepticVerdict.REJECTED
    assert any("expired" in obj.lower() for obj in res.objections)


def test_skeptic_rejects_nationality_restriction():
    profile = StudentProfile(
        name="Rahul Sharma",
        nationality="Indian",
        field_of_study="Computer Science",
    )
    opp = Opportunity(
        id="opp-eu-only",
        name=EvidenceField(value="EU Horizon Masters Grant"),
        eligible_nationalities=EvidenceField(value="EU/EEA Member States only", verified=True),
        deadline=EvidenceField(value="December 1, 2026", verified=True),
    )
    match = MatchResult(opportunity=opp, fit_score=80.0)

    res = evaluate_adversarially(profile, match)
    assert res.verdict == SkepticVerdict.REJECTED
    assert any("nationality" in obj.lower() for obj in res.objections)


def test_skeptic_survives_valid_program():
    profile = StudentProfile(
        name="Rahul Sharma",
        nationality="Indian",
        field_of_study="Computer Science",
        cgpa=8.5,
        cgpa_scale=10.0,
        cgpa_normalized_10=8.5,
    )
    opp = Opportunity(
        id="opp-daad-valid",
        name=EvidenceField(value="DAAD Master Study Scholarships"),
        eligible_nationalities=EvidenceField(value="International (All nationalities)", verified=True),
        deadline=EvidenceField(value="November 30, 2026", verified=True),
        min_gpa=EvidenceField(value="7.5", verified=True),
    )
    match = MatchResult(opportunity=opp, fit_score=90.0)

    res = evaluate_adversarially(profile, match)
    assert res.verdict == SkepticVerdict.SURVIVES
    assert len(res.objections) == 0
