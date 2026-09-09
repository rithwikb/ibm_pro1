# Person E — Manager Agent, Reflection Agent, Dashboard & Integration Lead
## Complete Build Plan — AI Dev Team in a Box (IBM Bob Hackathon)

---

## Role Summary

Person E owns the three hardest, most differentiated pieces of the project:

1. **Manager Agent** — reads every completed task's `history` array, computes per-agent success rates, and decides which agents need improvement
2. **Reflection Agent** — rewrites the system prompt of any underperforming agent based on what went wrong, producing an inspectable before/after diff
3. **Live Dashboard** — shows success-rate graphs climbing over time and the prompt diff view (the demo moment)
4. **End-to-End Integration Lead** — wires all five agents together into the orchestration pipeline and owns the full-chain debug sessions in Weeks 2–3

Person E's code is the last thing built and the first thing visible to judges. Everything the team builds flows through here.

---

## Architecture Person E Owns

```
agents/
  manager_agent/
    agent_stats.json       ← persistent success-rate store
    system_prompt.md       ← Manager Agent's Bob custom mode prompt
    manager.py             ← logic: reads history, computes rates, flags underperformers
  reflection_agent/
    system_prompt.md       ← Reflection Agent's Bob custom mode prompt
    reflection.py          ← logic: reads manager output, rewrites failing agent's prompt
    prompt_history/        ← before/after prompt diffs stored here

orchestration/
  pipeline.py              ← the main orchestrator: calls agents in sequence, passes JSON

dashboard/
  app.py                   ← Flask/Streamlit app
  templates/ or static/    ← HTML/CSS for the live graph + prompt diff view
  run_history.json         ← log of every pipeline run (input for the graph)
```

---

## The JSON Contract (read this before anything else)

The schema is already defined in [`schemas/task_schema.json`](../schemas/task_schema.json). Every agent reads and writes this exact object. The `history` array is what the Manager Agent consumes — each entry has `agent`, `output_summary`, `timestamp`, and `success` (boolean). Person E's agents read `success` to compute rates. Never modify the schema without a team heads-up.

---

## Sub-Tasks

---

### Sub-Task 0 — Day 0 Setup (TODAY)

**Intent:** Get your environment running, understand every existing file, confirm your Bob account is working, and do a calibration coin spend — so you know your real cost-per-action before Week 1 testing begins.

**Expected Outcomes:**
- Repo cloned and running on your machine
- Python environment created with dependencies noted
- `schemas/task_schema.json` read and understood
- One Bob agent session run (any simple test) with usage gauge checked
- `docs/person-e-plan.md` (this file) reviewed and any questions raised
- A short note in `docs/` capturing your calibration result

**Todo List:**
1. Clone the repo; confirm you are on the `feature/manager-agent` branch (`git status`)
2. Read [`schemas/task_schema.json`](../schemas/task_schema.json) end to end — this is the contract your Manager Agent reads
3. Create `requirements.txt` at repo root with: `jsonschema`, `flask` (or `streamlit`), `plotly` (for the graph), `pytest`
4. Create a Python virtual environment and install dependencies
5. Create `.env.example` at repo root documenting any environment variables you will need (Bob API key, etc.)
6. Run one trivial Bob session (ask it anything) and note the coin cost in `docs/calibration-notes.md`
7. Read the README so you know what the other agents are expected to produce
8. Write a one-paragraph note in `docs/calibration-notes.md` with: coins spent, what one full pipeline run might cost at that rate, and your personal spending strategy for 3 weeks

**Relevant Context:**
- [`schemas/task_schema.json`](../schemas/task_schema.json) — the contract
- [`README.md`](../README.md) — overall structure
- [`orchestration/pipeline.py`](../orchestration/pipeline.py) — currently empty, you will fill this

**Status:** [x] complete — Day 0 setup done; env files, schema read, calibration noted

---

### Sub-Task 1 — Design the Manager Agent Logic (Week 1, before building)

