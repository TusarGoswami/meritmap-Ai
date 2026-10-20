"""
ScoutAgent — Parallel web search, dedup, aggregator-spam filtering.
Discovers 15+ candidates from search results.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from urllib.parse import urlparse

from backend.events import emit
from backend.schemas.models import Opportunity, EvidenceField, EventType, OpportunityType
from backend.tools.search_tools import search, is_official_domain, filter_aggregator_spam
from backend.llm import llm_structured

logger = logging.getLogger("meritmap.agents.scout")


async def run_scout(
    run_id: str,
    queries: list[str],
    chaos_mode: bool = False,
) -> list[Opportunity]:
    """
    Execute search queries in parallel and aggregate results.
    Returns deduplicated, filtered list of opportunities.
    """
    emit(run_id, "ScoutAgent", EventType.START,
         f"🔍 Scouting {len(queries)} search courses in parallel...")

    all_results = []

    # Chaos mode: fail one search deliberately
    if chaos_mode and len(queries) > 2:
        emit(run_id, "ScoutAgent", EventType.WARNING,
             "🌊 Chaos Mode: deliberately failing one search query")

    # Run searches in parallel (batches of 3 to avoid rate limits)
    for i in range(0, len(queries), 3):
        batch = queries[i:i + 3]
        tasks = []
        for j, q in enumerate(batch):
            # Chaos mode: corrupt one query
            if chaos_mode and i == 0 and j == 0:
                emit(run_id, "ScoutAgent", EventType.WARNING,
                     "🌊 Chaos: Corrupted query, will recover with next batch")
                continue
            tasks.append(_search_one(run_id, q))

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in batch_results:
            if isinstance(res, Exception):
                emit(run_id, "ScoutAgent", EventType.WARNING,
                     f"⚠️ Search query failed: {res}")
                continue
            all_results.extend(res)

        emit(run_id, "ScoutAgent", EventType.PROGRESS,
             f"Batch {i // 3 + 1}: found {len(all_results)} raw results so far")

    # Filter and deduplicate
    filtered = filter_aggregator_spam(all_results)
    deduped = _deduplicate(filtered)

    emit(run_id, "ScoutAgent", EventType.PROGRESS,
         f"After dedup and filtering: {len(deduped)} unique results")

    # Convert to Opportunity objects
    opportunities = await _convert_to_opportunities(run_id, deduped)

    # Prefer official domains
    opportunities.sort(
        key=lambda o: (1 if is_official_domain(o.url or "") else 0, o.url or ""),
        reverse=True,
    )

    emit(run_id, "ScoutAgent", EventType.RESULT,
         f"✅ Discovered {len(opportunities)} candidate opportunities",
         {"count": len(opportunities)})

    return opportunities


async def _search_one(run_id: str, query: str) -> list[dict]:
    """Run a single search query."""
    try:
        results = await search(query, max_results=8)
        return results
    except Exception as e:
        logger.warning(f"Search failed for '{query}': {e}")
        emit(run_id, "ScoutAgent", EventType.WARNING,
             f"⚠️ Search failed for query, will try alternatives")
        return []


def _deduplicate(results: list[dict]) -> list[dict]:
    """Remove duplicate URLs and near-duplicate titles."""
    seen_urls = set()
    seen_title_hashes = set()
    deduped = []

    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")

        # URL dedup
        parsed = urlparse(url)
        normalized_url = f"{parsed.netloc}{parsed.path}".rstrip("/").lower()
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)

        # Title similarity dedup (simple hash of first 50 chars normalized)
        title_hash = hashlib.md5(title.lower().strip()[:50].encode()).hexdigest()
        if title_hash in seen_title_hashes:
            continue
        seen_title_hashes.add(title_hash)

        deduped.append(r)

    return deduped


async def _convert_to_opportunities(run_id: str, results: list[dict]) -> list[Opportunity]:
    """Convert raw search results to Opportunity objects using LLM for initial parsing."""
    opportunities = []

    for i, r in enumerate(results):
        url = r.get("url", "")
        title = r.get("title", "")
        content = r.get("content", "")
        source = r.get("source", "search")

        # Generate a stable ID
        opp_id = hashlib.md5(url.encode()).hexdigest()[:8]

        # Quick LLM extraction of basic fields from search snippet
        opp = Opportunity(
            id=opp_id,
            name=EvidenceField(
                value=title,
                source_url=url,
                exact_quote_snippet=title,
                confidence=0.6,
                verified=False,
            ),
            url=url,
            discovered_via=source,
        )

        # Try to extract country and institution from title/content
        _quick_extract(opp, title, content, url)

        opportunities.append(opp)

    return opportunities


def _quick_extract(opp: Opportunity, title: str, content: str, url: str):
    """Quick heuristic extraction of country and institution from text."""
    text = f"{title} {content}".lower()

    # Country detection
    country_keywords = {
        "canada": "Canada", "canadian": "Canada",
        "germany": "Germany", "german": "Germany", "daad": "Germany",
        "singapore": "Singapore", "nus": "Singapore", "ntu": "Singapore",
        "usa": "USA", "united states": "USA", "american": "USA",
        "uk": "UK", "united kingdom": "UK", "british": "UK",
        "australia": "Australia", "australian": "Australia",
        "netherlands": "Netherlands", "dutch": "Netherlands",
        "sweden": "Sweden", "swedish": "Sweden",
        "switzerland": "Switzerland", "swiss": "Switzerland", "eth": "Switzerland",
        "japan": "Japan", "japanese": "Japan",
        "south korea": "South Korea", "korean": "South Korea",
        "france": "France", "french": "France",
        "finland": "Finland", "finnish": "Finland",
        "norway": "Norway", "norwegian": "Norway",
        "denmark": "Denmark", "danish": "Denmark",
        "austria": "Austria", "austrian": "Austria",
        "ireland": "Ireland", "irish": "Ireland",
        "new zealand": "New Zealand",
        "italy": "Italy", "italian": "Italy",
        "spain": "Spain", "spanish": "Spain",
        "belgium": "Belgium", "belgian": "Belgium",
    }

    for kw, country in country_keywords.items():
        if kw in text:
            opp.country = country
            break

    # Institution from domain
    domain = urlparse(url).netloc.lower()
    if ".edu" in domain or ".ac." in domain:
        # Extract institution name from domain
        parts = domain.replace("www.", "").split(".")
        if parts:
            opp.institution = EvidenceField(
                value=parts[0].replace("-", " ").title(),
                source_url=url,
                confidence=0.5,
                verified=False,
            )
