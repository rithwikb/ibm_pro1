# Coding Agent — Context Assembly Specification

This document defines exactly how the Coding Agent builds the LLM prompt after input validation
passes. It covers file reading, new-file handling, token budget management, and the full prompt
structure in the order sections must appear.

This step runs after all guards in `input_validation.md` pass and before the LLM is called.

---

## Step 1 — Build `intended_new_files`

Parse `plan` for `NEW FILE:` declarations using the same logic as input_validation.md Guard 6.
This step is repeated here because context assembly must have `intended_new_files` available
before it touches the filesystem. If Guard 6 already ran and produced a validated set, reuse it.

**Parsing rule (identical to Guard 6):**

1. Split `plan` into lines.
2. Collect lines beginning exactly with `NEW FILE: ` (case-sensitive, no leading whitespace).
3. Extract the path after `NEW FILE: `, strip surrounding whitespace.
4. Normalise: remove leading `./`.
5. Reject (Architect-fault) any path containing `..`, starting with `/`, or resolving to empty.

Result: `intended_new_files` — a set of normalised relative paths the Coding Agent may create.

---

## Step 2 — Normalise All `scoped_files` Paths

Before any disk access, normalise every path in `scoped_files`:

- Strip leading `./`
- Collapse redundant separators (`src//foo.py` → `src/foo.py`)
- Comparison is case-sensitive

Maintain the original `scoped_files` array order. Normalisation is for comparison only — the
original strings are copied verbatim into the output task object.

---

## Step 3 — Read Files from Disk

Process each normalised `scoped_files` path in array order:

### 3a — New Files (path is in `intended_new_files`)

Do not read from disk. Instead, record a placeholder entry:

```
path:    <normalised path>
content: None  (new file — no existing content)
is_new:  True
```

### 3b — Existing Files (path is NOT in `intended_new_files`)

Read the file at `<repo_root>/<normalised_path>` using Bob's `read_file` tool.

Record:

```
path:    <normalised path>
content: <full file content as a string>
is_new:  False
char_count: len(content)
```

If `read_file` raises an error (e.g. a permission error on a file that was confirmed to exist
during Guard 7c), surface it as a Coding Agent failure:

```json
{
  "agent": "coding_agent",
  "output_summary": "validation failed: could not read scoped file: <path>",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```

Set `status: "blocked"`, `current_agent: "manager_agent"`. Output and stop.

---

## Step 4 — Apply Context Budget

### 4a — Estimate Total Token Count

Use the following approximation (configurable):

```
tokens_per_file ≈ ceil(char_count / 4)
```

New files (`is_new: True`) contribute **0 tokens** to the budget.

Sum token estimates across all existing files. Call this `total_tokens`.

### 4b — Budget Threshold

Default threshold: **100,000 tokens** (configurable via environment variable
`CODING_AGENT_CONTEXT_BUDGET`, parsed as an integer).

If `total_tokens <= threshold`: use all files in full. No truncation needed. Proceed to Step 5.

### 4c — Proportional Truncation (Fallback)

If `total_tokens > threshold`:

1. Count `N` = number of existing files (new files are excluded from budgeting).
2. Allocate `per_file_tokens = floor(threshold / N)` tokens to each existing file.
3. Convert back to a character limit: `per_file_chars = per_file_tokens * 4`.
4. For each existing file: if `len(content) > per_file_chars`, truncate to `per_file_chars`
   characters. Preserve the **start** of the file — the LLM needs imports, class headers, and
   function signatures more than it needs deeply nested implementation bodies.
5. Append a truncation marker at the cut point:

   ```
   ... [TRUNCATED: original file was N chars; showing first M chars] ...
   ```

6. Record which files were truncated and how many (`truncated_count`).

**Do not** drop any file entirely. Every file in `scoped_files` must appear in the prompt
(either in full or truncated), regardless of its position in the array.

### 4d — Truncation Notice

If any file was truncated, the `output_summary` for the Coding Agent's history entry must include
the suffix:

```
 (N file(s) truncated to fit context)
```

where N is `truncated_count`. Example:

```
"code_diff generated for 3 file(s) (2 file(s) truncated to fit context)"
```

This suffix is appended to whatever the base summary would have been. See system_prompt.md for
the full `output_summary` rules table.

---

## Step 5 — Assemble the LLM Prompt

Construct the prompt as a single string. Sections must appear in this exact order:

---

### Section 1 — Task

```
### TASK
Feature request: <feature_request>
```

---

### Section 2 — Acceptance Criteria

```
### ACCEPTANCE CRITERIA
- <acceptance_criteria[0]>
- <acceptance_criteria[1]>
...
```

One bullet per entry. Copy the strings verbatim from `acceptance_criteria` — do not paraphrase.

---

### Section 3 — Review Findings (conditional)

Include this section **only** if `review_result` is not `null`.

```
### REVIEW FINDINGS
You are on a retry. Address ALL findings with severity "high" or "critical".
Use best judgment on "low" and "medium" findings.

- [<severity>] <file>:<line> — <description> (<checklist_item>)
- [<severity>] <file>:<line if not null, else "?"> — <description> (<checklist_item>)
...
```

Render each entry in `review_result.findings` in the order they appear in the array.
If `finding.line` is `null`, render it as `?` (e.g. `src/auth/login.py:?`).

---

### Section 4 — Architect Plan

```
### ARCHITECT PLAN
<plan>
```

Paste `plan` verbatim. Do not summarise, reformat, or truncate it.

---

### Section 5 — Scoped Files

One block per file, in `scoped_files` array order:

**For existing files (full or truncated):**

````
### FILE: <normalised_path>
```
<file content, possibly truncated with truncation marker>
```
````

**For new files:**

````
### FILE: <normalised_path>
```
[NEW FILE — to be created]
```
````

The fenced code block uses plain triple-backtick fences with no language tag (the LLM should infer
the language from the file extension). If the file extension is known (e.g. `.py`, `.js`, `.ts`,
`.java`, `.go`), a language tag may be added for readability but is not required.

---

### Section 6 — Diff Instructions

Append the diff generation instructions verbatim at the end of the prompt. These are defined in
`system_prompt.md` Step 4 and must not be modified here. The runner copies them unchanged.

---

## Step 6 — Prompt Assembly Output

The result of this step is a single string: the assembled user-turn message to be passed to the
LLM alongside the system prompt from `system_prompt.md`.

Return alongside it the metadata the runner needs to assemble the output task object:

```
assembled_prompt:  <string>
truncated_count:   <int, 0 if no truncation>
new_file_count:    <int, number of paths in intended_new_files that are in scoped_files>
total_file_count:  <int, len(scoped_files)>
intended_new_files: <set of normalised new-file paths>
```

---

## Prompt Length Budget Reference

| Component | Approximate token cost |
|---|---|
| Section 1 (task) | ~20 tokens |
| Section 2 (acceptance criteria) | ~10 tokens per criterion |
| Section 3 (review findings, if present) | ~30 tokens per finding |
| Section 4 (plan) | Varies — typically 50–300 tokens |
| Section 5 (files) | Up to `threshold` tokens total after budgeting |
| Section 6 (diff instructions) | ~200 tokens (fixed) |

The context budget in Step 4 applies only to Section 5. Sections 1–4 and 6 are always included
in full. If the plan itself is pathologically large, the runner should log a warning but still
include it — truncating the plan is not permitted.

---

## Error Conditions Summary

| Condition | Output | `success` |
|---|---|---|
| `read_file` fails on an existing scoped file | `blocked` → `manager_agent`; one history entry | `false` |
| Path-traversal in `NEW FILE:` declaration | Architect-fault two-entry pattern; `blocked` | `null` |
| All other Architect-faults | Defined in `input_validation.md` Guards 7a/7b/7c | `null` |

No other error conditions are expected at this step. All pre-conditions were verified by the
guards in `input_validation.md` before context assembly begins.
