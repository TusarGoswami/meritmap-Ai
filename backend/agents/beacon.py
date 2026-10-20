"""
BeaconAgent — Matches student profile against verified opportunities.
Calculates transparent fit scores (0-100) and True Cost (including INR conversion).
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Optional

from backend.events import emit
from backend.schemas.models import (
    StudentProfile,
    Opportunity,
    MatchResult,
    FitBreakdown,
    TrueCost,
    SourceTrust,
    EventType,
)
from backend.llm import llm_structured
from pydantic import BaseModel, Field

logger = logging.getLogger("meritmap.agents.beacon")

# Exchange rates to INR (standard fallback rates)
FX_TO_INR = {
    "USD": 86.5,
    "EUR": 93.0,
    "CAD": 62.5,
    "GBP": 110.0,
    "SGD": 64.5,
    "CHF": 97.0,
    "AUD": 55.5,
    "INR": 1.0,
}

# Currency conversion to USD
FX_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "CAD": 0.72,
    "GBP": 1.28,
    "SGD": 0.75,
    "CHF": 1.12,
    "AUD": 0.65,
    "INR": 0.0116,
}


def calculate_source_trust(opp: Opportunity) -> SourceTrust:
    """Evaluate trustworthiness of the opportunity's data sources."""
    score = 0.5
    reasons = []
    is_official = False

    url = (opp.url or "").lower()
    official_tlds = [".edu", ".ac.", ".gov", ".org", "europa.eu", "daad.de", "fulbright", "chevening"]
    if any(tld in url for tld in official_tlds):
        is_official = True
        score += 0.3
        reasons.append("Official academic or government domain")
    else:
        score -= 0.1
        reasons.append("Non-university domain / aggregator")

    # Check verification status of critical fields
    verified_fields = 0
    total_fields = 0
    for field_name in ["deadline", "funding_type", "min_gpa", "eligible_nationalities"]:
        f = getattr(opp, field_name, None)
        if f:
            total_fields += 1
            if getattr(f, "verified", False):
                verified_fields += 1

    if verified_fields >= 2:
        score += 0.2
        reasons.append(f"{verified_fields} key fields directly verified with page snippets")
    elif verified_fields == 0:
        score -= 0.15
        reasons.append("Key fields lack direct page quote verification (Fog)")

    score = max(0.1, min(1.0, score))
    return SourceTrust(
        score=round(score, 2),
        is_official_domain=is_official,
        multiple_confirmations=verified_fields >= 2,
        page_recency="recent" if verified_fields > 0 else "unknown",
        reasons=reasons,
    )


