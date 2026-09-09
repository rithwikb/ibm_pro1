"""
test_phase1.py — Integration tests for the full Phase 1 auto-fix flow.
All subprocess, pipeline, and disk calls are mocked.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.bug_discovery_agent.bug_discovery_agent import save_bugs, load_bugs
from orchestration.scan import cmd_scan, cmd_run_selected
from orchestration.apply_fixes import load_approved_tasks, apply_diff, run_verification_flake8

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_FLAKE8_OUT = (
    "flaskbb/auth/views.py:3:1: F401 'os' imported but unused\n"
    "flaskbb/auth/forms.py:12:80: E501 line too long (125 > 120 characters)\n"
)


def _mock_proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _make_pipeline_result(task_id, status="approved"):
    return {
        "task_id": task_id,
        "status": status,
        "code_diff": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n",
        "feature_request": "fix it",
        "acceptance_criteria": [],
        "scoped_files": ["flaskbb/auth/views.py"],
        "history": [],
        "retry_count": 0,
        "current_agent": "review_agent",
        "plan": None,
        "test_results": None,
        "review_result": {"passed": True, "findings": []},
        "verification_result": None,
    }


# ---------------------------------------------------------------------------
# Phase 1 integration: scan → select → run → approve → apply → verify
# ---------------------------------------------------------------------------

class TestPhase1ScanCmd(unittest.TestCase):
    def test_cmd_scan_writes_bugs_json(self):
        with patch("subprocess.run", return_value=_mock_proc(stdout=SAMPLE_FLAKE8_OUT)):
            with tempfile.TemporaryDirectory() as tmpdir:
                bugs_path = os.path.join(tmpdir, "bugs.json")
                cmd_scan("some/source", bugs_path)
                bugs = load_bugs(bugs_path)
        self.assertEqual(len(bugs), 2)
        self.assertEqual(bugs[0]["bug_id"], "bug_001")
        self.assertEqual(bugs[0]["status"], "discovered")
        self.assertEqual(bugs[0]["error_type"], "F401")

    def test_cmd_scan_writes_empty_on_clean_repo(self):
        with patch("subprocess.run", return_value=_mock_proc(stdout="")):
            with tempfile.TemporaryDirectory() as tmpdir:
                bugs_path = os.path.join(tmpdir, "bugs.json")
                cmd_scan("some/source", bugs_path)
                bugs = load_bugs(bugs_path)
        self.assertEqual(bugs, [])


class TestPhase1RunSelected(unittest.TestCase):
    def test_skips_non_selected_bugs(self):
        bugs = [
            {"bug_id": "bug_001", "status": "discovered", "description": "Fix F401"},
            {"bug_id": "bug_002", "status": "selected", "description": "Fix E501"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            bugs_path = os.path.join(tmpdir, "bugs.json")
            save_bugs(bugs, bugs_path)

            mock_result = _make_pipeline_result("fix_bug_002")
            with patch("orchestration.scan.run_pipeline", return_value=mock_result) as mock_pipe:
                cmd_run_selected(bugs_path)

        # Only bug_002 (selected) should trigger a pipeline call
        self.assertEqual(mock_pipe.call_count, 1)
        call_kwargs = mock_pipe.call_args[1]
        self.assertEqual(call_kwargs["task_id"], "fix_bug_002")

    def test_updates_task_id_and_status_after_run(self):
        bugs = [{"bug_id": "bug_001", "status": "selected", "description": "Fix F401"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            bugs_path = os.path.join(tmpdir, "bugs.json")
            save_bugs(bugs, bugs_path)

            mock_result = _make_pipeline_result("fix_bug_001")
            with patch("orchestration.scan.run_pipeline", return_value=mock_result):
                cmd_run_selected(bugs_path)

            updated = load_bugs(bugs_path)

        self.assertEqual(updated[0]["status"], "done")
        self.assertEqual(updated[0]["task_id"], "fix_bug_001")

    def test_prints_warning_when_no_selected_bugs(self, capsys=None):
        bugs = [{"bug_id": "bug_001", "status": "discovered", "description": "Fix F401"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            bugs_path = os.path.join(tmpdir, "bugs.json")
            save_bugs(bugs, bugs_path)
            with patch("orchestration.scan.run_pipeline") as mock_pipe:
                cmd_run_selected(bugs_path)
            mock_pipe.assert_not_called()

    def test_handles_missing_bugs_file_gracefully(self):
        # Should not raise
        cmd_run_selected("/nonexistent/bugs.json")


class TestPhase1ApplyFixes(unittest.TestCase):
    def test_load_approved_tasks_filters_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Approved with diff
            t1 = {"task_id": "t1", "status": "approved", "code_diff": "diff...",
                  "feature_request": "fix", "scoped_files": []}
            # Needs retry — should be excluded
            t2 = {"task_id": "t2", "status": "needs_retry", "code_diff": "diff...",
                  "feature_request": "fix", "scoped_files": []}
            # Approved but no diff — should be excluded
            t3 = {"task_id": "t3", "status": "approved", "code_diff": None,
                  "feature_request": "fix", "scoped_files": []}
            for t in [t1, t2, t3]:
                with open(os.path.join(tmpdir, f"{t['task_id']}.json"), "w") as f:
                    json.dump(t, f)
            result = load_approved_tasks(tmpdir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["task_id"], "t1")

    def test_apply_diff_dry_run_returns_not_applied(self):
        task = {"task_id": "t1", "code_diff": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n"}
        ok_proc = _mock_proc(returncode=0)
        with patch("subprocess.run", return_value=ok_proc):
            result = apply_diff(task, "/fake/repo", dry_run=True)
        self.assertFalse(result["applied"])
        self.assertIn("dry-run", result["reason"])

    def test_verification_passes_on_empty_flake8_output(self):
        with patch("subprocess.run", return_value=_mock_proc(stdout="")):
            with patch("os.path.exists", return_value=True):
                result = run_verification_flake8(["flaskbb/auth/views.py"], "/fake/repo")
        self.assertTrue(result["flake8_passed"])

    def test_verification_fails_on_flake8_errors(self):
        with patch("subprocess.run", return_value=_mock_proc(stdout="file.py:1:1: F401 msg")):
            with patch("os.path.exists", return_value=True):
                result = run_verification_flake8(["file.py"], "/fake/repo")
        self.assertFalse(result["flake8_passed"])


if __name__ == "__main__":
    unittest.main()
