#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 OUTPUT_DIR" >&2
  exit 2
fi

output_dir=$1
hardware_json=${HARDWARE_JSON:-evals/phase1/hardware.json}
seeds=${SEEDS:-11,22,33}

if [[ ! -f "$hardware_json" ]]; then
  echo "hardware metadata not found: $hardware_json" >&2
  echo "copy evals/phase1/hardware.example.json and fill in real values" >&2
  exit 2
fi

args=(
  --cases evals/phase1/tasks.jsonl
  --condition single-e4b=evals/phase1/configs/01-single-e4b.json
  --condition same3-synth=evals/phase1/configs/02-same3-synth.json
  --condition same3-majority=evals/phase1/configs/03-same3-majority.json
  --condition heterogeneous3=evals/phase1/configs/04-heterogeneous3.json
  --condition role-specialized=evals/phase1/configs/05-role-specialized.json
  --condition large-local=evals/phase1/configs/06-large-local.json
  --condition-meta single-e4b=evals/phase1/metadata/gemma-e4b.json
  --condition-meta same3-synth=evals/phase1/metadata/gemma-e4b.json
  --condition-meta same3-majority=evals/phase1/metadata/gemma-e4b.json
  --condition-meta heterogeneous3=evals/phase1/metadata/mixed-local.json
  --condition-meta role-specialized=evals/phase1/metadata/mixed-local.json
  --condition-meta large-local=evals/phase1/metadata/gemma-26b.json
  --hardware-json "$hardware_json"
  --seeds "$seeds"
  --temperature 0.2
  --output-dir "$output_dir"
)

if [[ -n ${CLOUD_CONFIG:-} ]]; then
  args+=(--condition "cloud-reference=$CLOUD_CONFIG")
  if [[ -n ${CLOUD_METADATA:-} ]]; then
    args+=(--condition-meta "cloud-reference=$CLOUD_METADATA")
  fi
fi

PYTHONPATH=src python3 scripts/evaluate_orchestration.py "${args[@]}"
