# Reflection Agent — Design Document

## Purpose

The Reflection Agent receives the Manager Agent's report and the current system prompt of any flagged underperformer. It uses an LLM (via a Bob custom mode) to produce a **targeted, specific rewrite** of that agent's system prompt. The rewrite must be traceable to concrete failure patterns — not generic encouragement.

This is the demo's credibility hinge. Judges will look at the before/after diff. If the only change is "try harder", it fails.

---

## What Makes a Rewrite "Real" (Not Superficial)

**Bad rewrite (superficial):**
```
+ Please try to be more careful and thorough in your analysis.
```

**Good rewrite (specific):**
```
+ Before returning your output, explicitly check each item in this list:
+   - Does any user input reach a SQL query without parameterization? If yes, flag it.
+   - Are there any hardcoded strings that look like API keys, passwords, or tokens? If yes, flag them.
+   - Is rate limiting present on endpoints that accept passwords or reset tokens? If not, flag it.
```

The rule: **every added or changed line must trace to a named failure type from the Manager report**.

---

## Input the Reflection Agent Receives

```json
{
  "agent_name": "coding_agent",
  "current_prompt": "...full text of agents/coding_agent/system_prompt.md...",
  "manager_report": {
    "task_id": "task_003",
    "underperformers": ["coding_agent"],
    "reasoning": "coding_agent succeeded 2/5 runs; review rejected for 'missing input validation' (3x) and 'hardcoded secrets' (1x)",
    "stats_snapshot": { ... }
  },
  "failure_patterns": [
    "missing input validation",
    "hardcoded secrets"
  ]
}
```

`failure_patterns` is extracted from the Manager's `reasoning` field and from the `review_result.findings` of failed runs.

---

## Output the Reflection Agent Produces

```json
{
  "agent_name": "coding_agent",
  "version": 2,
  "rewritten_prompt": "...full text of the new system prompt...",
  "change_summary": [
    "Added explicit checklist for input validation before returning any code output — addresses 3x 'missing input validation' rejections",
    "Added rule: never hardcode API keys, tokens, or passwords — use environment variables. Addresses 1x 'hardcoded secrets' rejection"
  ]
}
```

`change_summary` is what gets displayed in the dashboard's diff view. It must be specific — a bulleted list of exactly what changed and why.

---

## Prompt Storage Strategy

Each version of each agent's system prompt is stored in:
```
agents/reflection_agent/prompt_history/
  coding_agent_v1.md    ← original (copied here before first rewrite)
  coding_agent_v2.md    ← first rewrite
  coding_agent_v3.md    ← second rewrite (if needed)
  testing_agent_v1.md
  ...
```

The "live" prompt that the pipeline uses stays in `agents/{agent_name}/system_prompt.md`. After a rewrite, `reflection.py` both writes the new version to `prompt_history/` AND overwrites `agents/{agent_name}/system_prompt.md`.

Version numbering: simple integer, read from existing files in `prompt_history/` — max version number + 1.

---

## Pseudocode — `reflection.py`

```python
def load_current_prompt(agent_name: str) -> str:
    """
    Read agents/{agent_name}/system_prompt.md.
    Raise FileNotFoundError if it doesn't exist (each agent must have this file by Week 2).
    """

def get_current_version(agent_name: str) -> int:
    """
    Scan agents/reflection_agent/prompt_history/ for files matching {agent_name}_v*.md.
    Return the highest version number found, or 0 if none exist.
    """

def save_prompt_version(agent_name: str, version: int, content: str) -> str:
    """
    Write content to agents/reflection_agent/prompt_history/{agent_name}_v{version}.md.
    Return the path of the saved file.
    """

def extract_failure_patterns(manager_report: dict) -> list:
    """
    Parse manager_report["reasoning"] for failure type keywords.
    Also scan manager_report["stats_snapshot"] for context.
    Return a list of strings, e.g. ["missing input validation", "hardcoded secrets"].
    """

def build_reflection_input(manager_report: dict, current_prompt: str) -> str:
    """
    Assemble the full context string to send to the Reflection Agent Bob mode:
      - The current system prompt
      - The manager report (as JSON)
      - The extracted failure patterns
      - The instruction: "Rewrite the system prompt. Every change must trace to a failure type."
    Return as a formatted string.
    """

def call_reflection_bob_mode(reflection_input: str) -> dict:
    """
    Send reflection_input to the Reflection Agent Bob custom mode.
    Parse and return the JSON response with fields:
      agent_name, version, rewritten_prompt, change_summary
    Raise ValueError if the response is not valid JSON or is missing required fields.
    """

def apply_rewrite(agent_name: str, rewritten_prompt: str, version: int) -> None:
    """
    1. Save old prompt to prompt_history/{agent_name}_v{version-1}.md (if not already saved)
    2. Save new prompt to prompt_history/{agent_name}_v{version}.md
    3. Overwrite agents/{agent_name}/system_prompt.md with rewritten_prompt
    """

def run_reflection(manager_report: dict) -> list:
    """
    Main entry point called by the pipeline.
    For each agent in manager_report["underperformers"]:
      1. load_current_prompt
      2. get_current_version
      3. build_reflection_input
      4. call_reflection_bob_mode
      5. apply_rewrite
    Return list of dicts: [{ agent_name, old_version, new_version, change_summary }]
    """
```

---

## The Bob Custom Mode Prompt (system_prompt.md)

The Reflection Agent's own system prompt instructs Bob how to behave when called:

```
You are the Reflection Agent for an AI development pipeline.

You will receive:
1. The current system prompt of a failing agent
2. A Manager Agent report describing that agent's failure pattern
3. A list of specific failure types extracted from the report

Your job: rewrite the failing agent's system prompt to fix the exact failure patterns described.

Rules:
- Every change you make MUST trace to one of the named failure types. No speculative improvements.
- Do NOT add vague instructions like "be more thorough" or "try harder".
- DO add concrete, checkable instructions like "Before returning output, verify X" or "Never do Y".
- Preserve all instructions in the original prompt that are NOT related to the failure patterns.

Output ONLY valid JSON. No prose outside the JSON.

Output schema:
{
  "agent_name": "<string>",
  "version": <integer>,
  "rewritten_prompt": "<full text of rewritten system prompt>",
  "change_summary": ["<bullet: what changed + why>", ...]
}
```

---

## What the Reflection Agent Does NOT Do

- Does not decide which agents to fix — that's the Manager's job
- Does not run tests to verify the rewrite works — that happens in the next pipeline run
- Does not modify `agent_stats.json` — stats only update when a new task runs
- Does not produce partial diffs — it always outputs the full rewritten prompt (simpler, less error-prone)
