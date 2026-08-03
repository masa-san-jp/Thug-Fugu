"""Sequential inference DAG executor.

Runs the seven-stage pipeline (planner -> solver -> verifier -> critic ->
reviser -> claim_judge -> writer). Each stage's prompt embeds the *actual*
output of prior stages as structured ``## Header`` sections -- not a
free-text concatenation -- so a later stage genuinely consumes an earlier
stage's work instead of merely running alongside it. This module has no
knowledge of how a stage's LLM call is actually made; the caller (normally
``orchestrator.py``) supplies a ``call_stage`` callback, which keeps this
module testable with plain stub callables (see ``tests/test_pipeline.py``)
independent of the full orchestrator/routing machinery.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .backends import TokenUsage
from .config import ChecksConfig, DagStageConfig
from .seeding import derive_seed
from .stages import Claim, StageOutput, parse_stage_output, stage_system_prompt
from .verifiers import verify_citation, verify_constraint


@dataclass(frozen=True)
class StageCallRequest:
    stage: str
    role: str
    system_prompt: str
    user_content: str
    seed: Optional[int]
    max_tokens: Optional[int]


@dataclass(frozen=True)
class StageCallResult:
    text: str = ""
    usage: Optional[TokenUsage] = None
    error: Optional[str] = None


StageCaller = Callable[[StageCallRequest], StageCallResult]


@dataclass(frozen=True)
class DagRunResult:
    content: str
    stage_results: List[StageOutput] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def run_sequential_dag(
    stages: List[DagStageConfig],
    call_stage: StageCaller,
    task_text: str,
    *,
    base_seed: Optional[int] = None,
    deadline: Optional[float] = None,
    max_stage_tokens: Optional[int] = None,
    verify_checks: Optional[ChecksConfig] = None,
) -> DagRunResult:
    """Execute the sequential DAG and return the final answer plus per-stage
    output and any deadline warnings. Never raises for a malformed or
    missing model response -- that is handled by ``stages.parse_stage_output``
    and the bypass rules below.

    ``verify_checks``, when provided, backs up the verifier stage's LLM
    self-report with in-process constraint/citation checks (see
    ``verifiers.py``); each check is individually a no-op unless its own
    ``enabled`` flag is set, so passing the all-disabled default leaves
    verifier-stage output unchanged."""

    return _PipelineRun(
        stages,
        call_stage,
        task_text,
        base_seed=base_seed,
        deadline=deadline,
        max_stage_tokens=max_stage_tokens,
        verify_checks=verify_checks,
    ).run()


def _deadline_passed(deadline: Optional[float]) -> bool:
    return deadline is not None and time.perf_counter() >= deadline


@dataclass
class _Section:
    header: str
    body: str


class _PipelineRun:
    def __init__(
        self,
        stages: List[DagStageConfig],
        call_stage: StageCaller,
        task_text: str,
        *,
        base_seed: Optional[int],
        deadline: Optional[float],
        max_stage_tokens: Optional[int],
        verify_checks: Optional[ChecksConfig] = None,
    ):
        self._stages_by_name = {stage.name: stage for stage in stages}
        self._call_stage = call_stage
        self._task_text = task_text
        self._base_seed = base_seed
        self._deadline = deadline
        self._max_stage_tokens = max_stage_tokens
        self._verify_checks = verify_checks
        self._sections: List[_Section] = []
        self.stage_results: List[StageOutput] = []
        self.warnings: List[str] = []
        self._solver_outputs: List[StageOutput] = []
        self._reviser_answer: Optional[str] = None

    # -- stage config lookups -------------------------------------------------

    def _enabled(self, name: str) -> bool:
        stage = self._stages_by_name.get(name)
        return stage is not None and stage.enabled

    def _fanout(self, name: str) -> int:
        stage = self._stages_by_name.get(name)
        return stage.fanout if stage is not None else 1

    # -- context accumulation --------------------------------------------------

    def _add_section(self, header: str, body: str) -> None:
        self._sections.append(_Section(header, body))

    def _render_context(self) -> str:
        if not self._sections:
            return "(no prior stage output yet)"
        return "\n\n".join(f"## {section.header}\n{section.body}" for section in self._sections)

    # -- LLM calling ------------------------------------------------------------

    def _call_stage_llm(
        self, stage_name: str, *, seed_key: str, extra_instruction: str
    ) -> StageOutput:
        role = self._stages_by_name[stage_name].role
        system_prompt = stage_system_prompt(stage_name)
        user_content = f"## Task\n{self._task_text}\n\n{self._render_context()}"
        if extra_instruction:
            user_content += f"\n\n{extra_instruction}"
        seed = derive_seed(self._base_seed, seed_key)
        request = StageCallRequest(
            stage=stage_name,
            role=role,
            system_prompt=system_prompt,
            user_content=user_content,
            seed=seed,
            max_tokens=self._max_stage_tokens,
        )
        result = self._call_stage(request)
        if result.error is not None:
            output = StageOutput(stage=stage_name, role=role, answer="", parse_error=result.error)
        else:
            output = parse_stage_output(stage_name, role, result.text)
            if result.usage is not None:
                output = dataclasses.replace(output, usage=result.usage)
        self.stage_results.append(output)
        return output

    # -- deadline handling --------------------------------------------------

    def _check_deadline(self, next_stage: str) -> bool:
        if _deadline_passed(self._deadline):
            self.warnings.append(
                f"request deadline exceeded before stage '{next_stage}' started; "
                "returning the best available result"
            )
            return True
        return False

    def _best_available_answer(self) -> str:
        if self._reviser_answer:
            return self._reviser_answer
        for output in self._solver_outputs:
            if output.answer:
                return output.answer
        return ""

    def _finish(self, content: str) -> DagRunResult:
        return DagRunResult(
            content=content,
            stage_results=list(self.stage_results),
            warnings=list(self.warnings),
        )

    # -- the run --------------------------------------------------------------

    def run(self) -> DagRunResult:
        subproblems = self._run_planner_stage()
        if self._check_deadline("solver"):
            return self._finish(self._best_available_answer())

        self._solver_outputs = self._run_solver_stage(subproblems)
        if self._check_deadline("verifier"):
            return self._finish(self._best_available_answer())

        verified_claims = self._run_verifier_stage(self._solver_outputs)
        if self._check_deadline("critic"):
            return self._finish(self._best_available_answer())

        critic_claims = self._run_critic_stage()
        if self._check_deadline("reviser"):
            return self._finish(self._best_available_answer())

        self._reviser_answer = self._run_reviser_stage()
        if self._check_deadline("claim_judge"):
            return self._finish(self._best_available_answer())

        self._run_claim_judge_stage(verified_claims, critic_claims)
        if self._check_deadline("writer"):
            return self._finish(self._best_available_answer())

        content = self._run_writer_stage()
        return self._finish(content)

    # -- stages -----------------------------------------------------------------

    def _run_planner_stage(self) -> List[str]:
        if self._enabled("planner"):
            output = self._call_stage_llm("planner", seed_key="dag:planner", extra_instruction="")
            subproblems = output.subproblems or [self._task_text]
            assumptions = output.assumptions
        else:
            subproblems = [self._task_text]
            assumptions = []

        self._add_section(
            "Subproblems from planner",
            "\n".join(f"{index + 1}. {problem}" for index, problem in enumerate(subproblems)),
        )
        self._add_section(
            "Constraints from planner",
            "\n".join(f"- {assumption}" for assumption in assumptions) if assumptions else "(none)",
        )
        return subproblems

    def _run_solver_stage(self, subproblems: List[str]) -> List[StageOutput]:
        fanout = self._fanout("solver")
        outputs: List[StageOutput] = []
        for index in range(fanout):
            assigned = subproblems[index % len(subproblems)]
            output = self._call_stage_llm(
                "solver",
                seed_key=f"dag:solver#{index}",
                extra_instruction=f"## Your assigned subproblem\n{assigned}",
            )
            outputs.append(output)

        lines = [f"- [solver#{index + 1}] {output.answer}" for index, output in enumerate(outputs)]
        self._add_section(
            "Candidate answers from solvers",
            "\n".join(lines) if lines else "(no solver output)",
        )
        return outputs

    def _run_verifier_stage(self, solver_outputs: List[StageOutput]) -> List[Claim]:
        solver_claims = [claim for output in solver_outputs for claim in output.claims]
        verifier_result_index: Optional[int] = None

        if self._enabled("verifier"):
            claims_text = (
                "\n".join(f'- "{claim.text}"' for claim in solver_claims)
                if solver_claims
                else "(no claims to verify)"
            )
            output = self._call_stage_llm(
                "verifier",
                seed_key="dag:verifier",
                extra_instruction=f"## Claims to verify\n{claims_text}",
            )
            verifier_result_index = len(self.stage_results) - 1
            verified = output.claims if output.claims else solver_claims
        else:
            verified = [
                Claim(
                    text=claim.text,
                    evidence="verifier stage disabled; machine verification did not run",
                    confidence=claim.confidence,
                    verification="unavailable",
                )
                for claim in solver_claims
            ]

        if self._verify_checks is not None:
            context = f"{self._task_text}\n\n{self._render_context()}"
            verified = [self._apply_mechanical_checks(claim, context) for claim in verified]
            # Reflect the mechanical checks in the recorded stage output too
            # (not just the downstream prompt section) so callers reading
            # OrchestrationResult.stage_results see the authoritative verdict.
            if verifier_result_index is not None:
                self.stage_results[verifier_result_index] = dataclasses.replace(
                    self.stage_results[verifier_result_index], claims=verified
                )

        lines = [
            f'- claim "{claim.text}" -> {claim.verification} (reason: {claim.evidence or "n/a"})'
            for claim in verified
        ]
        self._add_section(
            "Verification results",
            "\n".join(lines) if lines else "(no claims verified)",
        )
        return verified

    def _apply_mechanical_checks(self, claim: Claim, context: str) -> Claim:
        assert self._verify_checks is not None
        outcomes = []
        constraint_outcome = verify_constraint(claim.text, self._verify_checks.constraint)
        if constraint_outcome is not None:
            outcomes.append(constraint_outcome)
        citation_outcome = verify_citation(claim, context, self._verify_checks.citation)
        if citation_outcome is not None:
            outcomes.append(citation_outcome)
        if not outcomes:
            return claim
        verification = "failed" if any(o.verification == "failed" for o in outcomes) else "passed"
        evidence = "; ".join(o.evidence for o in outcomes)
        return dataclasses.replace(claim, verification=verification, evidence=evidence)

    def _run_critic_stage(self) -> List[Claim]:
        if self._enabled("critic"):
            output = self._call_stage_llm("critic", seed_key="dag:critic", extra_instruction="")
            critic_claims = output.claims
            lines = [
                f"- {claim.text} (evidence: {claim.evidence or 'n/a'})" for claim in critic_claims
            ]
            body = "\n".join(lines) if lines else "(critic found no issues)"
        else:
            critic_claims = []
            body = "(critic stage disabled; no critique performed)"

        self._add_section("Critic findings", body)
        return critic_claims

    def _run_reviser_stage(self) -> Optional[str]:
        # Bypass rule: disabling critic force-skips reviser too, regardless
        # of reviser's own `enabled` flag -- reviser's input contract
        # assumes critic findings exist.
        if self._enabled("reviser") and self._enabled("critic"):
            output = self._call_stage_llm("reviser", seed_key="dag:reviser", extra_instruction="")
            answer = output.answer
            self._add_section("Revised answer", answer or "(reviser produced no answer)")
            return answer

        self._add_section(
            "Revised answer",
            "(reviser stage disabled; using solver candidates directly)",
        )
        return None

    def _run_claim_judge_stage(
        self, verified_claims: List[Claim], critic_claims: List[Claim]
    ) -> List[Claim]:
        candidate_claims = verified_claims + critic_claims

        if self._enabled("claim_judge"):
            claims_text = (
                "\n".join(
                    f'- "{claim.text}" (verification: {claim.verification})'
                    for claim in candidate_claims
                )
                if candidate_claims
                else "(no claims to judge)"
            )
            output = self._call_stage_llm(
                "claim_judge",
                seed_key="dag:claim_judge",
                extra_instruction=f"## Claims to judge\n{claims_text}",
            )
            judged = output.claims if output.claims else candidate_claims
            lines = [f"- {claim.text} -> {claim.evidence or 'n/a'}" for claim in judged]
            self._add_section(
                "Claim judge decisions",
                "\n".join(lines) if lines else "(no claims judged)",
            )
            return judged

        lines = [f"- {claim.text}" for claim in candidate_claims]
        body = "(claim judge stage disabled; the following claims are unreviewed)\n" + (
            "\n".join(lines) if lines else "(no claims)"
        )
        self._add_section("Claim judge decisions", body)
        return candidate_claims

    def _run_writer_stage(self) -> str:
        output = self._call_stage_llm("writer", seed_key="dag:writer", extra_instruction="")
        return output.answer or self._best_available_answer()
