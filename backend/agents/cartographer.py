"""
CartographerAgent — Parses resume, marksheet, GitHub, and extra URLs into a StudentProfile.
Normalizes CGPA across scales. Form inputs override inferred values.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.llm import llm_structured, llm_call
from backend.events import emit
from backend.schemas.models import (
    StudentProfile, EventType, Project, TestScore,
)
from backend.tools.pdf_tools import extract_pdf_text
from backend.tools.github_tools import fetch_github_profile

logger = logging.getLogger("meritmap.agents.cartographer")


class CartographerOutput(StudentProfile):
    """Output model same as StudentProfile."""
    pass


async def run_cartographer(
    run_id: str,
    profile: StudentProfile,
    resume_path: Optional[str] = None,
    marksheet_path: Optional[str] = None,
) -> StudentProfile:
    """
    Build a comprehensive StudentProfile from all available sources.
    Priority: form data > PDF extraction > GitHub inference.
    """
    emit(run_id, "CartographerAgent", EventType.START,
         "📜 Charting the student's profile from all available sources...")

    extracted = StudentProfile()
    sources = []

    # 1. Parse Resume PDF
    if resume_path:
        emit(run_id, "CartographerAgent", EventType.TOOL_CALL,
             "Reading resume PDF...")
        resume_text = extract_pdf_text(resume_path)
        if resume_text:
            sources.append("resume")
            emit(run_id, "CartographerAgent", EventType.PROGRESS,
                 f"Extracted {len(resume_text)} chars from resume")
            extracted = await _extract_from_resume(run_id, resume_text, extracted)
        else:
            emit(run_id, "CartographerAgent", EventType.WARNING,
                 "⚠️ Resume PDF unreadable — continuing with other sources")

    # 2. Parse Marksheet PDF
    if marksheet_path:
        emit(run_id, "CartographerAgent", EventType.TOOL_CALL,
             "Reading marksheet PDF...")
        marksheet_text = extract_pdf_text(marksheet_path)
        if marksheet_text:
            sources.append("marksheet")
            emit(run_id, "CartographerAgent", EventType.PROGRESS,
                 f"Extracted {len(marksheet_text)} chars from marksheet")
            extracted = await _extract_from_marksheet(run_id, marksheet_text, extracted)
        else:
            emit(run_id, "CartographerAgent", EventType.WARNING,
                 "⚠️ Marksheet PDF unreadable — CGPA from form only")

    # 3. GitHub enrichment
    github_username = profile.github_username or extracted.github_username
    if github_username:
        emit(run_id, "CartographerAgent", EventType.TOOL_CALL,
             f"Exploring GitHub profile: {github_username}")
        try:
            gh_data = await fetch_github_profile(github_username)
            if gh_data:
                extracted.github_username = github_username
                extracted.github_repos = gh_data.get("repos", [])
                extracted.github_languages = gh_data.get("languages", [])
                extracted.github_total_stars = gh_data.get("total_stars", 0)

                # Add GitHub languages to skills
                for lang in gh_data.get("languages", []):
                    if lang not in extracted.programming_languages:
                        extracted.programming_languages.append(lang)

                # Add GitHub bio info
                if gh_data.get("name") and not extracted.name:
                    extracted.name = gh_data["name"]

                sources.append("github")
                emit(run_id, "CartographerAgent", EventType.PROGRESS,
                     f"Found {len(gh_data.get('repos', []))} repos, "
                     f"{gh_data.get('total_stars', 0)} stars, "
                     f"languages: {', '.join(gh_data.get('languages', [])[:5])}")
        except Exception as e:
            emit(run_id, "CartographerAgent", EventType.WARNING,
                 f"⚠️ GitHub fetch failed: {e}")

    # 4. Merge: form data takes priority over extracted
    final = _merge_profiles(profile, extracted)
    final.source_documents = sources

    # 5. Normalize CGPA
    final = _normalize_cgpa(final)

    # 6. Identify missing fields
    final.missing_fields = _find_missing_fields(final)

    # 7. Set extraction confidence
    final.extraction_confidence = _calc_confidence(final, sources)

    emit(run_id, "CartographerAgent", EventType.RESULT,
         f"✅ Profile charted: {final.name or 'Unknown'} "
         f"({final.degree or '?'} in {final.field_of_study or '?'}), "
         f"CGPA {final.cgpa or '?'}/{final.cgpa_scale or '?'}, "
         f"{len(final.skills)} skills, {len(final.projects)} projects",
         {"profile_name": final.name, "confidence": final.extraction_confidence})

    return final


async def _extract_from_resume(run_id: str, text: str, profile: StudentProfile) -> StudentProfile:
    """Use LLM to extract structured data from resume text."""
    prompt = f"""Extract the following from this resume text. Return ONLY the fields you can find.
