#!/usr/bin/env bash
# WP-7 budget-matched / ablation comparison harness
# (docs/plans/phase2-decision-implementation-plan.md section 8).
#
# Phase A: measure baseline (conditions 01/02) on the target task file and
# freeze a token/wall-clock budget manifest from the result.
# Phase B: run every condition (01-06, plus 07 if CLOUD_CONFIG is set) twice
# against the SAME target task file -- once budget-matched (the frozen
# manifest from Phase A), once natural-configuration (no budget) -- so both
# a fair, compute-normalized comparison and each pattern's own Pareto point
# are available.
#
# The target task file defaults to the (not-yet-authored, human-reviewed)
# WP-2 dev set. NEVER point CASES at tasks-v2-test.jsonl until the locked-test
# preconditions in docs/operations/benchmark-v2.md are met and a human has
# decided to start that run -- this script enforces nothing about that,
# it is a human process control.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 OUTPUT_DIR" >&2
  exit 2
fi

output_dir=$1
cases=${CASES:-evals/phase2/tasks-v2-dev.jsonl}
configs_dir=${CONFIGS_DIR:-evals/phase2/configs}
hardware_json=${HARDWARE_JSON:-evals/phase2/hardware.json}
seeds=${SEEDS:-11,22,33}
coefficient=${COEFFICIENT:-1.0}

if [[ ! -f "$cases" ]]; then
  echo "task file not found: $cases" >&2
  echo "WP-2's dev set is human-gated and may not exist yet; pass CASES=<path> to point" >&2
  echo "at a different task file (e.g. for a smoke test)." >&2
  exit 2
fi

if [[ ! -f "$hardware_json" ]]; then
  echo "hardware metadata not found: $hardware_json" >&2
  echo "copy evals/phase1/hardware.example.json and fill in real values, or set HARDWARE_JSON" >&2
  exit 2
fi

mkdir -p "$output_dir"

echo "== Phase A: baseline measurement =="
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --cases "$cases" \
  --condition "01-best-small-single=$configs_dir/01-best-small-single.json" \
  --condition "02-best-large-single=$configs_dir/02-best-large-single.json" \
  --hardware-json "$hardware_json" \
  --seeds "$seeds" \
  --temperature 0.2 \
  --output-dir "$output_dir/phase-a-baseline"

echo "== Phase A: freezing budget manifest =="
PYTHONPATH=src python3 scripts/make_budget_manifest.py \
  "$output_dir/phase-a-baseline/results.jsonl" \
  --output "$output_dir/budget-manifest.json" \
  --coefficient "$coefficient" \
  --source-conditions "01-best-small-single,02-best-large-single"

echo "== Generating ablation configs from 05-sequential-dag =="
PYTHONPATH=src python3 scripts/make_ablation_configs.py \
  "$configs_dir/05-sequential-dag.json" \
  --output-dir "$output_dir/ablation-configs"

condition_args=(
  --condition "01-best-small-single=$configs_dir/01-best-small-single.json"
  --condition "02-best-large-single=$configs_dir/02-best-large-single.json"
  --condition "03-same-model-repeat=$configs_dir/03-same-model-repeat.json"
  --condition "04-heterogeneous-ensemble=$configs_dir/04-heterogeneous-ensemble.json"
  --condition "05-sequential-dag=$configs_dir/05-sequential-dag.json"
)
for ablation_config in "$output_dir"/ablation-configs/*.json; do
  [[ -e "$ablation_config" ]] || continue
  stage_name=$(basename "$ablation_config" .json | sed 's/^06-ablation-no-//')
  condition_args+=(--condition "06-ablation-no-$stage_name=$ablation_config")
done
if [[ -n ${CLOUD_CONFIG:-} ]]; then
  condition_args+=(--condition "07-cloud-reference=$CLOUD_CONFIG")
fi

echo "== Phase B: budget-matched comparison =="
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --cases "$cases" \
  "${condition_args[@]}" \
  --budget-manifest "$output_dir/budget-manifest.json" \
  --hardware-json "$hardware_json" \
  --seeds "$seeds" \
  --temperature 0.2 \
  --output-dir "$output_dir/phase-b-budget-matched"

echo "== Phase B: natural-configuration comparison =="
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --cases "$cases" \
  "${condition_args[@]}" \
  --hardware-json "$hardware_json" \
  --seeds "$seeds" \
  --temperature 0.2 \
  --output-dir "$output_dir/phase-b-natural"

echo "== Done. Results under $output_dir =="
