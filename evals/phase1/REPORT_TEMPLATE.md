# Phase 1 comparison report

Experiment manifest:

## Hardware and models

- Hardware:
- Model versions/digests:
- Quantization:
- Serving configuration:
- Seeds:
- Temperature:

## Conditions

1. Single model / one call
2. Same model ×3 + synthesizer selection
3. Same model ×3 + majority vote
4. Heterogeneous models ×3 + synthesis
5. Planner / solver / critic / judge
6. Large local model / one call
7. Cloud reference (optional)

## Results

### Quality by domain

| Condition | Math | Reasoning | QA | Coding | Overall | 95% CI |
|---|---:|---:|---:|---:|---:|---:|

### Resource use

| Condition | Mean latency | Total tokens | Mean tokens | Power | Estimated cost |
|---|---:|---:|---:|---:|---:|

## Compute-matched comparison

Describe which conditions were normalized by number of calls/tokens. Compare
quality at similar inference budgets.

## Time-matched comparison

Describe the wall-clock budget and any early termination. Compare quality under
the same time limit.

## Uncertainty and failure analysis

- Number of cases and seeds:
- Confidence intervals:
- Backend errors/timeouts:
- Cases where synthesis lost a correct candidate:
- Correlated errors between models:

## Conclusions

- Does any multi-model condition reproducibly beat the single-model baseline?
- Is the gain larger than the extra compute/token budget?
- Does heterogeneous diversity help more than repeated sampling?
- Does role specialization help?

## Recommended next strategy

State which Phase 2 inference strategy should be implemented next, with evidence.
