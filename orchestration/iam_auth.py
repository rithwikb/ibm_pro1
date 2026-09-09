"""
iam_auth.py — LLM API helper for the AI Dev Team pipeline.

Migrated from IBM watsonx/Bob to Groq Cloud API.
All agents import `call_bob_chat` from this module — the function name
is kept for backward compatibility so zero import changes are needed.

Usage:
    from orchestration.iam_auth import call_bob_chat
    result = call_bob_chat("Generate acceptance criteria for ...")
"""

from __future__ import annotations

import os
from groq import Groq

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


class LLMCallError(Exception):
    """Raised when the LLM API call fails.

    Circuit breaker and agent wrappers catch this to route to fallback logic.
    """


def call_bob_chat(
    prompt_or_messages: str | list[dict],
    system_prompt: str | None = None,
    max_tokens: int = 1500,
    model_override: str | None = None,
    timeout: int = 60,
) -> str:
    """
    Execute a chat completion call via the Groq API.

    Parameters
    ----------
    prompt_or_messages : A plain string (treated as user message) or a list of
                         OpenAI-style message dicts.
    system_prompt      : Optional system prompt prepended to the messages.
    max_tokens         : Maximum tokens in the completion response.
    model_override     : Override the default model for this call.
    timeout            : Request timeout in seconds.

    Returns
    -------
    The assistant's response text (stripped).

    Raises
    ------
    LLMCallError
        On any failure (missing key, network error, API error, empty response).
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise LLMCallError(
            "GROQ_API_KEY is not set. Add it to your .env file.\n"
            "Get your API key from: https://console.groq.com"
        )

    model = model_override or os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
    max_tokens = min(max_tokens, 900)  # Stay within Groq free-tier OTPM limit

    # Build messages list
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if isinstance(prompt_or_messages, str):
        messages.append({"role": "user", "content": prompt_or_messages})
    else:
        messages.extend(prompt_or_messages)

    try:
        client = Groq(api_key=api_key, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        raise LLMCallError(f"Groq API call failed: {str(exc)[:300]}") from exc

    # Extract content
    try:
        content = response.choices[0].message.content
    except (IndexError, AttributeError) as exc:
        raise LLMCallError(
            f"Groq API response missing content: {str(response)[:200]}"
        ) from exc

    if not content or not content.strip():
        raise LLMCallError("Groq API returned empty content.")

    return content.strip()
