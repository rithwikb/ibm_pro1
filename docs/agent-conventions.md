# Cross-Agent Conventions

**Status: LOCKED** — agreed by the full team. All agents and the pipeline must follow these rules.
Do not change these without a team discussion.

---

## 1. `success` Field Semantics — `false` vs `null`

Every `history[]` entry has a `success` field. The meaning is precise:

| Value | Meaning |
|---|---|
| `true` | The agent attempted its own responsibility and succeeded |
| `false` | The agent attempted its own responsibility and **failed** (the fault is with that agent) |
| `null` | The agent **could not attempt** its task because a prior agent's output was invalid — the fault belongs to the prior agent, not this one |

**Rule:** Never log `success: false` for a downstream agent when the root cause is an upstream agent's bad output. Log `success: null` for the downstream agent and `success: false` for the upstream agent (or log the failure against the upstream agent's most recent history entry).

**Example:** Coding Agent receives a `scoped_files` path that does not exist on disk and is not in a `NEW FILE:` declaration.
- `coding_agent` history entry → `success: null`, `output_summary: "blocked: scoped file does not exist: <path> — Architect error"`
- The pipeline routes straight to Manager Agent; the retry loop (Coding → Testing → Review) is skipped entirely.
- The Manager Agent reads `coding_agent success: null` and correctly does NOT penalise the coding agent's success rate.

---

## 2. Attribution Rule

If a Coding Agent failure is caused by **bad Architect output** (non-existent file path, path already exists for a NEW FILE, file not in `scoped_files`), the failure is attributed to `architect_agent`, not `coding_agent`.

Concretely:
- `coding_agent` logs `success: null` (it was blocked, not failed)
- The task's `status` is set to `"blocked"` immediately
- The normal Coding → Testing → Review retry loop is **skipped**
- The task routes directly to Manager Agent

This means:
- Manager Agent will not increment `coding_agent`'s failure count
- The `retry_count` is **not** incremented (retrying with the same bad Architect output would loop forever)

---

## 3. `NEW FILE:` Convention

When the Architect Agent intends for the Coding Agent to **create a new file** (one that does not yet exist on disk), it must use this exact prefix in the `plan` field:

```
NEW FILE: <path/relative/to/repo/root.py>
```

One `NEW FILE:` line per new file. The path must also appear in `scoped_files`.

### Coding Agent validation logic (pipeline enforces this):

Before implementing the plan, the pipeline checks `scoped_files` against disk:

1. Parse `plan` for all `NEW FILE: <path>` lines → this is the **new-file set**.
2. For each path in `scoped_files`:
   - If the path **exists on disk** → OK to modify.
   - If the path **does not exist on disk** AND is in the new-file set → OK to create.
   - If the path **does not exist on disk** AND is **not** in the new-file set → **Architect error**: block the task.
3. If a path appears in the new-file set but **already exists on disk** → **Architect error**: block the task (Architect told Coding to create a file that already exists).
4. If a path appears in the new-file set but is **not** in `scoped_files` → **Architect error**: block the task (new-file declaration must be consistent with scope).

### On Architect error (any of the above):
- Set `task["status"] = "blocked"`
- Append a `history` entry for `coding_agent` with `success: null` and an explanatory `output_summary`
- Skip Coding → Testing → Review entirely
- Route straight to Manager Agent

---

## 4. Blocked Task Routing

When the Coding Agent detects an Architect error (Section 3 above):

```
task["status"]  = "blocked"
task["current_agent"] = "manager_agent"
history entry   = { agent: "coding_agent", success: null, output_summary: "<reason>" }
```

The pipeline loop exits immediately. Manager Agent still runs (it always runs), records the outcome, and saves the task for the dashboard's "Needs Your Review" tab.

The retry counter (`retry_count`) is **not** incremented — the block is due to upstream bad input, not a code quality failure.

---

## 5. History Entry — Always Write One

Every agent must append a `history` entry, even if it did nothing.

- If an agent **ran and succeeded** → `success: true`
- If an agent **ran and failed** → `success: false`
- If an agent **was blocked** (prior agent's bad output) → `success: null` with a clear `output_summary` explaining why

Never silently skip without writing a history entry. The Manager Agent and dashboard rely on complete history for every task.

---

## 6. Output JSON — No Prose Outside

Every agent's system prompt instructs: **"Output ONLY valid JSON matching this schema. No prose, no explanation outside the JSON."**

The pipeline passes the raw LLM output string directly to `json.loads()`. Any prose before or after the JSON object will cause a parse failure.

---

## 7. Retry Loop Cap

The Coding → Testing → Review retry loop is capped at `MAX_RETRIES = 2`.

- After 2 failed reviews: `status = "blocked"`, escalate to human
- This cap only applies to **review rejections** — a Coding Agent block from Architect errors (Section 3) bypasses the loop entirely and goes straight to Manager, regardless of `retry_count`
