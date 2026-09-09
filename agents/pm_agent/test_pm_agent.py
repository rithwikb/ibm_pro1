"""
test_pm_agent.py — Unit tests for PM Agent (pm_agent.py)

All tests mock the Bob API call — no real credentials needed.
Run with:
    pytest agents/pm_agent/test_pm_agent.py -v
"""
import json
import sys
import os
from unittest.mock import patch

# Make pm_agent importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agents.pm_agent.pm_agent import (
    validate_pm_output,
    run_pm_agent,
    call_bob_pm,   # kept: tested directly below in test_call_bob_pm_*
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_task(feature_request: str = "Add rate limiting to the login endpoint") -> dict:
    return {
        "task_id": "task_test_001",
        "feature_request": feature_request,
        "acceptance_criteria": [],
        "scoped_files": [],
        "status": "pending",
        "current_agent": "pm_agent",
        "plan": None,
        "history": [],
        "code_diff": None,
        "test_results": None,
        "review_result": None,
        "retry_count": 0,
    }


def _good_bob_response(n: int = 4) -> str:
    """
    Return a realistic watsonx JSON string with n acceptance criteria.
    Fix 2: always includes "All existing tests still pass" as the last item
    without double-slicing it away. n must be >= 2 (1 real criterion + the
    mandatory tests-pass criterion).
    """
    base_criteria = [
        "The login endpoint rejects requests exceeding 5 attempts per IP within 10 minutes with HTTP 429",
        "A rejected response includes a Retry-After header",
        "Successful logins within the limit return HTTP 200 or a redirect",
        "The rate limit counter resets after the 10-minute window expires",
        "Admin users bypass the login rate limit",
    ]
    # Take (n-1) real criteria + the mandatory tests-pass criterion
    real = base_criteria[:max(n - 1, 1)]
    real.append("All existing tests still pass")
    return json.dumps({"acceptance_criteria": real})


def _fenced_bob_response() -> str:
    """Same response wrapped in markdown code fences."""
    return "```json\n" + _good_bob_response(3) + "\n```"


def _blocked_bob_response() -> str:
    return json.dumps({
        "acceptance_criteria": [],
        "blocked": True,
        "block_reason": "Stock price prediction is unrelated to FlaskBB's forum domain.",
    })


# ──────────────────────────────────────────────────────────────────────────────
# validate_pm_output tests
# ──────────────────────────────────────────────────────────────────────────────

def test_validate_accepts_3_good_criteria():
    output = {"acceptance_criteria": [
        "Login endpoint rejects excess attempts with HTTP 429",
        "Rate limit resets after the window expires",
        "All existing tests still pass",
    ]}
    assert validate_pm_output(output) is True


def test_validate_rejects_empty_list():
    assert validate_pm_output({"acceptance_criteria": []}) is False


def test_validate_rejects_single_item():
    assert validate_pm_output({"acceptance_criteria": ["Only one criterion"]}) is False


def test_validate_rejects_more_than_6_items():
    output = {"acceptance_criteria": [f"Criterion {i}" for i in range(7)]}
    assert validate_pm_output(output) is False


def test_validate_rejects_duplicate_criteria():
    output = {"acceptance_criteria": [
        "Login returns HTTP 429 on excess attempts",
        "Login returns HTTP 429 on excess attempts",  # duplicate
        "All existing tests still pass",
    ]}
    assert validate_pm_output(output) is False


def test_validate_rejects_empty_string_criterion():
    output = {"acceptance_criteria": [
        "Login returns HTTP 429",
        "",  # empty string
        "All existing tests still pass",
    ]}
    assert validate_pm_output(output) is False


def test_validate_rejects_missing_acceptance_criteria_key():
    assert validate_pm_output({}) is False


def test_validate_accepts_blocked_output():
    """A blocked output (nonsensical request) is valid — pipeline handles status."""
    output = {
        "acceptance_criteria": [],
        "blocked": True,
        "block_reason": "Feature is unrelated to forum domain.",
    }
    assert validate_pm_output(output) is True


# ──────────────────────────────────────────────────────────────────────────────
# run_pm_agent tests
# ──────────────────────────────────────────────────────────────────────────────

def test_run_pm_agent_success_sets_criteria_and_handoff():
    """On a valid mock response, criteria are set and current_agent handed off."""
    task = _make_task()
    mock_raw = _good_bob_response(4)

    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=mock_raw):
        result = run_pm_agent(task)

    assert isinstance(result["acceptance_criteria"], list)
    assert 2 <= len(result["acceptance_criteria"]) <= 6
    assert result["current_agent"] == "architect_agent"
    assert result["status"] == "in_progress"


