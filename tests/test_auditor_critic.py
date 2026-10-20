"""
Tests for AuditorAgent provenance and compliance checks.
"""
import pytest
from backend.schemas.models import (
    PipelineState,
    Opportunity,
    EvidenceField,
    MatchResult,
    SkepticResult,
    SkepticVerdict,
    SOPDraft,
)
from backend.agents.auditor import audit_pipeline


def test_auditor_flags_rejected_candidate_in_top_matches():
    opp = Opportunity(id="opp-bad", name=EvidenceField(value="Bad Opp", source_url="https://bad.edu"))
    match = MatchResult(opportunity=opp, fit_score=85.0)

    state = PipelineState(
        run_id="audit-test",
        top_matches=[match],
        skeptic_results=[
            SkepticResult(
                opportunity_id="opp-bad",
                verdict=SkepticVerdict.REJECTED,
                objections=["Fake accreditation"],
            )
        ],
    )

    report = audit_pipeline(state)
    assert report.passed is False
    assert any(i.severity == "error" and i.field == "skeptic_verdict" for i in report.issues)


def test_auditor_flags_missing_source_url():
    # Opportunity without any URL
    opp = Opportunity(id="opp-no-url", name=EvidenceField(value="No URL Opp"))
    match = MatchResult(opportunity=opp, fit_score=75.0)

    state = PipelineState(
        run_id="audit-test-2",
        top_matches=[match],
    )

    report = audit_pipeline(state)
    assert report.passed is False
    assert any(i.severity == "error" and i.field == "url" for i in report.issues)
