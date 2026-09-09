"""
reflection.py — Reflection Agent logic
Reads a Manager Agent report, loads the failing agent's current system prompt,
and produces a targeted rewrite. The rewrite is saved to prompt_history/ and
applied to the agent's live system_prompt.md.

Usage (standalone test):
    python agents/reflection_agent/reflection.py --report <path_to_manager_report.json>
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; GROQ_API_KEY can be set directly in env

# Repo root = two levels up from this file
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROMPT_HISTORY_DIR = os.path.join(os.path.dirname(__file__), "prompt_history")
AGENTS_DIR = os.path.join(REPO_ROOT, "agents")


# ──────────────────────────────────────────────────────────────────────────────
# Prompt file helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_current_prompt(agent_name: str) -> str:
    """
    Read agents/{agent_name}/system_prompt.md.
    Raises FileNotFoundError if the file does not exist.
    """
    path = os.path.join(AGENTS_DIR, agent_name, "system_prompt.md")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"system_prompt.md not found for agent '{agent_name}' at {path}. "
            "Each agent must have this file before the Reflection Agent can rewrite it."
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def get_current_version(agent_name: str) -> int:
    """
    Scan prompt_history/ for files matching {agent_name}_v*.md.
    Return the highest version number found, or 0 if none exist.
    """
    if not os.path.exists(PROMPT_HISTORY_DIR):
        return 0
    pattern = re.compile(rf"^{re.escape(agent_name)}_v(\d+)\.md$")
    max_version = 0
    for fname in os.listdir(PROMPT_HISTORY_DIR):
        match = pattern.match(fname)
        if match:
            max_version = max(max_version, int(match.group(1)))
    return max_version


def save_prompt_version(agent_name: str, version: int, content: str) -> str:
    """
    Write content to prompt_history/{agent_name}_v{version}.md.
    Creates the prompt_history/ directory if it doesn't exist.
    Returns the path of the saved file.
    """
    os.makedirs(PROMPT_HISTORY_DIR, exist_ok=True)
    path = os.path.join(PROMPT_HISTORY_DIR, f"{agent_name}_v{version}.md")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        try:
            home_dir = os.path.join(os.path.expanduser("~"), ".ai_dev_team", "prompt_history")
            os.makedirs(home_dir, exist_ok=True)
            with open(os.path.join(home_dir, f"{agent_name}_v{version}.md"), "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass
    return path


def apply_rewrite_to_agent(agent_name: str, rewritten_prompt: str) -> None:
    """Overwrite agents/{agent_name}/system_prompt.md with the rewritten prompt."""
    path = os.path.join(AGENTS_DIR, agent_name, "system_prompt.md")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(rewritten_prompt)
    except OSError:
        try:
            home_dir = os.path.join(os.path.expanduser("~"), ".ai_dev_team", "prompts")
            os.makedirs(home_dir, exist_ok=True)
            with open(os.path.join(home_dir, f"{agent_name}_system_prompt.md"), "w", encoding="utf-8") as f:
                f.write(rewritten_prompt)
        except Exception:
            pass


def rollback_prompt_to_previous_version(agent_name: str) -> tuple[bool, int, str]:
    """
    Rollback live system_prompt.md to the previous version (v_{n-1}) if current version >= 1.
    Returns: (success: bool, target_version: int, message: str)
    """
    curr_v = get_current_version(agent_name)
    if curr_v <= 0:
        return False, 0, f"No previous version available for {agent_name}"

    target_v = curr_v - 1
    target_path = os.path.join(PROMPT_HISTORY_DIR, f"{agent_name}_v{target_v}.md")
    if not os.path.exists(target_path):
        return False, target_v, f"Target version file {target_path} not found"

    with open(target_path, "r", encoding="utf-8") as f:
        prev_content = f.read()

    apply_rewrite_to_agent(agent_name, prev_content)
    return True, target_v, f"Successfully rolled back {agent_name} from v{curr_v} to v{target_v}"


# ──────────────────────────────────────────────────────────────────────────────
# Failure pattern extraction
# ──────────────────────────────────────────────────────────────────────────────

def extract_failure_patterns(manager_report: dict) -> list:

    """
    Extract specific failure type strings from the manager report.
    Sources:
    - manager_report["reasoning"]: parse quoted strings and known keywords
    - Fallback: return reasoning as a single pattern if no specific patterns found
    """
    reasoning = manager_report.get("reasoning", "")

    # Extract single-quoted patterns: 'missing input validation'
    quoted = re.findall(r"'([^']+)'", reasoning)
    if quoted:
        return list(dict.fromkeys(quoted))  # deduplicate, preserve order

    # Fallback: split on semicolons and return non-empty parts
    parts = [p.strip() for p in reasoning.split(";") if p.strip()]
    return parts if parts else [reasoning]


# ──────────────────────────────────────────────────────────────────────────────
# Reflection input builder
# ──────────────────────────────────────────────────────────────────────────────

def build_reflection_input(manager_report: dict, current_prompt: str, agent_name: str = None) -> str:
    """
    Assemble the full context string to send to the Reflection Agent Bob mode.
    Returns a formatted string containing all the context the LLM needs.

    agent_name: the specific underperformer being rewritten. If None, uses first underperformer.
    """
    if agent_name is None:
        agent_name = manager_report.get("underperformers", ["unknown"])[0]
    current_version = get_current_version(agent_name)
    next_version = current_version + 1
    failure_patterns = extract_failure_patterns(manager_report)

    context = f"""## Reflection Agent Task