def test_run_pm_agent_success_appends_history_entry():
    """A success run appends exactly one pm_agent history entry with success=True."""
    task = _make_task()
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=_good_bob_response(3)):
        result = run_pm_agent(task)

    pm_entries = [e for e in result["history"] if e["agent"] == "pm_agent"]
    assert len(pm_entries) == 1
    assert pm_entries[0]["success"] is True
    assert "output_summary" in pm_entries[0]
    assert "timestamp" in pm_entries[0]


def test_run_pm_agent_handles_markdown_fences():
    """Markdown-fenced LLM response is parsed and accepted."""
    task = _make_task()
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=_fenced_bob_response()):
        result = run_pm_agent(task)

    assert isinstance(result["acceptance_criteria"], list)
    assert len(result["acceptance_criteria"]) >= 2


def test_run_pm_agent_blocked_request_sets_status_blocked():
    """A blocked LLM response sets task status to blocked and success=False."""
    task = _make_task("Add machine learning stock price prediction")
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=_blocked_bob_response()):
        result = run_pm_agent(task)

    assert result["status"] == "blocked"
    assert result["acceptance_criteria"] == []
    pm_entries = [e for e in result["history"] if e["agent"] == "pm_agent"]
    assert len(pm_entries) == 1
    assert pm_entries[0]["success"] is False


def test_run_pm_agent_api_failure_sets_status_blocked():
    """When _call_watsonx raises RuntimeError, task is blocked and success=False."""
    task = _make_task()
    with patch("agents.pm_agent.pm_agent._call_watsonx", side_effect=RuntimeError("no key")):
        result = run_pm_agent(task)

    assert result["status"] == "blocked"
    pm_entries = [e for e in result["history"] if e["agent"] == "pm_agent"]
    assert len(pm_entries) == 1
    assert pm_entries[0]["success"] is False


def test_run_pm_agent_invalid_json_sets_status_blocked():
    """When watsonx returns non-JSON garbage, task is blocked and success=False."""
    task = _make_task()
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value="This is not JSON at all."):
        result = run_pm_agent(task)

    assert result["status"] == "blocked"
    pm_entries = [e for e in result["history"] if e["agent"] == "pm_agent"]
    assert pm_entries[0]["success"] is False


def test_run_pm_agent_too_many_criteria_blocked():
    """LLM returning 7 criteria (over max) triggers validation failure → blocked."""
    task = _make_task()
    over_limit = json.dumps({
        "acceptance_criteria": [f"Criterion {i}" for i in range(7)]
    })
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=over_limit):
        result = run_pm_agent(task)

    assert result["status"] == "blocked"
    pm_entries = [e for e in result["history"] if e["agent"] == "pm_agent"]
    assert pm_entries[0]["success"] is False


def test_run_pm_agent_blocked_sets_current_agent_human():
    """On any error/blocked path, current_agent must be 'human' not 'pm_agent'."""
    task = _make_task()
    with patch("agents.pm_agent.pm_agent._call_watsonx", side_effect=RuntimeError("no key")):
        result = run_pm_agent(task)

    assert result["current_agent"] == "human", (
        f"Expected current_agent='human' on blocked task, got '{result['current_agent']}'"
    )


# ──────────────────────────────────────────────────────────────────────────────
# call_bob_pm direct tests
# ──────────────────────────────────────────────────────────────────────────────

def test_call_bob_pm_returns_dict_with_criteria():
    """call_bob_pm parses the watsonx response and returns a dict with acceptance_criteria."""
    mock_raw = _good_bob_response(3)
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=mock_raw):
        result = call_bob_pm("Add rate limiting to the login endpoint")

    assert isinstance(result, dict)
    assert "acceptance_criteria" in result
    assert isinstance(result["acceptance_criteria"], list)


def test_call_bob_pm_raises_on_invalid_json():
    """call_bob_pm raises ValueError when watsonx returns non-JSON text."""
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value="not json at all"):
        try:
            call_bob_pm("Some feature")
            assert False, "Expected ValueError to be raised"
        except (ValueError, json.JSONDecodeError):
            pass  # expected


def test_call_bob_pm_handles_fenced_response():
    """call_bob_pm handles markdown-fenced JSON response from the LLM."""
    mock_raw = "```json\n" + _good_bob_response(3) + "\n```"
    with patch("agents.pm_agent.pm_agent._call_watsonx", return_value=mock_raw):
        result = call_bob_pm("Add rate limiting to the login endpoint")

    assert "acceptance_criteria" in result
