# Phase 1 — Auto-Fix Broken Repo Plan

## Top-Level Overview

**Goal:** Extend the AI Dev Team pipeline so it can automatically scan a repo for flake8
errors, present them to a human in the dashboard for selection, run the existing pipeline
on each selected bug, show the resulting diff for human approval, and then apply the
approved diffs to disk and verify the fix by re-running flake8 on the affected file.

**Scope:**
- NEW: `agents/bug_discovery_agent/bug_discovery_agent.py` — scans repo with flake8, returns structured bug list
- NEW: `orchestration/scan.py` — CLI: scan mode writes `bugs.json`, run-selected mode calls pipeline per bug
- MODIFIED: `dashboard/app.py` — new Tab 0 "Bug Scanner" for human selection + Tab 3 functional approve + verification badge
- MODIFIED: `schemas/task_schema.json` — add `verified_fixed`/`verified_failed` statuses + `verification_result` field
- NEW: `orchestration/apply_fixes.py` — applies approved diffs, re-runs flake8 for verification

**Approach:**
1. Bug Discovery Agent is pure Python, no LLM — runs `python -m flake8` on the repo source directory from project root.
2. Results are persisted as `dashboard/bugs.json` — the single source of truth between scan, dashboard, and apply.
3. The existing `run_pipeline()` is reused as-is — flake8 error descriptions become feature requests for the PM Agent.
4. The dashboard's Tab 3 "Needs Your Review" already handles diff display — extend it with functional buttons + badge.
5. `apply_fixes.py` reads `dashboard/tasks/*.json`, finds approved tasks, applies diffs via `git apply`, re-runs flake8 on affected files to verify.

**Scanning scope (confirmed):**
- flake8 only: `python -m flake8 sample_repo/flaskbb/flaskbb/` from project root
- pytest scanning is deferred to Phase 2
- flake8 installed in our own Python env (install if missing)

**Non-goals:**
- Running FlaskBB's own pytest suite (Phase 2)
- Auto-apply without human approval
- Fixing compile-time import errors that prevent the repo from loading
- Fixing database migration issues
- Generating a full app from scratch (Phase 2)

---

## Data Flow

```
scan.py --repo sample_repo/flaskbb
    → BugDiscoveryAgent.scan()
    → dashboard/bugs.json          (list of discovered bugs, status=discovered)

dashboard/app.py (Tab 0 "Bug Scanner")
    → human sees bug list, selects which to fix
    → writes status=selected to dashboard/bugs.json

orchestration/scan.py --run-selected
    → for each selected bug: run_pipeline(request=bug.description)
    → pipeline writes dashboard/tasks/{task_id}.json

dashboard/app.py (Tab 3 "Needs Your Review")
    → human sees diff, clicks Approve toggle
    → writes status=approved to dashboard/tasks/{task_id}.json

orchestration/apply_fixes.py --repo sample_repo/flaskbb
    → reads dashboard/tasks/*.json where status=approved
    → git apply the code_diff to disk
    → re-runs pytest on affected test file
    → writes verification_result to dashboard/tasks/{task_id}.json

dashboard/app.py (Tab 3)
    → shows green/red verification badge on each approved task
```

---

## `dashboard/bugs.json` Schema

Each entry in the array:
```json
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
  "status": "discovered" | "selected" | "running" | "done" | "skipped",
  "task_id": null | "task_abc123"
}
```

---

## Sub-Tasks

---

### Sub-Task 1 — Bug Discovery Agent

**Status:** [ ] pending

**Intent:**
Create `agents/bug_discovery_agent/bug_discovery_agent.py` — a pure-Python (no LLM) agent
that scans a repo by running `python -m flake8` from the project root and returns a structured
list of bug dicts. This is the foundation everything else depends on.

**Expected Outcomes:**
- `scan_flake8(source_dir: str) -> list[dict]` runs flake8 and parses its output into bug dicts
- Each bug dict matches the `bugs.json` schema above
- `description_from_flake8(file, line, code, message) -> str` returns plain English suitable for `run_pipeline()`
- `scan_repo(source_dir: str) -> list[dict]` is the main entry point — calls `scan_flake8`, assigns sequential `bug_id`s
- `save_bugs(bugs, path)` and `load_bugs(path)` handle JSON persistence
- Agent handles: flake8 not installed (auto-installs via pip), zero errors found, malformed output lines
- Test file `agents/bug_discovery_agent/test_bug_discovery_agent.py` passes all tests

