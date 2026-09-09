# AI Dev Team — Integration Plan

## Goal

Wire all 5 real LLM agents (PM, Architect, Coding, Testing, Review) into the pipeline,
replacing the current stubs, while making the system bulletproof against LLM failures,
bad output, and missing fields.

## Ownership

| Person | Owns |
|--------|------|
| Person A | PM Agent (`call_pm_agent`), Architect Agent (`call_architect_agent`) |
| Person B | Coding Agent (`call_coding_agent` real LLM path) |
| Person C | Testing Agent (`call_testing_agent`) |
| Person D | Review Agent (`call_review_agent`) |
| Person E | Manager Agent, Reflection Agent, Pipeline infrastructure — **YOU** |

## Approach

Every `call_*_agent()` function follows the same pattern:

```
1. Build a prompt string (system prompt + task JSON)
2. Call the LLM via watsonx API (same as reflection.py does)
3. Parse the JSON response — strip markdown fences, extract first {...} block
4. MERGE the parsed dict onto the existing task (never replace — merge)
5. Validate merged task against task_schema.json
6. On any error (parse fail, validation fail): status="blocked", success=null, route to Manager
```

The pipeline already has `_validate_architect_output()` and `append_history()` as models.
The merge-and-validate pattern will be extracted into a shared helper in `pipeline.py`.

---

## Sub-Task 1 — Pipeline Infrastructure: Shared LLM helpers

**Status:** `[ ] pending`

**Intent:**
Extract the LLM call + JSON parse + merge + validate logic into reusable helpers
in `orchestration/pipeline.py` so every teammate's agent uses identical error handling.
No teammate should need to write their own HTTP calls — they only write their prompt builder.

**Why this must be done first:**
Every other sub-task depends on these helpers. If each agent does its own parsing,
errors will be inconsistent and the "never crash" requirement cannot be enforced uniformly.

**Expected Outcomes:**
- `_call_llm(prompt: str) -> str` — makes watsonx HTTP call, returns raw text. Raises `RuntimeError` on HTTP error. Falls back to stub if no API key.
- `_parse_agent_json(raw: str) -> dict` — strips markdown fences, extracts first `{...}` block, calls `json.loads`. Raises `ValueError` with the raw text in the message if parsing fails.
- `_merge_task(existing: dict, llm_output: dict) -> dict` — merges LLM dict onto existing task. Fields present in llm_output overwrite existing. Fields absent in llm_output keep their existing value.
- `_safe_agent_call(task: dict, agent_name: str, prompt: str) -> dict` — calls `_call_llm`, then `_parse_agent_json`, then `_merge_task`. On any exception: sets `status="blocked"`, appends history entry with `success=null` and `output_summary="LLM parse error: <reason>"`, routes to `current_agent="manager_agent"`.
- `_build_prompt(agent_name: str, task: dict) -> str` — loads `agents/{agent_name}/system_prompt.md`, appends the task JSON as a fenced block.
- Tests added to `orchestration/test_pipeline.py` for: parse strips markdown fences, parse raises on prose-only output, merge preserves fields not in LLM output, safe_agent_call returns blocked task on parse error.

**Todo List:**
1. Add `BOB_API_KEY`, `WATSONX_URL`, `WATSONX_MODEL_ID`, `WATSONX_PROJECT_ID` loading from `.env` at top of `pipeline.py` (same pattern as `reflection.py` lines 162-170)
2. Implement `_call_llm(prompt)` in `pipeline.py` — copy the `_call_watsonx` pattern from `reflection.py:173-228`, adapted for pipeline use. Falls back to returning `"{}"` (empty object, triggers merge with no changes) if no API key, so stubs still work.
3. Implement `_parse_agent_json(raw)` — copy `_parse_llm_json_response` pattern from `reflection.py:231-256`. Must strip ` ```json ` fences and find first `{...}` block.
4. Implement `_merge_task(existing, llm_output)` — shallow merge: `{**existing, **llm_output}`. History is special: append, don't replace. If llm_output has `history` as a list longer than existing, use it; otherwise keep existing and append from llm_output.
5. Implement `_safe_agent_call(task, agent_name, prompt)` — wraps the above three. On `RuntimeError` or `ValueError`: call `append_history(task, agent_name, f"LLM parse error: {e}", None)`, set `task["status"]="blocked"`, `task["current_agent"]="manager_agent"`, return task.
6. Implement `_build_prompt(agent_name, task)` — reads `agents/{agent_name}/system_prompt.md`, appends `\n\n## Input Task\n```json\n{json.dumps(task, indent=2)}\n` `` ` `.
7. Add unit tests for all 4 helpers.

