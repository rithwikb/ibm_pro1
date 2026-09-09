# Coding Agent — Task Object Assembly Specification

This document defines how the Coding Agent constructs the complete output `AgentTaskObject`
across every possible execution path. It covers field-by-field assembly rules, `output_summary`
templates, history entry shapes, and the mandatory `validate_task()` gate.

This step runs after diff generation (`diff_generation.md`) and is the final step before the
output object is passed downstream.

---

## Fields That Are Always Copied Verbatim

The following fields must be copied **byte-for-byte** from the input object. Never compute,
infer, or modify them.

| Field | Type in schema |
|---|---|
| `task_id` | `string` |
| `feature_request` | `string` |
| `acceptance_criteria` | `array of string` |
| `scoped_files` | `array of string` |
| `plan` | `string \| null` |
| `test_results` | `object \| null` |
| `review_result` | `object \| null` |
| `retry_count` | `integer` |

Do not re-serialise, re-order array elements, or apply any transformation. The downstream agents
depend on these values being identical to what the upstream agents wrote.

---

## Fields Written by the Coding Agent

### `code_diff`

| Outcome | Value |
|---|---|
| Diff produced and validated | The raw unified diff string from the LLM |
| Any failure path (validation, diff check, Guard 4/5) | `null` |
| Retry-cap escalation (Guard 3) | Copied verbatim from input (preserve previous attempt) |
| Architect-fault path | `null` (or unchanged if input already had a prior diff — copy from input) |

> Rationale for retry-cap: the human reviewer must be able to see the last attempted diff.
> Setting it to `null` would destroy that information.

---

### `status`

Exactly one of the seven schema-defined enum values:

| Outcome | `status` |
|---|---|
| Diff produced successfully (first run or retry) | `"in_progress"` |
| Retry-cap escalation (`retry_count == 2`) | `"awaiting_human_approval"` |
| Guard 4 failure (plan null/empty) | `"blocked"` |
| Guard 5 failure (`scoped_files` empty) | `"blocked"` |
| Diff generation failure (checks 1–4) | `"blocked"` |
| Architect-fault (Guards 7a/7b/7c or path traversal in Guard 6) | `"blocked"` |
| `read_file` error on an existing file (Step 3 in context_assembly.md) | `"blocked"` |

---

### `current_agent`

Exactly one of the seven schema-defined enum values:

| Outcome | `current_agent` |
|---|---|
| Diff produced successfully | `"testing_agent"` |
| Retry-cap escalation | `"human"` |
| Any Coding Agent failure or Architect-fault | `"manager_agent"` |

---

### `history`

`history` is append-only. Never modify, remove, or reorder existing entries.

Append the new entry (or entries, on the Architect-fault path) at the **end** of the array.
The schema requires `["agent", "output_summary", "timestamp"]` on every entry.
Always include `"success"` as well — it is required for the Manager Agent's success-rate tracking.

#### Normal path — one entry appended

```json
{
  "agent": "coding_agent",
  "output_summary": "<see output_summary rules below>",
  "timestamp": "<ISO 8601 UTC now — e.g. 2026-08-10T09:10:00Z>",
  "success": <true | false>
}
```

#### Architect-fault path — two entries appended in this order

```json
{
  "agent": "architect_agent",
  "output_summary": "<architect-fault template string>",
  "timestamp": "<ISO 8601 UTC now>",
  "success": false
}
```
```json
{
  "agent": "coding_agent",
  "output_summary": "<coding-agent mirror template string>",
  "timestamp": "<ISO 8601 UTC now>",
  "success": null
}
```

Both entries receive the **same** timestamp (the current time at assembly).
`success: null` on the Coding Agent entry means the Coding Agent could not attempt its
responsibility because a prior agent supplied invalid input — the coding step was never executed.
This is the only permitted use of `null` in the Coding Agent's own history entry.
`null` entries are excluded from Coding Agent success-rate calculations by the Manager Agent.

---

## `output_summary` Rules

The `output_summary` is a single line. Select the applicable row, then apply any applicable
suffix modifier.

### Base summaries

| Outcome | `output_summary` | `success` |
|---|---|---|
| First run, diff produced, no new files | `"code_diff generated for N file(s)"` | `true` |
| First run, diff produced, includes new file(s) | `"code_diff generated for N file(s) including M new file(s)"` | `true` |
| Retry, diff produced | `"code_diff revised addressing M review findings"` | `true` |
| Retry-cap escalation | `"retry_count cap reached, escalating to human"` | `false` |
| Guard 4 — plan is `null` | `"validation failed: plan is null"` | `false` |
| Guard 4 — plan is `""` or whitespace | `"validation failed: plan is empty"` | `false` |
| Guard 5 — scoped_files empty | `"validation failed: scoped_files is empty"` | `false` |
| Diff check 1 — empty output | `"diff generation failed: LLM returned empty output"` | `false` |
| Diff check 2 — no hunk markers | `"diff generation failed: output is not a valid unified diff (no @@ hunk markers)"` | `false` |
| Diff check 3 — out-of-scope path | `"diff generation failed: diff references out-of-scope file: <path>"` | `false` |
| Diff check 4 — path traversal | `"diff generation failed: diff contains unsafe path: <path>"` | `false` |
| `read_file` error | `"validation failed: could not read scoped file: <path>"` | `false` |

