"""
LegalKGent — LLM Client Wrappers
==================================
Unified client creation and JSON response parsing.
"""

import json
import re
from config import GROQ_API_KEY, MISTRAL_API_KEY, MISTRAL_MODEL
from groq import Groq


def get_groq_client(timeout: float = 120.0):
    """Create a client pointing at Groq's blazing fast API.
    
    Args:
        timeout: Request timeout in seconds.
    """
    return Groq(
        api_key=GROQ_API_KEY,
        timeout=timeout,
        max_retries=2,
    )


def get_mistral_client():
    """Create a Mistral client for the query agent."""
    from mistralai import Mistral
    return Mistral(api_key=MISTRAL_API_KEY)


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
