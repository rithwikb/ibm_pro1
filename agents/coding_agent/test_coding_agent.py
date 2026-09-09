"""
test_coding_agent.py — Tests for agents/coding_agent/coding_agent.py.

Coverage
--------
- validate_scoped_files: passes when all files exist normally
- validate_scoped_files: passes when a nonexistent file is correctly marked NEW FILE
- validate_scoped_files: fails (Architect error) when a nonexistent file is NOT marked NEW FILE
- validate_diff_format: accepts a well-formed unified diff string
- validate_diff_format: rejects plain prose or malformed text
- run_coding_agent: produces the DOUBLE history entries
  (architect_agent success:false AND coding_agent success:null) on Architect error
- run_coding_agent: produces coding_agent success:true on valid mocked Bob response
- run_coding_agent: produces coding_agent success:false (not null) when diff is invalid
- All Bob API calls are mocked; temp directories are used for file existence checks.

Run with:
    python -m pytest agents/coding_agent/test_coding_agent.py -v
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure the project root is on sys.path so imports resolve.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agents.coding_agent.coding_agent import (
    read_scoped_file_contents,
    run_coding_agent,
    validate_diff_format,
    validate_scoped_files,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides) -> dict:
    """Return a minimal valid AgentTaskObject for testing."""
    base = {
        "task_id": "task_test_001",
        "feature_request": "Add rate limiting to login",
        "acceptance_criteria": [
            "Max 5 attempts per IP per 10 minutes",
            "Returns HTTP 429 when exceeded",
        ],
        "scoped_files": ["flaskbb/auth/views.py"],
        "status": "in_progress",
        "current_agent": "coding_agent",
        "plan": "Add rate limiting to the login view.",
        "history": [
            {
                "agent": "pm_agent",
                "output_summary": "criteria defined",
                "timestamp": "2026-08-10T09:00:00Z",
                "success": True,
            },
            {
                "agent": "architect_agent",
                "output_summary": "plan + files scoped",
                "timestamp": "2026-08-10T09:05:00Z",
                "success": True,
            },
        ],
        "code_diff": None,
        "test_results": None,
        "review_result": None,
        "retry_count": 0,
    }
    base.update(overrides)
    return base


VALID_DIFF = (
    "--- a/flaskbb/auth/views.py\n"
    "+++ b/flaskbb/auth/views.py\n"
    "@@ -1,3 +1,4 @@\n"
    " from flask import request\n"
    " from flask_login import login_user\n"
    " from flaskbb.auth.forms import LoginForm\n"
    "+from flaskbb.utils.rate_limiter import RateLimiter\n"
)


def _create_file(directory: str, relative_path: str, content: str = "# placeholder\n") -> None:
    abs_path = os.path.join(directory, relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# Tests: validate_scoped_files
# ---------------------------------------------------------------------------


class TestValidateScopedFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_passes_when_all_files_exist(self):
        """All scoped files exist on disk → (True, "")."""
        _create_file(self.tmp, "flaskbb/auth/views.py")
        _create_file(self.tmp, "flaskbb/utils/helpers.py")
        ok, msg = validate_scoped_files(
            ["flaskbb/auth/views.py", "flaskbb/utils/helpers.py"],
            plan="Add rate limiting.",
            repo_path=self.tmp,
        )
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_passes_when_nonexistent_file_is_marked_new(self):
        """
        A file that does NOT exist on disk is OK if it appears in
        ``NEW FILE: <path>`` lines in plan.
        """
        _create_file(self.tmp, "flaskbb/auth/views.py")
        # flaskbb/utils/rate_limiter.py does NOT exist on disk
        plan = (
            "Add a rate limiter.\n"
            "NEW FILE: flaskbb/utils/rate_limiter.py\n"
            "Wrap the login view."
        )
        ok, msg = validate_scoped_files(
            ["flaskbb/auth/views.py", "flaskbb/utils/rate_limiter.py"],
            plan=plan,
            repo_path=self.tmp,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(msg, "")

    def test_fails_when_nonexistent_file_not_marked_new(self):
        """
        A file that does NOT exist on disk and is NOT in NEW FILE set
        → Architect error → (False, "<descriptive message>").
        """
        _create_file(self.tmp, "flaskbb/auth/views.py")
        # flaskbb/missing_module.py NOT on disk, NOT in plan NEW FILE lines
        plan = "Add rate limiting."
        ok, msg = validate_scoped_files(
            ["flaskbb/auth/views.py", "flaskbb/missing_module.py"],
            plan=plan,
            repo_path=self.tmp,
        )
        self.assertFalse(ok)
        self.assertIn("flaskbb/missing_module.py", msg)
        self.assertIn("not found", msg.lower())

    def test_passes_with_dot_slash_prefix_in_plan(self):
        """
        NEW FILE with leading ./ in plan should normalise and match scoped_files
        without leading ./.
        """
        plan = "NEW FILE: ./flaskbb/new_module.py"
        ok, msg = validate_scoped_files(
            ["flaskbb/new_module.py"],
            plan=plan,
            repo_path=self.tmp,
        )
        self.assertTrue(ok, msg)

    def test_empty_scoped_files_passes(self):
        """Empty scoped_files list → nothing to check → True."""
        ok, msg = validate_scoped_files([], plan="", repo_path=self.tmp)
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_plan_none_treats_all_nonexistent_as_errors(self):
        """None plan → no NEW FILE entries → any missing file is an Architect error."""
        ok, msg = validate_scoped_files(
            ["flaskbb/missing.py"],
            plan=None,
            repo_path=self.tmp,
        )
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Tests: validate_diff_format
# ---------------------------------------------------------------------------


class TestValidateDiffFormat(unittest.TestCase):

    def test_accepts_well_formed_diff(self):
        self.assertTrue(validate_diff_format(VALID_DIFF))

    def test_accepts_new_file_diff(self):
        diff = (
            "--- /dev/null\n"
            "+++ b/flaskbb/utils/rate_limiter.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+# rate limiter\n"
            "+import time\n"
            "+pass\n"
        )
        self.assertTrue(validate_diff_format(diff))

    def test_rejects_plain_prose(self):
        self.assertFalse(validate_diff_format("Here is my implementation plan."))

    def test_rejects_empty_string(self):
        self.assertFalse(validate_diff_format(""))

    def test_rejects_whitespace_only(self):
        self.assertFalse(validate_diff_format("   \n\n  "))

    def test_rejects_missing_hunk_marker(self):
        """Has --- and +++ but no @@ → not a valid diff."""
        diff = "--- a/foo.py\n+++ b/foo.py\nsome content\n"
        self.assertFalse(validate_diff_format(diff))

    def test_rejects_missing_minus_header(self):
        """Has +++ and @@ but no --- → not a valid diff."""
        diff = "+++ b/foo.py\n@@ -1,3 +1,4 @@\n line\n"
        self.assertFalse(validate_diff_format(diff))

    def test_rejects_json_blob_without_diff_markers(self):
        self.assertFalse(validate_diff_format('{"code_diff": "some text"}'))

    def test_rejects_none(self):
        self.assertFalse(validate_diff_format(None))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests: run_coding_agent — Architect error path
# ---------------------------------------------------------------------------


class TestRunCodingAgentArchitectError(unittest.TestCase):
    """
    Most important test group: verify the DOUBLE history entry pattern when
    Architect's scoped_files contains a path that doesn't exist and isn't NEW FILE.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Create ONLY views.py — missing_module.py intentionally absent
        _create_file(self.tmp, "flaskbb/auth/views.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_double_history_entries_on_architect_error(self):
        """
        The MOST IMPORTANT test:
        When validate_scoped_files returns False, run_coding_agent must append
        exactly two history entries:
          [-2] agent="architect_agent", success=False
          [-1] agent="coding_agent",    success=None  (not False — never attempted)
        """
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py", "flaskbb/missing_module.py"],
            plan="Add rate limiting.",  # no NEW FILE declaration
        )
        initial_history_len = len(task["history"])

        with patch("agents.coding_agent.coding_agent.call_bob_coding") as mock_bob:
            result = run_coding_agent(task, repo_path=self.tmp)

        # Bob must NOT have been called
        mock_bob.assert_not_called()

        # History must have grown by exactly 2
        new_entries = result["history"][initial_history_len:]
        self.assertEqual(
            len(new_entries), 2,
            f"Expected 2 new history entries, got {len(new_entries)}: {new_entries}",
        )

        arch_entry = new_entries[0]
        coding_entry = new_entries[1]

        # architect_agent entry: success must be False
        self.assertEqual(arch_entry["agent"], "architect_agent")
        self.assertIs(
            arch_entry["success"], False,
            f"architect_agent entry success must be False, got {arch_entry['success']!r}",
        )
        self.assertIn("flaskbb/missing_module.py", arch_entry["output_summary"])

        # coding_agent entry: success must be None (null), NOT False
        self.assertEqual(coding_entry["agent"], "coding_agent")
        self.assertIsNone(
            coding_entry["success"],
            f"coding_agent entry success must be None (null) on Architect-fault path, "
            f"got {coding_entry['success']!r}",
        )
        self.assertIn("skipped", coding_entry["output_summary"].lower())

    def test_status_is_blocked_on_architect_error(self):
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py", "flaskbb/missing_module.py"],
            plan="Add rate limiting.",
        )
        result = run_coding_agent(task, repo_path=self.tmp)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["current_agent"], "manager_agent")
        self.assertIsNone(result["code_diff"])

    def test_input_task_is_not_mutated(self):
        """run_coding_agent must not mutate the input task object."""
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py", "flaskbb/missing_module.py"],
            plan="Add rate limiting.",
        )
        original_history_len = len(task["history"])
        run_coding_agent(task, repo_path=self.tmp)
        self.assertEqual(len(task["history"]), original_history_len)


