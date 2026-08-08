# Feature inventory and status audit

Parent Epic: [#69](https://github.com/masa-san-jp/Thug-Fugu/issues/69).
Deliverable for [#70](https://github.com/masa-san-jp/Thug-Fugu/issues/70).

This document is the single source of truth for what Thug-Fugu implements today,
how confident we are in each area, and where the docs still disagree with the
code. It is written to be consumed by follow-up issues (#71 doc sync, #72+
evaluation and distributed work).

Snapshot: `main` at v0.1.0 (`6275b96`), 2026-07-30.

## Status legend

| Status | Meaning |
|---|---|
| `stable` | Implemented, covered by tests, safe to rely on. |
| `experimental` | Implemented but minimal, or intentionally narrow scope. |
| `partial` | Implemented for a subset; explicit gaps remain. |
| `not implemented` | Design/spec only, no runtime code. |
| `deprecated` | Present but discouraged. |

Test counts below refer to `tests/` on this snapshot (208 tests total,
86% line+branch coverage, gate 85%).

## Feature matrix

### Orchestration

| Feature | Status | Code | Tests |
|---|---|---|---|
| Multi-role worker fan-out + synthesizer merge | `stable` | `orchestrator.py` (`_run_role_split`, `_run_workers`, `_synthesize`) | `test_orchestrator.py` |
| Deterministic merge fallback (no/failed synthesizer) | `stable` | `orchestrator.py` (`_deterministic_merge`) | `test_orchestrator.py` |
| Selection policies `all` / `keyword` | `stable` | `orchestrator.py` (`_select_worker_roles`), `config.py` | `test_orchestrator.py` |
| Adaptive coordinator `direct` / `role_split` / `parallel_ensemble` | `stable` | `coordinator.py`, `orchestrator.py` | `test_coordinator.py`, `test_orchestrator.py` |
| Normalized-majority and judge-tiebreak ensemble voting (`answers.py`, `coordinator.ensemble.normalize`/`judge_role`, `vote: "judge_tiebreak"`, `vote_summary`) | `stable` | `answers.py`, `orchestrator.py` (`_vote_content`, `_judge_tiebreak`), `config.py` | `test_answers.py`, `test_orchestrator.py` (`EnsembleVoteTests`), `test_config.py` |
| Verifier retry loop (bounded budget) | `stable` | `orchestrator.py` (`_run_verifier`) | `test_orchestrator.py` (`VerifierRetryTests`) |
| Request deadline + partial-result fallback | `stable` | `orchestrator.py` (`_run_workers`, deadline) | `test_orchestrator.py` (`RequestDeadlineTests`) |
| Structured non-sensitive per-run logging | `stable` | `orchestrator.py` (`_log_run`) | `test_orchestrator.py` (`ObservabilityTest`) |
| Optional per-request seeding (`orchestrator.seed`, `chat(seed=...)`), derived per worker/verifier/synthesizer/coordinator stream and passed to Ollama/OpenAI-compatible backends when set | `experimental` | `orchestrator.py` (`derive_seed`), `backends.py`, `coordinator.py` | `test_orchestrator.py` (`DeriveSeedTests`, `SeedPropagationTests`), `test_backends.py` (`SeedPayloadTests`), `test_config.py` |

### Model pools / routing / failover

| Feature | Status | Code | Tests |
|---|---|---|---|
| Model pools with multiple endpoints | `stable` | `config.py` (`ModelPoolConfig`), `routing.py` | `test_routing.py`, `test_config.py` |
| Routing policies `round_robin` / `least_busy` | `stable` | `routing.py` (`_attempt_order`) | `test_routing.py` |
| Failover across pool members | `stable` | `routing.py` (`chat`) | `test_routing.py` |
| Passive cooldown / circuit breaker | `stable` | `routing.py` (`_record_failure`) | `test_routing.py` |
| Active health probes (Ollama `/api/tags`, OpenAI `/v1/models`) | `stable` | `health.py`, `backends.py` (`probe_*`) | `test_health.py`, `test_backends.py` |
| Health-aware ordering + strict model presence | `stable` | `routing.py`, `health.py`, `config.py` | `test_routing.py`, `test_health.py` |
| Server plan / single-GPU parallel role planning | `stable` | `serverplan.py` | `test_serverplan.py` |

### Streaming

| Feature | Status | Code | Tests |
|---|---|---|---|
| Buffered SSE (all patterns) | `stable` | `server.py` (`_chat_completion_stream_events`) | `test_server.py` |
| True token streaming for `direct` | `stable` | `orchestrator.py` (`stream_direct_if_available`), `backends.py` (`stream_chat`) | `test_server.py`, `test_backends.py` |
| Role-split synthesizer token streaming | `stable` | `orchestrator.py` (`prepare_streaming_response`) | `test_server.py`, `test_orchestrator.py` |
| `stream_options.include_usage` final usage chunk | `stable` | `server.py` | `test_server.py` |
| `stream_options.include_progress` (`fugu_progress`) | `stable` | `server.py` | `test_server.py` |
| Multi-worker interleaved streaming | `not implemented` | — | — |

### Tool calling

| Feature | Status | Code | Tests |
|---|---|---|---|
| `tools` / `tool_choice` schema validation | `stable` | `server.py` (`_validate_tool_calling_request`) | `test_server.py` |
| Explicit client `tool_calls` execution | `stable` | `server.py`, `tools.py`, `consult.py` | `test_server.py`, `test_tools.py`, `test_consult.py` |
| Backend-generated synthesizer tool proposal | `stable` | `orchestrator.py` (`chat_with_backend_tools`), `backends.py` | `test_server.py`, `test_orchestrator.py` |
| One-round allow-listed backend tool execution | `stable` | `orchestrator.py`, `tools.py` | `test_orchestrator.py`, `test_server.py` |
| `tools`/`tool_choice` pass-through (OpenAI + Ollama) | `stable` | `backends.py` | `test_backends.py` |
| Built-in tool registry (`echo`, `lookup_static`) | `experimental` | `tools.py` (`default_tool_registry`) | `test_tools.py` |
| Multi-round autonomous tool loops | `not implemented` | guarded against in `chat_with_backend_tools` | `test_orchestrator.py` |
| Worker-side tools / side-effecting tools | `not implemented` | — | — |

### Interfaces (CLI / MCP / HTTP)

| Feature | Status | Code | Tests |
|---|---|---|---|
| CLI `run` (plain + `--json`), `serve`, `validate-config` | `stable` | `cli.py` | `test_cli.py` |
| `consult()` core | `stable` | `consult.py` | `test_consult.py` |
| MCP `consult_thug_fugu` tool | `experimental` | `mcp_server.py` (requires `[mcp]` extra) | not covered (coverage-omitted; no automated test) |
| OpenAI-compatible `/v1/chat/completions`, `/v1/models`, `/health` | `partial` | `server.py` | `test_server.py` |
| Server concurrency limit + bounded queue | `stable` | `server.py`, `config.py` | `test_server.py`, `test_config.py` |

### Security / operations

| Feature | Status | Code | Tests |
|---|---|---|---|
| Loopback-default bind + `--allow-unsafe-bind` opt-in | `stable` | `server.py` (`validate_bind_host`), `cli.py` | `test_server.py`, `test_cli.py` |
| Backend response-body + URL query/fragment redaction | `partial` | `backends.py` (`_safe_url`) | `test_backends.py` |
| Health endpoint URL credential/query redaction | `stable` | `routing.py` (`_safe_endpoint_label`) | `test_routing.py`, `test_server.py` |
| Request body size limit | `stable` | `server.py` (`MAX_REQUEST_BODY_BYTES`) | `test_server.py` |
| Token usage accounting | `stable` | `orchestrator.py` (`_aggregate_usage`), `backends.py` | `test_orchestrator.py`, `test_backends.py` |
| Reproducible single-vs-multi evaluation harness | `stable` | `scripts/evaluate_orchestration.py`, `evals/` | `test_evaluate_orchestration.py` |
| Phase 1 comparison matrix / multi-seed domain statistics | `experimental` | `evals/phase1/`, `scripts/run_phase1_comparison.sh` | `test_evaluate_orchestration.py` |
| Evaluator seed pass-through, task-level accuracy (`schema_version: 3`), and paired-bootstrap condition comparison (`--repeats`, `seed_sent`, `task_scores`, `worker_outputs[].passed`, `paired`) | `experimental` | `scripts/evaluate_orchestration.py` (`_run_case`, `_summarize`, `_paired_bootstrap_ci`) | `test_evaluate_orchestration.py` |

### Distributed inference (Epic target)

| Feature | Status | Code | Tests |
|---|---|---|---|
| Static per-model/per-endpoint distribution via `base_url` | `stable` | `config.py`, `routing.py` | `test_routing.py` |
| Static cross-machine endpoint health + failover | `stable` | `health.py`, `routing.py` | `test_health.py`, `test_routing.py` |
| Node registration / discovery | `not implemented` | design only: `docs/design/distributed-inference.md` | — |
| Registered-node health/load inventory | `not implemented` | design only | — |
| Dynamic registered-node failover / per-node backpressure | `not implemented` | design only | — |
| Automatic model placement / lifecycle | `not implemented` | design only | — |
| Coordinator redundancy (no SPOF) | `not implemented` | design only | — |
| Single-vs-multi-model comparison framework | `stable` | `scripts/evaluate_orchestration.py` | `test_evaluate_orchestration.py` |
| Reproducible empirical single-vs-multi results | `not implemented` | tracked by #73–#75 | — |

## Documentation synchronization

Resolved by [#71](https://github.com/masa-san-jp/Thug-Fugu/issues/71) on
2026-07-30:

- `local-llm-orchestration.md` now describes streaming/tool calling as
  implemented and lists current non-goals/future work.
- `distributed-inference.md` now separates implemented static endpoint
  distribution/health/failover from unimplemented registered-node clustering.
- `fugu-style-coordinator-spec.md` now marks implemented and partial phases.
- `CONTRIBUTING.md` and the pull request template require feature status and
  documentation consistency review.

## Known gaps and missing tests

- `mcp_server.py` has no automated test and is excluded from coverage
  (`pyproject.toml` `tool.coverage.run.omit`). MCP wiring is only manually verified.
- `backends.py::_safe_url()` drops query strings/fragments but retains URL userinfo
  (`user:password@host`) in backend error text. Health snapshots use a separate
  safe formatter and are covered. Backend error URL credential stripping needs a
  focused fix and regression test.
- The built-in tool registry is intentionally tiny; there is no plugin/registration
  mechanism for user tools.
- The evaluation harness records quality, latency, errors, raw output, token
  usage, config/seed/quantization/hardware metadata, and rerunnable manifests.
  Automatic power and total-cost collection remains Phase 1 work (#74).
- No machine-readable capability profile per model exists yet (Epic #69 Phase 2/3;
  issues #82–#85).

## How to update this document

Update this inventory whenever a feature changes status. Each row must keep a code
reference and, for `stable`/`experimental`/`partial`, a test reference. New
not-implemented rows should link the tracking issue.
