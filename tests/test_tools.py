import time
import unittest

from fugu_local.tools import (
    ToolCall,
    ToolExecutionError,
    execute_tool_calls,
    parse_tool_calls,
)


class ParseToolCallsTests(unittest.TestCase):
    def test_parse_json_string_arguments(self):
        calls = parse_tool_calls(
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text":"hi"}'},
                }
            ]
        )
        self.assertEqual(calls[0].name, "echo")
        self.assertEqual(calls[0].arguments, {"text": "hi"})

    def test_parse_object_arguments(self):
        calls = parse_tool_calls(
            [{"type": "function", "function": {"name": "echo", "arguments": {"text": "hi"}}}]
        )
        self.assertEqual(calls[0].arguments, {"text": "hi"})
        self.assertTrue(calls[0].id)

    def test_parse_rejects_invalid_json(self):
        with self.assertRaises(ToolExecutionError):
            parse_tool_calls(
                [{"type": "function", "function": {"name": "echo", "arguments": "{bad"}}]
            )

    def test_parse_rejects_non_function(self):
        with self.assertRaises(ToolExecutionError):
            parse_tool_calls([{"type": "web", "function": {"name": "x"}}])


class ExecuteToolCallsTests(unittest.TestCase):
    def _call(self, name, args, cid="c1"):
        return ToolCall(id=cid, name=name, arguments=args)

    def test_executes_allowed_tool(self):
        results = execute_tool_calls(
            [self._call("echo", {"text": "hello"})],
            allowed_tools=["echo"],
            timeout_seconds=5,
            max_output_chars=100,
        )
        self.assertEqual(results[0].content, "hello")
        self.assertFalse(results[0].error)

    def test_denies_tool_not_in_allowlist(self):
        results = execute_tool_calls(
            [self._call("echo", {"text": "x"})],
            allowed_tools=["lookup_static"],
            timeout_seconds=5,
            max_output_chars=100,
        )
        self.assertIn("not allowed", results[0].error)

    def test_truncates_output(self):
        results = execute_tool_calls(
            [self._call("echo", {"text": "x" * 50})],
            allowed_tools=["echo"],
            timeout_seconds=5,
            max_output_chars=10,
        )
        self.assertEqual(len(results[0].content), 10)
        self.assertTrue(results[0].truncated)

    def test_timeout_is_enforced(self):
        registry = {"slow": lambda args: time.sleep(2) or "done"}
        results = execute_tool_calls(
            [self._call("slow", {})],
            allowed_tools=["slow"],
            timeout_seconds=0.1,
            max_output_chars=100,
            registry=registry,
        )
        self.assertIn("timed out", results[0].error)

    def test_tool_error_is_captured(self):
        results = execute_tool_calls(
            [self._call("echo", {"text": 123})],
            allowed_tools=["echo"],
            timeout_seconds=5,
            max_output_chars=100,
        )
        self.assertTrue(results[0].error)


if __name__ == "__main__":
    unittest.main()
