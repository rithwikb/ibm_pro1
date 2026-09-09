"""
test_apply_fixes.py — Unit tests for orchestration/apply_fixes.py.

All subprocess calls are mocked — no real git or flake8 invocations.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestration.apply_fixes import (
    load_approved_tasks,
    apply_diff,
    run_verification_flake8,
    save_task,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_proc(returncode=0, stdout="", stderr=""):

    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _make_task(**overrides) -> dict:
    base = {
        "task_id": "fix_bug_001",
        "feature_request": "Fix F401 error",
        "status": "approved",
        "code_diff": "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-import os\n+# removed\n",
        "scoped_files": ["flaskbb/auth/views.py"],
        "acceptance_criteria": [],
        "history": [],
        "retry_count": 0,
        "current_agent": "review_agent",
        "review_result": {"passed": True, "findings": []},
        "test_results": None,
        "plan": "Fix it",
        "verification_result": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# load_approved_tasks tests
# ---------------------------------------------------------------------------

class TestLoadApprovedTasks(unittest.TestCase):
    def _write_task(self, tmpdir, task):
        path = os.path.join(tmpdir, f"{task['task_id']}.json")
        with open(path, "w") as f:
            json.dump(task, f)

    def test_returns_only_approved_tasks_with_diff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_task(tmpdir, _make_task(status="approved", code_diff="diff..."))
            self._write_task(tmpdir, _make_task(task_id="t2", status="needs_retry", code_diff="diff..."))
            self._write_task(tmpdir, _make_task(task_id="t3", status="approved", code_diff=None))
            result = load_approved_tasks(tmpdir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["task_id"], "fix_bug_001")

    def test_returns_empty_for_missing_dir(self):
        result = load_approved_tasks("/nonexistent/path/tasks")
        self.assertEqual(result, [])

    def test_skips_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_path = os.path.join(tmpdir, "bad.json")
            with open(bad_path, "w") as f:
                f.write("{not valid json")
            self._write_task(tmpdir, _make_task())
            result = load_approved_tasks(tmpdir)
        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# apply_diff tests
# ---------------------------------------------------------------------------

class TestApplyDiff(unittest.TestCase):
    def test_dry_run_never_calls_real_git_apply(self):
        task = _make_task()
        check_ok = _mock_proc(returncode=0)
        with patch("subprocess.run", return_value=check_ok) as mock_run:
            result = apply_diff(task, "/fake/repo", dry_run=True)
        # Should call git apply --check but NOT git apply
        calls = [str(c) for c in mock_run.call_args_list]
        self.assertTrue(any("--check" in c for c in calls))
        self.assertFalse(any("--check" not in c and "git" in c and "apply" in c for c in calls))
        self.assertFalse(result["applied"])
        self.assertIn("dry-run", result["reason"])

    def test_returns_applied_false_when_check_fails(self):
        task = _make_task()
        check_fail = _mock_proc(returncode=1, stderr="patch does not apply")
        with patch("subprocess.run", return_value=check_fail):
            result = apply_diff(task, "/fake/repo", dry_run=False)
        self.assertFalse(result["applied"])
        # git apply fails → fallback also fails (fake repo path) → applied=False
        self.assertIn("failed", result["reason"])

    def test_returns_applied_true_on_success(self):
        task = _make_task()
        ok = _mock_proc(returncode=0, stdout="")
        with patch("subprocess.run", return_value=ok):
            result = apply_diff(task, "/fake/repo", dry_run=False)
        self.assertTrue(result["applied"])

    def test_git_apply_check_called_first(self):
        task = _make_task()
        ok = _mock_proc(returncode=0)
        with patch("subprocess.run", return_value=ok) as mock_run:
            apply_diff(task, "/fake/repo", dry_run=False)
        first_call_args = mock_run.call_args_list[0][0][0]
        self.assertIn("--check", first_call_args)


# ---------------------------------------------------------------------------
# run_verification_flake8 tests
# ---------------------------------------------------------------------------

class TestRunVerificationFlake8(unittest.TestCase):
    def test_returns_passed_true_on_empty_output(self):
        with patch("subprocess.run", return_value=_mock_proc(stdout="")):
            with patch("os.path.exists", return_value=True):
                result = run_verification_flake8(["flaskbb/auth/views.py"], "/fake/repo")
        self.assertTrue(result["flake8_passed"])

    def test_returns_passed_false_when_errors_present(self):
        flake8_out = "flaskbb/auth/views.py:3:1: F401 'os' imported but unused"
        with patch("subprocess.run", return_value=_mock_proc(stdout=flake8_out)):
            with patch("os.path.exists", return_value=True):
                result = run_verification_flake8(["flaskbb/auth/views.py"], "/fake/repo")
        self.assertFalse(result["flake8_passed"])
        self.assertIn("F401", result["flake8_output"])

    def test_returns_passed_true_when_no_files_exist(self):
        with patch("os.path.exists", return_value=False):
            result = run_verification_flake8(["nonexistent.py"], "/fake/repo")
        self.assertTrue(result["flake8_passed"])
        self.assertIn("no files", result["flake8_output"])


# ---------------------------------------------------------------------------
# save_task tests
# ---------------------------------------------------------------------------

class TestSaveTask(unittest.TestCase):
    def test_writes_task_to_correct_path(self):
        task = _make_task(status="verified_fixed")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_task(task, tmpdir)
            path = os.path.join(tmpdir, "fix_bug_001.json")
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                loaded = json.load(f)
        self.assertEqual(loaded["status"], "verified_fixed")
        self.assertEqual(loaded["task_id"], "fix_bug_001")


if __name__ == "__main__":
    unittest.main()