**Todo List:**
1. Create `agents/bug_discovery_agent/__init__.py` (empty)
2. Create `agents/bug_discovery_agent/bug_discovery_agent.py` with:
   - `_ensure_flake8()` — checks `python -m flake8 --version`; if ImportError/not found, runs
     `pip install flake8` via subprocess
   - `scan_flake8(source_dir: str) -> list[dict]` — runs:
     `python -m flake8 <source_dir> --max-line-length=120 --format=default`
     captures stdout, parses each line with regex:
     `^(.+):(\d+):(\d+):\s+([A-Z]\d+)\s+(.+)$` → file, line, col, code, message
     skips lines that don't match (header/summary lines)
   - `description_from_flake8(file, line, code, message) -> str`
     — returns e.g. `"Fix flake8 error F401 in flaskbb/auth/views.py line 3 — 'os' imported but unused"`
   - `scan_repo(source_dir: str) -> list[dict]` — calls `scan_flake8`, assigns `bug_id` as
     `f"bug_{i+1:03d}"`, sets `status="discovered"`, `task_id=None`
   - `save_bugs(bugs: list[dict], output_path: str) -> None` — writes JSON
   - `load_bugs(path: str) -> list[dict]` — reads JSON, returns `[]` if missing
3. Create `agents/bug_discovery_agent/test_bug_discovery_agent.py` with tests for:
   - `scan_flake8` returns `[]` when subprocess output is empty (mock subprocess)
   - `scan_flake8` parses a known flake8 output line correctly (mock subprocess)
   - `scan_flake8` skips malformed lines without crashing
   - `description_from_flake8` produces non-empty plain English string
   - `scan_repo` assigns sequential `bug_id`s starting from `bug_001`
   - `scan_repo` sets `status="discovered"` and `task_id=None` on all entries
   - `save_bugs` / `load_bugs` round-trip preserves all fields

**Relevant Context:**
- `subprocess` used in `agents/testing_agent/agents/testing_agent/testing_agent.py` — follow same pattern
- Run as: `subprocess.run([sys.executable, "-m", "flake8", source_dir, ...], capture_output=True, text=True)`
  — using `sys.executable` ensures same Python env, avoids PATH issues
- flake8 default output format: `filename:line:col: CODE message` (one error per line)
- `source_dir` will be `sample_repo/flaskbb/flaskbb/` (the Python source, not tests)

---

### Sub-Task 2 — `orchestration/scan.py` CLI

**Status:** [ ] pending

**Intent:**
Create a CLI entry point that runs the Bug Discovery Agent and either saves results to
`dashboard/bugs.json` (scan mode) or runs `run_pipeline()` for each selected bug (run mode).

**Expected Outcomes:**
- `python orchestration/scan.py --source sample_repo/flaskbb/flaskbb` — scans and writes `dashboard/bugs.json`, prints summary
- `python orchestration/scan.py --source sample_repo/flaskbb/flaskbb --run-selected` — reads bugs, finds `status==selected`, calls `run_pipeline()` for each, writes `task_id` back
- Handles: `bugs.json` missing, no bugs selected, flake8 finds zero errors
- Summary print:
  ```
  Found 5 flake8 error(s) in sample_repo/flaskbb/flaskbb/
    [flake8] flaskbb/auth/views.py:3  — F401 'os' imported but unused
    [flake8] flaskbb/auth/forms.py:12 — E501 line too long (123 > 120 chars)
  Saved to dashboard/bugs.json
  Open dashboard to select bugs: python -m streamlit run dashboard/app.py
  ```

**Todo List:**
1. Create `orchestration/scan.py` with:
   - `argparse` CLI: `--source` (required, path to scan), `--run-selected` (flag),
     `--bugs-file` (default: `dashboard/bugs.json`)
   - `cmd_scan(source_dir, bugs_path)` — calls `scan_repo(source_dir)`, saves to `bugs_path`,
     prints summary, tells human to open dashboard to select
   - `cmd_run_selected(source_dir, bugs_path)` — loads bugs, filters `status == "selected"`,
     for each bug: sets `status = "running"`, saves, calls
     `run_pipeline(feature_request=bug["description"], task_id=f"fix_{bug['bug_id']}")`,
     updates `task_id` and sets `status = "done"`, saves after each bug so partial progress is not lost
   - `main()` dispatches to `cmd_scan` or `cmd_run_selected` based on `--run-selected` flag

