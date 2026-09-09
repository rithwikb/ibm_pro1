"""
test_runner.py — Fixture-driven tests for the Coding Agent runner.

Each of the 10 JSON fixtures in agents/coding_agent/fixtures/ has an
"_expected" block that defines assertions. This test module loads each
fixture, drives runner.run() with a mock LLM (or no-LLM for guard-only
paths), and verifies the output against the "_expected" block.

Run with:
    python -m pytest agents/coding_agent/test_runner.py -v
or:
    python agents/coding_agent/test_runner.py

Test isolation:
    Fixtures that depend on disk state ("_disk_state" key) use a
    temporary directory populated with the files described in "_disk_state".
    Fixtures with no "_disk_state" key run against an empty temp directory
    so all guard 7c checks fail as expected (files declared as existing
    won't actually exist).

Mock LLM strategy:
    - Fixtures whose _expected has llm_must_not_be_invoked: true use a
      sentinel LLM that raises AssertionError if called.
    - Fixtures whose _expected has status == "in_progress" (success path)
      use a minimal valid mock diff that only references paths in scoped_files.
    - test_scope_violation verifies diff scope enforcement by injecting a
      diff that touches an out-of-scope file and checking that the runner
      blocks it with the correct failure output.
"""

import copy
import json
import os
import re
import sys
import tempfile
import unittest

# Ensure the project root (two levels above this file) is on the path.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agents.coding_agent.runner import CodingAgentRoutingError, run

