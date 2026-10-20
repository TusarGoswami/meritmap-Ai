"""
MeritMap Web Fetching Tools — fetch and extract content from web pages.
Uses httpx + trafilatura with BeautifulSoup fallback.
Includes retry with backoff and Wayback Machine fallback.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger("meritmap.tools.fetch")

TIMEOUT = 6.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchResult:
    """Result of a web page fetch."""
    def __init__(
        self,
        url: str,
        text: str = "",
        success: bool = False,
        source: str = "direct",  # "direct", "wayback", "snippet"
        fetched_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ):
        self.url = url
        self.text = text
        self.success = success
        self.source = source
        self.fetched_at = fetched_at or datetime.now()
        self.error = error


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.5, min=1, max=3),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    reraise=True,
)
async def _fetch_raw(url: str, timeout: float = TIMEOUT) -> str:
    """Fetch raw HTML from a URL with retry."""
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=HEADERS,
        follow_redirects=True,
        max_redirects=3,
        verify=False,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _extract_with_trafilatura(html: str, url: str) -> str:
    """Extract main content using trafilatura."""
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            url=url,
            include_links=True,
            include_tables=True,
            favor_precision=True,
        )
        return text or ""
    except Exception as e:
        logger.warning(f"trafilatura failed: {e}")
        return ""


def _extract_with_bs4(html: str) -> str:
    """Fallback extraction using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Get text from main content areas
        main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.body
        if main:
            text = main.get_text(separator="\n", strip=True)
            # Clean up excessive whitespace
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            return "\n".join(lines)
        return ""
    except Exception as e:
        logger.warning(f"BeautifulSoup failed: {e}")
        return ""


async def fetch_page(url: str) -> FetchResult:
    """
    Fetch and extract text content from a web page.
    Tries direct fetch -> trafilatura -> BS4 -> Wayback Machine.
    """
    if not url:
        return FetchResult(url="", error="Empty URL")

    # Try direct fetch
    try:
        html = await _fetch_raw(url)

        # Try trafilatura first
        text = _extract_with_trafilatura(html, url)
        if text and len(text) > 100:
            return FetchResult(url=url, text=text, success=True, source="direct")

        # Fallback to BS4
        text = _extract_with_bs4(html)
        if text and len(text) > 100:
            return FetchResult(url=url, text=text, success=True, source="direct")

        # HTML fetched but no meaningful content
        return FetchResult(
            url=url, text=text or html[:3000], success=True,
            source="direct",
        )

    except Exception as e:
        logger.warning(f"Direct fetch failed for {url}: {e}")

    # Try Wayback Machine
    try:
        wayback_text = await _fetch_wayback(url)
        if wayback_text:
            return FetchResult(
                url=url, text=wayback_text, success=True,
                source="wayback",
            )
    except Exception as e:
        logger.warning(f"Wayback fetch failed for {url}: {e}")

    return FetchResult(url=url, error=f"All fetch methods failed", success=False)


async def _fetch_wayback(url: str) -> str:
    """Try to fetch from Wayback Machine."""
    wayback_api = f"https://web.archive.org/web/2/{url}"
    try:
        html = await _fetch_raw(wayback_api, timeout=3.0)
        text = _extract_with_trafilatura(html, url)
        if text:
            return text
        return _extract_with_bs4(html)
    except Exception:
        return ""


async def fetch_page_with_links(url: str, follow_links: list[str] | None = None) -> dict:
    """
    Fetch a page and optionally follow specific links one level deep.

    Args:
        url: Main page URL
        follow_links: Keywords to match in link text (e.g., ["eligibility", "how to apply"])

    Returns:
        Dict with main_page and linked_pages content.
    """
    result = {"main_page": await fetch_page(url), "linked_pages": []}

    if not follow_links or not result["main_page"].success:
        return result

    # Parse links from the main page
    try:
        html = await _fetch_raw(url)
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        soup = BeautifulSoup(html, "html.parser")
        links_found = []

        for a in soup.find_all("a", href=True):
            link_text = a.get_text(strip=True).lower()
            for kw in follow_links:
                if kw.lower() in link_text:
                    full_url = urljoin(url, a["href"])
                    if full_url not in [url] + links_found:
                        links_found.append(full_url)
                    break

        # Fetch linked pages (max 3)
        for link_url in links_found[:3]:
            linked = await fetch_page(link_url)
            if linked.success:
                result["linked_pages"].append(linked)

    except Exception as e:
        logger.warning(f"Failed to follow links from {url}: {e}")

    return result