**Relevant Files:**
- `orchestration/pipeline.py` — where helpers go
- `agents/reflection_agent/reflection.py:162-256` — copy patterns from here
- `orchestration/test_pipeline.py` — add tests here

---

## Sub-Task 2 — Person A: PM Agent real LLM call

**Status:** `[ ] pending`

**Intent:**
Replace `call_pm_agent()` stub body with a real LLM call using the shared helpers.
The PM Agent receives a raw feature request and returns `acceptance_criteria`.
This is the simplest real call — short context, structured output, good calibration run.

**Expected Outcomes:**
- `call_pm_agent(task)` sends system prompt + task JSON to watsonx, parses response, merges onto task.
- On success: `acceptance_criteria` is populated with 3–6 strings, `status="in_progress"`, `current_agent="architect_agent"`, history entry appended with `success=true`.
- On LLM error: `status="blocked"`, `success=null` history entry, routes to Manager.
- Stub behaviour preserved when `BOB_API_KEY` is not set (empty merge = task unchanged = stub output intact via existing history append).
- All 67 existing tests still pass.

**Todo List:**
1. In `call_pm_agent()`: replace the stub block with `return _safe_agent_call(task, "pm_agent", _build_prompt("pm_agent", task))`.
2. The history append and field mutations happen inside the LLM's returned JSON — the merge picks them up automatically.
3. Add a fallback: if `_safe_agent_call` returns a task where `acceptance_criteria` is still `[]` (LLM returned empty merge), fall back to stub criteria and log a warning. This ensures demo never silently fails.
4. Add one integration test: mock `_call_llm` to return a valid PM Agent JSON response, assert `acceptance_criteria` is populated correctly.

**Relevant Files:**
- `orchestration/pipeline.py:call_pm_agent` — replace stub
- `agents/pm_agent/system_prompt.md` — prompt loaded by `_build_prompt`
- `orchestration/test_pipeline.py` — add mocked integration test

---

## Sub-Task 3 — Person A: Architect Agent real LLM call

**Status:** `[ ] pending`

**Intent:**
Replace `call_architect_agent()` stub with real LLM call. Architect is the agent
that scopes files and writes the plan — its output directly gates whether Coding can proceed.
The `_validate_architect_output()` validation already exists in `pipeline.py` and will
catch bad file paths automatically after the real call.

**Expected Outcomes:**
- `call_architect_agent(task)` sends system prompt + task JSON, gets `scoped_files` and `plan` back.
- On success: `scoped_files` populated (max 5 paths), `plan` set, `status="in_progress"`, `current_agent="coding_agent"`, history appended.
- If LLM's `scoped_files` contains paths that don't exist and aren't `NEW FILE:` declared, `_validate_architect_output()` catches it automatically in `call_coding_agent()` and blocks the task.
- On LLM error: `status="blocked"`, routes to Manager.
- All 67 existing tests still pass.

**Todo List:**
1. Replace `call_architect_agent()` stub body with `return _safe_agent_call(task, "architect_agent", _build_prompt("architect_agent", task))`.
2. Add fallback: if merged task has `scoped_files=[]`, log warning and fall back to stub scoped files so pipeline doesn't silently proceed with no files.
3. Add one integration test: mock `_call_llm` to return a valid Architect JSON response, assert `scoped_files` and `plan` are set.

**Relevant Files:**
- `orchestration/pipeline.py:call_architect_agent` — replace stub
- `agents/architect_agent/system_prompt.md` — prompt loaded by `_build_prompt`

---

## Sub-Task 4 — Person B: Coding Agent real LLM call

**Status:** `[ ] pending`

**Intent:**
Replace the stub implementation path in `call_coding_agent()` with a real LLM call.
The pre-implementation validation (`_validate_architect_output`) already runs before
the LLM call — this sub-task only replaces the stub body that executes on valid input.

