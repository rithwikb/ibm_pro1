"""
manager.py — Manager Agent logic
Pure Python, no LLM calls. Reads a completed task JSON + agent_stats.json,
updates the rolling-window stats store, and produces a structured report.

Usage:
    python agents/manager_agent/manager.py --task <path_to_completed_task.json>
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Agents tracked in stats (excludes "human" and "reflection_agent")
TRACKED_AGENTS = ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]

STATS_PATH = os.path.join(os.path.dirname(__file__), "agent_stats.json")

# Rolling window: only the last WINDOW_SIZE outcomes per agent are evaluated.
# This means an agent that failed early but was fixed by the Reflection Agent
# will recover quickly on the graph, rather than being permanently dragged down
# by historical failures. We track current behavior, not all-time history.
WINDOW_SIZE = 5

# Canonical threshold rule: flag an agent when its rolling-window success rate
# is below UNDERPERFORM_THRESHOLD and it has at least MIN_RUNS runs in the window.
UNDERPERFORM_THRESHOLD = 0.6
MIN_RUNS = 3


# -----------------------------------------------------------------------
# Stats store
# -----------------------------------------------------------------------

def initialize_empty_stats() -> dict:
    """Return a zeroed-out stats dict for all tracked agents."""
    return {
        agent: {"recent_outcomes": [], "runs": 0, "successes": 0, "rate": None}
        for agent in TRACKED_AGENTS
    }


def load_stats(path: str = STATS_PATH) -> dict:
    """
    Load agent_stats.json. If the file doesn't exist or is malformed,
    return an initialized empty stats dict.
    """
    if not os.path.exists(path):
        return initialize_empty_stats()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all tracked agents are present and have recent_outcomes
        for agent in TRACKED_AGENTS:
            if agent not in data:
                data[agent] = {"recent_outcomes": [], "runs": 0, "successes": 0, "rate": None}
            elif "recent_outcomes" not in data[agent]:
                # Migrate old format that didn't have recent_outcomes
                data[agent]["recent_outcomes"] = []
        return data
    except (json.JSONDecodeError, KeyError):
        return initialize_empty_stats()


def save_stats(stats: dict, path: str = STATS_PATH) -> None:
    """Write updated stats back to agent_stats.json (with profile fallback if locked)."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
    except OSError:
        try:
            home_dir = os.path.join(os.path.expanduser("~"), ".ai_dev_team")
            os.makedirs(home_dir, exist_ok=True)
            with open(os.path.join(home_dir, "agent_stats.json"), "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
        except Exception:
            pass


# -----------------------------------------------------------------------
# Core logic
# -----------------------------------------------------------------------

def update_stats(task_obj: dict, stats: dict, window_size: int = WINDOW_SIZE) -> dict:
    """
    Iterate task_obj["history"]. For each entry belonging to a tracked agent
    with a non-null success value:
      1. Append the True/False outcome to that agent's recent_outcomes list.
      2. Cap the list at window_size (drop oldest if over limit).
      3. Recompute runs = len(recent_outcomes), successes = sum(recent_outcomes),
         rate = successes / runs.

    Using a rolling window means the stats reflect an agent's *current* behavior.
    After a Reflection Agent rewrite, improvements show up within WINDOW_SIZE runs
    rather than being diluted by old failures forever.

    Returns the updated stats dict (mutates in place and returns).
    """
    for entry in task_obj.get("history", []):
        agent = entry.get("agent")
        if agent not in TRACKED_AGENTS:
            continue
        success = entry.get("success")
        if success is None:
            continue  # not yet evaluated — skip

        # Append new outcome and enforce window cap
        stats[agent]["recent_outcomes"].append(bool(success))
        if len(stats[agent]["recent_outcomes"]) > window_size:
            stats[agent]["recent_outcomes"] = stats[agent]["recent_outcomes"][-window_size:]

        # Recompute derived fields from the window
        outcomes = stats[agent]["recent_outcomes"]
        stats[agent]["runs"] = len(outcomes)
        stats[agent]["successes"] = sum(outcomes)
        stats[agent]["rate"] = stats[agent]["successes"] / stats[agent]["runs"]

    return stats


def get_underperformers(
    stats: dict,
    threshold: float = UNDERPERFORM_THRESHOLD,
    min_runs: int = MIN_RUNS,
) -> list:
    """
    Return list of agent names where the rolling-window success rate is below
    the threshold and there are at least min_runs runs in the current window.

    Canonical rule: rate < 0.6 AND runs >= 3.
    """
    result = []
    for agent, data in stats.items():
        if data["rate"] is None:
            continue
        if data["runs"] >= min_runs and data["rate"] < threshold:
            result.append(agent)
    return result


def extract_failure_patterns(task_obj: dict, underperformers: list) -> dict:
    """
    For each underperforming agent, scan:
    - task_obj["review_result"]["findings"] for checklist_item values
    - task_obj["history"] for failure entries (success == False) and their output_summary
    Returns a dict: {agent_name: [pattern_string, ...]}
    """
    patterns = {agent: [] for agent in underperformers}

    # Pull from review_result findings (most specific signal)
    review = task_obj.get("review_result")
    if review and isinstance(review.get("findings"), list):
        for finding in review["findings"]:
            item = finding.get("checklist_item", "")
            # Review findings don't have direct agent attribution, but coding_agent
            # produced the code — attribute review findings to it
            if "coding_agent" in patterns and item:
                if item not in patterns["coding_agent"]:
                    patterns["coding_agent"].append(item)

    # Pull from history summaries of failed runs
    for entry in task_obj.get("history", []):
        agent = entry.get("agent")
        if agent in patterns and entry.get("success") is False:
            summary = entry.get("output_summary", "")
            if summary and summary not in patterns[agent]:
                patterns[agent].append(f"failed run: {summary}")

    return patterns


def build_reasoning(
    task_obj: dict, underperformers: list, stats: dict, patterns: dict
) -> str:
    """Build a human-readable reasoning string from the underperformer data."""
    if not underperformers:
        return "All agents performing at or above threshold."
    parts = []
    for agent in underperformers:
        data = stats[agent]
        rate_pct = f"{data['rate']:.0%}" if data["rate"] is not None else "N/A"
        agent_patterns = patterns.get(agent, [])
        pattern_str = (
            "; ".join(f"'{p}'" for p in agent_patterns)
            if agent_patterns
            else "no specific pattern identified"
        )
        parts.append(
            f"{agent} succeeded {data['successes']}/{data['runs']} runs in current window "
            f"(rate={rate_pct}); failure patterns: {pattern_str}"
        )
    return " | ".join(parts)


def generate_report(task_obj: dict, stats: dict) -> dict:
    """
    Assemble and return the Manager Agent output JSON.
    Manager Agent reasoning is 100% deterministic Python -- no LLM calls,
    no coins spent, fully auditable. This is a deliberate design choice: the
    trigger decision itself can never hallucinate or vary between runs.
    """
    underperformers = get_underperformers(stats)
    patterns = extract_failure_patterns(task_obj, underperformers)
    reasoning = build_reasoning(task_obj, underperformers, stats, patterns)
    recommended_action = "rewrite_prompt" if underperformers else "none"

    # Build per-agent stats array matching the spec output shape
    agent_stats_array = []
    for agent in TRACKED_AGENTS:
        data = stats[agent]
        outcomes = data.get("recent_outcomes", [])
        failures = outcomes.count(False)
        agent_stats_array.append({
            "agent": agent,
            "runs_evaluated": data["runs"],
            "successes": data["successes"],
            "failures": failures,
            "success_rate": data["rate"],
            "recent_outcomes": outcomes,
            "trigger_reflection": agent in underperformers,
            "reason": (
                reasoning if agent in underperformers
                else (
                    f"no failures in current window (rate={data['rate']:.0%})"
                    if data["rate"] is not None and data["rate"] >= UNDERPERFORM_THRESHOLD
                    else "insufficient runs (fewer than 3 in window)"
                    if data["runs"] < MIN_RUNS
                    else "no data yet"
                )
            ),
        })

    return {
        "run_id": f"run_{task_obj.get('task_id', 'unknown')}",
        "task_id": task_obj.get("task_id", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_stats": agent_stats_array,
        # Convenience fields kept for backward compat with pipeline + reflection
        "underperformers": underperformers,
        "recommended_action": recommended_action,
        "reasoning": reasoning,
        # Full snapshot for dashboard
        "stats_snapshot": stats,
    }


# -----------------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------------

def run(task_path: str, stats_path: str = STATS_PATH) -> dict:
    """
    Full Manager Agent run:
    1. Load task JSON
    2. Load stats
    3. Update rolling-window stats from task history
    4. Save stats
    5. Generate and return report
    """
    with open(task_path, "r", encoding="utf-8") as f:
        task_obj = json.load(f)

    stats = load_stats(stats_path)
    stats = update_stats(task_obj, stats)
    save_stats(stats, stats_path)
    report = generate_report(task_obj, stats)
    return report


def main():
    parser = argparse.ArgumentParser(description="Manager Agent -- analyse pipeline run stats")
    parser.add_argument("--task", required=True, help="Path to completed task JSON file")
    parser.add_argument(
        "--stats",
        default=STATS_PATH,
        help=f"Path to agent_stats.json (default: {STATS_PATH})",
    )
    args = parser.parse_args()

    if not os.path.exists(args.task):
        print(f"ERROR: task file not found: {args.task}", file=sys.stderr)
        sys.exit(1)

    report = run(args.task, args.stats)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
