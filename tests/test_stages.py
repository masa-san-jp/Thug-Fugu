import unittest

from fugu_local.stages import (
    STAGE_JSON_INSTRUCTION,
    STAGE_NAMES,
    Claim,
    parse_stage_output,
    stage_system_prompt,
)


class ParseStageOutputTests(unittest.TestCase):
    def test_valid_json(self):
        text = (
            '{"answer": "42", "claims": [{"text": "a", "evidence": "b", '
            '"confidence": 0.9, "verification": "passed"}], '
            '"assumptions": ["x"], "uncertainties": ["y"], '
            '"requested_checks": ["z"], "subproblems": ["p1", "p2"]}'
        )
        result = parse_stage_output("planner", "planner-role", text)

        self.assertEqual(result.answer, "42")
        self.assertEqual(result.claims, [Claim("a", "b", 0.9, "passed")])
        self.assertEqual(result.assumptions, ["x"])
        self.assertEqual(result.uncertainties, ["y"])
        self.assertEqual(result.requested_checks, ["z"])
        self.assertEqual(result.subproblems, ["p1", "p2"])
        self.assertIsNone(result.parse_error)
        self.assertEqual(result.stage, "planner")
        self.assertEqual(result.role, "planner-role")
        self.assertEqual(result.raw_text, text)

    def test_json_inside_code_fence(self):
        text = 'Here is my answer:\n```json\n{"answer": "fenced"}\n```\nThanks.'
        result = parse_stage_output("solver", "solver-role", text)

        self.assertEqual(result.answer, "fenced")
        self.assertIsNone(result.parse_error)

    def test_broken_json_falls_back_without_raising(self):
        text = '{"answer": "unterminated string'
        result = parse_stage_output("solver", "solver-role", text)

        self.assertEqual(result.answer, text)
        self.assertEqual(result.claims, [])
        self.assertIsNotNone(result.parse_error)
        self.assertEqual(result.raw_text, text)

    def test_non_json_text_falls_back_without_raising(self):
        text = "I think the answer is probably 42, but I'm not fully sure."
        result = parse_stage_output("writer", "writer-role", text)

        self.assertEqual(result.answer, text)
        self.assertEqual(result.claims, [])
        self.assertIsNotNone(result.parse_error)

    def test_unknown_fields_are_ignored(self):
        text = '{"answer": "ok", "unexpected_field": "should be ignored", "another": 123}'
        result = parse_stage_output("writer", "writer-role", text)

        self.assertEqual(result.answer, "ok")
        self.assertIsNone(result.parse_error)

    def test_malformed_claim_entries_are_dropped_not_fatal(self):
        text = (
            '{"answer": "ok", "claims": ['
            '{"text": "good"}, '
            '"not-an-object", '
            '{"evidence": "no text field"}, '
            '{"text": "bad-confidence", "confidence": "not-a-number"}'
            "]}"
        )
        result = parse_stage_output("critic", "critic-role", text)

        self.assertEqual(len(result.claims), 2)
        self.assertEqual(result.claims[0], Claim("good"))
        self.assertEqual(result.claims[1].text, "bad-confidence")
        self.assertEqual(result.claims[1].confidence, 0.0)

    def test_invalid_verification_falls_back_to_required(self):
        text = '{"answer": "ok", "claims": [{"text": "x", "verification": "maybe"}]}'
        result = parse_stage_output("verifier", "verifier-role", text)

        self.assertEqual(result.claims[0].verification, "required")

    def test_non_string_answer_field_falls_back_to_raw_text(self):
        text = '{"answer": 42, "claims": []}'
        result = parse_stage_output("solver", "solver-role", text)

        self.assertEqual(result.answer, text)


class StagePromptTests(unittest.TestCase):
    def test_every_stage_name_has_a_prompt(self):
        for stage in STAGE_NAMES:
            with self.subTest(stage=stage):
                prompt = stage_system_prompt(stage)
                self.assertIsInstance(prompt, str)
                self.assertGreater(len(prompt), 0)

    def test_prompt_includes_the_shared_json_instruction(self):
        for stage in STAGE_NAMES:
            with self.subTest(stage=stage):
                self.assertIn(STAGE_JSON_INSTRUCTION, stage_system_prompt(stage))

    def test_stage_names_cover_the_seven_documented_stages(self):
        self.assertEqual(
            set(STAGE_NAMES),
            {
                "planner",
                "solver",
                "verifier",
                "critic",
                "reviser",
                "claim_judge",
                "writer",
            },
        )


if __name__ == "__main__":
    unittest.main()