### Architect-fault summaries (two-entry pattern)

Use the exact template strings. Substitute `<path>` with the normalised offending path.

| Fault | `architect_agent` summary | `coding_agent` summary |
|---|---|---|
| Guard 6 — path traversal in `NEW FILE:` | `"NEW FILE path resolves outside repository root: <path>"` | `"execution blocked before coding — NEW FILE path resolves outside repository root: <path>"` |
| Guard 7a — `NEW FILE:` not in `scoped_files` | `"NEW FILE declaration missing from scoped_files: <path>"` | `"execution blocked before coding — NEW FILE declaration missing from scoped_files: <path>"` |
| Guard 7b — `NEW FILE:` path exists on disk | `"NEW FILE declared for already-existing file: <path>"` | `"execution blocked before coding — NEW FILE declared for already-existing file: <path>"` |
| Guard 7c — `scoped_files` path missing, not declared new | `"provided nonexistent file path: <path>"` | `"execution blocked before coding — provided nonexistent file path: <path>"` |

### Suffix modifiers

Apply after the base summary, space-separated, only when the condition is true:

| Condition | Suffix appended to base |
|---|---|
| Any file was truncated to fit context | `" (K file(s) truncated to fit context)"` |

Example with both new files and truncation:
```
"code_diff generated for 4 file(s) including 1 new file(s) (2 file(s) truncated to fit context)"
```

The truncation suffix is never appended to failure or Architect-fault summaries — truncation only
occurs on paths where the LLM was actually invoked.

---

## Variable Substitution Reference

| Variable | Value |
|---|---|
| `N` | `len(scoped_files)` — total number of scoped files (existing + new) |
| `M` on new-file summary | `len(intended_new_files ∩ scoped_files)` — new files actually created |
| `M` on retry summary | `len(review_result.findings)` — count of findings addressed |
| `K` | `truncated_count` from context assembly metadata |
| `<path>` | Normalised path (leading `./` stripped) |

---

## Complete Output Object Shape per Path

### Path A — Success (first run or retry)

```
task_id:             <copied>
feature_request:     <copied>
acceptance_criteria: <copied>
scoped_files:        <copied>
status:              "in_progress"
current_agent:       "testing_agent"
plan:                <copied>
history:             [...existing entries..., { coding_agent entry, success: true }]
code_diff:           "<validated unified diff string>"
test_results:        <copied>
review_result:       <copied>
retry_count:         <copied>
```

### Path B — Retry-Cap Escalation

```
task_id:             <copied>
feature_request:     <copied>
acceptance_criteria: <copied>
scoped_files:        <copied>
status:              "awaiting_human_approval"
current_agent:       "human"
plan:                <copied>
history:             [...existing entries..., { coding_agent entry, success: false }]
code_diff:           <copied — preserve input value, do NOT set to null>
test_results:        <copied>
review_result:       <copied>
retry_count:         <copied>
```

### Path C — Coding Agent Failure (Guards 4/5, diff checks, read_file error)

```
task_id:             <copied>
feature_request:     <copied>
acceptance_criteria: <copied>
scoped_files:        <copied>
status:              "blocked"
current_agent:       "manager_agent"
plan:                <copied>
history:             [...existing entries..., { coding_agent entry, success: false }]
code_diff:           null
test_results:        <copied>
review_result:       <copied>
retry_count:         <copied>
```

### Path D — Architect-Fault

```
task_id:             <copied>
feature_request:     <copied>
acceptance_criteria: <copied>
scoped_files:        <copied>
status:              "blocked"
current_agent:       "manager_agent"
plan:                <copied>
history:             [...existing entries...,
                      { architect_agent entry, success: false },
                      { coding_agent entry, success: null }]
code_diff:           null  (or <copied> if input already had a non-null code_diff from a prior run)
test_results:        <copied>
review_result:       <copied>
retry_count:         <copied>
```

---

## Final Gate — `validate_task()`

After assembling the output object on **every** path (A, B, C, D), run it through `validate_task()`.

`validate_task()` validates the assembled JSON against `task_schema.json` (JSON Schema draft-07).

**If validation passes:** pass the object downstream to the next agent.

**If validation raises:**
- Do not pass the object downstream.
- Log the full validation error for debugging.
- Surface as a hard process error. This should never happen if assembly followed this spec
  correctly — a raised error indicates a bug in the runner, not a task-level failure.

The `success: null` value permitted on the Architect-fault coding_agent history entry is valid
under the schema: `history[].success` is typed `["boolean", "null"]` in `task_schema.json`.
No schema change is required.
