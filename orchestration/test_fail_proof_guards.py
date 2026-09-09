"""
test_fail_proof_guards.py — Unit tests for Masterpiece Fail-Proof Modules:
- Circuit Breaker (orchestration/circuit_breaker.py)
- Pre-Flight AST Guard (orchestration/preflight_guard.py)
- Multi-Agent Consensus Arbiter (orchestration/consensus_arbiter.py)
- Autonomous Prompt Rollback (agents/reflection_agent/reflection.py)
"""

from __future__ import annotations

import unittest
from orchestration.circuit_breaker import CircuitBreaker, STATE_CLOSED, STATE_OPEN
from orchestration.preflight_guard import validate_python_code, preflight_diff_check
from orchestration.consensus_arbiter import compute_consensus_score
from agents.reflection_agent.reflection import rollback_prompt_to_previous_version


class TestCircuitBreaker(unittest.TestCase):
    def test_normal_closed_call(self):
        cb = CircuitBreaker(failure_threshold=2)
        res, fallback_used = cb.call(lambda: "live_ok", lambda: "fallback_ok")
        self.assertEqual(res, "live_ok")
        self.assertFalse(fallback_used)
        self.assertEqual(cb.state, STATE_CLOSED)

    def test_trips_to_open_on_repeated_failures(self):
        cb = CircuitBreaker(failure_threshold=2)

        def failing_live():
            raise TimeoutError("API timeout")

        # 1st failure
        res, used_fallback = cb.call(failing_live, lambda: "fallback_val")
        self.assertEqual(res, "fallback_val")
        self.assertTrue(used_fallback)

        # 2nd failure trips circuit to OPEN
        res2, used_fallback2 = cb.call(failing_live, lambda: "fallback_val")
        self.assertEqual(res2, "fallback_val")
        self.assertEqual(cb.state, STATE_OPEN)

        # Status check
        status = cb.get_status()
        self.assertEqual(status["state"], STATE_OPEN)
        self.assertTrue(status["is_resilient"])


class TestPreflightGuard(unittest.TestCase):
    def test_validate_valid_python(self):
        valid, err = validate_python_code("def foo(x):\n    return x * 2\n")
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_validate_invalid_syntax(self):
        valid, err = validate_python_code("def broken(\n    return 42\n")
        self.assertFalse(valid)
        self.assertIn("SyntaxError", err)

    def test_preflight_diff_check_valid_diff(self):
        diff = "--- a/test.py\n+++ b/test.py\n@@ -1,1 +1,2 @@\n+x = 10\n+y = 20\n"
        res = preflight_diff_check(diff)
        self.assertTrue(res["passed"])
        self.assertTrue(res["diff_structured"])


class TestConsensusArbiter(unittest.TestCase):
    def test_consensus_score_calculation(self):
        task = {
            "acceptance_criteria": ["Item 1", "Item 2"],
            "scoped_files": ["flaskbb/auth/views.py"],
            "code_diff": "--- a/views.py\n+++ b/views.py\n@@ -1,1 +1,2 @@\n+login()",
            "test_results": {"passed": True},
            "review_result": {"passed": True, "findings": []},
        }
        res = compute_consensus_score(task)
        self.assertGreaterEqual(res["consensus_score_pct"], 90.0)
        self.assertEqual(res["confidence_level"], "HIGH_CONFIDENCE")
        self.assertTrue(res["is_unanimous"])


class TestPromptRollback(unittest.TestCase):
    def test_rollback_handles_zero_version_safely(self):
        success, v, msg = rollback_prompt_to_previous_version("non_existent_agent_xyz")
        self.assertFalse(success)
        self.assertIn("No previous version", msg)


if __name__ == "__main__":
    unittest.main()