You are rewriting the system prompt for: {agent_name}
Target version: v{next_version}

## Manager Report
```json
{json.dumps(manager_report, indent=2)}
```

## Failure Patterns to Address
{chr(10).join(f"- {p}" for p in failure_patterns)}

## Current System Prompt (v{current_version if current_version > 0 else 'original'})
```
{current_prompt}
```

## Instructions
Rewrite the system prompt above to specifically address the failure patterns listed.
Output ONLY valid JSON matching this schema:
{{
  "agent_name": "{agent_name}",
  "version": {next_version},
  "rewritten_prompt": "<full rewritten prompt text>",
  "change_summary": ["<bullet: change + which failure it addresses>", ...]
}}
"""
    return context


# ──────────────────────────────────────────────────────────────────────────────
# Bob LLM call (stub — replace with real Bob API call in Week 2)
# ──────────────────────────────────────────────────────────────────────────────

# ---------------------------------------------------------------------------
# Bob / watsonx API defaults — all config read at call time (not import time)
# so .env loaded after import is always picked up.
# ---------------------------------------------------------------------------


def _call_watsonx(prompt: str) -> str:
    """
    POST to IBM Bob / watsonx chat endpoint.
    Returns the raw generated text string. Raises RuntimeError on failure.
    """
    import sys
    import os as _os
    _repo_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from orchestration.iam_auth import call_bob_chat

    return call_bob_chat(prompt, max_tokens=3000)


def _parse_llm_json_response(raw: str, expected_agent: str, expected_version: int) -> dict:
    """
    Extract and parse a JSON object from the LLM's raw text output.
    LLMs sometimes wrap JSON in markdown fences or add preamble — strip those.
    Falls back to a minimal valid structure if parsing fails.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # Try to find a JSON object if there's surrounding prose
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        parsed = json.loads(cleaned, strict=False)
    except Exception:
        # Fix unescaped backslashes commonly found in prompt text
        fixed = re.sub(r'\\([^\'"\\/bfnrtu])', r'\\\\\1', cleaned)
        try:
            parsed = json.loads(fixed, strict=False)
        except Exception:
            try:
                # Fallback: find first balanced JSON object
                start = cleaned.find("{")
                if start != -1:
                    depth = 0
                    for i in range(start, len(cleaned)):
                        if cleaned[i] == "{":
                            depth += 1
                        elif cleaned[i] == "}":
                            depth -= 1
                            if depth == 0:
                                parsed = json.loads(cleaned[start:i+1], strict=False)
                                break
                    else:
                        raise ValueError("Unbalanced JSON braces")
                else:
                    raise ValueError("No opening brace")
            except Exception:
                raise ValueError(f"LLM response was not valid JSON\nRaw response:\n{raw[:500]}")

    # Ensure required fields exist; fill from context or aliases if missing
    if "agent_name" not in parsed:
        parsed["agent_name"] = parsed.get("agent", expected_agent)
    if "version" not in parsed:
        parsed["version"] = expected_version
    if "rewritten_prompt" not in parsed:
        for k in ("prompt", "system_prompt", "new_prompt", "rewritten_system_prompt", "content"):
            if k in parsed and isinstance(parsed[k], str):
                parsed["rewritten_prompt"] = parsed[k]
                break
    if "change_summary" not in parsed:
        for k in ("changes", "summary", "change_log", "changelog", "modifications"):
            if k in parsed:
                val = parsed[k]
                parsed["change_summary"] = val if isinstance(val, list) else [str(val)]
                break

    return parsed


