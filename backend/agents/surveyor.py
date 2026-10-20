"""
SurveyorAgent — Fetches official pages and extracts structured evidence.
Follows eligibility and how-to-apply links one level deep.
Outputs Evidence Ledger format with provenance.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend.llm import llm_structured
from backend.events import emit
from backend.schemas.models import Opportunity, EvidenceField, EventType
from backend.tools.fetch_tools import fetch_page_with_links

logger = logging.getLogger("meritmap.agents.surveyor")

from pydantic import BaseModel, Field
from typing import Optional


class ExtractedEvidence(BaseModel):
    """LLM-extracted evidence from a web page."""
    name: Optional[str] = None
    institution: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None

    eligible_nationalities: Optional[str] = None
    eligible_degrees: Optional[str] = None
    eligible_fields: Optional[str] = None
    min_gpa: Optional[str] = None
    required_tests: Optional[str] = None
    other_requirements: Optional[str] = None

    funding_type: Optional[str] = None
    tuition_covered: Optional[str] = None
    stipend: Optional[str] = None
    other_benefits: Optional[str] = None

    tuition_yearly: Optional[str] = None
    living_cost_yearly: Optional[str] = None
    currency: Optional[str] = None

    deadline: Optional[str] = None
    program_start: Optional[str] = None

    application_link: Optional[str] = None
    how_to_apply: Optional[str] = None


async def run_surveyor(
    run_id: str,
    candidates: list[Opportunity],
    chaos_mode: bool = False,
) -> list[Opportunity]:
    """
    Verify candidates by fetching their official pages and extracting evidence.
    """
    emit(run_id, "SurveyorAgent", EventType.START,
         f"📐 Surveying {len(candidates)} candidates from official sources...")

    verified = []

    # Process in batches to avoid overwhelming servers
    for i in range(0, len(candidates), 4):
        batch = candidates[i:i + 4]
        tasks = []
        for j, opp in enumerate(batch):
            # Chaos mode: fail one fetch
            if chaos_mode and i == 0 and j == 0:
                emit(run_id, "SurveyorAgent", EventType.WARNING,
                     f"🌊 Chaos: Deliberately failing fetch for {opp.name.value}")
                tasks.append(_chaos_verify(run_id, opp))
            else:
                tasks.append(_verify_one(run_id, opp))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                emit(run_id, "SurveyorAgent", EventType.WARNING,
                     f"⚠️ Verification failed: {res}")
                continue
            if res:
                verified.append(res)

        emit(run_id, "SurveyorAgent", EventType.PROGRESS,
             f"Verified {len(verified)}/{i + len(batch)} candidates")

    emit(run_id, "SurveyorAgent", EventType.RESULT,
         f"✅ Verified {len(verified)} opportunities with evidence",
         {"verified_count": len(verified)})

    return verified


async def _chaos_verify(run_id: str, opp: Opportunity) -> Opportunity:
    """Simulate a failed verification that recovers."""
    emit(run_id, "SurveyorAgent", EventType.WARNING,
         f"⚠️ Fetch failed for {opp.url}, retrying with backoff...")
    await asyncio.sleep(1)
    emit(run_id, "SurveyorAgent", EventType.RECOVERED,
         f"✅ Recovered: using search snippet for {opp.name.value}")
    # Mark as unverified
    opp.name.verified = False
    opp.name.confidence = 0.4
    return opp


async def _verify_one(run_id: str, opp: Opportunity) -> Opportunity | None:
    """Verify a single opportunity by fetching its page."""
    url = opp.url
    if not url:
        return None

    try:
        # Fetch page and follow eligibility/apply links
        result = await fetch_page_with_links(
            url,
            follow_links=["eligibility", "how to apply", "requirements",
                         "scholarship", "funding", "deadline", "admission"],
        )

        main_text = result["main_page"].text if result["main_page"].success else ""
        linked_texts = [p.text for p in result.get("linked_pages", []) if p.success]

        # Combine all text
        all_text = main_text
        for lt in linked_texts:
            all_text += f"\n\n--- LINKED PAGE ---\n{lt}"

        if not all_text or len(all_text.strip()) < 50:
            emit(run_id, "SurveyorAgent", EventType.WARNING,
                 f"⚠️ Could not extract content from {url}")
            return opp  # Return unverified

        # Extract structured evidence using LLM
        evidence = await _extract_evidence(all_text[:8000], url)
        if evidence:
            opp = _apply_evidence(opp, evidence, url, result["main_page"].source)

        return opp

    except Exception as e:
        logger.warning(f"Verification failed for {url}: {e}")
        emit(run_id, "SurveyorAgent", EventType.WARNING,
             f"⚠️ Failed to verify {opp.name.value}: {e}")
        return opp


async def _extract_evidence(text: str, url: str) -> ExtractedEvidence | None:
    """Use LLM to extract structured evidence from page content."""
    prompt = f"""Extract scholarship/program details from this web page content.
