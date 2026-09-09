"""
test_manager.py — Unit tests for Manager Agent logic

Run with: pytest agents/manager_agent/test_manager.py -v
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from manager import (  # noqa: E402
    initialize_empty_stats,
    update_stats,
    get_underperformers,
    generate_report,
    load_stats,
    save_stats,
    extract_failure_patterns,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def make_task(history):
    return {
        "task_id": "test_001",
        "current_agent": "review_agent",
        "status": "approved",
        "feature_request": "test feature",
        "acceptance_criteria": [],
        "scoped_files": [],
        "plan": "",
        "code_diff": "",
        "test_results": {"passed": True},
        "review_result": {"passed": True, "findings": []},
        "history": history,
    }


def make_history_entry(agent, success, output_summary=""):
    return {
        "agent": agent,
        "success": success,
        "output_summary": output_summary,
        "timestamp": "2026-08-10T09:00:00Z",
    }


# -----------------------------------------------------------------------
# initialize_empty_stats
# -----------------------------------------------------------------------

def test_initialize_empty_stats_has_all_five_agents():
    stats = initialize_empty_stats()
    expected_agents = ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]
    for agent in expected_agents:
        assert agent in stats
        assert stats[agent]["recent_outcomes"] == []
        assert stats[agent]["runs"] == 0
        assert stats[agent]["successes"] == 0
        assert stats[agent]["rate"] is None


# -----------------------------------------------------------------------
# update_stats
# -----------------------------------------------------------------------

def test_update_stats_single_successful_run():
    stats = initialize_empty_stats()
    task = make_task([
        make_history_entry("pm_agent", True),
        make_history_entry("architect_agent", True),
        make_history_entry("coding_agent", True),
        make_history_entry("testing_agent", True),
        make_history_entry("review_agent", True),
    ])
    updated = update_stats(task, stats)

    for agent in ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]:
        assert updated[agent]["runs"] == 1
        assert updated[agent]["successes"] == 1
        assert updated[agent]["rate"] == 1.0
        assert updated[agent]["recent_outcomes"] == [True]


def test_update_stats_single_failed_run():
    stats = initialize_empty_stats()
    task = make_task([
        make_history_entry("pm_agent", True),
        make_history_entry("architect_agent", True),
        make_history_entry("coding_agent", False, "retry limit reached"),
        make_history_entry("testing_agent", True),
        make_history_entry("review_agent", True),
    ])
    updated = update_stats(task, stats)

    assert updated["coding_agent"]["runs"] == 1
    assert updated["coding_agent"]["successes"] == 0
    assert updated["coding_agent"]["rate"] == 0.0
    assert updated["coding_agent"]["recent_outcomes"] == [False]
    assert updated["pm_agent"]["rate"] == 1.0


def test_update_stats_rolling_window_caps_at_five():
    stats = initialize_empty_stats()
    # Feed 7 runs for coding_agent
    outcomes = [True, True, False, False, True, True, True]
    for outcome in outcomes:
        task = make_task([make_history_entry("coding_agent", outcome)])
        stats = update_stats(task, stats)

    # Window should be last 5: [False, False, True, True, True]
    assert stats["coding_agent"]["recent_outcomes"] == [False, False, True, True, True]
    assert stats["coding_agent"]["runs"] == 5
    assert stats["coding_agent"]["successes"] == 3
    assert stats["coding_agent"]["rate"] == 0.6


def test_update_stats_calculates_rate_correctly():
    stats = initialize_empty_stats()
    outcomes = [True, False, True, False]
    for outcome in outcomes:
        task = make_task([make_history_entry("coding_agent", outcome)])
        stats = update_stats(task, stats)

    assert stats["coding_agent"]["runs"] == 4
    assert stats["coding_agent"]["successes"] == 2
    assert stats["coding_agent"]["rate"] == 0.5


def test_update_stats_agent_not_in_task_history_is_unaffected():
    stats = initialize_empty_stats()
    # Task only has pm_agent in history
    task = make_task([make_history_entry("pm_agent", True)])
    updated = update_stats(task, stats)

    assert updated["pm_agent"]["runs"] == 1
    assert updated["coding_agent"]["runs"] == 0
    assert updated["coding_agent"]["rate"] is None
    assert updated["coding_agent"]["recent_outcomes"] == []


def test_update_stats_empty_history_changes_nothing():
    stats = initialize_empty_stats()
    task = make_task([])
    updated = update_stats(task, stats)

    for agent in stats:
        assert updated[agent]["runs"] == 0
        assert updated[agent]["rate"] is None


# -----------------------------------------------------------------------
# get_underperformers
# -----------------------------------------------------------------------

def test_get_underperformers_empty_stats_returns_empty():
    stats = initialize_empty_stats()
    assert get_underperformers(stats) == []


def test_get_underperformers_below_min_runs_not_flagged():
    stats = initialize_empty_stats()
    # 0% success rate, but only 2 runs (min_runs = 3)
    stats["coding_agent"] = {"recent_outcomes": [False, False], "runs": 2, "successes": 0, "rate": 0.0}
    result = get_underperformers(stats, min_runs=3, threshold=0.6)
    assert result == []


def test_get_underperformers_at_min_runs_below_threshold_flagged():
    stats = initialize_empty_stats()
    # 1/3 = 0.333 < 0.6, 3 runs >= min_runs (3)
    stats["coding_agent"] = {"recent_outcomes": [False, False, True], "runs": 3, "successes": 1, "rate": 0.333}
    result = get_underperformers(stats, min_runs=3, threshold=0.6)
    assert result == ["coding_agent"]


def test_get_underperformers_above_threshold_not_flagged():
    stats = initialize_empty_stats()
    stats["pm_agent"] = {"recent_outcomes": [True, True, True], "runs": 3, "successes": 3, "rate": 1.0}
    stats["coding_agent"] = {"recent_outcomes": [True, True, False], "runs": 3, "successes": 2, "rate": 0.667}
    result = get_underperformers(stats, min_runs=3, threshold=0.6)
    assert result == []


def test_get_underperformers_multiple_agents_flagged():
    stats = initialize_empty_stats()
    stats["coding_agent"] = {"recent_outcomes": [False, False, False], "runs": 3, "successes": 0, "rate": 0.0}
    stats["review_agent"] = {"recent_outcomes": [False, False, True], "runs": 3, "successes": 1, "rate": 0.333}
    stats["pm_agent"] = {"recent_outcomes": [True, True, True], "runs": 3, "successes": 3, "rate": 1.0}
    result = get_underperformers(stats, min_runs=3, threshold=0.6)
    assert "coding_agent" in result
    assert "review_agent" in result
    assert "pm_agent" not in result


def test_get_underperformers_does_not_flag_at_threshold():
    """An agent exactly at 0.6 must NOT be flagged (strictly less than)."""
    stats = initialize_empty_stats()
    stats["testing_agent"] = {
        "recent_outcomes": [True, True, True, False, False],
        "runs": 5,
        "successes": 3,
        "rate": 0.6,
    }
    result = get_underperformers(stats)
    assert "testing_agent" not in result


def test_get_underperformers_flags_only_failing_agents():
    stats = initialize_empty_stats()
    stats["pm_agent"] = {"recent_outcomes": [True, True, True, True, True], "runs": 5, "successes": 5, "rate": 1.0}
    stats["coding_agent"] = {"recent_outcomes": [True, False, False, False], "runs": 4, "successes": 1, "rate": 0.25}
    result = get_underperformers(stats)
    assert result == ["coding_agent"]


# -----------------------------------------------------------------------
# FIX 3 — Canonical threshold rule tests
# -----------------------------------------------------------------------

def test_get_underperformers_default_threshold_is_point_6():
    """Calling get_underperformers with no explicit threshold arg must use 0.6."""
    stats = initialize_empty_stats()
    stats["coding_agent"] = {"recent_outcomes": [True, False, True, False], "runs": 4, "successes": 2, "rate": 0.5}
    result = get_underperformers(stats)
    assert "coding_agent" in result


def test_get_underperformers_default_min_runs_is_3():
    """Calling get_underperformers with no explicit min_runs arg must use 3."""
    stats = initialize_empty_stats()
    stats["coding_agent"] = {"recent_outcomes": [False, False], "runs": 2, "successes": 0, "rate": 0.0}
    result = get_underperformers(stats)
    assert "coding_agent" not in result


# -----------------------------------------------------------------------
# generate_report
# -----------------------------------------------------------------------

def test_generate_report_no_underperformers():
    stats = initialize_empty_stats()
    stats["pm_agent"] = {"recent_outcomes": [True, True, True], "runs": 3, "successes": 3, "rate": 1.0}
    task = make_task([])
    task["task_id"] = "task_001"
    report = generate_report(task, stats)

    assert report["task_id"] == "task_001"
    assert report["underperformers"] == []
    assert report["recommended_action"] == "none"
    assert "stats_snapshot" in report
    assert "agent_stats" in report
    assert isinstance(report["agent_stats"], list)


def test_generate_report_with_underperformer():
    stats = initialize_empty_stats()
    stats["coding_agent"] = {
        "recent_outcomes": [True, False, False, False, False],
        "runs": 5,
        "successes": 1,
        "rate": 0.2,
    }
    task = make_task([make_history_entry("coding_agent", False, "review rejected: missing input validation")])
    report = generate_report(task, stats)

    assert "coding_agent" in report["underperformers"]
    assert report["recommended_action"] == "rewrite_prompt"
    assert "coding_agent" in report["reasoning"]


def test_generate_report_agent_stats_array_has_all_agents():
    """agent_stats array must contain an entry for every tracked agent."""
    stats = initialize_empty_stats()
    task = make_task([])
    report = generate_report(task, stats)

    agents_in_report = [e["agent"] for e in report["agent_stats"]]
    for agent in ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]:
        assert agent in agents_in_report


def test_generate_report_trigger_reflection_set_correctly():
    """trigger_reflection must be True only for underperforming agents."""
    stats = initialize_empty_stats()
    stats["coding_agent"] = {"recent_outcomes": [False, False, False], "runs": 3, "successes": 0, "rate": 0.0}
    stats["pm_agent"] = {"recent_outcomes": [True, True, True], "runs": 3, "successes": 3, "rate": 1.0}
    task = make_task([])
    report = generate_report(task, stats)

    coding_entry = next(e for e in report["agent_stats"] if e["agent"] == "coding_agent")
    pm_entry = next(e for e in report["agent_stats"] if e["agent"] == "pm_agent")
    assert coding_entry["trigger_reflection"] is True
    assert pm_entry["trigger_reflection"] is False


def test_generate_report_recent_outcomes_in_agent_stats():
    """Each agent_stats entry must include recent_outcomes list."""
    stats = initialize_empty_stats()
    stats["testing_agent"] = {"recent_outcomes": [True, False, True], "runs": 3, "successes": 2, "rate": 2 / 3}
    task = make_task([])
    report = generate_report(task, stats)

    testing_entry = next(e for e in report["agent_stats"] if e["agent"] == "testing_agent")
    assert testing_entry["recent_outcomes"] == [True, False, True]
    assert testing_entry["failures"] == 1


# -----------------------------------------------------------------------
# load_stats / save_stats persistence
# -----------------------------------------------------------------------

def test_save_and_load_stats_roundtrip():
    stats = initialize_empty_stats()
    stats["coding_agent"] = {"recent_outcomes": [True, False, True], "runs": 3, "successes": 2, "rate": 0.667}

    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        tmp_path = f.name

    try:
        save_stats(stats, tmp_path)
        loaded = load_stats(tmp_path)
        assert loaded["coding_agent"]["recent_outcomes"] == [True, False, True]
        assert loaded["coding_agent"]["runs"] == 3
        assert loaded["coding_agent"]["successes"] == 2
        assert loaded["coding_agent"]["rate"] == 0.667
    finally:
        os.unlink(tmp_path)


def test_load_stats_nonexistent_file_returns_initialized_stats():
    loaded = load_stats("/tmp/does_not_exist_xyz123_stats.json")
    for agent in ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]:
        assert agent in loaded
        assert loaded[agent]["runs"] == 0


def test_load_stats_migrates_old_format_without_recent_outcomes():
    old_format = {
        "pm_agent": {"runs": 2, "successes": 2, "rate": 1.0},
        "architect_agent": {"runs": 2, "successes": 2, "rate": 1.0},
        "coding_agent": {"runs": 2, "successes": 1, "rate": 0.5},
        "testing_agent": {"runs": 2, "successes": 2, "rate": 1.0},
        "review_agent": {"runs": 2, "successes": 2, "rate": 1.0},
    }
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(old_format, f)
        tmp_path = f.name

    try:
        loaded = load_stats(tmp_path)
        assert "recent_outcomes" in loaded["pm_agent"]
        assert loaded["pm_agent"]["recent_outcomes"] == []
    finally:
        os.unlink(tmp_path)


# -----------------------------------------------------------------------
# extract_failure_patterns
# -----------------------------------------------------------------------

def test_extract_failure_patterns_from_review_findings():
    task = make_task([make_history_entry("coding_agent", False)])
    task["review_result"] = {
        "passed": False,
        "findings": [
            {
                "checklist_item": "missing input validation",
                "file": "login.py",
                "line": 10,
                "severity": "high",
                "description": "...",
            },
            {
                "checklist_item": "hardcoded secrets",
                "file": "login.py",
                "line": 5,
                "severity": "critical",
                "description": "...",
            },
        ],
    }
    patterns = extract_failure_patterns(task, ["coding_agent"])
    assert "missing input validation" in patterns["coding_agent"]
    assert "hardcoded secrets" in patterns["coding_agent"]
