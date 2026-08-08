# Hard benchmark v2 (WP-2)

Status: schema, validator, and this document are implemented
(`scripts/validate_tasks.py`, WP-2a). The task files themselves
(`evals/phase2/tasks-v2-calibration.jsonl`, `tasks-v2-dev.jsonl`,
`tasks-v2-test.jsonl`) are WP-2b and require human review before use --
see [HUMAN GATE](#human-gate) below.

Parent plan: [`phase2-decision-implementation-plan.md`](../plans/phase2-decision-implementation-plan.md)
§3. Parent issue: [#106](https://github.com/masa-san-jp/Thug-Fugu/issues/106).

## Why

The existing `evals/phase1/tasks.jsonl` (12 tasks) is dominated by
arithmetic, capitals, and basic Python/SQL -- every condition tested so
far reaches 100% accuracy on it. A benchmark that every condition already
maxes out cannot show a quality difference between single-model and
multi-model orchestration. This benchmark targets a **40-70% single-model
accuracy band** so a real difference, if one exists, is observable.

## Task schema

Extends the existing schema; existing fields stay compatible.

```json
{
  "id": "math-v2-001",
  "family": "math",
  "difficulty": "hard",
  "answer_type": "single",
  "prompt": "...",
  "grader": {"type": "regex", "pattern": "...", "normalize": true},
  "source": "authored",
  "gold": "102",
  "gold_rationale": "...",
  "review_status": "pending"
}
```

| Field | Constraint |
|---|---|
| `family` | one of `math`, `coding`, `logic`, `planning`, `long_context`, `japanese` |
| `difficulty` | one of `easy`, `medium`, `hard` |
| `answer_type` | one of `single`, `multi` -- no other value |
| `grader.type` | one of `contains`, `regex`, `exact` -- no other value (see below) |
| `gold` | non-empty; must pass its own `grader` (self-consistency, see [Validation](#validation)) |
| `review_status` | `pending` or `approved`; **only a human may set `approved`** |

### Why the decision set is deterministic-only

`exec` (code execution) grading and `freeform` + rubric grading are **not
in this schema** and never will be for the Phase 2 decision set: no
grading framework for either exists yet, so including them here would be
circular (a spec depending on infrastructure that doesn't exist). `coding`
and `long_context` tasks are still included, but phrased so the answer is
short and deterministically checkable (a program's printed/returned value,
a value extracted or computed from a long passage) instead of asking for a
full generated program or essay. Rubric/exec grading and long-form
generation quality are explicitly deferred to a post-Phase-2 extension WP
and must never be smuggled into the Go/Pivot/No-Go decision set.

## The three-way split and the locked test set

| File | Purpose | Minimum size |
|---|---|---|
| `tasks-v2-calibration.jsonl` | difficulty calibration; freely replaceable | 30 |
| `tasks-v2-dev.jsonl` | model/config/prompt selection and tuning | 60 |
| `tasks-v2-test.jsonl` | final Go/Pivot/No-Go decision only; **locked** | 60, >=10 per family |

This exists to prevent overfitting the evaluation set: repeatedly tuning
difficulty, model choice, or prompts against the same tasks used for the
final decision is a selection bias that would invalidate the decision.

- `id` is unique **across all three files** combined (`scripts/validate_tasks.py`
  checks this).
- At least 150 unique tasks total (target: 300), at least 20 per family
  across all three files combined.
- `easy` tasks are at most 20% of each file (intentionally kept, to still
  be able to measure a quality regression from `direct`-pattern routing).
- **Locked-test execution rule**: `tasks-v2-test.jsonl` must not be
  executed against any condition until the comparison conditions, the
  budget manifest (plan §8.4), and the decision thresholds
  (`evals/phase2/decision-criteria.json`, plan §10.3) are all finalized and
  committed. At that point the test file's SHA-256 is recorded into
  `decision-criteria.json`, and the file must not change afterward.
- All difficulty/configuration tuning happens against calibration/dev
  only. Any run against the test file counts as the final decision run --
  there is no do-over. **A human decides when to start that run**
  (plan §0.6).

## Calibration procedure

1. Run the `best small single` condition against the **calibration set**
   with `--repeats 3`:
   ```bash
   PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
     --cases evals/phase2/tasks-v2-calibration.jsonl \
     --condition single=<config> \
     --repeats 3 \
     --output-dir results/phase2-calibration
   ```
2. Check the set-wide accuracy is inside the 40-70% band.
3. If more than 20% of tasks were answered correctly on every repeat
   (ceiling), swap those tasks for harder ones.
4. If more than 30% of tasks were answered incorrectly on every repeat
   (floor), swap some of those tasks for easier ones.
5. Only calibration/dev may be adjusted this way. The test set is authored
   with the same recipe and difficulty bar and is never adjusted afterward
   (see the locked-test rule above).
6. Save the calibration result to `evals/phase2/calibration.json`.

## Authoring recipe (per family)

Task authoring is the one part of this plan that involves human-unverifiable
judgment, so it is restricted to these recipes -- no free-form authoring
outside them. Never determine `gold` from memory or mental arithmetic;
always derive it mechanically.

- **math**: parameterized problem templates (multi-step quantitative
  reasoning, number theory, combinatorics, probability). Compute `gold`
  with a throwaway script; keep the full derivation (formulas and
  intermediate values) in `gold_rationale`.
- **logic**: constraint-satisfaction puzzles (seating, scheduling,
  knights-and-knaves). Confirm solution uniqueness with an exhaustive
  solver before finalizing `gold`; put a summary of the enumeration in
  `gold_rationale`.
- **coding**: "what does this code output" / "what does this function
  return for this input" framing. `gold` is the value obtained by actually
  running the code at authoring time; paste the run output into
  `gold_rationale`.
- **planning**: dependency-ordered tasks with a uniquely-determined answer
  (minimum steps, count of valid orderings). Determine `gold` via
  scripted search.
- **long_context**: a >=2,000 character passage where the question
  requires cross-referencing multiple locations in the passage to answer.
  `gold` must be mechanically recomputable from the passage.
- **japanese**: any of the above task types, written natively in Japanese
  (not translated from an English task).

Authoring scripts are throwaway and must not be committed, but their
output must always be preserved in `gold_rationale`. Run
`scripts/validate_tasks.py` locally (including the gold self-consistency
check) before committing.

## Licensing

If a task is adapted from an existing dataset or public source, record the
source and its license in the `source` field (e.g. `"source": "adapted
from <dataset name>, <license>, <url>"`). Do not include content whose
license is incompatible with this repository, or whose license terms are
unknown.

## Validation

```bash
PYTHONPATH=src python3 scripts/validate_tasks.py evals/phase2/tasks-v2-*.jsonl
```

Exits non-zero on any of: invalid JSON, cross-file duplicate `id`, an
unknown `family`/`difficulty`/`answer_type`, a `grader.type` outside
`contains`/`regex`/`exact` (rejects `exec` and rubric graders outright), a
`gold` value that fails its own grader, a file whose name doesn't
identify it as calibration/dev/test, or a file/family/split falling short
of the minimum counts above.

This validates schema and self-consistency (does the grader accept the
stated gold answer) -- it **cannot** verify a gold answer is actually
*correct*. That is what the [HUMAN GATE](#human-gate) below is for.

## HUMAN GATE

The correctness of gold answers cannot be self-certified by an agent.

- Prefer gold answers derivable by a deterministic process (a
  computation, a program's output, a value extracted from a passage) and
  always pass `scripts/validate_tasks.py`'s self-consistency check.
- Submit with `review_status: "pending"` on every task. Never change it to
  `"approved"` -- only a human does that.
- State plainly in the PR body that experiments must not be run against
  these tasks before human review.
- The decision to start the locked-test-set run is made by a human (see
  the locked-test rule above), not by an agent.