def calculate_true_cost(opp: Opportunity) -> TrueCost:
    """Calculate realistic out-of-pocket cost and convert to INR."""
    currency = opp.currency or "USD"
    if currency not in FX_TO_INR:
        currency = "USD"

    # Extract or estimate tuition
    tuition_val = None
    living_val = None
    funding_val = None
    cost_confidence = "estimate"
    notes = []

    # Check tuition evidence
    if opp.tuition_yearly and opp.tuition_yearly.value:
        try:
            # Clean string like "$20,000" or "20000"
            raw = str(opp.tuition_yearly.value).replace("$", "").replace("€", "").replace("£", "").replace(",", "").strip()
            # extract first float
            parts = [p for p in raw.split() if p.replace(".", "", 1).isdigit()]
            if parts:
                tuition_val = float(parts[0])
                if opp.tuition_yearly.verified:
                    cost_confidence = "official"
        except Exception:
            pass

    # Country-based typical living costs per year if missing
    country_living_defaults = {
        "germany": 11208.0,  # EUR blocked account
        "canada": 20635.0,   # CAD study permit requirement
        "united states": 18000.0, # USD
        "usa": 18000.0,
        "singapore": 15000.0, # SGD
        "united kingdom": 14000.0, # GBP
        "uk": 14000.0,
        "switzerland": 24000.0, # CHF
    }

    opp_country = (opp.country or "").lower()
    default_living = 15000.0
    for c, val in country_living_defaults.items():
        if c in opp_country:
            default_living = val
            break

    if opp.living_cost_yearly and opp.living_cost_yearly.value:
        try:
            raw = str(opp.living_cost_yearly.value).replace("$", "").replace("€", "").replace("£", "").replace(",", "").strip()
            parts = [p for p in raw.split() if p.replace(".", "", 1).isdigit()]
            if parts:
                living_val = float(parts[0])
        except Exception:
            living_val = default_living
    else:
        living_val = default_living
        notes.append(f"Estimated baseline living cost for {opp.country or 'study country'}")

    # Tuitions default by country if not stated
    if tuition_val is None:
        if "germany" in opp_country:
            tuition_val = 600.0  # Public uni semester fee ~300 EUR * 2
            notes.append("Zero/minimal tuition at public German universities")
        elif "canada" in opp_country:
            tuition_val = 22000.0  # CAD
            notes.append("Estimated international graduate tuition")
        elif "singapore" in opp_country:
            tuition_val = 25000.0  # SGD
            notes.append("Estimated standard international tuition")
        else:
            tuition_val = 20000.0
            notes.append("Standard international tuition estimate")

    # Assess funding coverage
    funding_type_str = str(opp.funding_type.value or "").lower()
    is_fully_funded = False

    if "full" in funding_type_str or "100%" in funding_type_str:
        # Full coverage: tuition waived + living allowance
        is_fully_funded = True
        funding_val = tuition_val + living_val
        out_of_pocket = 0.0
        notes.append("Full funding covers 100% tuition and living stipend")
    elif "tuition" in funding_type_str or "half" in funding_type_str or "partial" in funding_type_str:
        # Partial: e.g. tuition covered
        funding_val = tuition_val
        out_of_pocket = living_val
        notes.append("Tuition covered; student responsible for living expenses")
    else:
        funding_val = 0.0
        out_of_pocket = tuition_val + living_val
        notes.append("No guaranteed funding confirmed — self-funded baseline")

    rate = FX_TO_INR.get(currency, 86.5)
    out_of_pocket_inr = round(out_of_pocket * rate, 2)

    return TrueCost(
        tuition_yearly_local=round(tuition_val, 2),
        living_cost_yearly_local=round(living_val, 2),
        funding_coverage_local=round(funding_val, 2),
        currency=currency,
        out_of_pocket_yearly_local=round(out_of_pocket, 2),
        out_of_pocket_yearly_inr=out_of_pocket_inr,
        exchange_rate_used=rate,
        is_fully_funded=is_fully_funded,
        cost_confidence=cost_confidence,
        notes=notes,
    )


