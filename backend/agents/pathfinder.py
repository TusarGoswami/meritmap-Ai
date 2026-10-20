"""
PathfinderAgent — Builds a gap-closing roadmap with dated milestones,
provides fast What-If scenario re-scoring, and generates .ics calendar exports.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import copy
from typing import Optional

from backend.events import emit
from backend.schemas.models import (
    StudentProfile,
    MatchResult,
    Roadmap,
    RoadmapTask,
    WhatIfParams,
    EventType,
)
from backend.agents.beacon import compute_fit_breakdown, calculate_true_cost

logger = logging.getLogger("meritmap.agents.pathfinder")


def build_roadmap(profile: StudentProfile, top_matches: list[MatchResult]) -> Roadmap:
    """
    Construct an actionable, chronologically sequenced timeline of tasks
    to close eligibility gaps and meet application deadlines.
    """
    tasks: list[RoadmapTask] = []
    today = datetime.now()

    # 1. Check for Language Test gaps (IELTS/TOEFL)
    has_english_test = any(t.test_name.lower() in ["ielts", "toefl"] for t in profile.test_scores)
    needs_english = not has_english_test or any(
        "IELTS" in req or "TOEFL" in req
        for m in top_matches[:3]
        for req in (m.missing_requirements + m.eligibility_gaps)
    )

    if needs_english:
        due = (today + timedelta(days=45)).strftime("%Y-%m-%d")
        tasks.append(
            RoadmapTask(
                title="Register and Complete IELTS Academic / TOEFL iBT",
                description="Target minimum 7.0 overall band (no sub-score below 6.5) or TOEFL 95+. Score reports take ~10-14 days to dispatch to universities.",
                due_date=due,
                category="test",
                priority="high",
            )
        )

    # 2. Academic Transcripts and Official Marksheets
    due = (today + timedelta(days=20)).strftime("%Y-%m-%d")
    tasks.append(
        RoadmapTask(
            title="Request Official Sealed Transcripts from University Registrar",
            description="Obtain 3-5 signed & stamped official physical transcripts plus certified digital copies. Several universities require certified English translations.",
            due_date=due,
            category="document",
            priority="high",
        )
    )

    # 3. Letters of Recommendation (LoRs)
    due = (today + timedelta(days=35)).strftime("%Y-%m-%d")
    tasks.append(
        RoadmapTask(
            title="Secure 2 Academic & 1 Professional Recommendation Letters",
            description="Contact professors and project supervisors. Provide them with your updated resume, course grades, and draft SOP to ensure compelling, specific endorsements.",
            due_date=due,
            category="document",
            priority="high",
        )
    )

    # 4. Financial Documentation / Bank Solvency Proof
    due = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    tasks.append(
        RoadmapTask(
            title="Compile Proof of Financial Solvency & Scholarship Endorsement Forms",
            description="Prepare bank solvency certificates, income tax returns, and any required sponsor affidavits for visa & departmental requirements.",
            due_date=due,
            category="financial",
            priority="medium",
        )
    )

    # 5. Specific Top Opportunity Deadlines
    for idx, match in enumerate(top_matches[:4]):
        opp = match.opportunity
        opp_name = str(opp.name.value or f"Opportunity #{idx+1}")
        deadline_str = str(opp.deadline.value or "")

        # Try to parse or set reasonable target date
        target_due = (today + timedelta(days=90 + idx * 25)).strftime("%Y-%m-%d")
        if "dec" in deadline_str.lower():
            target_due = f"{today.year}-12-15"
        elif "jan" in deadline_str.lower():
            target_due = f"{today.year + 1}-01-15"
        elif "feb" in deadline_str.lower():
            target_due = f"{today.year + 1}-02-15"
        elif "mar" in deadline_str.lower():
            target_due = f"{today.year + 1}-03-01"

        tasks.append(
            RoadmapTask(
                title=f"Final Submission: {opp_name}",
                description=f"Submit online application dossier for {opp.institution.value or opp.country}. Ensure SOP, LoRs, and proof of GPA are verified. Deadline reported: {deadline_str or 'Regular cycle'}.",
                due_date=target_due,
                category="application",
                related_opportunity=opp.id,
                priority="high",
            )
        )

    # Sort tasks by due date
    tasks.sort(key=lambda t: t.due_date or "9999-12-31")

    summary = (
        f"Pathfinder roadmap generated {len(tasks)} chronological milestones across "
        f"testing, documentation, funding certification, and submissions. "
        f"Critical path: complete English proficiency and LoRs within 45 days."
    )

    return Roadmap(tasks=tasks, summary=summary)


def recompute_what_if(
    profile: StudentProfile,
    matches: list[MatchResult],
    params: WhatIfParams,
) -> list[MatchResult]:
    """
    Pure-Python instant re-scoring of cached opportunities based on adjusted parameters.
    No re-scraping or network calls required!
    """
    # Clone profile
    sim_profile = profile.model_copy(deep=True)

    # Apply What-If parameters
    if params.cgpa is not None:
        sim_profile.cgpa = params.cgpa
        sim_profile.cgpa_scale = 10.0 if params.cgpa > 4.5 else 4.0
        if sim_profile.cgpa_scale == 10.0:
            sim_profile.cgpa_normalized_10 = params.cgpa
            sim_profile.cgpa_normalized_4 = round((params.cgpa / 10.0) * 4.0, 2)
        else:
            sim_profile.cgpa_normalized_4 = params.cgpa
            sim_profile.cgpa_normalized_10 = round((params.cgpa / 4.0) * 10.0, 2)

    if params.ielts_score is not None:
        # Update or append IELTS
        sim_profile.test_scores = [t for t in sim_profile.test_scores if t.test_name.upper() != "IELTS"]
        sim_profile.test_scores.append(
            type(profile.test_scores[0])(test_name="IELTS", score=params.ielts_score)
            if profile.test_scores else
            type("TestScore", (), {"test_name": "IELTS", "score": params.ielts_score})
        )

    if params.gre_score is not None:
        sim_profile.test_scores = [t for t in sim_profile.test_scores if t.test_name.upper() != "GRE"]
        sim_profile.test_scores.append(
            type(profile.test_scores[0])(test_name="GRE", score=params.gre_score)
            if profile.test_scores else
            type("TestScore", (), {"test_name": "GRE", "score": params.gre_score})
        )

    if params.add_publication:
        if params.add_publication not in sim_profile.publications:
            sim_profile.publications.append(params.add_publication)

    if params.target_countries:
        sim_profile.target_countries = params.target_countries

    if params.additional_skills:
        for s in params.additional_skills:
            if s not in sim_profile.skills:
                sim_profile.skills.append(s)

    # Re-score all matches
    updated_matches: list[MatchResult] = []
    for m in matches:
        opp = m.opportunity
        tc = calculate_true_cost(opp)
        bd, gaps, missing = compute_fit_breakdown(sim_profile, opp, tc)

        new_match = MatchResult(
            opportunity=opp,
            fit_score=bd.total,
            fit_breakdown=bd,
            true_cost=tc,
            why_it_fits=m.why_it_fits,
            missing_requirements=missing,
            eligibility_gaps=gaps,
        )
        updated_matches.append(new_match)

    updated_matches.sort(key=lambda m: m.fit_score, reverse=True)
    return updated_matches


def generate_ics_calendar(roadmap: Roadmap, matches: list[MatchResult]) -> str:
    """
    Generate an iCalendar (.ics) string for importing all deadlines and roadmap milestones.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MeritMap//AI Scholarship Finder//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:MeritMap Scholarship Deadlines & Roadmap",
        "X-WR-TIMEZONE:UTC",
    ]

    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Add roadmap tasks as calendar events
    for idx, task in enumerate(roadmap.tasks):
        due = task.due_date
        if not due:
            continue
        try:
            # Format YYYYMMDD
            clean_date = due.replace("-", "")
            # End date = next day for all-day event
            dt = datetime.strptime(due, "%Y-%m-%d")
            next_day = (dt + timedelta(days=1)).strftime("%Y%m%d")
        except Exception:
            continue

        uid = f"task-{idx}-{clean_date}@meritmap.ai"
        summary = f"[MeritMap] {task.title}"
        desc = f"{task.description}\\nPriority: {task.priority.upper()}\\nCategory: {task.category.capitalize()}"

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{clean_date}",
            f"DTEND;VALUE=DATE:{next_day}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc}",
            f"STATUS:CONFIRMED",
            f"PRIORITY:{'1' if task.priority == 'high' else '5'}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


async def run_pathfinder(
    run_id: str,
    profile: StudentProfile,
    top_matches: list[MatchResult],
    chaos_mode: bool = False,
) -> Roadmap:
    """Execute PathfinderAgent workflow."""
    emit(run_id, "PathfinderAgent", EventType.START, "🧭 PathfinderAgent charting application roadmap and deadlines...")

    roadmap = build_roadmap(profile, top_matches)

    emit(
        run_id,
        "PathfinderAgent",
        EventType.RESULT,
        f"🧭 Roadmap ready: {len(roadmap.tasks)} milestones scheduled with calendar export enabled.",
        {
            "task_count": len(roadmap.tasks),
            "critical_milestones": [t.title for t in roadmap.tasks if t.priority == "high"],
        },
    )

    return roadmap


async def whatif_rescore(state: PipelineState, params: WhatIfParams) -> list[MatchResult]:
    """Re-score cached opportunities in state using What-If parameters."""
    if not state.profile or not state.top_matches:
        return []
    return recompute_what_if(state.profile, state.top_matches, params)


def export_ics(roadmap: Roadmap) -> str:
    """Export roadmap as an iCalendar (.ics) string."""
    return generate_ics_calendar(roadmap, [])
