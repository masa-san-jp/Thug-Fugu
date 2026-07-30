# Evaluation harness

Status: implemented for reproducible single-vs-multi configuration comparisons
(Issue #72). Power/cost collection and larger research datasets are tracked by
later Phase 1 issues.

The dependency-free harness runs multiple Thug-Fugu configurations against the
same JSONL task set and records accuracy, errors, wall time, raw output, and
backend-reported token usage.

Multiple seeds (`--seeds 11,22,33`) repeat every `(condition, case)` run.
Optional case `domain` fields produce per-domain summaries and Wilson 95%
confidence intervals.

Typical conditions:

- **single**: one model / one worker baseline
- **static-multi**: fixed multi-role or multi-model configuration
- **adaptive**: coordinator-enabled configuration

## Quick offline comparison

This command uses the echo backend and verifies the complete experiment-bundle
pipeline without requiring a model server:

```bash
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --cases evals/compare-echo.jsonl \
  --condition single=examples/fugu-local.eval-single.json \
  --condition multi=examples/fugu-local.eval-multi.json \
  --seed 7 \
  --output-dir /tmp/thug-fugu-eval/echo-001
```

## Experiment bundle

`--output-dir` creates:

```text
experiment/
├── manifest.json
├── rerun.sh
├── results.jsonl
├── results.csv
├── summary.json
└── inputs/
    ├── cases.jsonl
    └── 01-condition.json ...
```

- `manifest.json`: seed, temperature override, input hashes, model/backend names,
  roles, coordinator settings, quantization metadata, hardware metadata, output
  paths, and a rerun command.
- `results.jsonl`: full raw output and metrics for each `(condition, case)`.
- `results.csv`: spreadsheet-friendly preview and metrics.
- `summary.json`: accuracy, errors, mean/median latency, total tokens, and mean
  tokens per condition.
- `inputs/`: task/config snapshots. Literal `api_key` values are redacted.

The original config path and SHA-256 are also recorded. A rerun uses the original
file when it still matches, otherwise it falls back to the sanitized snapshot.

## Rerun from a manifest

```bash
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --rerun-manifest /tmp/thug-fugu-eval/echo-001/manifest.json \
  --output-dir /tmp/thug-fugu-eval/echo-001-rerun
```

The generated `rerun.sh` contains the same command.

## Task format

Each line is one JSON object:

```json
{"id":"capital-france","domain":"qa","prompt":"What is the capital of France?","grader":{"type":"contains","value":"Paris"}}
```

Supported deterministic graders:

| Type | Fields | Meaning |
|---|---|---|
| `contains` | `value` | Case-insensitive substring match |
| `regex` | `pattern` | Python regex search with `IGNORECASE | MULTILINE` |
| `exact` | `value` | Exact string match after trimming whitespace |

## Recording quantization and hardware

Per-condition metadata can record quantization or experiment notes:

```json
{"quantization":"Q4_K_M","notes":"Ollama model tag abc"}
```

```bash
--condition-meta single=/path/single-meta.json
```

Hardware metadata can be supplied as a JSON object:

```json
{
  "host_label": "node-a",
  "cpu": "example",
  "gpu": "example",
  "ram_gb": 64,
  "vram_gb": 24,
  "power_meter": "wall meter model"
}
```

```bash
--hardware-json /path/hardware.json
```

Without this option the manifest records basic OS, architecture, Python version,
and hostname. The harness records the seed even though backend/model determinism
depends on the serving implementation.

## Minimal real-LLM experiment

1. Prepare two configs that point at the same available hardware:
   - `single.json`: one worker/model, no synthesizer.
   - `multi.json`: two or more workers/models plus a synthesizer.
2. Keep model generation settings and the task file identical.
3. Record exact model tags and quantization using condition metadata.
4. Record CPU/GPU/RAM/VRAM and power-measurement setup with `--hardware-json`.
5. Run:

   ```bash
   PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
     --cases /path/tasks.jsonl \
     --condition single=/path/single.json \
     --condition multi=/path/multi.json \
     --condition-meta single=/path/single-meta.json \
     --condition-meta multi=/path/multi-meta.json \
     --hardware-json /path/hardware.json \
  --seeds 11,22,33 \
     --temperature 0.2 \
     --output-dir results/experiment-001
   ```

6. Preserve the whole output directory. Do not publish it without reviewing raw
   prompts, outputs, hostnames, and hardware metadata for sensitive content.

## Legacy output mode

The previous CSV/summary interface remains supported:

```bash
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --cases evals/smoke.jsonl \
  --condition A=examples/fugu-local.single-gpu.json \
  --condition B=examples/fugu-local.model-pool.json \
  --csv /tmp/thug-fugu-eval.csv \
  --summary /tmp/thug-fugu-eval-summary.json
```

## Interpretation

Compare quality together with latency, errors, and token usage. More worker calls
often improve quality by spending more computation; later Phase 1 work must add
power and total-cost measurements so improvements are not attributed to
orchestration when they are only caused by a larger inference budget.

The checked-in Phase 1 matrix and reporting protocol are documented in
[`phase1-comparison.md`](phase1-comparison.md).
