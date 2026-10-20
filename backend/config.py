"""
MeritMap configuration — loads .env and exposes typed settings.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ─── LLM ───
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
LLM_FAST_MODEL: str = os.getenv("LLM_FAST_MODEL", "gemini-2.0-flash")
LLM_STRONG_MODEL: str = os.getenv("LLM_STRONG_MODEL", "gemini-2.5-flash")

# ─── Search ───
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# ─── GitHub ───
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# ─── Exchange rates (fallback) ───
EXCHANGE_RATES: dict[str, float] = {
    "USD": float(os.getenv("DEFAULT_USD_TO_INR", "84.0")),
    "EUR": float(os.getenv("DEFAULT_EUR_TO_INR", "91.0")),
    "CAD": float(os.getenv("DEFAULT_CAD_TO_INR", "61.0")),
    "SGD": float(os.getenv("DEFAULT_SGD_TO_INR", "62.0")),
    "GBP": float(os.getenv("DEFAULT_GBP_TO_INR", "105.0")),
    "AUD": float(os.getenv("DEFAULT_AUD_TO_INR", "55.0")),
    "CHF": float(os.getenv("DEFAULT_CHF_TO_INR", "95.0")),
    "INR": 1.0,
}

# ─── Paths ───
RUNS_DIR = _PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)
SAMPLE_DATA_DIR = _PROJECT_ROOT / "sample_data"
FRONTEND_DIR = _PROJECT_ROOT / "frontend"

# ─── Server ───
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