# ---------------------------------------------------------------------------
# Tests: run_coding_agent — success path
# ---------------------------------------------------------------------------


class TestRunCodingAgentSuccess(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _create_file(self.tmp, "flaskbb/auth/views.py", content="from flask import request\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_coding_agent_success_true_entry(self):
        """
        Valid scoped_files + valid Bob response → single coding_agent history entry
        with success=True.
        """
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py"],
            plan="Add rate limiting to login view.",
        )
        initial_history_len = len(task["history"])

        mock_response = {"code_diff": VALID_DIFF}

        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value=mock_response,
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        new_entries = result["history"][initial_history_len:]
        self.assertEqual(len(new_entries), 1, f"Expected 1 new entry, got: {new_entries}")

        entry = new_entries[0]
        self.assertEqual(entry["agent"], "coding_agent")
        self.assertIs(
            entry["success"], True,
            f"Expected success=True, got {entry['success']!r}",
        )

    def test_status_and_routing_on_success(self):
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py"],
            plan="Add rate limiting to login view.",
        )
        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value={"code_diff": VALID_DIFF},
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["current_agent"], "testing_agent")
        self.assertEqual(result["code_diff"], VALID_DIFF)

    def test_success_with_new_file_in_plan(self):
        """
        scoped_files contains a new file (in plan NEW FILE) — valid input.
        The new file doesn't exist on disk; it should be skipped in read_scoped_file_contents.
        """
        plan = (
            "Add rate limiter.\n"
            "NEW FILE: flaskbb/utils/rate_limiter.py"
        )
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py", "flaskbb/utils/rate_limiter.py"],
            plan=plan,
        )
        new_file_diff = (
            "--- a/flaskbb/auth/views.py\n"
            "+++ b/flaskbb/auth/views.py\n"
            "@@ -1,3 +1,4 @@\n"
            " from flask import request\n"
            " from flask_login import login_user\n"
            " from flaskbb.auth.forms import LoginForm\n"
            "+from flaskbb.utils.rate_limiter import RateLimiter\n"
            "--- /dev/null\n"
            "+++ b/flaskbb/utils/rate_limiter.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+# rate limiter\n"
            "+import time\n"
            "+pass\n"
        )
        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value={"code_diff": new_file_diff},
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["current_agent"], "testing_agent")
        last = result["history"][-1]
        self.assertIs(last["success"], True)


