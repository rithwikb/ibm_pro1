"""
bug_discovery_agent.py — Pure-Python (no LLM) agent that scans a source directory
for flake8 errors and returns a structured list of bug dicts.

Usage:
    from agents.bug_discovery_agent.bug_discovery_agent import scan_repo, save_bugs, load_bugs

    bugs = scan_repo("sample_repo/flaskbb/flaskbb")
    save_bugs(bugs, "dashboard/bugs.json")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# flake8 availability
# ---------------------------------------------------------------------------


def _ensure_flake8() -> None:
    """Install flake8 into the current Python env if it is not already available."""
    result = subprocess.run(
        [sys.executable, "-m", "flake8", "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[bug_discovery_agent] flake8 not found — installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "flake8"],
            check=True,
        )


# ---------------------------------------------------------------------------
# Scanners (pure Python, no LLM)
# ---------------------------------------------------------------------------

def scan_flake8(source_dir: str) -> list[dict]:
    """
    Run flake8 on *source_dir* and return a list of raw bug dicts.
    Never crashes — returns [] if flake8 is not found or has no output.
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "flake8",
                "--exclude=.venv,venv,.git,.uv-cache,__pycache__",
                source_dir,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    bugs: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        file_path, line_no, col_no, rest = parts[0], parts[1], parts[2], parts[3]
        rest = rest.strip()
        code_parts = rest.split(" ", 1)
        code = code_parts[0]
        message = code_parts[1] if len(code_parts) > 1 else ""

        try:
            line_int = int(line_no)
            col_int = int(col_no)
        except ValueError:
            line_int, col_int = 0, 0

        norm_file = os.path.normpath(file_path.strip()).replace("\\", "/")
        bugs.append({
            "file": norm_file,
            "line": line_int,
            "col": col_int,
            "error_type": code,
            "message": message.strip(),
            "raw_output": line.strip(),
        })
    return bugs


def scan_pytest(source_dir: str) -> list[dict]:
    """
    Run pytest on *source_dir* and return a list of raw bug dicts.
    Catches FAILED and ERROR lines using robust regex extraction.
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--ignore=.venv",
                "--ignore=.uv-cache",
                source_dir,
                "--tb=short",
                "-q",
                "--no-header",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    # If pytest is not installed, return []
    if "No module named pytest" in result.stderr or result.returncode == 127:
        return []

    bugs: list[dict] = []
    pattern = re.compile(r"^(FAILED|ERROR)\s+([^:\s]+)(?:::(\S+))?\s*(?:-\s*(.*))?$")

    for raw_line in result.stdout.splitlines() + result.stderr.splitlines():
        line_str = raw_line.strip()
        m = pattern.match(line_str)
        if m:
            error_type = m.group(1)
            file_path = m.group(2)
            reason = m.group(4) or "Test failure / execution error"

            norm_file = os.path.normpath(file_path.strip()).replace("\\", "/")
            bugs.append({
                "file": norm_file,
                "line": 0,
                "col": 0,
                "error_type": error_type,
                "message": reason.strip(),
                "raw_output": line_str,
            })

    return bugs


# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------

def description_from_flake8(file: str, line: int, code: str, message: str) -> str:
    """Return a plain-English description suitable as a run_pipeline() feature request."""
    return f"Fix flake8 error {code} in {file} line {line} — {message}"


def description_from_pytest(raw_output: str) -> str:
    """Return a plain-English description for a failing test."""
    match = re.search(r'(?:FAILED|ERROR) ([^\s]+)', raw_output)
    if match:
        path_and_test = match.group(1)
        test_name = path_and_test.split("::")[-1]
        return f"Fix failing test: {test_name}"
    return "Fix failing test"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan_repo(source_dir: str) -> list[dict]:
    """
    Scan *source_dir* with flake8 and pytest and return a list of complete bug dicts
    ready for saving to dashboard/bugs.json.
    """
    raw_flake8 = scan_flake8(source_dir)
    raw_pytest = scan_pytest(source_dir)

    bugs: list[dict] = []

    # Process flake8 bugs
    for raw in raw_flake8:
        bugs.append({
            "source": "flake8",
            "file": raw["file"],
            "line": raw["line"],
            "col": raw["col"],
            "error_type": raw["error_type"],
            "message": raw["message"],
            "description": description_from_flake8(
                raw["file"], raw["line"], raw["error_type"], raw["message"]
            ),
            "raw_output": raw["raw_output"],
            "status": "discovered",
            "task_id": None,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        })

    # Process pytest bugs
    for raw in raw_pytest:
        bugs.append({
            "source": "pytest",
            "file": raw["file"],
            "line": raw["line"],
            "col": raw["col"],
            "error_type": raw["error_type"],
            "message": raw["message"],
            "description": description_from_pytest(raw["raw_output"]),
            "raw_output": raw["raw_output"],
            "status": "discovered",
            "task_id": None,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        })

    # Assign sequential bug IDs
    for i, bug in enumerate(bugs):
        bug["bug_id"] = f"bug_{i + 1:03d}"

    return bugs


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def save_bugs(bugs: list[dict], output_path: str) -> None:
    """Write *bugs* list to *output_path* as pretty-printed JSON."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(bugs, f, indent=2)
    except OSError as exc:
        print(f"[bug_discovery_agent] Warning: could not write {output_path} ({exc})", file=sys.stderr)


def load_bugs(path: str) -> list[dict]:
    """Load bugs from *path*. Returns [] if the file is missing or unreadable."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []
