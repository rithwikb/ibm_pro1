"""
runner.py — Coding Agent orchestration layer.

Executes the full Coding Agent pipeline for one AgentTaskObject:

    Guard chain (Guards 1–7c)
    → Context assembly (file reading, token budget, prompt construction)
    → LLM invocation (pluggable; default stub raises NotImplementedError)
    → Diff post-processing validation (Checks 1–4)
    → Task object assembly
    → validate_task() gate
    → Return output object

Usage
-----
    from agents.coding_agent.runner import run
    output = run(task_object, repo_root="/path/to/repo", llm_fn=my_llm)

The *llm_fn* callable receives a single string (the assembled user-turn prompt)
and must return the raw LLM response string (the unified diff).

Environment variables
---------------------
CODING_AGENT_CONTEXT_BUDGET   int   Token ceiling for scoped-file content (default 100000)
CODING_AGENT_REPO_ROOT        str   Path offset from cwd to repo root (default "")

Exit codes (raised as CodingAgentRoutingError)
-----------------------------------------------
Guard 1 — current_agent != "coding_agent"  → no output object produced
Guard 2 — status not in {"in_progress", "needs_retry"}  → no output object produced
"""

import copy
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Callable, Optional, Set

from agents.coding_agent.validate_task import validate_task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_STATUSES = {"in_progress", "needs_retry"}
_DEFAULT_CONTEXT_BUDGET = 100_000  # tokens
_CHARS_PER_TOKEN = 4               # approximation: 1 token ≈ 4 chars

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CodingAgentRoutingError(RuntimeError):
    """
    Raised by Guard 1 or Guard 2 when the task object was mis-routed.
    No output object is produced; no history entry is appended.
    The caller must surface this as a hard pipeline routing error.
    """


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(
    task: dict,
    repo_root: str = "",
    llm_fn: Optional[Callable[[str], str]] = None,
) -> dict:
    """
    Run the Coding Agent on *task*.

    Parameters
    ----------
    task      : AgentTaskObject dict (input — not mutated).
    repo_root : Absolute or relative path to the repository root.
                Files in scoped_files are resolved as <repo_root>/<path>.
                Defaults to the value of CODING_AGENT_REPO_ROOT env var, then "".
    llm_fn    : Callable[[str], str] — receives the assembled prompt, returns raw diff.
                If None, a stub is used that raises NotImplementedError.

    Returns
    -------
    Output AgentTaskObject dict (always schema-valid; never the same object as input).

    Raises
    ------
    CodingAgentRoutingError
        If Guard 1 or Guard 2 fires (mis-routed task — no output produced).
    jsonschema.ValidationError
        If the assembled output object fails validate_task() — indicates a runner bug.
    """
    repo_root = repo_root or os.environ.get("CODING_AGENT_REPO_ROOT", "")
    context_budget = int(
        os.environ.get("CODING_AGENT_CONTEXT_BUDGET", str(_DEFAULT_CONTEXT_BUDGET))
    )
    if llm_fn is None:
        llm_fn = _llm_stub

    # Work on a deep copy so the caller's object is never mutated.
    task = copy.deepcopy(task)

    # -----------------------------------------------------------------------
    # Guard 1 — current_agent identity
    # -----------------------------------------------------------------------
    if task.get("current_agent") != "coding_agent":
        msg = (
            f"Guard 1: task routed to wrong agent — "
            f"current_agent={task.get('current_agent')!r}, expected 'coding_agent'"
        )
        logger.error(msg)
        raise CodingAgentRoutingError(msg)

    # -----------------------------------------------------------------------
    # Guard 2 — status validity
    # -----------------------------------------------------------------------
    status_in = task.get("status")
    if status_in not in _VALID_STATUSES:
        msg = (
            f"Guard 2: unexpected task status — "
            f"status={status_in!r}, expected one of {_VALID_STATUSES}"
        )
        logger.error(msg)
        raise CodingAgentRoutingError(msg)

    # -----------------------------------------------------------------------
    # Guard 3 — retry-cap gate
    # -----------------------------------------------------------------------
    if status_in == "needs_retry" and task.get("retry_count") == 2:
        logger.info("Guard 3: retry_count cap reached — escalating to human")
        return _build_output(
            task,
            status="awaiting_human_approval",
            current_agent="human",
            code_diff=task.get("code_diff"),          # preserve last attempt
            history_entries=[_history_entry(
                "coding_agent",
                "retry_count cap reached, escalating to human",
                success=False,
            )],
        )

    # -----------------------------------------------------------------------
    # Guard 4 — plan presence
    # -----------------------------------------------------------------------
    plan = task.get("plan")
    if plan is None:
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=[_history_entry(
                "coding_agent",
                "validation failed: plan is null",
                success=False,
            )],
        )
    if not plan.strip():
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=[_history_entry(
                "coding_agent",
                "validation failed: plan is empty",
                success=False,
            )],
        )

    # -----------------------------------------------------------------------
    # Guard 5 — scoped_files non-empty
    # -----------------------------------------------------------------------
    scoped_files_raw: list = task.get("scoped_files", [])
    if not scoped_files_raw:
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=[_history_entry(
                "coding_agent",
                "validation failed: scoped_files is empty",
                success=False,
            )],
        )

    # -----------------------------------------------------------------------
    # Guard 6 — parse NEW FILE: declarations from plan
    # -----------------------------------------------------------------------
    # Returns (intended_new_files, error_output_or_None)
    intended_new_files, architect_fault_output = _parse_new_file_declarations(
        plan, task
    )
    if architect_fault_output is not None:
        return architect_fault_output

    # Normalise all scoped_files paths (for comparison only — originals are preserved).
    scoped_normalised: list = [_normalise_path(p) for p in scoped_files_raw]
    scoped_set: Set[str] = set(scoped_normalised)

    # -----------------------------------------------------------------------
    # Guard 7a — NEW FILE: paths must be in scoped_files
    # -----------------------------------------------------------------------
    history_entries = []
    architect_fault = False
    for new_path in sorted(intended_new_files):   # sorted for determinism
        if new_path not in scoped_set:
            history_entries += _architect_fault_entries(
                f"NEW FILE declaration missing from scoped_files: {new_path}",
                f"execution blocked before coding — NEW FILE declaration missing from scoped_files: {new_path}",
            )
            architect_fault = True
    if architect_fault:
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=history_entries,
        )

    # -----------------------------------------------------------------------
    # Guard 7b — NEW FILE: paths must not already exist on disk
    # -----------------------------------------------------------------------
    history_entries = []
    architect_fault = False
    for new_path in sorted(intended_new_files):
        abs_path = _resolve(repo_root, new_path)
        if os.path.exists(abs_path):
            history_entries += _architect_fault_entries(
                f"NEW FILE declared for already-existing file: {new_path}",
                f"execution blocked before coding — NEW FILE declared for already-existing file: {new_path}",
            )
            architect_fault = True
    if architect_fault:
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=history_entries,
        )

    # -----------------------------------------------------------------------
    # Guard 7c — existing scoped_files must be on disk
    # Files not on disk and not declared NEW FILE in the plan are an Architect fault.
    # -----------------------------------------------------------------------
    history_entries = []
    architect_fault = False
    for norm_path in scoped_normalised:
        if norm_path in intended_new_files:
            continue  # already known new file
        abs_path = _resolve(repo_root, norm_path)
        if not os.path.exists(abs_path):
            history_entries += _architect_fault_entries(
                f"provided nonexistent file path: {norm_path}",
                f"execution blocked before coding — provided nonexistent file path: {norm_path}",
            )
            architect_fault = True
    if architect_fault:
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=history_entries,
        )

    # -----------------------------------------------------------------------
    # Context assembly — read files, apply token budget, build prompt
    # -----------------------------------------------------------------------
    context_result = _assemble_context(
        task=task,
        scoped_normalised=scoped_normalised,
        intended_new_files=intended_new_files,
        repo_root=repo_root,
        context_budget=context_budget,
    )
    if "error" in context_result:
        # read_file failure — Coding Agent failure (not Architect-fault)
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=[_history_entry(
                "coding_agent",
                context_result["error"],
                success=False,
            )],
        )

    prompt: str = context_result["prompt"]
    truncated_count: int = context_result["truncated_count"]
    new_file_count: int = context_result["new_file_count"]
    total_file_count: int = len(scoped_files_raw)

    # -----------------------------------------------------------------------
    # LLM invocation
    # -----------------------------------------------------------------------
    raw_diff: str = llm_fn(prompt)

    # -----------------------------------------------------------------------
    # Diff post-processing validation (Checks 1–4)
    # -----------------------------------------------------------------------
    diff_error = _validate_diff(raw_diff, scoped_set)
    if diff_error:
        return _build_output(
            task,
            status="blocked",
            current_agent="manager_agent",
            code_diff=None,
            history_entries=[_history_entry(
                "coding_agent",
                diff_error,
                success=False,
            )],
        )

    # -----------------------------------------------------------------------
    # Success — assemble output_summary and return Path A object
    # -----------------------------------------------------------------------
    output_summary = _build_success_summary(
        task=task,
        total_file_count=total_file_count,
        new_file_count=new_file_count,
        truncated_count=truncated_count,
    )
    return _build_output(
        task,
        status="in_progress",
        current_agent="testing_agent",
        code_diff=raw_diff,
        history_entries=[_history_entry(
            "coding_agent",
            output_summary,
            success=True,
        )],
    )


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def _assemble_context(
    task: dict,
    scoped_normalised: list,
    intended_new_files: Set[str],
    repo_root: str,
    context_budget: int,
) -> dict:
    """
    Read scoped files, apply proportional token budget, and build the LLM prompt.

    Returns a dict with keys:
        prompt          str
        truncated_count int
        new_file_count  int

    Or on read failure:
        error           str   (output_summary message)
    """
    # Step 1 — collect file records
    file_records = []  # list of {path, content, is_new, char_count}
    for norm_path in scoped_normalised:
        if norm_path in intended_new_files:
            file_records.append({
                "path": norm_path,
                "content": None,
                "is_new": True,
                "char_count": 0,
            })
        else:
            abs_path = _resolve(repo_root, norm_path)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError as exc:
                logger.error("read_file failed for %r: %s", abs_path, exc)
                return {"error": f"validation failed: could not read scoped file: {norm_path}"}
            file_records.append({
                "path": norm_path,
                "content": content,
                "is_new": False,
                "char_count": len(content),
            })

    # Step 2 — apply context budget (proportional truncation)
    existing_records = [r for r in file_records if not r["is_new"]]
    total_tokens = sum(
        math.ceil(r["char_count"] / _CHARS_PER_TOKEN) for r in existing_records
    )
    truncated_count = 0

    if total_tokens > context_budget and existing_records:
        n = len(existing_records)
        per_file_tokens = context_budget // n
        per_file_chars = per_file_tokens * _CHARS_PER_TOKEN
        for rec in existing_records:
            if len(rec["content"]) > per_file_chars:
                original_len = rec["char_count"]
                rec["content"] = (
                    rec["content"][:per_file_chars]
                    + f"\n... [TRUNCATED: original file was {original_len} chars; "
                    f"showing first {per_file_chars} chars] ..."
                )
                truncated_count += 1

    # Step 3 — build prompt
    sections = []

    # Section 1 — Task
    sections.append(
        f"### TASK\nFeature request: {task['feature_request']}"
    )

    # Section 2 — Acceptance criteria
    criteria_lines = "\n".join(
        f"- {c}" for c in task.get("acceptance_criteria", [])
    )
    sections.append(f"### ACCEPTANCE CRITERIA\n{criteria_lines}")

    # Section 3 — Review findings (only if review_result is not null)
    review_result = task.get("review_result")
    if review_result is not None:
        findings = review_result.get("findings", [])
        finding_lines = []
        for f in findings:
            line_ref = str(f.get("line")) if f.get("line") is not None else "?"
            finding_lines.append(
                f"- [{f['severity']}] {f['file']}:{line_ref} — "
                f"{f['description']} ({f['checklist_item']})"
            )
        findings_text = "\n".join(finding_lines)
        sections.append(
            "### REVIEW FINDINGS\n"
            'You are on a retry. Address ALL findings with severity "high" or "critical".\n'
            'Use best judgment on "low" and "medium" findings.\n\n'
            + findings_text
        )

    # Section 4 — Architect plan
    sections.append(f"### ARCHITECT PLAN\n{task['plan']}")

    # Section 5 — Scoped files
    file_blocks = []
    for rec in file_records:
        if rec["is_new"]:
            file_blocks.append(
                f"### FILE: {rec['path']}\n```\n[NEW FILE — to be created]\n```"
            )
        else:
            file_blocks.append(
                f"### FILE: {rec['path']}\n```\n{rec['content']}\n```"
            )
    sections.append("\n\n".join(file_blocks))

    # Section 6 — Diff instructions (verbatim from diff_generation.md Part 1)
    sections.append(_DIFF_INSTRUCTIONS)

    prompt = "\n\n".join(sections)
    new_file_count = sum(1 for r in file_records if r["is_new"])

    return {
        "prompt": prompt,
        "truncated_count": truncated_count,
        "new_file_count": new_file_count,
    }


