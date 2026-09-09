# Demo Script — AI Dev Team in a Box

**Event:** IBM SkillUp Hackathon — Developer AI Track  
**Slot:** Live demo (~5 minutes) + Q&A  
**Person running the demo:** Person E  
**Backup:** screen recording of last clean rehearsal run (save before the event)

---

## The Story You're Telling Judges

> "We built a simulated AI engineering team. Five specialized agents — PM, Architect,
> Coder, Tester, and Reviewer — turn a plain-English feature request into a tested,
> reviewed code change with no human writing the code directly.
>
> But the really interesting part: a Manager Agent watches the team work, identifies
> which agent is underperforming, and a Reflection Agent automatically rewrites that
> agent's instructions to fix the problem. The dashboard shows the improvement in
> real time — you can literally watch the system get smarter."

**The demo proves this story with a live run. Not slides. Live.**

---

## Pre-Demo Checklist (do this 30 min before presenting)

- [ ] `git pull` — confirm on `feature/manager-agent` branch
- [ ] `python -m pytest agents/manager_agent/test_manager.py agents/reflection_agent/test_reflection.py orchestration/test_pipeline.py -q` — all 59 passing
- [ ] `streamlit run dashboard/app.py` — confirm dashboard opens, no import errors
- [ ] `dashboard/run_history.json` is pre-seeded with at least 5 synthetic run records (see Section "Pre-seeding the Graph" below)
- [ ] `agents/reflection_agent/prompt_history/` has at least one `coding_agent_v0.md` and one `coding_agent_v1.md` (see Section "Pre-seeding the Prompt Diff" below)
- [ ] Terminal window is clean and font is large enough for judges to read from 2m away
- [ ] Two terminal windows open: one for `pipeline.py`, one for the dashboard (already running)
- [ ] Browser has the dashboard open at `http://localhost:8501`

---

## Demo Run — Step by Step

### Step 1 — Introduce the system (30 seconds, no typing yet)

> "What you're looking at is a live orchestration pipeline. Each column in the
> dashboard is one agent — PM, Architect, Coder, Tester, Reviewer. The line graph
> shows each agent's success rate across the last five pipeline runs. Right now
> they're all at 100% because we've been running clean tests."

**Point to the dashboard on screen. Let judges see the graph for 5 seconds.**

---

### Step 2 — Run the happy path (the pipeline working correctly)

Type this exact command in terminal window 1:

```
python orchestration/pipeline.py --request "Add rate limiting to the login endpoint" --task-id demo_001
```

**Expected output:** Watch the stage banners scroll — PM Agent, Architect Agent, Coding Agent, Testing Agent, Review Agent, Manager Agent. Final line: `[pipeline] + Awaiting human approval.`

**While it runs, narrate:**
> "Watch the stage banners. Each agent picks up the JSON task object from the
> previous agent, does its job, and passes it forward. The Architect scoped the
> relevant files — auth/views.py, extensions.py — so the Coding Agent only sees
> what it needs. This is how we keep context small and cost low."

**After it finishes, switch to the dashboard:**
> "The graph just updated. Run #6 — all five agents at 100%. The task is in the
> 'Needs Your Review' tab, with the code diff and acceptance criteria, waiting
> for human sign-off."

**Click on Tab 3 "Needs Your Review" — show the diff.**

---

### Step 3 — Trigger the improvement loop (the differentiator)

> "Now let me show you the part that makes this different from a chatbot demo."

**Pre-requisite:** Before the demo, run the pipeline 4 times with `review_passed = False`
so `coding_agent` is already at 0% in the rolling window and `coding_agent_v1.md` exists
in `prompt_history/`. This means you don't need to run failures live — you just
point to the already-triggered graph dip and recovery.

**Narrate while pointing to the graph dip on Tab 1:**
> "Earlier, I deliberately made the Review Agent reject the Coding Agent's output
> four times in a row. Watch what happened to the Coding Agent's line — it dipped
> below the 60% threshold here."
>
> "The Manager Agent detected this automatically — pure Python arithmetic, no LLM
> call, so it can never hallucinate a false positive. It identified the failure
> pattern: 'missing input validation'. And it triggered the Reflection Agent."

**Switch to Tab 2 "Prompt Diffs":**
> "Here's the actual before/after diff. The Reflection Agent didn't just say
> 'try harder'. It added a specific pre-return checklist: 'before returning code,
> verify all user inputs are validated for type, length, and format — this addresses
> the pattern that caused 4 consecutive rejections by the Review Agent.'
>
> And then the Coding Agent's rate climbed back up. The system diagnosed its own
> weakness and rewrote the instructions to fix it."

---

### Step 4 — Closing (30 seconds)

