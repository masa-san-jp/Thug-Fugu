import unittest

from fugu_local.backends import ChatResponse
from fugu_local.config import CoordinatorConfig, CoordinatorRule, EnsembleConfig
from fugu_local.coordinator import Coordinator


class StaticMetaBackend:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, request):
        self.calls.append(request)
        return ChatResponse(content=self.content)


class CoordinatorTests(unittest.TestCase):
    def test_configured_rule_takes_precedence(self):
        coordinator = Coordinator(
            CoordinatorConfig(
                enabled=True,
                rules=[CoordinatorRule(match=["比較"], pattern="parallel_ensemble")],
            )
        )

        plan = coordinator.plan("2つの案を比較して")

        self.assertEqual(plan.pattern, "parallel_ensemble")
        self.assertEqual(plan.source, "rule")

    def test_heuristic_selects_direct_for_short_task(self):
        coordinator = Coordinator(CoordinatorConfig(enabled=True))

        plan = coordinator.plan("東京の首都は？")

        self.assertEqual(plan.pattern, "direct")
        self.assertEqual(plan.source, "heuristic")

    def test_meta_call_extracts_json_when_heuristics_do_not_match(self):
        backend = StaticMetaBackend('noise {"pattern":"role_split","reason":"needs steps"} tail')
        coordinator = Coordinator(
            CoordinatorConfig(
                enabled=True,
                default_pattern="direct",
                ensemble=EnsembleConfig(n=2, vote="majority"),
            ),
            meta_backend=backend,
            meta_model_name="mock-meta",
        )

        plan = coordinator.plan(
            "ああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああ"
        )

        self.assertEqual(plan.pattern, "role_split")
        self.assertEqual(plan.reason, "meta-call selected role_split")
        self.assertEqual(plan.raw["reason"], "needs steps")
        self.assertEqual(plan.source, "meta")
        self.assertEqual(plan.ensemble_n, 2)
        self.assertEqual(plan.ensemble_vote, "majority")
        self.assertEqual(len(backend.calls), 1)

    def test_invalid_meta_call_falls_back_to_default(self):
        coordinator = Coordinator(
            CoordinatorConfig(enabled=True, default_pattern="direct"),
            meta_backend=StaticMetaBackend("not json"),
            meta_model_name="mock-meta",
        )

        plan = coordinator.plan(
            "ああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああ"
        )

        self.assertEqual(plan.pattern, "direct")
        self.assertEqual(plan.source, "default")


if __name__ == "__main__":
    unittest.main()
