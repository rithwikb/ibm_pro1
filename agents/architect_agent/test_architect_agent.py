"""
test_architect_agent.py — Unit tests for Architect Agent (architect_agent.py)

All tests mock the Bob API call — no real credentials needed.
Disk existence tests use a temporary directory.
Run with:
    pytest agents/architect_agent/test_architect_agent.py -v
"""
import json
import os
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agents.architect_agent.architect_agent import (
    parse_new_file_lines,
    validate_architect_output,
    run_architect_agent,
    _filter_listing_for_llm,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def test_filter_listing_keeps_arbitrary_text_and_excludes_secrets_and_binaries():
    listing = ["train.py", "analysis.ipynb", "config.yaml", "notes.xyz", ".env", "image.png"]

    filtered = _filter_listing_for_llm(listing)

    assert "train.py" in filtered
    assert "analysis.ipynb" in filtered
    assert "config.yaml" in filtered
    assert "notes.xyz" in filtered
    assert ".env" not in filtered
    assert "image.png" not in filtered

def _make_task(feature_request: str = "Add rate limiting to the login endpoint") -> dict:
    return {
        "task_id": "task_test_002",
        "feature_request": feature_request,
        "acceptance_criteria": [
            "The login endpoint rejects requests exceeding 5 attempts per IP with HTTP 429",
            "All existing tests still pass",
        ],
        "scoped_files": [],
        "status": "in_progress",
        "current_agent": "architect_agent",
        "plan": None,
        "history": [
            {
                "agent": "pm_agent",
                "output_summary": "acceptance criteria defined",
                "timestamp": "2026-08-10T09:00:00+00:00",
                "success": True,
            }
        ],
        "code_diff": None,
        "test_results": None,
        "review_result": None,
        "retry_count": 0,
    }


class TempRepo:
    """
    Fix 10: context manager that creates a temp directory with the given
    relative file paths as empty files, and cleans it up on exit.

    Usage:
        with TempRepo(["flaskbb/auth/views.py"]) as tmpdir:
            ...  # tmpdir is the path string
    """
    def __init__(self, files: list):
        self.files = files
        self.path = None

    def __enter__(self) -> str:
        self.path = tempfile.mkdtemp()
        for rel_path in self.files:
            full = os.path.join(self.path, rel_path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
        return self.path

    def __exit__(self, *_):
        if self.path:
            shutil.rmtree(self.path, ignore_errors=True)


def _good_bob_response(files: list = None, plan: str = None) -> str:
    if files is None:
        files = [
            "flaskbb/extensions.py",
            "flaskbb/auth/views.py",
            "tests/unit/test_auth.py",
        ]
    if plan is None:
        plan = (
            "Register Flask-Limiter in flaskbb/extensions.py and configure a limit of "
            "5 requests per 10 minutes keyed by IP. Apply the limiter decorator to the "
            "login view in flaskbb/auth/views.py. Update tests/unit/test_auth.py with "
            "a test verifying HTTP 429 on the 6th attempt."
        )
    return json.dumps({"plan": plan, "scoped_files": files})


def _fenced_response() -> str:
    return "```json\n" + _good_bob_response() + "\n```"


# ──────────────────────────────────────────────────────────────────────────────
# parse_new_file_lines tests
# ──────────────────────────────────────────────────────────────────────────────

def test_parse_new_file_single():
    plan = "Some plan text.\nNEW FILE: flaskbb/utils/tokens.py\nMore text."
    result = parse_new_file_lines(plan)
    assert result == {"flaskbb/utils/tokens.py"}


def test_parse_new_file_multiple():
    plan = (
        "Create a utility. NEW FILE: flaskbb/utils/tokens.py\n"
        "Add tests. NEW FILE: tests/unit/test_tokens.py\n"
        "Done."
    )
    result = parse_new_file_lines(plan)
    assert result == {"flaskbb/utils/tokens.py", "tests/unit/test_tokens.py"}


def test_parse_new_file_empty_when_none():
    plan = "Modify the existing auth views to add rate limiting. Update tests."
    assert parse_new_file_lines(plan) == set()


def test_parse_new_file_strict_prefix_no_fuzzy_match():
    """Lowercase 'new file:' or prose 'create a new file' must NOT match."""
    plan = (
        "We will create a new file for tokens.\n"
        "new file: flaskbb/utils/lowercase.py\n"  # lowercase — should NOT match
        "A NEW FILE has been planned.\n"            # no colon — should NOT match
    )
    result = parse_new_file_lines(plan)
    assert result == set(), f"Expected empty set, got {result}"


def test_parse_new_file_strips_whitespace():
    plan = "  NEW FILE:   flaskbb/utils/tokens.py   \n"
    result = parse_new_file_lines(plan)
    assert result == {"flaskbb/utils/tokens.py"}


# ──────────────────────────────────────────────────────────────────────────────
# validate_architect_output tests
# ──────────────────────────────────────────────────────────────────────────────

def test_validate_passes_all_existing_files():
    """All scoped_files exist on disk, no NEW FILE lines."""
    with TempRepo(["flaskbb/extensions.py", "flaskbb/auth/views.py", "tests/unit/test_auth.py"]) as tmpdir:
        output = _good_bob_response()
        is_valid, err = validate_architect_output(json.loads(output), tmpdir)
    assert is_valid, f"Expected valid, got error: {err}"


def test_validate_fails_new_file_not_in_scoped_files():
    """NEW FILE path in plan but missing from scoped_files → invalid."""
    plan = "Do something. NEW FILE: flaskbb/utils/missing.py"
    output = {
        "plan": plan,
        "scoped_files": ["flaskbb/auth/views.py"],  # missing.py not here
    }
    with TempRepo(["flaskbb/auth/views.py"]) as tmpdir:
        is_valid, err = validate_architect_output(output, tmpdir)
    assert not is_valid
    assert "missing from scoped_files" in err


def test_validate_fails_new_file_already_exists_on_disk():
    """NEW FILE path that already exists on disk → invalid."""
    plan = "Do something. NEW FILE: flaskbb/auth/views.py"
    output = {
        "plan": plan,
        "scoped_files": ["flaskbb/auth/views.py"],
    }
    with TempRepo(["flaskbb/auth/views.py"]) as tmpdir:
        is_valid, err = validate_architect_output(output, tmpdir)
    assert not is_valid
    assert "already exists on disk" in err


def test_validate_fails_existing_file_missing_from_disk():
    """scoped_files entry (not NEW FILE) doesn't exist on disk → invalid."""
    output = {
        "plan": "Modify auth views.",
        "scoped_files": ["flaskbb/auth/views.py", "flaskbb/auth/DOES_NOT_EXIST.py"],
    }
    with TempRepo(["flaskbb/auth/views.py"]) as tmpdir:
        is_valid, err = validate_architect_output(output, tmpdir)
    assert not is_valid
    assert "does not exist" in err


def test_validate_fails_missing_plan():
    output = {"plan": "", "scoped_files": ["flaskbb/auth/views.py"]}
    is_valid, err = validate_architect_output(output, "")
    assert not is_valid
    assert "plan" in err


def test_validate_fails_empty_scoped_files():
    output = {"plan": "A solid plan.", "scoped_files": []}
    is_valid, err = validate_architect_output(output, "")
    assert not is_valid
    assert "scoped_files" in err


def test_validate_fails_over_5_files():
    output = {
        "plan": "Plan.",
        "scoped_files": [f"file{i}.py" for i in range(6)],
    }
    is_valid, err = validate_architect_output(output, "")
    assert not is_valid
    assert "maximum is 5" in err


# ──────────────────────────────────────────────────────────────────────────────
# run_architect_agent tests
# ──────────────────────────────────────────────────────────────────────────────

def test_run_architect_success_sets_plan_and_scoped_files():
    """On valid mock response (with real files), plan and scoped_files are set."""
    files = ["flaskbb/extensions.py", "flaskbb/auth/views.py", "tests/unit/test_auth.py"]
    with TempRepo(files) as tmpdir:
        task = _make_task()
        mock_raw = _good_bob_response()
        with patch("agents.architect_agent.architect_agent._call_watsonx", return_value=mock_raw):
            result = run_architect_agent(task, repo_path=tmpdir)
    assert isinstance(result["scoped_files"], list)
    assert 1 <= len(result["scoped_files"]) <= 5
    assert isinstance(result["plan"], str) and result["plan"].strip()
    assert result["current_agent"] == "coding_agent"
    assert result["status"] == "in_progress"


def test_run_architect_appends_history_success_true():
    """Successful run appends architect_agent history entry with success=True."""
    files = ["flaskbb/extensions.py", "flaskbb/auth/views.py", "tests/unit/test_auth.py"]
    with TempRepo(files) as tmpdir:
        task = _make_task()
        history_before = len(task["history"])
        with patch("agents.architect_agent.architect_agent._call_watsonx",
                   return_value=_good_bob_response()):
            result = run_architect_agent(task, repo_path=tmpdir)
    arch_entries = [e for e in result["history"] if e["agent"] == "architect_agent"]
    assert len(arch_entries) == 1
    assert arch_entries[0]["success"] is True
    assert len(result["history"]) == history_before + 1


def test_run_architect_enforces_5_file_cap():
    """When LLM returns 6 files, run_architect_agent truncates to 5."""
    six_files = [
        "flaskbb/extensions.py", "flaskbb/auth/views.py", "flaskbb/auth/forms.py",
        "tests/unit/test_auth.py", "flaskbb/utils/helpers.py", "flaskbb/models.py",
    ]
    with TempRepo(six_files) as tmpdir:
        mock_raw = _good_bob_response(files=six_files)
        task = _make_task()
        with patch("agents.architect_agent.architect_agent._call_watsonx", return_value=mock_raw):
            result = run_architect_agent(task, repo_path=tmpdir)
    assert len(result["scoped_files"]) <= 5


def test_run_architect_handles_markdown_fences():
    """Fenced LLM response is parsed correctly."""
    files = ["flaskbb/extensions.py", "flaskbb/auth/views.py", "tests/unit/test_auth.py"]
    with TempRepo(files) as tmpdir:
        task = _make_task()
        with patch("agents.architect_agent.architect_agent._call_watsonx",
                   return_value=_fenced_response()):
            result = run_architect_agent(task, repo_path=tmpdir)
    assert isinstance(result["scoped_files"], list) and len(result["scoped_files"]) >= 1
    assert result["plan"] and result["plan"].strip()


def test_run_architect_api_failure_sets_blocked():
    """RuntimeError from _call_watsonx → task blocked, success=False."""
    task = _make_task()
    with patch("agents.architect_agent.architect_agent._call_watsonx",
               side_effect=RuntimeError("no key")):
        result = run_architect_agent(task, repo_path="")
    assert result["status"] == "blocked"
    arch_entries = [e for e in result["history"] if e["agent"] == "architect_agent"]
    assert len(arch_entries) == 1
    assert arch_entries[0]["success"] is False


def test_run_architect_invalid_output_sets_blocked():
    """Invalid LLM output (validation failure) → task blocked, success=False."""
    with TempRepo([]) as tmpdir:  # empty repo — any file reference will fail validation
        mock_raw = _good_bob_response(files=["flaskbb/auth/views.py"])
        task = _make_task()
        with patch("agents.architect_agent.architect_agent._call_watsonx", return_value=mock_raw):
            result = run_architect_agent(task, repo_path=tmpdir)
    assert result["status"] == "blocked"
    arch_entries = [e for e in result["history"] if e["agent"] == "architect_agent"]
    assert arch_entries[0]["success"] is False