**Relevant Context:**
- `run_pipeline()` in `orchestration/pipeline.py` — import directly
- `scan_repo()`, `save_bugs()`, `load_bugs()` in `agents/bug_discovery_agent/bug_discovery_agent.py`
- `dashboard/bugs.json` — scan.py owns writing it; dashboard/app.py reads it
- No separate test file for this CLI — covered in Sub-Task 6 integration tests

---

### Sub-Task 3 — Dashboard Tab 0 "Bug Scanner"

**Status:** [ ] pending

**Intent:**
Add a new first tab to the dashboard where the human can:
1. See all discovered bugs from `dashboard/bugs.json`
2. Select which ones to fix (checkboxes → writes `status=selected` to `bugs.json`)
3. See which ones are already running/done and click through to their task

This makes the human-in-the-loop selection step explicit and visible without needing a CLI.

**Expected Outcomes:**
- Dashboard now has 4 tabs: "🐛 Bug Scanner", "📈 Agent Performance", "📝 Prompt Diffs", "🔍 Needs Your Review"
- Tab 0 shows a table of bugs (source, file, line, description, status)
- Each `discovered` bug has a checkbox next to it
- A "▶ Run Selected Fixes" button calls `scan.py --run-selected` via `subprocess` or writes
  `status=selected` to `bugs.json` so the human can then manually run `scan.py --run-selected`
- `running` bugs show a spinner / "in progress" badge
- `done` bugs show a link to their task in Tab 3
- `skipped` bugs are greyed out
- If `bugs.json` doesn't exist, shows instructions to run `python orchestration/scan.py --repo <path>`

**Todo List:**
1. Add `_BUGS_PATH = os.path.join(_DASHBOARD_DIR, "bugs.json")` to `dashboard/app.py` paths section
2. Add `_load_bugs()` helper function (reads `bugs.json`, returns `[]` if missing)
3. Add `_save_bugs(bugs)` helper function (writes `bugs.json`)
4. Change the `st.tabs()` call from 3 tabs to 4 tabs, inserting "🐛 Bug Scanner" as the first tab
5. Implement Tab 0 content:
   - If no bugs file: `st.info("Run: python orchestration/scan.py --repo sample_repo/flaskbb")`
   - If bugs exist: show a `st.dataframe` summary row per bug with columns:
     source, file:line, error_type, status
   - Below dataframe: for each `discovered` bug, show a `st.checkbox` keyed by `bug_id`
   - "Mark Selected" button: updates checked bugs to `status=selected` and saves `bugs.json`
   - "Run Selected Fixes" button: writes selected status, then shows instructions to run
     `python orchestration/scan.py --repo <repo> --run-selected` in a `st.code` block
     (dashboard does not exec subprocesses — keep it read/write JSON only)
   - Show counts: `X discovered, Y selected, Z running, W done`
6. Keep `dashboard/app.py` syntax valid — run `py_compile` check after

**Relevant Context:**
- `_load_tasks()` pattern in `dashboard/app.py` lines 55–67 — follow same pattern for `_load_bugs()`
- Streamlit `st.dataframe` renders a clean table from a list of dicts
- Do NOT use nested expanders (already learned this lesson — Streamlit forbids it)
- `st.checkbox` state is session-scoped — use `key=f"select_{bug['bug_id']}"` to avoid key collisions

---

### Sub-Task 4 — `orchestration/apply_fixes.py` CLI

**Status:** [ ] pending

**Intent:**
Create the CLI that reads all `approved` tasks from `dashboard/tasks/*.json`, applies each
`code_diff` to the repo using `subprocess` + `git apply`, re-runs pytest on the affected
test files, and writes a `verification_result` back to the task JSON. This is the final step
in the loop — it closes the human-approval cycle and verifies the fix actually worked.

**Expected Outcomes:**
- `python orchestration/apply_fixes.py --repo sample_repo/flaskbb` reads all task JSONs
  where `status == "approved"` and `code_diff` is not null
- For each approved task:
  - Writes the diff to a temp file, runs `git apply --check` first (dry-run) — if it fails,
    marks task as `verification_result = {"applied": false, "reason": "patch rejected by git"}`
    and skips
  - If check passes, runs `git apply` for real
  - Derives which test files to run from `scoped_files` (e.g. `flaskbb/auth/views.py` →
    look for `tests/unit/auth/test_views.py` or any test file mentioning the scoped file)
  - Runs `pytest <derived_test_paths> --tb=short -q` with `cwd=repo_path`
  - Writes `verification_result` back to the task JSON:
    ```json
    {
      "applied": true,
      "git_apply_output": "...",
      "pytest_passed": true,
      "pytest_output": "3 passed in 0.4s",
      "verified_at": "<ISO timestamp>"
    }
    ```
  - Updates task `status` to `"verified_fixed"` if pytest passed, `"verified_failed"` if pytest
    failed (new status values — add to schema)
