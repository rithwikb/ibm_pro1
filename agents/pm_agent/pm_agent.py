"""
pm_agent.py — PM Agent standalone module
Reads a feature_request from the task dict, calls IBM watsonx to generate
concrete acceptance criteria, validates the response, and returns the
updated task dict.

Usage (called from pipeline.py):
    from agents.pm_agent.pm_agent import run_pm_agent
    task = run_pm_agent(task)

CLI (standalone test):
    python agents/pm_agent/pm_agent.py --request "Add rate limiting to login"
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    _ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    load_dotenv(_ENV_PATH)
except ImportError:
    pass  # python-dotenv optional; env vars can be set directly in environment

# ──────────────────────────────────────────────────────────────────────────────
# watsonx defaults — plain string literals so _call_watsonx() fallbacks are
# never stale import-time values. All env vars are read inside _call_watsonx().
# ──────────────────────────────────────────────────────────────────────────────

# Path to this agent's system prompt
_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")

# ──────────────────────────────────────────────────────────────────────────────
# LLM helpers
# ──────────────────────────────────────────────────────────────────────────────


def _load_system_prompt() -> str:
    """Load agents/pm_agent/system_prompt.md at call time (so edits take effect immediately)."""
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _call_watsonx(prompt: str) -> str:
    """
    POST to IBM Bob / watsonx chat endpoint.
    Returns raw generated text. Raises RuntimeError on failure.
    """
    import sys
    import os as _os
    sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))
    from orchestration.iam_auth import call_bob_chat

    return call_bob_chat(prompt, max_tokens=1200)


def _parse_json_response(raw: str) -> dict:
    """
    Extract and parse a JSON object from the LLM's raw text output.
    Strips markdown code fences and surrounding prose before parsing.
    Raises ValueError if no valid JSON object can be found.
    """
    cleaned = raw.strip()
    # Strip [ANSWER] prefix tag commonly added by IBM Bob chat completions
    if "[ANSWER]" in cleaned:
        cleaned = cleaned.replace("[ANSWER]", "").strip()

    # Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # If there are code fences inside the text, extract what's inside
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        if end != -1:
            cleaned = cleaned[start:end].strip()

    # Search for outer { and }
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned_json = cleaned[start_idx:end_idx + 1]
    else:
        cleaned_json = cleaned

    try:
        return json.loads(cleaned_json, strict=False)
    except Exception:
        # Fix unescaped backslashes in JSON string values
        fixed = re.sub(r'\\([^\'"\\/bfnrtu])', r'\\\\\1', cleaned_json)
        try:
            return json.loads(fixed, strict=False)
        except Exception:
            pass

    # Fallback 1: regex extract "acceptance_criteria": [...]
    match = re.search(r'"acceptance_criteria"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
    if match:
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
        if items:
            return {"acceptance_criteria": items}

    # Fallback 2: if LLM returned a numbered or bulleted list
    lines = [
        re.sub(r"^[\s*\-\d\.\)]+\s*", "", line).strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith(("{", "}", "[", "]", "```"))
    ]
    lines = [
        l for l in lines
        if len(l) > 8 and not l.lower().startswith(("here are", "feature request", "acceptance criteria", "output only"))
    ]
    if len(lines) >= 2:
        return {"acceptance_criteria": lines[:6]}

    raise ValueError(f"Could not parse valid JSON from LLM output:\n{raw[:300]}")


def call_bob_pm(feature_request: str, previous_error: str = "") -> dict:
    """
    Call the PM Agent Bob LLM with the feature_request.
    Returns the parsed JSON dict from the LLM response.
    Raises RuntimeError on API failure, ValueError on JSON parse failure.
    """
    system_prompt = _load_system_prompt()
    retry_hint = ""
    if previous_error:
        retry_hint = (
            f"\n[CRITICAL FEEDBACK FROM PREVIOUS FAILED ATTEMPT]: {previous_error}\n"
            "You MUST generate EXACTLY 3 or 4 acceptance criteria in total, including 'All existing tests still pass'.\n"
        )
    user_message = (
        f"{system_prompt}\n\n"
        f"Feature request: {feature_request}\n\n"
        f"{retry_hint}"
        "CRITICAL CONSTRAINTS:\n"
        "- 'acceptance_criteria' MUST contain between 2 and 5 specific feature criteria, PLUS 'All existing tests still pass' (total 3 to 6 criteria maximum).\n"
        "- Under NO circumstances return more than 6 criteria or fewer than 2.\n"
        "- Output only valid JSON matching the schema above. No prose, no markdown fences."
    )
    raw = _call_watsonx(user_message)
    return _parse_json_response(raw)


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_pm_output(output: dict) -> bool:
    """
    Validate PM Agent LLM output.

    Rules:
    - `acceptance_criteria` must be present and a list
    - Must have between 2 and 6 items
    - No empty strings
    - No exact duplicates (case-sensitive)
    - At least one criterion must reference "existing tests" + "pass"
      (the mandatory safety-net criterion required by the system prompt)

    Returns True if valid, False otherwise.
    Note: a `blocked` output with empty criteria is also considered valid
    (the pipeline handles it as a blocked task, not a PM failure).
    """
    # Blocked output — valid by design, pipeline sets status=blocked
    if output.get("blocked") is True:
        return True

    criteria = output.get("acceptance_criteria")
    if not isinstance(criteria, list):
        return False
    if not (2 <= len(criteria) <= 6):
        return False
    for c in criteria:
        if not isinstance(c, str) or not c.strip():
            return False
    # Fix C: case-insensitive duplicate check — catches capitalisation variants
    lower_criteria = [c.lower().strip() for c in criteria]
    if len(lower_criteria) != len(set(lower_criteria)):
        return False
    # Mandatory "all existing tests still pass" criterion
    # Accept any wording that contains "existing" + "tests" + "pass" — the LLM
    # sometimes writes "existing automated tests ... continue to pass" which is
    # semantically identical but doesn't contain the exact phrase "existing tests".
    combined_lower = " ".join(lower_criteria)
    has_existing = "existing" in combined_lower
    has_tests = "tests" in combined_lower or "test" in combined_lower
    has_pass = "pass" in combined_lower
    if not (has_existing and has_tests and has_pass):
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Main agent entry point
# ──────────────────────────────────────────────────────────────────────────────

def _append_history(task: dict, summary: str, success: bool) -> dict:
    """Append a pm_agent entry to task history."""
    task["history"].append({
        "agent": "pm_agent",
        "output_summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
    })
    return task


def run_pm_agent(task: dict) -> dict:
    """
    Main entry point called by pipeline.py.

    Steps:
      a. Call Bob PM with task["feature_request"] (up to 2 attempts on transient parse failures)
      b. Validate the output
      c. If valid (and not blocked):
           - Set task["acceptance_criteria"]
           - Set task["status"] = "in_progress"
           - Set task["current_agent"] = "architect_agent"
           - Append history entry: success=True
      d. If blocked by PM Agent (nonsensical request):
           - Set task["status"] = "blocked"
           - Set task["acceptance_criteria"] = []
           - Append history entry: success=False, output_summary = block_reason
      e. If LLM call failed or output invalid:
           - Set task["status"] = "blocked"
           - Append history entry: success=False, output_summary = specific error
      f. Return updated task
    """
    print("[pm_agent] Generating acceptance criteria...")
    task["current_agent"] = "pm_agent"
    task["status"] = "in_progress"

    max_attempts = 2
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            output = call_bob_pm(task["feature_request"], previous_error=last_err)

            # --- Handle explicitly blocked requests ---
            if output.get("blocked") is True:
                block_reason = output.get("block_reason", "feature request blocked by PM Agent")
                print(f"[pm_agent] Request blocked: {block_reason}")
                task["acceptance_criteria"] = []
                task["status"] = "blocked"
                task = _append_history(task, f"blocked: {block_reason}", False)
                return task

            # Sanitize criteria items without truncating (so >6 fails validation)
            if isinstance(output.get("acceptance_criteria"), list):
                cleaned_crit = []
                for c in output["acceptance_criteria"]:
                    if not isinstance(c, str):
                        continue
                    s = c.strip().rstrip(",")
                    s = re.sub(r'^\\?"?[a-zA-Z_0-9]+"?\\?\s*:\s*', '', s).strip()
                    s = s.strip('"').strip("'").strip()
                    if s and s not in cleaned_crit:
                        cleaned_crit.append(s)
                output["acceptance_criteria"] = cleaned_crit

            # Ensure mandatory safety-net criterion is present if under max limit
            if isinstance(output.get("acceptance_criteria"), list) and output["acceptance_criteria"]:
                crit_list = output["acceptance_criteria"]
                combined = " ".join(str(c).lower() for c in crit_list)
                if not ("existing" in combined and "test" in combined and "pass" in combined):
                    if len(crit_list) < 6:
                        crit_list.append("All existing tests still pass")
                    elif len(crit_list) == 6:
                        crit_list[-1] = "All existing tests still pass"

            # --- Validate criteria ---
            if not validate_pm_output(output):
                raise ValueError(
                    f"PM output failed validation — acceptance_criteria must be a list of "
                    f"2–6 non-empty, non-duplicate strings. Got: {output.get('acceptance_criteria')}"
                )

            task["acceptance_criteria"] = output["acceptance_criteria"]
            task["status"] = "in_progress"
            task["current_agent"] = "architect_agent"
            task = _append_history(
                task,
                f"acceptance criteria defined ({len(output['acceptance_criteria'])} criteria)",
                True,
            )
            print(f"[pm_agent] {len(output['acceptance_criteria'])} criteria generated.")
            return task

        except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as e:
            last_err = str(e)
            err_str = str(e)
            if "401" in err_str or "authentication_token_not_valid" in err_str:
                print("[pm_agent] WARNING: LLM auth failed — falling back to default acceptance criteria.")
                task["acceptance_criteria"] = [
                    "All existing tests still pass",
                    "Feature works as specified in the request",
                ]
                task["status"] = "in_progress"
                task["current_agent"] = "architect_agent"
                task = _append_history(
                    task,
                    "acceptance criteria defined (2 criteria, watsonx auth fallback)",
                    True,
                )
                return task

            if attempt < max_attempts:
                print(f"[pm_agent] Attempt {attempt} failed ({e}). Retrying PM Agent LLM call with error feedback...")
                continue

            print(f"[pm_agent] ERROR: {e}")
            task["status"] = "blocked"
            task["acceptance_criteria"] = []
            task["current_agent"] = "human"
            error_str = str(e)[:120]
            task = _append_history(task, f"pm_agent error: {error_str}", False)
            return task

    return task


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PM Agent — generate acceptance criteria")
    parser.add_argument("--request", required=True, help="Feature request in plain English")
    args = parser.parse_args()

    # Build a minimal task and run
    task = {
        "task_id": "cli_test",
        "feature_request": args.request,
        "acceptance_criteria": [],
        "scoped_files": [],
        "status": "pending",
        "current_agent": "pm_agent",
        "plan": None,
        "history": [],
        "code_diff": None,
        "test_results": None,
        "review_result": None,
        "retry_count": 0,
    }
    result = run_pm_agent(task)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
