"""
SkepticAgent — Adversarial verification of top candidate matches.
Actively hunts for deal-breakers: nationality restrictions, passed deadlines,
degree mismatches, GPA shortfalls, mandatory pre-admissions, and hidden costs.
Participates in visible debate events with BeaconAgent.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from backend.events import emit
from backend.schemas.models import (
    StudentProfile,
    MatchResult,
    SkepticResult,
    SkepticVerdict,
    EventType,
)

logger = logging.getLogger("meritmap.agents.skeptic")


def evaluate_adversarially(profile: StudentProfile, match: MatchResult) -> SkepticResult:
    """
    Adversarially scrutinize a match against student profile and evidence ledger.
    """
    opp = match.opportunity
    objections: list[str] = []
    evidence: list[str] = []
    verdict = SkepticVerdict.SURVIVES

    opp_name = str(opp.name.value or "Opportunity")
    student_nat = (profile.nationality or "India").lower()
    student_gpa_10 = profile.cgpa_normalized_10 or profile.cgpa or 8.0
    student_gpa_4 = profile.cgpa_normalized_4 or (student_gpa_10 / 2.5)

    # 1. Check Deadline Expired
    deadline_val = str(opp.deadline.value or "").strip().lower()
    if deadline_val and deadline_val not in ["rolling", "open", "tbd", "none", "unknown"]:
        # Search for past years (e.g., 2021, 2022, 2023, 2024, 2025)
        past_years = re.findall(r"\b202[0-5]\b", deadline_val)
        future_years = re.findall(r"\b202[6-9]\b", deadline_val)
        if past_years and not future_years:
            objections.append(f"Deadline has expired ({deadline_val}). Prior cycle page was crawled.")
            evidence.append(f"Extracted deadline: '{opp.deadline.value}' from {opp.deadline.source_url or opp.url}")
            verdict = SkepticVerdict.REJECTED

    # 2. Check Strict Nationality Exclusion
    nats_val = str(opp.eligible_nationalities.value or "").strip().lower()
    is_open = any(kw in nats_val for kw in ["all", "international", "global", "any", "worldwide", "open to all"])
    if nats_val and not is_open:
        # Check if student's nationality is explicitly excluded or missing from a whitelist
        whitelist_words = [w.strip() for w in re.split(r"[,;/]|\band\b", nats_val)]
        # If whitelist has specific countries and student nationality is not in it
        if len(whitelist_words) >= 1 and not any(kw in nats_val for kw in [student_nat, "india", "developing", "commonwealth", "asia", "third world"]):
            objections.append(f"Restricted nationality list ({nats_val[:60]}); student holds {profile.nationality or 'Indian'} citizenship.")
            evidence.append(f"Eligibility snippet: '{opp.eligible_nationalities.exact_quote_snippet or nats_val}'")
            verdict = SkepticVerdict.REJECTED

    # 3. Check Minimum GPA hard gate
    min_gpa_val = None
    if opp.min_gpa and opp.min_gpa.value:
        nums = re.findall(r"\d+\.?\d*", str(opp.min_gpa.value))
        if nums:
            try:
                min_gpa_val = float(nums[0])
            except ValueError:
                pass

    if min_gpa_val:
        if min_gpa_val <= 4.0:
            if student_gpa_4 < (min_gpa_val - 0.2):
                objections.append(f"Hard GPA threshold unmet: requires {min_gpa_val}/4.0, student has {student_gpa_4:.2f}/4.0.")
                evidence.append(f"Min GPA quoted: '{opp.min_gpa.exact_quote_snippet or opp.min_gpa.value}'")
                if verdict != SkepticVerdict.REJECTED:
                    verdict = SkepticVerdict.WEAKENED
        else:
            if student_gpa_10 < (min_gpa_val - 0.4):
                objections.append(f"Hard GPA threshold unmet: requires {min_gpa_val}/10.0, student has {student_gpa_10:.2f}/10.0.")
                evidence.append(f"Min GPA quoted: '{opp.min_gpa.exact_quote_snippet or opp.min_gpa.value}'")
                if verdict != SkepticVerdict.REJECTED:
                    verdict = SkepticVerdict.WEAKENED

    # 4. Check Mandatory Separate Admission / Prior Offer Requirement
    reqs_str = str(opp.other_requirements.value or "").lower() + " " + str(opp.how_to_apply.value or "").lower()
    if any(phrase in reqs_str for phrase in ["must hold an offer", "unconditional offer required", "already admitted", "prior admission"]):
        objections.append("Requires pre-existing university offer of admission before scholarship application can be submitted.")
        evidence.append("How-to-apply text indicates dual sequential application rather than unified portal.")
        if verdict == SkepticVerdict.SURVIVES:
            verdict = SkepticVerdict.WEAKENED

    # 5. Check Test Score Gaps (IELTS / GRE)
    test_str = str(opp.required_tests.value or "").lower()
    if "gre" in test_str:
        has_gre = any(t.test_name.lower() == "gre" for t in profile.test_scores)
        if not has_gre:
            objections.append("Mandatory GRE General Test specified; student has not yet taken the GRE.")
            evidence.append(f"Test requirements: '{opp.required_tests.value}'")
            if verdict == SkepticVerdict.SURVIVES:
                verdict = SkepticVerdict.WEAKENED

    if "ielts" in test_str or "toefl" in test_str:
        has_ielts = any(t.test_name.lower() == "ielts" for t in profile.test_scores)
        has_toefl = any(t.test_name.lower() == "toefl" for t in profile.test_scores)
        if not has_ielts and not has_toefl:
            objections.append("English language certification (IELTS/TOEFL) strictly required for visa and departmental review.")
            evidence.append(f"Language requirements: '{opp.required_tests.value}'")
            if verdict == SkepticVerdict.SURVIVES:
                verdict = SkepticVerdict.WEAKENED

    # 6. Check Target Degree Match
    target_deg = (profile.target_degree_level or "Masters").lower()
    eligible_deg = str(opp.eligible_degrees.value or "").lower()
    if eligible_deg and ("phd only" in eligible_deg or "doctoral only" in eligible_deg) and "master" in target_deg:
        objections.append(f"Degree mismatch: scholarship is restricted to PhD/Doctoral researchers, while student seeks {profile.target_degree_level}.")
        evidence.append(f"Eligible degrees: '{opp.eligible_degrees.value}'")
        verdict = SkepticVerdict.REJECTED

    reasoning = ""
    if verdict == SkepticVerdict.REJECTED:
        reasoning = f"REJECTED: Critical deal-breaker found. {'; '.join(objections)}"
    elif verdict == SkepticVerdict.WEAKENED:
        reasoning = f"WEAKENED: Potential friction points. {'; '.join(objections)}"
    else:
        reasoning = "SURVIVES: Candidate satisfies core eligibility filters with no disqualifying clauses found in source documents."

    return SkepticResult(
        opportunity_id=opp.id,
        verdict=verdict,
        objections=objections,
        evidence=evidence,
        reasoning=reasoning,
    )


async def run_skeptic(
    run_id: str,
    profile: StudentProfile,
    matches: list[MatchResult],
    round_num: int = 1,
    chaos_mode: bool = False,
) -> tuple[list[MatchResult], list[SkepticResult]]:
    """
    Perform adversarial verification against the top ranked matches.
    Emits debate events visible in the UI.
    """
    emit(run_id, "SkepticAgent", EventType.START, f"🔍 SkepticAgent starting adversarial verification (Round {round_num})...")

    # Scrutinize the top 8 matches
    candidates_to_challenge = matches[:8]
    skeptic_results: list[SkepticResult] = []
    updated_matches: list[MatchResult] = []

    for match in candidates_to_challenge:
        opp_name = str(match.opportunity.name.value or "Opportunity")
        inst_name = str(match.opportunity.institution.value or match.opportunity.country or "")

        # Emit Beacon defense
        emit(
            run_id,
            "BeaconAgent",
            EventType.DEBATE,
            f"🏮 Beacon: Recommending '{opp_name}' ({inst_name}) — Fit Score {match.fit_score:.1f}/100. "
            f"{'Fully funded' if match.true_cost.is_fully_funded else 'Estimated out-of-pocket: ' + str(match.true_cost.currency) + ' ' + str(match.true_cost.out_of_pocket_yearly_local)}.",
            {"opportunity_id": match.opportunity.id, "fit_score": match.fit_score, "speaker": "Beacon"}
        )

        res = evaluate_adversarially(profile, match)
        skeptic_results.append(res)

        if res.verdict == SkepticVerdict.REJECTED:
            emit(
                run_id,
                "SkepticAgent",
                EventType.DEBATE,
                f"⚔️ Skeptic CHALLENGE [REJECTED]: Objection against '{opp_name}'! {res.reasoning}",
                {"opportunity_id": match.opportunity.id, "verdict": "rejected", "speaker": "Skeptic", "objections": res.objections}
            )
            # Demote score heavily and append objections
            match.fit_score = max(0.0, match.fit_score - 45.0)
            match.eligibility_gaps.extend(res.objections)
        elif res.verdict == SkepticVerdict.WEAKENED:
            emit(
                run_id,
                "SkepticAgent",
                EventType.DEBATE,
                f"⚠️ Skeptic CAUTION [WEAKENED]: Caution on '{opp_name}'. {res.reasoning}",
                {"opportunity_id": match.opportunity.id, "verdict": "weakened", "speaker": "Skeptic", "objections": res.objections}
            )
            match.fit_score = max(0.0, match.fit_score - 10.0)
            match.eligibility_gaps.extend(res.objections)
        else:
            emit(
                run_id,
                "SkepticAgent",
                EventType.DEBATE,
                f"🛡️ Skeptic CLEARANCE [SURVIVES]: '{opp_name}' verified. No deal-breakers found in source ledger.",
                {"opportunity_id": match.opportunity.id, "verdict": "survives", "speaker": "Skeptic"}
            )

        updated_matches.append(match)

    # Append remaining unscored matches unchanged
    for match in matches[8:]:
        updated_matches.append(match)

    # Re-sort matches after skeptic penalties
    updated_matches.sort(key=lambda m: m.fit_score, reverse=True)

    survived_count = sum(1 for r in skeptic_results if r.verdict == SkepticVerdict.SURVIVES)
    rejected_count = sum(1 for r in skeptic_results if r.verdict == SkepticVerdict.REJECTED)
    weakened_count = sum(1 for r in skeptic_results if r.verdict == SkepticVerdict.WEAKENED)

    emit(
        run_id,
        "SkepticAgent",
        EventType.RESULT,
        f"🔍 Skeptic audit complete: {survived_count} survived cleanly, {weakened_count} flagged with cautions, {rejected_count} rejected.",
        {
            "survived": survived_count,
            "weakened": weakened_count,
            "rejected": rejected_count,
        }
    )

    return updated_matches, skeptic_results
