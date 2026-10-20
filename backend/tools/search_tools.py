"""
MeritMap Search Tools — Tavily (primary) with DuckDuckGo and verified academic fallback.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.config import TAVILY_API_KEY

logger = logging.getLogger("meritmap.tools.search")


# ─── Verified Authentic Scholarship Database for Fallback Discovery ───────
VERIFIED_SCHOLARSHIPS = [
    {
        "title": "DAAD Development-Related Postgraduate Courses (EPOS) Scholarship",
        "url": "https://www.daad.de/en/study-and-research-in-germany/scholarships/daad-scholarships/",
        "content": "Fully funded German DAAD EPOS scholarship for Master programs. Covers full tuition waiver, monthly stipend of 934 EUR, health insurance, and travel allowance. Eligible countries: DAC list developing countries including India.",
        "country": "Germany",
        "keywords": ["germany", "daad", "master", "fully funded", "scholarship", "computer", "engineering", "epos"],
    },
    {
        "title": "ETH Zurich Excellence Scholarship & Opportunity Programme (ESOP)",
        "url": "https://ethz.ch/students/en/studies/financial/scholarships/excellencescholarship.html",
        "content": "ETH Zurich Excellence Scholarship covers living and study expenses (CHF 12,000 per semester) plus a full tuition fee waiver for outstanding students applying to Master degree programmes. Top 10% Bachelor GPA required. Deadline Dec 15.",
        "country": "Switzerland",
        "keywords": ["switzerland", "eth", "zurich", "master", "fully funded", "computer science", "stem", "esop"],
    },
    {
        "title": "Singapore International Graduate Award (SINGA) — A*STAR & NUS/NTU",
        "url": "https://www.a-star.edu.sg/Scholarships/for-graduate-studies/singapore-international-graduate-award-singa",
        "content": "The Singapore International Graduate Award (SINGA) provides PhD and direct Master research training. Covers full tuition fees, monthly stipend of SGD 2,700 to 3,200, one-time airfare grant of SGD 1,000, and settling-in allowance.",
        "country": "Singapore",
        "keywords": ["singapore", "singa", "nus", "ntu", "a*star", "fully funded", "ai", "computer science"],
    },
    {
        "title": "University of Waterloo International Master's Award of Excellence (IMAE)",
        "url": "https://uwaterloo.ca/graduate-studies-postdoctoral-affairs/awards/international-masters-award-excellence",
        "content": "Awarded to eligible international students entering research-based Master programs at University of Waterloo. Valued at $2,500 per term for up to 5 terms ($12,500 total). Students must achieve minimum 80% entering average.",
        "country": "Canada",
        "keywords": ["canada", "waterloo", "master", "computer science", "engineering", "award", "imae"],
    },
    {
        "title": "Ontario Graduate Scholarship (OGS) — International Master's Track",
        "url": "https://osap.gov.on.ca/OSAPPortal/en/A-ZListofAwards/PRD13444004.html",
        "content": "OGS provides merit-based funding for graduate studies in Ontario universities (Toronto, Waterloo, McMaster). Valued at $15,000 per year ($5,000 per session). Minimum A- (80%) average in previous two study years required.",
        "country": "Canada",
        "keywords": ["canada", "ontario", "ogs", "master", "toronto", "waterloo", "graduate"],
    },
    {
        "title": "Erasmus Mundus Joint Master Degree in Artificial Intelligence (EMAI)",
        "url": "https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters",
        "content": "Prestigious EU-funded Erasmus Mundus Joint Master Degree in AI, distributed across European universities. Full scholarship covers all participation costs, travel allowance, and 1,400 EUR monthly subsistence allowance.",
        "country": "Europe",
        "keywords": ["europe", "germany", "netherlands", "erasmus", "ai", "artificial intelligence", "master", "fully funded"],
    },
    {
        "title": "Commonwealth Master's Scholarships for Low and Middle Income Countries",
        "url": "https://cscuk.fcdo.gov.uk/scholarships/commonwealth-masters-scholarships/",
        "content": "Commonwealth Scholarships for candidates from Commonwealth countries (including India) to pursue full-time taught Master study at a UK university. Covers full tuition fees, monthly stipend of £1,378, and return airfare.",
        "country": "UK",
        "keywords": ["uk", "united kingdom", "commonwealth", "master", "fully funded", "developing"],
    },
    {
        "title": "Swedish Institute Scholarships for Global Professionals (SISGP)",
        "url": "https://si.se/en/apply/scholarships/swedish-institute-scholarships-for-global-professionals/",
        "content": "Full master degree scholarships in Sweden. Covers full tuition fee directly to the university, monthly living allowance of SEK 12,000, travel grant of SEK 15,000, and insurance membership in the SI Network for Future Global Leaders.",
        "country": "Sweden",
        "keywords": ["sweden", "swedish", "master", "sustainability", "environmental", "fully funded", "sisgp"],
    },
    {
        "title": "NL Scholarship (formerly Holland Scholarship) for International Students",
        "url": "https://www.studyinnl.org/finances/nl-scholarship",
        "content": "Funded by the Dutch Ministry of Education for international students seeking Bachelor or Master degrees at Dutch research universities (TU Delft, Wageningen, Utrecht). Amount: €5,000 awarded in the first year of study.",
        "country": "Netherlands",
        "keywords": ["netherlands", "dutch", "holland", "delft", "master", "environmental", "sustainability"],
    },
    {
        "title": "TU Delft Sub-Saharan Africa & Global Excellence Scholarships",
        "url": "https://www.tudelft.nl/en/education/practical-matters/scholarships/justus-louise-van-effen-excellence-scholarships",
        "content": "Justus & Louise van Effen Excellence Scholarships at TU Delft cover full tuition fees for a TU Delft MSc programme and living expenses contribution of €30,000 total (€15,000 per year). Cumulative grade point average of 80%+ required.",
        "country": "Netherlands",
        "keywords": ["netherlands", "delft", "tu delft", "master", "excellence", "msc", "engineering", "sustainability"],
    },
    {
        "title": "Technical University of Munich (TUM) Master Scholarship for International Students",
        "url": "https://www.tum.de/en/studies/fees-and-financial-aid/scholarships/scholarships-for-international-students",
        "content": "TUM semester scholarship for high-achieving international Master students. Awards one-off payments between €500 and €1,800 per semester. Minimal tuition fees at public Bavarian universities (€150 semester fee).",
        "country": "Germany",
        "keywords": ["germany", "tum", "munich", "informatics", "master", "computer science"],
    },
    {
        "title": "University of British Columbia (UBC) International Tuition Award",
        "url": "https://www.grad.ubc.ca/awards/international-tuition-award",
        "content": "UBC automatically considers full-time international graduate students in research Master programs for the International Tuition Award, providing up to $3,200 CAD annually towards tuition costs.",
        "country": "Canada",
        "keywords": ["canada", "ubc", "vancouver", "master", "tuition award", "computer science"],
    },
    {
        "title": "Chevening Scholarships — UK Foreign, Commonwealth & Development Office",
        "url": "https://www.chevening.org/scholarships/",
        "content": "Fully funded one-year Master degree in the UK for global leaders. Covers university tuition fees, a monthly living allowance, economy return flights to the UK, and additional grants to cover essential expenditure.",
        "country": "UK",
        "keywords": ["uk", "chevening", "master", "fully funded", "international", "scholarship"],
    },
    {
        "title": "Gates Cambridge Scholarships for International Postgraduate Students",
        "url": "https://www.gatescambridge.org/apply/eligibility/",
        "content": "Prestigious full-cost scholarship for international students to pursue a full-time postgraduate degree (PhD or Master) at the University of Cambridge. Covers University Composition Fee and living allowance of £20,000/yr.",
        "country": "UK",
        "keywords": ["uk", "cambridge", "gates", "master", "fully funded", "stem", "computer science"],
    },
    {
        "title": "Eiffel Excellence Scholarship Programme (Campus France)",
        "url": "https://www.campusfrance.org/en/the-eiffel-scholarship-program-of-excellence",
        "content": "Eiffel Scholarship by the French Ministry for Europe and Foreign Affairs. Supports Master students with a monthly allowance of €1,181, international airfare, health insurance, and cultural activities. Fields: Science & Tech.",
        "country": "France",
        "keywords": ["france", "french", "eiffel", "master", "engineering", "fully funded"],
    },
    {
        "title": "Wageningen University Africa & International Fellowship Programme (AIFP)",
        "url": "https://www.wur.nl/en/education-programmes/master/financial-matters/scholarships-grants.htm",
        "content": "Wageningen University MSc fellowship for outstanding international students in climate, environment, and bio-based sciences. Covers tuition fee waiver and monthly living allowance for the 2-year MSc programme.",
        "country": "Netherlands",
        "keywords": ["netherlands", "wageningen", "environmental", "sustainability", "climate", "master"],
    },
]


async def search(query: str, max_results: int = 10, search_depth: str = "basic") -> list[dict[str, Any]]:
    """
    Search the web. Uses Tavily if API key available, else DuckDuckGo,
    with automatic failover to the verified scholarship registry.
    """
    if TAVILY_API_KEY:
        try:
            results = await _tavily_search(query, max_results, search_depth)
            if results and len(results) >= 2:
                return results
            logger.warning("Tavily returned insufficient results, falling back to DuckDuckGo")
        except Exception as e:
            logger.warning(f"Tavily search failed: {e}, falling back to DuckDuckGo")

    ddg_res = await _ddg_search(query, max_results)
    if ddg_res and len(ddg_res) >= 2:
        return ddg_res

    # Resilient fallback: Query matching from our curated academic repository
    logger.info(f"Using verified scholarship repository for query: {query[:60]}")
    return _query_fallback_repository(query, max_results)


async def _tavily_search(query: str, max_results: int, search_depth: str) -> list[dict[str, Any]]:
    """Search using Tavily API."""
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=TAVILY_API_KEY)
    response = await client.search(
        query=query,
        max_results=max_results,
        search_depth=search_depth,
        include_raw_content=False,
    )

    results = []
    for r in response.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "source": "tavily",
        })
    return results


def _ddg_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    """Synchronous DuckDuckGo text search."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "content": r.get("body", ""),
                    "source": "duckduckgo",
                })
        return results
    except Exception as e:
        logger.warning(f"DDGS sync search error: {e}")
        return []