# ---------------------------------------------------------------------------
# Tests: run_coding_agent — Coding Agent own failure (valid input, bad diff)
# ---------------------------------------------------------------------------


class TestRunCodingAgentOwnFailure(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _create_file(self.tmp, "flaskbb/auth/views.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_success_false_not_null_on_invalid_diff(self):
        """
        Valid scoped_files, but Bob returns a malformed diff → coding_agent
        success=False (NOT null — the agent attempted its task).
        """
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py"],
            plan="Add rate limiting.",
        )
        initial_history_len = len(task["history"])

        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value={"code_diff": "this is not a diff"},
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        new_entries = result["history"][initial_history_len:]
        self.assertEqual(len(new_entries), 1)

        entry = new_entries[0]
        self.assertEqual(entry["agent"], "coding_agent")
        self.assertIs(
            entry["success"], False,
            f"Expected success=False (not null), got {entry['success']!r}",
        )
        # Critically: must NOT be None
        self.assertIsNotNone(entry["success"])

    def test_status_blocked_on_invalid_diff(self):
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py"],
            plan="Add rate limiting.",
        )
        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value={"code_diff": "plain prose, not a diff"},
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["current_agent"], "manager_agent")
        self.assertIsNone(result["code_diff"])

    def test_success_false_on_bob_api_exception(self):
        """Bob API raises RuntimeError → coding_agent success=False."""
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py"],
            plan="Add rate limiting.",
        )
        initial_history_len = len(task["history"])

        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            side_effect=RuntimeError("API timeout"),
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        new_entries = result["history"][initial_history_len:]
        self.assertEqual(len(new_entries), 1)
        entry = new_entries[0]
        self.assertEqual(entry["agent"], "coding_agent")
        self.assertIs(entry["success"], False)
        self.assertIsNotNone(entry["success"])

    def test_success_false_on_empty_diff(self):
        """Bob returns empty string diff → Coding Agent failure."""
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py"],
            plan="Add rate limiting.",
        )
        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value={"code_diff": ""},
        ):
            result = run_coding_agent(task, repo_path=self.tmp)

        self.assertEqual(result["status"], "blocked")
        last = result["history"][-1]
        self.assertIs(last["success"], False)
        self.assertIsNotNone(last["success"])