# Verbatim diff instruction block (from diff_generation.md Part 1).
_DIFF_INSTRUCTIONS = """\
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
   Do not invent changes."""


# ---------------------------------------------------------------------------
# Diff post-processing validation
# ---------------------------------------------------------------------------


def _validate_diff(raw: str, scoped_set: Set[str]) -> Optional[str]:
    """
    Run Checks 1–4 on the raw LLM output.
    Returns None if all checks pass, or an output_summary error string on failure.

    Order: 1 (empty) → 2 (structure) → 4 (path traversal) → 3 (scope).
    Path-traversal must run before scope check so ``../secret.py`` is reported
    as "unsafe path" rather than "out-of-scope file".
    """
    # Check 1 — empty output
    if not raw or not raw.strip():
        return "diff generation failed: LLM returned empty output"

    # Check 0 — template placeholder leak
    if "<path>" in raw or "<start_line>" in raw:
        return "diff generation failed: diff contains placeholder '<path>' instead of actual file"

    # Check 2 — no diff structure
    has_hunk = "@@" in raw
    starts_with_dash = raw.lstrip().startswith("---")
    if not has_hunk or not starts_with_dash:
        return (
            "diff generation failed: output is not a valid unified diff "
            "(no @@ hunk markers)"
        )

    # Check 4 — path traversal in diff headers (runs BEFORE scope check)
    # Inspect all --- and +++ header lines for unsafe paths.
    for match in re.finditer(r"^(?:---|\+\+\+) (.+)$", raw, re.MULTILINE):
        path_str = match.group(1).strip()
        # Exclude the valid sentinel --- /dev/null
        if path_str == "/dev/null":
            continue
        # Strip the a/ or b/ prefix used by unified diff format before checking.
        stripped = re.sub(r"^[ab]/", "", path_str)
        if _is_unsafe_path(stripped):
            return f"diff generation failed: diff contains unsafe path: {stripped}"

    # Check 3 — scope violation
    # Find all "+++ b/<path>" lines; normalise and check against scoped_set.
    matches = list(re.finditer(r"^[ \t]*\+\+\+[ \t]+[ab]/(.+)$", raw, re.MULTILINE))
    if not matches:
        return "diff generation failed: output is not a valid unified diff (no +++ hunk target)"
    for match in matches:
        diff_path = _normalise_path(match.group(1).strip())
        if diff_path not in scoped_set:
            return (
                f"diff generation failed: diff references out-of-scope file: {diff_path}"
            )

    return None  # all checks passed


