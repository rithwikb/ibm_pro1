# Coding Agent — Input Validation Specification

This document is the authoritative specification for every guard the Coding Agent's orchestration
layer must execute **before** the LLM is invoked. Guards run in the order listed. On the first
failure, emit the prescribed output and stop — do not continue to subsequent guards.

All output objects (including every failure path below) must be passed through `validate_task()`
(jsonschema draft-07 against `task_schema.json`) before being written downstream.

---

## Terminology

| Term | Meaning |
|---|---|
| **Coding Agent failure** | The Coding Agent attempted its responsibility and failed — records `success: false`. Applies to Guards 4 and 5 and all diff-generation failures. Guards 1 and 2 are routing errors that produce no output object and no history entry at all. |
| **Architect-fault** | The Coding Agent could not attempt its responsibility because a prior agent (Architect) supplied invalid input — records `success: null` on the Coding Agent's history entry and `success: false` on a prepended `architect_agent` entry. Excluded from Coding Agent success-rate calculations. |
| **Two-entry pattern** | The required history output for any Architect-fault: one `architect_agent` entry (`success: false`) followed by one `coding_agent` entry (`success: null`) |
| **Blocking** | Setting `status: "blocked"` and `current_agent: "manager_agent"` with no diff produced |

---

## `success` Semantics (Team-Confirmed Rule)

| Value | Meaning |
|---|---|
| `true` | The Coding Agent attempted its responsibility and succeeded |
| `false` | The Coding Agent attempted its responsibility and failed |
| `null` | The Coding Agent could not attempt its responsibility because a prior agent supplied invalid input; excluded from Coding Agent success-rate calculations |

---

## Execution Order

```
Guard 1  → current_agent identity check        ← routing error, no output object produced
Guard 2  → status validity check               ← routing error, no output object produced
Guard 3  → retry-cap gate                      ← EXIT EARLY if triggered (not a failure)
Guard 4  → plan presence check
Guard 5  → scoped_files non-empty check
Guard 6  → NEW FILE: declaration parsing
Guard 7a → NEW FILE cross-check: every declared path present in scoped_files
Guard 7b → NEW FILE cross-check: declared path must not already exist on disk
Guard 7c → scoped_files existence check: every non-new-file path must exist on disk
```

Guards 1–2 are routing errors — no output object is produced, no history entry is appended.
Guard 3 is a routing shortcut — not a failure; produces an output object with `success: false` (attempted, no accepted diff).
Guards 4–5 are Coding Agent failures (`success: false` — attempted and failed).
Guards 6, 7a, 7b, 7c are Architect-faults (`success: null` — could not attempt due to prior-agent invalid input).

---

## Guard 1 — `current_agent` Identity Check

**Condition:** `current_agent != "coding_agent"`

**Rationale:** The task object has been routed to the wrong agent. Do not process it.

**Action:** Log the mismatch and halt without modifying the task object. Do not append a history
entry — the Coding Agent was never the rightful owner of this object. This is a pipeline routing
error, not a task-level failure.

> No output object is produced. The runner should surface this as a hard process error.

---

## Guard 2 — `status` Validity Check

**Condition:** `status` is not `"in_progress"` and not `"needs_retry"`

**Rationale:** The Coding Agent only operates on tasks actively in flight. Any other status
(e.g. `"pending"`, `"approved"`, `"blocked"`) means the object was sent here by mistake.

**Action:** Same as Guard 1 — halt without modifying the object. Log the unexpected status.
Do not append a history entry.

> No output object is produced. Runner surfaces as a hard process error.

---

## Guard 3 — Retry-Cap Gate

**Condition:** `status == "needs_retry"` AND `retry_count == 2`

**Rationale:** The team has capped retries at 2. A third coding attempt is not permitted.
Route to a human instead of looping.

**This is not a failure.** The Coding Agent completed its responsibility correctly.

**Output object:**

| Field | Value |
|---|---|
| `status` | `"awaiting_human_approval"` |
| `current_agent` | `"human"` |
| `code_diff` | Unchanged from input (preserve the last attempt for human review) |
| All other fields | Copied verbatim from input |

**History entry appended:**

```json
{
  "agent": "coding_agent",
  "output_summary": "retry_count cap reached, escalating to human",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```

> `success: false` here records that this retry cycle did not produce an accepted diff,
> not that the Coding Agent itself erred. The Manager Agent uses this to track outcomes.

---

## Guard 4 — `plan` Presence Check

**Condition:** `plan` is `null` OR is an empty string (`""` or whitespace-only)

**Rationale:** The Coding Agent cannot implement anything without the Architect's plan.
A null or empty plan indicates the Architect Agent did not complete its step.

**Output object:**

| Field | Value |
|---|---|
| `status` | `"blocked"` |
| `current_agent` | `"manager_agent"` |
| `code_diff` | `null` |
| All other fields | Copied verbatim from input |

**History entry appended — two distinct cases:**

When `plan` is `null`:
```json
{
  "agent": "coding_agent",
  "output_summary": "validation failed: plan is null",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```

When `plan` is `""` or whitespace-only:
```json
{
  "agent": "coding_agent",
  "output_summary": "validation failed: plan is empty",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```

> Use the exact string that matches the actual condition. Do not use `"plan is null"` when the
> plan is an empty or whitespace-only string — a downstream Manager Agent reading history must
> receive accurate diagnostic information.

---

## Guard 5 — `scoped_files` Non-Empty Check

**Condition:** `scoped_files` is an empty array (`[]`)

**Rationale:** There is nothing to read or modify. The Architect must scope at least one file.

**Output object:**

