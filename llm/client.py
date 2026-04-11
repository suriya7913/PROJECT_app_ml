"""
LegalKGent — LLM Client Wrappers
==================================
Unified client creation and JSON response parsing.
"""

import json
import re
from config import VLLM_BASE_URL, VLLM_MODEL, VLLM_TIMEOUT, LIGHTNING_API_KEY


def get_vllm_client():
    """Create an OpenAI-compatible client pointing at local vLLM server.
    
    vLLM exposes an OpenAI-compatible API at /v1/chat/completions,
    so we use the standard openai SDK to connect to it.
    """
    from openai import OpenAI
    return OpenAI(
        base_url=VLLM_BASE_URL,
        api_key="EMPTY",  # vLLM doesn't require a real key
        timeout=VLLM_TIMEOUT,
    )


def get_query_client():
    """Create an OpenAI client for the query agent hitting Lightning AI."""
    from openai import OpenAI
    return OpenAI(
        base_url="https://lightning.ai/api/v1/",
        api_key=LIGHTNING_API_KEY,
    )


def parse_llm_json(raw_text: str) -> list[dict] | None:
    """
    Parse JSON from LLM output. Handles common quirks:
    - Raw JSON arrays
    - Markdown-wrapped code blocks (```json ... ```)
    - Wrapped in dict: {"relationships": [...]}
    - Single object → wrap in list

    Returns a list of dicts, or None if parsing fails.
    """
    text = raw_text.strip()

    # Try direct parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON array from markdown/text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Unwrap dict to list
    if isinstance(parsed, dict):
        items = (parsed.get("mutations") or parsed.get("relationships")
                 or parsed.get("results") or parsed.get("data") or [])
        if not items and "action" in parsed:
            items = [parsed]
        return items if isinstance(items, list) else None

    if isinstance(parsed, list):
        return parsed

    return None
