"""
build.py — Orchestrate a full from-scratch app build using batch pipeline runs.

Usage:
    python orchestration/build.py --description "Build a Flask todo app with login, CRUD, SQLite" --name todo_app
    python orchestration/build.py --name todo_app --resume    # resume an interrupted build
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)

from agents.scaffold_agent.scaffold_agent import plan_batches
from orchestration.pipeline import run_pipeline

_DEFAULT_OUTPUT_DIR = os.path.join(_REPO_ROOT, "generated_projects")


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def init_manifest(project_name: str, description: str, batches: list[list[str]]) -> dict:
    """Create a fresh BuildPlan manifest dict."""
    return {
        "project_name": project_name,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "batches": [
            {
                "batch_index": i + 1,
                "files": batch,
                "task_id": None,
                "status": "pending",
                "completed_at": None,
            }
            for i, batch in enumerate(batches)
        ],
    }


def save_manifest(manifest: dict, project_dir: str) -> None:
    """Write *manifest* to {project_dir}/build_manifest.json."""
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, "build_manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def load_manifest(project_dir: str) -> dict | None:
    """Read build_manifest.json from *project_dir*. Returns None if missing."""
    path = os.path.join(project_dir, "build_manifest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Diff apply (non-git, file-write based — new project dirs have no git history)
# ---------------------------------------------------------------------------

def apply_diff_to_disk(code_diff: str, project_dir: str) -> dict:
    """
    Apply a unified diff to *project_dir* by writing file contents directly.

    Strategy:
      1. Try `git apply` if project_dir is inside a git repo.
      2. Fall back to parsing the diff and writing new/modified file content.

    Returns dict with written files and per-file errors:
      {"applied": bool, "method": str, "written": list[str], "failed": dict[str, str]}
    """
    # Write diff to temp file
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(code_diff)
        tmp.close()
        patch_path = tmp.name

        # Try git apply first
        check = subprocess.run(
            ["git", "apply", "--check", patch_path],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if check.returncode == 0:
            apply_res = subprocess.run(
                ["git", "apply", patch_path],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            if apply_res.returncode == 0:
                return {"applied": True, "method": "git", "written": [], "failed": {}}
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    # Fallback: parse diff and write files with per-file error isolation
    return _apply_diff_fallback(code_diff, project_dir)


def _apply_diff_fallback(code_diff: str, project_dir: str) -> dict:
    """
    Parse a unified diff and write new file content to disk.
    Handles +++ b/path header lines and added lines (+).
    Wraps each file write in a try/except to prevent whole-batch crashes.
    """
    current_file: str | None = None
    current_lines: list[str] = []
    written: list[str] = []
    failed: dict[str, str] = {}

    def _flush():
        nonlocal current_file, current_lines
        if current_file and current_lines:
            abs_path = os.path.join(project_dir, current_file)
            try:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write("".join(current_lines))
                written.append(current_file)
            except Exception as exc:  # noqa: BLE001
                failed[current_file] = str(exc)
                print(f"[build]   ⚠ Failed to write file '{current_file}': {exc}")

    for line in code_diff.splitlines(keepends=True):
        # New file header: +++ b/path/to/file.py
        if line.startswith("+++ b/") or line.startswith("+++ "):
            _flush()
            raw_path = re.sub(r"^\+\+\+ [ab]/", "", line).rstrip("\n").strip()
            current_file = raw_path if raw_path != "/dev/null" else None
            current_lines = []
        elif line.startswith("+") and not line.startswith("+++"):
            current_lines.append(line[1:])  # strip leading +
        elif line.startswith(" "):
            current_lines.append(line[1:])  # context line, strip leading space
        # Lines starting with - are deletions — skip for new files

    _flush()
    return {"applied": len(failed) == 0, "method": "direct", "written": written, "failed": failed}


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(
    batch_files: list[str],
    batch_index: int,
    total_batches: int,
    description: str,
    project_name: str,
    project_dir: str,
) -> dict:
    """
    Run one pipeline batch for the given files.
    Returns {"status": "done"|"failed", "task_id": str, "reason": str}
    """
    task_id = f"build_{project_name}_b{batch_index}"
    files_str = ", ".join(batch_files)
    feature_request = (
        f"[Batch {batch_index}/{total_batches}] Generate files: {files_str} "
        f"for project '{project_name}': {description}. "
        f"Mark every file as NEW FILE in the plan. "
        f"Generate complete, working code for each file."
    )

    print(f"[build] Batch {batch_index}/{total_batches}: {files_str}")

    try:
        result = run_pipeline(
            feature_request=feature_request,
            repo_path=project_dir,
            task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "task_id": task_id, "reason": str(exc)}

    final_status = result.get("status", "blocked")
    if final_status in ("approved", "awaiting_human_approval"):
        code_diff = result.get("code_diff")
        file_results = None
        if code_diff:
            try:
                file_results = apply_diff_to_disk(code_diff, project_dir)
                if file_results and file_results.get("failed"):
                    failed_files = list(file_results["failed"].keys())
                    print(f"[build]   ⚠ Diff applied with file write failure(s): {failed_files}")
                else:
                    print(f"[build]   ✓ Diff applied to {project_dir}")
            except Exception as exc:  # noqa: BLE001
                print(f"[build]   ⚠ Diff apply error: {exc}")
        file_statuses = {}
        if file_results:
            for f in file_results.get("written", []):
                file_statuses[f] = "written"
            for f, err in file_results.get("failed", {}).items():
                file_statuses[f] = f"failed: {err}"
        return {
            "status": "done" if not (file_results and file_results.get("failed")) else "partial",
            "task_id": task_id,
            "reason": "" if not (file_results and file_results.get("failed")) else f"File write failures: {file_results['failed']}",
            "file_statuses": file_statuses,
        }
    else:
        return {
            "status": "failed",
            "task_id": task_id,
            "reason": f"pipeline returned status={final_status}",
            "file_statuses": {},
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Dev Team — build a full app from scratch using batch pipeline runs."
    )
    parser.add_argument(
        "--description", "-d",
        help="Plain-English app description (required for new builds).",
    )
    parser.add_argument(
        "--name", "-n",
        required=True,
        help="Project name (snake_case, e.g. todo_app).",
    )
    parser.add_argument(
        "--output-dir",
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Parent directory for generated projects (default: {_DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted build (reads existing build_manifest.json).",
    )
    args = parser.parse_args()

    project_dir = os.path.join(os.path.abspath(args.output_dir), args.name)

    if args.resume:
        manifest = load_manifest(project_dir)
        if not manifest:
            print(f"[build] No existing manifest found at {project_dir}. Use --description to start a new build.")
            sys.exit(1)
        print(f"[build] Resuming build for '{args.name}'...")
    else:
        if not args.description:
            parser.error("--description is required for new builds.")
        print(f"[build] Planning build for '{args.name}'...")
        plan = plan_batches(args.description, args.name)
        manifest = init_manifest(args.name, args.description, plan["batches"])
        os.makedirs(project_dir, exist_ok=True)
        save_manifest(manifest, project_dir)
        print(f"[build] {len(manifest['batches'])} batch(es) planned → {project_dir}")

    total = len(manifest["batches"])
    done_count = 0
    failed_count = 0

    for batch_entry in manifest["batches"]:
        if batch_entry["status"] == "done":
            done_count += 1
            print(f"[build] Batch {batch_entry['batch_index']}/{total}: already done — skipping")
            continue

        try:
            result = run_batch(
                batch_files=batch_entry["files"],
                batch_index=batch_entry["batch_index"],
                total_batches=total,
                description=manifest["description"],
                project_name=manifest["project_name"],
                project_dir=project_dir,
            )

            batch_entry["task_id"] = result.get("task_id")
            batch_entry["status"] = result.get("status", "failed")
            if "file_statuses" in result:
                batch_entry["file_statuses"] = result["file_statuses"]
            batch_entry["completed_at"] = datetime.now(timezone.utc).isoformat()

            if result.get("status") == "done":
                done_count += 1
            elif result.get("status") == "partial":
                done_count += 1
                print(f"[build]   ⚠ Batch {batch_entry['batch_index']} partially completed: {result.get('reason', '')[:80]}")
            else:
                failed_count += 1
                print(f"[build]   ✗ Batch {batch_entry['batch_index']} failed: {result.get('reason', '')[:80]}")
        except Exception as exc:  # noqa: BLE001
            batch_entry["status"] = "failed"
            batch_entry["completed_at"] = datetime.now(timezone.utc).isoformat()
            failed_count += 1
            print(f"[build]   ✗ Batch {batch_entry['batch_index']} error: {exc}")
        finally:
            save_manifest(manifest, project_dir)

    manifest["status"] = "complete" if failed_count == 0 else "failed"
    save_manifest(manifest, project_dir)

    print("\n[build] ── Build Summary ──")
    print(f"[build]   Project:  {project_dir}")
    print(f"[build]   Batches:  {total} total, {done_count} done, {failed_count} failed")
    print(f"[build]   Status:   {manifest['status']}")
    if failed_count > 0:
        print("[build]   Run with --resume to retry failed batches.")
    else:
        print("[build]   ✓ All batches complete. View project in dashboard Tab 4.")
        print("            python -m streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