# ---------------------------------------------------------------------------
# NEW FILE: declaration parsing (Guard 6)
# ---------------------------------------------------------------------------


def _parse_new_file_declarations(plan: str, task: dict):
    """
    Parse plan for 'NEW FILE: <path>' lines.

    Returns (intended_new_files: set, error_output: dict | None).
    If a path-traversal is found, error_output is the assembled Architect-fault output.
    Otherwise error_output is None and intended_new_files is populated.
    """
    intended = set()
    for line in plan.splitlines():
        if not line.startswith("NEW FILE: "):
            continue
        raw_path = line[len("NEW FILE: "):].strip()
        norm_path = _normalise_path(raw_path)
        if not norm_path or _is_unsafe_path(norm_path):
            # Architect-fault: path traversal
            entries = _architect_fault_entries(
                f"NEW FILE path resolves outside repository root: {raw_path}",
                f"execution blocked before coding — NEW FILE path resolves outside repository root: {raw_path}",
            )
            out = _build_output(
                task,
                status="blocked",
                current_agent="manager_agent",
                code_diff=None,
                history_entries=entries,
            )
            return intended, out
        intended.add(norm_path)
    return intended, None


# ---------------------------------------------------------------------------
# Output_summary construction
# ---------------------------------------------------------------------------


def _build_success_summary(
    task: dict,
    total_file_count: int,
    new_file_count: int,
    truncated_count: int,
) -> str:
    review_result = task.get("review_result")
    is_retry = review_result is not None

    if is_retry:
        m = len(review_result.get("findings", []))
        base = f"code_diff revised addressing {m} review findings"
    else:
        if new_file_count > 0:
            base = (
                f"code_diff generated for {total_file_count} file(s) "
                f"including {new_file_count} new file(s)"
            )
        else:
            base = f"code_diff generated for {total_file_count} file(s)"

    if truncated_count > 0:
        base += f" ({truncated_count} file(s) truncated to fit context)"

    return base


