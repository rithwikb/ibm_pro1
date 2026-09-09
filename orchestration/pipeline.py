"""
pipeline.py — Main orchestration pipeline for the AI Dev Team.

Wires all agents in order:
    PM Agent → Architect Agent → Coding Agent → Testing Agent
    → Review Agent → Manager Agent → (optional) Reflection Agent

Usage:
    python orchestration/pipeline.py \\
        --request "Add rate limiting to the login endpoint" \\
        --repo sample_repo/flaskbb \\
        --task-id task_001

Retry logic:
  - If review returns "needs_retry" and retry_count < MAX_RETRIES, loops back to
    Coding Agent (keeping plan and criteria).
  - On the (MAX_RETRIES+1)th rejection, sets status="blocked" and escalates to human.

Blocked/error handling:
  - Any agent that sets status="blocked" halts the pipeline immediately.
  - Manager Agent always runs (even on blocked) to update stats.
  - The final task JSON is written to --output for inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
import urllib.error
import urllib.request

# Force UTF-8 stdout/stderr on Windows so LLM responses with non-ASCII
# characters don't crash the pipeline with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Path setup + .env loading
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv optional; env vars can be set directly

# ---------------------------------------------------------------------------
# Agent imports
# ---------------------------------------------------------------------------
from agents.pm_agent.pm_agent import run_pm_agent
from agents.architect_agent.architect_agent import run_architect_agent
from agents.coding_agent.runner import run as _coding_runner
from agents.coding_agent.coding_agent import call_bob_coding, read_scoped_file_contents
from agents.testing_agent.testing_agent import run_testing_agent
from agents.review_agent.review_agent import run_review
from agents.manager_agent.manager import (
    load_stats,
    update_stats,
    save_stats,
    generate_report,
    STATS_PATH,
)
from agents.reflection_agent.reflection import run_reflection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RETRIES = 2

# Paths for dashboard persistence
_RUN_HISTORY_PATH = os.path.join(_REPO_ROOT, "dashboard", "run_history.json")
_TASKS_DIR = os.path.join(_REPO_ROOT, "dashboard", "tasks")


# ---------------------------------------------------------------------------
# Shared LLM helpers (Sub-Task 1 from integration-plan.md)
# ---------------------------------------------------------------------------

def _call_llm(prompt: str) -> str:
    """
    Call the Groq LLM chat API.
    Falls back to returning '{}' (empty JSON) on any error so merge produces no changes.
    """
    from orchestration.iam_auth import call_bob_chat

    # If GROQ_API_KEY is explicitly set to empty (e.g. in unit tests), or not set
    if os.environ.get("GROQ_API_KEY") == "":
        return "{}"
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return "{}"

    try:
        return call_bob_chat(prompt, max_tokens=1200)
    except Exception:
        return "{}"


def _parse_agent_json(raw: str) -> dict:
    """
    Strip markdown fences and extract the first JSON object from raw LLM output.
    Raises ValueError if no valid JSON object can be found.
    """
    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    # Find first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Agent returned non-JSON. Parse error: {exc}. Raw (truncated): {raw[:300]!r}"
        ) from exc


def _merge_task(existing: dict, llm_output: dict) -> dict:
    """
    Merge llm_output onto existing task.
    - All scalar fields in llm_output overwrite existing.
    - history is special: if llm_output contains history entries longer than
      existing, use the longer list (LLM appended); otherwise keep existing
      and ignore llm_output's history (prevents accidental truncation).
    - Fields absent in llm_output keep their existing value.
    """
    merged = dict(existing)
    for key, value in llm_output.items():
        if key == "history":
            existing_history = existing.get("history", [])
            llm_history = value if isinstance(value, list) else []
            # Use whichever is longer — LLM should have appended to existing
            merged["history"] = llm_history if len(llm_history) >= len(existing_history) else existing_history
        else:
            merged[key] = value
    return merged


def _safe_agent_call(task: dict, agent_name: str, prompt: str) -> dict:
    """
    Full safe wrapper: call LLM → parse JSON → merge onto task.
    On any error (HTTP, parse, validation): block the task gracefully.
    """
    try:
        raw = _call_llm(prompt)
        llm_output = _parse_agent_json(raw)
        task = _merge_task(task, llm_output)
    except (RuntimeError, ValueError) as exc:
        print(f"[{agent_name}] ERROR: {exc}")
        task.setdefault("history", []).append({
            "agent": agent_name,
            "output_summary": f"LLM error: {str(exc)[:120]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": None,
        })
        task["status"] = "blocked"
        task["current_agent"] = "manager_agent"
    return task


def _build_prompt(agent_name: str, task: dict) -> str:
    """
    Load agents/{agent_name}/system_prompt.md and append the task JSON.
    """
    prompt_path = os.path.join(_REPO_ROOT, "agents", agent_name, "system_prompt.md")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except OSError:
        system_prompt = f"# {agent_name}\nYou are the {agent_name}."

    task_json = json.dumps(task, indent=2)
    return (
        f"{system_prompt}\n\n"
        f"## Input Task\n```json\n{task_json}\n```\n\n"
        "Output only valid JSON. No prose, no explanation outside the JSON."
    )


# ---------------------------------------------------------------------------
# Task factory
# ---------------------------------------------------------------------------

def _make_task(task_id: str, feature_request: str) -> dict:
    """Build a minimal valid AgentTaskObject to start a pipeline run."""
    return {
        "task_id": task_id,
        "feature_request": feature_request,
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


# ---------------------------------------------------------------------------
# Dashboard persistence helpers
# ---------------------------------------------------------------------------

def _append_run_history(manager_report: dict, reflection_results: list, agent_timings: list = None) -> None:
    """Append one run record to dashboard/run_history.json."""
    try:
        os.makedirs(os.path.dirname(_RUN_HISTORY_PATH), exist_ok=True)
        history = []
        if os.path.exists(_RUN_HISTORY_PATH):
            try:
                with open(_RUN_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, OSError):
                history = []

        run_id = len(history) + 1
        agent_rates = {
            agent: data.get("rate")
            for agent, data in manager_report.get("stats_snapshot", {}).items()
        }
        history.append({
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": manager_report.get("task_id", "unknown"),
            "agent_rates": agent_rates,
            "reflections_triggered": [r["agent_name"] for r in (reflection_results or [])],
            "agent_timings": agent_timings or [],
        })

        try:
            with open(_RUN_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
            print(f"[pipeline] Run history updated: run #{run_id} -> {_RUN_HISTORY_PATH}")
        except OSError:
            try:
                home_dir = os.path.join(os.path.expanduser("~"), ".ai_dev_team")
                os.makedirs(home_dir, exist_ok=True)
                fallback = os.path.join(home_dir, "run_history.json")
                with open(fallback, "w", encoding="utf-8") as f:
                    json.dump(history, f, indent=2)
                print(f"[pipeline] Run history updated: run #{run_id} -> {fallback}")
            except Exception:
                pass
    except Exception:
        pass


def _save_task_for_dashboard(task: dict, username: str = "default") -> None:
    """Persist the final task JSON to dashboard/tasks/{username}/{task_id}.json."""
    clean = {k: v for k, v in task.items() if k not in ("manager_report", "reflection_results")}
    task_id = clean.get("task_id", "task_unknown")
    # Sanitise username for use as a directory name
    import re as _re
    safe_username = _re.sub(r'[^a-zA-Z0-9_\-]', '_', username.strip().lower()) or "default"
    user_tasks_dir = os.path.join(_TASKS_DIR, safe_username)
    try:
        os.makedirs(user_tasks_dir, exist_ok=True)
        path = os.path.join(user_tasks_dir, f"{task_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2)
        print(f"[pipeline] Task saved for dashboard: {path}")
    except OSError:
        try:
            home_dir = os.path.join(os.path.expanduser("~"), ".ai_dev_team", "tasks", safe_username)
            os.makedirs(home_dir, exist_ok=True)
            fallback = os.path.join(home_dir, f"{task_id}.json")
            with open(fallback, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=2)
            print(f"[pipeline] Task saved for dashboard: {fallback}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    feature_request: str,
    repo_path: str = "",
    task_id: str = "",
    fast: bool = False,
    username: str = "default",
) -> dict:
    """
    Run the full agent pipeline for *feature_request*.

    Parameters
    ----------
    feature_request : Plain-English description of the feature to build.
    repo_path       : Absolute or relative path to the sample repo root.
    task_id         : Unique identifier. Auto-generated if not provided.

    Returns
    -------
    Final AgentTaskObject dict (with manager_report and reflection_results added).
    """
    if repo_path and not os.path.isdir(repo_path):
        raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

    if not task_id:
        task_id = f"task_{uuid.uuid4().hex[:6]}"

    task = _make_task(task_id, feature_request)
    import time as _time
    agent_timings = []
    print(f"\n{'=' * 60}")
    print(f"[pipeline] Starting task {task_id}")
    print(f"[pipeline] Request: {feature_request}")
    print(f"{'=' * 60}\n")

    # ── 1. PM Agent ──────────────────────────────────────────────────────────
    print("[pipeline] Stage: PM Agent")
    _t0 = _time.time()
    try:
        task = run_pm_agent(task)
    except Exception as exc:
        print(f"[pipeline] PM Agent exception: {exc}")
        task["status"] = "blocked"
        task["current_agent"] = "manager_agent"
        task.setdefault("history", []).append({
            "agent": "pm_agent",
            "output_summary": f"runtime error: {str(exc)[:120]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": False,
        })
    _t1 = _time.time()
    agent_timings.append({
        "agent": "pm_agent",
        "duration_s": round(_t1 - _t0, 2),
        "success": task["status"] != "blocked",
    })
    if task["status"] == "blocked":
        print("[pipeline] PM Agent blocked. Halting.")
        return _finish(task, agent_timings=agent_timings, username=username)

    # ── 2. Architect Agent (up to 2 attempts) ───────────────────────────────
    _ARCH_MAX_ATTEMPTS = 2
    for _arch_attempt in range(1, _ARCH_MAX_ATTEMPTS + 1):
        print(f"[pipeline] Stage: Architect Agent (attempt {_arch_attempt}/{_ARCH_MAX_ATTEMPTS})")
        _t0 = _time.time()
        # Reset status so a previous failure doesn't prevent the retry
        if _arch_attempt > 1:
            task["status"] = "in_progress"
            # Remove last failed history entry so it gets re-appended cleanly
            task["history"] = [
                h for h in task.get("history", [])
                if not (h.get("agent") == "architect_agent" and not h.get("success"))
            ]
        try:
            task = run_architect_agent(task, repo_path=repo_path)
        except Exception as exc:
            print(f"[pipeline] Architect Agent exception: {exc}")
            task["status"] = "blocked"
            task["current_agent"] = "manager_agent"
            task.setdefault("history", []).append({
                "agent": "architect_agent",
                "output_summary": f"runtime error: {str(exc)[:120]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": False,
            })
        _t1 = _time.time()
        agent_timings.append({
            "agent": "architect_agent",
            "duration_s": round(_t1 - _t0, 2),
            "success": task["status"] != "blocked",
        })
        if task["status"] != "blocked":
            break  # Architect succeeded — continue pipeline
        if _arch_attempt < _ARCH_MAX_ATTEMPTS:
            print(f"[pipeline] Architect Agent blocked on attempt {_arch_attempt}. Retrying...")
        else:
            print("[pipeline] Architect Agent blocked after all attempts. Halting.")
            return _finish(task, agent_timings=agent_timings, username=username)

    if not task.get("scoped_files"):
        print("[pipeline] Architect Agent returned empty scoped_files. Halting.")
        task["status"] = "blocked"
        task["current_agent"] = "manager_agent"
        task.setdefault("history", []).append({
            "agent": "architect_agent",
            "output_summary": "Architect Agent returned empty scoped_files",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": False,
        })
        return _finish(task, agent_timings=agent_timings, username=username)

    # ── 3. Coding → Testing → Review loop (capped at MAX_RETRIES) ────────────
    while True:
        print(f"[pipeline] Stage: Coding Agent (retry_count={task.get('retry_count', 0)})")

        # Ensure runner.run Guard 1+2 are satisfied
        task["current_agent"] = "coding_agent"
        if task.get("status") not in ("in_progress", "needs_retry"):
            task["status"] = "in_progress"

        # Bridge: runner.run calls llm_fn(prompt); we delegate to call_bob_coding
        _rp = repo_path
        _task_snapshot = dict(task)  # capture current task state for closure

        _allowed_files = [f.replace("\\", "/") for f in _task_snapshot.get("scoped_files", [])]

        def _llm_fn(prompt: str) -> str:  # noqa: E306
            from orchestration.iam_auth import call_bob_chat
            allowed_str = ", ".join(_allowed_files)
            system_prompt = (
                "You are an expert software engineer generating a git unified diff.\n"
                f"ALLOWED FILES TO MODIFY: {allowed_str}.\n"
                "CRITICAL: Output ONLY a valid git unified diff starting with '--- a/' and containing '@@' hunk markers.\n"
                "Do NOT wrap in markdown fences. Do NOT write prose or explanations. Output ONLY the raw unified diff."
            )
            raw = call_bob_chat(prompt, system_prompt=system_prompt, max_tokens=3000)

            # 1. Strip [ANSWER] and any leading prose
            if "[ANSWER]" in raw:
                raw = raw.replace("[ANSWER]", "").strip()

            # 2. Extract diff if wrapped in ``` fences
            if "```" in raw:
                fence_match = re.search(r"```(?:diff|patch)?\s*\n(.*?)\n```", raw, re.DOTALL)
                if fence_match and "--- " in fence_match.group(1):
                    raw = fence_match.group(1).strip()

            # 3. If there is preamble before '--- ', strip to the first '--- '
            dash_idx = raw.find("--- ")
            if dash_idx != -1:
                raw = raw[dash_idx:].strip()

            # 4. If trailing ``` exists, strip it
            if "```" in raw:
                raw = raw[:raw.find("```")].strip()

            if raw.startswith("{") and "code_diff" in raw:
                try:
                    raw = json.loads(raw).get("code_diff", raw)
                except Exception:
                    pass

            # 5. Fallback: If still no @@ hunk markers, doesn't start with ---, or contains '<path>' template
            has_scoped_file = any(af in raw for af in _allowed_files) if _allowed_files else True
            if "@@" not in raw or not raw.startswith("---") or "<path>" in raw or not has_scoped_file:
                target_file = _allowed_files[0] if _allowed_files else "flaskbb/auth/views.py"
                recovery_prompt = (
                    f"Generate a real git unified diff for file '{target_file}' to implement: {task.get('feature_request')}.\n"
                    f"Use the exact file path '{target_file}'. Do NOT use '<path>' placeholders.\n"
                    f"Example:\n--- a/{target_file}\n+++ b/{target_file}\n@@ -1,1 +1,5 @@\n+<code lines>\n\n"
                    f"Output ONLY the raw unified diff."
                )
                try:
                    recovery_raw = call_bob_chat(recovery_prompt, max_tokens=2000)
                    if "[ANSWER]" in recovery_raw:
                        recovery_raw = recovery_raw.replace("[ANSWER]", "").strip()
                    if "```" in recovery_raw:
                        r_fence = re.search(r"```(?:diff|patch)?\s*\n(.*?)\n```", recovery_raw, re.DOTALL)
                        if r_fence and "--- " in r_fence.group(1):
                            recovery_raw = r_fence.group(1).strip()
                    r_dash = recovery_raw.find("--- ")
                    if r_dash != -1:
                        recovery_raw = recovery_raw[r_dash:].strip()
                    if "```" in recovery_raw:
                        recovery_raw = recovery_raw[:recovery_raw.find("```")].strip()
                    if "@@" in recovery_raw and recovery_raw.startswith("---") and "<path>" not in recovery_raw:
                        raw = recovery_raw
                except Exception:
                    pass

            # 6. Filter diff chunks so ONLY in-scope files are modified
            chunks = re.split(r"(?=^--- )", raw, flags=re.MULTILINE)
            valid_chunks = []
            scoped_norm = {s.strip() for s in _allowed_files}
            for chunk in chunks:
                if not chunk.strip():
                    continue
                m = re.search(r"^\+\+\+ [ab]/(.+)$", chunk, re.MULTILINE)
                if m:
                    p = m.group(1).strip().replace("\\", "/")
                    if p in scoped_norm and "<path>" not in p:
                        valid_chunks.append(chunk)
                elif "@@" in chunk and chunk.startswith("---") and "<path>" not in chunk:
                    valid_chunks.append(chunk)

            if valid_chunks:
                out_diff = "".join(valid_chunks).strip()
            else:
                out_diff = raw.strip()
            from orchestration.apply_fixes import fix_hunk_counts
            return fix_hunk_counts(out_diff)


        try:
            _t0 = _time.time()
            task = _coding_runner(task, repo_root=repo_path, llm_fn=_llm_fn)
            _t1 = _time.time()
            agent_timings.append({
                "agent": "coding_agent",
                "duration_s": round(_t1 - _t0, 2),
                "success": task["status"] != "blocked",
            })
        except Exception as exc:
            print(f"[pipeline] Coding Agent runner error: {exc}")
            task["status"] = "blocked"
            task["current_agent"] = "manager_agent"
            task.setdefault("history", []).append({
                "agent": "coding_agent",
                "output_summary": f"runner error: {str(exc)[:120]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": False,
            })

        if task["status"] == "blocked":
            print("[pipeline] Coding Agent blocked. Halting.")
            break

        # Preflight AST syntax check on generated code diff
        from orchestration.preflight_guard import preflight_diff_check
        code_diff = task.get("code_diff") or ""
        preflight = preflight_diff_check(code_diff)
        if not preflight["passed"] or not preflight["syntax_clean"]:
            err_msg = preflight.get("error") or "preflight syntax check failed"
            print(f"[pipeline] Preflight guard error: {err_msg}")
            task.setdefault("history", []).append({
                "agent": "coding_agent",
                "output_summary": f"preflight syntax check failed: {err_msg[:120]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": False,
            })
            retry_count = task.get("retry_count", 0)
            if retry_count < MAX_RETRIES:
                task["retry_count"] = retry_count + 1
                task["code_diff"] = None
                task["test_results"] = None
                task["current_agent"] = "coding_agent"
                task["status"] = "needs_retry"
                print(f"[pipeline] Preflight syntax check failed. Retry {task['retry_count']}/{MAX_RETRIES}.")
                continue
            else:
                print(f"[pipeline] Max retries ({MAX_RETRIES}) reached on preflight failure. Blocking.")
                task["status"] = "blocked"
                task["current_agent"] = "manager_agent"
                break

        print("[pipeline] Stage: Testing Agent")
        _t0 = _time.time()
        if fast:
            print("[pipeline] Fast testing enabled (baseline comparison skipped)")
        task = run_testing_agent(task, repo_path=repo_path, fast=fast)
        _t1 = _time.time()
        agent_timings.append({
            "agent": "testing_agent",
            "duration_s": round(_t1 - _t0, 2),
            "success": task["status"] != "blocked",
        })
        if task["status"] == "blocked":
            print("[pipeline] Testing Agent blocked. Halting.")
            break

        print("[pipeline] Stage: Review Agent")
        _t0 = _time.time()
        task = run_review(task)
        _t1 = _time.time()
        agent_timings.append({
            "agent": "review_agent",
            "duration_s": round(_t1 - _t0, 2),
            "success": task["status"] != "blocked",
        })
        if task["status"] == "blocked":
            print("[pipeline] Review Agent blocked. Halting.")
            break

        status = task["status"]
        if status == "approved":
            print("[pipeline] [OK] Code approved by Review Agent.")
            if repo_path and os.path.isdir(repo_path) and task.get("code_diff"):
                try:
                    from orchestration.apply_fixes import apply_diff
                    res = apply_diff(task, repo_path=repo_path)
                    if res.get("applied"):
                        print(f"[pipeline] [OK] Approved diff applied to {repo_path}")
                    else:
                        print(f"[pipeline] [!] Could not auto-apply diff to {repo_path}: {res.get('reason', 'unknown')}")

                    for sf in task.get("scoped_files", []):
                        repo_copy = os.path.join(repo_path, sf)
                        root_copy = os.path.join(_REPO_ROOT, sf)
                        if os.path.isfile(repo_copy) and not sf.startswith("flaskbb"):
                            try:
                                with open(repo_copy, "r", encoding="utf-8") as rf:
                                    content = rf.read()
                                with open(root_copy, "w", encoding="utf-8") as wf:
                                    wf.write(content)
                                print(f"[pipeline] [OK] Synced changes to workspace: {sf}")
                            except Exception as sync_exc:
                                cfa_note = ""
                                if getattr(sync_exc, "errno", None) in (2, 9, 13):
                                    cfa_note = " (Windows Defender Controlled Folder Access may be blocking writes in Documents)"
                                print(f"[pipeline] [!] Could not sync {sf} to workspace: {sync_exc}{cfa_note}")
                    
                    # Auto-commit and push if running on cloud
                    if res.get("applied"):
                        try:
                            from orchestration.apply_fixes import git_commit
                            commit_res = git_commit(
                                repo_path=_REPO_ROOT, 
                                task_id=task.get("task_id", "auto"), 
                                feature_request=task.get("feature_request", "Auto-applied pipeline task")
                            )
                            if commit_res.get("committed"):
                                print(f"[pipeline] [OK] Auto-committed and pushed to GitHub: {commit_res.get('commit_output', '').strip()}")
                            else:
                                print(f"[pipeline] [!] Could not auto-commit: {commit_res.get('commit_output', '').strip()}")
                        except Exception as e:
                            print(f"[pipeline] [!] Auto-commit failed: {e}")
                except Exception as exc:
                    print(f"[pipeline] Warning: could not auto-apply diff to {repo_path}: {exc}")
            break
        if status == "awaiting_human_approval":
            print("[pipeline] [OK] Awaiting human approval (low/medium findings).")
            break
        if status == "needs_retry":
            retry_count = task.get("retry_count", 0)
            if retry_count >= MAX_RETRIES:
                print(f"[pipeline] Max retries ({MAX_RETRIES}) reached. Escalating to human.")
                task["status"] = "blocked"
                task["current_agent"] = "human"
                break
            task["retry_count"] = retry_count + 1
            task["code_diff"] = None
            task["test_results"] = None
            task["current_agent"] = "coding_agent"
            # Keep status="needs_retry" — runner.run Guard 2 accepts it
            print(f"[pipeline] Review rejected. Retry {task['retry_count']}/{MAX_RETRIES}.")
            continue

        # Unknown status
        print(f"[pipeline] Unexpected status '{status}'. Halting.")
        break

    return _finish(task, agent_timings=agent_timings, username=username)


def _finish(task: dict, agent_timings: list = None, username: str = "default") -> dict:
    """Run Manager Agent, optional Reflection Agent, persist to dashboard."""
    print("\n[pipeline] Stage: Manager Agent")
    stats = load_stats(STATS_PATH)
    stats = update_stats(task, stats)
    save_stats(stats, STATS_PATH)
    manager_report = generate_report(task, stats)
    task["manager_report"] = manager_report

    underp = manager_report["underperformers"]
    action = manager_report["recommended_action"]
    print(f"[pipeline] Manager: underperformers={underp or 'none'}, action={action}")

    reflection_results: list = []
    if action == "rewrite_prompt":
        print("[pipeline] Stage: Reflection Agent")
        reflection_results = run_reflection(manager_report)
        task["reflection_results"] = reflection_results
        print(f"[pipeline] Reflection Agent rewrote {len(reflection_results)} prompt(s).")

    # Masterpiece: Multi-Agent Consensus & Confidence Scoring
    from orchestration.consensus_arbiter import compute_consensus_score
    task["consensus_report"] = compute_consensus_score(task)

    _append_run_history(manager_report, reflection_results, agent_timings=agent_timings)
    _save_task_for_dashboard(task, username=username)

    c_pct = task['consensus_report']['consensus_score_pct']
    c_lvl = task['consensus_report']['confidence_level']
    print(f"\n[pipeline] -- Final status : {task['status']}")
    print(f"[pipeline] -- Consensus confidence : {c_pct}% ({c_lvl})")
    print(f"[pipeline] -- Final agent  : {task['current_agent']}")
    return task


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Dev Team Pipeline — run all agents end-to-end."
    )
    parser.add_argument("--request", "-r", required=True, help="Feature request in plain English.")
    parser.add_argument(
        "--repo",
        default=os.environ.get("PIPELINE_REPO_PATH", "sample_repo/flaskbb"),
        help="Path to the sample repo root (default: PIPELINE_REPO_PATH or sample_repo/flaskbb).",
    )
    parser.add_argument("--task-id", default="",        help="Optional task ID. Auto-generated if omitted.")
    parser.add_argument("--username", default="default", help="Username for per-user task isolation.")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use patched-repo tests without the baseline comparison.",
    )
    parser.add_argument(
        "--output", "-o",
        default="pipeline_output.json",
        help="Path to write final task JSON (default: pipeline_output.json).",
    )
    args = parser.parse_args()

    result = run_pipeline(
        feature_request=args.request,
        repo_path=args.repo,
        task_id=args.task_id,
        fast=args.fast,
        username=args.username,
    )

    output_str = json.dumps(result, indent=2, default=str)
    try:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
        print(f"\n[pipeline] Output written to {args.output}")
    except OSError:
        try:
            fallback = os.path.join(os.path.expanduser("~"), ".ai_dev_team", os.path.basename(args.output))
            os.makedirs(os.path.dirname(fallback), exist_ok=True)
            with open(fallback, "w", encoding="utf-8") as f:
                f.write(output_str)
            print(f"\n[pipeline] Output written to {fallback}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
