"""
test_bug_discovery_agent.py — Unit tests for bug_discovery_agent.py.

All subprocess calls are mocked — no real flake8 invocation.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agents.bug_discovery_agent.bug_discovery_agent import (
    scan_flake8,
    scan_pytest,
    description_from_flake8,
    description_from_pytest,
    scan_repo,
    save_bugs,
    load_bugs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_FLAKE8_OUTPUT = """\
flaskbb/auth/views.py:3:1: F401 'os' imported but unused
flaskbb/auth/forms.py:12:80: E501 line too long (125 > 120 characters)
flaskbb/forum/views.py:45:5: F841 local variable 'result' is assigned to but never used
"""

MALFORMED_LINES = """\
This is a header line
flaskbb/auth/views.py:3:1: F401 'os' imported but unused
Another non-matching line without colons
"""

SAMPLE_PYTEST_OUTPUT = """\
FAILED tests/test_auth.py::test_login_invalid_password - AssertionError: expected 401 got 200
ERROR tests/test_api.py::test_token_refresh - RuntimeError: DB connection failed
"""


def _make_proc(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# scan_flake8 tests
# ---------------------------------------------------------------------------

class TestScanFlake8(unittest.TestCase):
    @patch("subprocess.run")
    def test_parses_standard_output(self, mock_run):
        mock_run.return_value = _make_proc(SAMPLE_FLAKE8_OUTPUT)
        bugs = scan_flake8("sample_repo/flaskbb")

        self.assertEqual(len(bugs), 3)
        self.assertEqual(bugs[0]["file"], "flaskbb/auth/views.py")
        self.assertEqual(bugs[0]["line"], 3)
        self.assertEqual(bugs[0]["col"], 1)
        self.assertEqual(bugs[0]["error_type"], "F401")
        self.assertIn("os", bugs[0]["message"])

    @patch("subprocess.run")
    def test_returns_empty_list_on_clean_output(self, mock_run):
        mock_run.return_value = _make_proc("")
        bugs = scan_flake8("sample_repo/flaskbb")
        self.assertEqual(bugs, [])

    @patch("subprocess.run")
    def test_skips_malformed_lines_gracefully(self, mock_run):
        mock_run.return_value = _make_proc(MALFORMED_LINES)
        bugs = scan_flake8("sample_repo/flaskbb")
        self.assertEqual(len(bugs), 1)
        self.assertEqual(bugs[0]["error_type"], "F401")

    @patch("subprocess.run", side_effect=FileNotFoundError("flake8 not found"))
    def test_handles_missing_flake8_gracefully(self, mock_run):
        bugs = scan_flake8("sample_repo/flaskbb")
        self.assertEqual(bugs, [])

    @patch("subprocess.run")
    def test_handles_non_integer_line_col(self, mock_run):
        mock_run.return_value = _make_proc("file.py:abc:def: E999 bad syntax\n")
        bugs = scan_flake8("sample_repo/flaskbb")
        self.assertEqual(len(bugs), 1)
        self.assertEqual(bugs[0]["line"], 0)
        self.assertEqual(bugs[0]["col"], 0)


# ---------------------------------------------------------------------------
# description_from_flake8 tests
# ---------------------------------------------------------------------------

class TestDescriptionFromFlake8(unittest.TestCase):
    def test_format(self):
        desc = description_from_flake8("flaskbb/auth/views.py", 3, "F401", "'os' imported but unused")
        self.assertIn("F401", desc)
        self.assertIn("flaskbb/auth/views.py", desc)
        self.assertIn("3", desc)
        self.assertIn("'os' imported but unused", desc)


# ---------------------------------------------------------------------------
# scan_pytest tests
# ---------------------------------------------------------------------------

class TestScanPytest(unittest.TestCase):
    @patch("subprocess.run")
    def test_parses_pytest_failures_and_errors(self, mock_run):
        mock_run.return_value = _make_proc(SAMPLE_PYTEST_OUTPUT)
        bugs = scan_pytest("sample_repo/flaskbb")

        self.assertEqual(len(bugs), 2)
        self.assertEqual(bugs[0]["file"], "tests/test_auth.py")
        self.assertEqual(bugs[0]["error_type"], "FAILED")
        self.assertIn("AssertionError", bugs[0]["message"])

        self.assertEqual(bugs[1]["file"], "tests/test_api.py")
        self.assertEqual(bugs[1]["error_type"], "ERROR")
        self.assertIn("RuntimeError", bugs[1]["message"])

    @patch("subprocess.run")
    def test_returns_empty_on_clean_pytest(self, mock_run):
        mock_run.return_value = _make_proc("5 passed in 0.12s")
        bugs = scan_pytest("sample_repo/flaskbb")
        self.assertEqual(bugs, [])

    @patch("subprocess.run", side_effect=FileNotFoundError("pytest not found"))
    def test_handles_missing_pytest_gracefully(self, mock_run):
        bugs = scan_pytest("sample_repo/flaskbb")
        self.assertEqual(bugs, [])

    @patch("subprocess.run")
    def test_returns_empty_when_module_not_found(self, mock_run):
        mock_run.return_value = _make_proc("", returncode=1, stderr="No module named pytest")
        bugs = scan_pytest("sample_repo/flaskbb")
        self.assertEqual(bugs, [])

    @patch("subprocess.run")
    def test_scan_pytest_empty_on_no_failures(self, mock_run):
        mock_run.return_value = _make_proc("10 passed in 0.05s")
        result = scan_pytest("tests")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# description_from_pytest tests
# ---------------------------------------------------------------------------

class TestDescriptionFromPytest(unittest.TestCase):
    def test_extracts_test_name(self):
        desc = description_from_pytest("FAILED tests/test_auth.py::test_login_invalid_password - AssertionError")
        self.assertEqual(desc, "Fix failing test: test_login_invalid_password")

    def test_extracts_test_name_error(self):
        desc = description_from_pytest("ERROR tests/test_auth.py::test_login_invalid_password")
        self.assertEqual(desc, "Fix failing test: test_login_invalid_password")

    def test_fallback(self):
        desc = description_from_pytest("FAILED tests/test_auth.py - AssertionError")
        self.assertEqual(desc, "Fix failing test: tests/test_auth.py")


# ---------------------------------------------------------------------------
# scan_repo tests
# ---------------------------------------------------------------------------

class TestScanRepo(unittest.TestCase):
    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_pytest")
    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_flake8")
    def test_assigns_sequential_bug_ids_and_combines(self, mock_flake8, mock_pytest):
        mock_flake8.return_value = [
            {"file": "f.py", "line": 1, "col": 1, "error_type": "E1", "message": "msg", "raw_output": ""}
        ]
        mock_pytest.return_value = [
            {
                "file": "t.py", "line": 0, "col": 0, "error_type": "FAILED",
                "message": "err", "raw_output": "FAILED t.py::test",
            }
        ]

        bugs = scan_repo("some/dir")
        self.assertEqual(len(bugs), 2)

        self.assertEqual(bugs[0]["bug_id"], "bug_001")
        self.assertEqual(bugs[0]["source"], "flake8")

        self.assertEqual(bugs[1]["bug_id"], "bug_002")
        self.assertEqual(bugs[1]["source"], "pytest")
        self.assertEqual(bugs[1]["description"], "Fix failing test: test")

    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_pytest")
    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_flake8")
    def test_sets_status_discovered(self, mock_flake8, mock_pytest):
        mock_flake8.return_value = [
            {"file": "f.py", "line": 1, "col": 1, "error_type": "E1", "message": "msg", "raw_output": ""}
        ]
        mock_pytest.return_value = []
        bugs = scan_repo("some/dir")
        for bug in bugs:
            self.assertEqual(bug["status"], "discovered")

    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_pytest")
    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_flake8")
    def test_sets_task_id_none(self, mock_flake8, mock_pytest):
        mock_flake8.return_value = [
            {"file": "f.py", "line": 1, "col": 1, "error_type": "E1", "message": "msg", "raw_output": ""}
        ]
        mock_pytest.return_value = []
        bugs = scan_repo("some/dir")
        for bug in bugs:
            self.assertIsNone(bug["task_id"])

    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_pytest")
    @patch("agents.bug_discovery_agent.bug_discovery_agent.scan_flake8")
    def test_returns_empty_on_clean_repo(self, mock_flake8, mock_pytest):
        mock_flake8.return_value = []
        mock_pytest.return_value = []
        bugs = scan_repo("some/dir")
        self.assertEqual(bugs, [])


# ---------------------------------------------------------------------------
# save_bugs / load_bugs round-trip tests
# ---------------------------------------------------------------------------

class TestSaveLoadBugs(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self):
        bugs = [
            {
                "bug_id": "bug_001",
                "source": "flake8",
                "file": "flaskbb/auth/views.py",
                "line": 3,
                "col": 1,
                "error_type": "F401",
                "message": "'os' imported but unused",
                "description": "Fix flake8 error F401 in flaskbb/auth/views.py line 3 — 'os' imported but unused",
                "raw_output": "flaskbb/auth/views.py:3:1: F401 'os' imported but unused",
                "status": "discovered",
                "task_id": None,
                "discovered_at": "2026-08-28T00:00:00Z",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "bugs.json")
            save_bugs(bugs, out_file)
            loaded = load_bugs(out_file)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["bug_id"], "bug_001")
        self.assertEqual(loaded[0]["source"], "flake8")
        self.assertEqual(loaded[0]["file"], "flaskbb/auth/views.py")
        self.assertEqual(loaded[0]["line"], 3)
        self.assertEqual(loaded[0]["col"], 1)
        self.assertEqual(loaded[0]["error_type"], "F401")
        self.assertEqual(loaded[0]["status"], "discovered")
        self.assertIsNone(loaded[0]["task_id"])

    def test_load_bugs_returns_empty_list_for_missing_file(self):
        result = load_bugs("non_existent_path/bugs.json")
        self.assertEqual(result, [])

    def test_load_bugs_returns_empty_list_for_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = os.path.join(tmpdir, "corrupt.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{not valid json")
            result = load_bugs(bad_file)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
