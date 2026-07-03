# Evaluation harness

The evaluation harness compares multiple Thug-Fugu configurations against the same
JSONL task set. It is the first lightweight implementation of the A/B/C comparison
called out in `docs/design/fugu-style-coordinator-spec.md`.

Typical conditions:

- **A**: direct single-role baseline
- **B**: static role-split baseline
- **C**: adaptive coordinator config

The script is dependency-free and uses deterministic graders first. LLM-judge and
coding/unit-test graders can be added later.

## Task format

Each line is one JSON object:

```json
{"id":"capital-france","prompt":"What is the capital of France?","grader":{"type":"contains","value":"Paris"}}
```

Supported graders:

| Type | Fields | Meaning |
|---|---|---|
| `contains` | `value` | Case-insensitive substring match |
| `regex` | `pattern` | Python regex search with `IGNORECASE | MULTILINE` |
| `exact` | `value` | Exact string match after trimming whitespace |

See `evals/smoke.jsonl` for a minimal example.

## Run

```bash
PYTHONPATH=src python3 scripts/evaluate_orchestration.py \
  --cases evals/smoke.jsonl \
  --condition A=examples/fugu-local.single-gpu.json \
  --condition B=examples/fugu-local.model-pool.json \
  --condition C=examples/fugu-local.coordinator.json \
  --csv /tmp/thug-fugu-eval.csv \
  --summary /tmp/thug-fugu-eval-summary.json
```

For offline wiring checks, `examples/fugu-local.echo.json` can be used, but the
sample QA graders are expected to fail because the echo backend does not answer the
questions.

## Outputs

- CSV: one row per `(condition, case)` with pass/fail, wall-clock latency, pattern,
  worker count, error, and content preview.
- Summary JSON: aggregate cases, passed count, accuracy, errors, mean/median wall
  time per condition.

## Interpretation

The goal is not only maximum accuracy. Track accuracy together with wall-clock time
and errors. If the adaptive coordinator does not beat or at least match a simpler
baseline on the chosen task set, disable that pattern or refine coordinator rules.
