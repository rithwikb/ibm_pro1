"""
test_build.py — Unit tests for orchestration/build.py.
All pipeline and subprocess calls are mocked.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestration.build import (
    init_manifest,
    save_manifest,
    load_manifest,
    run_batch,
)


def _mock_pipeline_result(status="approved", code_diff=None, task_id="build_app_b1"):
    return {
        "task_id": task_id,
        "status": status,
        "code_diff": code_diff,
        "feature_request": "test",
        "acceptance_criteria": [],
        "scoped_files": [],
        "history": [],
        "retry_count": 0,
        "current_agent": "review_agent",
        "plan": None,
        "test_results": None,
        "review_result": {"passed": True, "findings": []},
        "verification_result": None,
        "batch_index": None,
        "total_batches": None,
        "project_name": "app",
    }


class TestInitManifest(unittest.TestCase):
    def test_creates_correct_structure(self):
        batches = [["app.py", "models.py"], ["templates/index.html"]]
        m = init_manifest("todo_app", "Build a todo app", batches)
        self.assertEqual(m["project_name"], "todo_app")
        self.assertEqual(m["status"], "in_progress")
        self.assertEqual(len(m["batches"]), 2)
        self.assertEqual(m["batches"][0]["batch_index"], 1)
        self.assertEqual(m["batches"][0]["files"], ["app.py", "models.py"])
        self.assertEqual(m["batches"][0]["status"], "pending")
        self.assertIsNone(m["batches"][0]["task_id"])

    def test_batch_indices_are_sequential(self):
        m = init_manifest("app", "desc", [["a.py"], ["b.py"], ["c.py"]])
        indices = [b["batch_index"] for b in m["batches"]]
        self.assertEqual(indices, [1, 2, 3])


class TestSaveLoadManifest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = init_manifest("app", "desc", [["app.py"]])
            save_manifest(m, tmpdir)
            loaded = load_manifest(tmpdir)
        self.assertEqual(loaded["project_name"], "app")
        self.assertEqual(loaded["batches"][0]["files"], ["app.py"])

    def test_load_returns_none_for_missing(self):
        result = load_manifest("/nonexistent/path")
        self.assertIsNone(result)

    def test_save_creates_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = os.path.join(tmpdir, "new_project")
            m = init_manifest("app", "desc", [["app.py"]])
            save_manifest(m, project_dir)
            self.assertTrue(os.path.exists(os.path.join(project_dir, "build_manifest.json")))


class TestRunBatch(unittest.TestCase):
    def test_calls_run_pipeline_with_correct_task_id(self):
        mock_result = _mock_pipeline_result(status="approved", code_diff=None)
        with patch("orchestration.build.run_pipeline", return_value=mock_result) as mock_pipe:
            with patch("orchestration.build.apply_diff_to_disk"):
                run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        call_kwargs = mock_pipe.call_args[1]
        self.assertEqual(call_kwargs["task_id"], "build_my_app_b1")

    def test_returns_done_on_approved_status(self):
        mock_result = _mock_pipeline_result(status="approved", code_diff=None)
        with patch("orchestration.build.run_pipeline", return_value=mock_result):
            with patch("orchestration.build.apply_diff_to_disk"):
                result = run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        self.assertEqual(result["status"], "done")

    def test_returns_done_on_awaiting_human_approval(self):
        mock_result = _mock_pipeline_result(status="awaiting_human_approval", code_diff=None)
        with patch("orchestration.build.run_pipeline", return_value=mock_result):
            with patch("orchestration.build.apply_diff_to_disk"):
                result = run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        self.assertEqual(result["status"], "done")

    def test_returns_failed_on_blocked_status(self):
        mock_result = _mock_pipeline_result(status="blocked", code_diff=None)
        with patch("orchestration.build.run_pipeline", return_value=mock_result):
            result = run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        self.assertEqual(result["status"], "failed")

    def test_calls_apply_diff_when_diff_present(self):
        mock_result = _mock_pipeline_result(status="approved", code_diff="--- a/app.py\n+++ b/app.py\n")
        with patch("orchestration.build.run_pipeline", return_value=mock_result):
            with patch("orchestration.build.apply_diff_to_disk") as mock_apply:
                run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        mock_apply.assert_called_once()

    def test_skips_apply_diff_when_no_diff(self):
        mock_result = _mock_pipeline_result(status="approved", code_diff=None)
        with patch("orchestration.build.run_pipeline", return_value=mock_result):
            with patch("orchestration.build.apply_diff_to_disk") as mock_apply:
                run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        mock_apply.assert_not_called()

    def test_returns_failed_on_pipeline_exception(self):
        with patch("orchestration.build.run_pipeline", side_effect=RuntimeError("boom")):
            result = run_batch(["app.py"], 1, 2, "Build an app", "my_app", "/fake/dir")
        self.assertEqual(result["status"], "failed")
        self.assertIn("boom", result["reason"])


class TestApplyDiffToDisk(unittest.TestCase):
    def test_writes_multiple_files_successfully(self):
        from orchestration.build import apply_diff_to_disk
        diff = (
            "--- a/file1.py\n+++ b/file1.py\n@@ -0,0 +1,2 @@\n+print('file1')\n"
            "--- a/file2.py\n+++ b/file2.py\n@@ -0,0 +1,2 @@\n+print('file2')\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            res = apply_diff_to_disk(diff, tmpdir)
            self.assertTrue(res["applied"])
            self.assertIn("file1.py", res["written"])
            self.assertIn("file2.py", res["written"])
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "file1.py")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "file2.py")))

    def test_per_file_error_isolation_continues_batch(self):
        from orchestration.build import apply_diff_to_disk
        diff = (
            "--- a/good1.py\n+++ b/good1.py\n@@ -0,0 +1,2 @@\n+print('good1')\n"
            "--- a/bad.py\n+++ b/bad.py\n@@ -0,0 +1,2 @@\n+print('bad')\n"
            "--- a/good2.py\n+++ b/good2.py\n@@ -0,0 +1,2 @@\n+print('good2')\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_open = open

            def mock_open_fn(file, *args, **kwargs):
                if "bad.py" in str(file):
                    raise PermissionError("Permission denied: bad.py")
                return orig_open(file, *args, **kwargs)

            with patch("builtins.open", side_effect=mock_open_fn):
                res = apply_diff_to_disk(diff, tmpdir)

            self.assertFalse(res["applied"])
            self.assertIn("good1.py", res["written"])
            self.assertIn("good2.py", res["written"])
            self.assertIn("bad.py", res["failed"])
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "good1.py")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "good2.py")))


if __name__ == "__main__":
    unittest.main()