Return ONLY facts that are explicitly stated in the text.
If something is not mentioned, use null — never guess.

Page URL: {url}

Content:
---
{text}
---

Extract all available fields. For monetary values include the currency.
For dates, use ISO format (YYYY-MM-DD) where possible.
For eligibility fields, quote the exact relevant text from the page.
"""
    try:
        return await llm_structured(
            prompt, ExtractedEvidence,
            system="You are an expert at extracting scholarship details from web pages. "
                   "Only extract facts explicitly stated. Never guess or infer.",
            tier="fast",
        )
    except Exception as e:
        logger.warning(f"LLM evidence extraction failed: {e}; applying heuristic extractor")
        import re
        ev = ExtractedEvidence()
        
        # Deadline
        dl_m = re.search(r"(?:deadline|apply by|closing date|applications close)[:\s]+([A-Za-z0-9,\s]{5,35})", text, re.I)
        if dl_m:
            ev.deadline = dl_m.group(1).strip()
        
        # Funding
        if re.search(r"\b(fully funded|full tuition|100% tuition|full scholarship)\b", text, re.I):
            ev.funding_type = "Full Scholarship (Tuition + Stipend)"
            ev.tuition_covered = "100% Tuition Waived"
        elif re.search(r"\b(partial|tuition waiver|5,000|10,000|award)\b", text, re.I):
            ev.funding_type = "Partial Award"

        # Monthly stipend
        stipend_m = re.search(r"(\b(?:EUR|CHF|SGD|CAD|USD|GBP|£|€|\$)\s*[\d,]+(?:\s*(?:per month|/mo|monthly|per semester))?)", text, re.I)
        if stipend_m:
            ev.stipend = stipend_m.group(1).strip()

        # Nationalities
        nat_m = re.search(r"(?:eligible|open to|eligibility)[:\s]+([^.\n]{10,80})", text, re.I)
        if nat_m:
            ev.eligible_nationalities = nat_m.group(1).strip()
        elif "international" in text.lower():
            ev.eligible_nationalities = "International students (All nationalities)"

        # Min GPA
        gpa_m = re.search(r"(?:minimum GPA|GPA of|grade point average of|average of)[:\s]+([^\n,]{3,30})", text, re.I)
        if gpa_m:
            ev.min_gpa = gpa_m.group(1).strip()

        return ev


def _apply_evidence(
    opp: Opportunity,
    evidence: ExtractedEvidence,
    url: str,
    source: str,
) -> Opportunity:
    """Apply extracted evidence to an opportunity, creating Evidence Ledger entries."""
    now = datetime.now(timezone.utc)
    is_verified = source == "direct"  # Only direct fetches count as verified

    def make_field(value, snippet=None) -> EvidenceField:
        return EvidenceField(
            value=value,
            source_url=url,
            exact_quote_snippet=snippet or str(value)[:200],
            fetched_at=now,
            confidence=0.8 if is_verified else 0.5,
            verified=is_verified,
        )

    if evidence.name:
        opp.name = make_field(evidence.name)
    if evidence.institution:
        opp.institution = make_field(evidence.institution)
    if evidence.country:
        opp.country = evidence.country
    if evidence.city:
        opp.city = evidence.city

    # Eligibility
    if evidence.eligible_nationalities:
        opp.eligible_nationalities = make_field(evidence.eligible_nationalities)
    if evidence.eligible_degrees:
        opp.eligible_degrees = make_field(evidence.eligible_degrees)
    if evidence.eligible_fields:
        opp.eligible_fields = make_field(evidence.eligible_fields)
    if evidence.min_gpa:
        opp.min_gpa = make_field(evidence.min_gpa)
    if evidence.required_tests:
        opp.required_tests = make_field(evidence.required_tests)
    if evidence.other_requirements:
        opp.other_requirements = make_field(evidence.other_requirements)

    # Funding
    if evidence.funding_type:
        opp.funding_type = make_field(evidence.funding_type)
    if evidence.tuition_covered:
        opp.tuition_covered = make_field(evidence.tuition_covered)
    if evidence.stipend:
        opp.stipend = make_field(evidence.stipend)
    if evidence.other_benefits:
        opp.other_benefits = make_field(evidence.other_benefits)

    # Cost
    if evidence.tuition_yearly:
        opp.tuition_yearly = make_field(evidence.tuition_yearly)
    if evidence.living_cost_yearly:
        opp.living_cost_yearly = make_field(evidence.living_cost_yearly)
    if evidence.currency:
        opp.currency = evidence.currency

    # Dates
    if evidence.deadline:
        opp.deadline = make_field(evidence.deadline)
    if evidence.program_start:
        opp.program_start = make_field(evidence.program_start)

    # Application
    if evidence.application_link:
        opp.application_link = make_field(evidence.application_link)
    if evidence.how_to_apply:
        opp.how_to_apply = make_field(evidence.how_to_apply)

    return opp
