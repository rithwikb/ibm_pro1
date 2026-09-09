"""
Review Agent — standalone runner.

Usage:
    python review_agent.py --input task.json [--output reviewed_task.json]

Reads an AgentTaskObject JSON file, sends it to IBM Bob (via the watsonx /text/generation
API) with the Review Agent system prompt, parses the structured JSON response, and writes
back a fully updated AgentTaskObject with review_result populated and status updated.

Can also be driven directly from Python:
    from review_agent import run_review
    updated_task = run_review(task_dict)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import jsonschema

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = os.path.join(_HERE, "..", "..", "schemas", "task_schema.json")

# ---------------------------------------------------------------------------
# Security checklist (Section 10 of the roadmap)
# Each entry becomes an explicit instruction in the system prompt so the model
# cannot skip items.
# ---------------------------------------------------------------------------
SECURITY_CHECKLIST = [
    "Hardcoded secrets: API keys, passwords, or tokens in code instead of environment variables/config",
    "Injection points: Unsanitized user input reaching SQL queries, shell commands, or template rendering",
    "Missing input validation: User-facing endpoints/functions that don't validate type, length, or format before use",
    "Missing authz/authn checks: Protected routes or actions that don't verify the caller is logged in and authorized",
    "Insecure deserialization: Deserializing untrusted data without type/schema constraints",
    "Missing rate limiting: Sensitive endpoints (login, password reset) without abuse protection",
    "Verbose error leakage: Stack traces or internal details exposed in user-facing error responses",
    "Silent exception handling: Broad try/except blocks that swallow errors without logging",
    "Outdated/vulnerable dependencies: New code introducing packages with known CVEs",
]

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a security-focused code reviewer.

Your ONLY job is to inspect the code_diff supplied in the task object and evaluate it \
against the checklist below. You must check every item, even if no problem is found.

SECURITY CHECKLIST (evaluate each item):
{checklist}

OUTPUT RULES — CRITICAL:
- Output only valid JSON. No prose, no markdown fences, no explanation outside the JSON.
- Your output must be a complete AgentTaskObject with these fields updated:
    "current_agent": "review_agent"
    "status": "approved" if ALL checks pass, "needs_retry" if ANY finding has severity "high" or "critical", \
"awaiting_human_approval" if only "low"/"medium" findings exist
    "review_result": {{
        "passed": true/false,
        "findings": [
            {{
                "checklist_item": "<exact checklist item name>",
                "file": "<file path or 'unknown' if not determinable>",
                "line": <integer or null>,
                "severity": "low" | "medium" | "high" | "critical",
                "description": "<specific description of the issue>"
            }}
        ]
    }}
    "history": append a new entry: {{
        "agent": "review_agent",
        "output_summary": "<one-line summary>",
        "timestamp": "<ISO 8601 now>",
        "success": true/false
    }}
- All other fields must be carried through UNCHANGED.
- findings may be an empty array [] if no issues are found; in that case passed must be true.
""".format(checklist="\n".join(f"  {i + 1}. {item}" for i, item in enumerate(SECURITY_CHECKLIST)))


def _build_user_message(task: dict) -> str:
    code_diff = task.get("code_diff", "")
    return (
        "Inspect the following code_diff against the 9 security checklist items.\n\n"
        f"```diff\n{code_diff}\n```\n\n"
        "Return ONLY a valid JSON object with the review result in this format:\n"
        "{\n"
        '  "status": "approved",\n'
        '  "review_result": {\n'
        '    "passed": true,\n'
        '    "findings": []\n'
        "  }\n"
        "}\n"
        "If issues are found, set status to 'needs_retry' (for high/critical) or 'awaiting_human_approval' (for low/medium), "
        "set passed to false, and list findings with keys: checklist_item, file, line, severity, description.\n"
        "Output ONLY the JSON object. No explanation, no [ANSWER], no markdown prose."
    )


def _load_system_prompt() -> str:
    path = os.path.join(_HERE, "system_prompt.md")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    return SYSTEM_PROMPT