# ---------------------------------------------------------------------------
# Tests: read_scoped_file_contents
# ---------------------------------------------------------------------------


class TestReadScopedFileContents(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_existing_files(self):
        _create_file(self.tmp, "flaskbb/auth/views.py", content="print('hello')\n")
        contents = read_scoped_file_contents(
            ["flaskbb/auth/views.py"],
            plan="Add rate limiting.",
            repo_path=self.tmp,
        )
        self.assertIn("flaskbb/auth/views.py", contents)
        self.assertEqual(contents["flaskbb/auth/views.py"], "print('hello')\n")

    def test_skips_new_files(self):
        """Files declared NEW FILE in plan are skipped (not read from disk)."""
        plan = "NEW FILE: flaskbb/new_module.py"
        contents = read_scoped_file_contents(
            ["flaskbb/new_module.py"],
            plan=plan,
            repo_path=self.tmp,
        )
        # new file should not appear in contents
        self.assertNotIn("flaskbb/new_module.py", contents)

    def test_returns_empty_string_for_unreadable_file(self):
        """Unreadable file → empty string, no exception raised."""
        contents = read_scoped_file_contents(
            ["flaskbb/ghost.py"],
            plan="",
            repo_path=self.tmp,
        )
        self.assertIn("flaskbb/ghost.py", contents)
        self.assertEqual(contents["flaskbb/ghost.py"], "")


# ---------------------------------------------------------------------------
# Tests: history immutability
# ---------------------------------------------------------------------------


class TestHistoryImmutability(unittest.TestCase):
    """run_coding_agent must return a new dict — it must not mutate the input."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _create_file(self.tmp, "flaskbb/auth/views.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_input_history_not_mutated_on_success(self):
        task = _make_task(scoped_files=["flaskbb/auth/views.py"], plan="Rate limit.")
        original = copy.deepcopy(task)

        with patch(
            "agents.coding_agent.coding_agent.call_bob_coding",
            return_value={"code_diff": VALID_DIFF},
        ):
            run_coding_agent(task, repo_path=self.tmp)

        self.assertEqual(task["history"], original["history"])

    def test_input_history_not_mutated_on_arch_error(self):
        task = _make_task(
            scoped_files=["flaskbb/auth/views.py", "flaskbb/missing.py"],
            plan="Rate limit.",
        )
        original = copy.deepcopy(task)
        run_coding_agent(task, repo_path=self.tmp)
        self.assertEqual(task["history"], original["history"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
