"""
test_pipeline.py — Unit tests for orchestration/pipeline.py shared helpers.

Tests:
  1. _parse_agent_json — strips markdown fences correctly
  2. _parse_agent_json — raises ValueError on prose-only output
  3. _merge_task — preserves fields absent in llm_output
  4. _merge_task — history: keeps longer list (LLM appended)
  5. _merge_task — history: keeps existing if LLM returned shorter
  6. _safe_agent_call — returns blocked task on parse error
  7. _safe_agent_call — returns blocked task on LLM RuntimeError
  8. _safe_agent_call — merges correctly on valid LLM response
  9. _build_prompt — loads system_prompt.md and appends task JSON
 10. _call_llm — returns '{}' when GROQ_API_KEY is not set (stub mode)
 11. _make_task — produces schema-valid initial task
 12. run_pipeline — end-to-end: all agents mocked, happy path → approved
 13. run_pipeline — retry cap: needs_retry MAX_RETRIES times → blocked
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestration.pipeline import (
    _parse_agent_json,
    _merge_task,
    _safe_agent_call,
    _build_prompt,
    _call_llm,
    _make_task,
    MAX_RETRIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_task(**overrides) -> dict:
    t = _make_task("t_001", "Add rate limiting to login")
    t.update(overrides)
    return t


# ---------------------------------------------------------------------------
# 1–2. _parse_agent_json
# ---------------------------------------------------------------------------

class TestParseAgentJson(unittest.TestCase):
    def test_plain_json(self):
        result = _parse_agent_json('{"acceptance_criteria": ["a", "b"]}')
        self.assertEqual(result["acceptance_criteria"], ["a", "b"])

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = _parse_agent_json(raw)
        self.assertEqual(result["key"], "value")

    def test_strips_plain_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = _parse_agent_json(raw)
        self.assertEqual(result["key"], "value")

    def test_extracts_from_prose(self):
        raw = 'Here is my response: {"key": "value"} and some trailing text.'
        result = _parse_agent_json(raw)
        self.assertEqual(result["key"], "value")

    def test_raises_on_prose_only(self):
        with self.assertRaises(ValueError):
            _parse_agent_json("Here is some prose with no JSON at all.")

    def test_raises_on_empty_string(self):
        with self.assertRaises(ValueError):
            _parse_agent_json("")

    def test_nested_json(self):
        raw = '{"review_result": {"passed": true, "findings": []}}'
        result = _parse_agent_json(raw)
        self.assertTrue(result["review_result"]["passed"])


# ---------------------------------------------------------------------------
# 3–5. _merge_task
# ---------------------------------------------------------------------------

class TestMergeTask(unittest.TestCase):
    def test_overwrites_scalar_fields(self):
        existing = _base_task(status="pending")
        result = _merge_task(existing, {"status": "in_progress"})
        self.assertEqual(result["status"], "in_progress")

    def test_preserves_absent_fields(self):
        existing = _base_task(feature_request="original request")
        result = _merge_task(existing, {"status": "in_progress"})
        self.assertEqual(result["feature_request"], "original request")

    def test_history_keeps_longer_llm_list(self):
        """If LLM appended a history entry, use the longer list."""
        existing = _base_task()
        existing["history"] = [{"agent": "pm_agent", "output_summary": "done", "timestamp": "T", "success": True}]
        llm_output = {
            "history": [
                {"agent": "pm_agent", "output_summary": "done", "timestamp": "T", "success": True},
                {"agent": "architect_agent", "output_summary": "scoped", "timestamp": "T", "success": True},
            ]
        }
        result = _merge_task(existing, llm_output)
        self.assertEqual(len(result["history"]), 2)
        self.assertEqual(result["history"][-1]["agent"], "architect_agent")

    def test_history_keeps_existing_when_llm_shorter(self):
        """If LLM returned a truncated history, keep the existing (longer) one."""
        existing = _base_task()
        existing["history"] = [
            {"agent": "pm_agent", "output_summary": "done", "timestamp": "T", "success": True},
            {"agent": "architect_agent", "output_summary": "scoped", "timestamp": "T", "success": True},
        ]
        llm_output = {"history": [{"agent": "pm_agent", "output_summary": "done", "timestamp": "T", "success": True}]}
        result = _merge_task(existing, llm_output)
        self.assertEqual(len(result["history"]), 2)  # kept existing longer list

    def test_merge_does_not_mutate_existing(self):
        existing = _base_task()
        original_status = existing["status"]
        _merge_task(existing, {"status": "in_progress"})
        self.assertEqual(existing["status"], original_status)  # not mutated


# ---------------------------------------------------------------------------
# 6–8. _safe_agent_call
# ---------------------------------------------------------------------------

class TestSafeAgentCall(unittest.TestCase):
    def test_returns_blocked_on_parse_error(self):
        task = _base_task()
        with patch("orchestration.pipeline._call_llm", return_value="not json at all"):
            result = _safe_agent_call(task, "pm_agent", "some prompt")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["current_agent"], "manager_agent")
        history_agents = [e["agent"] for e in result["history"]]
        self.assertIn("pm_agent", history_agents)
        last = result["history"][-1]
        self.assertIsNone(last["success"])

    def test_returns_blocked_on_llm_runtime_error(self):
        task = _base_task()
        with patch("orchestration.pipeline._call_llm", side_effect=RuntimeError("HTTP 500")):
            result = _safe_agent_call(task, "architect_agent", "some prompt")
        self.assertEqual(result["status"], "blocked")
        last = result["history"][-1]
        self.assertEqual(last["agent"], "architect_agent")
        self.assertIsNone(last["success"])

    def test_merges_on_valid_response(self):
        task = _base_task()
        valid_response = json.dumps({
            "acceptance_criteria": ["criterion 1", "All existing tests still pass"],
            "status": "in_progress",
            "current_agent": "architect_agent",
            "history": [{"agent": "pm_agent", "output_summary": "criteria set", "timestamp": "T", "success": True}],
        })
        with patch("orchestration.pipeline._call_llm", return_value=valid_response):
            result = _safe_agent_call(task, "pm_agent", "some prompt")
        self.assertEqual(result["acceptance_criteria"], ["criterion 1", "All existing tests still pass"])
        self.assertEqual(result["current_agent"], "architect_agent")
        self.assertEqual(result["status"], "in_progress")


# ---------------------------------------------------------------------------
# 9. _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt(unittest.TestCase):
    def test_includes_task_json(self):
        task = _base_task()
        prompt = _build_prompt("pm_agent", task)
        self.assertIn(task["task_id"], prompt)
        self.assertIn(task["feature_request"], prompt)

    def test_includes_system_prompt_content(self):
        task = _base_task()
        prompt = _build_prompt("pm_agent", task)
        # pm_agent/system_prompt.md starts with "# PM Agent"
        self.assertIn("PM Agent", prompt)

    def test_graceful_missing_system_prompt(self):
        """Gracefully handles a missing system_prompt.md (no crash)."""
        task = _base_task()
        prompt = _build_prompt("nonexistent_agent_xyz", task)
        self.assertIn(task["task_id"], prompt)

    def test_ends_with_json_instruction(self):
        task = _base_task()
        prompt = _build_prompt("pm_agent", task)
        self.assertIn("Output only valid JSON", prompt)


# ---------------------------------------------------------------------------
# 10. _call_llm
# ---------------------------------------------------------------------------

class TestCallLlm(unittest.TestCase):
    def test_stub_mode_no_api_key(self):
        """When GROQ_API_KEY is not set, returns '{}' without making HTTP calls."""
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            result = _call_llm("some prompt")
        self.assertEqual(result, "{}")


# ---------------------------------------------------------------------------
# 11. _make_task
# ---------------------------------------------------------------------------

class TestMakeTask(unittest.TestCase):
    def test_required_fields_present(self):
        task = _make_task("t_001", "test feature")
        required = [
            "task_id", "feature_request", "acceptance_criteria", "scoped_files",
            "status", "current_agent", "history", "code_diff",
            "test_results", "review_result", "retry_count",
        ]
        for field in required:
            self.assertIn(field, task, f"Missing field: {field}")

    def test_initial_values(self):
        task = _make_task("t_001", "test feature")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["current_agent"], "pm_agent")
        self.assertEqual(task["retry_count"], 0)
        self.assertIsNone(task["plan"])
        self.assertIsNone(task["code_diff"])
        self.assertEqual(task["history"], [])


# ---------------------------------------------------------------------------
# 12–13. run_pipeline integration (all LLM calls mocked)
# ---------------------------------------------------------------------------

class TestRunPipelineIntegration(unittest.TestCase):
    """
    End-to-end pipeline tests with all external I/O mocked.
    """

    def _make_pm_output(self, task: dict) -> dict:
        t = dict(task)
        t["acceptance_criteria"] = ["Rate-limited to 5/min", "Returns 429 on exceed", "All existing tests still pass"]
        t["status"] = "in_progress"
        t["current_agent"] = "architect_agent"
        t["history"] = [{"agent": "pm_agent", "output_summary": "criteria set", "timestamp": "T", "success": True}]
        return t

    def _make_architect_output(self, task: dict) -> dict:
        t = dict(task)
        t["plan"] = "Add rate limiter to login view."
        t["scoped_files"] = ["flaskbb/auth/views.py"]
        t["status"] = "in_progress"
        t["current_agent"] = "coding_agent"
        h = list(t.get("history", []))
        h.append({"agent": "architect_agent", "output_summary": "1 file scoped", "timestamp": "T", "success": True})
        t["history"] = h
        return t

    def _make_coding_output(self, task: dict) -> dict:
        t = dict(task)
        t["code_diff"] = (
            "--- a/flaskbb/auth/views.py\n+++ b/flaskbb/auth/views.py\n@@ -1,3 +1,4 @@\n line\n line\n line\n+added\n"
        )
        t["status"] = "in_progress"
        t["current_agent"] = "testing_agent"
        h = list(t.get("history", []))
        h.append({"agent": "coding_agent", "output_summary": "diff generated", "timestamp": "T", "success": True})
        t["history"] = h
        return t

    def _make_testing_output(self, task: dict) -> dict:
        t = dict(task)
        t["test_results"] = {"passed": True, "criteria_matched": t.get("acceptance_criteria", []), "failures": []}
        t["status"] = "in_progress"
        t["current_agent"] = "review_agent"
        h = list(t.get("history", []))
        h.append({
            "agent": "testing_agent",
            "output_summary": "3/3 criteria matched",
            "timestamp": "T",
            "success": True,
        })
        t["history"] = h
        return t

    def _make_review_approved(self, task: dict) -> dict:
        t = dict(task)
        t["review_result"] = {"passed": True, "findings": []}
        t["status"] = "approved"
        t["current_agent"] = "review_agent"
        h = list(t.get("history", []))
        h.append({"agent": "review_agent", "output_summary": "approved", "timestamp": "T", "success": True})
        t["history"] = h
        return t

    def _make_review_rejected(self, task: dict) -> dict:
        t = dict(task)
        t["review_result"] = {
            "passed": False,
            "findings": [{"checklist_item": "Missing input validation", "file": "flaskbb/auth/views.py",
                          "line": 5, "severity": "high", "description": "no validation"}]
        }
        t["status"] = "needs_retry"
        t["current_agent"] = "coding_agent"
        h = list(t.get("history", []))
        h.append({"agent": "review_agent", "output_summary": "1 finding", "timestamp": "T", "success": True})
        t["history"] = h
        return t

    def _patch_all(self):
        return [
            patch("orchestration.pipeline.run_pm_agent", side_effect=self._make_pm_output),
            patch(
                "orchestration.pipeline.run_architect_agent",
                side_effect=lambda t, **kw: self._make_architect_output(t),
            ),
            patch("orchestration.pipeline._coding_runner", side_effect=lambda t, **kw: self._make_coding_output(t)),
            patch("orchestration.pipeline.run_testing_agent", side_effect=lambda t, **kw: self._make_testing_output(t)),
            patch("orchestration.pipeline.run_review", side_effect=self._make_review_approved),
            patch("orchestration.pipeline.load_stats", return_value={
                a: {"recent_outcomes": [], "runs": 0, "successes": 0, "rate": None}
                for a in ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]
            }),
            patch("orchestration.pipeline.update_stats", side_effect=lambda task, stats: stats),
            patch("orchestration.pipeline.save_stats"),
            patch("orchestration.pipeline.generate_report", return_value={
                "run_id": "run_t001", "task_id": "t001", "timestamp": "T",
                "agent_stats": [], "underperformers": [], "recommended_action": "none",
                "reasoning": "all ok", "stats_snapshot": {},
            }),
            patch("orchestration.pipeline.run_reflection", return_value=[]),
            patch("orchestration.pipeline._append_run_history"),
            patch("orchestration.pipeline._save_task_for_dashboard"),
        ]

    def test_happy_path_approved(self):
        patches = self._patch_all()
        for p in patches:
            p.start()
        try:
            result = run_pipeline("Add login rate limiting")
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["feature_request"], "Add login rate limiting")
            self.assertEqual(
                result["acceptance_criteria"],
                ["Rate-limited to 5/min", "Returns 429 on exceed", "All existing tests still pass"],
            )
            self.assertEqual(result["scoped_files"], ["flaskbb/auth/views.py"])
            self.assertIn("diff generated", [h["output_summary"] for h in result["history"]])
            self.assertIn("approved", [h["output_summary"] for h in result["history"]])
        finally:
            for p in patches:
                p.stop()

    def test_retry_cap_produces_blocked(self):
        patches = self._patch_all()
        patches[4] = patch("orchestration.pipeline.run_review", side_effect=self._make_review_rejected)
        for p in patches:
            p.start()
        try:
            result = run_pipeline("Feature that keeps failing review")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["retry_count"], MAX_RETRIES)
            self.assertEqual(result["current_agent"], "human")
        finally:
            for p in patches:
                p.stop()

    def test_pm_blocked_halts_immediately(self):
        def _pm_blocked(t):
            t = dict(t)
            t["status"] = "blocked"
            t["current_agent"] = "manager_agent"
            t["history"] = [{"agent": "pm_agent", "output_summary": "blocked", "timestamp": "T", "success": False}]
            return t

        patches = self._patch_all()
        patches[0] = patch("orchestration.pipeline.run_pm_agent", side_effect=_pm_blocked)
        # Architect should never be called
        architect_called = [False]

        def _arch_sentinel(t, **kw):
            architect_called[0] = True
            return t
        patches[1] = patch("orchestration.pipeline.run_architect_agent", side_effect=_arch_sentinel)

        for p in patches:
            p.start()
        try:
            result = run_pipeline("some request")
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(architect_called[0], "Architect should not run after PM blocks")
        finally:
            for p in patches:
                p.stop()

    def test_preflight_guard_catches_syntax_error_before_testing_agent(self):
        """Preflight guard blocks broken syntax and prevents Testing Agent from running."""
        patches = self._patch_all()

        def _bad_syntax_coding(t, **kw):
            t = dict(t)
            t["code_diff"] = "--- a/flaskbb/auth/views.py\n+++ b/flaskbb/auth/views.py\n@@ -1,1 +1,2 @@\n+def broken(\n"
            t["status"] = "in_progress"
            return t

        testing_called = [False]
        def _testing_sentinel(t, **kw):
            testing_called[0] = True
            return t

        patches[2] = patch("orchestration.pipeline._coding_runner", side_effect=_bad_syntax_coding)
        patches[3] = patch("orchestration.pipeline.run_testing_agent", side_effect=_testing_sentinel)

        for p in patches:
            p.start()
        try:
            result = run_pipeline("feature with syntax bug")
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(testing_called[0], "Testing Agent must not be invoked when preflight guard fails")
            preflight_entries = [h for h in result["history"] if "preflight syntax check failed" in h.get("output_summary", "")]
            self.assertTrue(len(preflight_entries) > 0)
            self.assertEqual(preflight_entries[0]["agent"], "coding_agent")
            self.assertFalse(preflight_entries[0]["success"])
        finally:
            for p in patches:
                p.stop()

    def test_empty_scoped_files_blocks_pipeline(self):
        """Empty scoped_files from Architect Agent halts the pipeline immediately."""
        patches = self._patch_all()

        def _empty_arch(t, **kw):
            t = dict(t)
            t["plan"] = "No files needed"
            t["scoped_files"] = []
            t["status"] = "in_progress"
            return t

        coding_called = [False]
        def _coding_sentinel(t, **kw):
            coding_called[0] = True
            return t

        patches[1] = patch("orchestration.pipeline.run_architect_agent", side_effect=_empty_arch)
        patches[2] = patch("orchestration.pipeline._coding_runner", side_effect=_coding_sentinel)

        for p in patches:
            p.start()
        try:
            result = run_pipeline("feature with zero scoped files")
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(coding_called[0], "Coding Agent must not run when scoped_files is empty")
            arch_failures = [h for h in result["history"] if h.get("agent") == "architect_agent" and not h.get("success")]
            self.assertTrue(len(arch_failures) > 0)
        finally:
            for p in patches:
                p.stop()


def run_pipeline(feature_request, repo_path="", task_id=""):
    """Local re-import so test file is self-contained."""
    from orchestration.pipeline import run_pipeline as _rp
    return _rp(feature_request, repo_path=repo_path, task_id=task_id or "t_test")


if __name__ == "__main__":
    unittest.main()