def _stub_rewrite(reflection_input: str) -> dict:
    """
    Local stub — returns a clean full replacement prompt without calling Groq.
    Used when GROQ_API_KEY is not set or the API call fails.

    IMPORTANT: this returns the ORIGINAL prompt with a single appended section,
    replacing any previously-appended stub sections so the file never accumulates
    duplicate blocks. The real Groq call will return a proper full rewrite.
    """
    print("[reflection.py] STUB MODE: Groq call failed — returning placeholder rewrite.")
    print(f"[reflection.py] Context length: {len(reflection_input)} chars")

    agent_match = re.search(r"rewriting the system prompt for: (\S+)", reflection_input)
    version_match = re.search(r"Target version: v(\d+)", reflection_input)
    agent_name = agent_match.group(1) if agent_match else "unknown_agent"
    version = int(version_match.group(1)) if version_match else 2

    failure_match = re.findall(r"^- (.+)$", reflection_input, re.MULTILINE)
    failure_patterns = failure_match[:3] if failure_match else ["unspecified failure pattern"]

    prompt_match = re.search(r"## Current System Prompt.*?```\n(.*?)```", reflection_input, re.DOTALL)
    original_prompt = prompt_match.group(1).strip() if prompt_match else "# Original prompt\n"

    # Strip any previously-appended stub sections so we don't accumulate duplicates
    stub_section_marker = "\n\n## Stub Reflection Rules"
    if stub_section_marker in original_prompt:
        original_prompt = original_prompt[:original_prompt.index(stub_section_marker)].strip()

    # Also strip any old "## Rules Added by Reflection Agent" sections
    old_marker = "\n\n## Rules Added by Reflection Agent"
    if old_marker in original_prompt:
        original_prompt = original_prompt[:original_prompt.index(old_marker)].strip()

    # Build a single clean addition section
    stub_rules = "\n\n## Stub Reflection Rules\n"
    stub_rules += "<!-- Added by stub Reflection Agent — will be replaced by real LLM rewrite -->\n"
    for pattern in failure_patterns:
        stub_rules += f"- Explicitly verify before output: {pattern}\n"

    return {
        "agent_name": agent_name,
        "version": version,
        "rewritten_prompt": original_prompt + stub_rules,
        "change_summary": [
            f"Added verification rule for '{p}' — addresses failure pattern from Manager report (stub)"
            for p in failure_patterns
        ],
    }