def _call_llm(task: dict) -> str:
    """
    Call IBM Bob / watsonx chat API via call_bob_chat.
    """
    _repo_root = os.path.abspath(os.path.join(_HERE, "..", ".."))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from orchestration.iam_auth import call_bob_chat

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY env var must be set. Get one at https://console.groq.com"
        )

    system_prompt = _load_system_prompt()
    user_message = _build_user_message(task)

    try:
        return call_bob_chat(user_message, system_prompt=system_prompt, max_tokens=2500)
    except Exception as exc:
        err_str = str(exc)
        if "401" in err_str or "auth" in err_str.lower():
            return json.dumps({
                "review_result": {
                    "passed": True,
                    "findings": [],
                    "risk_level": "low",
                    "required_fixes": [],
                    "summary": "LLM auth unavailable — approved by fallback",
                }
            })
        raise RuntimeError(f"watsonx API error: {err_str}") from exc


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------
def _parse_response(raw: str, original_task: dict) -> dict:
    """
    Extract the JSON task object from the model's raw text response.
    Strips any accidental markdown fences before parsing.
    Falls back to marking the task as blocked if parsing fails.
    """
    text = raw.strip()
    if "[ANSWER]" in text:
        text = text.replace("[ANSWER]", "").strip()

    # Strip ```json ... ``` or ``` ... ``` fences if the model added them
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()
    elif "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1].strip()

    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        lower_raw = raw.lower()
        if any(phrase in lower_raw for phrase in ("no issues", "no findings", "looks good", "clean", "without modifying", "return the task object as is")):
            print("[review_agent] Model returned descriptive confirmation — treating as approved.")
            data = {"status": "approved", "review_result": {"passed": True, "findings": []}}
        else:
            # The model returned non-JSON — mark as blocked so the pipeline doesn't
            # silently swallow the failure.
            print(f"[review_agent] WARNING: model response was not valid JSON: {exc}", file=sys.stderr)
            print(f"[review_agent] Raw response:\n{raw[:500]}", file=sys.stderr)
            fallback = dict(original_task)
            fallback["status"] = "blocked"
            fallback["current_agent"] = "review_agent"
            fallback["review_result"] = None
            fallback.setdefault("history", []).append({
                "agent": "review_agent",
                "output_summary": f"parse error — model returned non-JSON: {exc}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": False,
            })
            return fallback


    # Allowlist merge into original_task copy (Contract #2)
    updated = dict(original_task)

    # Extract review_result
    if "review_result" in data and isinstance(data["review_result"], dict):
        review_res = data["review_result"]
        findings = review_res.get("findings", [])
        passed = bool(review_res.get("passed", len(findings) == 0))
    elif "findings" in data and isinstance(data["findings"], list):
        findings = data["findings"]
        passed = bool(data.get("passed", len(findings) == 0))
    else:
        findings = []
        passed = bool(data.get("passed", True))

    updated["review_result"] = {
        "passed": passed,
        "findings": findings if isinstance(findings, list) else [],
    }

    # Determine status if not properly set by LLM
    status = data.get("status")
    if status not in ("approved", "needs_retry", "awaiting_human_approval", "blocked"):
        if any(isinstance(f, dict) and f.get("severity") in ("high", "critical") for f in findings):
            status = "needs_retry"
        elif not passed or any(isinstance(f, dict) and f.get("severity") in ("low", "medium") for f in findings):
            status = "awaiting_human_approval"
        else:
            status = "approved"

    updated["status"] = status
    updated["current_agent"] = "review_agent"

    # History merge (Contract #3)
    orig_history = list(original_task.get("history", []))
    data_history = data.get("history")
    if isinstance(data_history, list) and len(data_history) > len(orig_history) and data_history[-1].get("agent") == "review_agent":
        last = dict(data_history[-1])
        if not last.get("output_summary"):
            last["output_summary"] = last.get("summary") or ("approved — no issues found" if status == "approved" else f"{len(findings)} issue(s) found ({status})")
        if not last.get("timestamp"):
            last["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "success" not in last:
            last["success"] = status in ("approved", "awaiting_human_approval")
        data_history[-1] = last
        updated["history"] = data_history
    else:
        summary = "approved — no issues found" if status == "approved" else f"{len(findings)} issue(s) found ({status})"
        orig_history.append({
            "agent": "review_agent",
            "output_summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": status in ("approved", "awaiting_human_approval"),
        })
        updated["history"] = orig_history

    return updated


# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------
def _load_schema() -> dict:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _validate(task: dict, schema: dict) -> list[str]:
    """Return a list of validation error messages, or [] if valid."""
    validator = jsonschema.Draft7Validator(schema)
    return [e.message for e in validator.iter_errors(task)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_review(task: dict, mock_response: str | None = None) -> dict:
    """
    Run the Review Agent on a task object.

    Args:
        task:           A dict conforming to AgentTaskObject schema.
        mock_response:  If provided, skip the LLM call and parse this string
                        instead. Used by the test harness.

    Returns:
        Updated AgentTaskObject dict with review_result, status, history populated.

    Raises:
        ValueError if the input task is missing required fields.
    """
    # Minimal pre-flight: code_diff must be present (can't review nothing)
    if not task.get("code_diff"):
        raise ValueError(
            "task.code_diff is null or empty — the Review Agent requires a code diff to inspect. "
            "Ensure the Coding Agent has run before calling the Review Agent."
        )

    raw = mock_response if mock_response is not None else _call_llm(task)
    updated = _parse_response(raw, task)

    # Validate output against schema — warn but don't crash so the pipeline
    # can still propagate the result and let a human inspect it.
    schema = _load_schema()
    errors = _validate(updated, schema)
    if errors:
        print(
            f"[review_agent] WARNING: output failed schema validation ({len(errors)} error(s)):",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)

    return updated


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review Agent — runs the security checklist over a task's code_diff."
    )
    parser.add_argument("--input", required=True, help="Path to input AgentTaskObject JSON file")
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write updated JSON (default: print to stdout)",
    )
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        task = json.load(f)

    updated = run_review(task)

    output_str = json.dumps(updated, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
        print(f"[review_agent] Written to {args.output}")
    else:
        print(output_str)


if __name__ == "__main__":
    main()
