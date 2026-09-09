# Coding Agent — Project File Structure

This document describes every file in the Coding Agent component, its responsibility, and
how the files relate to each other. It is the reference for any teammate integrating with or
extending the Coding Agent.

---

## Full Directory Layout

```
IBM project/                                  ← repository root
│
├── task_schema.json                          ← FIXED team contract — never modify
├── AGENTS.md                                 ← pipeline overview + status FSM
├── coding-agent-plan.md                      ← implementation plan + decision log
│
├── agents/
│   └── coding_agent/
│       ├── system_prompt.md                  ← LLM operating contract (source of truth for roleDefinition)
│       ├── input_validation.md               ← all 9 validation guards with exact output shapes
│       ├── context_assembly.md               ← file reading, NEW FILE handling, token budget, prompt structure
│       ├── diff_generation.md                ← diff instruction block + 4 post-processing checks
│       ├── task_object_assembly.md           ← field-by-field output rules for all 4 execution paths
│       ├── PROJECT_STRUCTURE.md              ← this file
│       └── fixtures/                         ← JSON test inputs (created in Sub-Task 7)
│           ├── test_first_run.json
│           ├── test_retry_with_findings.json
│           ├── test_retry_cap.json
│           ├── test_missing_plan.json
│           ├── test_scope_violation.json
│           ├── test_empty_scoped_files.json
│           ├── test_new_file_valid.json
│           ├── test_architect_fault_missing_path.json
│           ├── test_architect_fault_new_file_not_scoped.json
│           └── test_architect_fault_new_file_exists.json
│
└── .bob/
    ├── custom_modes.yaml                     ← registers the coding-agent Bob mode
    ├── rules-coding-agent/
    │   └── AGENTS.md                         ← mode-specific rules, auto-injected by Bob
    ├── rules-agent/
    │   └── AGENTS.md                         ← generic agent-mode rules (pre-existing)
    ├── rules-ask/
    │   └── AGENTS.md                         ← ask-mode rules (pre-existing)
    └── rules-plan/
        └── AGENTS.md                         ← plan-mode rules (pre-existing)
```

---

## File Responsibilities

### `task_schema.json`
The fixed JSON Schema (draft-07) defining the `AgentTaskObject` contract shared by all agents.
**Never modified.** All Coding Agent output is validated against this schema by `validate_task()`.

---

### `agents/coding_agent/system_prompt.md`
**Source of truth for the LLM's operating contract.**

Contains the complete step-by-step instructions the LLM follows when acting as the Coding Agent.
The `roleDefinition` field in `custom_modes.yaml` contains a condensed identity string; the full
detail is here. When the two diverge, this file takes precedence — update `custom_modes.yaml`
to match after any edit here.

Sections: mandatory output contract → identity → Step 0 (retry cap) → Step 1 (NEW FILE parsing) → Step 2 (validation) → Step 3 (file reading) → Step 4 (diff production) → Step 5 (diff validation) → Step 6 (output assembly) → history rules → immutable fields → status/routing reference → retry handling.

---

### `agents/coding_agent/input_validation.md`
**Orchestration-layer guard specification.**

Defines the 9 guards the runner executes before invoking the LLM. Guards run in fixed order;
the first failure stops execution. Covers:
- Guards 1–2: routing sanity checks (hard halt, no output produced)
- Guard 3: retry-cap gate (early exit, not a failure)
- Guards 4–5: Coding Agent failures (`success: false`)
- Guard 6: `NEW FILE:` declaration parsing (builds `intended_new_files`)
- Guards 7a/7b/7c: Architect-fault checks (`success: null`, two-entry history pattern)

Also contains path normalisation rules used throughout all guards and context assembly.

---

### `agents/coding_agent/context_assembly.md`
**LLM prompt construction specification.**

Defines the 6-step process for building the user-turn prompt sent to the LLM:
1. Build `intended_new_files` (before any disk access)
2. Normalise all `scoped_files` paths
3. Read existing files via `read_file`; new files get placeholder text
4. Apply token budget (default 100k tokens, configurable via `CODING_AGENT_CONTEXT_BUDGET`); proportional truncation if exceeded
5. Assemble 6 prompt sections in fixed order: task → acceptance criteria → review findings → plan → scoped files → diff instructions
6. Return assembled prompt + assembly metadata

---

### `agents/coding_agent/diff_generation.md`
**Diff instruction block and post-generation validation specification.**

Part 1: The verbatim diff instruction block appended as Section 6 of the LLM prompt — 7 rules
the LLM must follow exactly (scope boundary, format for existing vs new files, no prose).

