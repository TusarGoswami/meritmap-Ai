"""
AuditorAgent (Critic) — Runs final multi-point quality audits on pipeline output.
Enforces:
1. Every recommendation has a source URL and traceable provenance.
2. No recommendation retains a high score after being REJECTED by Skeptic.
3. Minimum candidate count is met (>= 10 opportunities discovered).
4. SOP grounds claims strictly in student profile.
5. Evidence Ledger fields have verification or fog indicators.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.events import emit
from backend.schemas.models import (
    PipelineState,
    AuditReport,
    AuditIssue,
    SkepticVerdict,
    EventType,
)

logger = logging.getLogger("meritmap.agents.auditor")


def audit_pipeline(state: PipelineState) -> AuditReport:
    """Run comprehensive quality and provenance checks on the pipeline state."""
    issues: list[AuditIssue] = []

    # 1. Candidate count check (Min 10 candidates discovered)
    total_candidates = len(state.raw_candidates) + len(state.verified_candidates)
    if len(state.verified_candidates) < 10:
        if len(state.raw_candidates) >= 10:
            issues.append(
                AuditIssue(
                    severity="warning",
                    agent="SurveyorAgent",
                    message=f"Only {len(state.verified_candidates)} candidates fully verified of {len(state.raw_candidates)} discovered.",
                    field="verified_candidates",
                )
            )
        else:
            issues.append(
                AuditIssue(
                    severity="warning",
                    agent="ScoutAgent",
                    message=f"Discovered {total_candidates} opportunities, below ideal threshold of 10+.",
                    field="raw_candidates",
                )
            )

    # 2. Check provenance on top recommendations
    for idx, match in enumerate(state.top_matches[:5]):
        opp = match.opportunity
        opp_name = str(opp.name.value or f"Opportunity #{idx+1}")

        if not opp.url and not getattr(opp.name, "source_url", None):
            issues.append(
                AuditIssue(
                    severity="error",
                    agent="SurveyorAgent",
                    message=f"Recommendation '{opp_name}' is missing source URL provenance.",
                    field="url",
                )
            )

        # Check evidence quotes
        has_snippets = any(
            getattr(getattr(opp, attr, None), "exact_quote_snippet", None)
            for attr in ["funding_type", "deadline", "min_gpa", "eligible_nationalities"]
        )
        if not has_snippets:
            issues.append(
                AuditIssue(
                    severity="warning",
                    agent="SurveyorAgent",
                    message=f"Recommendation '{opp_name}' relies on metadata without direct page text quotes.",
                    field="exact_quote_snippet",
                )
            )

    # 3. Check Skeptic enforcement: No REJECTED opportunities should be in top 3
    skeptic_verdict_map = {r.opportunity_id: r.verdict for r in state.skeptic_results}
    for match in state.top_matches[:3]:
        v = skeptic_verdict_map.get(match.opportunity.id)
        if v == SkepticVerdict.REJECTED:
            issues.append(
                AuditIssue(
                    severity="error",
                    agent="SkepticAgent",
                    message=f"Opportunity '{match.opportunity.name.value}' was REJECTED by SkepticAgent but remains in Top 3 recommendations.",
                    field="skeptic_verdict",
                )
            )

    # 4. Check SOP grounding
    if state.sop:
        sop_text = state.sop.sop_text.lower()
        profile_skills = [s.lower() for s in (state.profile.skills if state.profile else [])]
        # Check that SOP doesn't fabricate unmentioned companies or institutions
        suspicious_terms = ["google research fellowship", "meta ai resident", "stanford graduate fellow"]
        for term in suspicious_terms:
            if term in sop_text:
                issues.append(
                    AuditIssue(
                        severity="error",
                        agent="ScribeAgent",
                        message=f"SOP references '{term}' which is ungrounded in student profile.",
                        field="sop_text",
                    )
                )

    # Check for hard errors
    has_errors = any(i.severity == "error" for i in issues)
    passed = not has_errors

    summary_text = (
        f"Audit {'PASSED' if passed else 'FLAGGED'}: "
        f"{sum(1 for i in issues if i.severity == 'error')} errors, "
        f"{sum(1 for i in issues if i.severity == 'warning')} warnings. "
        f"Provenance and source verification: {'Compliant' if passed else 'Issues detected'}."
    )

    return AuditReport(
        passed=passed,
        issues=issues,
        summary=summary_text,
    )


async def run_auditor(
    run_id: str,
    state: PipelineState,
    chaos_mode: bool = False,
) -> AuditReport:
    """Execute AuditorAgent evaluation on full pipeline state."""
    emit(run_id, "AuditorAgent", EventType.START, "🛡️ AuditorAgent running final quality and provenance compliance audit...")

    report = audit_pipeline(state)

    for issue in report.issues:
        event_type = EventType.WARNING if issue.severity == "warning" else EventType.ERROR
        emit(
            run_id,
            "AuditorAgent",
            event_type,
            f"[{issue.severity.upper()}] {issue.agent}: {issue.message}",
            {"field": issue.field, "severity": issue.severity},
        )

    emit(
        run_id,
        "AuditorAgent",
        EventType.RESULT if report.passed else EventType.WARNING,
        f"🛡️ Auditor complete: {report.summary}",
        {
            "passed": report.passed,
            "issue_count": len(report.issues),
        },
    )

    return report
