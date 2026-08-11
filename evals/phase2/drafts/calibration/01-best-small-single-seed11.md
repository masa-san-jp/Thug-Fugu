## Calibration pilot: 01-best-small-single on generated draft calibration (seed=11)

- Scope: `evals/phase2/drafts/tasks-v2-calibration.jsonl` only; locked test was not run.
- Condition: `01-best-small-single` (`gemma4:e4b`)
- Result: **20/30 = 66.7%**
- Interpretation: overall in the 40–70% target band, but family distribution is imbalanced; do not approve without review/swaps.

### Family breakdown
- coding: 5/5 = 100%
- japanese: 5/5 = 100%
- logic: 0/5 = 0%
- long_context: 4/5 = 80%
- math: 5/5 = 100%
- planning: 1/5 = 20%

### Difficulty breakdown
- easy: 3/6 = 50%
- medium: 9/12 = 75%
- hard: 8/12 = 67%

### Auto flags
- Ceiling: math/coding/japanese are 100%; long_context is 80%.
- Floor: logic is 0%; planning is 20%.
- Next action: run the same calibration on `02-best-large-single`, then auto-propose swaps or grader/prompt changes for ceiling/floor families.

### Failed rows
- `genv2-calibration-logic-easy-1` (logic/easy): wall=56.3s tokens=1924 preview='Let $A, B, C$ represent Alice, Bob, and Carol, respectively. Let $K_X$ be true if person $X$ is a knight, and false if t'
- `genv2-calibration-logic-medium-1` (logic/medium): wall=52.6s tokens=1825 preview='Let $T_X$ denote the type of person $X$: Knight (K, always tells the truth) or Knave (N, always lies).\\n\\nWe analyze the'
- `genv2-calibration-logic-medium-2` (logic/medium): wall=71.0s tokens=2470 preview='This is a classic knight and knave logic puzzle. We will use $T(X)=K$ if person $X$ is a knight, and $T(X)=N$ if person '
- `genv2-calibration-logic-hard-1` (logic/hard): wall=138.0s tokens=4635 preview='This problem requires determining the type (Knight or Knave) of five people: Alice (A), Bob (B), Carol (C), Dave (D), an'
- `genv2-calibration-logic-hard-2` (logic/hard): wall=107.0s tokens=3580 preview='This problem requires determining the type (Knight or Knave) of each person based on their statements, knowing that Knig'
- `genv2-calibration-planning-easy-1` (planning/easy): wall=94.5s tokens=3201 preview='The tasks are A, B, C, and D. We need to find the number of permutations satisfying the dependencies:\\n1. A must occur b'
- `genv2-calibration-planning-medium-2` (planning/medium): wall=110.9s tokens=3857 preview='This problem asks for the number of topological sorts (valid task orders) given a set of precedence constraints.\\nLet $T'
- `genv2-calibration-planning-hard-1` (planning/hard): wall=193.2s tokens=6570 preview='The set of tasks is $T = \\{A, B, C, D, E, F\\}$. We are given the following dependencies:\\n1. A must precede D ($A \\prec '
- `genv2-calibration-planning-hard-2` (planning/hard): wall=180.6s tokens=6094 preview='The problem asks for the number of topological sorts of the tasks $\\{A, B, C, D, E, F\\}$ subject to the given dependenci'
- `genv2-calibration-long_context-easy-1` (long_context/easy): wall=6.3s tokens=1249 preview='$402 + 839 = 1241$'
