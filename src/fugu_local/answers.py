"""Answer normalization and normalized-majority voting.

Shared by the orchestrator's ensemble voting (``orchestrator.py``) and the
evaluation harness's deterministic graders (``scripts/evaluate_orchestration.py``)
so both apply the same normalization rules instead of maintaining separate
copies.

Normalization is purely lexical (Unicode/Markdown/whitespace/number-format
cleanup). It is not a semantic-equivalence judge: ``cluster_answers`` groups
answers by normalized-string equality only. No embedding model or other
additional dependency is introduced.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Sequence, Tuple

# Prefixes stripped from the front of an answer, matched case-insensitively.
# This list is intentionally fixed and small: tests only assert that these
# exact prefixes are recognized, not that other phrasings are.
ANSWER_PREFIXES: Tuple[str, ...] = (
    "answer:",
    "答え:",
    "最終回答:",
    "final answer:",
    "the final answer is",
    "the answer is",
)

_MARKDOWN_EMPHASIS_CHARS = ("`", "*", "_")
_TRAILING_PUNCTUATION = ".。,、"
_NUMERIC_RE = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?$")
_NUMERIC_PARTS_RE = re.compile(r"^([+-]?)(\d[\d,]*)(?:\.(\d+))?$")


def normalize_answer(text: str) -> str:
    """Reduce Unicode/Markdown/whitespace/number-format noise before comparing.

    Applies, in order: NFKC normalization; code-fence/emphasis marker removal;
    answer-prefix removal; whitespace collapsing; casefolding; trailing
    punctuation removal; and, only when the entire remaining string is
    numeric, thousands-separator and trailing-zero normalization.
    """

    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("```", "")
    for char in _MARKDOWN_EMPHASIS_CHARS:
        normalized = normalized.replace(char, "")
    normalized = _strip_answer_prefix(normalized)
    normalized = re.sub(r"\s+", " ", normalized.strip())
    normalized = normalized.casefold()
    normalized = normalized.rstrip(_TRAILING_PUNCTUATION)
    if _NUMERIC_RE.match(normalized):
        normalized = _normalize_numeric(normalized)
    return normalized


def _strip_answer_prefix(text: str) -> str:
    stripped = text.lstrip()
    lowered = stripped.casefold()
    for prefix in ANSWER_PREFIXES:
        if lowered.startswith(prefix):
            return stripped[len(prefix) :]
    return stripped


def _normalize_numeric(text: str) -> str:
    match = _NUMERIC_PARTS_RE.match(text)
    if match is None:
        return text
    sign, integer_part, fraction_part = match.groups()
    integer_part = integer_part.replace(",", "")
    if fraction_part is not None:
        fraction_part = fraction_part.rstrip("0")
    if fraction_part:
        return f"{sign}{integer_part}.{fraction_part}"
    return f"{sign}{integer_part}"


def extract_final_answer(text: str) -> str:
    """Extract the line most likely to be a multi-line response's final answer.

    Scans non-empty lines from the end. The last line that starts with a
    known answer prefix (see ``ANSWER_PREFIXES``) is returned as-is; if no
    line has a prefix, the last non-empty line is returned.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        lowered = unicodedata.normalize("NFKC", line).casefold()
        if any(lowered.startswith(prefix) for prefix in ANSWER_PREFIXES):
            return line
    return lines[-1]


def cluster_answers(contents: Sequence[str]) -> List[List[int]]:
    """Group indices of ``contents`` whose normalized text is identical.

    Clusters are ordered by first appearance; indices within a cluster are in
    ascending order. Deterministic: the same input always yields the same
    output.
    """

    buckets: Dict[str, List[int]] = {}
    for index, content in enumerate(contents):
        key = normalize_answer(content)
        buckets.setdefault(key, []).append(index)
    return list(buckets.values())


def majority_vote(contents: Sequence[str]) -> Tuple[str, int, int]:
    """Return (winning text, winning cluster size, cluster count).

    Ties are broken by first appearance: among clusters sharing the largest
    size, the one whose first member appears earliest in ``contents`` wins.
    """

    clusters = cluster_answers(contents)
    if not clusters:
        return "", 0, 0
    best_cluster = max(clusters, key=len)
    return contents[best_cluster[0]], len(best_cluster), len(clusters)
