# Coding Agent — Diff Generation Specification

This document defines:
1. The diff instruction block appended to the LLM prompt (Section 6 of `context_assembly.md`)
2. The post-processing validation the runner applies to the raw LLM output before writing `code_diff`
3. The failure output when diff validation does not pass

This step runs after context assembly (`context_assembly.md`) and before task object assembly
(`task_object_assembly.md`).

---

## Part 1 — Diff Instruction Block (LLM Prompt Section 6)

The following text is appended verbatim as the final section of the assembled LLM prompt.
The runner copies it unchanged — do not summarise or paraphrase it.

---

```
### DIFF INSTRUCTIONS

Produce a single unified diff (GNU diff -u format) that implements the Architect Plan above
and satisfies all Acceptance Criteria.

RULES — you must follow every one of these exactly:

1. Only produce changes for files listed in the SCOPED FILES section above.
   Do not reference, create, or modify any file whose path does not appear there.

2. For EXISTING files, use this header format:
   --- a/<path>
   +++ b/<path>
   @@ -<start_line>,<line_count> +<start_line>,<line_count> @@
   Include exactly 3 lines of unchanged context above and below every changed block.

3. For NEW files (those marked "[NEW FILE — to be created]"), use this header format:
   --- /dev/null
   +++ b/<path>
   @@ -0,0 +1,<total_line_count> @@
   Every line of the new file's content must appear as a "+" line.
   Do not include context lines for new files.

4. New-file paths in the diff must exactly match the paths shown in the SCOPED FILES section.
   Do not alter capitalisation, separators, or add/remove extensions.

5. The diff must be a single contiguous string covering all changed files.
   If multiple files are changed, concatenate their per-file diff blocks one after another
   with no blank lines between the end of one file block and the start of the next.

6. Do not produce prose, explanations, markdown fences, or any text outside the diff itself.
   The entire response must be the raw diff string and nothing else.

7. If no changes are required to satisfy the plan and criteria, output an empty string.
   Do not invent changes.
```

---

## Part 2 — Post-Processing Diff Validation

After the LLM responds, the runner must validate the raw output before writing it to `code_diff`.
Run checks in the order listed. Stop at the first failure.

### Check 1 — Empty Output

**Condition:** Raw LLM output is `None`, empty string, or contains only whitespace.

**Allowed exception:** If the plan explicitly states no code changes are needed and the diff is
intentionally empty, this is a valid edge case. In practice for this pipeline the Coding Agent
should not be invoked on a no-op task — if the LLM returns empty, treat it as a generation
failure.

**Action on failure:**
```
code_diff:      null
output_summary: "diff generation failed: LLM returned empty output"
success:        false
status:         "blocked"
current_agent:  "manager_agent"
```

---

### Check 2 — No Diff Structure Detected

**Condition:** The raw output does not contain at least one `@@` hunk marker AND does not start
with `---`.

A string that starts with `---` but has no `@@` is likely a partial or malformed diff; still
fail it. Both markers must be present.

**Action on failure:**
```
code_diff:      null
output_summary: "diff generation failed: output is not a valid unified diff (no @@ hunk markers)"
success:        false
status:         "blocked"
current_agent:  "manager_agent"
```

---

### Check 3 — Scope Violation

**Condition:** The raw output contains a `+++ b/<path>` line where `<path>` (normalised:
strip leading `./`, collapse `//`) is NOT present in `scoped_files` (normalised the same way).

Apply the same path normalisation rules from `input_validation.md` Path Normalisation Rules.

**Why this matters:** The LLM may hallucinate file paths not in the Architect's scope. Passing
such a diff downstream would cause the Testing Agent to apply changes to unscoped files, breaking
the Architect's blast-radius boundary.

**Action on failure:**
```
code_diff:      null
output_summary: "diff generation failed: diff references out-of-scope file: <path>"
success:        false
status:         "blocked"
current_agent:  "manager_agent"
```

If multiple out-of-scope paths are present, report the first one found (in order of appearance
in the diff). Do not enumerate all violations — one is sufficient to stop.

---

### Check 4 — Path Traversal in Diff

**Condition:** Any path appearing in a `---` or `+++` header contains `..` as a path component
or is absolute (starts with `/`), excluding the literal `--- /dev/null` which is valid for new
files.

**Action on failure:**
```
code_diff:      null
output_summary: "diff generation failed: diff contains unsafe path: <path>"
success:        false
status:         "blocked"
current_agent:  "manager_agent"
```

---

### Validation Passed — Write `code_diff`

If all four checks pass, write the raw LLM output string as-is to `code_diff`.
Do not strip whitespace, reformat, or modify the diff in any way — the Testing Agent applies
it directly. Any alteration could break applicability.

---

## Part 3 — Validation Failure Output Object

When any check above fails, the runner assembles and outputs the task object as follows:

**Fields copied verbatim from input:**
- `task_id`, `feature_request`, `acceptance_criteria`, `scoped_files`, `plan`,
  `test_results`, `review_result`, `retry_count`

**Fields written:**

| Field | Value |
|---|---|
| `code_diff` | `null` |
| `status` | `"blocked"` |
| `current_agent` | `"manager_agent"` |

**History entry appended:**

```json
{
  "agent": "coding_agent",
  "output_summary": "diff generation failed: <specific reason from the check that failed>",
  "timestamp": "<ISO 8601 now>",
  "success": false
}
```

The output object must be passed through `validate_task()` before being written downstream,
as with all outputs from the Coding Agent.

---

## Part 4 — Retry-Cap Escalation (Guard 3 reminder)

This case is handled by Guard 3 in `input_validation.md` before diff generation is attempted.
It is documented here for cross-reference only.

When `status == "needs_retry"` AND `retry_count == 2`:
- Diff generation is **not** invoked.
- `code_diff` is preserved from the input object (the last attempted diff).
- `status` → `"awaiting_human_approval"`, `current_agent` → `"human"`.

The diff produced in a previous cycle is intentionally kept intact so the human reviewer can
inspect what the agent last attempted.

---

## Unified Diff Format Reference

```
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -42,7 +42,12 @@
     def login(self, request):
         username = request.data.get("username")
         password = request.data.get("password")
+        ip = request.META.get("REMOTE_ADDR")
+        if self.rate_limiter.is_exceeded(ip):
+            return Response(status=429)
+        self.rate_limiter.record(ip)
         user = authenticate(username=username, password=password)
         if not user:
             return Response(status=401)
--- /dev/null
+++ b/src/middleware/rate_limit.py
@@ -0,0 +1,18 @@
+from collections import defaultdict
+import time
+
+
+class TokenBucketRateLimiter:
+    def __init__(self, max_requests=5, window_seconds=600):
+        self.max_requests = max_requests
+        self.window_seconds = window_seconds
+        self._buckets = defaultdict(list)
+
+    def is_exceeded(self, key: str) -> bool:
+        now = time.time()
+        bucket = [t for t in self._buckets[key] if now - t < self.window_seconds]
+        self._buckets[key] = bucket
+        return len(bucket) >= self.max_requests
+
+    def record(self, key: str) -> None:
+        self._buckets[key].append(time.time())
```

This example corresponds to the `task_schema.json` example task (rate-limiting the login
endpoint). It shows a two-file diff: one existing file modified and one new file created.

---

## TEAM DISCUSSION POINT (carried from plan)

> The schema stores `code_diff` as a single `string | null` field. For tasks that scope many
> large files, the combined diff could be very large and approach LLM output token limits.
> This is acceptable for the hackathon scope. If the team encounters truncation issues at the
> output stage, a structured multi-file diff representation would need discussion.
> **Do not change `task_schema.json` unilaterally.**
