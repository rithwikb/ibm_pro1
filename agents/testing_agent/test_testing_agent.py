"""
agents/testing_agent/test_testing_agent.py

Unit tests for the Testing Agent.

All external I/O is mocked:
  - apply_diff_to_temp_copy  (no real repo copy / git)
  - run_repo_test_suite       (no real pytest execution)
  - call_bob_testing          (no real Bob / watsonx call)
"""

from __future__ import annotations

import copy
import os
from unittest.mock import patch

from agents.testing_agent.testing_agent import (
    _ignore_temp_copy_entries,
    run_testing_agent,
    validate_criteria_matched,
)

os.environ.setdefault("GROQ_API_KEY", "unit-test-api-key")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACCEPTANCE_CRITERIA = [
    "Max 5 login attempts per IP per 10 minutes",
    "Returns HTTP 429 when limit exceeded",
    "All existing login tests still pass",
]

_BASE_TASK = {
    "task_id": "task_001",
    "feature_request": "Add rate limiting to the login endpoint",
    "acceptance_criteria": ACCEPTANCE_CRITERIA,
    "scoped_files": [
        "flaskbb/auth/views.py",
        "flaskbb/utils/rate_limit.py",
    ],
    "status": "in_progress",
    "current_agent": "testing_agent",
    "plan": "Add a token-bucket rate limiter middleware keyed by IP.",
    "history": [
        {
            "agent": "pm_agent",
            "output_summary": "criteria defined",
            "timestamp": "2026-08-10T09:00:00Z",
            "success": True,
        },
        {
            "agent": "architect_agent",
            "output_summary": "plan written",
            "timestamp": "2026-08-10T09:05:00Z",
            "success": True,
        },
        {
            "agent": "coding_agent",
            "output_summary": "diff generated",
            "timestamp": "2026-08-10T09:15:00Z",
            "success": True,
        },
    ],
    "code_diff": "--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,2 @@\n-old\n+new",
    "test_results": None,
    "review_result": None,
    "retry_count": 0,
}

_MOCK_TEST_OUTPUT_PASSED = {
    "applied": True,
    "pytest_passed": True,
    "total": 42,
    "passed": 42,
    "failed": 0,
    "new_failures": [],
    "baseline_failures": [],
    "error": None,
}

_MOCK_BOB_ALL_PASSED = {
    "passed": True,
    "criteria_matched": list(ACCEPTANCE_CRITERIA),
    "failures": [],
}

_MOCK_BOB_ONE_FAILED = {
    "passed": False,
    "criteria_matched": [
        "Max 5 login attempts per IP per 10 minutes",
        "All existing login tests still pass",
    ],
    "failures": [
        "Returns HTTP 429 when limit exceeded — diff returns 200 instead of 429",
    ],
}


# ---------------------------------------------------------------------------
# 1. validate_criteria_matched
# ---------------------------------------------------------------------------

class TestValidateCriteriaMatched:
    def test_exact_subset_passes(self):
        output = {
            "criteria_matched": [
                "Max 5 login attempts per IP per 10 minutes",
                "Returns HTTP 429 when limit exceeded",
            ]
        }
        assert validate_criteria_matched(output, ACCEPTANCE_CRITERIA) is True

    def test_full_set_passes(self):
        output = {"criteria_matched": list(ACCEPTANCE_CRITERIA)}
        assert validate_criteria_matched(output, ACCEPTANCE_CRITERIA) is True

    def test_empty_criteria_matched_passes(self):
        output = {"criteria_matched": []}
        assert validate_criteria_matched(output, ACCEPTANCE_CRITERIA) is True

    def test_paraphrased_string_fails(self):
        output = {
            "criteria_matched": [
                "Max 5 attempts allowed",
            ]
        }
        assert validate_criteria_matched(output, ACCEPTANCE_CRITERIA) is False

    def test_fabricated_string_not_in_original_fails(self):
        output = {
            "criteria_matched": [
                "Max 5 login attempts per IP per 10 minutes",
                "Rate limit header is returned in the response",
            ]
        }
        assert validate_criteria_matched(output, ACCEPTANCE_CRITERIA) is False

    def test_case_difference_fails(self):
        output = {
            "criteria_matched": [
                "max 5 login attempts per ip per 10 minutes",
            ]
        }
        assert validate_criteria_matched(output, ACCEPTANCE_CRITERIA) is False


