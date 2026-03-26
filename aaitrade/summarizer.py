"""Summarizer — uses HuggingFace Inference API to condense large tool outputs.

When tool results (news, web search) exceed a threshold, this module
summarizes them before returning to Claude, saving context window tokens.
Session memory compression is handled separately by Claude Haiku.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_HF_API_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
_HF_TOKEN: str | None = None
_SUMMARIZE_THRESHOLD = 800  # chars — summarize if tool output exceeds this


def init_summarizer(token: str | None = None):
    """Initialize the HuggingFace summarizer with an API token."""
    global _HF_TOKEN
    _HF_TOKEN = token or os.environ.get("HF_API_TOKEN", "")
    if _HF_TOKEN:
        logger.info("HuggingFace summarizer initialized")
    else:
        logger.info("HuggingFace summarizer not configured (no HF_API_TOKEN)")


def summarize_text(text: str, max_length: int = 200, min_length: int = 50) -> str:
    """Summarize text using HuggingFace Inference API.

    Falls back to truncation if the API call fails or isn't configured.
    """
    if not _HF_TOKEN:
        return _truncate(text, max_length * 3)

    if len(text) < _SUMMARIZE_THRESHOLD:
        return text

    try:
        response = requests.post(
            _HF_API_URL,
            headers={"Authorization": f"Bearer {_HF_TOKEN}"},
            json={
                "inputs": text[:3000],  # API input limit
                "parameters": {
                    "max_length": max_length,
                    "min_length": min_length,
                    "do_sample": False,
                },
            },
            timeout=15,
        )

        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and result:
                return result[0].get("summary_text", _truncate(text, max_length * 3))
            return _truncate(text, max_length * 3)
        else:
            logger.warning(f"HuggingFace API returned {response.status_code}: {response.text[:200]}")
            return _truncate(text, max_length * 3)

    except Exception as e:
        logger.warning(f"HuggingFace summarization failed: {e}")
        return _truncate(text, max_length * 3)


def maybe_summarize_tool_result(tool_name: str, result_str: str) -> str:
    """Conditionally summarize a tool result if it's too long.

    Only summarizes text-heavy tools (news, search). Leaves numeric/structured
    data (prices, portfolio, indicators) untouched.
    """
    SUMMARIZABLE_TOOLS = {"get_stock_news", "get_sector_news", "get_macro_news", "search_web"}

    if tool_name not in SUMMARIZABLE_TOOLS:
        return result_str

    if len(result_str) <= _SUMMARIZE_THRESHOLD:
        return result_str

    summary = summarize_text(result_str)
    logger.debug(f"Summarized {tool_name} output: {len(result_str)} → {len(summary)} chars")
    return summary


def _truncate(text: str, max_chars: int) -> str:
    """Simple truncation fallback."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
