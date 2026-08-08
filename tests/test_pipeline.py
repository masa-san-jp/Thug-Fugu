import time
import unittest

from fugu_local.config import DagStageConfig
from fugu_local.pipeline import StageCallResult, run_sequential_dag


class RecordingCaller:
    """Stub StageCaller: records every request and returns a scripted
    response keyed by stage name, falling back to a default response."""

    def __init__(self, responses, default='{"answer": "ok"}', errors=None, exceptions=None):
        self.responses = responses
        self.default = default
        self.errors = errors or {}
        self.exceptions = exceptions or {}
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.stage in self.exceptions:
            raise self.exceptions[request.stage]
        if request.stage in self.errors:
            return StageCallResult(error=self.errors[request.stage])
        text = self.responses.get(request.stage, self.default)
        return StageCallResult(text=text)


def full_stages(fanout=1):
    return [
        DagStageConfig(name="planner", role="planner"),
        DagStageConfig(name="solver", role="solver", fanout=fanout),
        DagStageConfig(name="verifier", role="judge"),
        DagStageConfig(name="critic", role="critic"),
        DagStageConfig(name="reviser", role="solver"),
        DagStageConfig(name="claim_judge", role="judge"),
        DagStageConfig(name="writer", role="synthesizer"),
    ]


def _replace_stage(stages, name, **overrides):
    updated = list(stages)
    for index, stage in enumerate(updated):
        if stage.name == name:
            updated[index] = DagStageConfig(
                name=stage.name,
                role=overrides.get("role", stage.role),
                enabled=overrides.get("enabled", stage.enabled),
                fanout=overrides.get("fanout", stage.fanout),
            )
    return updated


class PipelineOrderTests(unittest.TestCase):
    def test_stages_execute_in_defined_order(self):
        caller = RecordingCaller({})

        run_sequential_dag(full_stages(), caller, "task text")

        self.assertEqual(
            [r.stage for r in caller.requests],
            ["planner", "solver", "verifier", "critic", "reviser", "claim_judge", "writer"],
        )


class StageDataFlowTests(unittest.TestCase):
    def test_planner_subproblems_reach_solver_prompt(self):
        caller = RecordingCaller({"planner": '{"subproblems": ["subproblem-A", "subproblem-B"]}'})

        run_sequential_dag(full_stages(), caller, "task text")

        solver_request = next(r for r in caller.requests if r.stage == "solver")
        self.assertIn("subproblem-A", solver_request.user_content)
        self.assertIn("subproblem-B", solver_request.user_content)

    def test_solver_candidates_reach_critic_prompt(self):
        caller = RecordingCaller({"solver": '{"answer": "solver-candidate-answer"}'})

        run_sequential_dag(full_stages(), caller, "task text")

        critic_request = next(r for r in caller.requests if r.stage == "critic")
        self.assertIn("solver-candidate-answer", critic_request.user_content)

    def test_verifier_results_reach_claim_judge_prompt(self):
        caller = RecordingCaller(
            {
                "solver": '{"answer": "x", "claims": [{"text": "claim-1"}]}',
                "verifier": (
                    '{"claims": [{"text": "claim-1", "verification": "passed", '
                    '"evidence": "checked-ok"}]}'
                ),
            }
        )

        run_sequential_dag(full_stages(), caller, "task text")

        claim_judge_request = next(r for r in caller.requests if r.stage == "claim_judge")
        self.assertIn("passed", claim_judge_request.user_content)
        self.assertIn("checked-ok", claim_judge_request.user_content)


class BypassRuleTests(unittest.TestCase):
    def test_planner_disabled_uses_whole_task_as_single_subproblem(self):
        stages = _replace_stage(full_stages(), "planner", enabled=False)
        caller = RecordingCaller({})

        run_sequential_dag(stages, caller, "the whole task text")

        called_stages = [r.stage for r in caller.requests]
        self.assertNotIn("planner", called_stages)
        solver_request = next(r for r in caller.requests if r.stage == "solver")
        self.assertIn("the whole task text", solver_request.user_content)

    def test_critic_disabled_also_skips_reviser(self):
        stages = _replace_stage(full_stages(), "critic", enabled=False)
        caller = RecordingCaller({"solver": '{"answer": "solver-candidate-xyz"}'})

        run_sequential_dag(stages, caller, "task")

        called_stages = [r.stage for r in caller.requests]
        self.assertNotIn("critic", called_stages)
        self.assertNotIn("reviser", called_stages)
        claim_judge_request = next(r for r in caller.requests if r.stage == "claim_judge")
        self.assertIn("solver-candidate-xyz", claim_judge_request.user_content)

    def test_verifier_disabled_marks_claims_unavailable_downstream(self):
        stages = _replace_stage(full_stages(), "verifier", enabled=False)
        caller = RecordingCaller({"solver": '{"answer": "x", "claims": [{"text": "claim-1"}]}'})

        run_sequential_dag(stages, caller, "task")

        self.assertNotIn("verifier", [r.stage for r in caller.requests])
        critic_request = next(r for r in caller.requests if r.stage == "critic")
        self.assertIn("unavailable", critic_request.user_content)

    def test_claim_judge_disabled_marks_claims_unreviewed_in_writer_prompt(self):
        stages = _replace_stage(full_stages(), "claim_judge", enabled=False)
        caller = RecordingCaller({})

        run_sequential_dag(stages, caller, "task")

        self.assertNotIn("claim_judge", [r.stage for r in caller.requests])
        writer_request = next(r for r in caller.requests if r.stage == "writer")
        self.assertIn("unreviewed", writer_request.user_content)