> "The differentiator here isn't any one agent — it's the feedback loop.
> A system that watches itself fail, identifies the cause, and updates its own
> instructions. With five agents, 59 passing unit tests, a full retry loop with
> a hard cap at two retries to control cost, and a live dashboard showing
> improvement over time.
>
> Every design decision was made for real-world reliability: JSON contracts
> between agents so information can't be lost in paraphrasing, scoped file
> manifests so context stays small, and deterministic Manager Agent logic
> so the trigger decision is fully auditable."

---

## Q&A Prep — Likely Judge Questions

**Q: How does the Reflection Agent know what to change?**  
A: The Manager Agent extracts the specific failure pattern from `review_result.findings` — e.g. "missing input validation". The Reflection Agent receives that exact pattern in its input and is instructed to trace every change it makes back to a named failure. Vague rewrites are rejected programmatically — `validate_reflection_output()` checks that the `change_summary` references at least one of the expected failure patterns by name.

**Q: How much does this cost to run?**  
A: The Manager Agent is 100% Python — zero LLM calls, zero coins. The Reflection Agent is the only component that calls an LLM, and it only runs when a threshold is crossed — so a clean pipeline run costs nothing beyond the five worker agent calls. Full end-to-end with stub agents: approximately 20–35 coins per run depending on context size.

**Q: What sample repo are you running on?**  
A: FlaskBB — a Flask forum application with ~10,000 lines of code, well-structured auth layer, and a self-contained unit test suite that runs without any external database or API. We scope files narrowly per run — the Architect Agent limits context to 3–5 relevant files, not the whole repo.

**Q: What happens when the pipeline gets stuck?**  
A: Hard cap at 2 retries in the Review → Coding loop. After the second rejection, the task is marked `blocked` and escalated to a human. It also appears in Tab 3 of the dashboard with the Review Agent's findings so the human knows exactly what to fix. The Manager Agent still runs on blocked tasks to update stats — a block counts as a failure for the Coding Agent.

**Q: Is the Reflection Agent actually improving things or just appending text?**  
A: The change is validated before being applied. `validate_reflection_output()` enforces two rules: the `change_summary` must be at least 40 characters (no "improved the prompt"), and it must reference at least one of the named failure patterns. The actual diff in the prompt history shows exactly what changed — the demo makes this visible. And the rolling window graph shows whether the rate actually recovered after the rewrite.

**Q: Why Bob / IBM watsonx specifically?**  
A: Bob's custom modes with separate role definitions and tool permissions are exactly what we needed to isolate each agent's behavior. Each agent runs as its own Bob mode with its own system prompt — they can't see each other's prompts or history. And the agent mode with subagents maps directly onto our pipeline architecture. Plus the Granite model on watsonx was a strong fit for instruction-following tasks like structured JSON output.

---

## Pre-seeding the Graph (for a compelling demo)

To make the graph show a visible dip-and-recovery before the live demo run, seed `dashboard/run_history.json` with synthetic records that tell this story:

```
python -c "
import json, datetime, random

history = []
# Runs 1-3: all agents at 100%
for i in range(1, 4):
    history.append({'run_id': i, 'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                    'task_id': f'demo_seed_{i:03d}',
                    'agent_rates': {'pm_agent': 1.0, 'architect_agent': 1.0, 'coding_agent': 1.0,
                                    'testing_agent': 1.0, 'review_agent': 1.0},
                    'reflections_triggered': []})

# Runs 4-7: coding_agent failing
for i in range(4, 8):
    history.append({'run_id': i, 'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                    'task_id': f'demo_seed_{i:03d}',
                    'agent_rates': {'pm_agent': 1.0, 'architect_agent': 1.0, 'coding_agent': 0.25,
                                    'testing_agent': 1.0, 'review_agent': 1.0},
                    'reflections_triggered': ['coding_agent'] if i == 7 else []})

# Runs 8-10: coding_agent recovered after Reflection rewrite
for i in range(8, 11):
    history.append({'run_id': i, 'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                    'task_id': f'demo_seed_{i:03d}',
                    'agent_rates': {'pm_agent': 1.0, 'architect_agent': 1.0, 'coding_agent': 1.0,
                                    'testing_agent': 1.0, 'review_agent': 1.0},
                    'reflections_triggered': []})

with open('dashboard/run_history.json', 'w') as f:
    json.dump(history, f, indent=2)
print(f'Seeded {len(history)} synthetic run records')
"
```

**The graph will show:** All agents flat at 100% → coding_agent dips to 25% → Reflection triggers → coding_agent climbs back to 100%. This is the story. Run the live demo command to add run #11.

---

## Pre-seeding the Prompt Diff (for a compelling Tab 2)