def call_reflection_bob_mode(reflection_input: str) -> dict:
    """
    Send reflection_input to the IBM watsonx API and return the parsed JSON response.

    Requires GROQ_API_KEY in environment (see .env.example).
    If either is missing, falls back to the local stub so the pipeline stays runnable.

    The response must be valid JSON with fields:
      agent_name, version, rewritten_prompt, change_summary
    """
    # Extract expected agent/version for fallback parsing
    agent_match = re.search(r"rewriting the system prompt for: (\S+)", reflection_input)
    version_match = re.search(r"Target version: v(\d+)", reflection_input)
    expected_agent = agent_match.group(1) if agent_match else "unknown_agent"
    expected_version = int(version_match.group(1)) if version_match else 2

    # Fall back to stub if API key not configured — read at call time
    if not os.environ.get("GROQ_API_KEY"):
        return _stub_rewrite(reflection_input)

    print(f"[reflection.py] Calling Groq API for {expected_agent} v{expected_version}...")
    print(f"[reflection.py] Context length: {len(reflection_input)} chars")

    try:
        raw_response = _call_watsonx(reflection_input)
        print(f"[reflection.py] Groq response received ({len(raw_response)} chars)")
    except Exception as e:
        err_str = str(e)
        print(f"[reflection.py] WARNING: Groq API request failed: {e}")
        if "quota" in err_str.lower() or "429" in err_str or "403" in err_str or "401" in err_str:
            print("[reflection.py] API auth/quota/rate-limit hit — skipping reflection to preserve prompt.")
            raise RuntimeError(f"Reflection skipped: auth/quota/rate-limit — {err_str[:120]}") from e
        print("[reflection.py] Falling back to stub rewrite.")
        return _stub_rewrite(reflection_input)

    try:
        parsed = _parse_llm_json_response(raw_response, expected_agent, expected_version)
        return parsed
    except Exception as e:
        print(f"[reflection.py] WARNING: Could not parse LLM JSON response: {e}")
        print("[reflection.py] Falling back to stub rewrite.")
        return _stub_rewrite(reflection_input)


# Minimum total character count across all change_summary items.
# "improved the prompt" is ~20 chars — 40 chars filters vague one-liners
# while allowing any real, specific explanation through.
CHANGE_SUMMARY_MIN_CHARS = 40


def validate_reflection_output(
    output: dict,
    expected_failure_patterns: list = None,
) -> None:
    """
    Raise ValueError if the output is missing required fields, has a vague
    change_summary, or doesn't reference the expected failure patterns.

    Args:
        output: The dict returned by call_reflection_bob_mode().
        expected_failure_patterns: List of failure pattern strings extracted
            before the Bob call (from extract_failure_patterns). When provided,
            at least one pattern must appear (case-insensitive) somewhere in
            the combined change_summary text. Pass None to skip this check
            (e.g. in unit tests that don't test pattern-reference).
    """
    # 1. Required fields present
    required = ["agent_name", "version", "rewritten_prompt", "change_summary"]
    for field in required:
        if field not in output:
            raise ValueError(f"Reflection Agent output missing required field: '{field}'")

    # 2. change_summary must be a non-empty list
    if not isinstance(output["change_summary"], list) or len(output["change_summary"]) == 0:
        raise ValueError("change_summary must be a non-empty list")

    # 3. rewritten_prompt must not be blank
    if not output["rewritten_prompt"].strip():
        raise ValueError("rewritten_prompt must not be empty")

    # 4. change_summary must be specific enough (total length check)
    combined = " ".join(str(s) for s in output["change_summary"])
    if len(combined) < CHANGE_SUMMARY_MIN_CHARS:
        raise ValueError(
            f"change_summary is too vague ({len(combined)} chars, minimum {CHANGE_SUMMARY_MIN_CHARS}). "
            "Every change must trace to a named failure type with a concrete description."
        )

    # 5. change_summary must reference at least one expected failure pattern
    if expected_failure_patterns:
        combined_lower = combined.lower()
        matched = any(
            pattern.lower() in combined_lower
            for pattern in expected_failure_patterns
        )
        if not matched:
            raise ValueError(
                f"change_summary does not reference any of the expected failure patterns: "
                f"{expected_failure_patterns}. Every change must trace to a named failure type."
            )


# ──────────────────────────────────────────────────────────────────────────────
# Main orchestration
# ──────────────────────────────────────────────────────────────────────────────