class FanoutSeedTests(unittest.TestCase):
    def test_fanout_calls_solver_multiple_times_with_distinct_seeds(self):
        caller = RecordingCaller({})

        run_sequential_dag(full_stages(fanout=3), caller, "task", base_seed=42)

        solver_requests = [r for r in caller.requests if r.stage == "solver"]
        self.assertEqual(len(solver_requests), 3)
        seeds = [r.seed for r in solver_requests]
        self.assertTrue(all(seed is not None for seed in seeds))
        self.assertEqual(len(set(seeds)), 3)

    def test_no_base_seed_leaves_requests_unseeded(self):
        caller = RecordingCaller({})

        run_sequential_dag(full_stages(fanout=2), caller, "task")

        self.assertTrue(all(r.seed is None for r in caller.requests))


class DeadlineTests(unittest.TestCase):
    def test_deadline_exceeded_stops_early_and_records_warning(self):
        caller = RecordingCaller({})
        past_deadline = time.perf_counter() - 1

        result = run_sequential_dag(full_stages(), caller, "task", deadline=past_deadline)

        self.assertEqual([r.stage for r in caller.requests], ["planner"])
        self.assertTrue(any("deadline" in warning for warning in result.warnings))

    def test_deadline_exceeded_after_solver_falls_back_to_solver_answer(self):
        caller = RecordingCaller({"solver": '{"answer": "solver-fallback-answer"}'})
        # Nothing left to spend once the pipeline reaches the check after
        # solver: a zero-length deadline computed at construction time is
        # already in the past for every check after the first.
        result = run_sequential_dag(full_stages(), caller, "task", deadline=time.perf_counter())

        called = [r.stage for r in caller.requests]
        self.assertLessEqual(len(called), 2)
        self.assertTrue(called[0] == "planner")
        if len(called) == 2:
            self.assertEqual(called[1], "solver")
            self.assertEqual(result.content, "solver-fallback-answer")
        self.assertTrue(any("deadline" in warning for warning in result.warnings))


class ParseFailureTests(unittest.TestCase):
    def test_parse_failure_still_returns_a_final_answer(self):
        caller = RecordingCaller({}, default="this is not json at all, just prose")

        result = run_sequential_dag(full_stages(), caller, "task")

        self.assertTrue(result.content)
        writer_output = next(o for o in result.stage_results if o.stage == "writer")
        self.assertIsNotNone(writer_output.parse_error)

    def test_backend_error_does_not_raise_and_falls_back(self):
        # Disable critic (and therefore reviser, via the critic bypass rule)
        # so the only candidate answer available for the fallback chain is
        # the solver's, isolating the "writer failed" -> "use solver answer"
        # path from the (also correct) "prefer reviser when present" rule.
        stages = _replace_stage(full_stages(), "critic", enabled=False)
        caller = RecordingCaller(
            {"solver": '{"answer": "solver-recovers"}'},
            errors={"writer": "backend unreachable"},
        )

        result = run_sequential_dag(stages, caller, "task")

        self.assertEqual(result.content, "solver-recovers")
        writer_output = next(o for o in result.stage_results if o.stage == "writer")
        self.assertEqual(writer_output.parse_error, "backend unreachable")

    def test_backend_exception_does_not_raise_and_falls_back(self):
        stages = _replace_stage(full_stages(), "critic", enabled=False)
        caller = RecordingCaller(
            {"solver": '{"answer": "solver-recovers-from-exception"}'},
            exceptions={"writer": RuntimeError("backend exploded")},
        )

        result = run_sequential_dag(stages, caller, "task")

        self.assertEqual(result.content, "solver-recovers-from-exception")
        writer_output = next(o for o in result.stage_results if o.stage == "writer")
        self.assertEqual(writer_output.parse_error, "backend exploded")

    def test_invalid_stage_caller_result_does_not_raise(self):
        class InvalidResultCaller(RecordingCaller):
            def __call__(self, request):
                if request.stage == "writer":
                    self.requests.append(request)
                    return None
                return super().__call__(request)

        stages = _replace_stage(full_stages(), "critic", enabled=False)
        caller = InvalidResultCaller({"solver": '{"answer": "solver-recovers-from-none"}'})

        result = run_sequential_dag(stages, caller, "task")

        self.assertEqual(result.content, "solver-recovers-from-none")
        writer_output = next(o for o in result.stage_results if o.stage == "writer")
        self.assertIn("call_stage must return StageCallResult", writer_output.parse_error)


if __name__ == "__main__":
    unittest.main()
