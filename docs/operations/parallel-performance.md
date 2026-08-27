# Parallel performance benchmark contract

This document is the operator-facing contract for performance work packages
under [#125](https://github.com/masa-san-jp/Thug-Fugu/issues/125). The benchmark
does not start Ollama, contact an OpenAI-compatible endpoint, or change the
production scheduler. It uses a bounded fake backend so CI can verify the
measurement and gate logic without an LLM or external network.

## Reproduce an artifact

From the repository root:

```sh
PYTHONPATH=src python3 scripts/benchmark_parallelism.py \
  --output /tmp/thug-fugu-parallelism.json \
  --runs 3 \
  --seed 7
python3 scripts/benchmark_parallelism.py \
  --validate /tmp/thug-fugu-parallelism.json
```

The command runs the N=2 and N=4 serial baselines and fan-out comparisons,
concurrent orchestration requests, unequal worker latency, worker failure,
deadline expiration, endpoint capacities 1/2/4, and explicit cold/warm runs.
Use `--format jsonl` when a line-oriented consumer is required. JSON is the
canonical interchange format; JSONL has one manifest record followed by one
scenario record per line and is accepted by the same validator.

The fake backend parameters are exposed for controlled experiments:

```sh
PYTHONPATH=src python3 scripts/benchmark_parallelism.py \
  --output /tmp/parallelism-small.json \
  --runs 5 --latency-ms 25 --cold-start-ms 10 --seed 7
```

`seed` is part of the config contract even though the v1 fake backend has no
random branch. Future latency distributions must derive all randomness from
this seed. Timing values are expected to vary between runs; the scenario list,
field names, and config hash remain stable for the same inputs and seed.

## Artifact schema v1

The top-level JSON object contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer version, currently `1`. |
| `artifact_type` | `thug-fugu.parallel-performance`. |
| `benchmark` | `parallelism-contract`. |
| `commit` | Git commit being measured, or `unknown`. |
| `config_hash` | SHA-256 of the canonical benchmark configuration. |
| `seed` / `config` | Reproduction inputs and scenario definitions. |
| `backend` | Backend kind, model label, and runtime label. v1 is fake-only. |
| `hardware` | Non-identifying local process metadata; hostname is excluded. |
| `privacy` | Explicit assertions that raw content, API keys, and hostnames are not saved. |
| `scenarios` | Request-level samples and percentile summaries. |
| `comparisons` | N=2/N=4 serial-vs-parallel calculations and gate results. |

Each scenario has `id`, `kind`, `mode`, `n`, `worker_count`,
`endpoint_count`, `endpoint_capacity`, `warm_run`, `sample_unit`, `samples`,
and `summary`. A request sample has one `worker_results` entry per submitted
worker. Every worker entry records `submitted_ms`, `start_ms`, `end_ms`,
`queue_wait_ms`, `warm`, `success`, and a bounded `error_kind`; worker content
and exception messages are intentionally absent.

The `metrics` object uses the #125 vocabulary:

| Metric | Definition |
| --- | --- |
| `critical_path_ms` | Earliest admitted worker start to latest worker end. |
| `dispatch_skew_ms` | Latest worker submission minus earliest submission. |
| `queue_wait_ms` | Mean endpoint admission wait for the request. |
| `orchestration_overhead_ms` | Request wall time minus critical path, floored at zero. |
| `throughput_rps` | Successful workers per request wall second; request-throughput scenarios also store batch throughput. |
| `capacity_violation` | True only if observed in-flight work exceeded configured endpoint capacity. |

`summary` and `comparisons` expose `p50`, `p95`, and `p99` for each timing or
rate series. Percentiles use nearest-rank selection over the sorted samples.
For fewer than 20 samples, p95 and p99 are still emitted using the highest
available rank; they are descriptive, not statistically stable evidence. An
empty series is represented as JSON `null`, never as a fabricated zero.

The comparison fields are `T_serial_workers_ms`, `T_parallel_workers_ms`,
`worker_speedup`, `parallel_efficiency`, `dispatch_skew_ms`,
`orchestration_overhead_ms`, `queue_wait_ms`, `throughput_rps`, and
`capacity_violation`. `worker_speedup` is the serial p50 critical path divided
by the parallel p50 critical path; `parallel_efficiency` is speedup divided by
N. The fake-backend CI graduation gates are the thresholds from #125:

- N=2 worker speedup >= 1.8
- N=4 worker speedup >= 3.4
- parallel dispatch skew p95 <= 50 ms
- no capacity violation

These gates validate the benchmark contract. They do not prove production
runtime performance.

## Privacy and validation

`--validate` checks required top-level fields, all required scenarios, one
worker result per worker, required metric fields, schema version, and obvious
secret-bearing keys or values. The validator rejects fields such as
`api_key`, `authorization`, `password`, `secret`, `hostname`, and raw worker
content. Keep real endpoint URLs, prompts, responses, and credentials outside
the artifact. Hardware metadata intentionally contains platform, Python
version, and logical CPU count only.

Schema v1 is append-only for consumers: new optional fields may be added while
existing fields retain their meaning. A breaking field or semantic change
requires a new `schema_version` and a migration note in this document. Readers
must reject unknown schema versions rather than silently interpreting them.

## Existing benchmark ownership and migration

`scripts/benchmark_parallel_roles.py` remains the ad-hoc real-orchestrator
wall-time tool for inspecting selected role patterns and optional CSV output.
`scripts/benchmark_cluster.py` remains the model-pool throughput, latency, and
failover harness. This benchmark owns the stable parallelism contract and
fake-backend CI evidence. It does not replace either tool in v1.

When a production scheduler or transport work package lands, it should emit
this artifact shape (or an explicitly versioned adapter) and attach a real
runtime report alongside the fake-backend report. The real report must label
backend/model/runtime, endpoint count and capacity, warm/cold policy, and
hardware separately; it must not mix real measurements into the fake sample
series. Migration to a new producer is complete only when the validator and
the #125 comparison fields remain available.
