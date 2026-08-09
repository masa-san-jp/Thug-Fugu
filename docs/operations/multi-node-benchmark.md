# Multi-node cluster benchmark (`scripts/benchmark_cluster.py`)

**This measures performance only: throughput, latency, and request
success rate for a `model_pool`.** It does not measure answer quality or
accuracy in any way, and its output must never be read as evidence for or
against the "coordinating multiple models improves answer quality"
hypothesis. Quality/accuracy is measured separately by
`scripts/evaluate_orchestration.py` and `scripts/analyze_results.py`
(WP-1/WP-6, [`evaluation-harness.md`](evaluation-harness.md)). This script
only speaks to the separate "pool several cheap machines together"
performance/cost hypothesis tracked by WP-8
(`docs/plans/phase2-decision-implementation-plan.md`).

## What it measures

- **Throughput**: completed requests per second, at each of several
  concurrency levels you choose.
- **Latency**: p50 / p95 / p99, in milliseconds, computed from successful
  requests only (nearest-rank percentile, no numpy/scipy dependency).
- **Success rate**: percentage of requests that completed without error.
- **Degradation curve** (optional, `--outage-member`): the same sweep run
  twice -- once with every pool member healthy ("baseline"), once with one
  member simulated as stopped ("degraded") -- so you can see how much the
  pool's built-in failover (`src/fugu_local/routing.py`) costs in success
  rate and added latency when a member goes down.

It reuses the existing `model_pool` config, `ModelRouter`, and `health.py`
failover machinery -- there is no separate "cluster mode" to configure.

## Usage

Point it at an existing config with a `model_pools[]` entry:

```bash
PYTHONPATH=src python3 scripts/benchmark_cluster.py \
  --config examples/fugu-local.ollama.json \
  --pool fast \
  --concurrency-levels 1,2,4,8 \
  --requests-per-level 40 \
  --prompt "Summarize the word 'benchmark' in one sentence." \
  --hardware-json hardware.json \
  --output cluster-benchmark.json
```

To also measure the failover degradation curve, add `--outage-member` with
one of the pool's configured endpoint URLs:

```bash
PYTHONPATH=src python3 scripts/benchmark_cluster.py \
  --config examples/fugu-local.ollama.json \
  --pool fast \
  --concurrency-levels 1,2,4,8 \
  --requests-per-level 40 \
  --outage-member http://192.168.1.20:11434 \
  --output cluster-benchmark.json
```

The named endpoint is not literally stopped over the network -- the script
simulates the outage in-process by making every call routed to that member
fail, so you don't need to actually SSH into a second machine and kill its
Ollama process for this to be meaningful. If you want to measure a *real*
process kill (including OS-level TCP reset/timeout behavior), stop that
node's server yourself before invoking the script with the same
`--outage-member` value omitted (the router will discover the real failure
through actual failed calls instead).

## `hardware.json`

There is no automated power or hardware measurement -- provide it by hand:

```json
{
  "nodes": [
    {"name": "node-a", "cpu": "Apple M2", "ram_gb": 16, "power_watts_idle": 8},
    {"name": "node-b", "cpu": "Intel i5-1135G7", "ram_gb": 16, "power_watts_idle": 12}
  ],
  "notes": "Measured with a Kill A Watt at the wall for 60s idle."
}
```

If `--hardware-json` is omitted, the output records only auto-detectable,
non-power metadata (hostname, OS, Python version) under `hardware.source:
"auto"`, with a note that GPU/VRAM/RAM/power details need to be supplied
manually.

## Output

`cluster-benchmark.json`:

```json
{
  "schema_version": 1,
  "note": "Throughput, latency, and request success-rate only. ...",
  "pool": "fast",
  "conditions": {
    "baseline": [
      {"concurrency": 1, "throughput_rps": 2.1, "success_rate_pct": 100.0,
       "latency_p50_ms": 410.2, "latency_p95_ms": 890.1, "latency_p99_ms": 950.4, ...}
    ],
    "degraded": [ ... ]
  },
  "outage_member": "http://192.168.1.20:11434",
  "hardware": { ... }
}
```

`conditions.degraded` is present only when `--outage-member` was passed.
This file is the intended input to WP-9's `decide_phase2.py`, which reads
`success_rate_pct` and `latency_p95_ms` from `baseline` vs `degraded` to
evaluate the Efficiency Pivot "graceful degradation" criteria.

## Testing

`tests/test_benchmark_cluster.py` covers the percentile/throughput math,
the outage-injection behavior (with and without a healthy failover
partner), and a full `main()` round trip -- all against an in-process stub
backend. No real network call is made in CI.