**Expected Outcomes:**
- On valid Architect input + successful LLM call: `code_diff` populated with unified diff string, `status="in_progress"`, `current_agent="testing_agent"`, history appended with `success=true`.
- Architect error path unchanged — still blocks with `success=null` before LLM is called.
- On LLM error (parse fail, HTTP fail): `status="blocked"`, `success=null` history, routes to Manager.
- All 67 existing tests still pass.

**Todo List:**
1. In `call_coding_agent()`, replace the stub implementation block (after the validation check) with `return _safe_agent_call(task, "coding_agent", _build_prompt("coding_agent", task))`.
2. Add fallback: if merged `code_diff` is `None` or empty string after merge, set `status="blocked"` and explain — this prevents silent empty diffs proceeding to Testing.
3. Add one integration test: mock `_call_llm` to return valid Coding JSON, assert `code_diff` is set.

**Relevant Files:**
- `orchestration/pipeline.py:call_coding_agent` lines 263-284 — replace stub path only
- `agents/coding_agent/system_prompt.md`

---

## Sub-Task 5 — Person C: Testing Agent real LLM call

**Status:** `[ ] pending`

**Intent:**
Replace `call_testing_agent()` stub. The Testing Agent reads the `code_diff` and
`acceptance_criteria` and returns `test_results`. It must handle both pass and fail
correctly — a test failure should set `status="needs_retry"` and route back to Coding.

**Expected Outcomes:**
- On successful LLM call: `test_results` populated with `{passed, criteria_matched, failures}`.
- If `test_results.passed == false`: `status="needs_retry"`, `current_agent="coding_agent"`, history with `success=false`.
- If `test_results.passed == true`: `status="in_progress"`, `current_agent="review_agent"`, history with `success=true`.
- On LLM error: `status="blocked"`, `success=null`, routes to Manager.
- All 67 existing tests still pass.

**Todo List:**
1. Replace `call_testing_agent()` stub with `_safe_agent_call(task, "testing_agent", _build_prompt("testing_agent", task))`.
2. After merge: check `task["test_results"]["passed"]`. If false and pipeline hasn't set `status="needs_retry"` (LLM may not have set it), enforce: `task["status"]="needs_retry"`, `task["current_agent"]="coding_agent"`. This is a safety net — the LLM's JSON should do this, but the pipeline enforces it.
3. Add one integration test: mock `_call_llm` to return a test-failed JSON, assert `status="needs_retry"`.

**Relevant Files:**
- `orchestration/pipeline.py:call_testing_agent` — replace stub
- `agents/testing_agent/system_prompt.md`

---

## Sub-Task 6 — Person D: Review Agent real LLM call

**Status:** `[ ] pending`

**Intent:**
Replace `call_review_agent()` stub. The Review Agent reads the `code_diff` and applies
the security checklist. A failed review (high/critical findings) should set
`status="needs_retry"` and also retroactively mark the most recent `coding_agent`
history entry as `success=false` — this is what the Manager Agent reads to track
coding agent failure rate.

**Expected Outcomes:**
- On successful LLM call with no high/critical findings: `review_result={passed:true, findings:[]}`, `status="awaiting_human_approval"`, history with `success=true`.
- On successful LLM call with high/critical findings: `review_result={passed:false, findings:[...]}`, `status="needs_retry"`, `current_agent="coding_agent"`, history with `success=false`. Most recent `coding_agent` history entry retroactively set to `success=false`.
- On LLM error: `status="blocked"`, `success=null`, routes to Manager.
- All 67 existing tests still pass.