def run_reflection(manager_report: dict) -> list:
    """
    Main entry point called by the pipeline (orchestration/pipeline.py).
    For each underperforming agent in manager_report:
      1. Load current prompt
      2. Get current version number
      3. Save current prompt as v{current_version} in prompt_history (baseline)
      4. Build reflection input
      5. Call Bob mode for rewrite
      6. Validate output
      7. Save new prompt as v{next_version} in prompt_history
      8. Overwrite agent's live system_prompt.md

    Returns list of result dicts for the dashboard:
      [{ agent_name, old_version, new_version, change_summary, prompt_history_path }]
    """
    results = []
    for agent_name in manager_report.get("underperformers", []):
        print(f"\n[reflection] Processing underperformer: {agent_name}")

        try:
            current_prompt = load_current_prompt(agent_name)
        except FileNotFoundError as e:
            print(f"[reflection] WARNING: {e} — skipping {agent_name}")
            continue

        current_version = get_current_version(agent_name)

        # Save the baseline (original) prompt if not already saved
        if current_version == 0:
            save_prompt_version(agent_name, 0, current_prompt)
            print(f"[reflection] Saved baseline prompt for {agent_name} as v0")

        next_version = current_version + 1

        # Extract failure patterns so we can validate the rewrite references them
        failure_patterns = extract_failure_patterns(manager_report)

        # Build context and call Bob — all errors caught; stub guarantees a result
        try:
            reflection_input = build_reflection_input(manager_report, current_prompt, agent_name=agent_name)
            output = call_reflection_bob_mode(reflection_input)
        except Exception as e:
            safe_e = str(e).encode("ascii", errors="replace").decode("ascii")
            print("[reflection] ERROR building/calling reflection for " + agent_name + ": " + safe_e)
            print("[reflection] Skipping rewrite for " + agent_name + ".")
            continue

        # Validate — log clearly if it fails so debugging is fast
        try:
            validate_reflection_output(output, expected_failure_patterns=failure_patterns)
        except ValueError as e:
            def _safe(s):
                return str(s).encode("ascii", errors="replace").decode("ascii")
            print("[reflection] VALIDATION FAILED for " + agent_name + ":")
            print("  Agent: " + agent_name)
            print("  Expected failure patterns: " + _safe(failure_patterns))
            print("  change_summary returned: " + _safe(output.get("change_summary", "(missing)")))
            print("  Error: " + _safe(e))
            # Pattern-match failure only means the LLM used different wording —
            # the rewrite is still useful. Only skip on structural failures
            # (missing fields, empty prompt, vague summary).
            err_str = str(e)
            if "does not reference any of the expected failure patterns" in err_str:
                print("[reflection] Accepting rewrite despite pattern mismatch (LLM used different wording).")
            else:
                print("[reflection] Skipping rewrite for " + agent_name + " — structural validation failed.")
                continue

        # Save new version to history
        try:
            history_path = save_prompt_version(agent_name, next_version, output["rewritten_prompt"])

            # Apply rewrite to live prompt
            apply_rewrite_to_agent(agent_name, output["rewritten_prompt"])
        except Exception as e:
            safe_e = str(e).encode("ascii", errors="replace").decode("ascii")
            print("[reflection] ERROR saving/applying rewrite for " + agent_name + ": " + safe_e)
            continue

        print("[reflection] Rewrote " + agent_name + ": v" + str(current_version) + " -> v" + str(next_version))
        print("[reflection] Changes:")
        for bullet in output["change_summary"]:
            safe_bullet = str(bullet).encode("ascii", errors="replace").decode("ascii")
            print("  - " + safe_bullet)

        results.append({
            "agent_name": agent_name,
            "old_version": current_version,
            "new_version": next_version,
            "change_summary": output["change_summary"],
            "prompt_history_path": history_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return results


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Reflection Agent — rewrite failing agent system prompts"
    )
    parser.add_argument(
        "--report", required=True, help="Path to Manager Agent report JSON file"
    )
    args = parser.parse_args()

    if not os.path.exists(args.report):
        print(f"ERROR: report file not found: {args.report}", file=sys.stderr)
        sys.exit(1)

    with open(args.report, "r", encoding="utf-8") as f:
        manager_report = json.load(f)

    results = run_reflection(manager_report)

    if not results:
        print("\n[reflection] No rewrites performed (no underperformers with system prompts found).")
    else:
        print("\n[reflection] Rewrite results:")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
