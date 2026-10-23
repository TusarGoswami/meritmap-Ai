"""
MeritMap Event Bus — async event emitter with SSE broadcasting.
Each run has its own event queue. Subscribers get real-time events.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from backend.schemas.models import AgentEvent, EventType

logger = logging.getLogger("meritmap.events")

# Global registry: run_id -> list of subscriber queues
_subscribers: dict[str, list[asyncio.Queue]] = {}
# Event history per run (for replay)
_history: dict[str, list[AgentEvent]] = {}


def emit(
    run_id: str,
    agent: str,
    event_type: EventType,
    message: str,
    data: dict[str, Any] | None = None,
) -> AgentEvent:
    """Emit an event for a run. Non-blocking, queues to all subscribers."""
    event = AgentEvent(
        timestamp=datetime.now(timezone.utc),
        agent=agent,
        type=event_type,
        message=message,
        data=data,
    )

    # Store in history
    if run_id not in _history:
        _history[run_id] = []
    _history[run_id].append(event)

    # Broadcast to subscribers
    for q in _subscribers.get(run_id, []):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"Event queue full for run {run_id}, dropping event")

    logger.info(f"[{run_id}] {agent} | {event_type.value}: {message}")
    return event


def subscribe(run_id: str) -> asyncio.Queue:
    """Subscribe to events for a run. Returns a queue."""
    if run_id not in _subscribers:
        _subscribers[run_id] = []
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers[run_id].append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    """Remove a subscriber queue."""
    if run_id in _subscribers:
        try:
            _subscribers[run_id].remove(q)
        except ValueError:
            pass


async def event_stream(run_id: str) -> AsyncGenerator[dict[str, Any], None]:
    """
    Async generator yielding SSE event dictionaries for EventSourceResponse.
    Yields all historical events first, then live events.
    """
    # Replay history
    for event in _history.get(run_id, []):
        yield {"data": event.model_dump_json()}

    # Subscribe to live events
    q = subscribe(run_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield {"data": event.model_dump_json()}
                if event.type == EventType.DONE:
                    break
            except asyncio.TimeoutError:
                # Send keepalive
                yield {"comment": "keepalive"}
    finally:
        unsubscribe(run_id, q)


def get_history(run_id: str) -> list[AgentEvent]:
    """Get all events for a run."""
    return _history.get(run_id, [])


def clear_run(run_id: str) -> None:
    """Clear all data for a run."""
    _history.pop(run_id, None)
    _subscribers.pop(run_id, None)
