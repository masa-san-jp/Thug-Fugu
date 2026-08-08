"""Stage-to-stage data contract for the sequential inference DAG.

Each DAG stage (executed by ``pipeline.py``) calls a backend and gets back
free text, which is parsed into a :class:`StageOutput` via a lenient JSON
parser. Local models frequently miss the requested schema, so a parse
failure becomes ``StageOutput(answer=text, parse_error=...)`` rather than an
exception -- that fallback is normal operation, not a bug, and callers must
never let it interrupt the pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    # Deferred to avoid a runtime cycle: backends.py imports from config.py,
    # and config.py imports STAGE_NAMES from this module for validation.
    from .backends import TokenUsage

STAGE_NAMES = (
    "planner",
    "solver",
    "verifier",
    "critic",
    "reviser",
    "claim_judge",
    "writer",
)

_KNOWN_VERIFICATIONS = {"required", "passed", "failed", "unavailable"}


@dataclass(frozen=True)
class Claim:
    text: str
    evidence: str = ""
    confidence: float = 0.0
    verification: str = "required"  # required|passed|failed|unavailable


@dataclass(frozen=True)
class StageOutput:
    stage: str
    role: str
    answer: str
    claims: List[Claim] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    requested_checks: List[str] = field(default_factory=list)
    subproblems: List[str] = field(default_factory=list)  # planner only
    raw_text: str = ""
    parse_error: Optional[str] = None
    usage: Optional[TokenUsage] = None


def parse_stage_output(stage: str, role: str, text: str) -> StageOutput:
    """Parse a stage's raw response text into a :class:`StageOutput`.

    Never raises. If no JSON object can be extracted, the whole response
    text becomes ``answer`` and ``parse_error`` records why; unknown JSON
    fields are silently ignored, and malformed known fields fall back to
    their dataclass defaults rather than aborting the parse.
    """

    payload = _extract_json_object(text)
    if payload is None:
        return StageOutput(
            stage=stage,
            role=role,
            answer=text,
            raw_text=text,
            parse_error="no JSON object found in response",
        )

    answer = payload.get("answer")
    if not isinstance(answer, str):
        answer = text

    return StageOutput(
        stage=stage,
        role=role,
        answer=answer,
        claims=_parse_claims(payload.get("claims")),
        assumptions=_parse_string_list(payload.get("assumptions")),
        uncertainties=_parse_string_list(payload.get("uncertainties")),
        requested_checks=_parse_string_list(payload.get("requested_checks")),
        subproblems=_parse_string_list(payload.get("subproblems")),
        raw_text=text,
    )


def _parse_claims(raw: object) -> List[Claim]:
    if not isinstance(raw, list):
        return []
    claims: List[Claim] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, str):
            evidence = ""
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = 0.0
        verification = item.get("verification")
        if verification not in _KNOWN_VERIFICATIONS:
            verification = "required"
        claims.append(
            Claim(
                text=text,
                evidence=evidence,
                confidence=float(confidence),
                verification=verification,
            )
        )
    return claims


def _parse_string_list(raw: object) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first balanced top-level JSON object.

    Prefers an object found inside a fenced code block (```...``` or
    ```json...```) over one found by scanning the whole text.
    """

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = []
    if fence_match:
        candidates.append(fence_match.group(1))
    candidates.append(text)

    for candidate in candidates:
        parsed = _scan_balanced_object(candidate)
        if parsed is not None:
            return parsed
    return None


def _scan_balanced_object(text: str) -> Optional[dict]:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


STAGE_JSON_INSTRUCTION = (
    "Respond with a single JSON object: "
    '{"answer": str, "claims": [{"text": str, "evidence": str, "confidence": float, '
    '"verification": "required"}], "assumptions": [str], "uncertainties": [str], '
    '"requested_checks": [str], "subproblems": [str]}. '
    "Omit fields that do not apply. Do not add any text outside the JSON object."
)

_STAGE_PROMPTS = {
    "planner": (
        "You are the planner. Decompose the task into 2-5 self-contained "
        "subproblems and list the explicit constraints. Put the subproblems "
        'in "subproblems" and the constraints in "assumptions".'
    ),
    "solver": (
        "You are a solver. Solve ONLY the subproblem assigned to you, "
        'respecting the planner\'s constraints. Put your result in "answer" '
        'and each factual step in "claims".'
    ),
    "verifier": (
        "You are the verifier. For each claim listed below, check it "
        "against the task and the constraints. Set each claim's "
        '"verification" to "passed" or "failed" and put the reason in "evidence".'
    ),
    "critic": (
        "You are the critic. Read the candidate answers and the "
        "verification results below and identify concrete errors. "
        'Describe each error as a claim in "claims".'
    ),
    "reviser": (
        "You are the reviser. Fix the candidate answer using ONLY the "
        'critic\'s findings below. Put the corrected answer in "answer".'
    ),
    "claim_judge": (
        "You are the claim judge. For each claim below, decide adopt, "
        "reject, or unknown, and record the decision with its reason in "
        '"evidence".'
    ),
    "writer": (
        "You are the writer. Compose the final answer using ONLY the "
        'adopted claims below. Put the final answer in "answer".'
    ),
}


def stage_system_prompt(stage: str) -> str:
    """Return the fixed initial system prompt for a DAG stage.

    Wording is intentionally frozen (see the implementation plan, WP-4
    section 5.4.1); tuning it is out of scope here and left to a later PR
    informed by experiment results. The shared JSON response instruction is
    appended to every stage's prompt.
    """

    return f"{_STAGE_PROMPTS[stage]}\n\n{STAGE_JSON_INSTRUCTION}"
