## Calibration pilot: 02-best-large-single on generated draft calibration (seed=11)

- Scope: `evals/phase2/drafts/tasks-v2-calibration.jsonl` only; locked test was not run.
- Condition: `02-best-large-single` (`gemma4:26b`)
- Result: **23/30 = 76.7%**
- Interpretation: above the 40–70% target band; the draft is too easy for the large single-model baseline and needs swaps/hardening before approval.

### Family breakdown
- coding: 3/5 = 60%
- japanese: 5/5 = 100%
- logic: 4/5 = 80%
- long_context: 5/5 = 100%
- math: 5/5 = 100%
- planning: 1/5 = 20%

### Difficulty breakdown
- easy: 5/6 = 83%
- medium: 11/12 = 92%
- hard: 7/12 = 58%

### Failed rows
- `genv2-calibration-coding-hard-1` (coding/hard): wall=163.9s tokens=5423 preview='To find the integer that this Python program prints, we can trace the value of the variable `value` through each iterati'
- `genv2-calibration-coding-hard-2` (coding/hard): wall=221.8s tokens=7327 preview='The program starts with `value = 4` and iterates through the range of 10 (from `i = 0` to `i = 9`). In each iteration, i'
- `genv2-calibration-logic-hard-2` (logic/hard): wall=90.5s tokens=3135 preview='To determine how many people are knights, we analyze each statement one by one based on the rules that knights always te'
- `genv2-calibration-planning-easy-1` (planning/easy): wall=216.3s tokens=7278 preview='To find the number of valid complete task orders for tasks A, B, C, and D with the dependencies that A must come before '
- `genv2-calibration-planning-medium-2` (planning/medium): wall=525.3s tokens=15847 preview='To find the number of valid complete task orders that satisfy all dependencies, we can use a topological sort approach. '
- `genv2-calibration-planning-hard-1` (planning/hard): wall=303.4s tokens=9605 preview='To find the number of valid complete task orders for the tasks {A, B, C, D, E, F} given the dependencies (A < D, A < F, '
- `genv2-calibration-planning-hard-2` (planning/hard): wall=293.1s tokens=9222 preview='To find the number of valid complete task orders that satisfy all dependencies, we first identify the constraints and th'
