"""
test_reflection.py — Unit tests for Reflection Agent logic

Run with: pytest agents/reflection_agent/test_reflection.py -v
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from reflection import (
    save_prompt_version,
    get_current_version,
    build_reflection_input,
    extract_failure_patterns,
    validate_reflection_output,
    load_current_prompt,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

MOCK_REPORT_PATH = os.path.join(os.path.dirname(__file__), "test_data", "mock_manager_report.json")


@pytest.fixture()
def mock_manager_report():

    with open(MOCK_REPORT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def tmp_prompt_history(tmp_path, monkeypatch):
    """Redirect PROMPT_HISTORY_DIR to a temp directory for isolation."""
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def tmp_agents_dir(tmp_path, monkeypatch):
    """
    Create a fake agents directory with a coding_agent/system_prompt.md
    and redirect AGENTS_DIR.
    """
    import reflection as ref_module
    agents_dir = tmp_path / "agents"
    coding_dir = agents_dir / "coding_agent"
    coding_dir.mkdir(parents=True)
    prompt_file = coding_dir / "system_prompt.md"
    prompt_file.write_text("# Coding Agent\nYou write code.\n")
    monkeypatch.setattr(ref_module, "AGENTS_DIR", str(agents_dir))
    return agents_dir


# ──────────────────────────────────────────────────────────────────────────────
# save_prompt_version
# ──────────────────────────────────────────────────────────────────────────────

def test_save_prompt_version_creates_file(tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    path = save_prompt_version("coding_agent", 1, "# Rewritten prompt v1")
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read() == "# Rewritten prompt v1"


def test_save_prompt_version_correct_filename(tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    path = save_prompt_version("testing_agent", 3, "some content")
    assert os.path.basename(path) == "testing_agent_v3.md"


def test_save_prompt_version_creates_directory_if_missing(tmp_path, monkeypatch):
    import reflection as ref_module
    new_dir = str(tmp_path / "new_history_dir")
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", new_dir)

    path = save_prompt_version("pm_agent", 1, "content")
    assert os.path.exists(path)


# ──────────────────────────────────────────────────────────────────────────────
# get_current_version
# ──────────────────────────────────────────────────────────────────────────────

def test_get_current_version_no_files_returns_zero(tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))
    assert get_current_version("coding_agent") == 0


def test_get_current_version_finds_max(tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    (tmp_prompt_history / "coding_agent_v1.md").write_text("v1")
    (tmp_prompt_history / "coding_agent_v3.md").write_text("v3")
    (tmp_prompt_history / "coding_agent_v2.md").write_text("v2")

    assert get_current_version("coding_agent") == 3


def test_get_current_version_ignores_other_agents(tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    (tmp_prompt_history / "testing_agent_v5.md").write_text("v5")
    assert get_current_version("coding_agent") == 0


# ──────────────────────────────────────────────────────────────────────────────
# load_current_prompt
# ──────────────────────────────────────────────────────────────────────────────

def test_load_current_prompt_reads_file(tmp_agents_dir, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "AGENTS_DIR", str(tmp_agents_dir))

    content = load_current_prompt("coding_agent")
    assert "Coding Agent" in content
    assert len(content) > 0


def test_load_current_prompt_raises_for_missing_agent(tmp_agents_dir, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "AGENTS_DIR", str(tmp_agents_dir))

    with pytest.raises(FileNotFoundError):
        load_current_prompt("nonexistent_agent")


# ──────────────────────────────────────────────────────────────────────────────
# build_reflection_input
# ──────────────────────────────────────────────────────────────────────────────

def test_build_reflection_input_is_non_empty(mock_manager_report, tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    result = build_reflection_input(mock_manager_report, "# Coding Agent\nYou write code.")
    assert isinstance(result, str)
    assert len(result) > 50


def test_build_reflection_input_contains_agent_name(mock_manager_report, tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    result = build_reflection_input(mock_manager_report, "# Coding Agent")
    assert "coding_agent" in result


def test_build_reflection_input_contains_failure_patterns(mock_manager_report, tmp_prompt_history, monkeypatch):
    import reflection as ref_module
    monkeypatch.setattr(ref_module, "PROMPT_HISTORY_DIR", str(tmp_prompt_history))

    result = build_reflection_input(mock_manager_report, "# Coding Agent")
    assert "missing input validation" in result
    assert "hardcoded secrets" in result


# ──────────────────────────────────────────────────────────────────────────────
# extract_failure_patterns
# ──────────────────────────────────────────────────────────────────────────────

def test_extract_failure_patterns_quoted(mock_manager_report):
    patterns = extract_failure_patterns(mock_manager_report)
    assert "missing input validation" in patterns
    assert "hardcoded secrets" in patterns


def test_extract_failure_patterns_no_quotes():
    report = {
        "reasoning": "coding_agent failed; check for sql injection; check for missing auth",
        "underperformers": ["coding_agent"]
    }
    patterns = extract_failure_patterns(report)
    assert len(patterns) > 0
    # Should fall back to semicolon split
    assert any("sql injection" in p for p in patterns)


# ──────────────────────────────────────────────────────────────────────────────
# validate_reflection_output
# ──────────────────────────────────────────────────────────────────────────────

# -----------------------------------------------------------------------
# validate_reflection_output
# -----------------------------------------------------------------------

def test_validate_reflection_output_valid():
    """A specific, long-enough change_summary that references the failure pattern passes."""
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "# Rewritten prompt with proper validation rules",
        "change_summary": [
            "Added explicit pre-return check for missing input validation — "
            "addresses 3x rejection by Review Agent citing user input not sanitised"
        ],
    }
    validate_reflection_output(
        output,
        expected_failure_patterns=["missing input validation"],
    )  # should not raise


def test_validate_reflection_output_missing_field():
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "# Prompt",
        # missing change_summary
    }
    with pytest.raises(ValueError, match="missing required field"):
        validate_reflection_output(output)


def test_validate_reflection_output_empty_change_summary():
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "# Prompt",
        "change_summary": [],
    }
    with pytest.raises(ValueError, match="non-empty list"):
        validate_reflection_output(output)


def test_validate_reflection_output_empty_rewritten_prompt():
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "   ",
        "change_summary": ["some sufficiently long change summary that passes the length check yes"],
    }
    with pytest.raises(ValueError, match="must not be empty"):
        validate_reflection_output(output)


# -----------------------------------------------------------------------
# FIX 4 — Quality checks on change_summary
# -----------------------------------------------------------------------

def test_validate_rejects_vague_change_summary():
    """A short, generic change_summary like 'improved the prompt' must be rejected."""
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "# Rewritten prompt",
        "change_summary": ["improved the prompt"],   # ~19 chars, below MIN
    }
    with pytest.raises(ValueError, match="too vague"):
        validate_reflection_output(output)


def test_validate_rejects_change_summary_not_referencing_failure_patterns():
    """
    A change_summary that is long enough but doesn't mention any of the
    expected failure patterns must be rejected.
    """
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "# Rewritten prompt that is definitely not empty",
        "change_summary": [
            "Added a rule about always commenting code thoroughly for readability "
            "and added logging statements to every function for observability purposes"
        ],
    }
    with pytest.raises(ValueError, match="does not reference any of the expected failure patterns"):
        validate_reflection_output(
            output,
            expected_failure_patterns=["missing input validation", "hardcoded secrets"],
        )


def test_validate_accepts_specific_change_summary():
    """A realistic, specific change_summary that references a pattern and is long enough passes."""
    output = {
        "agent_name": "coding_agent",
        "version": 2,
        "rewritten_prompt": "# Full rewritten system prompt with new rules",
        "change_summary": [
            "Added pre-return checklist: before returning code, verify all user inputs are "
            "validated for type, length, and format before use — addresses: missing input validation (3x rejection)",
            "Added rule: never hardcode API keys, tokens, or passwords; always use env vars "
            "— addresses: hardcoded secrets (1x rejection)",
        ],
    }
    validate_reflection_output(
        output,
        expected_failure_patterns=["missing input validation", "hardcoded secrets"],
    )  # should not raise