**Intent:** Decide exactly how the Manager Agent computes success rates and flags underperformers — on paper (pseudocode/design doc) — before writing a single line of code. This prevents rework and is the design output the Reflection Agent depends on.

**Expected Outcomes:**
- `docs/manager-design.md` exists with: data model for `agent_stats.json`, the formula for success rate, and the threshold for "underperforming"
- The `agent_stats.json` schema is agreed and written down
- Pseudocode for `manager.py`'s main loop is written

**Todo List:**
1. Decide on the `agent_stats.json` data model — suggestion:
   ```json
   {
     "pm_agent":       { "runs": 0, "successes": 0, "rate": null },
     "architect_agent": { "runs": 0, "successes": 0, "rate": null },
     "coding_agent":   { "runs": 0, "successes": 0, "rate": null },
     "testing_agent":  { "runs": 0, "successes": 0, "rate": null },
     "review_agent":   { "runs": 0, "successes": 0, "rate": null }
   }
   ```
2. Define the "underperforming" threshold — suggestion: success_rate < 0.6 after at least 3 runs (avoids flagging on a single failure)
3. Write pseudocode in `docs/manager-design.md` for:
   - `update_stats(task_object)` — iterates `history`, increments runs/successes per agent
   - `get_underperformers(stats, threshold)` — returns list of agents below threshold
   - `generate_manager_report(task_object, stats)` — what the Manager Agent outputs (a JSON object)
4. Define the Manager Agent's output JSON shape — suggestion:
   ```json
   {
     "task_id": "task_001",
     "stats_snapshot": { ... },
     "underperformers": ["coding_agent"],
     "recommended_action": "rewrite_prompt",
     "reasoning": "coding_agent succeeded 1/4 runs; review rejected 3 times for missing input validation"
   }
   ```
5. Write the design doc to `docs/manager-design.md`

**Relevant Context:**
- [`schemas/task_schema.json`](../schemas/task_schema.json) — specifically the `history[].success` field the Manager reads
- Section 9 of the hackathon spec (agent handoff, JSON contract)

**Status:** [x] complete — `docs/manager-design.md` written; rolling window, threshold rule, output JSON shape, pseudocode all documented

---

### Sub-Task 2 — Design the Reflection Agent Logic (Week 1, before building)

**Intent:** Define exactly how the Reflection Agent rewrites a failing agent's system prompt — specifically and inspectably, not just "try harder". This is the demo's credibility hinge.

**Expected Outcomes:**
- `docs/reflection-design.md` exists with: input/output schema, the rewriting strategy, and what makes a diff "real" vs superficial
- Prompt storage strategy defined (where old and new prompts are saved)

**Todo List:**
1. Define what the Reflection Agent receives as input — it reads the Manager's report plus the failing agent's current system prompt
2. Define the rewriting strategy — the Reflection Agent must produce targeted changes, e.g.:
   - If coding_agent keeps failing review for "missing input validation" → add a specific instruction about always validating user inputs
   - If testing_agent keeps missing criteria → add a specific instruction about checking each acceptance criterion explicitly
3. Define the diff format — store as `agents/reflection_agent/prompt_history/{agent_name}_{version}.md` so the dashboard can show before/after
4. Write pseudocode for `reflection.py`:
   - `load_current_prompt(agent_name)` — reads the agent's current `system_prompt.md`
   - `generate_rewrite(current_prompt, manager_report, failure_reasons)` — calls the Reflection Agent Bob mode
   - `save_prompt_diff(agent_name, old_prompt, new_prompt)` — writes both versions to `prompt_history/`
5. Write the design doc to `docs/reflection-design.md`

**Relevant Context:**
- Sub-Task 1 output (`docs/manager-design.md`) — Reflection receives Manager's output
- Section 8 of hackathon spec (the "fake vs real improvement" risk)
- Each agent's `system_prompt.md` will live in their directory — treat these as versioned files

**Status:** [x] complete — `docs/reflection-design.md` written; input/output schema, rewrite strategy, specificity rules, prompt versioning documented