- Prints a summary after all tasks are processed
- Handles: no approved tasks, git apply conflict, pytest not found in repo venv

**Todo List:**
1. Create `orchestration/apply_fixes.py` with:
   - `argparse` CLI: `--repo` (required), `--dry-run` (flag, skips git apply but shows what would happen)
   - `load_approved_tasks(tasks_dir) -> list[dict]` — reads all JSONs, filters `status == "approved"`
     and `code_diff is not None`
   - `derive_test_paths(scoped_files: list[str], repo_path: str) -> list[str]` — for each scoped file,
     check if a corresponding test file exists under `tests/unit/` by mirroring the path structure
     (e.g. `flaskbb/auth/views.py` → `tests/unit/auth/test_views.py`); fall back to `tests/` if no
     match found
   - `apply_diff(task: dict, repo_path: str, dry_run: bool) -> dict` — runs `git apply --check`
     then `git apply`, returns result dict
   - `run_verification_pytest(test_paths: list[str], repo_path: str) -> dict` — runs pytest,
     returns `{"passed": bool, "output": str}`
   - `save_task(task: dict, tasks_dir: str) -> None` — writes updated task back to its JSON file
   - `main()` — orchestrates the above, prints summary
2. Update `schemas/task_schema.json`:
   - Add `"verified_fixed"` and `"verified_failed"` to the `status` enum
   - Add optional `"verification_result"` field (object or null) with sub-fields:
     `applied` (bool), `git_apply_output` (string), `pytest_passed` (bool),
     `pytest_output` (string), `verified_at` (string)
3. Create `orchestration/test_apply_fixes.py` with tests for:
   - `derive_test_paths` finds correct test file from scoped file path
   - `derive_test_paths` falls back to `tests/` when no match
   - `load_approved_tasks` only returns tasks with `status == "approved"` and non-null `code_diff`
   - `apply_diff` with `dry_run=True` does not call `git apply` (mock subprocess)
   - `run_verification_pytest` parses passed/failed correctly (mock subprocess)

**Relevant Context:**
- `subprocess` pattern from `agents/testing_agent/agents/testing_agent/testing_agent.py`
- `git apply` requires the diff to be written to a temp file — use `tempfile.NamedTemporaryFile`
- FlaskBB's test structure: `tests/unit/{module}/test_{file}.py` — sub-task 1 already explored this
- `scoped_files` in the task JSON use paths relative to the repo root (e.g. `flaskbb/auth/views.py`)
- The `--dry-run` flag is critical for safe demos — always show it in documentation

---

### Sub-Task 5 — Dashboard Tab 3 Verification Badge

**Status:** [ ] pending

**Intent:**
Extend Tab 3 "Needs Your Review" in the dashboard to:
1. Enable the Approve button (writes `status=approved` to the task JSON on disk)
2. Show a green/red verification badge on tasks that have been through `apply_fixes.py`
   (i.e. have a `verification_result` field)

This closes the visual feedback loop — the human can see in one place: diff → approve → verified green.

**Expected Outcomes:**
- Approve button in Tab 3 is now functional — clicking it writes `status=approved` to
  `dashboard/tasks/{task_id}.json` and refreshes the page (via `st.rerun()`)
- Reject button writes `status=rejected` to the task JSON
- Tasks with `verification_result` show a badge:
  - `✅ Verified fixed — 3 passed` (green) if `pytest_passed=true`
  - `❌ Verification failed — see output below` (red) if `pytest_passed=false`
  - The pytest output is shown in a `st.code` block (not inside an expander)
- Tasks that are `approved` but not yet applied show a `⏳ Awaiting apply_fixes.py` label

**Todo List:**
1. Replace the disabled Approve/Reject buttons in Tab 3 with functional ones:
   - Approve: `if st.button("✅ Approve", key=f"approve_{task_id}"):`
     → load task JSON, set `status = "approved"`, save, `st.rerun()`
   - Reject: `if st.button("❌ Reject", key=f"reject_{task_id}"):`
     → load task JSON, set `status = "rejected"`, save, `st.rerun()`
   - Remove the disabled caption below the buttons