def compute_fit_breakdown(profile: StudentProfile, opp: Opportunity, true_cost: TrueCost) -> tuple[FitBreakdown, list[str], list[str]]:
    """Deterministic, transparent fit scoring (0-100)."""
    academic_fit = 0.0
    field_fit = 0.0
    funding_fit = 0.0
    eligibility_fit = 0.0
    deadline_feasibility = 0.0
    affordability = 0.0
    gaps = []
    missing_reqs = []

    # 1. Academic fit (0-25)
    student_gpa_10 = profile.cgpa_normalized_10 or (profile.cgpa if (profile.cgpa_scale or 10) == 10 else 7.5)
    min_gpa_val = None
    if opp.min_gpa and opp.min_gpa.value:
        try:
            raw = str(opp.min_gpa.value)
            # Find numbers
            import re
            nums = re.findall(r"\d+\.?\d*", raw)
            if nums:
                min_gpa_val = float(nums[0])
        except Exception:
            pass

    if min_gpa_val:
        # Check scale
        if min_gpa_val <= 4.0:
            student_gpa_4 = profile.cgpa_normalized_4 or (student_gpa_10 / 2.5)
            if student_gpa_4 >= min_gpa_val:
                academic_fit = 25.0
            elif student_gpa_4 >= min_gpa_val - 0.3:
                academic_fit = 18.0
                gaps.append(f"GPA slightly below stated requirement ({student_gpa_4:.2f} vs {min_gpa_val})")
            else:
                academic_fit = 8.0
                gaps.append(f"GPA significantly below requirement ({student_gpa_4:.2f} vs {min_gpa_val})")
        else:
            if student_gpa_10 >= min_gpa_val:
                academic_fit = 25.0
            elif student_gpa_10 >= min_gpa_val - 0.5:
                academic_fit = 18.0
                gaps.append(f"CGPA ({student_gpa_10}) slightly below minimum {min_gpa_val}")
            else:
                academic_fit = 8.0
                gaps.append(f"CGPA ({student_gpa_10}) below requirement {min_gpa_val}")
    else:
        # Default based on student's standing
        if student_gpa_10 >= 8.5:
            academic_fit = 24.0
        elif student_gpa_10 >= 7.5:
            academic_fit = 21.0
        else:
            academic_fit = 16.0

    # Boost academic fit for strong projects or publications
    if profile.projects or profile.publications:
        academic_fit = min(25.0, academic_fit + 2.0)

    # 2. Field fit (0-20)
    student_field = (profile.field_of_study or "").lower()
    student_skills = " ".join(profile.skills + profile.programming_languages).lower()
    opp_field = str(opp.eligible_fields.value or "").lower()
    opp_name = str(opp.name.value or "").lower()

    if any(kw in opp_field or kw in opp_name for kw in [student_field, "computer", "computing", "technology", "engineering", "stem", "data", "science"]):
        field_fit = 20.0
    elif "all fields" in opp_field or "any" in opp_field or not opp_field:
        field_fit = 17.0
    else:
        field_fit = 10.0
        gaps.append(f"Field mismatch: profile is {profile.field_of_study}, program focuses on {opp_field[:50]}")

    # 3. Funding fit (0-20)
    if true_cost.is_fully_funded:
        funding_fit = 20.0
    elif "partial" in str(opp.funding_type.value or "").lower() or "tuition" in str(opp.funding_type.value or "").lower():
        funding_fit = 14.0 if not profile.needs_full_funding else 8.0
        if profile.needs_full_funding:
            gaps.append("Provides partial funding, but student specifically requested full funding")
    else:
        funding_fit = 6.0 if not profile.needs_full_funding else 2.0
        if profile.needs_full_funding:
            gaps.append("Self-funded or unconfirmed funding; student needs full funding")

    # 4. Eligibility fit (0-15)
    # Check nationality
    nat = (profile.nationality or "India").lower()
    opp_nats = str(opp.eligible_nationalities.value or "").lower()
    if opp_nats and "all" not in opp_nats and "international" not in opp_nats:
        if nat in opp_nats:
            eligibility_fit += 8.0
        elif "developing" in opp_nats or "commonwealth" in opp_nats or "global" in opp_nats:
            eligibility_fit += 7.0
        else:
            eligibility_fit += 2.0
            gaps.append(f"Nationality eligibility restriction ({opp_nats[:50]})")
    else:
        eligibility_fit += 8.0

    # Check test requirements
    tests_str = str(opp.required_tests.value or "").lower()
    if "ielts" in tests_str or "toefl" in tests_str:
        has_ielts = any(t.test_name.lower() == "ielts" and (t.score or 0) >= 6.5 for t in profile.test_scores)
        has_toefl = any(t.test_name.lower() == "toefl" and (t.score or 0) >= 90 for t in profile.test_scores)
        if has_ielts or has_toefl:
            eligibility_fit += 7.0
        else:
            eligibility_fit += 3.0
            missing_reqs.append("English Proficiency Test (IELTS / TOEFL)")
    elif "gre" in tests_str:
        has_gre = any(t.test_name.lower() == "gre" for t in profile.test_scores)
        if has_gre:
            eligibility_fit += 7.0
        else:
            eligibility_fit += 4.0
            missing_reqs.append("GRE General Test")
    else:
        eligibility_fit += 7.0

    # 5. Deadline feasibility (0-10)
    deadline_str = str(opp.deadline.value or "").lower()
    if not deadline_str or "rolling" in deadline_str or "open" in deadline_str or "tbd" in deadline_str:
        deadline_feasibility = 8.0
    else:
        # Check if expired
        is_past = False
        import re
        years = re.findall(r"202[0-5]", deadline_str)
        if years and not re.findall(r"202[6-9]", deadline_str):
            is_past = True

        if is_past:
            deadline_feasibility = 0.0
            gaps.append(f"Deadline appears expired ({deadline_str})")
        else:
            deadline_feasibility = 10.0

    # 6. Affordability (0-10)
    budget = profile.budget_max_yearly_usd or 5000.0
    # convert out of pocket to USD
    curr = true_cost.currency
    out_usd = true_cost.out_of_pocket_yearly_local * FX_TO_USD.get(curr, 1.0)
    if true_cost.is_fully_funded or out_usd <= budget:
        affordability = 10.0
    elif out_usd <= budget * 1.5:
        affordability = 6.0
        gaps.append(f"Out of pocket (~${out_usd:,.0f}/yr) slightly exceeds stated budget (~${budget:,.0f}/yr)")
    else:
        affordability = 2.0
        gaps.append(f"High out-of-pocket expenses (~${out_usd:,.0f}/yr) exceed budget (~${budget:,.0f}/yr)")

    total = academic_fit + field_fit + funding_fit + eligibility_fit + deadline_feasibility + affordability
    total = round(min(100.0, max(0.0, total)), 1)

    breakdown = FitBreakdown(
        academic_fit=round(academic_fit, 1),
        field_fit=round(field_fit, 1),
        funding_fit=round(funding_fit, 1),
        eligibility_fit=round(eligibility_fit, 1),
        deadline_feasibility=round(deadline_feasibility, 1),
        affordability=round(affordability, 1),
        total=total,
        reasoning=f"Fit score {total}/100: Academic ({academic_fit}/25), Field ({field_fit}/20), Funding ({funding_fit}/20), Eligibility ({eligibility_fit}/15), Deadline ({deadline_feasibility}/10), Affordability ({affordability}/10)",
    )

    return breakdown, gaps, missing_reqs