If a field is not present, use null.

Resume text:
---
{text[:6000]}
---

Extract: name, email, phone, nationality, degree, field_of_study, university,
graduation_year, cgpa (numeric), cgpa_scale, skills (list), programming_languages (list),
projects (list with name, description, technologies), achievements (list),
work_experience (list), publications (list), certifications (list),
test_scores (list with test_name and score), github_username.
"""
    try:
        result = await llm_structured(
            prompt, StudentProfile,
            system="You are an expert resume parser. Extract structured data accurately.",
            tier="fast",
        )
        # Merge non-null fields into profile
        for field in result.model_fields:
            val = getattr(result, field)
            existing = getattr(profile, field)
            if val and (not existing or existing == [] or existing == 0):
                setattr(profile, field, val)
        return profile
    except Exception as e:
        emit(run_id, "CartographerAgent", EventType.WARNING,
             f"⚠️ LLM resume extraction notice ({e}); using regex heuristic parser")
        import re
        if not profile.name:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for l in lines[:3]:
                if len(l.split()) in [2, 3] and not any(kw in l.lower() for kw in ["resume", "curriculum", "fictional", "page"]):
                    profile.name = l.replace("*", "").strip()
                    break

        if not profile.cgpa:
            cgpa_m = re.search(r"(?:CGPA|GPA)[:\s*]+(\d+\.?\d*)\s*(?:/\s*(\d+\.?\d*))?", text, re.I)
            if cgpa_m:
                profile.cgpa = float(cgpa_m.group(1))
                if cgpa_m.group(2):
                    profile.cgpa_scale = float(cgpa_m.group(2))
                else:
                    profile.cgpa_scale = 10.0 if profile.cgpa > 4.5 else 4.0

        if not profile.degree:
            deg_m = re.search(r"(B\.Tech|Bachelor of Technology|B\.Sc|M\.Sc|Master of Science|B\.E\.)", text, re.I)
            if deg_m:
                profile.degree = deg_m.group(1)

        if not profile.field_of_study:
            field_m = re.search(r"(Computer Science and Engineering|Computer Science|Environmental Engineering|Environmental Science|Artificial Intelligence|Data Science)", text, re.I)
            if field_m:
                profile.field_of_study = field_m.group(1)

        if not profile.skills:
            for s in ["Python", "PyTorch", "Docker", "SQL", "Machine Learning", "C++", "FastAPI", "GIS", "R", "Hydrological Modeling"]:
                if re.search(r"\b" + re.escape(s) + r"\b", text, re.I) and s not in profile.skills:
                    profile.skills.append(s)

        return profile


async def _extract_from_marksheet(run_id: str, text: str, profile: StudentProfile) -> StudentProfile:
    """Extract academic data from marksheet text."""
    prompt = f"""Extract academic information from this marksheet/transcript.

Text:
---
{text[:4000]}
---