class TestTempCopyIgnore:
    def test_ignores_heavy_generated_directories(self):
        ignored = _ignore_temp_copy_entries(
            "/repo",
            [
                ".git",
                ".venv",
                ".uv-cache",
                "__pycache__",
                "pytest-cache-files-abc",
                ".pytest-tmp",
                "flaskbb",
                "tests",
            ],
        )

        assert ".git" in ignored
        assert ".venv" in ignored
        assert ".uv-cache" in ignored
        assert "__pycache__" in ignored
        assert "pytest-cache-files-abc" in ignored
        assert ".pytest-tmp" in ignored
        assert "flaskbb" not in ignored
        assert "tests" not in ignored


# ---------------------------------------------------------------------------
# 2. Diff failure attribution
# ---------------------------------------------------------------------------

class TestDiffFailureAttribution:
    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy")
    def test_diff_failure_attributed_to_coding_agent(self, mock_apply):
        mock_apply.side_effect = RuntimeError("patch does not apply cleanly")

        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "blocked"
        assert result["current_agent"] == "manager_agent"

        last_entry = result["history"][-1]
        assert last_entry["agent"] == "coding_agent"
        assert last_entry["success"] is False
        assert "patch does not apply cleanly" in last_entry["output_summary"]

    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy")
    def test_diff_failure_attribution_is_coding_agent_not_testing_agent(self, mock_apply):
        mock_apply.side_effect = RuntimeError("corrupt hunk header")

        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        agents_in_history = [e["agent"] for e in result["history"]]
        assert agents_in_history.count("testing_agent") == 0
        assert result["history"][-1]["agent"] == "coding_agent"


# ---------------------------------------------------------------------------
# 3. Testing Agent success:true on both passed=True and passed=False
# ---------------------------------------------------------------------------

