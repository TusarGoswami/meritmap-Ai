"""
MeritMap GitHub Tools — fetch public profile data from GitHub REST API.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.config import GITHUB_TOKEN
from backend.schemas.models import Project

logger = logging.getLogger("meritmap.tools.github")

GITHUB_API = "https://api.github.com"
TIMEOUT = 15.0


def _headers() -> dict[str, str]:
    """Build headers with optional auth token."""
    h = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "MeritMap-ScholarshipFinder/1.0",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


async def fetch_github_profile(username: str) -> dict[str, Any]:
    """
    Fetch GitHub user profile, repos, languages, and README summaries.
    Returns a dict with: repos, languages, total_stars, bio, etc.
    """
    if not username:
        return {}

    result = {
        "username": username,
        "repos": [],
        "languages": [],
        "total_stars": 0,
        "bio": "",
        "name": "",
        "location": "",
        "public_repos": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_headers()) as client:
            # Fetch user info
            user_resp = await client.get(f"{GITHUB_API}/users/{username}")
            if user_resp.status_code == 200:
                user = user_resp.json()
                result["name"] = user.get("name", "")
                result["bio"] = user.get("bio", "")
                result["location"] = user.get("location", "")
                result["public_repos"] = user.get("public_repos", 0)

            # Fetch repos (top 30 by stars)
            repos_resp = await client.get(
                f"{GITHUB_API}/users/{username}/repos",
                params={"sort": "stars", "direction": "desc", "per_page": 30},
            )
            if repos_resp.status_code == 200:
                repos_data = repos_resp.json()
                languages_set = set()

                for repo in repos_data:
                    if repo.get("fork"):
                        continue  # Skip forks

                    stars = repo.get("stargazers_count", 0)
                    result["total_stars"] += stars

                    lang = repo.get("language")
                    if lang:
                        languages_set.add(lang)

                    result["repos"].append(Project(
                        name=repo.get("name", ""),
                        description=repo.get("description", ""),
                        technologies=[lang] if lang else [],
                        url=repo.get("html_url", ""),
                        stars=stars,
                    ))

                result["languages"] = sorted(languages_set)

            # Fetch README of top repos for project descriptions
            for repo in result["repos"][:5]:  # Top 5 only
                try:
                    readme_resp = await client.get(
                        f"{GITHUB_API}/repos/{username}/{repo.name}/readme",
                        headers={"Accept": "application/vnd.github.v3.raw"},
                    )
                    if readme_resp.status_code == 200:
                        readme_text = readme_resp.text[:1000]  # First 1000 chars
                        if readme_text and not repo.description:
                            # Extract first meaningful line as description
                            lines = [l.strip() for l in readme_text.split("\n") if l.strip() and not l.startswith("#")]
                            if lines:
                                repo.description = lines[0][:200]
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"GitHub API error for {username}: {e}")

    return result
