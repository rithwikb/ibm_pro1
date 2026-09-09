"""
scaffold_agent.py — LLM-powered agent that takes a plain-English app description
and returns an ordered batch plan (list of ≤5-file groups).

Usage:
    from agents.scaffold_agent.scaffold_agent import plan_batches

    result = plan_batches("Build a Flask todo app with login and SQLite", "todo_app")
    # result = {"project_name": "todo_app", "description": "...", "batches": [[...], ...]}
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_AGENT_DIR, "..", ".."))

_MAX_FILES_PER_BATCH = 5

# ---------------------------------------------------------------------------
# LLM call (mirrors architect_agent pattern exactly)
# ---------------------------------------------------------------------------


def _call_watsonx(prompt: str) -> str:
    """
    Call the IBM Bob / watsonx chat API.
    Returns generated text string or empty string on error / stub mode.
    """
    _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from orchestration.iam_auth import call_bob_chat

    if not os.environ.get("GROQ_API_KEY", ""):
        return ""  # trigger stub fallback

    try:
        return call_bob_chat(prompt, max_tokens=1500)
    except Exception:
        return ""  # trigger stub fallback on error


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_batch_output(raw: str) -> list[list[str]]:
    """
    Parse LLM output into a list of batches.
    Each batch is a list of ≤5 file path strings.
    Raises ValueError on bad structure.
    """
    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    # Extract first JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Scaffold Agent returned non-JSON: {exc}. Raw: {raw[:200]!r}") from exc

    if "batches" not in data:
        raise ValueError(f"Scaffold Agent output missing 'batches' key. Got: {list(data.keys())}")

    batches = data["batches"]
    if not isinstance(batches, list) or not batches:
        raise ValueError(f"'batches' must be a non-empty list. Got: {type(batches)}")

    validated: list[list[str]] = []
    for i, batch in enumerate(batches):
        if not isinstance(batch, list):
            raise ValueError(f"Batch {i} is not a list: {batch!r}")
        clean_files = []
        for f in batch:
            p = str(f).strip()
            if not p:
                continue
            # Normalize slashes and strip leading separators
            p = p.replace("\\", "/").lstrip("/")
            # Strip any IBM / watsonx prefixes
            for prefix in ("ibm/", "watsonx/", "ibm_cloud/", "ibm-cloud/"):
                if p.lower().startswith(prefix):
                    p = p[len(prefix):].lstrip("/")
            if p:
                clean_files.append(p)

        if len(clean_files) > _MAX_FILES_PER_BATCH:
            print(
                f"[scaffold_agent] WARNING: Batch {i} has {len(clean_files)} files — "
                f"truncating to {_MAX_FILES_PER_BATCH}."
            )
            clean_files = clean_files[:_MAX_FILES_PER_BATCH]
        if clean_files:
            validated.append(clean_files)

    if not validated:
        raise ValueError("All batches were empty after validation.")

    return validated


# ---------------------------------------------------------------------------
# Stub fallback (when no API key)
# ---------------------------------------------------------------------------

def _stub_plan(project_name: str, description: str) -> dict:
    """Return a minimal hardcoded Flask app plan for stub/dev mode."""
    print("[scaffold_agent] No API key — using stub batch plan.")
    return {
        "project_name": project_name,
        "description": description,
        "batches": [
            ["app.py", "models.py", "config.py", "requirements.txt", "database.py"],
            ["auth/routes.py", "auth/forms.py", "main/routes.py"],
            ["templates/base.html", "templates/index.html", "templates/login.html"],
            ["tests/test_app.py", "tests/conftest.py"],
        ],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def plan_batches(description: str, project_name: str) -> dict:
    """
    Call the LLM to plan a full-app build as ordered batches of ≤5 files each.

    Returns a BuildPlan dict:
    {
        "project_name": str,
        "description": str,
        "batches": [[file1, file2, ...], [file3, file4, ...], ...]
    }
    Falls back to a stub plan if no API key is set.
    """
    # Load system prompt
    prompt_path = os.path.join(_AGENT_DIR, "system_prompt.md")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except OSError:
        system_prompt = "You are the Scaffold Agent. Return a JSON build plan."

    prompt = (
        f"{system_prompt}\n\n"
        f"## Input\n"
        f"App description: {description}\n"
        f"Project name: {project_name}\n\n"
        "Output only valid JSON. No prose, no explanation outside the JSON."
    )

    raw = _call_watsonx(prompt)

    if not raw:
        return _stub_plan(project_name, description)

    try:
        batches = _parse_batch_output(raw)
    except ValueError as exc:
        print(f"[scaffold_agent] Parse error: {exc} — using stub plan.")
        return _stub_plan(project_name, description)

    return {
        "project_name": project_name,
        "description": description,
        "batches": batches,
    }