async def run_beacon(
    run_id: str,
    profile: StudentProfile,
    opportunities: list[Opportunity],
    chaos_mode: bool = False,
) -> list[MatchResult]:
    """
    Score, compute true costs, and rank verified opportunities for student profile.
    """
    emit(run_id, "BeaconAgent", EventType.START, f"🏮 BeaconAgent evaluating {len(opportunities)} verified opportunities...")

    results: list[MatchResult] = []

    for opp in opportunities:
        true_cost = calculate_true_cost(opp)
        breakdown, gaps, missing_reqs = compute_fit_breakdown(profile, opp, true_cost)

        opp_name = opp.name.value or "Scholarship Program"
        inst_name = opp.institution.value or opp.country or "Global"

        why_parts = []
        if breakdown.academic_fit >= 20:
            why_parts.append(f"Strong academic match for {profile.cgpa or 'stated'} CGPA")
        if breakdown.field_fit >= 16:
            why_parts.append(f"Aligned with {profile.field_of_study or 'target'} studies")
        if true_cost.is_fully_funded:
            why_parts.append("Fully funded covering tuition and living expenses")
        elif true_cost.funding_coverage_local and true_cost.funding_coverage_local > 0:
            why_parts.append(f"Partial funding available ({true_cost.currency} {true_cost.funding_coverage_local:,.0f})")

        why_it_fits = "; ".join(why_parts) if why_parts else f"Matches target degree level in {opp.country or 'destination'}."

        match = MatchResult(
            opportunity=opp,
            fit_score=breakdown.total,
            fit_breakdown=breakdown,
            true_cost=true_cost,
            why_it_fits=why_it_fits,
            missing_requirements=missing_reqs,
            eligibility_gaps=gaps,
        )
        results.append(match)

    # Sort descending by fit score
    results.sort(key=lambda m: m.fit_score, reverse=True)

    top_count = min(5, len(results))
    top_summary = ", ".join([f"{r.opportunity.name.value or 'Opportunity'} ({r.fit_score})" for r in results[:top_count]])

    emit(
        run_id,
        "BeaconAgent",
        EventType.RESULT,
        f"🏮 Scored {len(results)} opportunities. Top recommendations: {top_summary}",
        {
            "total_scored": len(results),
            "top_5": [
                {
                    "id": m.opportunity.id,
                    "name": m.opportunity.name.value,
                    "institution": m.opportunity.institution.value,
                    "fit_score": m.fit_score,
                    "is_fully_funded": m.true_cost.is_fully_funded,
                    "out_of_pocket_inr": m.true_cost.out_of_pocket_yearly_inr,
                }
                for m in results[:5]
            ],
        },
    )

    return results
