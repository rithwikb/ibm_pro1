# How IBM Bob Technology Was Used in This Project

## Project: AI Dev Team in a Box
**Multi-Agent Software Engineering System with Self-Improvement**

---

## Overview

This project uses IBM Bob as the foundational orchestration platform for a team of nine specialized AI agents that collaboratively process software engineering tasks. IBM Bob's Custom Modes, Agent Mode, and Rules Files provide the structural backbone for agent isolation, permission control, behavioral enforcement, and self-improvement capabilities.

---

## IBM Bob Features Used

### 1. Custom Modes (`.bob/custom_modes.yaml`)

Each of the nine agents runs as its own **isolated IBM Bob Custom Mode**, registered under a unique slug with its own `roleDefinition` and permission `groups`:

| Agent | Bob Mode Slug | Permissions | Purpose |
|-------|---------------|-------------|---------|
| PM Agent | `pm-agent` | `read` | Converts feature requests into acceptance criteria |
| Architect Agent | `architect-agent` | `read` | Scans repos, scopes files, writes implementation plans |
| Coding Agent | `coding-agent` | `read`, `todo` | Generates unified diffs from plan + scoped files |
| Testing Agent | `testing-agent` | `read`, `execute` | Applies patches, runs pytest, interprets results |
| Review Agent | `review-agent` | `read` | Security + quality checklist (SQL injection, secrets, etc.) |
| Manager Agent | `manager-agent` | (pure Python) | Rolling success rate tracking, underperformer detection |
| Reflection Agent | `reflection-agent` | `read`, `edit` | Rewrites failing agents' system prompts |
| Bug Discovery Agent | `bug-discovery` | `read`, `execute` | Scans repos with pytest + flake8 |
| Scaffold Agent | `scaffold-agent` | `read` | Plans from-scratch project file structures |

**Why Custom Modes Matter:** Mode-level isolation ensures the Coding Agent can never write to disk directly — it produces a diff string in a JSON field. The Testing Agent can execute tests but not modify source. This permission model is a security feature enforced at the Bob platform level.

### 2. Agent Mode with Sub-Agents

Bob's Agent Mode with sub-agent support directly maps to our pipeline architecture:

```
Human Request → PM Agent → Architect Agent → Coding Agent → Testing Agent → Review Agent
                                                                              ↓
                                                              Manager Agent → Reflection Agent
                                                              (detects failure)  (rewrites prompt)
```

Each agent is invoked as a Bob sub-agent, receiving the shared `AgentTaskObject` JSON and writing only its own fields via allowlist merge.

### 3. Rules Files (`.bob/rules-{agent}/AGENTS.md`)

Each agent has a dedicated rules file that Bob auto-injects into every session:

- **Coding Agent rules** enforce valid unified diff format (`---`, `+++`, `@@` markers)
- **Review Agent rules** enforce the security checklist (hardcoded secrets, SQL injection, input validation, scope creep)
- **Architect Agent rules** cap scoped files at 5 and require `NEW FILE:` declarations for new files
- **Testing Agent rules** require pytest execution before LLM interpretation

These behavioral contracts are enforced at the Bob platform level — the LLM receives them as mandatory context, not optional prompt text.

### 4. System Prompts (`system_prompt.md`)

Each agent's behavior is defined in a `system_prompt.md` file within its directory. This is the file that the Reflection Agent rewrites when self-improvement is triggered. The prompts are versioned in `prompt_history/{agent}_v{n}.md`, enabling:

- Side-by-side diff viewing in the dashboard
- 1-click rollback to a previous stable version
- Full audit trail of prompt evolution

### 5. LLM Integration via `call_bob_chat()`

All LLM-powered agents use a unified `call_bob_chat()` interface (`orchestration/iam_auth.py`) originally built for IBM watsonx.ai with Granite models and IBM Cloud IAM authentication. The interface:

