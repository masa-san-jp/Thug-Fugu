"""Adaptive coordinator (Fugu-style triage) for selecting a processing pattern.

The coordinator looks at a task and decides how to process it:

- ``direct``: answer with a single model.
- ``role_split``: split into worker roles and synthesize (the classic behavior).
- ``parallel_ensemble``: run the same model several times and vote/synthesize.

Decision order is non-learning and deterministic-first:

1. Explicit configured rules (substring match -> pattern).
2. Built-in heuristics over the latest user message.
3. Optional small-model meta-call returning a JSON plan.
4. Fallback to ``coordinator.default_pattern``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .backends import ChatMessage, ChatRequest, LLMBackend
from .config import SUPPORTED_PATTERNS, CoordinatorConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger("fugu_local.coordinator")

_BRAINSTORM_KEYWORDS = (
    "複数案",
    "比較",
    "ブレスト",
    "選択肢",
    "案を",
    "brainstorm",
    "compare",
    "options",
    "alternatives",
    "pros and cons",
    "trade-off",
    "tradeoff",
)
_ROLE_SPLIT_KEYWORDS = (
    "実装",
    "レビュー",
    "設計",
    "検証",
    "implement",
    "review",
    "design",
    "refactor",
    "debug",
    "plan",
    "verify",
    "test",
)
_DIRECT_MAX_CHARS = 80


@dataclass(frozen=True)
class Plan:
    """The coordinator's decision for one task."""

    pattern: str
    reason: str
    source: str = "default"
    ensemble_n: int = 3
    ensemble_vote: str = "synth"
    raw: dict = field(default_factory=dict)


class Coordinator:
    """Decide a processing pattern for a task using rules, heuristics, and meta-call."""

    def __init__(
        self,
        config: CoordinatorConfig,
        *,
        meta_backend: Optional[LLMBackend] = None,
        meta_model_name: Optional[str] = None,
    ):
        self.config = config
        self._meta_backend = meta_backend
        self._meta_model_name = meta_model_name

    def plan(self, user_text: str) -> Plan:
        ensemble_n = self.config.ensemble.n
        ensemble_vote = self.config.ensemble.vote

        rule_pattern = self._match_rules(user_text)
        if rule_pattern is not None:
            return Plan(
                pattern=rule_pattern,
                reason="matched configured rule",
                source="rule",
                ensemble_n=ensemble_n,
                ensemble_vote=ensemble_vote,
            )

        heuristic = self._heuristic(user_text)
        if heuristic is not None:
            pattern, reason = heuristic
            return Plan(
                pattern=pattern,
                reason=reason,
                source="heuristic",
                ensemble_n=ensemble_n,
                ensemble_vote=ensemble_vote,
            )

        meta = self._meta_call(user_text)
        if meta is not None:
            return meta

        return Plan(
            pattern=self.config.default_pattern,
            reason="no rule/heuristic/meta match; using default_pattern",
            source="default",
            ensemble_n=ensemble_n,
            ensemble_vote=ensemble_vote,
        )

    def _match_rules(self, user_text: str) -> Optional[str]:
        text = user_text.casefold()
        for rule in self.config.rules:
            if any(token.casefold() in text for token in rule.match):
                return rule.pattern
        return None

    def _heuristic(self, user_text: str) -> Optional[tuple]:
        text = user_text.casefold()
        if any(keyword.casefold() in text for keyword in _BRAINSTORM_KEYWORDS):
            return "parallel_ensemble", "task asks for multiple options/comparison"
        if any(keyword.casefold() in text for keyword in _ROLE_SPLIT_KEYWORDS):
            return "role_split", "task implies multi-step build/review work"
        if len(user_text.strip()) <= _DIRECT_MAX_CHARS:
            return "direct", "short, likely single-shot task"
        return None

    def _meta_call(self, user_text: str) -> Optional[Plan]:
        if self._meta_backend is None or self._meta_model_name is None:
            return None
        request = ChatRequest(
            model=self._meta_model_name,
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "You are a triage coordinator. Choose how to process the user task. "
                        "Reply with ONE JSON object only, no prose, of the form "
                        '{"pattern": "direct|role_split|parallel_ensemble", '
                        '"reason": "short"}. '
                        "Use direct for simple single-shot tasks, role_split for multi-step "
                        "build/verify tasks, parallel_ensemble when multiple independent "
                        "attempts help."
                    ),
                ),
                ChatMessage(role="user", content=user_text),
            ],
            temperature=0.0,
        )
        try:
            response = self._meta_backend.chat(request)
        except Exception as exc:  # noqa: BLE001 - meta-call must never break triage.
            logger.debug("coordinator meta-call failed: %s", exc)
            return None

        parsed = _extract_json_object(response.content)
        if not parsed:
            return None
        pattern = parsed.get("pattern")
        if pattern not in SUPPORTED_PATTERNS:
            return None
        reason = parsed.get("reason")
        return Plan(
            pattern=pattern,
            reason=reason if isinstance(reason, str) and reason else "meta-call decision",
            source="meta",
            ensemble_n=self.config.ensemble.n,
            ensemble_vote=self.config.ensemble.vote,
            raw=parsed,
        )


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first balanced top-level JSON object from text (ollama-safe)."""

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
