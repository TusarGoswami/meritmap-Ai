"""
Tests for iCalendar (.ics) generation.
"""
import pytest
from backend.schemas.models import Roadmap, RoadmapTask, MatchResult, Opportunity, EvidenceField
from backend.agents.pathfinder import generate_ics_calendar, export_ics


def test_ics_calendar_generation():
    task1 = RoadmapTask(
        title="IELTS Registration Deadline",
        description="Book academic module with ID proof",
        due_date="2026-11-20",
        priority="high",
        category="test",
    )
    task2 = RoadmapTask(
        title="LoR Request Submission",
        description="Submit references to departmental referee",
        due_date="2026-12-01",
        priority="medium",
        category="document",
    )
    roadmap = Roadmap(tasks=[task1, task2])

    ics = export_ics(roadmap)
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "IELTS Registration Deadline" in ics
    assert "20261120" in ics
    assert "20261201" in ics
    assert "PRODID:-//MeritMap//AI Scholarship Finder//EN" in ics