---

### Sub-Task 3 — Build the Manager Agent (Week 1, standalone)

**Intent:** Build a working `manager.py` that, given a completed task JSON object, updates `agent_stats.json` and produces a Manager report JSON. Tested standalone — no pipeline dependency yet.

**Expected Outcomes:**
- `agents/manager_agent/manager.py` is written and runnable
- `agents/manager_agent/agent_stats.json` is initialized
- `agents/manager_agent/system_prompt.md` contains the Bob custom mode definition
- Running `python agents/manager_agent/manager.py --task sample_task.json` produces valid output
- At least 3 unit tests pass in `agents/manager_agent/test_manager.py`

**Todo List:**
1. Create `agents/manager_agent/agent_stats.json` with zeroed-out stats for all 5 agents
2. Write `agents/manager_agent/manager.py` with three functions:
   - `update_stats(task_obj, stats)` — reads `history`, updates stats
   - `get_underperformers(stats, threshold=0.6, min_runs=3)` — returns list
   - `generate_report(task_obj, stats)` — assembles the Manager output JSON
3. Write `agents/manager_agent/system_prompt.md` — the Bob custom mode prompt for the Manager Agent. Key instructions:
   - "You are the Manager Agent. You receive a completed task JSON and the current agent_stats.json."
   - "Output only valid JSON. No prose."
   - "Your output must contain: task_id, stats_snapshot, underperformers, recommended_action, reasoning."
   - "Flag any agent whose success rate is below 60% across at least 3 runs."
4. Write `agents/manager_agent/test_manager.py` with tests for:
   - `update_stats` correctly increments from a task with 3 history entries
   - `get_underperformers` correctly flags an agent at 33% success rate
   - `get_underperformers` does NOT flag an agent with fewer than 3 runs
5. Run tests: `pytest agents/manager_agent/test_manager.py -v`

**Relevant Context:**
- [`schemas/task_schema.json`](../schemas/task_schema.json) — `history[].success` field
- `docs/manager-design.md` — your design from Sub-Task 1
- Manager Agent is pure Python logic — it does NOT call Bob in this sub-task, it just processes JSON

**Status:** [x] complete — `manager.py` fully built + 24 unit tests passing; rolling window, underperformer detection, report generation all implemented

---

### Sub-Task 4 — Build the Reflection Agent (Week 1, standalone)

**Intent:** Build `reflection.py` and the Bob custom mode prompt for the Reflection Agent. When given a Manager report identifying a failing agent, it produces a rewritten system prompt for that agent.

**Expected Outcomes:**
- `agents/reflection_agent/reflection.py` is written
- `agents/reflection_agent/system_prompt.md` exists
- `agents/reflection_agent/prompt_history/` directory exists
- Running `reflection.py` on a mock manager report produces a non-trivial prompt rewrite
- The rewrite is saved as a diff-able file pair in `prompt_history/`

**Todo List:**
1. Create `agents/reflection_agent/prompt_history/` directory (add a `.gitkeep` to track it)
2. Write `agents/reflection_agent/reflection.py` with:
   - `load_current_prompt(agent_name)` — reads `agents/{agent_name}/system_prompt.md`
   - `save_prompt_version(agent_name, version, content)` — writes to `prompt_history/{agent_name}_v{version}.md`
   - `build_reflection_input(manager_report, current_prompt)` — assembles the prompt context for the Reflection Agent Bob call
   - `parse_reflection_output(output)` — extracts the rewritten prompt from the Bob response
3. Write `agents/reflection_agent/system_prompt.md` — the Bob custom mode prompt for the Reflection Agent. Key instructions:
   - "You are the Reflection Agent. You receive a failing agent's current system prompt and a report of its failures."
   - "Identify the specific instructions that are missing or ambiguous, based on the failure pattern."
   - "Output a rewritten system prompt that adds or corrects those specific instructions."
   - "Do NOT output vague changes like 'try harder' or 'be more careful'. Every change must be specific and traceable to a failure type."
   - "Output format: JSON with fields: agent_name, version, rewritten_prompt, change_summary (a bulleted list of exactly what changed and why)"