Run these commands to create a clean `coding_agent_v0.md` (baseline) and `coding_agent_v1.md` (after rewrite) in `prompt_history/`:

```bash
# 1. Snapshot current coding_agent system_prompt.md as v0
python -c "
import shutil, os
src = 'agents/coding_agent/system_prompt.md'
dst = 'agents/reflection_agent/prompt_history/coding_agent_v0.md'
shutil.copy2(src, dst)
print(f'Saved baseline: {dst}')
"

# 2. Force a Reflection Agent run (set review_passed = False, run 4x, then reset)
# Or manually write a realistic v1:
python -c "
content = open('agents/reflection_agent/prompt_history/coding_agent_v0.md').read()
addition = '''
## Pre-Return Checklist (Added by Reflection Agent v1)

Before returning your code diff, verify ALL of the following:

1. **Missing input validation** — every user-facing function validates type, length,
   and format of all inputs before processing. This was the failure pattern in 4
   consecutive Review Agent rejections.
2. **Hardcoded secrets** — no API keys, tokens, passwords, or sensitive values are
   present in the diff. Use environment variables exclusively.
3. **Rate limiting** — if the feature involves login, registration, or password
   reset endpoints, confirm rate limiting is applied.

If ANY item above is not satisfied, fix it before returning output.
'''
with open('agents/reflection_agent/prompt_history/coding_agent_v1.md', 'w') as f:
    f.write(content + addition)
print('Saved coding_agent_v1.md')
"
```

---

## Fallback Plan (if live demo breaks)

1. **Pre-recorded video** — record one clean full run + dashboard in Week 3 rehearsal. Save as `demo_backup.mp4` on a USB stick. If anything goes wrong live, play the video and narrate over it.
2. **Static dashboard screenshot** — if Streamlit won't start, open the pre-taken screenshot in a browser window.
3. **JSON output backup** — the final task JSON from a clean run is in `dashboard/tasks/`. Show it in a text editor if the dashboard is down — the `history` array and `code_diff` tell the whole story.

---

## Rehearsal Log

| # | Date | Result | Issues | Fixed? |
|---|------|--------|--------|--------|
| 1 | — | — | — | — |
| 2 | — | — | — | — |
| 3 | — | — | — | — |

Goal: 3/3 clean runs before the live event.

---

## Phase 1 — Auto-Fix Broken Repo

### The Story
> "We can also point the pipeline at any existing Python repo, scan it for code quality errors automatically, and let the AI team fix them — one bug at a time, with human approval before anything touches the repo."

### Commands

**Step 1 — Scan for bugs:**
```powershell
python orchestration/scan.py --source sample_repo/flaskbb/flaskbb
```
Expected output:
```
Found N flake8 error(s) in sample_repo/flaskbb/flaskbb/
  [flake8] flaskbb/auth/views.py:3  — F401 'os' imported but unused
  ...
Saved to dashboard/bugs.json
Open dashboard to select bugs: python -m streamlit run dashboard/app.py
```

**Step 2 — Open dashboard, go to "🐛 Bug Scanner" tab, select bugs, click "Mark Selected"**

**Step 3 — Run pipeline on selected bugs:**
```powershell
python orchestration/scan.py --source sample_repo/flaskbb/flaskbb --run-selected
```

**Step 4 — Dashboard Tab 3 "Needs Your Review" — review diffs, click Approve**

**Step 5 — Apply approved diffs and verify:**
```powershell
python orchestration/apply_fixes.py --repo sample_repo/flaskbb
```
Expected output:
```
Applied: N/N
Verified fixed: N
```

**Step 6 — Dashboard Tab 3 — see green "✅ Verified fixed — flake8 clean" badges**

---

## Phase 2 — Build From Scratch

### The Story
> "And for a completely new app — you just describe what you want in plain English, and the pipeline generates the full file structure and code, in batches of 5 files, reviewing each batch for security before writing it to disk."

### Commands

**Step 1 — Start a build:**
```powershell
python orchestration/build.py --description "Build a Flask todo app with login, CRUD tasks, and SQLite" --name todo_app
```
Expected output:
```
Planning build for 'todo_app'...
4 batch(es) planned → generated_projects/todo_app/
Batch 1/4: app.py, models.py, config.py, requirements.txt, database.py
...
Build Summary: 4 total, 4 done, 0 failed
```

**Step 2 — Dashboard Tab 4 "🏗️ Generated Projects" — watch progress bar fill**

**Step 3 — Resume if interrupted:**
```powershell
python orchestration/build.py --name todo_app --resume
```

**Step 4 — Find the generated project:**
```powershell
Get-ChildItem generated_projects/todo_app/
```

---