2. Add verification badge logic:
   - After the Approve/Reject buttons, check `task.get("verification_result")`
   - If present and `applied=true and pytest_passed=true`: `st.success("✅ Verified fixed — pytest passed")`
   - If present and `applied=true and pytest_passed=false`: `st.error("❌ Verification failed")`
     + `st.code(verification_result["pytest_output"], language="text")`
   - If present and `applied=false`: `st.warning(f"⚠ Patch not applied: {verification_result['reason']}")`
   - If `status == "approved"` and no `verification_result`:
     `st.info("⏳ Approved — run: python orchestration/apply_fixes.py --repo <path>")`
3. Update Tab 3 filter to also show `status == "approved"` and `status == "verified_failed"` tasks
   (not just `awaiting_human_approval`, `blocked`, `needs_retry`)
4. Run `py_compile` check after changes

**Relevant Context:**
- `st.rerun()` is the Streamlit way to force a page refresh after a state change
- Task JSON path: `dashboard/tasks/{task_id}.json` — derive from `task["task_id"]`
- Do not use nested expanders — already fixed in the previous session
- The `_TASKS_DIR` path constant is already defined in `dashboard/app.py`

---

### Sub-Task 6 — End-to-End Test & Documentation

**Status:** [ ] pending

**Intent:**
Write a full integration test that exercises the entire Phase 1 flow with mocked subprocess
calls (no real pytest/flake8 run needed), and update `docs/demo-script.md` with the new
Phase 1 demo steps.

**Expected Outcomes:**
- `orchestration/test_phase1.py` passes — covers:
  1. `scan_repo()` returns structured bugs from mocked pytest + flake8 output
  2. `scan.py` cmd_scan writes correct `bugs.json`
  3. `scan.py` cmd_run_selected calls `run_pipeline()` for each selected bug
  4. `apply_fixes.py` applies diffs and writes verification_result correctly
  5. `derive_test_paths` maps scoped files to test paths correctly
- `docs/demo-script.md` updated with Phase 1 section:
  ```
  Step 1: python orchestration/scan.py --repo sample_repo/flaskbb
  Step 2: Open dashboard, go to Bug Scanner tab, select bugs, click Mark Selected
  Step 3: python orchestration/scan.py --repo sample_repo/flaskbb --run-selected
  Step 4: Dashboard Tab 3 — review diffs, click Approve
  Step 5: python orchestration/apply_fixes.py --repo sample_repo/flaskbb
  Step 6: Dashboard Tab 3 — see green verification badges
  ```
- All existing 162 tests still pass

**Todo List:**
1. Create `orchestration/test_phase1.py` with integration tests (all subprocess mocked)
2. Update `docs/demo-script.md` with Phase 1 section
3. Run full test suite: `python -m pytest agents/ orchestration/ -q --tb=short`
4. Confirm `py_compile` passes on all new files

**Relevant Context:**
- Follow the same mock pattern as `orchestration/test_pipeline.py` — patch at module level
- `conftest.py` at root already exists — add any new ignore patterns if needed
- `docs/demo-script.md` already has Phase 0 demo content — append Phase 1 section

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Bug Discovery Agent is pure Python, no LLM | Deterministic, fast, auditable — no token cost for scanning |
| `bugs.json` is the single source of truth | Dashboard reads it, scan.py writes it, no database needed |
| Dashboard buttons write JSON only, no subprocess | Keeps dashboard stateless/safe — CLI owns the heavy operations |
| `git apply --check` before real apply | Prevents partial application of conflicting diffs |
| `--dry-run` on `apply_fixes.py` | Safe for demos and CI — always verify before modifying files |
| Add `verified_fixed`/`verified_failed` to schema | Keeps the task object as the single audit trail |
| `-p no:xdist` when running pytest programmatically | FlaskBB's `--numprocesses auto` makes output non-deterministic for parsing |

## Files Created / Modified

| File | Action |
|---|---|
| `agents/bug_discovery_agent/__init__.py` | CREATE |
| `agents/bug_discovery_agent/bug_discovery_agent.py` | CREATE |
| `agents/bug_discovery_agent/test_bug_discovery_agent.py` | CREATE |
| `orchestration/scan.py` | CREATE |
| `orchestration/apply_fixes.py` | CREATE |
| `orchestration/test_apply_fixes.py` | CREATE |
| `orchestration/test_phase1.py` | CREATE |
| `dashboard/app.py` | MODIFY — add Tab 0, functional buttons, verification badge |
| `schemas/task_schema.json` | MODIFY — add verified_fixed/verified_failed status + verification_result field |
| `docs/demo-script.md` | MODIFY — add Phase 1 section |
