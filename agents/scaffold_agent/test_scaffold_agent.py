"""
test_scaffold_agent.py — Unit tests for scaffold_agent.py.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agents.scaffold_agent.scaffold_agent import (
    _parse_batch_output,
    _stub_plan,
    plan_batches,
)

VALID_RAW = """{
  "project_name": "todo_app",
  "description": "Build a Flask todo app",
  "batches": [
    ["app.py", "models.py", "config.py", "requirements.txt"],
    ["auth/routes.py", "auth/forms.py"],
    ["tests/test_app.py"]
  ]
}"""

OVERSIZED_BATCH_RAW = """{
  "project_name": "todo_app",
  "description": "test",
  "batches": [
    ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"]
  ]
}"""

FENCED_RAW = """```json
{
  "project_name": "todo_app",
  "description": "Build a Flask todo app",
  "batches": [["app.py", "models.py"]]
}
```"""


class TestParseBatchOutput(unittest.TestCase):
    def test_parses_valid_json(self):
        batches = _parse_batch_output(VALID_RAW)
        self.assertEqual(len(batches), 3)
        self.assertIn("app.py", batches[0])

    def test_truncates_oversized_batch(self):
        batches = _parse_batch_output(OVERSIZED_BATCH_RAW)
        self.assertEqual(len(batches[0]), 5)

    def test_strips_markdown_fences(self):
        batches = _parse_batch_output(FENCED_RAW)
        self.assertEqual(batches[0], ["app.py", "models.py"])

    def test_raises_on_missing_batches_key(self):
        with self.assertRaises(ValueError):
            _parse_batch_output('{"project_name": "x"}')

    def test_raises_on_empty_string(self):
        with self.assertRaises(ValueError):
            _parse_batch_output("")

    def test_raises_on_non_json(self):
        with self.assertRaises(ValueError):
            _parse_batch_output("Here is my response, no JSON here.")

    def test_empty_batches_are_dropped(self):
        raw = '{"batches": [["app.py"], [], ["tests/test.py"]]}'
        batches = _parse_batch_output(raw)
        self.assertEqual(len(batches), 2)


class TestStubPlan(unittest.TestCase):
    def test_returns_valid_structure(self):
        plan = _stub_plan("my_app", "Build something")
        self.assertEqual(plan["project_name"], "my_app")
        self.assertIsInstance(plan["batches"], list)
        self.assertTrue(len(plan["batches"]) > 0)

    def test_no_batch_exceeds_five_files(self):
        plan = _stub_plan("my_app", "Build something")
        for batch in plan["batches"]:
            self.assertLessEqual(len(batch), 5)


class TestPlanBatches(unittest.TestCase):
    def test_uses_stub_when_no_api_key(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            result = plan_batches("Build a Flask app", "flask_app")
        self.assertEqual(result["project_name"], "flask_app")
        self.assertIsInstance(result["batches"], list)

    def test_returns_stub_on_parse_error(self):
        with patch("agents.scaffold_agent.scaffold_agent._call_watsonx", return_value="not json"):
            result = plan_batches("Build a Flask app", "flask_app")
        self.assertIsInstance(result["batches"], list)

    def test_returns_llm_batches_on_success(self):
        with patch("agents.scaffold_agent.scaffold_agent._call_watsonx", return_value=VALID_RAW):
            result = plan_batches("Build a Flask todo app", "todo_app")
        self.assertEqual(len(result["batches"]), 3)
        self.assertIn("app.py", result["batches"][0])


if __name__ == "__main__":
    unittest.main()
