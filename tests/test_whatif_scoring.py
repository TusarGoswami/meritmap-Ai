"""
Tests for instant pure-Python What-If scenario re-scoring.
"""
import pytest
from backend.schemas.models import (
    StudentProfile,
    Opportunity,
    EvidenceField,
    MatchResult,
    FitBreakdown,
    TrueCost,
    WhatIfParams,
    TestScore,
)
from backend.agents.pathfinder import recompute_what_if


def test_whatif_rescore_gpa_improvement():
    profile = StudentProfile(
        name="Rahul Sharma",
        field_of_study="Computer Science",
        cgpa=7.5,
        cgpa_scale=10.0,
        cgpa_normalized_10=7.5,
        cgpa_normalized_4=3.0,
        needs_full_funding=True,
    )

    opp = Opportunity(
        id="opp-tum",
        name=EvidenceField(value="TUM Masters in Informatics"),
        country="Germany",
        currency="EUR",
        min_gpa=EvidenceField(value="8.5", verified=True),
        funding_type=EvidenceField(value="Full", verified=True),
    )

    base_match = MatchResult(
        opportunity=opp,
        fit_score=68.0,
        fit_breakdown=FitBreakdown(academic_fit=16.0, total=68.0),
        true_cost=TrueCost(is_fully_funded=True, out_of_pocket_yearly_local=0.0),
    )

    # Simulate What-If with CGPA boosted to 9.2
    params = WhatIfParams(cgpa=9.2)
    updated = recompute_what_if(profile, [base_match], params)

    assert len(updated) == 1
    # Academic fit and overall fit score should strictly increase
    assert updated[0].fit_score > base_match.fit_score
    assert updated[0].fit_breakdown.academic_fit > base_match.fit_breakdown.academic_fit
