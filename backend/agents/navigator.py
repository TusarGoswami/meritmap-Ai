"""
NavigatorAgent — Generates diverse search queries from the student profile.
Produces 6-10 queries covering different angles: countries, funding types, fields.
Nothing is hardcoded; queries are dynamically built from the profile.
"""
from __future__ import annotations

import logging
from pydantic import BaseModel, Field

from backend.llm import llm_structured
from backend.events import emit
from backend.schemas.models import StudentProfile, EventType

logger = logging.getLogger("meritmap.agents.navigator")


class SearchQueries(BaseModel):
    """Output of NavigatorAgent."""
    queries: list[str] = Field(default_factory=list)
    reasoning: str = ""


async def run_navigator(
    run_id: str,
    profile: StudentProfile,
    iteration: int = 0,
    broaden: bool = False,
) -> list[str]:
    """
    Generate search queries tailored to the student profile.
    If broaden=True, generates wider/alternative queries.
    """
    emit(run_id, "NavigatorAgent", EventType.START,
         f"🧭 Plotting search courses {'(broadened)' if broaden else ''}...")

    countries = ", ".join(profile.target_countries) if profile.target_countries else "any country"
    field = profile.field_of_study or "any field"
    degree = profile.target_degree_level or "Masters"
    nationality = profile.nationality or "international"

    broaden_instruction = ""
    if broaden:
        broaden_instruction = """
IMPORTANT: Previous search round found too few results. BROADEN your queries:
- Try neighboring countries or regions
- Search for general international scholarships not specific to one country
- Look for STEM/science scholarships if the field is technical
- Search for government-funded scholarships from source countries
- Try different search terms and phrasings
"""

    prompt = f"""Generate 8-10 diverse search queries to find scholarships, funded programs,
and financial aid opportunities for this student profile:

- Nationality: {nationality}
- Current Degree: {profile.degree or 'unknown'}
- Field: {field}
- Target Degree: {degree}
- Target Countries: {countries}
- CGPA: {profile.cgpa or 'unknown'}/{profile.cgpa_scale or 'unknown'}
- Skills: {', '.join(profile.skills[:10]) if profile.skills else 'not specified'}
- Needs full funding: {profile.needs_full_funding}
- Budget max per year: ${profile.budget_max_yearly_usd or 'not specified'}

{broaden_instruction}

Generate queries that cover:
1. Country-specific scholarships for {nationality} students
2. University-specific funded {degree} programs in {field}
3. Government scholarships (both source and destination countries)
4. Research fellowships and assistantships
5. Need-based and merit-based aid
6. Professional/industry scholarships in the field

Each query should be a real search query that would work on Google/Tavily.
Make them specific and varied. Do NOT hardcode any specific university names unless
they are well-known for the field.

Return a JSON object with a "queries" list and brief "reasoning".
"""
    try:
        result = await llm_structured(
            prompt, SearchQueries,
            system="You are a scholarship research expert. Generate diverse, effective search queries.",
            tier="fast",
        )
        queries = result.queries[:10]

        # Add some structured queries
        if profile.target_countries:
            for country in profile.target_countries[:3]:
                base_q = f"fully funded {degree} scholarship {field} {country} {nationality} students 2025 2026"
                if base_q not in queries:
                    queries.append(base_q)

        emit(run_id, "NavigatorAgent", EventType.RESULT,
             f"✅ Plotted {len(queries)} search courses",
             {"query_count": len(queries), "queries": queries[:5]})

        return queries

    except Exception as e:
        emit(run_id, "NavigatorAgent", EventType.WARNING,
             f"⚠️ LLM query generation failed: {e}, using fallback queries")
        return _fallback_queries(profile)


def _fallback_queries(profile: StudentProfile) -> list[str]:
    """Generate basic queries without LLM."""
    queries = []
    field = profile.field_of_study or "STEM"
    degree = profile.target_degree_level or "Masters"
    nationality = profile.nationality or "international"

    if profile.target_countries:
        for country in profile.target_countries:
            queries.append(f"fully funded {degree} scholarship {field} {country} {nationality} students 2025")
            queries.append(f"university {degree} programs {field} {country} funding international students")
    else:
        queries.append(f"fully funded {degree} scholarship {field} international students 2025")
        queries.append(f"best {degree} programs {field} with scholarship international")

    queries.append(f"government scholarship {nationality} students abroad {degree}")
    queries.append(f"{field} research fellowship international 2025 2026")
    queries.append(f"merit scholarship {field} {degree} international students")

    return queries