Part 2: 4 post-processing validation checks run on the raw LLM output before writing to
`code_diff`: empty output check, diff structure check, scope violation check, path traversal
check. Any failure → `code_diff: null`, `blocked`, `manager_agent`, `success: false`.

Part 3: Failure output object assembly template.

Part 4: Retry-cap cross-reference (Guard 3 runs before this step; `code_diff` is preserved, not nulled).

---

### `agents/coding_agent/task_object_assembly.md`
**Output object construction specification.**

Field-by-field rules for assembling the final `AgentTaskObject` across all 4 execution paths:
- Path A (success): diff → `testing_agent`
- Path B (retry-cap): preserve diff → `human`
- Path C (Coding Agent failure): null diff → `manager_agent`
- Path D (Architect-fault): null diff → `manager_agent`, two history entries

Also contains:
- Complete `output_summary` template table with all 15 rows + suffix modifier rules
- Variable substitution reference (N, M, K)
- `validate_task()` gate — mandatory on all 4 paths

---

### `agents/coding_agent/fixtures/`
**Test JSON inputs for isolated Coding Agent testing (created in Sub-Task 7).**

Each file is a complete `AgentTaskObject` representing a specific scenario. Used to verify
the agent produces the correct output without running the full pipeline.

| File | Scenario |
|---|---|
| `test_first_run.json` | Happy path — first run |
| `test_retry_with_findings.json` | Retry with `review_result.findings` populated |
| `test_retry_cap.json` | Retry cap — `retry_count == 2` |
| `test_missing_plan.json` | Guard 4 — `plan` is null |
| `test_scope_violation.json` | Diff scope boundary check |
| `test_empty_scoped_files.json` | Guard 5 — `scoped_files` is `[]` |
| `test_new_file_valid.json` | Valid `NEW FILE:` declaration — allowed new file |
| `test_architect_fault_missing_path.json` | Guard 7c — nonexistent path, no `NEW FILE:` |
| `test_architect_fault_new_file_not_scoped.json` | Guard 7a — `NEW FILE:` not in `scoped_files` |
| `test_architect_fault_new_file_exists.json` | Guard 7b — `NEW FILE:` for existing file |

---

### `.bob/custom_modes.yaml`
**Bob mode registration.**

Registers the `coding-agent` mode (slug: `coding-agent`, name: `Coding Agent`) with:
- `roleDefinition`: condensed identity string pointing to full spec in `system_prompt.md`
- `groups`: `read` (loads `scoped_files` from disk via `read_file`) + `todo` (progress tracking)
- No `edit` or `execute` permissions — the agent writes a diff string, never applies file edits directly

To activate: open the Bob mode picker and select **Coding Agent**.

---

### `.bob/rules-coding-agent/AGENTS.md`
**Mode-specific invariants, auto-injected by Bob.**

Bob automatically injects this file into every session running in the `coding-agent` mode.
Contains the critical invariants from the spec files in a compact, always-available form:
output format, history append-only rule, `success` semantics, `scoped_files` boundary,
`NEW FILE:` convention, Architect-fault attribution, retry-cap behaviour, immutable fields,
diff format, pipeline context, and cross-references.

---

## Statelessness Requirement

The Coding Agent is **stateless**. Each invocation receives the complete task object and produces
the complete output object. There is no shared memory, no database, no cached state between runs.
All state lives in the `AgentTaskObject` passed through the pipeline.

---

## Standalone Bridge (Conditional — Not Yet Created)

If the team's pipeline orchestration layer requires invoking the Coding Agent via CLI rather than
through Bob's mode interface, a thin bridge script (`agents/coding_agent/bridge.py` or `.js`)
should be added. This bridge would:
- Accept a task object JSON from stdin or a file argument
- Invoke the Coding Agent logic
- Write the output task object to stdout or a file

**This file does not exist yet.** Create it only when the team confirms the orchestration
mechanism. If Bob is the end-to-end runner, no bridge is needed.

---

## Spec File Reading Order (for new contributors)

Read in this order to understand the full Coding Agent before modifying anything:

1. `task_schema.json` — understand the contract
2. `system_prompt.md` — understand the full agent behaviour
3. `input_validation.md` — understand all failure modes
4. `context_assembly.md` — understand how the LLM receives its input
5. `diff_generation.md` — understand what the LLM must produce
6. `task_object_assembly.md` — understand how the output is built
7. `PROJECT_STRUCTURE.md` (this file) — understand how everything fits together
