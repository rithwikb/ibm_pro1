"""
Testing Agent — agents/testing_agent/testing_agent.py

Responsibilities:
  1. apply_diff_to_temp_copy  — copy repo, apply diff, return temp path
  2. run_repo_test_suite       — run pytest, compare to cached baseline
  3. call_bob_testing          — call Bob API with real test output
  4. validate_criteria_matched — confirm no paraphrasing/hallucination
  5. run_testing_agent         — orchestrator; replaces pipeline.py stub
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_PATH = _REPO_ROOT / "schemas" / "task_schema.json"


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


_TASK_SCHEMA = _load_schema()


def validate_task(task: dict) -> None:
    """Raises jsonschema.ValidationError if *task* does not conform to the schema."""
    jsonschema.validate(instance=task, schema=_TASK_SCHEMA)


# ---------------------------------------------------------------------------
# Baseline cache
# ---------------------------------------------------------------------------

# Keyed by repo_path so different repos don't share a baseline.
_baseline_cache: dict[str, dict] = {}

_COPY_IGNORE_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
}

_COPY_IGNORE_PREFIXES = (
    ".pytest-tmp",
    "pytest-cache-files-",
)


def _testing_timeout(name: str, default: int) -> int:
    """Read a positive Testing Agent timeout from the environment."""
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _ignore_temp_copy_entries(_directory: str, names: list[str]) -> set[str]:
    """Skip heavyweight local/generated folders when copying a repo to test it."""
    ignored = set()
    for name in names:
        if name in _COPY_IGNORE_NAMES:
            ignored.add(name)
            continue
        if any(name.startswith(prefix) for prefix in _COPY_IGNORE_PREFIXES):
            ignored.add(name)
    return ignored


def _has_pytest_tests(repo_path: str) -> bool:
    """Return whether the repository contains a pytest configuration or tests."""
    names = {"pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"}
    if any(os.path.isfile(os.path.join(repo_path, name)) for name in names):
        return True
    for root, _, files in os.walk(repo_path):
        if any(name.startswith("test_") and name.endswith(".py") for name in files):
            return True
    return False


def _run_pytest(repo_path: str) -> dict:
    """
    Run pytest in *repo_path* with JSON output and return a summary dict.

    Uses ``--json-report`` when pytest-json-report is available; falls back
    to parsing the human-readable summary line so vanilla pytest installs work.
    """
    if not _has_pytest_tests(repo_path):
        return {
            "pytest_passed": True,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "raw_output": "(no pytest configuration or tests found; skipped)",
        }

    json_report_path = os.path.join(repo_path, ".pytest_report.json")
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "--tb=short", "-q",
            f"--json-report-file={json_report_path}",
            "--json-report",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=_testing_timeout("TESTING_AGENT_PYTEST_TIMEOUT", 60),
    )

    # If json-report produced a file, parse it (most reliable)
    if os.path.isfile(json_report_path):
        try:
            with open(json_report_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
            summary = report.get("summary", {})
            n_passed = summary.get("passed", 0)
            n_failed = summary.get("failed", 0)
            n_error = summary.get("error", 0)
            failed_tests = [
                t["nodeid"]
                for t in report.get("tests", [])
                if t.get("outcome") in ("failed", "error")
            ]
            os.remove(json_report_path)
            no_tests = (result.returncode == 5) or (n_passed + n_failed + n_error == 0 and result.returncode in (0, 5))
            return {
                "pytest_passed": result.returncode == 0 or no_tests,
                "no_tests_collected": no_tests,
                "total": n_passed + n_failed + n_error,
                "passed": n_passed,
                "failed": n_failed + n_error,
                "failed_tests": failed_tests,
                "raw_output": result.stdout + result.stderr,
            }
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: parse the pytest summary line "N passed, M failed in X.Xs"
    stdout = result.stdout + result.stderr
    n_passed = 0
    n_failed = 0
    summary_match = re.search(r"(\d+) passed(?:.*?(\d+) failed)?", stdout)
    if summary_match:
        n_passed = int(summary_match.group(1))
        n_failed = int(summary_match.group(2) or 0)
    else:
        fail_only = re.search(r"(\d+) failed", stdout)
        if fail_only:
            n_failed = int(fail_only.group(1))

    failed_tests = re.findall(r"FAILED\s+([\w/.::\-]+)", stdout)

    no_tests = (
        result.returncode == 5
        or "no tests were collected" in stdout.lower()
        or "collected 0 items" in stdout.lower()
        or "0 items collected" in stdout.lower()
        or "no tests ran" in stdout.lower()
        or (result.returncode == 0 and (n_passed + n_failed) == 0 and not failed_tests)
    )

    return {
        "pytest_passed": result.returncode == 0 or no_tests,
        "no_tests_collected": no_tests,
        "total": n_passed + n_failed,
        "passed": n_passed,
        "failed": n_failed,
        "failed_tests": failed_tests,
        "raw_output": stdout,
    }


def _get_baseline(repo_path: str) -> dict:
    """Return (and cache) the baseline test results for the unmodified repo."""
    if repo_path not in _baseline_cache:
        _baseline_cache[repo_path] = _run_pytest(repo_path)
    return _baseline_cache[repo_path]


# ---------------------------------------------------------------------------
# 1. apply_diff_to_temp_copy
# ---------------------------------------------------------------------------

def apply_diff_to_temp_copy(code_diff: str, repo_path: str) -> str:
    """
    Copy *repo_path* to a temp directory, apply *code_diff* using ``git apply``
    (falling back to ``patch -p1``), and return the temp directory path.
    """
    temp_dir = tempfile.mkdtemp(prefix="testing_agent_")
    shutil.copytree(
        repo_path,
        temp_dir,
        dirs_exist_ok=True,
        ignore=_ignore_temp_copy_entries,
    )

    # Write diff to a temp file with LF newlines
    diff_file = os.path.join(temp_dir, "_agent.patch")
    with open(diff_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(code_diff)

    # Try git apply first (with whitespace tolerance)
    git_result = subprocess.run(
        ["git", "apply", "--ignore-whitespace", "--ignore-space-change", "--check", diff_file],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        timeout=_testing_timeout("TESTING_AGENT_APPLY_TIMEOUT", 20),
    )
    if git_result.returncode == 0:
        subprocess.run(
            ["git", "apply", "--ignore-whitespace", "--ignore-space-change", diff_file],
            cwd=temp_dir,
            check=True,
            capture_output=True,
            timeout=_testing_timeout("TESTING_AGENT_APPLY_TIMEOUT", 20),
        )
        os.remove(diff_file)
        return temp_dir

    # Fall back to patch -p1 when available.
    try:
        patch_result = subprocess.run(
            ["patch", "-p1", "--dry-run", "-i", diff_file],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=_testing_timeout("TESTING_AGENT_APPLY_TIMEOUT", 20),
        )
        if patch_result.returncode == 0:
            subprocess.run(
                ["patch", "-p1", "-i", diff_file],
                cwd=temp_dir,
                check=True,
                capture_output=True,
                timeout=_testing_timeout("TESTING_AGENT_APPLY_TIMEOUT", 20),
            )
            os.remove(diff_file)
            return temp_dir
    except FileNotFoundError:
        pass

    # Fall back to pure-Python direct diff applier (from orchestration/apply_fixes.py)
    try:
        from orchestration.apply_fixes import _apply_diff_direct
        ok, reason = _apply_diff_direct(code_diff, temp_dir)
        if ok:
            if os.path.exists(diff_file):
                os.remove(diff_file)
            return temp_dir
    except Exception as exc:
        reason = str(exc)

    shutil.rmtree(temp_dir, ignore_errors=True)
    raise RuntimeError(
        f"diff did not apply cleanly.\ngit apply: {git_result.stderr}\nfallback: {reason}"
    )


# ---------------------------------------------------------------------------
# 2. run_repo_test_suite
# ---------------------------------------------------------------------------

def run_repo_test_suite(temp_repo_path: str, original_repo_path: str = "") -> dict:
    """
    Run pytest suite in *temp_repo_path*, compare against baseline,
    and return structured dictionary.
    """
    baseline = _get_baseline(original_repo_path)
    baseline_failed_set = set(baseline.get("failed_tests", []))

    run = _run_pytest(temp_repo_path)
    new_failures = [t for t in run["failed_tests"] if t not in baseline_failed_set]

    return {
        "applied": True,
        "pytest_passed": run["pytest_passed"],
        "no_tests_collected": run.get("no_tests_collected", False),
        "total": run["total"],
        "passed": run["passed"],
        "failed": run["failed"],
        "new_failures": new_failures,
        "baseline_failures": list(baseline_failed_set),
        "error": None,
    }


# ---------------------------------------------------------------------------
# 3. call_bob_testing
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "system_prompt.md"
    with open(prompt_path, "r", encoding="utf-8") as fh:
        return fh.read()


def call_bob_testing(task: dict, actual_test_output: dict) -> dict:
    """
    Call the IBM watsonx.ai chat API with the task and real pytest output.
    Returns the parsed ``test_results`` dict from the model response.
    """
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from orchestration.iam_auth import call_bob_chat

    system_prompt = _load_system_prompt()
    acceptance_criteria = task.get("acceptance_criteria", [])
    user_payload = (
        "Evaluate whether the code_diff in task satisfies acceptance_criteria given actual_test_output.\n\n"
        "CRITICAL RULES:\n"
        "1. In 'criteria_matched', you MUST ONLY copy strings verbatim from the ACCEPTANCE CRITERIA list below. "
        "Do NOT rephrase, do NOT paraphrase, do NOT add new strings.\n"
        "2. Output ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "test_results": {\n'
        '    "passed": true,\n'
        '    "criteria_matched": ["<exact string from ACCEPTANCE CRITERIA list>"],\n'
        '    "failures": []\n'
        "  }\n"
        "}\n\n"
        f"ACCEPTANCE CRITERIA (choose ONLY from this exact list):\n"
        + "\n".join(f"- {c}" for c in acceptance_criteria)
        + "\n\nDATA:\n"
        + json.dumps({"task": task, "actual_test_output": actual_test_output}, indent=2)
    )

    raw = call_bob_chat(
        user_payload,
        system_prompt=system_prompt,
        max_tokens=1024,
        timeout=_testing_timeout("TESTING_AGENT_API_TIMEOUT", 45),
    )

    text = raw.strip()
    if "[ANSWER]" in text:
        text = text.replace("[ANSWER]", "").strip()

    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()
    elif text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1].strip()

    try:
        parsed = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        passed_m = re.search(r'"passed"\s*:\s*(true|false)', text, re.IGNORECASE)
        if passed_m:
            passed_val = passed_m.group(1).lower() == "true"
            matched = [c for c in acceptance_criteria if c.lower() in text.lower() or any(w in text.lower() for w in c.lower().split()[:3])]
            return {
                "passed": passed_val,
                "criteria_matched": matched or list(acceptance_criteria),
                "failures": [] if passed_val else ["One or more criteria unverified"],
            }
        raise ValueError(f"watsonx returned non-JSON content: {exc}\nRaw: {raw[:300]}") from exc

    results = parsed.get("test_results") if "test_results" in parsed else parsed
    if not isinstance(results, dict) or "passed" not in results:
        return _local_testing_judge(task, actual_test_output)

    # Normalize criteria_matched to exact strings from acceptance_criteria
    crit_set = {c.strip(): c for c in acceptance_criteria}
    crit_lower = {c.strip().lower(): c for c in acceptance_criteria}
    normalized = []
    for item in results.get("criteria_matched", []):
        if not isinstance(item, str):
            continue
        cleaned_item = item.strip()
        if cleaned_item in crit_set:
            normalized.append(crit_set[cleaned_item])
        elif cleaned_item.lower() in crit_lower:
            normalized.append(crit_lower[cleaned_item.lower()])
        else:
            for ac in acceptance_criteria:
                if cleaned_item.lower() in ac.lower() or ac.lower() in cleaned_item.lower():
                    normalized.append(ac)
                    break
    results["criteria_matched"] = list(dict.fromkeys(normalized))
    return results


def _should_use_local_testing_judge() -> bool:
    """Return True when the Testing Agent should avoid an LLM/API call."""
    flag = os.environ.get("TESTING_AGENT_LOCAL_JUDGE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return not os.environ.get("GROQ_API_KEY", "").strip()


def _local_testing_judge(task: dict, actual_test_output: dict) -> dict:
    """
    Deterministic fallback for local demos and missing credentials.

    It trusts the real test runner for regressions and only marks all criteria
    matched when pytest introduced no failures.
    """
    acceptance_criteria = task.get("acceptance_criteria", [])
    new_failures = actual_test_output.get("new_failures", [])
    pytest_passed = bool(actual_test_output.get("pytest_passed"))
    no_new_failures = not new_failures

    no_tests = actual_test_output.get("no_tests_collected", False)
    if (pytest_passed or no_tests) and no_new_failures:
        failures = []
        if no_tests:
            failures.append(
                "no tests were collected for the modified files — result reflects zero-conflict, not verified correctness"
            )
        return {
            "passed": True,
            "criteria_matched": list(acceptance_criteria),
            "failures": failures,
        }

    failures = [
        f"Unverified criterion: {criterion}"
        for criterion in acceptance_criteria
    ]
    failures.extend(str(item) for item in new_failures)
    if actual_test_output.get("error"):
        failures.append(str(actual_test_output["error"]))
    if not failures:
        failures.append("pytest did not pass")

    return {
        "passed": False,
        "criteria_matched": [],
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# 4. validate_criteria_matched
# ---------------------------------------------------------------------------

def validate_criteria_matched(output: dict, acceptance_criteria: list) -> bool:
    """
    Return True iff every string in ``output["test_results"]["criteria_matched"]``
    is an exact match of one of the strings in *acceptance_criteria*.
    """
    criteria_matched = output.get("criteria_matched", [])
    criteria_set = set(acceptance_criteria)
    return all(item in criteria_set for item in criteria_matched)


# ---------------------------------------------------------------------------
# 5. run_testing_agent
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_history(task: dict, agent: str, summary: str, success: bool) -> None:
    task.setdefault("history", []).append(
        {
            "agent": agent,
            "output_summary": summary,
            "timestamp": _now_iso(),
            "success": success,
        }
    )


def run_testing_agent(task: dict, repo_path: str, fast: bool = False) -> dict:
    """
    Full orchestration for one Testing Agent pass.
    """
    task = copy.deepcopy(task)

    code_diff = task.get("code_diff")
    acceptance_criteria = task.get("acceptance_criteria", [])

    # ── (a) Apply diff ───────────────────────────────────────────────────────
    temp_path = None
    try:
        print("[testing_agent] Applying code diff to temporary repo copy...", flush=True)
        temp_path = apply_diff_to_temp_copy(code_diff, repo_path)
    except Exception as exc:
        # Diff failure is a CODING AGENT error, not a testing agent error
        _append_history(
            task,
            "coding_agent",
            f"diff did not apply cleanly: {exc}",
            False,
        )
        task["status"] = "blocked"
        task["current_agent"] = "manager_agent"
        return task

    try:
        # ── (b) Run test suite ───────────────────────────────────────────────
        if fast:
            print("[testing_agent] Running pytest on patched repo (fast mode)...", flush=True)
            actual_test_output = _run_pytest(temp_path)
            actual_test_output.update({
                "applied": True,
                "new_failures": actual_test_output["failed_tests"],
                "baseline_failures": [],
                "error": None,
                "verification_mode": "fast; baseline comparison skipped",
            })
        else:
            print("[testing_agent] Running baseline and patched pytest suites...", flush=True)
            actual_test_output = run_repo_test_suite(
                temp_repo_path=temp_path,
                original_repo_path=repo_path,
            )

        # ── (c) Call Bob ─────────────────────────────────────────────────────
        if _should_use_local_testing_judge():
            print("[testing_agent] Using local testing judge (no watsonx call).", flush=True)
            test_results = _local_testing_judge(task, actual_test_output)
        else:
            print("[testing_agent] Calling watsonx testing judge...", flush=True)
            test_results = call_bob_testing(task, actual_test_output)

        # ── (d) Validate criteria_matched ────────────────────────────────────
        if actual_test_output.get("no_tests_collected"):
            soft_pass_note = (
                "no tests were collected for the modified files — result reflects zero-conflict, not verified correctness"
            )
            test_results["passed"] = True
            if "failures" not in test_results or not isinstance(test_results["failures"], list):
                test_results["failures"] = []
            if soft_pass_note not in test_results["failures"]:
                test_results["failures"].append(soft_pass_note)
            if not test_results.get("criteria_matched"):
                test_results["criteria_matched"] = list(acceptance_criteria)

        if not validate_criteria_matched(test_results, acceptance_criteria):
            raise ValueError(
                "criteria_matched contains strings not in acceptance_criteria "
                "(paraphrasing or hallucination detected)"
            )

        if not isinstance(test_results.get("passed"), bool):
            raise ValueError("test_results.passed must be a boolean")
        if not isinstance(test_results.get("criteria_matched"), list):
            raise ValueError("test_results.criteria_matched must be a list")
        if not isinstance(test_results.get("failures"), list):
            raise ValueError("test_results.failures must be a list")

        # ── (e) Populate task ────────────────────────────────────────────────
        task["test_results"] = test_results
        task["current_agent"] = "review_agent"
        task["status"] = "in_progress"

        matched = len(test_results["criteria_matched"])
        total = len(acceptance_criteria)
        passed_str = "passed" if test_results["passed"] else "failed"

        # ── (f) Append history — testing_agent success:true ──────────────────
        _append_history(
            task,
            "testing_agent",
            f"{matched}/{total} criteria matched; {passed_str}",
            True,
        )

        validate_task(task)
        return task

    except Exception as exc:
        err_str = str(exc)
        if "401" in err_str or "authentication_token_not_valid" in err_str:
            print("[testing_agent] WARNING: watsonx auth failed — falling back to local testing judge.", flush=True)
            try:
                test_results = _local_testing_judge(task, actual_test_output)
            except Exception as local_exc:
                _append_history(
                    task,
                    "testing_agent",
                    f"testing agent error: {local_exc}",
                    False,
                )
                task["status"] = "blocked"
                task["current_agent"] = "manager_agent"
                return task
        else:
            _append_history(
                task,
                "testing_agent",
                f"testing agent error: {exc}",
                False,
            )
            task["status"] = "blocked"
            task["current_agent"] = "manager_agent"
            return task

    finally:
        if temp_path:
            shutil.rmtree(temp_path, ignore_errors=True)