4. Create a mock manager report JSON in `agents/reflection_agent/test_data/mock_manager_report.json` for testing
5. Write `agents/reflection_agent/test_reflection.py` — test that `save_prompt_version` creates the expected file and that `build_reflection_input` produces a non-empty string

**Relevant Context:**
- `docs/reflection-design.md` — your design from Sub-Task 2
- Sub-Task 3 output — Manager report shape
- The "fake vs real improvement" risk is the biggest threat to this component — the `change_summary` field enforces specificity

**Status:** [x] complete — `reflection.py` fully built + 20 unit tests passing; file versioning, watsonx HTTP call, graceful stub fallback, `validate_reflection_output()` enforcing specificity all implemented

---

### Sub-Task 5 — Build the Orchestration Pipeline (Week 2)

**Intent:** Write `orchestration/pipeline.py` — the Python script that calls each agent in sequence, passes the JSON task object, and handles the retry loop. This is what wires the whole team's work together.

**Expected Outcomes:**
- `orchestration/pipeline.py` is a runnable script
- Given a feature request string, it calls PM → Architect → Coding → Testing → Review in sequence
- The retry loop is implemented with a hard cap at `retry_count == 2`
- After the Review stage, the Manager Agent is called to update stats
- If `retry_count == 2` and review fails, status is set to `blocked` and a human message is printed
- Each agent's call is a function that takes a task JSON and returns an updated task JSON

**Todo List:**
1. Define the pipeline structure — each agent is a function `call_{agent_name}(task: dict) -> dict`
2. For Week 2, these agent functions can be stubs that print what they would do and return a modified task — real agent calls come later
3. Implement the main pipeline loop:
   ```
   task = initialize_task(feature_request)
   task = call_pm_agent(task)
   task = call_architect_agent(task)
   while task["retry_count"] <= 2:
       task = call_coding_agent(task)
       task = call_testing_agent(task)
       task = call_review_agent(task)
       if review passes: break
       if retry_count == 2: set status = blocked; break
       retry_count += 1
   if status != blocked:
       task = call_manager_agent(task)
       task["status"] = "awaiting_human_approval"
   print_final_report(task)
   ```
4. Implement `initialize_task(feature_request)` — creates a fresh task object matching `task_schema.json`
5. Implement `call_manager_agent(task)` — calls `manager.py`'s logic, appends to history, decides if Reflection Agent should run
6. Implement `call_reflection_agent(manager_report)` if underperformers are flagged
7. Validate every task object against the JSON schema before passing it to the next agent: use `jsonschema.validate(task, schema)`
8. Write `orchestration/test_pipeline.py` — test the retry cap (ensure it stops at 2) and the happy-path flow

**Relevant Context:**
- [`schemas/task_schema.json`](../schemas/task_schema.json) — validate against this at every handoff
- Sub-Task 3 (`manager.py`) and Sub-Task 4 (`reflection.py`) are called from here
- Other team members (Persons A–D) will fill in `call_pm_agent`, `call_architect_agent`, etc. — coordinate stubs

**Status:** [x] complete — `pipeline.py` fully built + 15 unit tests passing; full PM→Architect→Coding→Testing→Review→Manager→Reflection chain, retry loop capped at 2, schema validation at every handoff, `save_task()` + `append_run_history()` for dashboard feed

---

### Sub-Task 6 — Build the Live Dashboard (Week 2–3, with Person A)

**Intent:** Build a web app that shows two things: (1) a line graph of per-agent success rates across pipeline runs, (2) a side-by-side before/after prompt diff for any rewritten agent prompt. This is the visual proof of the improvement loop.

**Expected Outcomes:**
- `dashboard/app.py` runs with `python dashboard/app.py` or `streamlit run dashboard/app.py`
- Graph updates in near-real-time as `run_history.json` is updated by the pipeline
- Prompt diff view shows the exact text that changed between prompt versions
- Demo-ready: works reliably on the laptop being used for the live presentation