class TestTestingAgentSuccessTrue:
    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch("agents.testing_agent.testing_agent.call_bob_testing", return_value=_MOCK_BOB_ALL_PASSED)
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_passed_true_produces_testing_agent_success_true(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "in_progress"
        assert result["current_agent"] == "review_agent"
        assert result["test_results"]["passed"] is True

        last_entry = result["history"][-1]
        assert last_entry["agent"] == "testing_agent"
        assert last_entry["success"] is True

    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch("agents.testing_agent.testing_agent.call_bob_testing", return_value=_MOCK_BOB_ONE_FAILED)
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_passed_false_produces_testing_agent_success_true(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "in_progress"
        assert result["current_agent"] == "review_agent"
        assert result["test_results"]["passed"] is False

        last_entry = result["history"][-1]
        assert last_entry["agent"] == "testing_agent"
        assert last_entry["success"] is True

    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch("agents.testing_agent.testing_agent.call_bob_testing", return_value=_MOCK_BOB_ALL_PASSED)
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_criteria_matched_populated_correctly(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")
        assert result["test_results"]["criteria_matched"] == ACCEPTANCE_CRITERIA

    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch("agents.testing_agent.testing_agent.call_bob_testing", return_value=_MOCK_BOB_ONE_FAILED)
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_failures_populated_correctly(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")
        assert len(result["test_results"]["failures"]) == 1
        assert "Returns HTTP 429" in result["test_results"]["failures"][0]


class TestLocalTestingJudge:
    @patch.dict(os.environ, {"TESTING_AGENT_LOCAL_JUDGE": "1"}, clear=False)
    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch("agents.testing_agent.testing_agent.call_bob_testing")
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_local_judge_skips_bob_when_enabled(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        mock_bob.assert_not_called()
        assert result["status"] == "in_progress"
        assert result["current_agent"] == "review_agent"
        assert result["test_results"]["passed"] is True
        assert result["test_results"]["criteria_matched"] == ACCEPTANCE_CRITERIA

    @patch.dict(os.environ, {"TESTING_AGENT_LOCAL_JUDGE": "1"}, clear=False)
    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch(
        "agents.testing_agent.testing_agent.run_repo_test_suite",
        return_value={
            **_MOCK_TEST_OUTPUT_PASSED,
            "pytest_passed": False,
            "failed": 1,
            "new_failures": ["tests/test_login.py::test_rate_limit"],
        },
    )
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_local_judge_reports_test_failures(self, mock_rm, mock_suite, mock_apply):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "in_progress"
        assert result["test_results"]["passed"] is False
        assert result["test_results"]["criteria_matched"] == []
        assert "tests/test_login.py::test_rate_limit" in result["test_results"]["failures"]


# ---------------------------------------------------------------------------
# 4. Malformed Bob output → testing_agent success:false
# ---------------------------------------------------------------------------

class TestMalformedBobOutput:
    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch("agents.testing_agent.testing_agent.call_bob_testing")
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_bob_raises_value_error_produces_testing_agent_success_false(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        mock_bob.side_effect = ValueError("watsonx response missing 'test_results' key")

        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "blocked"
        assert result["current_agent"] == "manager_agent"

        last_entry = result["history"][-1]
        assert last_entry["agent"] == "testing_agent"
        assert last_entry["success"] is False
        assert "watsonx response missing" in last_entry["output_summary"]

    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch(
        "agents.testing_agent.testing_agent.call_bob_testing",
        return_value={
            "passed": True,
            "criteria_matched": ["A paraphrased version of the criterion"],
            "failures": [],
        },
    )
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_paraphrased_criteria_matched_produces_testing_agent_success_false(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "blocked"
        assert result["current_agent"] == "manager_agent"

        last_entry = result["history"][-1]
        assert last_entry["agent"] == "testing_agent"
        assert last_entry["success"] is False
        assert "paraphrasing or hallucination" in last_entry["output_summary"]

    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch("agents.testing_agent.testing_agent.run_repo_test_suite", return_value=_MOCK_TEST_OUTPUT_PASSED)
    @patch(
        "agents.testing_agent.testing_agent.call_bob_testing",
        return_value={
            "criteria_matched": list(ACCEPTANCE_CRITERIA),
            "failures": [],
        },
    )
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_missing_passed_field_produces_testing_agent_success_false(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "blocked"
        assert result["current_agent"] == "manager_agent"

        last_entry = result["history"][-1]
        assert last_entry["agent"] == "testing_agent"
        assert last_entry["success"] is False


# ---------------------------------------------------------------------------
# 5. Soft pass on "no tests collected"
# ---------------------------------------------------------------------------

class TestNoTestsCollectedSoftPass:
    @patch("agents.testing_agent.testing_agent.apply_diff_to_temp_copy", return_value="/tmp/fake_dir")
    @patch(
        "agents.testing_agent.testing_agent.run_repo_test_suite",
        return_value={
            "applied": True,
            "pytest_passed": True,
            "no_tests_collected": True,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "new_failures": [],
            "baseline_failures": [],
            "error": None,
        },
    )
    @patch(
        "agents.testing_agent.testing_agent.call_bob_testing",
        return_value={
            "passed": True,
            "criteria_matched": list(ACCEPTANCE_CRITERIA),
            "failures": [],
        },
    )
    @patch("agents.testing_agent.testing_agent.shutil.rmtree")
    def test_no_tests_collected_produces_soft_pass(
        self, mock_rm, mock_bob, mock_suite, mock_apply
    ):
        task = copy.deepcopy(_BASE_TASK)
        result = run_testing_agent(task, repo_path="/fake/repo")

        assert result["status"] == "in_progress"
        assert result["current_agent"] == "review_agent"
        assert result["test_results"]["passed"] is True
        expected_note = (
            "no tests were collected for the modified files — result reflects zero-conflict, not verified correctness"
        )
        assert expected_note in result["test_results"]["failures"]
