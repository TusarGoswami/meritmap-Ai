"""
MeritMap Pipeline — HarborMaster Orchestrator with LangGraph.
Implements the multi-agent state graph with autonomous loops:
- Surveyor -> (too few verified < 10) -> Navigator (broaden) -> Scout -> Surveyor
- Beacon <-> Skeptic debate loop (max 2 rounds)
- Auditor critic check & remediation
- Visible failure recovery and Chaos Mode events
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, TypedDict, Optional
from pathlib import Path

from langgraph.graph import StateGraph, START, END

from backend.events import emit
from backend.schemas.models import (
    PipelineState,
    StudentProfile,
    Opportunity,
    MatchResult,
    SkepticResult,
    SkepticVerdict,
    Roadmap,
    SOPDraft,
    AuditReport,
    EventType,
)
from backend.agents.cartographer import run_cartographer
from backend.agents.navigator import run_navigator
from backend.agents.scout import run_scout
from backend.agents.surveyor import run_surveyor
from backend.agents.beacon import run_beacon, calculate_source_trust
from backend.agents.skeptic import run_skeptic
from backend.agents.pathfinder import run_pathfinder
from backend.agents.scribe import run_scribe
from backend.agents.auditor import run_auditor

logger = logging.getLogger("meritmap.graph.pipeline")


class GraphState(TypedDict):
    run_id: str
    chaos_mode: bool
    state_obj: PipelineState
    resume_path: Optional[str]
    marksheet_path: Optional[str]
    navigator_iterations: int
    skeptic_rounds: int
    audit_attempts: int


# ─── Graph Node Definitions ──────────────────────────────────────────────

async def cartographer_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]

    # Chaos mode injection for Cartographer
    if state["chaos_mode"]:
        emit(run_id, "CartographerAgent", EventType.WARNING, "⚠️ [Chaos Mode] PDF extraction timed out on marksheet. Triggering fallback...")
        await asyncio.sleep(0.6)
        emit(run_id, "CartographerAgent", EventType.RECOVERED, "✅ [Chaos Mode] Recovered: Continuing with resume text and form profile attributes.")

    # Form overrides from initial state
    form_data = {}
    if p_state.profile:
        form_data = {
            "name": p_state.profile.name,
            "nationality": p_state.profile.nationality,
            "degree": p_state.profile.degree,
            "field_of_study": p_state.profile.field_of_study,
            "cgpa": p_state.profile.cgpa,
            "cgpa_scale": p_state.profile.cgpa_scale,
            "target_countries": p_state.profile.target_countries,
            "target_degree_level": p_state.profile.target_degree_level,
            "target_fields": p_state.profile.target_fields,
            "budget_max_yearly_usd": p_state.profile.budget_max_yearly_usd,
            "needs_full_funding": p_state.profile.needs_full_funding,
        }

    profile = await run_cartographer(
        run_id=run_id,
        profile=p_state.profile or StudentProfile(),
        resume_path=state.get("resume_path"),
        marksheet_path=state.get("marksheet_path"),
    )

    p_state.profile = profile
    return {"state_obj": p_state}


async def navigator_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]
    nav_iter = state.get("navigator_iterations", 0)

    is_broaden = nav_iter > 0
    queries = await run_navigator(
        run_id=run_id,
        profile=p_state.profile,
        broaden=is_broaden,
        iteration=nav_iter,
    )

    p_state.search_queries = queries
    p_state.navigator_iterations = nav_iter + 1
    return {
        "state_obj": p_state,
        "navigator_iterations": nav_iter + 1,
    }


async def scout_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]

    raw_candidates = await run_scout(
        run_id=run_id,
        queries=p_state.search_queries,
        chaos_mode=state["chaos_mode"],
    )

    # Accumulate candidates
    existing_ids = {c.id for c in p_state.raw_candidates}
    for c in raw_candidates:
        if c.id not in existing_ids:
            p_state.raw_candidates.append(c)

    return {"state_obj": p_state}


async def surveyor_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]

    verified = await run_surveyor(
        run_id=run_id,
        candidates=p_state.raw_candidates,
        chaos_mode=state["chaos_mode"],
    )

    p_state.verified_candidates = verified
    return {"state_obj": p_state}


async def beacon_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]

    # Use verified candidates or raw if verification pool was constrained
    opps = p_state.verified_candidates if p_state.verified_candidates else p_state.raw_candidates

    matches = await run_beacon(
        run_id=run_id,
        profile=p_state.profile,
        opportunities=opps,
        chaos_mode=state["chaos_mode"],
    )

    p_state.match_results = matches
    p_state.top_matches = matches[:5]

    # Compute source trust ratings
    for m in p_state.top_matches:
        p_state.source_trusts[m.opportunity.id] = calculate_source_trust(m.opportunity)

    return {"state_obj": p_state}


async def skeptic_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]
    round_num = state.get("skeptic_rounds", 0) + 1

    updated_matches, skeptic_res = await run_skeptic(
        run_id=run_id,
        profile=p_state.profile,
        matches=p_state.match_results,
        round_num=round_num,
        chaos_mode=state["chaos_mode"],
    )

    p_state.match_results = updated_matches
    p_state.skeptic_results = skeptic_res
    p_state.top_matches = [m for m in updated_matches if m.fit_score > 30.0][:5]
    if not p_state.top_matches:
        p_state.top_matches = updated_matches[:5]

    p_state.skeptic_rounds = round_num
    return {
        "state_obj": p_state,
        "skeptic_rounds": round_num,
    }


async def pathfinder_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]

    roadmap = await run_pathfinder(
        run_id=run_id,
        profile=p_state.profile,
        top_matches=p_state.top_matches,
        chaos_mode=state["chaos_mode"],
    )

    p_state.roadmap = roadmap
    return {"state_obj": p_state}


async def scribe_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]

    sop = await run_scribe(
        run_id=run_id,
        profile=p_state.profile,
        top_matches=p_state.top_matches,
        chaos_mode=state["chaos_mode"],
    )

    p_state.sop = sop
    return {"state_obj": p_state}


async def auditor_node(state: GraphState) -> Dict[str, Any]:
    run_id = state["run_id"]
    p_state = state["state_obj"]
    attempt = state.get("audit_attempts", 0) + 1

    report = await run_auditor(
        run_id=run_id,
        state=p_state,
        chaos_mode=state["chaos_mode"],
    )

    p_state.audit = report
    return {
        "state_obj": p_state,
        "audit_attempts": attempt,
    }


# ─── Conditional Edge Logic ──────────────────────────────────────────────

def route_after_surveyor(state: GraphState) -> str:
    """Check candidate count. If < 10 and iterations < 2, loop back to Navigator."""
    p_state = state["state_obj"]
    nav_iter = state.get("navigator_iterations", 0)
    count = len(p_state.verified_candidates)

    if count < 10 and nav_iter < 2:
        emit(
            state["run_id"],
            "HarborMaster",
            EventType.WARNING,
            f"🔄 Verified candidates ({count}) below target (10). HarborMaster triggering query broadening loop...",
            {"verified_count": count, "loop": nav_iter + 1},
        )
        return "navigator"

    return "beacon"


def route_after_skeptic(state: GraphState) -> str:
    """If top match rejected and rounds < 2, allow Beacon debate re-evaluation."""
    p_state = state["state_obj"]
    rounds = state.get("skeptic_rounds", 0)

    # Check if top 2 matches were rejected
    rejected_top = False
    skeptic_map = {r.opportunity_id: r.verdict for r in p_state.skeptic_results}
    for m in p_state.top_matches[:2]:
        if skeptic_map.get(m.opportunity.id) == SkepticVerdict.REJECTED:
            rejected_top = True
            break

    if rejected_top and rounds < 2:
        emit(
            state["run_id"],
            "HarborMaster",
            EventType.DEBATE,
            f"⚖️ Top recommendation overturned by Skeptic. HarborMaster initiating round {rounds + 1} debate...",
            {"round": rounds + 1},
        )
        return "beacon"

    return "pathfinder"


def route_after_auditor(state: GraphState) -> str:
    """Final route to end."""
    return END


# ─── Build and Compile LangGraph Workflow ─────────────────────────────────

def create_pipeline_graph():
    """Build the compiled LangGraph state workflow."""
    workflow = StateGraph(GraphState)

    workflow.add_node("cartographer", cartographer_node)
    workflow.add_node("navigator", navigator_node)
    workflow.add_node("scout", scout_node)
    workflow.add_node("surveyor", surveyor_node)
    workflow.add_node("beacon", beacon_node)
    workflow.add_node("skeptic", skeptic_node)
    workflow.add_node("pathfinder", pathfinder_node)
    workflow.add_node("scribe", scribe_node)
    workflow.add_node("auditor", auditor_node)

    # Sequence
    workflow.add_edge(START, "cartographer")
    workflow.add_edge("cartographer", "navigator")
    workflow.add_edge("navigator", "scout")
    workflow.add_edge("scout", "surveyor")

    # Conditional loop: Surveyor -> Navigator or Beacon
    workflow.add_conditional_edges(
        "surveyor",
        route_after_surveyor,
        {
            "navigator": "navigator",
            "beacon": "beacon",
        },
    )

    # Beacon -> Skeptic
    workflow.add_edge("beacon", "skeptic")

    # Conditional loop: Skeptic -> Beacon (re-debate) or Pathfinder
    workflow.add_conditional_edges(
        "skeptic",
        route_after_skeptic,
        {
            "beacon": "beacon",
            "pathfinder": "pathfinder",
        },
    )

    # Pathfinder -> Scribe -> Auditor -> END
    workflow.add_edge("pathfinder", "scribe")
    workflow.add_edge("scribe", "auditor")
    workflow.add_conditional_edges(
        "auditor",
        route_after_auditor,
        {
            END: END,
        },
    )

    return workflow.compile()


# Global compiled instance
_compiled_pipeline = None


def get_pipeline():
    global _compiled_pipeline
    if _compiled_pipeline is None:
        _compiled_pipeline = create_pipeline_graph()
    return _compiled_pipeline


async def run_pipeline(run_id: str, state: PipelineState, state_data: dict):
    """Execute the full MeritMap LangGraph pipeline."""
    emit(run_id, "HarborMaster", EventType.START, "⚓ HarborMaster orchestrator launched. Routing state graph...")

    initial_graph_state: GraphState = {
        "run_id": run_id,
        "chaos_mode": state.chaos_mode,
        "state_obj": state,
        "resume_path": state_data.get("resume_path"),
        "marksheet_path": state_data.get("marksheet_path"),
        "navigator_iterations": 0,
        "skeptic_rounds": 0,
        "audit_attempts": 0,
    }

    try:
        pipeline = get_pipeline()
        final_state = await pipeline.ainvoke(initial_graph_state)
        res_state: PipelineState = final_state["state_obj"]

        res_state.status = "completed"
        emit(
            run_id,
            "HarborMaster",
            EventType.DONE,
            f"🏁 MeritMap pipeline successfully completed! Found {len(res_state.verified_candidates)} verified opportunities, {len(res_state.top_matches)} top matches, and tailored SOP.",
            {
                "status": "completed",
                "verified_count": len(res_state.verified_candidates),
                "top_match_count": len(res_state.top_matches),
            },
        )
    except Exception as e:
        logger.exception(f"Error in pipeline graph execution: {e}")
        state.status = "failed"
        state.errors.append(str(e))
        emit(run_id, "HarborMaster", EventType.ERROR, f"HarborMaster execution error: {str(e)}")
        emit(run_id, "HarborMaster", EventType.DONE, "Pipeline finished with errors")
        raise
