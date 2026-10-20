"""
MeritMap FastAPI Application — serves API + static frontend.
"""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from sse_starlette.sse import EventSourceResponse

from backend.config import RUNS_DIR, FRONTEND_DIR
from backend.events import event_stream, get_history, emit
from backend.schemas.models import (
    RunResponse, EventType, PipelineState, WhatIfParams,
    StudentProfile, TestScore,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("meritmap.api")

app = FastAPI(
    title="MeritMap",
    description="Chart your way to funded education — AI multi-agent scholarship finder",
    version="1.0.0",
)

# In-memory run store (persisted to disk)
_runs: dict[str, PipelineState] = {}


def _parse_list_input(raw: Optional[str]) -> list[str]:
    """Parse a field that can be a JSON array string, comma-separated string, or newline-separated."""
    if not raw or not raw.strip():
        return []
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    return [x.strip() for x in s.replace("\n", ",").split(",") if x.strip()]


# ─── API Routes ────────────────────────────────────────────────────────────

@app.post("/api/run", response_model=RunResponse)
async def start_run(
    background_tasks: BackgroundTasks,
    resume: Optional[UploadFile] = File(None),
    marksheet: Optional[UploadFile] = File(None),
    github_username: Optional[str] = Form(None),
    extra_urls: Optional[str] = Form(None),  # JSON array string or comma-separated
    target_countries: Optional[str] = Form(None),  # JSON array string or comma-separated
    target_degree_level: Optional[str] = Form(None),
    target_fields: Optional[str] = Form(None),  # JSON array string or comma-separated
    budget_max_yearly_usd: Optional[float] = Form(None),
    needs_full_funding: bool = Form(False),
    chaos_mode: bool = Form(False),
    name: Optional[str] = Form(None),
    nationality: Optional[str] = Form(None),
    degree: Optional[str] = Form(None),
    field_of_study: Optional[str] = Form(None),
    cgpa: Optional[float] = Form(None),
    cgpa_scale: Optional[float] = Form(None),
    test_scores_json: Optional[str] = Form(None),
):
    """Start a MeritMap pipeline run."""
    run_id = str(uuid.uuid4())[:8]

    # Save uploaded files
    resume_path = None
    marksheet_path = None
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(exist_ok=True)

    if resume and resume.filename:
        resume_path = str(run_dir / "resume.pdf")
        content = await resume.read()
        Path(resume_path).write_bytes(content)

    if marksheet and marksheet.filename:
        marksheet_path = str(run_dir / "marksheet.pdf")
        content = await marksheet.read()
        Path(marksheet_path).write_bytes(content)

    # Safely parse array / list fields (supports JSON array or comma-separated strings)
    countries = _parse_list_input(target_countries)
    fields = _parse_list_input(target_fields)
    urls = _parse_list_input(extra_urls)
    scores = []
    if test_scores_json:
        try:
            parsed_scores = json.loads(test_scores_json)
            if isinstance(parsed_scores, list):
                scores = [TestScore(**s) for s in parsed_scores if isinstance(s, dict)]
        except Exception:
            scores = []

    # Build initial state
    state = PipelineState(
        run_id=run_id,
        chaos_mode=chaos_mode,
        status="running",
        profile=StudentProfile(
            name=name,
            nationality=nationality,
            degree=degree,
            field_of_study=field_of_study,
            cgpa=cgpa,
            cgpa_scale=cgpa_scale,
            github_username=github_username,
            extra_urls=urls,
            target_countries=countries,
            target_degree_level=target_degree_level,
            target_fields=fields,
            budget_max_yearly_usd=budget_max_yearly_usd,
            needs_full_funding=needs_full_funding,
            test_scores=scores,
        ),
    )

    _runs[run_id] = state

    # Store file paths in state data for agents
    state_data = {
        "resume_path": resume_path,
        "marksheet_path": marksheet_path,
    }

    emit(run_id, "HarborMaster", EventType.START, "MeritMap pipeline initiated", state_data)

    # Launch pipeline in background
    background_tasks.add_task(_run_pipeline, run_id, state, state_data)

    return RunResponse(run_id=run_id, status="started")


async def _run_pipeline(run_id: str, state: PipelineState, state_data: dict):
    """Execute the full MeritMap pipeline."""
    try:
        # Import here to avoid circular imports
        from backend.graph.pipeline import run_pipeline
        await run_pipeline(run_id, state, state_data)
    except Exception as e:
        logger.exception(f"Pipeline failed for run {run_id}")
        emit(run_id, "HarborMaster", EventType.ERROR, f"Pipeline failed: {str(e)}")
        emit(run_id, "HarborMaster", EventType.DONE, "Pipeline completed with errors")
        state.status = "failed"
        state.errors.append(str(e))
    finally:
        # Persist run to disk
        _save_run(run_id, state)


def _save_run(run_id: str, state: PipelineState):
    """Save run state to disk."""
    run_file = RUNS_DIR / f"{run_id}.json"
    run_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")


@app.get("/api/run/{run_id}/events")
async def stream_events(run_id: str):
    """SSE endpoint for real-time events."""
    return EventSourceResponse(event_stream(run_id))


@app.get("/api/run/{run_id}/result")
async def get_result(run_id: str):
    """Get the final result of a run."""
    if run_id in _runs:
        return _runs[run_id].model_dump()

    # Try loading from disk
    run_file = RUNS_DIR / f"{run_id}.json"
    if run_file.exists():
        return json.loads(run_file.read_text())

    raise HTTPException(status_code=404, detail=f"Run {run_id} not found")


@app.post("/api/run/{run_id}/whatif")
async def whatif(run_id: str, params: WhatIfParams):
    """Re-score matches with what-if parameters."""
    if run_id not in _runs:
        run_file = RUNS_DIR / f"{run_id}.json"
        if run_file.exists():
            try:
                _runs[run_id] = PipelineState.model_validate_json(run_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    state = _runs[run_id]
    if not state.top_matches:
        raise HTTPException(status_code=400, detail="No matches to re-score")

    try:
        from backend.agents.pathfinder import whatif_rescore
        new_matches = await whatif_rescore(state, params)
        return {"matches": [m.model_dump() for m in new_matches]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/run/{run_id}/calendar.ics")
async def get_calendar(run_id: str):
    """Export roadmap as ICS calendar file."""
    if run_id not in _runs:
        run_file = RUNS_DIR / f"{run_id}.json"
        if run_file.exists():
            try:
                _runs[run_id] = PipelineState.model_validate_json(run_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    state = _runs[run_id]
    if not state.roadmap:
        raise HTTPException(status_code=400, detail="No roadmap available")

    try:
        from backend.agents.pathfinder import export_ics
        ics_content = export_ics(state.roadmap)
        from fastapi.responses import Response
        return Response(
            content=ics_content,
            media_type="text/calendar",
            headers={"Content-Disposition": f"attachment; filename=meritmap_{run_id}.ics"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/runs")
async def list_runs():
    """List all available runs."""
    runs = []
    for f in RUNS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            runs.append({
                "run_id": data.get("run_id", f.stem),
                "status": data.get("status", "unknown"),
                "profile_name": data.get("profile", {}).get("name", "Unknown"),
            })
        except Exception:
            pass
    return {"runs": runs}


# ─── Demo data ────────────────────────────────────────────────────────────

@app.get("/api/demo/{persona}")
async def get_demo_data(persona: str):
    """Get pre-filled demo data for testing."""
    demos = {
        "rahul": {
            "name": "Rahul Sharma",
            "nationality": "Indian",
            "degree": "B.Tech",
            "field_of_study": "Computer Science",
            "cgpa": 8.2,
            "cgpa_scale": 10.0,
            "github_username": "rahul-cs-dev",
            "target_countries": ["Canada", "Germany", "Singapore"],
            "target_degree_level": "Masters",
            "target_fields": ["Computer Science", "Artificial Intelligence", "Machine Learning"],
            "needs_full_funding": True,
            "budget_max_yearly_usd": 5000,
        },
        "persona_b": {
            "name": "Amara Okafor",
            "nationality": "Nigerian",
            "degree": "B.Sc",
            "field_of_study": "Environmental Science",
            "cgpa": 3.7,
            "cgpa_scale": 4.0,
            "github_username": "",
            "target_countries": ["Netherlands", "Sweden", "UK"],
            "target_degree_level": "Masters",
            "target_fields": ["Environmental Science", "Sustainability", "Climate Science"],
            "needs_full_funding": True,
            "budget_max_yearly_usd": 3000,
        },
    }

    if persona not in demos:
        raise HTTPException(status_code=404, detail=f"Demo persona '{persona}' not found")

    return demos[persona]


# ─── Static Frontend ──────────────────────────────────────────────────────

# Serve frontend assets
app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")


@app.get("/")
async def serve_frontend():
    """Serve the main frontend page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MeritMap — Frontend not built yet</h1>")