- Accepts both plain strings and OpenAI-style message lists
- Supports per-agent model override via environment variables
- Handles timeout, retry, and error propagation uniformly
- Is the single point of change for switching LLM backends

### 6. Bob's `read_file` and `update_todo_list` Tools

- **`read_file`** (via `read` permission group) — Used by the Coding Agent to load the full content of scoped files at runtime, rather than embedding them statically in the prompt
- **`update_todo_list`** (via `todo` permission group) — Used for step tracking during agent sessions

---

## Key Workflows Powered by IBM Bob

### Workflow 1: Feature Development Pipeline
1. Human types a feature request
2. PM Agent (Bob mode) generates acceptance criteria
3. Architect Agent (Bob mode) scopes 3-5 files and writes an implementation plan
4. Coding Agent (Bob mode) generates a unified diff
5. Testing Agent (Bob mode) applies the diff, runs pytest, interprets results
6. Review Agent (Bob mode) runs security checklist
7. Human approves/rejects in the dashboard
8. Approved diffs become git commits

### Workflow 2: Self-Improvement Loop
1. Manager Agent (pure Python) detects agent success rate < 60%
2. Reflection Agent (Bob mode) rewrites the failing agent's `system_prompt.md`
3. Rewrite is validated for specificity (must reference failure patterns by name)
4. New prompt is versioned and applied
5. Dashboard graph shows the success rate recover in real time

### Workflow 3: Bug Discovery & Auto-Fix
1. Bug Discovery Agent (pure Python) scans repo with pytest + flake8
2. Bugs displayed in dashboard for human selection
3. Selected bugs flow through the same 5-agent pipeline
4. Approved fixes are applied and verified via git

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     IBM Bob Platform                            │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ PM Agent │→ │Architect │→ │ Coding   │→ │ Testing  │       │
│  │  Mode    │  │  Mode    │  │  Mode    │  │  Mode    │       │
│  │ (read)   │  │ (read)   │  │(read,todo│  │(read,exec│       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│       ↓              ↓             ↓              ↓             │
│  ┌──────────────────────────────────────────────────────┐      │
│  │          Shared AgentTaskObject (JSON Schema)        │      │
│  │     Validated at every hop via task_schema.json       │      │
│  └──────────────────────────────────────────────────────┘      │
│                                                                 │
│  ┌──────────┐        ┌──────────┐       ┌──────────┐          │
│  │ Review   │   ←    │ Manager  │  →    │Reflection│          │
│  │  Mode    │        │  Agent   │       │  Mode    │          │
│  │ (read)   │        │(Python)  │       │(read,edit│          │
│  └──────────┘        └──────────┘       └──────────┘          │
│                                                                 │
│  ┌──────────┐  ┌──────────┐                                   │
│  │Bug Disc. │  │Scaffold  │                                   │
│  │(Python)  │  │  Mode    │                                   │
│  └──────────┘  └──────────┘                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Files Demonstrating IBM Bob Integration

| File | IBM Bob Usage |
|------|--------------|
| `agents/coding_agent/BOB_CONFIGURATION.md` | Complete Bob mode setup and configuration documentation |
| `orchestration/iam_auth.py` | `call_bob_chat()` — unified LLM interface (watsonx.ai / Granite) |
| `agents/*/system_prompt.md` | Per-agent system prompts used as Bob mode role definitions |
| `schemas/task_schema.json` | JSON Schema contract shared across all Bob agent modes |
| `orchestration/pipeline.py` | Pipeline orchestrator wiring all Bob modes in sequence |
| `agents/reflection_agent/reflection.py` | Prompt rewriting engine using Bob's edit capabilities |
| `dashboard/app.py` | 10-tab Streamlit dashboard for monitoring all Bob agent modes |

---

## Test Coverage

- **266 unit and integration tests** — all passing, 0 failures
- **0 flake8 lint errors** across all agents and orchestrators
- **100% mocked LLM isolation** in tests — zero API credits spent
- All Bob agent interactions are tested with deterministic mock responses
