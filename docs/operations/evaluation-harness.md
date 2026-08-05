# Evaluation harness

Status: implemented for reproducible single-vs-multi configuration comparisons
(Issue #72). Power/cost collection and larger research datasets are tracked by
later Phase 1 issues.

The dependency-free harness runs multiple Thug-Fugu configurations against the
same JSONL task set and records accuracy, errors, wall time, raw output, and
backend-reported token usage.

**The sample unit is the unique task, not the run.** With `--repeats` and/or
`--seeds` greater than 1, a task is run more than once; `summary.json` reports
each condition's `task_scores` (one score per task, averaged across its
repeats) and `accuracy` as the mean of those per-task scores — not the mean of
every individual run. Averaging over runs instead of tasks silently
double-counts whichever tasks happened to get more repeats.

`--repeats N` (requires `--seed`, not `--seeds`) re-runs every `(condition,
case)` N times, deriving a distinct seed per repeat from the base seed
(`derive_seed(base_seed, "repeat#i")`). `--seeds 11,22,33` instead runs once
per listed base seed; it cannot be combined with `--repeats > 1`, since
repeat seeds must derive from a single base seed. Optional case `domain`
fields produce a per-condition `by_domain` breakdown (also task-level, not
run-level).

Each result row records whether a seed was actually handed to the backend
request: `seed_sent`. This is `false` whenever every model/pool in the
condition uses the offline `echo` backend, since `EchoBackend` never builds an
outbound payload — report such runs as **stochastic repeats**, not seeded
runs. `seed_sent: true` (Ollama or an OpenAI-compatible backend) only confirms
the seed was placed on the outbound request; it is not proof the backend
actually used it to make output deterministic — inference-server seed support
varies. Report `seed_sent: true` runs as "seed sent; determinism not
verified", not as "reproducible".

When two or more `--condition`s are given, `summary.json` also reports
`paired`: a deterministic paired-bootstrap 95% CI (fixed RNG seed, stdlib
`random` only) on the per-task score difference between the first
`--condition` (treated as the baseline) and every other condition, computed
only over tasks common to both.

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
  --repeats 2 \
  --output-dir /tmp/thug-fugu-eval/echo-001
```

Every row from this command will have `seed_sent: false` and should be read
as a stochastic repeat, not a seeded run — the echo backend never builds an
outbound payload (see above).

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

- `manifest.json`: seed(s), repeats, temperature override, input hashes,
  model/backend names, roles, coordinator settings (including
  `orchestrator.seed`), quantization metadata, hardware metadata, output
  paths, and a rerun command.
- `results.jsonl`: full raw output and metrics for each `(condition, case,
  seed, repeat_index)` run, including `seed_sent`, per-worker `worker_outputs`
  (each with its own `passed`, from applying the task's grader to that
  worker's own output), and `stage_results` (reserved, empty until the
  sequential-DAG work lands).
- `results.csv`: spreadsheet-friendly preview and scalar metrics.
- `summary.json` (`schema_version: 3`): per-condition `task_scores`,
  task-level `accuracy` and `accuracy_stderr`, `by_domain`, `tokens_total`,
  `wall_ms_p50`/`wall_ms_p95`, and cross-condition `paired` comparisons.
- `inputs/`: task/config snapshots. Literal `api_key` values are redacted.

The original config path and SHA-256 are also recorded. A rerun uses the original
file when it still matches, otherwise it falls back to the sanitized snapshot.

## Rerun from a manifest

```bash
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --rerun-manifest /tmp/thug-fugu-eval/echo-001/manifest.json \
  --output-dir /tmp/thug-fugu-eval/echo-001-rerun
```

The generated `rerun.sh` contains the same command. Manifests from
`schema_version` 1 or 2 (produced before `--repeats`/`seed_sent`/paired
comparisons existed) can still be rerun; a rerun always writes a fresh
`schema_version: 3` manifest with `repeats` defaulted to `1` when the source
manifest predates it.

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

Any grader may set `"normalize": true` to strip common Markdown/LaTeX formatting
and convert Unicode subscript/superscript digits before matching. This is useful
for equivalent forms such as `H2O`, `H_2O`, `$\text{H}_2\text{O}$`, and `H₂O`.

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
and hostname. The harness always records the seed it requested; whether it was
actually sent to a real backend is reported per-row as `seed_sent` (see above),
and even a sent seed does not guarantee backend/model determinism.

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
