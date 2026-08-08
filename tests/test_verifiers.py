import unittest

from fugu_local.config import CitationCheckConfig, ConstraintCheckConfig
from fugu_local.stages import Claim
from fugu_local.verifiers import verify_citation, verify_constraint


class ConstraintVerifierTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        self.assertIsNone(verify_constraint("anything", ConstraintCheckConfig(enabled=False)))

    def test_regex_match_passes(self):
        outcome = verify_constraint(
            "ok-123", ConstraintCheckConfig(enabled=True, regex=r"^ok-\d+$")
        )
        self.assertEqual(outcome.verification, "passed")

    def test_regex_mismatch_fails(self):
        outcome = verify_constraint("nope", ConstraintCheckConfig(enabled=True, regex=r"^ok-\d+$"))
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("regex", outcome.evidence)

    def test_min_length_failure(self):
        outcome = verify_constraint("ab", ConstraintCheckConfig(enabled=True, min_length=5))
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("min_length", outcome.evidence)

    def test_max_length_failure(self):
        outcome = verify_constraint("abcdef", ConstraintCheckConfig(enabled=True, max_length=3))
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("max_length", outcome.evidence)

    def test_length_within_bounds_passes(self):
        outcome = verify_constraint(
            "abcd", ConstraintCheckConfig(enabled=True, min_length=2, max_length=10)
        )
        self.assertEqual(outcome.verification, "passed")

    def test_numeric_range_within_bounds_passes(self):
        outcome = verify_constraint(
            "5", ConstraintCheckConfig(enabled=True, numeric_range=[0.0, 10.0])
        )
        self.assertEqual(outcome.verification, "passed")

    def test_numeric_range_outside_bounds_fails(self):
        outcome = verify_constraint(
            "42", ConstraintCheckConfig(enabled=True, numeric_range=[0.0, 10.0])
        )
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("numeric_range", outcome.evidence)

    def test_non_numeric_text_fails_numeric_range_check(self):
        outcome = verify_constraint(
            "not-a-number", ConstraintCheckConfig(enabled=True, numeric_range=[0.0, 10.0])
        )
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("not numeric", outcome.evidence)

    def test_require_json_valid_passes(self):
        outcome = verify_constraint(
            '{"a": 1}', ConstraintCheckConfig(enabled=True, require_json=True)
        )
        self.assertEqual(outcome.verification, "passed")

    def test_require_json_invalid_fails(self):
        outcome = verify_constraint(
            "not json", ConstraintCheckConfig(enabled=True, require_json=True)
        )
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("JSON", outcome.evidence)

    def test_multiple_failures_are_joined(self):
        outcome = verify_constraint(
            "x", ConstraintCheckConfig(enabled=True, min_length=5, require_json=True)
        )
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("min_length", outcome.evidence)
        self.assertIn("JSON", outcome.evidence)


class CitationVerifierTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        claim = Claim(text="x", evidence="the sky is blue")
        self.assertIsNone(verify_citation(claim, "context", CitationCheckConfig(enabled=False)))

    def test_evidence_found_in_context_passes(self):
        claim = Claim(text="x", evidence="the sky is blue")
        outcome = verify_citation(
            claim, "context says the sky is blue today", CitationCheckConfig(enabled=True)
        )
        self.assertEqual(outcome.verification, "passed")

    def test_evidence_not_found_in_context_fails(self):
        claim = Claim(text="x", evidence="the sky is green")
        outcome = verify_citation(claim, "the sky is blue", CitationCheckConfig(enabled=True))
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("not found", outcome.evidence)

    def test_empty_evidence_fails(self):
        claim = Claim(text="x", evidence="")
        outcome = verify_citation(claim, "anything", CitationCheckConfig(enabled=True))
        self.assertEqual(outcome.verification, "failed")
        self.assertIn("no evidence", outcome.evidence)

    def test_does_not_perform_network_calls(self):
        import socket
        from unittest import mock

        claim = Claim(text="x", evidence="http://example.com/data")
        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network call attempted")
        ):
            outcome = verify_citation(
                claim, "see http://example.com/data for details", CitationCheckConfig(enabled=True)
            )
        self.assertEqual(outcome.verification, "passed")


class ModuleSafetyTests(unittest.TestCase):
    def test_verifiers_module_does_not_import_subprocess_or_networking(self):
        import ast
        import inspect

        import fugu_local.verifiers as verifiers_module

        tree = ast.parse(inspect.getsource(verifiers_module))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)

        forbidden = {"subprocess", "socket", "urllib", "http", "http.client", "requests"}
        self.assertFalse(
            imported_modules & forbidden,
            f"verifiers.py must not import any of {sorted(forbidden)}; "
            f"found: {sorted(imported_modules & forbidden)}",
        )


if __name__ == "__main__":
    unittest.main()
