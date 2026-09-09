# Manager Agent — Design Document

## Purpose

The Manager Agent sits at the end of every pipeline run. It reads the completed
task's `history` array, updates a **rolling-window** per-agent success-rate store
(`agent_stats.json`), and produces a structured report identifying any underperforming
agents. It does **not** call an LLM.

**Manager Agent's reasoning is 100% deterministic Python — no LLM calls, no coins
spent, fully auditable. This is a deliberate design choice: the trigger decision
itself can never hallucinate or vary between runs.**

---

## Rolling Window

We track the **last `WINDOW_SIZE` (default: 5) runs** per agent, not an all-time
average. Rationale: we want the stats to reflect an agent's *current* behavior.
After a Reflection Agent rewrites a failing agent's prompt, we need improvements
to show up on the dashboard graph within a few runs — not be permanently diluted
by old failures. A window also makes the "success rate climbing after rewrite"
demo moment possible to see clearly.

---

## Data Model — `agent_stats.json`

```json
{
  "pm_agent":        { "recent_outcomes": [true, true, true], "runs": 3, "successes": 3, "rate": 1.0 },
  "architect_agent": { "recent_outcomes": [],                 "runs": 0, "successes": 0, "rate": null },
  "coding_agent":    { "recent_outcomes": [true, false, false], "runs": 3, "successes": 1, "rate": 0.33 },
  "testing_agent":   { "recent_outcomes": [],                 "runs": 0, "successes": 0, "rate": null },
  "review_agent":    { "recent_outcomes": [],                 "runs": 0, "successes": 0, "rate": null }
}
```

- `recent_outcomes`: list of True/False values, capped at `WINDOW_SIZE`. Oldest
  entries drop off when a new entry pushes the list past the cap.
- `runs`: `len(recent_outcomes)` — the number of outcomes in the current window
- `successes`: `sum(recent_outcomes)` — count of True values in the window
- `rate`: `successes / runs`, recomputed on every update. `null` if runs == 0

---

## Canonical Threshold Rule

**An agent is flagged as underperforming when BOTH conditions are true:**
1. `rate < 0.6` — failed more than 40% of the time in the current window
2. `runs >= 3` — minimum sample size in the window before any flag is raised

This rule is consistent across all files:
- `manager.py`: `get_underperformers(stats, threshold=0.6, min_runs=3)`
- `agents/manager_agent/system_prompt.md`: canonical rule stated verbatim
- `dashboard/app.py`: threshold line labeled "60% threshold, min. 3 runs"

---

## Manager Agent Output — JSON Shape

```json
{
  "run_id": "run_task_003",
  "task_id": "task_003",
  "timestamp": "2026-08-10T14:00:00Z",
  "agent_stats": [
    {
      "agent": "coding_agent",
      "runs_evaluated": 5,
      "successes": 2,
      "failures": 3,
      "success_rate": 0.40,
      "recent_outcomes": [true, false, false, true, false],
      "trigger_reflection": true,
      "reason": "coding_agent succeeded 2/5 runs in current window (rate=40%); failure patterns: 'missing input validation'"
    },
    {
      "agent": "pm_agent",
      "runs_evaluated": 5,
      "successes": 5,
      "failures": 0,
      "success_rate": 1.0,
      "recent_outcomes": [true, true, true, true, true],
      "trigger_reflection": false,
      "reason": "no failures in current window (rate=100%)"
    }
  ],
  "underperformers": ["coding_agent"],
  "recommended_action": "rewrite_prompt",
  "reasoning": "coding_agent succeeded 2/5 runs in current window (rate=40%); failure patterns: 'missing input validation'",
  "stats_snapshot": { "...": "..." }
}
```

The dashboard reads `agent_stats` directly — no re-derivation needed.

---

## Pseudocode — `manager.py`

```python
WINDOW_SIZE = 5
UNDERPERFORM_THRESHOLD = 0.6
MIN_RUNS = 3

def update_stats(task_obj, stats, window_size=WINDOW_SIZE):
    for entry in task_obj["history"]:
        if entry["agent"] not in TRACKED_AGENTS: continue
        if entry["success"] is None: continue
        stats[agent]["recent_outcomes"].append(bool(entry["success"]))
        stats[agent]["recent_outcomes"] = stats[agent]["recent_outcomes"][-window_size:]
        outcomes = stats[agent]["recent_outcomes"]
        stats[agent]["runs"] = len(outcomes)
        stats[agent]["successes"] = sum(outcomes)
        stats[agent]["rate"] = successes / runs

def get_underperformers(stats, threshold=0.6, min_runs=3):
    return [a for a, d in stats.items()
            if d["rate"] is not None
            and d["runs"] >= min_runs
            and d["rate"] < threshold]
```

---

## What the Manager Agent Does NOT Do

- Does not call an LLM (reasoning is deterministic string templating)
- Does not rewrite any prompts (that is the Reflection Agent's job)
- Does not modify the task object (it only reads the completed task)
- Does not decide *how* to fix the prompt — it only identifies *who* needs fixing
  and *why* (failure reasons from history/review_result)
