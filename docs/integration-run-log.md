# Integration Run Log — End-to-End Tests

Track every full-pipeline test run here. One row per run. Log what broke and what was fixed.
This is the Week 2–3 debugging record and also evidence for the hackathon submission.

---

## How to Run

```bash
# Single run (stubs active — all agents are stubs until teammates wire in real calls)
python orchestration/pipeline.py --request "Add rate limiting to the login endpoint"

# Run with a custom task ID (useful for tracking in this log)
python orchestration/pipeline.py --request "Add rate limiting to the login endpoint" --task-id task_run_001

# Deliberately trigger retry loop (test Manager + Reflection) — set review_passed = False in pipeline.py first
python orchestration/pipeline.py --request "Add input validation to registration form"

# View the dashboard while running
streamlit run dashboard/app.py
```

---

## Run Records

| # | Date | Task ID | Feature Request | Status | Retries | Reflection Triggered? | Notes |
|---|------|---------|----------------|--------|---------|----------------------|-------|
| — | — | — | — | — | — | — | No runs yet — start here in Week 2 |

---

## Run Template (copy this row for each run)

```
| <run_id> | <YYYY-MM-DD> | <task_id> | <feature request (short)> | awaiting_human_approval / blocked | <0/1/2> | yes/no (<agent_name>) | <notes> |
```

---

## Issues Log

Track problems found during integration runs and how they were resolved.

### Issue Template

**Run #:** _  
**Found:** _describe what broke_  
**Root cause:** _why it happened_  
**Fixed by:** _what change fixed it_  
**File changed:** _which file_  
**Status:** open / resolved  

---

## Reflection Agent Trigger Log

Record every time the Reflection Agent was triggered during integration testing.

| Run # | Agent Rewritten | Old Version | New Version | Failure Pattern | Rate Before | Rate After (est.) |
|-------|----------------|-------------|-------------|-----------------|-------------|-------------------|
| — | — | — | — | — | — | — |

---

## Notes: How to Force a Reflection Agent Trigger for Testing

The Reflection Agent triggers automatically when an agent's rolling-window success rate drops
below 60% with at least 3 runs. To test this deliberately without waiting for real failures:

1. Open `orchestration/pipeline.py`
2. Find `call_review_agent` and set `review_passed = False`
3. Run the pipeline 4 times in a row:
   ```
   python orchestration/pipeline.py --request "Test trigger" --task-id trigger_001
   python orchestration/pipeline.py --request "Test trigger" --task-id trigger_002
   python orchestration/pipeline.py --request "Test trigger" --task-id trigger_003
   python orchestration/pipeline.py --request "Test trigger" --task-id trigger_004
   ```
4. On run 4, coding_agent's rolling window will be [F, F, F, F] → rate = 0.0 → Reflection triggers
5. Check `agents/reflection_agent/prompt_history/` for new `coding_agent_v1.md`
6. Open the dashboard (`streamlit run dashboard/app.py`) — Tab 2 "Prompt Diffs" will show the before/after

**Important:** Set `review_passed = True` again before any real demo runs.

---

## Stub → Real Agent Replacement Tracker

Track which pipeline stubs have been replaced with real Bob calls.

| Agent | Owner | Stub in pipeline.py | Real call ready? | Date replaced | Notes |
|-------|-------|--------------------|-----------------:|---------------|-------|
| PM Agent | Person A | `call_pm_agent()` | [ ] | — | — |
| Architect Agent | Person A | `call_architect_agent()` | [ ] | — | — |
| Coding Agent | Person B | `call_coding_agent()` | [ ] | — | — |
| Testing Agent | Person C | `call_testing_agent()` | [ ] | — | — |
| Review Agent | Person D | `call_review_agent()` | [ ] | — | — |
| Manager Agent | Person E | already real | [x] | Day 0 | Pure Python, no Bob call |
| Reflection Agent | Person E | stub fallback active | [x] | Day 0 | Real watsonx call when .env is set |