# ---------------------------------------------------------------------------
# Output object construction
# ---------------------------------------------------------------------------


def _build_output(
    task: dict,
    status: str,
    current_agent: str,
    code_diff,
    history_entries: list,
) -> dict:
    """
    Assemble the full output AgentTaskObject.

    Copies all immutable fields verbatim from *task*, then writes the
    mutable fields and appends *history_entries* to history.

    Passes the result through validate_task() before returning.
    Raises jsonschema.ValidationError on any schema violation (runner bug).
    """
    output = {
        "task_id": task["task_id"],
        "feature_request": task["feature_request"],
        "acceptance_criteria": task["acceptance_criteria"],
        "scoped_files": task["scoped_files"],
        "status": status,
        "current_agent": current_agent,
        "plan": task.get("plan"),
        "history": list(task.get("history", [])) + history_entries,
        "code_diff": code_diff,
        "test_results": task.get("test_results"),
        "review_result": task.get("review_result"),
        "retry_count": task["retry_count"],
    }
    validate_task(output)
    return output


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def _history_entry(agent: str, output_summary: str, success) -> dict:
    return {
        "agent": agent,
        "output_summary": output_summary,
        "timestamp": _utcnow(),
        "success": success,
    }


def _architect_fault_entries(
    architect_summary: str, coding_summary: str
) -> list:
    ts = _utcnow()
    return [
        {
            "agent": "architect_agent",
            "output_summary": architect_summary,
            "timestamp": ts,
            "success": False,
        },
        {
            "agent": "coding_agent",
            "output_summary": coding_summary,
            "timestamp": ts,
            "success": None,
        },
    ]


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def _normalise_path(path: str) -> str:
    """
    Normalise a relative path per the spec:
    - Strip leading ./
    - Collapse redundant separators
    """
    # Collapse interior //
    path = re.sub(r"/+", "/", path)
    # Strip leading ./
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/") if path == "/" else path


def _is_unsafe_path(path: str) -> bool:
    """
    Return True if the path contains .. as a component or is absolute.
    """
    if os.path.isabs(path):
        return True
    parts = path.replace("\\", "/").split("/")
    return ".." in parts


def _resolve(repo_root: str, relative_path: str) -> str:
    """Join repo_root with a normalised relative path."""
    if repo_root:
        return os.path.join(repo_root, relative_path)
    return relative_path


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return current UTC time as ISO 8601 string, e.g. '2026-08-10T09:10:00Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# LLM stub
# ---------------------------------------------------------------------------


def _llm_stub(prompt: str) -> str:
    """
    Default LLM callable — raises NotImplementedError.

    Replace with a real LLM integration by passing *llm_fn* to run().
    """
    raise NotImplementedError(
        "No LLM function provided. Pass llm_fn=<callable> to runner.run()."
    )
