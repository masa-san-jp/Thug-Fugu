# Phase 1 single-vs-multi comparison protocol

Tracking issue: [#73](https://github.com/masa-san-jp/Thug-Fugu/issues/73).

This protocol separates gains from repeated compute, model diversity, and role
specialization.

## Fixed comparison matrix

| Label | Configuration |
|---|---|
| `single-e4b` | gemma4:e4b, one direct call |
| `same3-synth` | gemma4:e4b ×3, synthesizer vote |
| `same3-majority` | gemma4:e4b ×3, deterministic majority |
| `heterogeneous3` | gemma4:e4b + gpt-oss:20b + qwen2.5:0.5b, gemma4:26b synthesis |
| `role-specialized` | planner / solver / critic / judge / synthesizer |
| `large-local` | gemma4:26b, one direct call |
| `cloud-reference` | optional OpenAI-compatible reference endpoint |

The checked-in model metadata matches the local Ollama inventory observed on
2026-07-30. Verify digests before every published experiment.

## Run

1. Copy and fill hardware metadata:

   ```bash
   cp evals/phase1/hardware.example.json evals/phase1/hardware.json
   ```

2. Confirm all local model tags:

   ```bash
   curl -s http://127.0.0.1:11434/api/tags
   ```

3. Run three seeds:

   ```bash
   scripts/run_phase1_comparison.sh results/phase1/local-001
   ```

Override seeds:

```bash
SEEDS=101,202,303 scripts/run_phase1_comparison.sh results/phase1/local-002
```

Cloud reference (through an OpenAI-compatible endpoint/proxy):

```bash
CLOUD_CONFIG=/path/cloud.json \
CLOUD_METADATA=/path/cloud-meta.json \
scripts/run_phase1_comparison.sh results/phase1/with-cloud-001
```

Never commit API credentials or unreviewed raw prompts/outputs.

## Compute-matched vs time-matched

The default matrix is **not compute matched**: ensemble and role-specialized
conditions deliberately use more calls. Report it as the "natural configuration"
comparison.

For compute-matched analysis:

- compare `single-e4b` against repeated calls at the same total-token ceiling;
- report quality per 1,000 tokens;
- keep the synthesizer/judge token budget in the total.

For time-matched analysis:

- set a common wall-clock deadline;
- record incomplete/error rows rather than dropping them;
- compare accuracy within the deadline.

The current harness records latency/tokens but does not enforce a cross-condition
budget automatically. Budget enforcement is part of #74/#80; document manual
limits in the report until then.

## Reporting

Copy `evals/phase1/REPORT_TEMPLATE.md` into the experiment result directory.
Use `summary.json` for overall/domain accuracy and Wilson 95% intervals, and
`results.jsonl` for error analysis. Preserve the manifest and all input
snapshots.

Issue #73 remains open until all seven conditions (or a documented unavailable
cloud condition), multiple seeds, uncertainty, and the final strategy
recommendation are committed as a reproducible report.
