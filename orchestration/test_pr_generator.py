"""
test_pr_generator.py — Unit tests for orchestration/pr_generator.py
"""

from __future__ import annotations

import unittest
from orchestration.pr_generator import generate_pr_markdown


class TestPrGenerator(unittest.TestCase):
    def test_generate_pr_markdown_clean_task(self):
        task = {
            "task_id": "test_001",
            "feature_request": "Add password hashing",
            "status": "approved",
            "acceptance_criteria": ["Hash password with bcrypt", "Validate length >= 8"],
            "scoped_files": ["flaskbb/auth/views.py"],
            "code_diff": "--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,2 @@\n-old_pass\n+new_pass\n+hash()",
            "review_result": {"passed": True, "findings": []},
            "history": [{
                "agent": "pm_agent",
                "success": True,
                "output_summary": "criteria set",
                "timestamp": "2026-08-29T00:00:00Z",
            }],
            "retry_count": 0,
        }
        md = generate_pr_markdown(task)
        self.assertIn("# Add password hashing", md)
        self.assertIn("Hash password with bcrypt", md)
        self.assertIn("Security_Audit-PASSED", md)
        self.assertIn("+2 / -1", md)
        self.assertIn("flaskbb/auth/views.py", md)

        def test_generate_pr_markdown_handles_blocked_task_without_review_result(self):
            task = {
                "task_id": "blocked_task",
                "feature_request": "Modify train.py",
                "status": "blocked",
                "acceptance_criteria": [],
                "scoped_files": [],
                "code_diff": None,
                "review_result": None,
                "history": [],
                "retry_count": 0,
            }

            md = generate_pr_markdown(task)

            assert "BLOCKED" in md
            assert "Security Findings" in md

    def test_generate_pr_markdown_with_findings(self):
        task = {
            "task_id": "test_002",
            "feature_request": "Fix SQL login",
            "status": "awaiting_human_approval",
            "acceptance_criteria": ["Sanitize input"],
            "scoped_files": ["flaskbb/auth/views.py"],
            "code_diff": "+line1",
            "review_result": {
                "passed": False,
                "findings": [
                    {
                        "checklist_item": "Injection points",
                        "file": "flaskbb/auth/views.py",
                        "line": 15,
                        "severity": "medium",
                        "description": "Raw string concatenation in query",
                    }
                ],
            },
            "history": [],
            "retry_count": 1,
        }
        md = generate_pr_markdown(task)
        self.assertIn("Security_Findings-1_Flagged", md)
        self.assertIn("Injection points", md)
        self.assertIn("Raw string concatenation", md)


if __name__ == "__main__":
    unittest.main()