**Todo List:**
1. Decide on the dashboard framework — **Streamlit** is recommended (fastest to build, no separate frontend needed, Python-only):
   - Add `streamlit` and `plotly` to `requirements.txt`
2. Define `dashboard/run_history.json` — appended to by the pipeline after every run:
   ```json
   [
     {
       "run_id": 1,
       "timestamp": "...",
       "agent_rates": {
         "coding_agent": 0.5,
         "testing_agent": 1.0,
         ...
       },
       "reflections_triggered": ["coding_agent"]
     }
   ]
   ```
3. Update `orchestration/pipeline.py` to append a run record to `run_history.json` at the end of each full pipeline run
4. Build `dashboard/app.py` with two tabs:
   - **Tab 1 — Success Rate Graph:** `plotly` line chart, one line per agent, x-axis = run number, y-axis = success rate 0.0–1.0. Auto-refreshes every 5 seconds.
   - **Tab 2 — Prompt Diff View:** dropdown to select agent name and version; shows old and new prompt side by side with changed lines highlighted
5. Implement the diff rendering — use Python's `difflib.unified_diff` to generate the diff, display in a `st.code` block
6. Test with at least 5 synthetic run records in `run_history.json` to confirm the graph renders correctly before any real pipeline runs

**Relevant Context:**
- `agents/reflection_agent/prompt_history/` — source of prompt diff data
- `dashboard/run_history.json` — source of graph data
- Person A assists with dashboard polish in Week 3

**Status:** [x] complete — `dashboard/app.py` built; 3-tab Streamlit app (Success Rate Graph, Prompt Diffs, Needs Your Review); Plotly line chart with threshold line; `difflib` unified diff view; auto-refresh every 5s

---

### Sub-Task 7 — End-to-End Integration & Debug Runs (Week 2–3, whole team)

**Intent:** Run the full pipeline end-to-end on the sample repo, debug whatever breaks, and get to a state where one complete feature request goes PM → Review → Manager → Reflection reliably.

**Expected Outcomes:**
- At least two clean end-to-end runs complete without crashing
- `run_history.json` has at least 5 entries from real runs
- The dashboard shows a visible improvement trend (at least one Reflection Agent rewrite occurred)
- All team members have confirmed their agent's stub → real implementation transition

**Todo List:**
1. Coordinate with Person A (PM + Architect) to replace pipeline stubs with real agent calls for their agents
2. Coordinate with Person B (Coding) to replace the coding stub
3. Coordinate with Person C (Testing) to replace the testing stub
4. Coordinate with Person D (Review) to replace the review stub — this is the agent that feeds `success` back into `history`
5. Run end-to-end test #1 — expect failures, document every failure in `docs/integration-run-log.md`
6. Fix the top 3 issues from run #1
7. Run end-to-end test #2 — both passes should be clean
8. Manually force a Reflection Agent trigger (set a stub to always return `success: false`) to test the improvement loop visibly
9. Confirm the dashboard updates correctly after each run

**Relevant Context:**
- All sub-tasks above must be complete before this begins
- `docs/integration-run-log.md` is new — create it to log run results

**Status:** [-] in progress — `docs/integration-run-log.md` created; stub-to-real replacement tracker written; full-chain runs pending teammates delivering real agents (Persons A–D)

---

### Sub-Task 8 — Demo Polish & Rehearsal (Week 3)

**Intent:** Make the demo moment reliable, visually clear, and rehearsable. The target: someone types a feature request, five agents visibly work, a tested diff emerges, then the dashboard shows the improvement graph.

**Expected Outcomes:**
- Demo script written in `docs/demo-script.md` (exact feature request to type, expected outputs at each stage)
- Backup video recorded of the best clean run
- Coins-per-run measured and documented so you don't run out mid-demo

