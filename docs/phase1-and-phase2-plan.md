# Phase 1 & Phase 2 — Full Plan

## Overview

This document covers two phases of capability expansion for the AI Dev Team pipeline:

- **Phase 1 — Auto-Fix Broken Repo:** Scan an existing repo for flake8 errors, let the
  human select which to fix, run the existing pipeline on each, review and approve diffs
  in the dashboard, apply them to disk with `git apply`, and verify the fix.

- **Phase 2 — Build From Scratch:** Accept a plain-English app description (e.g. "Build a
  Flask todo app with login, CRUD tasks, and SQLite"), generate a complete working project
  in `generated_projects/{name}/` by running the pipeline in iterative 5-file batches, and
  surface the result in the dashboard.

Both phases reuse the existing agent stack (PM → Architect → Coding → Testing → Review →
Manager → Reflection) without replacing any existing agent.

---

## Constraints & Confirmed Decisions

| Decision | Source |
|---|---|
| Human-in-the-loop approval before diffs are applied to disk (Phase 1) | Confirmed |
| Auto-apply diffs between Phase 2 batches (no approval per batch) | Confirmed |
| Verification via flake8 re-run on patched files (not full pytest) for Phase 1 | Confirmed |
| `git apply` inside `sample_repo/flaskbb` (reversible with `git reset`) | Confirmed |
| Phase 2 output lands in `generated_projects/{project_name}/` | Confirmed |
| Keep 5-file cap; Phase 2 Architect breaks app into batches of ≤5 files each | Confirmed |
| Phase 2 Testing Agent generates a test file as part of the project and runs pytest on it | Confirmed |
| Pytest scanning of FlaskBB deferred to Phase 2 | Confirmed |

---

## New Files Created by Each Phase

### Phase 1

| File | Action |
|---|---|
| `agents/bug_discovery_agent/__init__.py` | CREATE |
| `agents/bug_discovery_agent/bug_discovery_agent.py` | CREATE |
| `agents/bug_discovery_agent/test_bug_discovery_agent.py` | CREATE |
| `orchestration/scan.py` | CREATE |
| `orchestration/apply_fixes.py` | CREATE |
| `orchestration/test_apply_fixes.py` | CREATE |
| `orchestration/test_phase1.py` | CREATE |
| `dashboard/app.py` | MODIFY — Tab 0 + Tab 3 functional buttons + verification badge |
| `schemas/task_schema.json` | MODIFY — add verified_fixed/verified_failed + verification_result |
| `docs/demo-script.md` | MODIFY — Phase 1 section |

### Phase 2

| File | Action |
|---|---|
| `agents/scaffold_agent/scaffold_agent.py` | CREATE |
| `agents/scaffold_agent/__init__.py` | CREATE |
| `agents/scaffold_agent/test_scaffold_agent.py` | CREATE |
| `orchestration/build.py` | CREATE |
| `orchestration/test_build.py` | CREATE |
| `generated_projects/` | CREATE (directory, .gitkeep) |
| `dashboard/app.py` | MODIFY — Tab 4 "Generated Projects" |
| `schemas/task_schema.json` | MODIFY — add batch_index, total_batches fields |
| `docs/demo-script.md` | MODIFY — Phase 2 section |

---

## Phase 1 Data Flow

```
python orchestration/scan.py --source sample_repo/flaskbb/flaskbb
    → BugDiscoveryAgent.scan_repo()  [runs python -m flake8]
    → dashboard/bugs.json            [list of discovered bugs]

Dashboard Tab 0 "Bug Scanner"
    → Human checks boxes, clicks "Mark Selected"
    → dashboard/bugs.json updated    [status = selected]

python orchestration/scan.py --source sample_repo/flaskbb/flaskbb --run-selected
    → for each selected bug: run_pipeline(request=bug.description)
    → dashboard/tasks/fix_bug_001.json  etc.

Dashboard Tab 3 "Needs Your Review"
    → Human sees diff, clicks Approve button
    → dashboard/tasks/fix_bug_001.json  [status = approved]

python orchestration/apply_fixes.py --repo sample_repo/flaskbb
    → git apply --check, then git apply
    → python -m flake8 <affected files>   [verification]
    → dashboard/tasks/fix_bug_001.json  [verification_result written]

Dashboard Tab 3
    → Green badge "Verified fixed" or Red badge "Verification failed"
```

---

## Phase 2 Data Flow

```
python orchestration/build.py --description "Build a Flask todo app with login, CRUD tasks, and SQLite" --name todo_app
    → ScaffoldAgent.plan_batches()  [LLM: returns ordered list of file batches]
    → orchestration/build.py persists build manifest to generated_projects/todo_app/build_manifest.json

For each batch (up to N batches of 5 files each):
    → run_pipeline_batch(batch, project_dir)
        PM Agent   — acceptance criteria for this batch
        Architect  — scoped_files = this batch's files, plan = what to write
        Coding     — generates diff (NEW FILE diffs for brand-new files)
        Testing    — runs pytest on generated test file
        Review     — security check
    → auto-apply diff to generated_projects/todo_app/
    → update build_manifest.json with batch status

Dashboard Tab 4 "Generated Projects"
    → List of projects with batch progress bars
    → Final project structure tree
    → "Download as zip" (future)
```

---

## `dashboard/bugs.json` Schema

```json
[
  {
    "bug_id": "bug_001",
    "source": "flake8",
    "file": "flaskbb/auth/views.py",
    "line": 42,
    "col": 1,
    "error_type": "F401",
    "message": "'os' imported but unused",
    "description": "Fix flake8 error F401 in flaskbb/auth/views.py line 42 — 'os' imported but unused",
    "raw_output": "flaskbb/auth/views.py:42:1: F401 'os' imported but unused",
    "status": "discovered",
    "task_id": null
  }
]
```

`status` lifecycle: `discovered` → `selected` → `running` → `done` | `skipped`

---

## `generated_projects/{name}/build_manifest.json` Schema

```json
{
  "project_name": "todo_app",
  "description": "Build a Flask todo app with login, CRUD tasks, and SQLite",
  "created_at": "<ISO timestamp>",
  "status": "in_progress" | "complete" | "failed",
  "batches": [
    {
      "batch_index": 1,
      "files": ["app.py", "models.py", "config.py", "requirements.txt", "tests/test_app.py"],
      "task_id": "build_todo_app_b1",
      "status": "done" | "pending" | "failed",
      "completed_at": "<ISO timestamp> or null"
    }
  ]
}
```

---

## Schema Changes to `schemas/task_schema.json`

### Phase 1 additions
- `status` enum: add `"verified_fixed"`, `"verified_failed"`
- New optional field `verification_result` (object or null):
  ```json
  {
    "applied": true,
    "git_apply_output": "...",
    "flake8_passed": true,
    "flake8_output": "0 errors",
    "verified_at": "<ISO timestamp>"
  }
  ```

### Phase 2 additions
- New optional field `batch_index` (integer or null) — which batch in a build run
- New optional field `total_batches` (integer or null) — total planned batches
- New optional field `project_name` (string or null) — name of generated project

---

# Phase 1 Sub-Tasks

---

## P1-ST1 — Bug Discovery Agent

**Status:** [ ] pending

**Intent:**
Create `agents/bug_discovery_agent/bug_discovery_agent.py` — pure Python, no LLM.
Runs `python -m flake8` on a source directory, parses its output, returns structured
bug dicts suitable for feeding into `run_pipeline()` as feature requests.

**Expected Outcomes:**
- `scan_flake8(source_dir)` runs flake8 and returns list of parsed bug dicts
- `description_from_flake8(file, line, code, message)` returns plain-English string
- `scan_repo(source_dir)` is the main entry: calls scan_flake8, assigns `bug_id`s, sets `status="discovered"`
- `save_bugs(bugs, path)` and `load_bugs(path)` handle JSON persistence
- Handles: flake8 not installed (auto-installs), zero errors, malformed lines
- All tests pass

**Todo List:**
1. Create `agents/bug_discovery_agent/__init__.py` (empty)
2. Create `agents/bug_discovery_agent/bug_discovery_agent.py`:
   - `_ensure_flake8()` — runs `sys.executable -m flake8 --version`; on failure runs
     `pip install flake8`
   - `scan_flake8(source_dir: str) -> list[dict]` — runs:
     `[sys.executable, "-m", "flake8", source_dir, "--max-line-length=120", "--format=default"]`
     parses stdout with regex `^(.+):(\d+):(\d+):\s+([A-Z]\d+)\s+(.+)$`
     skips non-matching lines silently
   - `description_from_flake8(file, line, code, message) -> str` — plain English e.g.:
     `"Fix flake8 error F401 in flaskbb/auth/views.py line 3 — 'os' imported but unused"`
   - `scan_repo(source_dir: str) -> list[dict]` — calls scan_flake8, assigns
     `bug_id = f"bug_{i+1:03d}"`, sets `status="discovered"`, `task_id=None`, `source="flake8"`
   - `save_bugs(bugs, output_path)` — writes JSON with indent=2
   - `load_bugs(path)` — reads JSON, returns `[]` if missing or parse error
3. Create `agents/bug_discovery_agent/test_bug_discovery_agent.py`:
   - `scan_flake8` returns `[]` on empty subprocess output (mock)
   - `scan_flake8` parses known flake8 line into correct dict fields
   - `scan_flake8` skips malformed lines without raising
   - `description_from_flake8` returns non-empty string
   - `scan_repo` assigns `bug_001`, `bug_002`, ... sequentially
   - `scan_repo` sets `status="discovered"` and `task_id=None`
   - `save_bugs`/`load_bugs` round-trip preserves all fields

**Relevant Context:**
- Use `sys.executable` not `"python"` — guarantees same env, avoids PATH issues
- `subprocess` pattern: `agents/testing_agent/agents/testing_agent/testing_agent.py` lines 64-75
- flake8 output format: `filename:line:col: CODE message` — one error per line
- `source_dir` = `sample_repo/flaskbb/flaskbb/` (Python source, not tests dir)

---

## P1-ST2 — `orchestration/scan.py` CLI

**Status:** [ ] pending

**Intent:**
CLI wrapper that either scans and writes `bugs.json` or runs the pipeline on selected bugs.
Separates the scan step from the fix step so the human can inspect before committing.

**Expected Outcomes:**
- `python orchestration/scan.py --source sample_repo/flaskbb/flaskbb` scans + prints summary + saves `bugs.json`
- `python orchestration/scan.py --source sample_repo/flaskbb/flaskbb --run-selected` runs pipeline for each `status=selected` bug
- Handles: no bugs found, no bugs selected, `bugs.json` missing
- Each bug's `task_id` is written back to `bugs.json` after pipeline runs

**Todo List:**
1. Create `orchestration/scan.py`:
   - `argparse`: `--source` (required), `--run-selected` (flag), `--bugs-file` (default: `dashboard/bugs.json`)
   - `cmd_scan(source_dir, bugs_path)`:
     - calls `scan_repo(source_dir)`
     - saves to `bugs_path` via `save_bugs()`
     - prints summary table (source, file:line, error_type, description[:60])
     - prints: `"Open dashboard to select bugs: python -m streamlit run dashboard/app.py"`
   - `cmd_run_selected(bugs_path)`:
     - loads bugs via `load_bugs()`
     - filters `status == "selected"`
     - if none: prints warning and exits
     - for each selected bug:
       - set `status = "running"`, save (partial-progress safety)
       - call `run_pipeline(feature_request=bug["description"], task_id=f"fix_{bug['bug_id']}")`
       - set `task_id = result["task_id"]`, `status = "done"`, save
   - `main()` dispatches based on `--run-selected` flag

**Relevant Context:**
- `run_pipeline` in `orchestration/pipeline.py` — add to sys.path or use relative import
- `scan_repo`, `save_bugs`, `load_bugs` from `agents/bug_discovery_agent/bug_discovery_agent.py`
- `dashboard/bugs.json` is owned by this module; dashboard only reads it

---

## P1-ST3 — Dashboard Tab 0 "Bug Scanner"

**Status:** [ ] pending

**Intent:**
Add a "Bug Scanner" tab as the first tab in the dashboard. The human sees discovered bugs,
checks which to fix, saves their selection to `bugs.json`, and gets instructions for
running `scan.py --run-selected`.

**Expected Outcomes:**
- Dashboard has 4 tabs: "🐛 Bug Scanner", "📈 Agent Performance", "📝 Prompt Diffs", "🔍 Needs Your Review"
- Tab 0 shows a `st.dataframe` of all bugs with columns: source, file, line, error_type, description, status
- Each `discovered` bug has a `st.checkbox` keyed by `bug_id`
- "Mark Selected" button writes checked bugs to `status=selected` in `bugs.json` + `st.rerun()`
- Count badges: `X discovered · Y selected · Z running · W done`
- If `bugs.json` missing: `st.info` with scan command
- `running` bugs show spinner icon; `done` bugs show link badge with `task_id`

**Todo List:**
1. Add to `dashboard/app.py` paths section:
   `_BUGS_PATH = os.path.join(_DASHBOARD_DIR, "bugs.json")`
2. Add helper functions:
   - `_load_bugs() -> list[dict]` — reads `bugs.json`, returns `[]` if missing
   - `_save_bugs(bugs: list[dict]) -> None` — writes `bugs.json` with indent=2
3. Change `st.tabs(["📈...", "📝...", "🔍..."])` to
   `st.tabs(["🐛 Bug Scanner", "📈 Agent Performance", "📝 Prompt Diffs", "🔍 Needs Your Review"])`
   and unpack as `tab0, tab1, tab2, tab3`
4. Implement `with tab0:` block:
   - Load bugs via `_load_bugs()`
   - If empty: show `st.info` with scan command
   - Else: show `st.dataframe` summary
   - Count badges using `st.metric` columns
   - For each `discovered` bug: `st.checkbox(bug["description"][:80], key=f"sel_{bug['bug_id']}")`
   - "Mark Selected" button: iterate bugs, if checkbox is True set `status="selected"`, save, `st.rerun()`
   - `st.code("python orchestration/scan.py --source sample_repo/flaskbb/flaskbb --run-selected")`
5. Run `py_compile` check after

**Relevant Context:**
- Do NOT use nested expanders — Streamlit forbids it (already learned)
- `st.rerun()` forces a page refresh after JSON state changes
- `st.checkbox` key must be unique per bug: `f"sel_{bug['bug_id']}"`
- `_load_tasks()` pattern at lines 55-67 of `dashboard/app.py` — follow for `_load_bugs()`

---

## P1-ST4 — `orchestration/apply_fixes.py` CLI

**Status:** [ ] pending

**Intent:**
Read all `status=approved` tasks from `dashboard/tasks/`, apply each `code_diff` to the
repo using `git apply`, re-run flake8 on the patched files to verify, write
`verification_result` back to the task JSON, and update task status.

**Expected Outcomes:**
- `python orchestration/apply_fixes.py --repo sample_repo/flaskbb` processes all approved tasks
- `python orchestration/apply_fixes.py --repo sample_repo/flaskbb --dry-run` shows what would happen without changing files
- For each approved task:
  - `git apply --check` first (dry-run gate) — if fails, writes `applied=false` and skips
  - `git apply` on success
  - runs `python -m flake8 <scoped_files>` on patched files
  - writes `verification_result` to task JSON
  - sets `status = "verified_fixed"` or `"verified_failed"`
- Prints summary at end

**Todo List:**
1. Create `orchestration/apply_fixes.py`:
   - `argparse`: `--repo` (required), `--dry-run` (flag),
     `--tasks-dir` (default: `dashboard/tasks`)
   - `load_approved_tasks(tasks_dir) -> list[dict]` — reads all JSONs,
     filters `status == "approved"` AND `code_diff is not None`
   - `apply_diff(task, repo_path, dry_run) -> dict`:
     - writes `task["code_diff"]` to `tempfile.NamedTemporaryFile(suffix=".patch", delete=False)`
     - runs `git apply --check <patch_file>` with `cwd=repo_path`
     - if `--check` fails: returns `{"applied": False, "reason": stderr[:200]}`
     - if dry_run: returns `{"applied": False, "reason": "dry-run mode"}`
     - else: runs `git apply <patch_file>`, returns `{"applied": True, "git_apply_output": stdout}`
   - `run_verification_flake8(scoped_files, repo_path) -> dict`:
     - builds absolute paths: `[os.path.join(repo_path, f) for f in scoped_files]`
     - runs `[sys.executable, "-m", "flake8"] + abs_paths + ["--max-line-length=120"]`
     - returns `{"flake8_passed": output.strip() == "", "flake8_output": output[:500]}`
   - `save_task(task, tasks_dir)` — writes updated task to `{tasks_dir}/{task_id}.json`
   - `main()`:
     - loads approved tasks
     - for each: apply_diff → run_verification_flake8 → merge result into task →
       set `status = "verified_fixed"` or `"verified_failed"` → save_task
     - print summary: N applied, M verified fixed, K failed
2. Update `schemas/task_schema.json`:
   - Add `"verified_fixed"` and `"verified_failed"` to `status` enum
   - Add optional `verification_result` field (object or null) with:
     `applied` (bool), `git_apply_output` (string), `flake8_passed` (bool),
     `flake8_output` (string), `verified_at` (ISO string)
3. Create `orchestration/test_apply_fixes.py`:
   - `load_approved_tasks` returns only `status=approved` tasks with non-null diff
   - `apply_diff` with `dry_run=True` never calls `git apply` (mock subprocess)
   - `apply_diff` calls `git apply --check` first, then `git apply` on success
   - `run_verification_flake8` returns `passed=True` when stdout is empty
   - `run_verification_flake8` returns `passed=False` when stdout has errors

**Relevant Context:**
- `git apply` must run with `cwd=repo_path` (inside `sample_repo/flaskbb/`)
- `tempfile.NamedTemporaryFile` — use `delete=False` then clean up manually after
- `scoped_files` in task JSON are relative (e.g. `flaskbb/auth/views.py`) — prepend `repo_path`
- `sys.executable` for flake8 — same env guarantee
- Existing `subprocess` pattern: `agents/testing_agent/agents/testing_agent/testing_agent.py`

---

## P1-ST5 — Dashboard Tab 3 Verification Badge + Functional Approve Button

**Status:** [ ] pending

**Intent:**
Make the Approve/Reject buttons in Tab 3 actually write JSON. Show a verification badge
on tasks that have been processed by `apply_fixes.py`. Extend Tab 3 filter to include
`approved` and `verified_failed` tasks.

**Expected Outcomes:**
- Approve button writes `status=approved` to task JSON + `st.rerun()`
- Reject button writes `status=rejected` to task JSON + `st.rerun()`
- Tasks with `verification_result` show:
  - `st.success("✅ Verified fixed — flake8 clean")` if `flake8_passed=true`
  - `st.error("❌ Verification failed")` + `st.code(output)` if `flake8_passed=false`
  - `st.warning("⚠ Patch not applied: <reason>")` if `applied=false`
- Tasks that are `approved` but not yet processed show `st.info("⏳ Awaiting apply_fixes.py")`
- Tab 3 filter includes: `awaiting_human_approval`, `blocked`, `needs_retry`, `approved`, `verified_failed`

**Todo List:**
1. Replace disabled Approve/Reject buttons in Tab 3:
   ```python
   if st.button("✅ Approve", key=f"approve_{task_id}"):
       task_path = os.path.join(_TASKS_DIR, f"{task_id}.json")
       with open(task_path, "r") as f:
           t = json.load(f)
       t["status"] = "approved"
       with open(task_path, "w") as f:
           json.dump(t, f, indent=2)
       st.rerun()
   ```
   Same pattern for Reject → `status = "rejected"`
2. Remove the disabled caption below buttons
3. After buttons, add verification badge block checking `task.get("verification_result")`
4. Update Tab 3 status filter to include `"approved"` and `"verified_failed"`
5. Run `py_compile` check after

**Relevant Context:**
- `_TASKS_DIR` constant already defined in `dashboard/app.py`
- `st.rerun()` — Streamlit ≥1.27 — already used in ST3
- Do NOT use nested expanders

---

## P1-ST6 — Integration Tests + Documentation

**Status:** [ ] pending

**Intent:**
End-to-end integration test for the full Phase 1 flow (all subprocess mocked), and
updated demo script.

**Expected Outcomes:**
- `orchestration/test_phase1.py` — 6+ tests, all passing, subprocess fully mocked
- `docs/demo-script.md` Phase 1 section added
- All 162 existing tests still pass
- All new files pass `py_compile`

**Todo List:**
1. Create `orchestration/test_phase1.py`:
   - `scan_repo` returns structured list from mocked flake8 output
   - `cmd_scan` writes correct `bugs.json` (mock `scan_repo`)
   - `cmd_run_selected` calls `run_pipeline` for each `status=selected` bug (mock `run_pipeline`)
   - `cmd_run_selected` skips bugs with `status != selected`
   - `apply_diff` dry_run never calls subprocess
   - Full flow: scan → select → run → approve → apply → verified
2. Add Phase 1 section to `docs/demo-script.md`
3. Run `python -m pytest agents/ orchestration/ -q --tb=short`
4. Confirm `py_compile` on all new/modified files

---

# Phase 2 Sub-Tasks

---

## P2-ST1 — Scaffold Agent

**Status:** [ ] pending

**Intent:**
Create `agents/scaffold_agent/scaffold_agent.py` — a new LLM-powered agent that takes a
plain-English app description and returns an ordered list of file batches. Each batch
contains ≤5 files to generate. This is the "planning brain" for Phase 2 — it decides
the full file structure before any code is written.

**Expected Outcomes:**
- `plan_batches(description, project_name) -> list[list[str]]` — calls LLM, returns
  ordered list of file groups e.g. `[["app.py","models.py","config.py","requirements.txt","tests/test_app.py"], ["templates/base.html", ...]]`
- Each batch has ≤5 files
- Last batch always includes at least one test file
- First file in batch 1 is always the app entry point (e.g. `app.py`)
- Batch ordering respects dependency order (models before routes, routes before templates)
- Handles LLM returning > 5 files in a batch (truncates like Architect Agent does)
- Returns a `BuildPlan` object (dict): `{"project_name": ..., "description": ..., "batches": [[...], ...]}`
- Test file passes

**Todo List:**
1. Create `agents/scaffold_agent/__init__.py` (empty)
2. Create `agents/scaffold_agent/system_prompt.md` — instructs LLM to:
   - Return JSON with `batches` key: list of lists of relative file paths
   - Respect dependency order
   - Put test file in last batch
   - Keep each batch ≤5 files
   - Use only Flask/Jinja2/SQLite — no external databases
3. Create `agents/scaffold_agent/scaffold_agent.py`:
   - `_call_watsonx(prompt) -> str` — same pattern as `agents/architect_agent/architect_agent.py` lines 85-137,
     reads `BOB_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_MODEL_ID` at call time
   - `_parse_batch_output(raw: str) -> list[list[str]]` — strips markdown fences,
     parses JSON `{"batches": [[...]]}`, validates each batch ≤5 files,
     truncates over-sized batches, raises ValueError on bad structure
   - `plan_batches(description: str, project_name: str) -> dict` — builds prompt,
     calls LLM, parses, returns `BuildPlan` dict
   - Stub fallback: if no API key, return a hardcoded 2-batch plan for a minimal Flask app
4. Create `agents/scaffold_agent/test_scaffold_agent.py`:
   - `_parse_batch_output` parses valid JSON correctly
   - `_parse_batch_output` truncates batches > 5 files
   - `_parse_batch_output` raises ValueError on missing `batches` key
   - `plan_batches` uses stub fallback when no API key
   - Stub fallback returns valid BuildPlan structure

**Relevant Context:**
- Follow `agents/architect_agent/architect_agent.py` pattern exactly for `_call_watsonx`
- `_ensure_flake8` pattern from P1-ST1 for the stub fallback
- The BuildPlan dict feeds directly into `orchestration/build.py`

---

## P2-ST2 — `orchestration/build.py` CLI

**Status:** [ ] pending

**Intent:**
Create the CLI that orchestrates a full from-scratch build. It calls the Scaffold Agent
to get a batch plan, then runs `run_pipeline()` for each batch, auto-applies the resulting
diff to disk, and tracks progress in `build_manifest.json`.

**Expected Outcomes:**
- `python orchestration/build.py --description "Build a Flask todo app..." --name todo_app`
  generates a complete project in `generated_projects/todo_app/`
- Progress is saved to `generated_projects/todo_app/build_manifest.json` after each batch
  so a crashed run can be resumed
- Each batch's diff is applied to disk immediately (no human approval between batches)
- If a batch fails (review blocks, max retries hit), the manifest records `status=failed`
  for that batch and build continues to next batch (best-effort)
- Final summary printed: N batches attempted, M succeeded, K failed
- Dashboard Tab 4 can read `build_manifest.json` to show progress

**Todo List:**
1. Create `generated_projects/.gitkeep` (empty, just to create the directory)
2. Create `orchestration/build.py`:
   - `argparse`: `--description` (required), `--name` (required),
     `--output-dir` (default: `generated_projects`), `--resume` (flag)
   - `init_manifest(project_name, description, batches) -> dict` — creates `BuildPlan` manifest dict
   - `save_manifest(manifest, project_dir)` — writes `build_manifest.json`
   - `load_manifest(project_dir) -> dict | None` — reads manifest, returns None if missing
   - `apply_diff_to_disk(code_diff: str, project_dir: str)` — writes diff to temp file,
     runs `git apply` if project_dir is a git repo, otherwise writes files directly using
     `pathlib.Path.write_text()` by parsing the diff manually for `+++ b/` lines
     (Phase 2 project is a new dir, not necessarily a git repo — need fallback)
   - `run_batch(batch_files, batch_index, description, project_name, project_dir) -> dict`:
     - Builds a `feature_request` describing this batch:
       `f"Generate batch {batch_index}: create files {', '.join(batch_files)} for {project_name} — {description}"`
     - Calls `run_pipeline(feature_request=..., repo_path=project_dir, task_id=...)`
     - If result status is `approved` or `awaiting_human_approval`:
       calls `apply_diff_to_disk(result["code_diff"], project_dir)`
       returns `{"status": "done", "task_id": ...}`
     - If `blocked`: returns `{"status": "failed", "reason": ...}`
   - `main()`:
     - if `--resume`: load existing manifest, skip done batches
     - else: call `plan_batches()`, init manifest, create `project_dir`
     - for each pending batch: call `run_batch()`, update manifest, save
     - print final summary
3. Update `schemas/task_schema.json`:
   - Add optional `batch_index` (integer or null)
   - Add optional `total_batches` (integer or null)
   - Add optional `project_name` (string or null)
4. Create `orchestration/test_build.py`:
   - `init_manifest` produces correct structure
   - `save_manifest`/`load_manifest` round-trip
   - `run_batch` calls `run_pipeline` with correct `feature_request` (mock pipeline)
   - `run_batch` calls `apply_diff_to_disk` on approved result (mock apply)
   - `run_batch` skips `apply_diff_to_disk` on blocked result
   - `main()` with `--resume` skips batches already marked `done`

**Relevant Context:**
- `run_pipeline` in `orchestration/pipeline.py` — same import pattern as `scan.py`
- `plan_batches` from `agents/scaffold_agent/scaffold_agent.py`
- `apply_diff_to_disk` needs a non-git fallback because `generated_projects/todo_app/`
  starts as an empty directory with no git history
- The Architect Agent (`run_architect_agent`) will receive `repo_path=project_dir` —
  since the project starts empty, `get_repo_file_listing()` will return an empty list;
  this is fine — the system_prompt.md already says "LLM will scope without it"

---

## P2-ST3 — Architect Agent: Batch-Aware Mode

**Status:** [ ] pending

**Intent:**
The existing Architect Agent validates that scoped files exist on disk (Guard 7c in runner.py).
For Phase 2, all files in a batch are NEW FILES — they don't exist yet. The Architect Agent
must be told this is a "new project batch" so it marks every scoped file as `NEW FILE:` in
the plan. Currently the 5-file cap is handled — this sub-task ensures the guard chain in
`runner.py` doesn't reject new-project diffs.

**Expected Outcomes:**
- When `run_pipeline()` is called with `repo_path=project_dir` (a new/empty dir) and
  `scoped_files` contains only new paths, Guard 7c correctly treats all of them as NEW FILE
  paths and does not block
- The Architect Agent's `run_architect_agent()` includes `NEW FILE:` prefix for every file
  in the plan when the repo_path is a new/empty directory
- No changes to the guard logic — the fix is in how the prompt instructs the LLM to write
  the plan
- All existing runner tests still pass

**Todo List:**
1. In `agents/architect_agent/architect_agent.py`, detect when `repo_path` points to an
   empty or non-existent directory:
   - In `run_architect_agent()`, after building `listing`, check `if not listing:`
   - If empty listing, append a note to the prompt: `"This is a brand-new project directory.
     Every file in scoped_files is a new file — prefix each with 'NEW FILE:' in the plan."`
2. In `agents/architect_agent/system_prompt.md`, add a section explaining the NEW FILE
   convention and when to use it (already exists for existing repo, extend for new project)
3. Verify with a unit test in `agents/architect_agent/test_architect_agent.py`:
   - When `repo_path` is empty dir, prompt includes "brand-new project" instruction
   - (Mock the LLM call)

**Relevant Context:**
- `run_architect_agent` lines 304-360 in `agents/architect_agent/architect_agent.py`
- Guard 7b (runner.py lines 231-250): NEW FILE must NOT exist on disk — this is satisfied
  for a new empty project dir
- Guard 7c (runner.py lines 252-274): existing files must exist on disk — only fires for
  non-NEW-FILE paths; if all files are NEW FILE, this guard never triggers

---

## P2-ST4 — Dashboard Tab 4 "Generated Projects"

**Status:** [ ] pending

**Intent:**
Add a "Generated Projects" tab to the dashboard showing all projects in `generated_projects/`,
their batch progress, and per-batch status.

**Expected Outcomes:**
- Dashboard has 5 tabs: "🐛 Bug Scanner", "📈 Agent Performance", "📝 Prompt Diffs",
  "🔍 Needs Your Review", "🏗️ Generated Projects"
- Tab 4 lists all subdirectories of `generated_projects/` that contain a `build_manifest.json`
- For each project: shows name, description, overall status, and a progress bar
- Expandable section per project shows batch-level table: batch #, files, status, task_id
- If no projects yet: `st.info` with build command

**Todo List:**
1. Add `_GENERATED_PROJECTS_DIR` path constant to `dashboard/app.py`
2. Add `_load_build_manifests() -> list[dict]` helper — scans `generated_projects/` for
   `build_manifest.json` files, returns list
3. Extend `st.tabs()` from 4 to 5 tabs, add "🏗️ Generated Projects" as last tab
4. Implement `with tab4:` block:
   - Load manifests
   - If none: `st.info("No projects yet. Run: python orchestration/build.py --description ... --name ...")`
   - For each manifest: `st.subheader(project_name)`, `st.caption(description)`
   - Progress bar: `done_count / total_batches`
   - Status badge: `st.success("Complete")` / `st.warning("In progress")` / `st.error("Failed")`
   - `st.dataframe` of batch rows: index, files, status
5. Run `py_compile` check after

**Relevant Context:**
- Do NOT use nested expanders
- `_load_tasks()` pattern in `dashboard/app.py` — follow same for `_load_build_manifests()`
- `st.progress(value)` takes float 0.0–1.0

---

## P2-ST5 — Integration Tests + Documentation

**Status:** [ ] pending

**Intent:**
End-to-end integration test for Phase 2 flow (all LLM and subprocess mocked), and
demo script update.

**Expected Outcomes:**
- `orchestration/test_build.py` — all tests pass (written in P2-ST2)
- `agents/scaffold_agent/test_scaffold_agent.py` — all tests pass
- `agents/architect_agent/test_architect_agent.py` — all existing tests still pass + new batch-aware test
- `docs/demo-script.md` Phase 2 section added
- Full suite: all tests pass

**Todo List:**
1. Run `python -m pytest agents/ orchestration/ -q --tb=short` — confirm no regressions
2. Add Phase 2 section to `docs/demo-script.md`:
   ```
   Step 1: python orchestration/build.py --description "Build a Flask todo app with login, CRUD tasks, and SQLite" --name todo_app
   Step 2: Open dashboard Tab 4 to watch batch progress
   Step 3: python orchestration/build.py --name todo_app --resume  (if interrupted)
   Step 4: View generated_projects/todo_app/ for final output
   ```
3. Run `py_compile` on all new/modified files

---

## Dependency Order

```
Phase 1:
  P1-ST1 (Bug Discovery Agent)
      → P1-ST2 (scan.py)
          → P1-ST3 (Dashboard Tab 0)
          → P1-ST6 (Integration Tests)
      → P1-ST4 (apply_fixes.py)
          → P1-ST5 (Dashboard Tab 3 Badge)
          → P1-ST6

Phase 2:
  P2-ST1 (Scaffold Agent)
      → P2-ST2 (build.py)
          → P2-ST4 (Dashboard Tab 4)
          → P2-ST5 (Integration Tests)
  P2-ST3 (Architect batch-aware)
      → P2-ST2
```

## Implementation Order (strict sequence)

1. P1-ST1 — Bug Discovery Agent
2. P1-ST2 — scan.py CLI
3. P1-ST3 — Dashboard Tab 0
4. P1-ST4 — apply_fixes.py CLI
5. P1-ST5 — Dashboard Tab 3 Badge
6. P1-ST6 — Phase 1 Integration Tests
7. P2-ST1 — Scaffold Agent
8. P2-ST3 — Architect batch-aware mode
9. P2-ST2 — build.py CLI
10. P2-ST4 — Dashboard Tab 4
11. P2-ST5 — Phase 2 Integration Tests
