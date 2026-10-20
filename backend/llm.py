"""
MeritMap LLM Wrapper — provider-agnostic interface with JSON mode and retries.

Supports: anthropic, gemini, openai.
Swap via LLM_PROVIDER env var.
Two tiers: fast (extraction) and strong (reasoning/matching).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.config import (
    LLM_PROVIDER, ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY,
    LLM_FAST_MODEL, LLM_STRONG_MODEL,
)

logger = logging.getLogger("meritmap.llm")


class LLMError(Exception):
    """Raised when all LLM retries are exhausted."""


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from text that may contain markdown code fences or extra text."""
    # Try to find JSON in code fences first
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try to find a JSON object or array
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
    return text.strip()


import asyncio
import httpx

# ─── Provider implementations ─────────────────────────────────────────────

async def _call_gemini(prompt: str, system: str, model: str, json_mode: bool) -> str:
    """Call Google Gemini (google-genai SDK)."""
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set. Please set GEMINI_API_KEY in .env.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    config_kwargs: dict[str, Any] = {}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    config = types.GenerateContentConfig(
        system_instruction=system,
        **config_kwargs,
    )

    def _sync_generate():
        res = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return res.text or ""

    return await asyncio.to_thread(_sync_generate)


async def _call_anthropic(prompt: str, system: str, model: str, json_mode: bool) -> str:
    """Call Anthropic Claude."""
    if not ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set. Please set ANTHROPIC_API_KEY in .env.")
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    if json_mode:
        system += "\n\nYou MUST respond with valid JSON only. No markdown fences, no extra text."

    response = await client.messages.create(
        model=model,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def _call_openai(prompt: str, system: str, model: str, json_mode: bool) -> str:
    """Call OpenAI."""
    if not OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY is not set. Please set OPENAI_API_KEY in .env.")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=8192,
        **kwargs,
    )
    return response.choices[0].message.content or ""


_PROVIDERS = {
    "gemini": _call_gemini,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
}


# ─── Public API ────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((httpx.HTTPError, TimeoutError, ConnectionError)),
    reraise=True,
)
async def llm_call(
    prompt: str,
    system: str = "You are a helpful assistant.",
    tier: str = "fast",  # "fast" or "strong"
    json_mode: bool = False,
    provider: str | None = None,
) -> str:
    """
    Call the configured LLM provider. Retries up to 3 times with exponential backoff.

    Args:
        prompt: The user prompt.
        system: System instruction.
        tier: "fast" for extraction tasks, "strong" for reasoning tasks.
        json_mode: If True, request JSON-only output.
        provider: Override the default provider.

    Returns:
        The LLM response text.
    """
    prov = provider or LLM_PROVIDER
    model = LLM_FAST_MODEL if tier == "fast" else LLM_STRONG_MODEL
    call_fn = _PROVIDERS.get(prov)
    if not call_fn:
        raise LLMError(f"Unknown LLM provider: {prov}. Use: {list(_PROVIDERS.keys())}")

    logger.info(f"LLM call: provider={prov}, model={model}, tier={tier}, json_mode={json_mode}")
    result = await call_fn(prompt, system, model, json_mode)
    return result


async def llm_structured(
    prompt: str,
    response_model: type[BaseModel],
    system: str = "You are a helpful assistant.",
    tier: str = "fast",
    provider: str | None = None,
    max_retries: int = 2,
) -> BaseModel:
    """
    Call LLM and parse response into a Pydantic model.
    If parsing fails, re-prompts with the validation error (up to max_retries).
    """
    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
    full_system = (
        f"{system}\n\n"
        f"You MUST respond with valid JSON matching this schema:\n{schema_json}\n"
        f"No markdown fences. No extra text. Only the JSON object."
    )

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0 and last_error:
                prompt_with_error = (
                    f"{prompt}\n\n"
                    f"PREVIOUS ATTEMPT FAILED with validation error:\n{last_error}\n"
                    f"Please fix the JSON and try again."
                )
            else:
                prompt_with_error = prompt

            raw = await llm_call(
                prompt_with_error, full_system, tier=tier,
                json_mode=True, provider=provider,
            )

            # Extract JSON from potential markdown wrapping
            json_str = _extract_json_from_text(raw)
            data = json.loads(json_str)
            return response_model.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)
            logger.warning(f"LLM structured parse attempt {attempt + 1} failed: {last_error}")
            if attempt == max_retries:
                raise LLMError(
                    f"Failed to get valid {response_model.__name__} after {max_retries + 1} attempts. "
                    f"Last error: {last_error}"
                )

    raise LLMError("Unreachable")  # pragma: no cover


async def llm_generate(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    temperature: float = 0.5,
    use_strong: bool = False,
) -> str:
    """Convenience wrapper around llm_call."""
    tier = "strong" if use_strong else "fast"
    return await llm_call(prompt, system=system_prompt, tier=tier)
