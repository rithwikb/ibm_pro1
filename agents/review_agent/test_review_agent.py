"""
test_review_agent.py — Standard unit tests for Review Agent (review_agent.py).

All tests run without hitting the watsonx API.
Runs seamlessly with `pytest agents/review_agent/test_review_agent.py` or root `pytest`.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from agents.review_agent.review_agent import (  # noqa: E402
    run_review,
    SECURITY_CHECKLIST,
    SYSTEM_PROMPT,
    _load_schema,
    _validate,
)


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


class TestReviewAgent(unittest.TestCase):
    def test_missing_code_diff_raises_value_error(self):
        task = _load_fixture("task_with_bugs.json")
        task["code_diff"] = None
        with self.assertRaises(ValueError) as ctx:
            run_review(task)
        self.assertIn("code_diff", str(ctx.exception))

    def test_buggy_diff_sets_needs_retry(self):
        task = _load_fixture("task_with_bugs.json")
        mock = _make_mock_response(task, passed=False, findings=[
            {
                "checklist_item": "Hardcoded secrets",
                "file": "src/auth/login.py",
                "line": 7,
                "severity": "critical",
                "description": "DB_PASSWORD is hardcoded as a string literal.",
            },
            {
                "checklist_item": "Verbose error leakage",
                "file": "src/auth/login.py",
                "line": 16,
                "severity": "high",
                "description": "Exception message exposes DB_PASSWORD value.",
            },
        ])
        result = run_review(task, mock_response=mock)
        self.assertEqual(result["status"], "needs_retry")
        self.assertEqual(result["current_agent"], "review_agent")
        self.assertFalse(result["review_result"]["passed"])

    def test_clean_diff_sets_approved(self):
        task = _load_fixture("task_clean.json")
        mock = _make_mock_response(task, passed=True, findings=[])
        result = run_review(task, mock_response=mock)
        self.assertEqual(result["status"], "approved")
        self.assertTrue(result["review_result"]["passed"])
        self.assertEqual(result["review_result"]["findings"], [])

    def test_history_entry_appended(self):
        task = _load_fixture("task_with_bugs.json")
        orig_len = len(task["history"])
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
        self.assertEqual(len(result["history"]), orig_len + 1)
        self.assertEqual(result["history"][-1]["agent"], "review_agent")
        self.assertIn("timestamp", result["history"][-1])

    def test_output_validates_against_schema(self):
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
        self.assertEqual(errors, [])

    def test_low_medium_findings_set_awaiting_human(self):
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
        self.assertEqual(result["status"], "awaiting_human_approval")

    def test_malformed_response_becomes_blocked(self):
        task = _load_fixture("task_with_bugs.json")
        bad_response = "Sure! Here are my thoughts on the code: it looks mostly fine but..."
        result = run_review(task, mock_response=bad_response)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["review_result"])

    def test_immutable_fields_preserved(self):
        task = _load_fixture("task_clean.json")
        mock = _make_mock_response(task, passed=True, findings=[])
        result = run_review(task, mock_response=mock)
        for field in ("task_id", "feature_request", "acceptance_criteria", "scoped_files", "retry_count"):
            self.assertEqual(result.get(field), task.get(field))

    def test_system_prompt_contains_all_checklist_items(self):
        for item in SECURITY_CHECKLIST:
            fragment = item[:30]
            self.assertIn(fragment, SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
