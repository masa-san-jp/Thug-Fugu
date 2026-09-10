# Benchmark v2 — unapproved review drafts

These files are **machine-generated candidate drafts**, not the approved
benchmark. They exist so the WP-2b human review can happen on a pull request
instead of authoring 150 tasks by hand.

- Generator: `scripts/gen_benchmark_tasks.py --output-dir evals/phase2/drafts --seed 20260810`
- Every row is `review_status: "pending"` and carries `gold_rationale`.
- Gold values are mechanically computed or exhaustively verified
  (`math`/`japanese` computed, `coding` executed, `logic`/`planning` enumerated,
  `long_context` recomputable from the passage).
- Structural check: `PYTHONPATH=src python3 scripts/validate_tasks.py evals/phase2/drafts/tasks-v2-*.jsonl` → passes.

## Counts

- 150 tasks total: calibration 30, dev 60, test 60
- 25 tasks per family; exactly 10 test tasks per family
- 20% easy per split
- all 150 prompts unique

## Human review checklist (WP-2b HUMAN GATE)

These drafts are **not** usable for the Go/No-Go decision until a human:

1. Removes weak, ambiguous, or repetitive prompts.
2. Spot-checks `gold` against `gold_rationale`, and confirms the `japanese`
   tasks read naturally.
3. Confirms sourcing/licensing (all rows here are synthetic generator output;
   record a `source` if any task is later replaced with adapted content).
4. Runs difficulty calibration and confirms the 40–70% accuracy band,
   swapping ceiling/floor tasks in calibration/dev only.
5. Freezes the test split, moves approved files up to `evals/phase2/`
   (`tasks-v2-*.jsonl`), and sets each retained row to
   `review_status: "approved"`.

Only a human may set `approved` or run the locked test set. Do not regenerate
or modify the test split after approval.