_FIXTURES_DIR = os.path.join(_THIS_DIR, "fixtures")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:
    path = os.path.join(_FIXTURES_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _strip_private_keys(task: dict) -> dict:
    """Return a copy of the fixture with all '_*' keys removed."""
    return {k: v for k, v in task.items() if not k.startswith("_")}


def _sentinel_llm(prompt: str) -> str:
    raise AssertionError(
        "LLM must NOT have been invoked on this fixture, but it was. Prompt:\n"
        + prompt[:200]
    )


def _make_minimal_diff(scoped_files: list, new_files: set) -> str:
    """
    Build a minimal but structurally-valid unified diff that references
    only the paths in scoped_files (normalised).  Each file gets a trivial
    one-line hunk so the diff passes all four post-processing checks.
    """
    blocks = []
    for raw_path in scoped_files:
        norm = _normalise_path(raw_path)
        if norm in new_files:
            blocks.append(
                f"--- /dev/null\n"
                f"+++ b/{norm}\n"
                f"@@ -0,0 +1,1 @@\n"
                f"+# new file\n"
            )
        else:
            blocks.append(
                f"--- a/{norm}\n"
                f"+++ b/{norm}\n"
                f"@@ -1,3 +1,4 @@\n"
                f" # existing\n"
                f" # existing\n"
                f" # existing\n"
                f"+# added line\n"
            )
    return "".join(blocks)


def _normalise_path(path: str) -> str:
    path = re.sub(r"/+", "/", path)
    while path.startswith("./"):
        path = path[2:]
    return path


def _parse_new_files(plan: str) -> set:
    result = set()
    for line in (plan or "").splitlines():
        if line.startswith("NEW FILE: "):
            raw = line[len("NEW FILE: "):].strip()
            result.add(_normalise_path(raw))
    return result


def _populate_repo(tmp_dir: str, disk_state: dict, scoped_files: list) -> None:
    """
    Create files in tmp_dir according to disk_state and scoped_files.

    disk_state values:
        "EXISTS on disk"         → create a minimal placeholder file
        "DOES NOT EXIST on disk" → do NOT create the file
        (keys starting with "_" are metadata notes — skip)

    Any scoped file whose path is not mentioned in disk_state and does not
    start with '_' is created by default so the runner can read it.
    """
    mentioned = {_normalise_path(k): v for k, v in disk_state.items()
                 if not k.startswith("_")}

    # First pass: files explicitly mentioned in disk_state
    for norm_path, existence in mentioned.items():
        if "EXISTS on disk" in existence and "DOES NOT EXIST" not in existence:
            _create_placeholder(tmp_dir, norm_path)
        # "DOES NOT EXIST" → do nothing

    # Second pass: scoped files not mentioned → create them (they must exist
    # for the runner to read them on happy-path and retry fixtures)
    for raw_path in scoped_files:
        norm = _normalise_path(raw_path)
        if norm not in mentioned:
            _create_placeholder(tmp_dir, norm)


def _create_placeholder(tmp_dir: str, norm_path: str) -> None:
    abs_path = os.path.join(tmp_dir, norm_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    if not os.path.exists(abs_path):
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(f"# placeholder: {norm_path}\n")


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------


class FixtureTestCase(unittest.TestCase):
    """
    Base class for fixture-driven test cases.

    Subclasses set:
        fixture_name   str   — matches the JSON file name (without .json)
        llm_fn         callable | None  — None means use sentinel (must-not-invoke)
        inject_diff    str | None       — if set, the LLM returns this string verbatim
    """
    fixture_name: str = ""
    llm_fn = None
    inject_diff: str = None

    def setUp(self):
        self.raw_fixture = _load_fixture(self.fixture_name)
        self.fixture = _strip_private_keys(self.raw_fixture)
        self.expected = self.raw_fixture.get("_expected", {})
        self.disk_state = self.raw_fixture.get("_disk_state", {})

        # Create temp repo directory and populate it.
        self._tmp = tempfile.mkdtemp()
        _populate_repo(
            self._tmp,
            self.disk_state,
            self.fixture.get("scoped_files", []),
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _get_llm(self):
        if self.inject_diff is not None:
            _diff = self.inject_diff

            def _injected_llm(prompt):
                return _diff
            return _injected_llm
        if self.llm_fn is not None:
            return self.llm_fn
        if self.expected.get("llm_must_not_be_invoked"):
            return _sentinel_llm
        # Default: generate a minimal valid diff for the fixture's scoped files.
        new_files = _parse_new_files(self.fixture.get("plan", ""))
        scoped = self.fixture.get("scoped_files", [])

        def _default_llm(prompt):
            return _make_minimal_diff(scoped, new_files)
        return _default_llm

    def run_fixture(self) -> dict:
        return run(
            task=copy.deepcopy(self.fixture),
            repo_root=self._tmp,
            llm_fn=self._get_llm(),
        )

    # -----------------------------------------------------------------------
    # Generic assertion helpers
    # -----------------------------------------------------------------------

    def assert_immutable_fields(self, output: dict):
        fields = self.expected.get("immutable_fields_unchanged", [])
        for field in fields:
            self.assertEqual(
                output.get(field),
                self.fixture.get(field),
                msg=f"Immutable field '{field}' was modified",
            )

    def assert_status_and_agent(self, output: dict):
        exp_status = self.expected.get("status")
        exp_agent = self.expected.get("current_agent")
        if exp_status:
            self.assertEqual(output["status"], exp_status, "status mismatch")
        if exp_agent:
            self.assertEqual(output["current_agent"], exp_agent, "current_agent mismatch")

    def assert_history_last_entry(self, output: dict):
        exp_last = self.expected.get("history_last_entry")
        if not exp_last:
            return
        history = output.get("history", [])
        self.assertTrue(history, "history is empty")
        last = history[-1]
        if "agent" in exp_last:
            self.assertEqual(last["agent"], exp_last["agent"], "history last entry agent mismatch")
        if "success" in exp_last:
            self.assertEqual(last["success"], exp_last["success"], "history last entry success mismatch")
        if "output_summary" in exp_last:
            self.assertEqual(
                last["output_summary"], exp_last["output_summary"],
                "history last entry output_summary mismatch",
            )
        if "output_summary_pattern" in exp_last:
            self.assertIn(
                exp_last["output_summary_pattern"],
                last["output_summary"],
                "output_summary_pattern not found in output_summary",
            )

    def assert_history_new_entries(self, output: dict):
        exp_entries = self.expected.get("history_new_entries")
        if not exp_entries:
            return
        input_len = len(self.fixture.get("history", []))
        output_history = output.get("history", [])
        new_entries = output_history[input_len:]
        self.assertEqual(
            len(new_entries), len(exp_entries),
            f"Expected {len(exp_entries)} new history entries, got {len(new_entries)}",
        )
        for i, (actual, expected_entry) in enumerate(zip(new_entries, exp_entries)):
            for key, val in expected_entry.items():
                self.assertEqual(
                    actual.get(key), val,
                    f"history_new_entries[{i}].{key}: expected {val!r}, got {actual.get(key)!r}",
                )

    def assert_history_count_delta(self, output: dict):
        delta = self.expected.get("history_entry_count_delta")
        if delta is None:
            return
        input_len = len(self.fixture.get("history", []))
        output_len = len(output.get("history", []))
        self.assertEqual(
            output_len - input_len,
            delta,
            f"Expected history to grow by {delta}, grew by {output_len - input_len}",
        )

    def assert_code_diff(self, output: dict):
        exp = self.expected.get("code_diff")
        if exp is None and "code_diff" in self.expected:
            # Explicitly expected null
            self.assertIsNone(output.get("code_diff"), "code_diff should be null")
        elif isinstance(exp, str) and exp.startswith("<") and exp.endswith(">"):
            # Pattern placeholder like "<non-null unified diff string>" — just check non-null
            self.assertIsNotNone(
                output.get("code_diff"),
                f"code_diff should be non-null (pattern: {exp})",
            )
        elif exp is not None:
            self.assertEqual(output.get("code_diff"), exp, "code_diff value mismatch")


# ---------------------------------------------------------------------------
# Test cases — one per fixture
# ---------------------------------------------------------------------------


class TestFirstRun(FixtureTestCase):
    """Happy path — first run, produces diff, routes to testing_agent."""
    fixture_name = "test_first_run"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_code_diff(output)
        self.assert_history_last_entry(output)
        self.assert_immutable_fields(output)
        # code_diff must be a non-empty string
        self.assertIsNotNone(output["code_diff"])
        self.assertIsInstance(output["code_diff"], str)
        self.assertGreater(len(output["code_diff"].strip()), 0)


class TestRetryWithFindings(FixtureTestCase):
    """Retry after Review Agent rejection — revises diff, routes to testing_agent."""
    fixture_name = "test_retry_with_findings"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_history_last_entry(output)
        self.assert_immutable_fields(output)
        # retry_count must be unchanged
        self.assertEqual(output["retry_count"], self.expected["retry_count"])
        # code_diff must be non-null
        self.assertIsNotNone(output["code_diff"])


class TestRetryCap(FixtureTestCase):
    """Guard 3 — retry_count == 2 with needs_retry. Must NOT invoke LLM."""
    fixture_name = "test_retry_cap"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_history_last_entry(output)
        self.assert_immutable_fields(output)
        # code_diff must be preserved (not null)
        self.assertEqual(
            output["code_diff"],
            self.fixture["code_diff"],
            "code_diff must be preserved verbatim on retry-cap path",
        )


class TestMissingPlan(FixtureTestCase):
    """Guard 4 — plan is null. Coding Agent failure."""
    fixture_name = "test_missing_plan"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_code_diff(output)
        self.assert_history_last_entry(output)
        self.assert_history_count_delta(output)
        self.assert_immutable_fields(output)


class TestScopeViolation(FixtureTestCase):
    """
    Diff scope enforcement. Injects a diff that references an out-of-scope
    file; expects the runner to catch it and return a failure output.
    """
    fixture_name = "test_scope_violation"
    # Inject a diff that touches an out-of-scope path forbidden by the fixture.
    inject_diff = (
        "--- a/src/auth/utils.py\n"
        "+++ b/src/auth/utils.py\n"
        "@@ -1,3 +1,4 @@\n"
        " # existing\n"
        " # existing\n"
        " # existing\n"
        "+# scope violation\n"
    )

    def test_scope_violation_blocked(self):
        """With a scope-violating diff, runner must block with correct failure."""
        output = self.run_fixture()
        self.assertEqual(output["status"], "blocked")
        self.assertEqual(output["current_agent"], "manager_agent")
        self.assertIsNone(output["code_diff"])
        history = output["history"]
        last = history[-1]
        self.assertEqual(last["agent"], "coding_agent")
        self.assertEqual(last["success"], False)
        self.assertIn("diff references out-of-scope file", last["output_summary"])
        self.assert_immutable_fields(output)

    def test_clean_diff_passes(self):
        """With a clean diff (only scoped paths), runner must succeed."""
        new_files = _parse_new_files(self.fixture.get("plan", ""))
        scoped = self.fixture.get("scoped_files", [])
        clean_diff = _make_minimal_diff(scoped, new_files)
        output = run(
            task=copy.deepcopy(self.fixture),
            repo_root=self._tmp,
            llm_fn=lambda _: clean_diff,
        )
        self.assertEqual(output["status"], "in_progress")
        self.assertEqual(output["current_agent"], "testing_agent")
        self.assertIsNotNone(output["code_diff"])


class TestEmptyScopedFiles(FixtureTestCase):
    """Guard 5 — scoped_files is empty. Coding Agent failure."""
    fixture_name = "test_empty_scoped_files"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_code_diff(output)
        self.assert_history_last_entry(output)
        self.assert_history_count_delta(output)
        self.assert_immutable_fields(output)


class TestNewFileValid(FixtureTestCase):
    """Valid NEW FILE: declaration — creates new file via --- /dev/null header."""
    fixture_name = "test_new_file_valid"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_history_last_entry(output)
        self.assert_history_count_delta(output)
        self.assert_immutable_fields(output)
        # code_diff must contain new-file header
        self.assertIsNotNone(output["code_diff"])
        self.assertIn("--- /dev/null", output["code_diff"])
        self.assertIn("+++ b/src/middleware/rate_limit.py", output["code_diff"])

    def test_output_summary_includes_new_file(self):
        output = self.run_fixture()
        history = output["history"]
        last = history[-1]
        self.assertIn("including 1 new file(s)", last["output_summary"])


class TestArchitectFaultMissingPath(FixtureTestCase):
    """Guard 7c — scoped file path does not exist on disk, not declared new."""
    fixture_name = "test_architect_fault_missing_path"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_code_diff(output)
        self.assert_history_count_delta(output)
        self.assert_history_new_entries(output)
        self.assert_immutable_fields(output)


class TestArchitectFaultNewFileNotScoped(FixtureTestCase):
    """Guard 7a — NEW FILE: path not in scoped_files. Architect-fault."""
    fixture_name = "test_architect_fault_new_file_not_scoped"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_code_diff(output)
        self.assert_history_count_delta(output)
        self.assert_history_new_entries(output)
        self.assert_immutable_fields(output)


class TestArchitectFaultNewFileExists(FixtureTestCase):
    """Guard 7b — NEW FILE: path already exists on disk. Architect-fault."""
    fixture_name = "test_architect_fault_new_file_exists"

    def test_fixture(self):
        output = self.run_fixture()
        self.assert_status_and_agent(output)
        self.assert_code_diff(output)
        self.assert_history_count_delta(output)
        self.assert_history_new_entries(output)
        self.assert_immutable_fields(output)


# ---------------------------------------------------------------------------
# Additional unit tests for path and diff utilities
# ---------------------------------------------------------------------------


class TestNormalisePath(unittest.TestCase):
    """Unit tests for _normalise_path used inside the runner."""

    def _norm(self, path):
        return _normalise_path(path)

    def test_strips_leading_dot_slash(self):
        self.assertEqual(self._norm("./src/foo.py"), "src/foo.py")

    def test_collapses_double_slash(self):
        self.assertEqual(self._norm("src//foo.py"), "src/foo.py")

    def test_plain_path_unchanged(self):
        self.assertEqual(self._norm("src/auth/login.py"), "src/auth/login.py")

    def test_double_dot_slash_unchanged(self):
        # Normalisation does not remove .. — _is_unsafe_path catches it instead
        self.assertEqual(self._norm("src/../secret.py"), "src/../secret.py")


class TestGuard1And2Raise(unittest.TestCase):
    """Guards 1 and 2 must raise CodingAgentRoutingError (no output object)."""

    def _minimal_task(self, **overrides):
        base = {
            "task_id": "t", "feature_request": "x",
            "acceptance_criteria": [], "scoped_files": [],
            "status": "in_progress", "current_agent": "coding_agent",
            "plan": "do something", "history": [], "code_diff": None,
            "test_results": None, "review_result": None, "retry_count": 0,
        }
        base.update(overrides)
        return base

    def test_guard1_wrong_agent(self):
        task = self._minimal_task(current_agent="testing_agent")
        with self.assertRaises(CodingAgentRoutingError) as ctx:
            run(task, repo_root=tempfile.mkdtemp(), llm_fn=_sentinel_llm)
        self.assertIn("Guard 1", str(ctx.exception))

    def test_guard2_wrong_status(self):
        task = self._minimal_task(status="approved")
        with self.assertRaises(CodingAgentRoutingError) as ctx:
            run(task, repo_root=tempfile.mkdtemp(), llm_fn=_sentinel_llm)
        self.assertIn("Guard 2", str(ctx.exception))

    def test_guard2_pending_status(self):
        task = self._minimal_task(status="pending")
        with self.assertRaises(CodingAgentRoutingError):
            run(task, repo_root=tempfile.mkdtemp(), llm_fn=_sentinel_llm)


class TestDiffValidationChecks(unittest.TestCase):
    """Unit-level tests for the four post-processing diff checks."""

    def _run_with_diff(self, diff_str, scoped_files=None):
        """Run the runner with an injected diff string."""
        scoped_files = scoped_files or ["src/foo.py"]
        with tempfile.TemporaryDirectory() as tmp:
            for p in scoped_files:
                _create_placeholder(tmp, _normalise_path(p))
            task = {
                "task_id": "t", "feature_request": "x",
                "acceptance_criteria": ["pass"],
                "scoped_files": scoped_files,
                "status": "in_progress", "current_agent": "coding_agent",
                "plan": "do something",
                "history": [], "code_diff": None,
                "test_results": None, "review_result": None, "retry_count": 0,
            }
            return run(task, repo_root=tmp, llm_fn=lambda _: diff_str)

    def test_check1_empty_diff(self):
        out = self._run_with_diff("")
        self.assertEqual(out["status"], "blocked")
        self.assertIn("LLM returned empty output", out["history"][-1]["output_summary"])

    def test_check1_whitespace_only_diff(self):
        out = self._run_with_diff("   \n\n  ")
        self.assertIn("LLM returned empty output", out["history"][-1]["output_summary"])

    def test_check2_no_hunk_markers(self):
        out = self._run_with_diff("--- a/src/foo.py\n+++ b/src/foo.py\nsome text")
        self.assertIn("no @@ hunk markers", out["history"][-1]["output_summary"])

    def test_check3_out_of_scope_path(self):
        diff = (
            "--- a/src/evil.py\n+++ b/src/evil.py\n"
            "@@ -1,3 +1,4 @@\n line\n line\n line\n+added\n"
        )
        out = self._run_with_diff(diff, scoped_files=["src/foo.py"])
        self.assertIn("out-of-scope file", out["history"][-1]["output_summary"])

    def test_check4_path_traversal_in_diff(self):
        diff = (
            "--- a/../secret.py\n+++ b/../secret.py\n"
            "@@ -1,3 +1,4 @@\n line\n line\n line\n+added\n"
        )
        out = self._run_with_diff(diff, scoped_files=["src/foo.py"])
        self.assertIn("unsafe path", out["history"][-1]["output_summary"])

    def test_valid_diff_passes(self):
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1,3 +1,4 @@\n line\n line\n line\n+added\n"
        )
        out = self._run_with_diff(diff, scoped_files=["src/foo.py"])
        self.assertEqual(out["status"], "in_progress")
        self.assertEqual(out["current_agent"], "testing_agent")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
