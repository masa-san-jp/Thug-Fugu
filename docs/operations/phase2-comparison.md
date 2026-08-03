# Phase 2 budget-matched / ablation comparison (WP-7)

Parent plan: [`phase2-decision-implementation-plan.md`](../plans/phase2-decision-implementation-plan.md)
§8. Parent issue: [#106](https://github.com/masa-san-jp/Thug-Fugu/issues/106).

## Why

The Phase 1 comparisons had the multi-model side spending up to 7.2x the
tokens of the single-model side, so any quality difference observed could
not be separated from "it simply had more compute" versus "coordination
itself helped." This harness makes that separation possible by measuring
both a compute-normalized ("budget-matched") comparison and each
pattern's own natural-configuration Pareto point, run against the same
[hard-benchmark-v2](benchmark-v2.md) task set.

## Comparison conditions

| # | label | description |
|---|-------|-------------|
| 1 | `01-best-small-single` | single small model, `direct` pattern |
| 2 | `02-best-large-single` | single large model, `direct` pattern |
| 3 | `03-same-model-repeat` | same model, repeated sampling + majority vote (`parallel_ensemble`) |
| 4 | `04-heterogeneous-ensemble` | distinct models in parallel, synthesizer-merged (`role_split`) |
| 5 | `05-sequential-dag` | the WP-4 seven-stage sequential DAG |
| 6 | `06-ablation-no-<stage>` | condition 5 with one DAG stage disabled, one config per disableable stage -- **auto-generated**, not committed |
| 7 | `07-cloud-reference` | optional cloud API reference point; only a template ships (`07-cloud-reference.template.json`), no keys committed |

Condition configs 1-5 and the 7 template live in
[`evals/phase2/configs/`](../../evals/phase2/configs/). Condition 6's
configs are generated at run time by `scripts/make_ablation_configs.py`
from condition 5's config -- `solver` and `writer` are never ablated
(they cannot be disabled; see
[`sequential-inference-dag.md`](../design/sequential-inference-dag.md)),
and each ablation's actual behavior is defined by the DAG's own bypass
rules (e.g. disabling `critic` also skips `reviser`), not by this script.

## Budget control: pre-commitment, not a post-hoc penalty

Rejecting an answer as "over budget" after the fact still means the
compute was already spent -- that isn't budget control, it's bookkeeping.
And deriving a budget from the same experiment run being compared would
make the budget depend on execution order. So this happens in two
separate phases:

### Phase A: measure baseline, freeze the budget

1. Run the baseline conditions (`01`, `02`) at their natural
   configuration against the target task file.
2. `scripts/make_budget_manifest.py` computes each family's median token
   usage and median wall-clock time from that baseline run and writes
   `budget-manifest.json` (`budget = baseline median x coefficient`,
   coefficient defaults to `1.0` and is recorded in the manifest; the
   computation is deterministic -- same input rows always produce the
   same output, regardless of row order).
3. **Commit `budget-manifest.json` and stop regenerating it.** Every
   subsequent budget-matched run in the same experiment reads this frozen
   file; it is never recomputed from the run it's controlling.

### Phase B: run every condition twice

- **Budget-matched** (`--budget-manifest budget-manifest.json`): before
  each call, the case's family-specific `token_budget` is passed as
  `max_tokens` and `wall_clock_budget_ms` (converted to seconds) is
  passed as a per-call `request_timeout_seconds` override
  (`FuguLocalOrchestrator.chat(..., request_timeout_seconds=...)`), which
  the sequential DAG and every other pattern already honor as a request
  deadline. This is the pre-allocation.
- Pre-allocation is an approximation -- prompt tokens aren't known until
  after the call, and the deadline is only checked between orchestration
  steps (not mid-backend-call), so actual usage can still exceed the
  budget. When it does, the row is recorded with `budget_exceeded: true`
  and **counts as incorrect regardless of what the grader said** --
  "budget-matched" means only an answer actually returned within budget
  counts as correct. `summary.json` additionally carries a
  `budget_filtered` view: the same per-condition accuracy/latency/token
  metrics computed only over runs that stayed within budget (as opposed
  to `conditions`, which includes budget-exceeded runs counted as wrong).
- **Natural configuration** (no `--budget-manifest`): each pattern runs at
  its own normal settings, to see each pattern's actual Pareto frontier
  (quality vs. cost) independent of the budget-matched comparison.

## Running it

```bash
PYTHONPATH=src python3 scripts/run_phase2_comparison.sh results/phase2-run-001
```

Environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `CASES` | `evals/phase2/tasks-v2-dev.jsonl` | task file to evaluate against |
| `CONFIGS_DIR` | `evals/phase2/configs` | directory holding conditions 1-5 (and 7) |
| `HARDWARE_JSON` | `evals/phase2/hardware.json` | hardware/power metadata (copy `evals/phase1/hardware.example.json` and fill in real values) |
| `SEEDS` | `11,22,33` | comma-separated experiment seeds |
| `COEFFICIENT` | `1.0` | budget multiplier applied to the Phase A baseline median |
| `CLOUD_CONFIG` | unset | path to a real (non-template) condition-7 config; adds `07-cloud-reference` to the comparison when set |

Output layout under `OUTPUT_DIR`:

```
phase-a-baseline/          # conditions 01/02 natural-config run
budget-manifest.json       # frozen per-family token/wall-clock budget
ablation-configs/          # auto-generated 06-ablation-no-<stage>.json
phase-b-budget-matched/    # all conditions, --budget-manifest applied
phase-b-natural/           # all conditions, no budget applied
```

**Never point `CASES` at `tasks-v2-test.jsonl`** until the locked-test
preconditions in [`benchmark-v2.md`](benchmark-v2.md#the-three-way-split-and-the-locked-test-set)
are met (comparison conditions, budget manifest, and decision thresholds
all finalized and committed) and a human has decided to start that run.
This script does not enforce that on its own -- it is a human process
control, not a code guard.

### Smoke-testing without real hardware

The script has been verified to complete end-to-end against an
`echo`-backend condition set and task file (no real network, no Ollama
required) -- point `CASES` and `CONFIGS_DIR` at echo-backed fixtures to
reproduce that locally. Every stage (Phase A baseline run, budget-manifest
freeze, ablation-config generation, both Phase B runs) executes and
produces the expected output files; accuracy will be near-zero since the
echo backend just reflects the prompt back, not a real quality signal.
