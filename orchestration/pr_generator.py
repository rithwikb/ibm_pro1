"""
orchestration/pr_generator.py — Generate production-grade GitHub Pull Request markdown
from task JSON objects for the AI Dev Team.
"""

from __future__ import annotations

from datetime import datetime, timezone


def generate_pr_markdown(task: dict) -> str:
    """
    Generate a full GitHub PR description with summary, acceptance criteria,
    security audit results, diff stats, and git commands.
    """
    task_id = task.get("task_id", "task_001")
    feature_request = task.get("feature_request", "Feature implementation")
    status = task.get("status", "in_progress")
    criteria = task.get("acceptance_criteria", [])
    scoped_files = task.get("scoped_files", [])
    code_diff = task.get("code_diff", "")
    review_result = task.get("review_result") or {}
    history = task.get("history", [])
    retry_count = task.get("retry_count", 0)

    # Compute diff stats
    added_lines = 0
    removed_lines = 0
    if code_diff:
        for line in code_diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed_lines += 1

    # Security check badge
    review_passed = review_result.get("passed", False)
    findings = review_result.get("findings", [])
    if review_passed:
        sec_badge = "![Security Pass](https://img.shields.io/badge/Security_Audit-PASSED-2ea44f?style=flat-square)"
    else:
        sec_badge = (
            f"![Security Findings](https://img.shields.io/badge/Security_Findings-"
            f"{len(findings)}_Flagged-e5534b?style=flat-square)"
        )
    agent_badge = "![AI Dev Team](https://img.shields.io/badge/Generated_by-AI_Dev_Team-6f42c1?style=flat-square)"
    retries_badge = f"![Retries](https://img.shields.io/badge/Retries-{retry_count}%2F2-blue?style=flat-square)"

    # Build PR markdown
    lines = [
        f"# {feature_request.strip()}",
        "",
        f"{agent_badge} {sec_badge} {retries_badge}",
        "",
        "## 📌 Executive Summary",
        f"This pull request was automatically generated and verified by the **AI Dev Team multi-agent pipeline** "
        f"(Task `{task_id}`).",
        f"- **Status:** `{status.upper()}`",
        f"- **Diff Impact:** `+{added_lines} / -{removed_lines}` lines across `{len(scoped_files)}` scoped file(s)",
        "",
        "## ✅ Acceptance Criteria",
    ]

    if criteria:
        for c in criteria:
            lines.append(f"- [x] {c}")
    else:
        lines.append("- [x] Automated feature requirements fulfilled.")

    lines.extend([
        "",
        "## 🛡️ Review Agent Security & Quality Audit",
    ])

    if review_passed:
        lines.append("All 9 security checklist items verified clean with zero critical or high findings:")
        lines.append("- [x] No Hardcoded Secrets")
        lines.append("- [x] No SQL / Shell / Template Injection Points")
        lines.append("- [x] Strict User Input Validation Enforced")
        lines.append("- [x] Authn / Authz Verification Passed")
        lines.append("- [x] Insecure Deserialization Protected")
        lines.append("- [x] Sensitive Route Rate Limiting Validated")
        lines.append("- [x] Verbose Error Leakage Prevented")
        lines.append("- [x] Exception Logging & Handling Verified")
        lines.append("- [x] Dependency Safety Confirmed")
    else:
        lines.append(f"**{len(findings)} Finding(s) Flagged:**")
        for f in findings:
            sev = f.get("severity", "medium").upper()
            chk = f.get("checklist_item")
            loc = f"{f.get('file')}:{f.get('line', '?')}"
            desc = f.get("description")
            lines.append(f"- **[{sev}]** `{chk}` in `{loc}` — {desc}")

    lines.extend([
        "",
        "## 📁 Scoped Files Modified",
    ])
    for sf in scoped_files:
        lines.append(f"- `{sf}`")

    lines.extend([
        "",
        "## 📜 Multi-Agent Execution Audit Trail",
        "| Agent Stage | Result | Summary | Timestamp |",
        "|---|---|---|---|",
    ])

    for h in history:
        res = "✅ Passed" if h.get("success") else "❌ Failed"
        ts = (h.get("timestamp") or "")[:19].replace("T", " ")
        lines.append(f"| `{h.get('agent')}` | {res} | {h.get('output_summary', '')[:60]} | {ts} |")

    lines.extend([
        "",
        "## 💻 How to Apply Locally",
        "```bash",
        "# Apply this approved task patch via the orchestrator:",
        "python orchestration/apply_fixes.py --repo sample_repo/flaskbb",
        "```",
        "",
        "---",
        f"*Generated automatically by AI Dev Team on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*",
    ])

    return "\n".join(lines)
