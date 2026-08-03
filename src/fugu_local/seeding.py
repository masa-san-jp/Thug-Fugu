"""Deterministic per-stream seed derivation.

Shared by the orchestrator's role-split/ensemble seeding (see
``orchestrator.py``, which re-exports ``derive_seed`` for backward
compatibility) and the sequential DAG pipeline (``pipeline.py``), so both
derive per-request seeds the same way without one importing the other.
"""

from __future__ import annotations

import hashlib
from typing import Optional


def derive_seed(base_seed: Optional[int], stream_key: str) -> Optional[int]:
    """Derive a deterministic per-stream seed from a base seed.

    ``stream_key`` identifies the request stream within a run, e.g.
    ``"worker:planner"``, ``"worker:solver#2"``, ``"synthesizer"``,
    ``"verifier:attempt1"``, ``"coordinator"``, or a DAG stage key like
    ``"dag:solver#2"``. Keying by role/stage name (not index) keeps a
    stream's seed stable when other roles/stages are added or reordered,
    and deriving a distinct seed per stream keeps same-model roles from
    returning identical output under a shared seed.
    """

    if base_seed is None:
        return None
    digest = hashlib.sha256(f"{base_seed}:{stream_key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")
