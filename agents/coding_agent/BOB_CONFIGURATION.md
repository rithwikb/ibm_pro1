# Coding Agent — Bob Configuration and Integration Notes

This document covers the operational concerns for running the Coding Agent as an IBM Bob
custom mode: model selection, workspace root setup, `validate_task()` integration, and
coordination points with other agent builders.

---

## Bob Mode Registration

The Coding Agent is registered in `.bob/custom_modes.yaml` under slug `coding-agent`.

**To activate:** open the Bob mode picker and select **Coding Agent**.

**Files already created (Sub-Task 6):**

| File | Purpose |
|---|---|
| `.bob/custom_modes.yaml` | Mode registration — slug, name, roleDefinition, groups |
| `.bob/rules-coding-agent/AGENTS.md` | Mode-specific rules auto-injected by Bob into every session |

These files are the complete Bob configuration for the Coding Agent. No additional Bob config
is required.

---

## Permission Groups

The mode is registered with:

```yaml
groups:
  - read    # read_file tool — loads scoped_files from disk
  - todo    # update_todo_list tool — step tracking during a session
```

**`read` only — no `edit`, no `execute`.**

The agent produces a diff string written into the `code_diff` field of the task object.
It never applies file changes directly to the repository. Granting `edit` permissions would
be a security risk and is not needed.

---

## LLM Model Selection

The Coding Agent is the **highest context-load** agent in the pipeline. A single invocation
embeds:
- Full contents of every file in `scoped_files` (up to the token budget)
- The Architect's `plan`
- All `acceptance_criteria`
- All `review_result.findings` (on retry)
- The diff instruction block (~200 tokens, fixed)

**Minimum recommended context window: 128,000 tokens.**

Model selection is a **configuration concern**, not a hardcoded value. Set it in the
`custom_modes.yaml` entry or via the Bob session model picker — do not embed a model name
in the system prompt or rules files.

When choosing a model, verify its output token limit as well. A model with a 128k input
context but only 4k output tokens will truncate large diffs. Prefer models that support at
least 8k output tokens.

---

## Workspace Root Configuration

`scoped_files` contains paths relative to the **sample repository root**
(e.g. `src/auth/login.py`, not `/Users/user/project/src/auth/login.py`).

Bob's `read_file` tool resolves paths relative to the **active workspace directory**.

**Requirement:** the workspace opened in Bob must be the sample repository root — the directory
that contains `src/`, `tests/`, etc. — not a parent directory or subdirectory of it.

If the sample repo lives in a subdirectory of this project (e.g. `sample_repo/`), the runner
must prepend that offset to every `scoped_files` path before calling `read_file`. Document
the offset as an environment variable:

```
CODING_AGENT_REPO_ROOT=sample_repo   # relative to workspace, default: "" (workspace root itself)
```

Set `CODING_AGENT_REPO_ROOT` to empty string (`""`) when the workspace root is already the
repo root — this is the default and expected case for the hackathon.

---

## `validate_task()` Integration

Every output object produced by the Coding Agent — on every path including failures — must
pass through `validate_task()` before being handed to the next agent.

`validate_task()` validates the assembled JSON against `task_schema.json` (JSON Schema draft-07).
It is already present in `requirements.txt` and runs at every pipeline hop in `pipeline.py`.

**The Coding Agent must not bypass this gate.**

### Integration points

1. **After task object assembly** (all four output paths A/B/C/D in `task_object_assembly.md`):
   call `validate_task(output_object)` before passing downstream.

2. **On `validate_task()` raising a validation error:**
   - Do not pass the object downstream.
   - Log the full jsonschema error (field path, constraint violated, value received).
   - Surface as a hard process error — this indicates a bug in the runner assembly logic,
     not a task-level failure. It should never happen if assembly follows `task_object_assembly.md`.

