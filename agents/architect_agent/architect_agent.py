"""
architect_agent.py — Architect Agent standalone module
Receives a task with acceptance_criteria set, calls IBM watsonx to produce
a plan and scoped_files list, validates the output, and returns the
updated task dict.

Usage (called from pipeline.py):
    from agents.architect_agent.architect_agent import run_architect_agent
    task = run_architect_agent(task, repo_path="/path/to/sample_repo")

CLI (standalone test):
    python agents/architect_agent/architect_agent.py --task task.json --repo sample_repo/
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    _ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    load_dotenv(_ENV_PATH)
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# watsonx defaults — plain string literals so _call_watsonx() fallbacks are
# never stale import-time values. All env vars are read inside _call_watsonx().
# ──────────────────────────────────────────────────────────────────────────────

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")

# Dirs/files to skip when building the repo file listing
_SKIP_DIRS = {
    ".git", "__pycache__", "venv", ".venv", "env", "node_modules",
    ".tox", "dist", "build", "*.egg-info", ".mypy_cache", ".pytest_cache",
    ".uv-cache", ".cache", ".ruff_cache", ".tx", "instance", "logs",
}
_SKIP_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll"}

# Send common text and structured files to the LLM while keeping binary and
# generated artifacts out of the context.
_LISTING_EXCLUDED_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".ico", ".pdf", ".zip", ".tar", ".gz", ".7z", ".woff", ".woff2",
    ".ttf", ".otf", ".mp3", ".mp4", ".mov", ".avi",
}
_LISTING_EXCLUDED_NAMES = {".env", ".env.local", ".env.production"}
_LISTING_MAX_FILES = 250


# ──────────────────────────────────────────────────────────────────────────────
# Repo file listing
# ──────────────────────────────────────────────────────────────────────────────

def get_repo_file_listing(repo_path: str) -> list:
    """
    Walk repo_path and return all file paths relative to repo_path.
    Excludes .git, __pycache__, venv, node_modules, compiled artifacts.
    """
    listing = []
    repo_path = os.path.abspath(repo_path)
    for root, dirs, files in os.walk(repo_path):
        # Prune skip dirs in-place so os.walk doesn't descend into them
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.endswith(".egg-info")
        ]
        for fname in files:
            _, ext = os.path.splitext(fname)
            if ext in _SKIP_EXTENSIONS:
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, repo_path).replace("\\", "/")
            listing.append(rel_path)
    return sorted(listing)


# ──────────────────────────────────────────────────────────────────────────────
# LLM helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _call_watsonx(prompt: str, system_prompt: str | None = None) -> str:
    """
    POST to IBM Bob / watsonx chat endpoint.
    Returns raw generated text. Raises RuntimeError on failure.
    """
    import sys
    import os as _os
    sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))
    from orchestration.iam_auth import call_bob_chat

    return call_bob_chat(prompt, system_prompt=system_prompt, max_tokens=1500)


def _parse_json_response(raw: str) -> dict:
    """Strip markdown fences, extract first JSON object, parse and return."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        # Fix unescaped backslashes in JSON string values commonly returned by LLMs
        fixed = re.sub(r'\\([^\'"\\/bfnrtu])', r'\\\\\1', cleaned)
        try:
            return json.loads(fixed, strict=False)
        except Exception:
            pass
        # Fallback regex extraction for plan and scoped_files
        plan_m = re.search(r'"plan"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)
        files_m = re.search(r'"scoped_files"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
        if plan_m or files_m:
            plan = plan_m.group(1) if plan_m else ""
            scoped_files = []
            if files_m:
                scoped_files = [
                    f.strip(' "\'\t\r\n')
                    for f in files_m.group(1).split(",")
                    if f.strip(' "\'\t\r\n')
                ]
            return {"plan": plan, "scoped_files": scoped_files}
        raise


def _filter_listing_for_llm(listing: list) -> list:
    """
    Filter the repo listing before sending to the LLM.
    - Keep only files with relevant extensions (.py, .html, etc.)
    - Cap at _LISTING_MAX_FILES entries
    - Prioritize .py files so application code is always in context
    """
    filtered = [
        f for f in listing
        if os.path.splitext(f)[1].lower() not in _LISTING_EXCLUDED_EXTS
        and os.path.basename(f).lower() not in _LISTING_EXCLUDED_NAMES
    ]
    # Prioritize Python source files so core modules are never truncated
    filtered.sort(key=lambda x: (0 if x.endswith(".py") else 1, x))
    if len(filtered) > _LISTING_MAX_FILES:
        filtered = filtered[:_LISTING_MAX_FILES]
    return filtered


def call_bob_architect(
    feature_request: str,
    acceptance_criteria: list,
    file_listing: list,
    previous_error: str = "",
) -> dict:
    """
    Call the Architect Agent LLM.
    Returns the parsed JSON dict from the LLM response.
    """
    system_prompt = _load_system_prompt()
    filtered = _filter_listing_for_llm(file_listing)
    listing_str = "\n".join(filtered) if filtered else "(no repo listing available)"
    feedback_str = (
        f"\n[CRITICAL WARNING FROM PREVIOUS ATTEMPT]: {previous_error}\n"
        "Ensure all scoped files strictly exist in the listing below!\n"
        if previous_error else ""
    )
    user_message = (
        f"Feature request: {feature_request}\n\n"
        f"Acceptance criteria:\n{json.dumps(acceptance_criteria, indent=2)}\n\n"
        f"{feedback_str}"
        f"Repository file listing (relative paths):\n{listing_str}\n\n"
        "IMPORTANT RULES:\n"
        "1. Every file in 'scoped_files' MUST come directly from the Repository File Listing above (exact relative path) "
        "unless it is a NEW FILE declared in the plan with 'NEW FILE: <path>'.\n"
        "2. Do NOT invent filenames like 'controllers.py' or 'login.py' if they do not exist in the listing.\n"
        "3. Output only valid JSON matching the schema: {\"plan\": \"...\", \"scoped_files\": [\"...\"]}. No prose, no markdown fences."
    )
    raw = _call_watsonx(user_message, system_prompt=system_prompt)
    return _parse_json_response(raw)


# ──────────────────────────────────────────────────────────────────────────────
# NEW FILE line parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_new_file_lines(plan: str) -> set:
    """
    Extract every path that follows an exact 'NEW FILE: ' prefix in the plan text.
    Uses strict string matching — 'new file:' (lowercase) or prose like
    "we will create a new file" does NOT match. Only exact 'NEW FILE: <path>'.

    The prefix can appear at the start of a line OR mid-line (e.g. after a sentence).
    Returns a set of normalised path strings (stripped of whitespace, trailing
    punctuation, and leading './').

    Handles None plan gracefully — returns empty set.
    """
    if not plan:
        return set()
    new_files = set()
    TOKEN = "NEW FILE:"
    parts = plan.split(TOKEN)
    for part in parts[1:]:  # skip everything before the first TOKEN
        # Path ends at the next newline (or end of string)
        path_line = part.split("\n")[0].strip()
        path_line = path_line.strip("`'\"").rstrip(".,;")
        tokens = path_line.split()
        if tokens:
            first_tok = tokens[0].strip("`'\"").rstrip(".,;:")
            if ("." in first_tok or "/" in first_tok or "\\" in first_tok) and not first_tok.startswith("<"):
                path_line = first_tok
            else:
                for sep in (" - ", " — ", " : ", " (", " -- "):
                    if sep in path_line:
                        sub = path_line.split(sep)[0].strip("`'\" ")
                        if sub:
                            path_line = sub
                            break
        # Normalise leading ./ so 'NEW FILE: ./foo.py' matches scoped_files entry 'foo.py'
        while path_line.startswith("./"):
            path_line = path_line[2:]
        # Ignore placeholder tokens that the LLM echoes from prompt instructions
        if path_line and not (path_line.startswith("<") or "<path>" in path_line or "e.g." in path_line.lower()):
            new_files.add(path_line)
    return new_files


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_architect_output(
    output: dict,
    repo_path: str,
) -> tuple:
    """
    Validate Architect Agent output. Returns (is_valid: bool, error_message: str).

    Checks:
      a. plan and scoped_files both present and non-empty
      b. scoped_files has at most 5 entries
      c. every NEW FILE path is also in scoped_files
      d. every NEW FILE path does NOT already exist on disk
      e. every scoped_files entry NOT in NEW FILE set DOES exist on disk
    """
    plan = output.get("plan", "")
    scoped_files = output.get("scoped_files", [])

    # a. Presence checks
    if not isinstance(plan, str) or not plan.strip():
        return False, "plan is missing or empty"
    if not isinstance(scoped_files, list) or len(scoped_files) == 0:
        return False, "scoped_files is missing or empty"

    # b. 5-file cap
    if len(scoped_files) > 5:
        return False, f"scoped_files has {len(scoped_files)} entries — maximum is 5"

    new_files = parse_new_file_lines(plan)
    scoped_set = set(scoped_files)

    # c. Every NEW FILE entry must be in scoped_files
    for nf in new_files:
        if nf not in scoped_set:
            return (
                False,
                f"NEW FILE '{nf}' appears in plan but is missing from scoped_files",
            )

    # d & e. Disk existence checks (only if repo_path is provided and exists)
    if repo_path and os.path.isdir(repo_path):
        for path in scoped_files:
            full = os.path.join(repo_path, path)
            if path in new_files:
                # NEW FILE — must NOT already exist
                if os.path.exists(full):
                    return (
                        False,
                        f"NEW FILE '{path}' already exists on disk — remove the NEW FILE: marker",
                    )
            else:
                # Existing file — must exist on disk
                if not os.path.exists(full):
                    return (
                        False,
                        f"scoped_files path '{path}' does not exist on disk and is not marked NEW FILE in plan",
                    )

    return True, ""


# ──────────────────────────────────────────────────────────────────────────────
# Main agent entry point
# ──────────────────────────────────────────────────────────────────────────────

def _append_history(task: dict, summary: str, success: bool) -> dict:
    task["history"].append({
        "agent": "architect_agent",
        "output_summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
    })
    return task


def run_architect_agent(task: dict, repo_path: str = "") -> dict:
    """
    Main entry point called by pipeline.py.

    Steps:
      a. Build repo file listing (if repo_path provided)
      b. Call Bob Architect with feature_request, acceptance_criteria, listing
      c. Validate output (plan, scoped_files, NEW FILE consistency, disk existence)
      d. If valid:
           - Enforce 5-file cap (truncate silently if LLM exceeds it despite prompt)
           - Set task["plan"], task["scoped_files"]
           - Set task["current_agent"] = "coding_agent"
           - Append history entry success=True
      e. If invalid:
           - Set task["status"] = "blocked"
           - Append history entry success=False with specific error
      f. Return updated task
    """
    print("[architect_agent] Scoping files and writing implementation plan...")
    task["current_agent"] = "architect_agent"

    try:
        # Build file listing
        listing = get_repo_file_listing(repo_path) if repo_path and os.path.isdir(repo_path) else []

        # Detect new/empty project directory (Phase 2 build-from-scratch mode)
        _is_new_project = not listing and repo_path and os.path.isdir(repo_path)
        if _is_new_project:
            print("[architect_agent] New/empty project directory — all scoped files are NEW FILEs")
        elif not listing:
            print("[architect_agent] WARNING: no repo listing available — LLM will scope without it")

        # Inject new-project hint into the feature request when needed
        feature_request = task["feature_request"]
        fr_lower = feature_request.lower()
        if _is_new_project and "NEW FILE" not in feature_request:
            feature_request = (
                feature_request
                + "\n\n[IMPORTANT] This is a brand-new project directory. "
                "Every file in scoped_files does not exist yet. "
                "Prefix every file path in the plan with 'NEW FILE:' (e.g. 'NEW FILE: app.py')."
            )
        elif any(kw in fr_lower for kw in ("create", "new file", "add file", "make a file")) and "NEW FILE" not in feature_request:
            feature_request = (
                feature_request
                + "\n\n[IMPORTANT] If creating new files, you MUST declare each in the plan with 'NEW FILE: <path>' (e.g. 'NEW FILE: test.py') and list it in scoped_files."
            )

        # Check if there was a previous error from attempt 1 in history
        prev_err = ""
        for h in reversed(task.get("history", [])):
            if h.get("agent") == "architect_agent" and not h.get("success"):
                prev_err = h.get("output_summary", "")
                break

        output = call_bob_architect(
            feature_request,
            task.get("acceptance_criteria", []),
            listing,
            previous_error=prev_err,
        )

        # Sanitize scoped_files: strip quotes, whitespace, and 'NEW FILE:' prefixes
        if isinstance(output.get("scoped_files"), list):
            cleaned_scoped = []
            for p in output["scoped_files"]:
                if not isinstance(p, str):
                    continue
                clean_p = re.sub(r'^new\s+file:\s*', '', p, flags=re.IGNORECASE).strip()
                clean_p = clean_p.strip('"').strip("'").strip()
                while clean_p.startswith("./"):
                    clean_p = clean_p[2:]
                clean_p = clean_p.replace("\\", "/")
                if clean_p and clean_p not in cleaned_scoped:
                    cleaned_scoped.append(clean_p)
            output["scoped_files"] = cleaned_scoped

        # Smart autocorrect for common LLM path hallucinations in existing directories
        if repo_path and os.path.isdir(repo_path) and isinstance(output.get("scoped_files"), list):
            new_file_set = parse_new_file_lines(output.get("plan", ""))
            corrected_files = []
            for path in output["scoped_files"]:
                full = os.path.join(repo_path, path)
                if os.path.exists(full) or path in new_file_set:
                    corrected_files.append(path)
                    continue
                # Try finding closest match in same directory
                dir_part = os.path.dirname(path)
                full_dir = os.path.join(repo_path, dir_part)
                if os.path.isdir(full_dir):
                    candidates = [
                        os.path.join(dir_part, f).replace("\\", "/")
                        for f in os.listdir(full_dir)
                        if f.endswith(".py") and not f.startswith("__")
                    ]
                    matched = next((c for c in candidates if "views" in c and any(k in path for k in ("view", "controller", "route", "handler"))), None)
                    if matched:
                        print(f"[architect_agent] Autocorrected out-of-tree '{path}' -> '{matched}'")
                        corrected_files.append(matched)
                        if "plan" in output and path in output["plan"]:
                            output["plan"] = output["plan"].replace(path, matched)
                        continue
                corrected_files.append(path)
            output["scoped_files"] = corrected_files

        # Ensure plan is non-empty
        if not output.get("plan") or not str(output.get("plan")).strip():
            output["plan"] = f"Implement changes for: {feature_request}"

        # Auto-declare NEW FILE: marker for files explicitly requested to be created
        if repo_path and os.path.isdir(repo_path) and isinstance(output.get("scoped_files"), list):
            new_file_set = parse_new_file_lines(output.get("plan", ""))
            wants_new = any(kw in fr_lower for kw in ("create", "new file", "add file", "make a file"))
            for path in list(output["scoped_files"]):
                full = os.path.join(repo_path, path)
                if not os.path.exists(full) and path not in new_file_set:
                    path_name = os.path.basename(path).lower()
                    if wants_new or path_name in fr_lower:
                        print(f"[architect_agent] Adding NEW FILE declaration to plan for: {path}")
                        output["plan"] = (output.get("plan", "") + f"\nNEW FILE: {path}").strip()
                        new_file_set.add(path)

            # Auto-sync declared NEW FILE entries from plan into scoped_files
            for nf in new_file_set:
                if nf not in output["scoped_files"] and len(output["scoped_files"]) < 5:
                    print(f"[architect_agent] Auto-adding declared NEW FILE '{nf}' to scoped_files")
                    output["scoped_files"].append(nf)

        # Fallback if scoped_files was empty
        if not output.get("scoped_files") and listing:
            kw = feature_request.lower()
            keywords = [w for w in kw.split() if len(w) > 3]
            if any(term in kw for term in ("login", "logout", "auth", "password", "register", "session", "user")):
                keywords.extend(["auth", "views"])
            if any(term in kw for term in ("endpoint", "route", "view", "api", "handler")):
                keywords.extend(["views", "routes", "api"])

            matching = [
                f for f in listing
                if f.endswith(".py")
                and not f.startswith(("tests", "flaskbb/migrations", "migrations", ".pytest"))
                and any(w in f.lower() for w in keywords)
            ]
            # Prioritize view/route files for endpoints
            if any(term in kw for term in ("endpoint", "login", "auth", "view", "rate")):
                view_matches = [m for m in matching if "views.py" in m or "views" in m]
                if view_matches:
                    matching = view_matches + [m for m in matching if m not in view_matches]
            if matching:
                output["scoped_files"] = matching[:2]
                print(f"[architect_agent] Fallback populated scoped_files from listing: {output['scoped_files']}")

        # Hard-enforce 5-file cap before validation (catch LLM over-scoping)
        if isinstance(output.get("scoped_files"), list) and len(output["scoped_files"]) > 5:
            print(
                f"[architect_agent] WARNING: LLM returned {len(output['scoped_files'])} files — "
                "truncating to 5."
            )
            output["scoped_files"] = output["scoped_files"][:5]

        is_valid, error_msg = validate_architect_output(output, repo_path)

        if not is_valid:
            raise ValueError(f"Architect output validation failed: {error_msg}")

        task["plan"] = output["plan"]
        task["scoped_files"] = output["scoped_files"]
        task["status"] = "in_progress"
        task["current_agent"] = "coding_agent"
        task = _append_history(
            task,
            f"plan written, {len(output['scoped_files'])} files scoped",
            True,
        )
        print(f"[architect_agent] Plan written, {len(output['scoped_files'])} files scoped.")

    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as e:
        err_str = str(e)
        if "401" in err_str or "authentication_token_not_valid" in err_str:
            print("[architect_agent] WARNING: LLM auth failed — falling back to repo file listing.")
            listing = get_repo_file_listing(repo_path) if repo_path and os.path.isdir(repo_path) else []
            task["plan"] = task.get("feature_request", "")
            task["scoped_files"] = listing[:5] if listing else []
            task["status"] = "in_progress"
            task["current_agent"] = "coding_agent"
            task = _append_history(
                task,
                f"plan written, {len(task['scoped_files'])} files scoped (watsonx auth fallback)",
                True,
            )
            return task
        print(f"[architect_agent] ERROR: {e}")
        task["status"] = "blocked"
        task["current_agent"] = "human"
        error_str = str(e)[:120]
        task = _append_history(task, f"architect_agent error: {error_str}", False)

    return task


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Architect Agent — scope files and write plan")
    parser.add_argument("--task", required=True, help="Path to task JSON file")
    parser.add_argument("--repo", default="", help="Path to sample repo root (for file listing)")
    args = parser.parse_args()

    with open(args.task, "r", encoding="utf-8") as f:
        task = json.load(f)
    result = run_architect_agent(task, repo_path=args.repo)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
