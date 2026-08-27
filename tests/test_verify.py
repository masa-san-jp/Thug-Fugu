import sys
import unittest

from scripts.verify import build_steps


class VerifyPlanTests(unittest.TestCase):
    def test_fast_plan_is_suitable_for_iteration(self):
        steps = build_steps(fast=True)

        self.assertEqual([step.name for step in steps], ["ruff lint", "ruff format", "unit tests"])
        self.assertIn("scripts", steps[0].argv)
        self.assertIn("scripts", steps[1].argv)
        self.assertEqual(steps[2].argv[:3], [sys.executable, "-m", "unittest"])

    def test_full_plan_covers_ci_and_packaging(self):
        steps = build_steps(fast=False)

        self.assertEqual(
            [step.name for step in steps],
            [
                "ruff lint",
                "ruff format",
                "coverage erase",
                "unit tests with coverage",
                "coverage threshold",
                "package build",
            ],
        )
        self.assertIn("--fail-under=85", steps[-2].argv)
        self.assertEqual(steps[-1].argv[-1], "build")


if __name__ == "__main__":
    unittest.main()
