"""
coding_agent.py — Coding Agent for the multi-agent FlaskBB pipeline.

Responsibilities
----------------
1. Double-check that Architect's scoped_files are valid before spending
   any LLM tokens (validate_scoped_files).
2. Read the current content of every existing scoped file so the LLM has
   full context (read_scoped_file_contents).
3. Call the Bob/LLM API and obtain a unified diff (call_bob_coding).
4. Sanity-check that the returned string looks like a real diff
   (validate_diff_format).
5. Assemble the output task object and append the correct history entries.

Architect-error convention
--------------------------
If validate_scoped_files finds that a path in scoped_files does not exist
on disk AND is not declared as ``NEW FILE: <path>`` in plan, that is an
Architect error — not a Coding Agent failure:

  history[-2] = { agent: "architect_agent", success: false, ... }
  history[-1] = { agent: "coding_agent",    success: null,  ... }
  status       = "blocked"
  current_agent = "manager_agent"

The Coding Agent's own failures (bad LLM response, invalid diff) use:
  history[-1] = { agent: "coding_agent", success: false, ... }

Import / reuse
--------------
parse_new_file_lines is imported from agents.architect_agent.architect_agent
so NEW FILE parsing logic is byte-for-byte identical in both agents.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

# Reuse Architect Agent's canonical NEW FILE parser — do NOT duplicate.
from agents.architect_agent.architect_agent import parse_new_file_lines

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bob API integration
# ---------------------------------------------------------------------------
# All config read at call time inside _http_post_bob() so .env loaded after
# import is always picked up.  Module-level constants removed to prevent stale
# values if the process sets env vars after import.

# Path to this agent's system prompt, relative to this file.
_SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")


def _load_system_prompt() -> str:
    with open(_SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_scoped_files(
    scoped_files: list[str],
    plan: str | None,
    repo_path: str,
) -> tuple[bool, str]:
    """
    Double-check every path in *scoped_files* before the LLM is called.

    For each path:
    - If it exists on disk at ``<repo_path>/<path>``: OK.
    - If it does NOT exist on disk but IS declared in ``parse_new_file_lines(plan)``:
      OK — Coding Agent will create it.
    - If it does NOT exist on disk and is NOT in the NEW FILE set: ARCHITECT ERROR.

    Parameters
    ----------
    scoped_files : list of relative paths from the task object.
    plan         : task["plan"] — may be None.
    repo_path    : absolute path to the repository root.

    Returns
    -------
    (True, "")                  — all paths are valid.
    (False, "<error message>")  — first invalid path found, with reason.
    """
    new_file_set = parse_new_file_lines(plan)
    repo_root = os.path.realpath(repo_path) if repo_path else None

    for raw_path in scoped_files:
        norm = _normalise(raw_path)
        if not norm or os.path.isabs(norm) or ".." in norm.split("/"):
            return False, f"scoped_files path '{norm}' resolves outside repository root"
        abs_path = os.path.realpath(os.path.join(repo_path, norm)) if repo_path else norm
        if repo_root and os.path.commonpath([repo_root, abs_path]) != repo_root:
            return False, f"scoped_files path '{norm}' resolves outside repository root"
        if os.path.exists(abs_path):
            continue  # exists on disk — fine
        if norm in new_file_set:
            continue  # declared as new — fine
        return (
            False,
            f"scoped_files path '{norm}' not found on disk and not marked NEW FILE in plan",
        )

    return True, ""


def read_scoped_file_contents(
    scoped_files: list[str],
    plan: str | None,
    repo_path: str,
) -> dict[str, str]:
    """
    Read the current content of every *existing* scoped file.

    Files declared as ``NEW FILE`` in *plan* are skipped (they don't exist
    yet and will be created by the diff).

    Parameters
    ----------
    scoped_files : list of relative paths.
    plan         : task["plan"].
    repo_path    : absolute path to the repository root.

    Returns
    -------
    dict mapping normalised relative path → file content string.
    Files that cannot be read are mapped to an empty string with a warning.
    """
    new_file_set = parse_new_file_lines(plan)
    contents: dict[str, str] = {}

    for raw_path in scoped_files:
        norm = _normalise(raw_path)
        if norm in new_file_set:
            continue  # new file — no content to read
        abs_path = os.path.join(repo_path, norm) if repo_path else norm
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                contents[norm] = fh.read()
        except OSError as exc:
            logger.warning("Could not read scoped file %r: %s", abs_path, exc)
            contents[norm] = ""

    return contents


def validate_diff_format(diff_text: str) -> bool:
    """
    Sanity-check that *diff_text* looks like a real unified diff.

    Requires all three markers to be present:
    - At least one ``---`` header line
    - At least one ``+++`` header line
    - At least one ``@@`` hunk marker

    Returns True if the string passes, False otherwise.
    """
    if not diff_text or not diff_text.strip():
        return False
    has_minus = bool(re.search(r"^---[ \t]", diff_text, re.MULTILINE))
    has_plus = bool(re.search(r"^\+\+\+[ \t]", diff_text, re.MULTILINE))
    has_hunk = "@@" in diff_text
    return has_minus and has_plus and has_hunk


def call_bob_coding(
    plan: str,
    scoped_files: list[str],
    acceptance_criteria: list[str],
    file_contents: dict[str, str],
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """
    Call the Bob/LLM API to generate a unified diff implementing *plan*.

    The model is instructed (via system_prompt.md) to return only valid JSON:
        { "code_diff": "<unified diff text>" }

    Parameters
    ----------
    plan                : The Architect's implementation plan.
    scoped_files        : List of file paths the diff must be limited to.
    acceptance_criteria : PM's acceptance criteria for the feature.
    file_contents       : dict of existing file contents keyed by path.
    system_prompt       : Override system prompt (used in tests).

    Returns
    -------
    Parsed JSON dict, e.g. {"code_diff": "--- a/flaskbb/..."}

    Raises
    ------
    RuntimeError  — if the API call fails or returns non-JSON.
    ValueError    — if the returned JSON is missing the "code_diff" key.
    """
    if system_prompt is None:
        system_prompt = _load_system_prompt()

    # Build the user message passed to the model.
    user_message = _build_user_message(
        plan, scoped_files, acceptance_criteria, file_contents
    )

    raw_response = _http_post_bob(system_prompt, user_message)

    # Strip markdown fences if the model wrapped the JSON anyway.
    cleaned = _strip_fences(raw_response)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # The model hit the token limit mid-JSON. The response looks like:
        #   { "code_diff": "--- a/...\n+++ b/...\n@@...\n+line1\n+line2..." (truncated)
        # Extract the diff text directly via regex rather than giving up.
        result = _extract_code_diff_fallback(cleaned)
        if result is None:
            raise RuntimeError(
                f"Coding Agent: Bob response was not valid JSON and no "
                f"'code_diff' value could be extracted. "
                f"Response (truncated): {cleaned[:300]!r}"
            )
        return result

    if "code_diff" not in result:
        raise ValueError(
            f"Coding Agent: Bob response JSON missing 'code_diff' key. "
            f"Keys returned: {list(result.keys())}"
        )

    return result


def run_coding_agent(task: dict, repo_path: str = "") -> dict:
    """
    Main entry point — run the Coding Agent on *task*.

    Steps
    -----
    a. validate_scoped_files — before any LLM call.
    b. If invalid (Architect error): append two history entries, block, return.
    c. If valid: read file contents, call Bob, validate diff format.
    d. Good diff → set code_diff, route to testing_agent, success:true.
    e. Bad diff  → block, coding_agent success:false.
    f. Return updated task (always a new dict — does not mutate input).

    Parameters
    ----------
    task      : Current AgentTaskObject dict.
    repo_path : Absolute path to repo root.  Defaults to PIPELINE_REPO_PATH env var.

    Returns
    -------
    Updated AgentTaskObject dict.
    """
    repo_path = repo_path or os.environ.get("PIPELINE_REPO_PATH", "")
    task = dict(task)  # shallow copy — we replace list fields explicitly
    task["history"] = list(task.get("history", []))

    scoped_files: list[str] = task.get("scoped_files", [])
    plan: str | None = task.get("plan")
    acceptance_criteria: list[str] = task.get("acceptance_criteria", [])

    # ------------------------------------------------------------------
    # Step a — double-check scoped_files validity
    # ------------------------------------------------------------------
    valid, error_msg = validate_scoped_files(scoped_files, plan, repo_path)

    if not valid:
        # Step b — Architect error: append TWO history entries
        ts = _utcnow()
        task["history"].append({
            "agent": "architect_agent",
            "output_summary": (
                f"architect error: {error_msg}"
            ),
            "timestamp": ts,
            "success": False,
        })
        task["history"].append({
            "agent": "coding_agent",
            "output_summary": f"skipped: {error_msg}",
            "timestamp": ts,
            "success": None,  # null — never attempted
        })
        task["status"] = "blocked"
        task["current_agent"] = "manager_agent"
        return task

    # ------------------------------------------------------------------
    # Step c — read existing file contents, call Bob
    # ------------------------------------------------------------------
    file_contents = read_scoped_file_contents(scoped_files, plan, repo_path)

    try:
        bob_result = call_bob_coding(
            plan=plan or "",
            scoped_files=scoped_files,
            acceptance_criteria=acceptance_criteria,
            file_contents=file_contents,
        )
        diff_text: str = bob_result.get("code_diff", "")
    except Exception as exc:
        err_str = str(exc)
        if "401" in err_str or "authentication_token_not_valid" in err_str:
            logger.warning("Coding Agent LLM auth failed — skipping code generation.")
            diff_text = ""
        else:
            logger.error("Coding Agent Bob call failed: %s", exc)
            task["history"].append({
                "agent": "coding_agent",
                "output_summary": f"coding agent error: {exc}",
                "timestamp": _utcnow(),
                "success": False,
            })
            task["status"] = "blocked"
            task["current_agent"] = "manager_agent"
            task["code_diff"] = None
            return task

    # ------------------------------------------------------------------
    # Step c continued — validate diff format
    # ------------------------------------------------------------------
    if not validate_diff_format(diff_text):
        # Step e — invalid diff is a Coding Agent failure
        task["history"].append({
            "agent": "coding_agent",
            "output_summary": (
                "coding agent error: Bob returned an invalid or empty unified diff"
            ),
            "timestamp": _utcnow(),
            "success": False,
        })
        task["status"] = "blocked"
        task["current_agent"] = "manager_agent"
        task["code_diff"] = None
        return task

    # ------------------------------------------------------------------
    # Step d — success
    # ------------------------------------------------------------------
    task["code_diff"] = diff_text
    task["status"] = "in_progress"
    task["current_agent"] = "testing_agent"
    task["history"].append({
        "agent": "coding_agent",
        "output_summary": (
            f"code_diff generated for {len(scoped_files)} scoped file(s)"
        ),
        "timestamp": _utcnow(),
        "success": True,
    })
    return task


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_user_message(
    plan: str,
    scoped_files: list[str],
    acceptance_criteria: list[str],
    file_contents: dict[str, str],
) -> str:
    """
    Build the user-turn message sent to the Bob API.

    Layout:
        PLAN
        ACCEPTANCE CRITERIA
        SCOPED FILES (with content for existing files)
        INSTRUCTION: output only JSON { "code_diff": "..." }
    """
    criteria_text = "\n".join(f"- {c}" for c in acceptance_criteria)

    file_blocks: list[str] = []
    for raw_path in scoped_files:
        norm = _normalise(raw_path)
        if norm in file_contents:
            file_blocks.append(
                f"### FILE: {norm}\n```\n{file_contents[norm]}\n```"
            )
        else:
            file_blocks.append(
                f"### FILE: {norm}\n[NEW FILE — to be created]"
            )
    files_text = "\n\n".join(file_blocks)

    return (
        f"### PLAN\n{plan}\n\n"
        f"### ACCEPTANCE CRITERIA\n{criteria_text}\n\n"
        f"### SCOPED FILES\n{files_text}\n\n"
        "Produce a single unified diff implementing the plan above, touching ONLY "
        "the scoped files listed.  Output ONLY valid JSON: "
        '{ "code_diff": "<unified diff text>" }'
    )


def _http_post_bob(system_prompt: str, user_message: str) -> str:
    """
    POST to IBM Bob / watsonx chat endpoint.
    Returns the assistant message content string.
    Raises RuntimeError on non-200 status or network failure.
    """
    import sys as _sys
    import os as _os
    _repo_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
    if _repo_root not in _sys.path:
        _sys.path.insert(0, _repo_root)
    from orchestration.iam_auth import call_bob_chat

    return call_bob_chat(user_message, system_prompt=system_prompt, max_tokens=8192)


def _extract_code_diff_fallback(text: str) -> dict | None:
    """
    Recover a code_diff from a JSON response that was truncated by the token limit.

    The model always starts its response with:
        { "code_diff": "<diff text...
    and gets cut off before the closing quote/brace.

    Strategy:
    1. Find the opening of the "code_diff" string value.
    2. Grab everything after it as the diff body.
    3. Unescape JSON string escapes (\\n → newline, \\\\ → \\, etc.).
    4. Return {"code_diff": recovered_diff} only if the recovered text looks
       like a real unified diff (has --- / +++ / @@).

    Returns None if the pattern is not found or the recovered text is not
    a valid-looking diff.
    """
    # Match the opening of the code_diff value: "code_diff": "<content...
    # The content may be truncated before the closing quote.
    m = re.search(r'"code_diff"\s*:\s*"(.*)', text, re.DOTALL)
    if m:
        raw_value = m.group(1)
        raw_value = re.sub(r'\\$', '', raw_value)
        raw_value = raw_value.rstrip('"').rstrip()
        unescaped = (
            raw_value
            .replace('\\n', '\n')
            .replace('\\t', '\t')
            .replace('\\"', '"')
            .replace('\\\\', '\\')
        )
        has_minus = bool(re.search(r'^---[ \t]', unescaped, re.MULTILINE))
        has_plus = bool(re.search(r'^\+\+\+[ \t]', unescaped, re.MULTILINE))
        has_hunk = '@@' in unescaped
        if has_minus and has_plus and has_hunk:
            return {"code_diff": unescaped}

    # Also recover from markdown code fences if model didn't wrap in JSON
    for fence in re.finditer(r"```(?:diff|patch)?\s*\n([\s\S]*?)\n```", text):
        block = fence.group(1).strip()
        has_minus = bool(re.search(r'^---[ \t]', block, re.MULTILINE))
        has_plus = bool(re.search(r'^\+\+\+[ \t]', block, re.MULTILINE))
        has_hunk = '@@' in block
        if has_minus and has_plus and has_hunk:
            return {"code_diff": block}

    # Also check if raw text is already a unified diff
    has_minus = bool(re.search(r'^---[ \t]', text, re.MULTILINE))
    has_plus = bool(re.search(r'^\+\+\+[ \t]', text, re.MULTILINE))
    has_hunk = '@@' in text
    if has_minus and has_plus and has_hunk:
        return {"code_diff": text.strip()}

    return None


def _strip_fences(text: str) -> str:
    """Remove optional ```json ... ``` or ``` ... ``` markdown fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _normalise(path: str) -> str:
    """Normalise a relative path (strip leading ./, collapse //)."""
    path = path.strip()
    path = re.sub(r"/+", "/", path)
    while path.startswith("./"):
        path = path[2:]
    return path


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
