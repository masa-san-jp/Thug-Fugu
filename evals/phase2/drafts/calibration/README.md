# Calibration notes for the pending benchmark-v2 draft

These reports were run on the generated `tasks-v2-calibration.jsonl` only.
The locked test draft was **not** executed.

## Result

- `01-best-small-single` (`gemma4:e4b`, seed=11): **20/30 = 66.7%**
- `02-best-large-single` (`gemma4:26b`, seed=11): **23/30 = 76.7%**

The draft is **not approval-ready as-is**:

- overall small-model accuracy is inside the target band but near the upper edge
- large-model accuracy exceeds the 40–70% target band
- family distribution is imbalanced:
  - ceiling: math, japanese, long_context (and coding for the small model)
  - floor: planning for both models, logic for the small model

## Recommended next action

Use this PR as a review artifact, not as the approved set. Before promoting
anything to `evals/phase2/tasks-v2-*.jsonl`, replace/harden ceiling-family
items and simplify or reframe floor-family items, then rerun calibration.
