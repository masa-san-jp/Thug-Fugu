# Phase 1 local comparison — 3-seed preliminary report

Date: 2026-08-02  
Tracking issue: [#73](https://github.com/masa-san-jp/Thug-Fugu/issues/73)

## Setup

- Apple M4 Max: 16 CPU cores, 40 GPU cores
- 128 GB unified memory
- Ollama local endpoint
- Power: AC; no external power meter, so power was not measured
- 12 deterministic tasks: math, reasoning, QA, coding (3 each)
- Seeds: 11, 22, 33
- 36 runs per condition
- Models: gemma4:e4b, gemma4:26b, gpt-oss:20b, qwen2.5:0.5b

Raw outputs and hostname-bearing manifests are intentionally not committed. The
sanitized aggregate is in
`evals/phase1/results/2026-08-02-local-3seed.summary.json`.

## Aggregate result

| Condition | Accuracy | 95% Wilson CI | Mean latency | Total tokens |
|---|---:|---:|---:|---:|
| single-e4b | 36/36 (100%) | 90.36–100% | 6.865s | 9,465 |
| large-local | 36/36 (100%) | 90.36–100% | 7.028s | 9,329 |
| same3-majority | 36/36 (100%) | 90.36–100% | 20.875s | 29,154 |
| heterogeneous3 | 36/36 (100%) | 90.36–100% | 26.774s | 46,129 |
| same3-synth | 36/36 (100%) | 90.36–100% | 31.173s | 50,813 |
| role-specialized | 36/36 (100%) | 90.36–100% | 37.075s | 68,499 |

All four domains were 100% for every condition after answer-format
normalization. The original water-formula false negative was caused by valid
LaTeX/Unicode forms (`H₂O`, `\text{H}_2\text{O}`), not model error.

## Compute comparison

Relative to `single-e4b`:

| Condition | Latency ratio | Token ratio |
|---|---:|---:|
| large-local | 1.02× | 0.99× |
| same3-majority | 3.04× | 3.08× |
| heterogeneous3 | 3.90× | 4.87× |
| same3-synth | 4.54× | 5.37× |
| role-specialized | 5.40× | 7.24× |

## Preliminary conclusion

This task set has a strong ceiling effect. No multi-model strategy produced a
measurable accuracy gain, while all multi-model strategies used substantially
more time and tokens.

The evidence supports:

1. Use direct/single-model execution for easy tasks.
2. Do not enable role-specialized or synthesizer-heavy strategies by default.
3. Prefer majority over synthesizer aggregation when quality is tied, because it
   is materially cheaper.
4. Before selecting the next inference strategy, build a harder dataset that can
   separate model capabilities and expose correlated errors.

## Remaining work for #73

- Add harder tasks to remove the ceiling effect.
- Re-run the matrix and analyze model diversity versus repeated sampling.
- Decide whether to add a cloud reference condition; do not substitute a local
  OpenAI-compatible endpoint for a cloud frontier model.
- Produce the final strategy recommendation from the harder comparison.