Extract: name, university, degree, field_of_study, cgpa (numeric value),
cgpa_scale (the max scale like 10.0 or 4.0), percentage, graduation_year.
"""
    try:
        from pydantic import BaseModel
        class AcademicInfo(BaseModel):
            name: Optional[str] = None
            university: Optional[str] = None
            degree: Optional[str] = None
            field_of_study: Optional[str] = None
            cgpa: Optional[float] = None
            cgpa_scale: Optional[float] = None
            percentage: Optional[float] = None
            graduation_year: Optional[int] = None

        result = await llm_structured(
            prompt, AcademicInfo,
            system="You are an expert at reading academic transcripts.",
            tier="fast",
        )

        for field in ['name', 'university', 'degree', 'field_of_study',
                      'cgpa', 'cgpa_scale', 'percentage', 'graduation_year']:
            val = getattr(result, field, None)
            if val and not getattr(profile, field, None):
                setattr(profile, field, val)

        return profile
    except Exception as e:
        emit(run_id, "CartographerAgent", EventType.WARNING,
             f"⚠️ LLM marksheet extraction notice ({e}); using regex parser")
        import re
        if not profile.cgpa:
            cgpa_m = re.search(r"(?:CGPA|GPA)[:\s*]+(\d+\.?\d*)\s*(?:/\s*(\d+\.?\d*))?", text, re.I)
            if cgpa_m:
                profile.cgpa = float(cgpa_m.group(1))
                if cgpa_m.group(2):
                    profile.cgpa_scale = float(cgpa_m.group(2))
                else:
                    profile.cgpa_scale = 10.0 if profile.cgpa > 4.5 else 4.0
        return profile


def _merge_profiles(form: StudentProfile, extracted: StudentProfile) -> StudentProfile:
    """Merge form data (priority) with extracted data."""
    result = extracted.model_copy()

    # Form fields override extracted
    for field in form.model_fields:
        form_val = getattr(form, field)
        if form_val and form_val != [] and form_val != 0 and form_val is not False:
            setattr(result, field, form_val)

    return result


def _normalize_cgpa(profile: StudentProfile) -> StudentProfile:
    """Normalize CGPA to both 4.0 and 10.0 scales."""
    if profile.cgpa and profile.cgpa_scale:
        if profile.cgpa_scale == 10.0:
            profile.cgpa_normalized_10 = profile.cgpa
            # Common Indian conversion: (CGPA - 0.5) * 10 for percentage, then /25 for 4.0
            profile.cgpa_normalized_4 = round(profile.cgpa * 4.0 / 10.0, 2)
        elif profile.cgpa_scale == 4.0:
            profile.cgpa_normalized_4 = profile.cgpa
            profile.cgpa_normalized_10 = round(profile.cgpa * 10.0 / 4.0, 2)
        elif profile.cgpa_scale == 5.0:
            profile.cgpa_normalized_4 = round(profile.cgpa * 4.0 / 5.0, 2)
            profile.cgpa_normalized_10 = round(profile.cgpa * 10.0 / 5.0, 2)
        else:
            profile.cgpa_normalized_4 = round(profile.cgpa * 4.0 / profile.cgpa_scale, 2)
            profile.cgpa_normalized_10 = round(profile.cgpa * 10.0 / profile.cgpa_scale, 2)

    return profile


def _find_missing_fields(profile: StudentProfile) -> list[str]:
    """Identify critical missing fields."""
    missing = []
    if not profile.name:
        missing.append("name")
    if not profile.degree:
        missing.append("degree")
    if not profile.field_of_study:
        missing.append("field_of_study")
    if not profile.cgpa:
        missing.append("cgpa")
    if not profile.nationality:
        missing.append("nationality")
    if not profile.target_countries:
        missing.append("target_countries")
    if not profile.test_scores:
        missing.append("test_scores (IELTS/GRE/TOEFL)")
    if not profile.target_degree_level:
        missing.append("target_degree_level")
    return missing


def _calc_confidence(profile: StudentProfile, sources: list[str]) -> float:
    """Calculate overall profile extraction confidence."""
    score = 0.3  # Base
    if "resume" in sources:
        score += 0.25
    if "marksheet" in sources:
        score += 0.15
    if "github" in sources:
        score += 0.15
    if profile.name:
        score += 0.05
    if profile.cgpa:
        score += 0.05
    if profile.skills:
        score += 0.05
    return min(score, 1.0)