**Todo List:**
1. Replace `call_review_agent()` stub with `_safe_agent_call(task, "review_agent", _build_prompt("review_agent", task))`.
2. After merge: enforce review logic as safety net — if `review_result["passed"] == false`, ensure `status="needs_retry"` and `current_agent="coding_agent"`. If `review_result["passed"] == true`, ensure `status="awaiting_human_approval"`.
3. Keep the retroactive coding_agent history mutation (lines 344-348 in current stub) — this is intentional and matches conventions (coding_agent's output was rejected, so its run is a failure).
4. Add one integration test: mock `_call_llm` to return a review-failed JSON, assert `status="needs_retry"` and most recent coding_agent history entry has `success=false`.

**Relevant Files:**
- `orchestration/pipeline.py:call_review_agent` — replace stub
- `agents/review_agent/system_prompt.md`

---

## Sub-Task 7 — End-to-End Integration Test

**Status:** `[ ] pending`

**Intent:**
Run a full pipeline with all real agents (or mocked LLM responses) to confirm the
complete data flow is correct: task object arrives at each agent with all fields intact,
each agent's output is correctly merged, Manager Agent fires at the end, Reflection
triggers when coding_agent underperforms, dashboard shows correct data.

**Expected Outcomes:**
- `python orchestration/pipeline.py --request "Add rate limiting to the login endpoint"` runs end-to-end without crashing.
- Dashboard Tab 1 shows per-agent success rates after the run.
- Dashboard Tab 3 shows the task in "Needs Your Review" or "Blocked" as appropriate.
- If review fails twice: Manager report shows `coding_agent` in `underperformers`, Reflection Agent rewrites `coding_agent/system_prompt.md`, prompt_history shows v0 → v1.
- `pytest` still shows 67+ tests passing (new tests added above add to the count).

**Todo List:**
1. Confirm all teammates have merged their sub-tasks into `feature/manager-agent` (or integration branch).
2. Run `python orchestration/pipeline.py --request "Add rate limiting to the login endpoint"` with `review_passed=False` in stub. Confirm status=blocked after 2 retries.
3. Run again 2 more times (3 total blocked runs). Confirm `coding_agent` appears in Manager report `underperformers` after run 3.
4. Confirm `prompt_history/coding_agent_v1.md` is created.
5. Run `streamlit run dashboard/app.py`. Confirm Tab 1 shows lines dropping below 60% threshold, Tab 2 shows the diff, Tab 3 shows blocked tasks.
6. Run full test suite: `pytest agents/manager_agent/test_manager.py agents/reflection_agent/test_reflection.py orchestration/test_pipeline.py -v`. Must be 0 failures.

**Relevant Files:**
- `orchestration/pipeline.py` — full file
- `dashboard/app.py`
- `dashboard/run_history.json` — inspect after runs
- `agents/reflection_agent/prompt_history/` — inspect for version files

---

## Integration Order (dependency graph)

```
Sub-Task 1 (Pipeline helpers)      ← must be FIRST, everything depends on it
    ↓
Sub-Tasks 2, 3, 4, 5, 6            ← can run in PARALLEL across teammates
    ↓
Sub-Task 7 (End-to-end test)       ← must be LAST
```

Sub-tasks 2–6 are independent of each other once Sub-task 1 is merged.
Each teammate only touches `pipeline.py` in their own `call_*_agent()` function.
No merge conflicts expected.

---

## What to tell teammates (copy-paste for them)

> **Before you start your sub-task:**
> 1. Pull latest `feature/manager-agent` branch.
> 2. Confirm `python -m pytest orchestration/test_pipeline.py -v` shows 67 passed.
> 3. Your only job is to replace the stub body inside `call_YOUR_agent()` in `orchestration/pipeline.py`. Do NOT touch any other function.
> 4. Use `_safe_agent_call(task, "YOUR_agent", _build_prompt("YOUR_agent", task))` — this handles HTTP call, JSON parse, merge, error routing for you.
> 5. Add the safety net enforcement after the call (see your sub-task's Todo List for details).
> 6. Run `pytest` before pushing — must stay green.

---

## Pre-Integration Checklist (do this NOW before any sub-task starts)

- [ ] Sub-task 1 is implemented and merged
- [ ] All 67 existing tests pass on the integration branch
- [ ] `.env` file exists locally with real `BOB_API_KEY` and `WATSONX_PROJECT_ID`
- [ ] `python agents/reflection_agent/reflection.py --report agents/reflection_agent/test_data/mock_manager_report.json` returns a real rewrite (not STUB MODE)
- [ ] All teammates have pulled the latest branch with the completed system prompts
- [ ] Each teammate has read their agent's `system_prompt.md` and confirmed the output schema matches what their LLM call will return
- [ ] `streamlit run dashboard/app.py` runs without errors (test with no data first)
