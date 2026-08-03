"""In-process constraint and citation verification for the sequential DAG's
verifier stage.

These checks back up the DAG verifier stage's LLM self-report with
mechanical verification. They deliberately never execute model-generated
code: see WP-5 in docs/plans/phase2-decision-implementation-plan.md for why
a subprocess-based code execution verifier is out of scope (no OS-level
sandbox can be guaranteed here). Every check in this module runs entirely
in-process against text already in hand -- no subprocess, no network
socket, no file write.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .config import CitationCheckConfig, ConstraintCheckConfig
from .stages import Claim


@dataclass(frozen=True)
class VerificationOutcome:
    verification: str  # "passed" | "failed"
    evidence: str


def verify_constraint(text: str, config: ConstraintCheckConfig) -> Optional[VerificationOutcome]:
    """Check `text` against the configured regex/length/numeric-range/JSON
    constraints. Returns None when the check is disabled."""
    if not config.enabled:
        return None
    failures = []
    if config.regex is not None and re.search(config.regex, text) is None:
        failures.append(f"text does not match regex {config.regex!r}")
    if config.min_length is not None and len(text) < config.min_length:
        failures.append(f"length {len(text)} is below min_length {config.min_length}")
    if config.max_length is not None and len(text) > config.max_length:
        failures.append(f"length {len(text)} exceeds max_length {config.max_length}")
    if config.numeric_range is not None:
        low, high = config.numeric_range
        try:
            value = float(text.strip())
        except ValueError:
            failures.append("text is not numeric")
        else:
            if not (low <= value <= high):
                failures.append(f"value {value} is outside numeric_range [{low}, {high}]")
    if config.require_json:
        try:
            json.loads(text)
        except (json.JSONDecodeError, ValueError):
            failures.append("text is not valid JSON")
    if failures:
        return VerificationOutcome(verification="failed", evidence="; ".join(failures))
    return VerificationOutcome(verification="passed", evidence="all constraint checks passed")


def verify_citation(
    claim: Claim, context: str, config: CitationCheckConfig
) -> Optional[VerificationOutcome]:
    """Check that `claim.evidence` appears verbatim in `context`. `context`
    is whatever text the caller already has in hand -- this never fetches
    an external URL. Returns None when the check is disabled."""
    if not config.enabled:
        return None
    evidence = claim.evidence.strip()
    if not evidence:
        return VerificationOutcome(
            verification="failed", evidence="claim has no evidence text to check"
        )
    if evidence in context:
        return VerificationOutcome(verification="passed", evidence="evidence text found in context")
    return VerificationOutcome(verification="failed", evidence="evidence text not found in context")