**Todo List:**
1. Write `docs/demo-script.md` — the exact words to type as the feature request for the live demo, and the expected output at every stage
2. Run the demo script 3 times in a row — it must work all 3 times to be considered rehearsal-ready
3. Record one clean run as a backup video (screen record your terminal + dashboard)
4. Measure coins consumed per full run; multiply by the number of rehearsals + the live demo to confirm you have enough budget
5. Add the "agent transparency log" polish feature — print a visible "conversation" of what each agent decided, so judges see reasoning, not just a final diff. This is a simple formatted print statement in `pipeline.py`
6. Final rehearsal with whole team — Person E runs the pipeline live while others watch the dashboard

**Relevant Context:**
- Section 6 of hackathon spec (non-determinism risk — rehearse the exact demo request)
- Section 11 (Bobcoin budget — measure coins per run here)

**Status:** [-] in progress — `docs/demo-script.md` written with exact commands, narration script, judge Q&A prep, graph pre-seeding commands, and fallback plan; rehearsal runs pending

---

## Today (Day 0) — Exact Action List

This is what you do **right now**, before Week 1 officially starts:

| # | Action | Output |
|---|--------|--------|
| 1 | `git clone` / `git pull` the repo, confirm you're on `feature/manager-agent` | Terminal confirms correct branch |
| 2 | Read `schemas/task_schema.json` completely — understand every field | Mental model of the contract |
| 3 | Read this plan file completely | Know your full 3-week arc |
| 4 | Create `requirements.txt` with: `jsonschema`, `streamlit`, `plotly`, `pytest`, `flask` | File committed to branch |
| 5 | Create `.env.example` with placeholder for Bob API key | File committed (never commit real keys) |
| 6 | Run one Bob session — any question — and note the coin usage | Know your cost baseline |
| 7 | Write `docs/calibration-notes.md` with your spending strategy | Committed to branch |
| 8 | Message the team: confirm the JSON schema is locked (no changes without group OK), confirm who is doing the Day 1 shared meeting | Team alignment |
| 9 | Read Sub-Tasks 1 and 2 and start writing `docs/manager-design.md` pseudocode (even rough notes count) | Head start on Week 1 |

---

## Key Risks Person E Must Manage Personally

| Risk | Your Mitigation |
|------|----------------|
| Reflection Agent produces superficial rewrites | `change_summary` field in every rewrite forces specificity; show the actual diff in the demo |
| Coins drain on full-chain runs | Measure on Day 0, cap retries at 2, spread expensive runs across team accounts in Week 3 |
| Integration bottleneck (Person E doing everything alone in Week 3) | Week 2 full-team debug sessions are blocking — don't let teammates skip them |
| Dashboard not ready in time | Streamlit + plotly can be built in a single afternoon — prioritize Sub-Task 6 in Week 2, not Week 3 |
| Schema drift between agents | You are the integration owner — call it out immediately if any teammate's output doesn't match `task_schema.json` |

---

## File Checklist — Everything Person E Must Create

```
requirements.txt                                ← today
.env.example                                    ← today
docs/calibration-notes.md                       ← today
docs/manager-design.md                          ← Week 1
docs/reflection-design.md                       ← Week 1
docs/integration-run-log.md                     ← Week 2
docs/demo-script.md                             ← Week 3
agents/manager_agent/agent_stats.json           ← Week 1
agents/manager_agent/system_prompt.md           ← Week 1
agents/manager_agent/manager.py                 ← Week 1
agents/manager_agent/test_manager.py            ← Week 1
agents/reflection_agent/system_prompt.md        ← Week 1
agents/reflection_agent/reflection.py           ← Week 1
agents/reflection_agent/test_reflection.py      ← Week 1
agents/reflection_agent/prompt_history/.gitkeep ← Week 1
agents/reflection_agent/test_data/mock_manager_report.json ← Week 1
orchestration/pipeline.py                       ← Week 2
orchestration/test_pipeline.py                  ← Week 2
dashboard/app.py                                ← Week 2
dashboard/run_history.json                      ← Week 2
```
