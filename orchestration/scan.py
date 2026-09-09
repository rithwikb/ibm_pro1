"""
scan.py — CLI for scanning a repo for flake8 errors and running the pipeline on selected bugs.

Usage:
    # Step 1: Scan repo and write discovered bugs to dashboard/bugs.json
    python orchestration/scan.py --source sample_repo/flaskbb/flaskbb

    # Step 2: Open dashboard, select bugs in Tab 0, click "Mark Selected"

    # Step 3: Run pipeline on selected bugs
    python orchestration/scan.py --source sample_repo/flaskbb/flaskbb --run-selected
"""

from __future__ import annotations

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)

from agents.bug_discovery_agent.bug_discovery_agent import scan_repo, save_bugs, load_bugs
from orchestration.pipeline import run_pipeline

_DEFAULT_BUGS_FILE = os.path.join(_REPO_ROOT, "dashboard", "bugs.json")


# ---------------------------------------------------------------------------
# Scan command
# ---------------------------------------------------------------------------

def cmd_scan(source_dir: str, bugs_path: str) -> None:
    """Scan *source_dir* with flake8 and write results to *bugs_path*."""
    print(f"[scan] Scanning {source_dir} ...")
    bugs = scan_repo(source_dir)

    if not bugs:
        print("[scan] ✓ No flake8 errors found.")
    else:
        print(f"\n[scan] Found {len(bugs)} flake8 error(s) in {source_dir}:")
        for bug in bugs:
            print(f"  [{bug['source']}] {bug['file']}:{bug['line']}  — {bug['error_type']} {bug['message']}")

    save_bugs(bugs, bugs_path)
    print(f"\n[scan] Saved to {bugs_path}")
    if bugs:
        print("[scan] Open dashboard to select bugs:")
        print("         python -m streamlit run dashboard/app.py")


# ---------------------------------------------------------------------------
# Run-selected command
# ---------------------------------------------------------------------------

def cmd_run_selected(bugs_path: str, repo_path: str = "") -> None:
    """Load bugs from *bugs_path*, run pipeline for each bug with status=selected."""
    bugs = load_bugs(bugs_path)
    if not bugs:
        print(f"[scan] No bugs file found at {bugs_path}. Run scan first.")
        return

    selected = [b for b in bugs if b.get("status") == "selected"]
    if not selected:
        print("[scan] No bugs marked as selected. Open the dashboard and select bugs first.")
        print("         python -m streamlit run dashboard/app.py")
        return

    print(f"[scan] Running pipeline for {len(selected)} selected bug(s)...")

    for bug in selected:
        bug_id = bug["bug_id"]
        task_id = f"fix_{bug_id}"
        print(f"\n[scan] ── Bug {bug_id}: {bug['description'][:80]}")
        print(f"[scan]    task_id = {task_id}")

        # Mark as running immediately so a crash leaves a visible trace
        bug["status"] = "running"
        _save_in_place(bugs, bugs_path)

        try:
            result = run_pipeline(
                feature_request=bug["description"],
                repo_path=repo_path,
                task_id=task_id,
            )
            bug["task_id"] = result["task_id"]
            bug["status"] = "done"
            print(f"[scan]    ✓ Done — final status: {result['status']}")
        except Exception as exc:  # noqa: BLE001
            bug["status"] = "done"
            bug["task_id"] = task_id
            print(f"[scan]    ✗ Pipeline error: {exc}")

        _save_in_place(bugs, bugs_path)

    done = sum(1 for b in bugs if b.get("status") == "done")
    print(f"\n[scan] Finished. {done}/{len(selected)} bug(s) processed.")
    print("[scan] Review diffs in dashboard Tab 3:")
    print("         python -m streamlit run dashboard/app.py")


def queue_selected_bugs(bug_ids: list[str], bugs_path: str = _DEFAULT_BUGS_FILE) -> int:
    """Mark the specified bug IDs as selected in bugs.json."""
    bugs = load_bugs(bugs_path)
    count = 0
    for bug in bugs:
        if bug.get("bug_id") in bug_ids:
            bug["status"] = "selected"
            count += 1
    _save_in_place(bugs, bugs_path)
    return count


def _save_in_place(bugs: list[dict], path: str) -> None:
    """Write *bugs* back to *path*, preserving all entries (not just selected)."""
    save_bugs(bugs, path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Dev Team — scan a repo for flake8 errors and fix them."
    )
    parser.add_argument(
        "--source", "-s",
        required=True,
        help="Path to the Python source directory to scan (e.g. sample_repo/flaskbb/flaskbb).",
    )
    parser.add_argument(
        "--run-selected",
        action="store_true",
        help="Run the pipeline on all bugs marked as selected in bugs.json.",
    )
    parser.add_argument(
        "--bugs-file",
        default=_DEFAULT_BUGS_FILE,
        help=f"Path to bugs.json (default: {_DEFAULT_BUGS_FILE}).",
    )
    args = parser.parse_args()

    if args.run_selected:
        cmd_run_selected(args.bugs_file, repo_path=args.source)
    else:
        cmd_scan(args.source, args.bugs_file)


if __name__ == "__main__":
    main()