3. **The `success: null` value** on Architect-fault `coding_agent` history entries is valid
   under the schema — `history[].success` is typed `["boolean", "null"]`. `validate_task()`
   will not reject it.

### Token budget environment variable

```
CODING_AGENT_CONTEXT_BUDGET=100000   # token ceiling for scoped file content; default 100000
```

Parse as integer. Used in Step 4 of `context_assembly.md`.

---

## No MCP Required

The Coding Agent's core logic requires only:

| Capability | How it is satisfied |
|---|---|
| Read scoped files from disk | Bob's built-in `read_file` tool (`read` permission group) |
| Generate a unified diff | LLM call (handled by Bob's model routing) |
| Validate output JSON | `validate_task()` from `pipeline.py` / `requirements.txt` |
| Track progress | Bob's built-in `update_todo_list` tool (`todo` permission group) |

No MCP servers are needed. Do not add MCP tools speculatively — they increase attack surface
and session startup time.

---

## Coordination Notes for Teammates

### Testing Agent (apply `code_diff`)

The Testing Agent receives the output object and is responsible for applying `code_diff`
to the repository files. The Coding Agent's output contract is:

- `code_diff` is a raw GNU unified diff string, applicable with `patch -p1` or `git apply`
- Paths in the diff are relative to the repository root (matching `scoped_files`)
- New files use `--- /dev/null` / `+++ b/<path>` headers

**Action required:** agree with the Testing Agent builder on whether:
1. Diff application is entirely the Testing Agent's responsibility (preferred), or
2. A shared utility (`agents/shared/apply_diff.py` or similar) is created and used by both

This decision does not affect the Coding Agent's output — the diff format is fixed regardless.

### Architect Agent (own `scoped_files` and `plan`)

The Coding Agent depends on two Architect-owned fields. The following conventions must be
communicated to the Architect Agent builder:

| Convention | Rule |
|---|---|
| `NEW FILE:` declarations | Include an exact `NEW FILE: <path>` line in `plan` for every file that does not yet exist on disk but appears in `scoped_files` |
| Path accuracy | Every path in `scoped_files` must exist on disk OR be declared with `NEW FILE:` in `plan` |
| Path format | Use normalised relative paths — no leading `./`, no `..`, no absolute paths |

If the Architect omits a `NEW FILE:` declaration for a non-existent file, the Coding Agent will
attribute it as an Architect-fault and route to `manager_agent`. This is by design.

### Manager Agent (read `history` for success rates)

The Manager Agent reads `history[].success` to compute per-agent success rates.

Coding Agent `success` values and their meaning:
- `true` — diff was produced and output passed `validate_task()`
- `false` — Coding Agent failure (validation, diff generation, or retry-cap escalation)
- `null` — Coding Agent was never attempted (Architect-fault path); do not count against Coding Agent success rate

The Manager Agent builder should handle `null` as "not attempted" — exclude it from the
denominator when computing the Coding Agent's success rate.

---

## Resolved Team Discussion Points

Both discussion points from earlier in the plan are now resolved:

**1. New-file signaling** — resolved via the `NEW FILE: <path>` convention in `plan`.
No schema change was required. See `agents/coding_agent/input_validation.md` Guards 6–7c
for the full specification.

**2. Scoped-file read error signaling** — the schema has no structured field for this.
The workaround (`status: "blocked"` + reason in `history[].output_summary`) is confirmed
as sufficient for the hackathon scope. If the team needs richer error signaling post-hackathon,
a schema change would be required — flag to the Architect Agent builder at that point.

---

## Remaining Open Item

**TEAM DISCUSSION POINT (from `diff_generation.md`):**
> `code_diff` is stored as a single `string | null` field in `task_schema.json`. For tasks
> that scope many large files, the combined diff could approach the model's output token limit.
> This is acceptable for the hackathon scope. If the team encounters truncation issues at the
> output stage, a structured multi-file diff representation would need team discussion.
> **Do not change `task_schema.json` unilaterally.**
