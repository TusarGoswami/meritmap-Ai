"""
Integration tests for FastAPI application endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_serve_frontend():
    response = client.get("/")
    assert response.status_code == 200
    assert "MeritMap" in response.text
    assert "assets/logo.png" in response.text


def test_demo_personas():
    resp_rahul = client.get("/api/demo/rahul")
    assert resp_rahul.status_code == 200
    assert resp_rahul.json()["name"] == "Rahul Sharma"

    resp_b = client.get("/api/demo/persona_b")
    assert resp_b.status_code == 200
    assert resp_b.json()["degree"] == "B.Sc"


def test_runs_list_and_persistence():
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    runs = resp.json().get("runs", [])
    # At least the two CLI runs we generated
    assert len(runs) >= 1


def test_whatif_endpoint_from_saved_run():
    # Load cli-rahul-shar run
    resp = client.get("/api/run/cli-rahul-shar/result")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"]["name"] == "Rahul Sharma"

    # Test whatif re-scoring
    whatif_resp = client.post(
        "/api/run/cli-rahul-shar/whatif",
        json={"cgpa": 9.5, "ielts_score": 8.0},
    )
    assert whatif_resp.status_code == 200
    assert len(whatif_resp.json()["matches"]) > 0


def test_calendar_export_endpoint():
    resp = client.get("/api/run/cli-rahul-shar/calendar.ics")
    assert resp.status_code == 200
    assert "BEGIN:VCALENDAR" in resp.text
    assert "END:VCALENDAR" in resp.text
