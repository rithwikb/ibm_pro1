import os
import unittest
from unittest.mock import patch, MagicMock

from orchestration.iam_auth import call_bob_chat, LLMCallError


class TestCallBobChat(unittest.TestCase):
    def test_raises_on_missing_api_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
            with self.assertRaises(LLMCallError):
                call_bob_chat("hello")

    @patch("orchestration.iam_auth.Groq")
    def test_calls_groq_with_string_prompt(self, MockGroq):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"key": "value"}'
        mock_client.chat.completions.create.return_value = mock_response
        MockGroq.return_value = mock_client

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            result = call_bob_chat("hello world")

        self.assertEqual(result, '{"key": "value"}')
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    @patch("orchestration.iam_auth.Groq")
    def test_includes_system_prompt(self, MockGroq):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response"
        mock_client.chat.completions.create.return_value = mock_response
        MockGroq.return_value = mock_client

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            call_bob_chat("hello", system_prompt="You are helpful")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are helpful")

    @patch("orchestration.iam_auth.Groq")
    def test_wraps_sdk_error_in_llm_call_error(self, MockGroq):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network")
        MockGroq.return_value = mock_client

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            with self.assertRaises(LLMCallError):
                call_bob_chat("hello")


if __name__ == "__main__":
    unittest.main()