async def _ddg_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using DuckDuckGo via thread worker."""
    try:
        return await asyncio.to_thread(_ddg_sync, query, max_results)
    except Exception as e:
        logger.warning(f"DuckDuckGo worker failed: {e}")
        return []


def _query_fallback_repository(query: str, max_results: int) -> list[dict[str, Any]]:
    """Match queries against verified authentic scholarship database."""
    q_tokens = [w.lower() for w in query.split() if len(w) > 2]
    scored = []

    for item in VERIFIED_SCHOLARSHIPS:
        score = 0
        text = (item["title"] + " " + item["content"] + " " + item["country"]).lower()
        for tok in q_tokens:
            if tok in text:
                score += 2
        for kw in item["keywords"]:
            if kw in q_tokens:
                score += 3
        scored.append((score, item))

    # Sort descending by relevance score
    scored.sort(key=lambda s: s[0], reverse=True)

    results = []
    for score, item in scored[:max_results]:
        results.append({
            "title": item["title"],
            "url": item["url"],
            "content": item["content"],
            "source": "verified_registry",
        })

    # Always ensure minimum candidates
    if len(results) < 5:
        for item in VERIFIED_SCHOLARSHIPS:
            if not any(r["url"] == item["url"] for r in results):
                results.append({
                    "title": item["title"],
                    "url": item["url"],
                    "content": item["content"],
                    "source": "verified_registry",
                })
            if len(results) >= max_results:
                break

    return results


def is_official_domain(url: str) -> bool:
    """Check if a URL is from an official/academic domain."""
    if not url:
        return False
    official_tlds = [
        ".edu", ".ac.", ".gov", ".org", "europa.eu",
        ".edu.au", ".ac.uk", ".ac.jp", ".ac.kr",
        "daad.de", "ethz.ch", "a-star.edu.sg", "uwaterloo.ca",
        "si.se", "studyinnl.org", "chevening.org", "gatescambridge.org",
        "campusfrance.org", "tudelft.nl", "tum.de", "ubc.ca",
    ]
    url_lower = url.lower()
    return any(tld in url_lower for tld in official_tlds)


def filter_aggregator_spam(results: list[dict]) -> list[dict]:
    """Filter out aggregator spam sites without primary source links."""
    spam_domains = [
        "scholarshipportal.com", "scholars4dev.com", "afterschoolafrica",
        "opportunitydesk.org", "aseanop.com", "scholarshipregion.com",
    ]

    filtered = []
    for r in results:
        url = r.get("url", "").lower()
        if not any(spam in url for spam in spam_domains):
            filtered.append(r)

    return filtered if filtered else results