| Field | Value |
|---|---|
| `status` | `"blocked"` |
| `current_agent` | `"manager_agent"` |
| `code_diff` | `null` |
| All other fields | Copied verbatim from input |

**History entry appended:**

```json
{
  "agent": "coding_agent",
  "output_summary": "validation failed: scoped_files is empty",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```

---

## Guard 6 — `NEW FILE:` Declaration Parsing

**Not a failure condition.** This guard parses the `plan` field and builds `intended_new_files`
before any disk access occurs.

**Parsing rule:**

1. Split `plan` into lines.
2. For each line: if it begins **exactly** with the string `NEW FILE: ` (case-sensitive,
   no leading whitespace), extract the remainder as a path candidate.
3. Strip leading and trailing whitespace from each path candidate.
4. Normalise: remove any leading `./` from the path.
5. **Reject immediately as an Architect-fault** any path that:
   - Contains `..` as a path component (e.g. `../secret.py`, `src/../../etc/passwd`)
   - Is absolute (starts with `/`)
   - After normalisation resolves to empty string

   For path-traversal rejections, use the Architect-fault pattern with:
   - `architect_agent` output_summary: `"NEW FILE path resolves outside repository root: <path>"`
   - `coding_agent` output_summary: `"execution blocked before coding — NEW FILE path resolves outside repository root: <path>"`

6. Add all remaining valid paths to `intended_new_files`.

**Result:** `intended_new_files` is a set of normalised relative paths that the Coding Agent is
permitted to create as new files in the diff.

> Only exact `NEW FILE: <path>` lines qualify. Natural-language phrases like "create a new file
> called foo.py" or "add src/foo.py" in the prose of `plan` do not register.

---

## Guard 7a — `NEW FILE:` Paths Must Appear in `scoped_files`

**Condition:** Any path in `intended_new_files` is NOT present in `scoped_files` (after
normalising `scoped_files` paths with the same `./`-stripping rule).

**Rationale:** `scoped_files` is the authoritative list of files the Coding Agent is allowed to
touch. An Architect who declares a new file must also scope it. A declaration without a
corresponding `scoped_files` entry is an Architect error.

**Output object:**

| Field | Value |
|---|---|
| `status` | `"blocked"` |
| `current_agent` | `"manager_agent"` |
| `code_diff` | `null` (or unchanged if already set from a prior run) |
| All other fields | Copied verbatim from input |

**History entries appended (two-entry pattern):**

```json
{
  "agent": "architect_agent",
  "output_summary": "NEW FILE declaration missing from scoped_files: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```
```json
{
  "agent": "coding_agent",
  "output_summary": "execution blocked before coding — NEW FILE declaration missing from scoped_files: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": null
}
```

> If multiple `NEW FILE:` paths fail this check, emit one two-entry pair per offending path,
> in the order they appear in `plan`. All pairs are appended before stopping.

---

## Guard 7b — `NEW FILE:` Paths Must Not Already Exist on Disk

**Condition:** Any path in `intended_new_files` already exists at `<repo_root>/<path>` on disk.

**Rationale:** Declaring an existing file as "new" is contradictory. The Architect must have made
a mistake — either the file already exists and should be modified (not created), or the path is
wrong. Either way the Coding Agent cannot resolve this ambiguity and must escalate.

**Output object:** Same structure as Guard 7a.

**History entries appended (two-entry pattern):**

```json
{
  "agent": "architect_agent",
  "output_summary": "NEW FILE declared for already-existing file: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```
```json
{
  "agent": "coding_agent",
  "output_summary": "execution blocked before coding — NEW FILE declared for already-existing file: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": null
}
```

---

## Guard 7c — `scoped_files` Paths Must Exist on Disk (Unless Declared New)

**Condition:** Any path in `scoped_files` (normalised) does not exist at `<repo_root>/<path>` on
disk AND is not in `intended_new_files`.

**Rationale:** The Architect scoped a file the Coding Agent cannot load and did not declare as
new. This is an Architect error — a bad path that retrying the Coding Agent cannot fix.

**Output object:** Same structure as Guard 7a.

**History entries appended (two-entry pattern):**

```json
{
  "agent": "architect_agent",
  "output_summary": "provided nonexistent file path: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```
```json
{
  "agent": "coding_agent",
  "output_summary": "execution blocked before coding — provided nonexistent file path: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": null
}
```

> If multiple `scoped_files` paths fail this check, emit one two-entry pair per offending path,
> in `scoped_files` order. All pairs are appended before stopping.

---

## Path Normalisation Rules (Applied Everywhere)

These rules apply consistently to all path comparisons in Guards 6, 7a, 7b, and 7c:

1. Strip leading `./` (e.g. `./src/foo.py` → `src/foo.py`)
2. Collapse redundant interior separators (e.g. `src//foo.py` → `src/foo.py`)
3. Do not resolve symlinks — use the literal path string
4. Comparison is case-sensitive on all platforms (match the filesystem exactly)
5. Paths are always relative to the repository root — never absolute

---

## `validate_task()` — Final Output Gate

Every output object produced on any path (success, failure, Architect-fault, retry-cap escalation)
must pass through `validate_task()` before being handed to the next agent.

`validate_task()` validates the object against `task_schema.json` (JSON Schema draft-07).

If `validate_task()` raises a validation error on a failure-path output (which should not happen
if the output was assembled correctly), log the full error, do not pass the object downstream,
and surface it as a hard process error for debugging.

The `success: null` value on Coding Agent history entries on the Architect-fault path is valid
under the schema because `history[].success` is typed as `["boolean", "null"]`.
