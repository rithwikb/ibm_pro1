"""
Review Agent — standalone test harness.

Runs all tests WITHOUT hitting the watsonx API.
Each test injects a mock LLM response and validates that run_review() produces
the correct AgentTaskObject output.

Usage:
    python run_review_tests.py           # run all tests
    python run_review_tests.py -v        # verbose output
    python run_review_tests.py --live    # run one real API call (requires env vars)

Exit code 0 = all tests passed, 1 = one or more failures.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

# Make review_agent importable when running from within the review_agent/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_agent import run_review, SECURITY_CHECKLIST, _load_schema, _validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:

    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _make_mock_response(task: dict, passed: bool, findings: list) -> str:
    """Build a realistic mock LLM response (valid AgentTaskObject JSON)."""
    updated = dict(task)
    updated["current_agent"] = "review_agent"
    updated["review_result"] = {"passed": passed, "findings": findings}
    if not passed and any(f["severity"] in ("high", "critical") for f in findings):
        updated["status"] = "needs_retry"
    elif not passed:
        updated["status"] = "awaiting_human_approval"
    else:
        updated["status"] = "approved"

    history_entry = {
        "agent": "review_agent",
        "output_summary": "approved — no issues found" if passed else f"{len(findings)} issue(s) found",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": passed,
    }
    updated["history"] = list(task.get("history", [])) + [history_entry]
    return json.dumps(updated)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""

    def ok(self):
        self.passed = True
        return self

    def fail(self, msg: str):
        self.message = msg
        return self


def _run_test(name: str, fn) -> TestResult:
    result = TestResult(name)
    try:
        fn(result)
    except Exception as exc:  # noqa: BLE001
        result.message = f"unexpected exception: {exc}\n{traceback.format_exc()}"
    return result


# ---- Test 1: rejects task with null code_diff ----
def test_null_code_diff(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    task["code_diff"] = None
    try:
        run_review(task)
        r.fail("expected ValueError but none was raised")
    except ValueError as exc:
        if "code_diff" in str(exc):
            r.ok()
        else:
            r.fail(f"ValueError raised but wrong message: {exc}")


# ---- Test 2: buggy diff → status becomes 'needs_retry' ----
def test_buggy_diff_sets_needs_retry(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    mock = _make_mock_response(task, passed=False, findings=[
        {
            "checklist_item": "Hardcoded secrets",
            "file": "src/auth/login.py",
            "line": 7,
            "severity": "critical",
            "description": "DB_PASSWORD is hardcoded as a string literal in the module scope.",
        },
        {
            "checklist_item": "Insecure deserialization",
            "file": "src/auth/login.py",
            "line": 20,
            "severity": "critical",
            "description": "pickle.loads() called on untrusted raw_bytes with no type constraints.",
        },
        {
            "checklist_item": "Verbose error leakage",
            "file": "src/auth/login.py",
            "line": 16,
            "severity": "high",
            "description": "Exception message exposes DB_PASSWORD value in error output.",
        },
    ])
    result = run_review(task, mock_response=mock)
    if result["status"] != "needs_retry":
        r.fail(f"expected status='needs_retry', got '{result['status']}'")
        return
    if result["current_agent"] != "review_agent":
        r.fail(f"expected current_agent='review_agent', got '{result['current_agent']}'")
        return
    if result["review_result"]["passed"] is not False:
        r.fail("expected review_result.passed=false")
        return
    r.ok()


# ---- Test 3: clean diff → status becomes 'approved' ----
def test_clean_diff_sets_approved(r: TestResult):
    task = _load_fixture("task_clean.json")
    mock = _make_mock_response(task, passed=True, findings=[])
    result = run_review(task, mock_response=mock)
    if result["status"] != "approved":
        r.fail(f"expected status='approved', got '{result['status']}'")
        return
    if result["review_result"]["passed"] is not True:
        r.fail("expected review_result.passed=true")
        return
    if result["review_result"]["findings"] != []:
        r.fail(f"expected empty findings, got {result['review_result']['findings']}")
        return
    r.ok()


# ---- Test 4: history entry is appended, not replaced ----
def test_history_entry_appended(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    original_history_len = len(task["history"])
    mock = _make_mock_response(task, passed=False, findings=[
        {
            "checklist_item": "Hardcoded secrets",
            "file": "src/auth/login.py",
            "line": 7,
            "severity": "critical",
            "description": "Hardcoded DB_PASSWORD.",
        }
    ])
    result = run_review(task, mock_response=mock)
    new_len = len(result["history"])
    if new_len != original_history_len + 1:
        r.fail(f"expected history length {original_history_len + 1}, got {new_len}")
        return
    last = result["history"][-1]
    if last["agent"] != "review_agent":
        r.fail(f"last history entry agent should be 'review_agent', got '{last['agent']}'")
        return
    if "timestamp" not in last:
        r.fail("last history entry missing 'timestamp'")
        return
    r.ok()


# ---- Test 5: output passes JSON schema validation ----
def test_output_validates_against_schema(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    mock = _make_mock_response(task, passed=False, findings=[
        {
            "checklist_item": "Hardcoded secrets",
            "file": "src/auth/login.py",
            "line": 7,
            "severity": "critical",
            "description": "Hardcoded DB_PASSWORD.",
        }
    ])
    result = run_review(task, mock_response=mock)
    schema = _load_schema()
    errors = _validate(result, schema)
    if errors:
        r.fail("output failed schema validation:\n" + "\n".join(f"  - {e}" for e in errors))
        return
    r.ok()


# ---- Test 6: only low/medium findings → awaiting_human_approval ----
def test_low_medium_findings_set_awaiting_human(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    mock = _make_mock_response(task, passed=False, findings=[
        {
            "checklist_item": "Verbose error leakage",
            "file": "src/auth/login.py",
            "line": 16,
            "severity": "medium",
            "description": "Error message may reveal internal state.",
        }
    ])
    result = run_review(task, mock_response=mock)
    if result["status"] != "awaiting_human_approval":
        r.fail(f"expected status='awaiting_human_approval', got '{result['status']}'")
        return
    r.ok()


# ---- Test 7: malformed LLM response does not crash — status becomes 'blocked' ----
def test_malformed_response_becomes_blocked(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    bad_response = "Sure! Here are my thoughts on the code: it looks mostly fine but..."
    result = run_review(task, mock_response=bad_response)
    if result["status"] != "blocked":
        r.fail(f"expected status='blocked' for malformed response, got '{result['status']}'")
        return
    if result["review_result"] is not None:
        r.fail("expected review_result=null for blocked task")
        return
    r.ok()


# ---- Test 8: immutable fields are preserved ----
def test_immutable_fields_preserved(r: TestResult):
    task = _load_fixture("task_clean.json")
    mock = _make_mock_response(task, passed=True, findings=[])
    result = run_review(task, mock_response=mock)
    for field in ("task_id", "feature_request", "acceptance_criteria", "scoped_files", "retry_count"):
        if result.get(field) != task.get(field):
            r.fail(f"field '{field}' was mutated: {task.get(field)!r} → {result.get(field)!r}")
            return
    r.ok()


# ---- Test 9: system prompt contains all 9 checklist items ----
def test_system_prompt_contains_all_checklist_items(r: TestResult):
    from review_agent import SYSTEM_PROMPT
    for item in SECURITY_CHECKLIST:
        # Check the first ~30 chars of each item (enough to be distinctive)
        fragment = item[:30]
        if fragment not in SYSTEM_PROMPT:
            r.fail(f"system prompt is missing checklist item starting with: '{fragment}'")
            return
    if len(SECURITY_CHECKLIST) != 9:
        r.fail(f"expected 9 checklist items, found {len(SECURITY_CHECKLIST)}")
        return
    r.ok()


# ---------------------------------------------------------------------------
# Live smoke test (optional — requires real API creds)
# ---------------------------------------------------------------------------
def test_live_api(r: TestResult):
    task = _load_fixture("task_with_bugs.json")
    try:
        result = run_review(task)  # no mock — hits real API
    except EnvironmentError as exc:
        r.fail(f"env var missing: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        r.fail(f"API call failed: {exc}")
        return
    if result.get("review_result") is None:
        r.fail("live API returned null review_result")
        return
    if "passed" not in result["review_result"]:
        r.fail("live API response missing review_result.passed")
        return
    r.ok()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
TESTS = [
    ("null code_diff raises ValueError", test_null_code_diff),
    ("buggy diff -> status=needs_retry", test_buggy_diff_sets_needs_retry),
    ("clean diff -> status=approved", test_clean_diff_sets_approved),
    ("history entry appended correctly", test_history_entry_appended),
    ("output passes schema validation", test_output_validates_against_schema),
    ("low/medium findings -> awaiting_human_approval", test_low_medium_findings_set_awaiting_human),
    ("malformed LLM response -> status=blocked", test_malformed_response_becomes_blocked),
    ("immutable fields preserved", test_immutable_fields_preserved),
    ("system prompt contains all 9 checklist items", test_system_prompt_contains_all_checklist_items),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true", help="Show pass details too")
    parser.add_argument("--live", action="store_true", help="Also run live API test (needs env vars)")
    args = parser.parse_args()

    tests_to_run = list(TESTS)
    if args.live:
        tests_to_run.append(("live API smoke test", test_live_api))

    results = []
    for name, fn in tests_to_run:
        r = _run_test(name, fn)
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        if r.passed and args.verbose:
            print(f"  {icon}  {name}")
        elif not r.passed:
            print(f"  {icon}  {name}")
            print(f"         {r.message}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
