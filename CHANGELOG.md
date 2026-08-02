# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Reproducible single-vs-multi configuration evaluation bundles with manifests,
  input snapshots, full JSONL outputs, CSV/summary metrics, token usage, hardware
  and quantization metadata, and manifest-based reruns.
- Multi-seed and per-domain evaluation summaries with Wilson 95% confidence
  intervals, plus a fixed local Phase 1 comparison matrix and report template.
- Optional deterministic-answer normalization for Markdown, LaTeX, and Unicode
  subscript/superscript formatting.
- Sanitized three-seed Phase 1 local comparison report and aggregate metrics.

## [0.1.0] - 2026-07-30

First tagged release of Thug-Fugu, a standard-library-only multi-role local LLM
orchestrator for Ollama and OpenAI-compatible backends.

### Added

#### Orchestration
- Multi-role fan-out (planner / coder / reviewer / …) with a synthesizer role and
  deterministic merge fallback when synthesis is unavailable.
- Role selection policies `all` and `keyword`, with keyword routing based on the
  latest user message.
- Fugu-style adaptive coordinator selecting `direct`, `role_split`, or
  `parallel_ensemble` from rules, heuristics, and an optional meta-model call.
- Verifier retry loop with a bounded budget and best-available fallback.
- Request-level deadline with partial-result fallback.
- Structured, non-sensitive per-run logging.

#### Backends and model pools
- Ollama and OpenAI-compatible chat backends, plus an offline `echo` backend.
- Model pools with `round_robin` / `least_busy` routing and failover across
  endpoints.
- Passive cooldown (circuit breaker) for failing pool endpoints.
- Active health probes (`/api/tags`, `/v1/models`) with health-aware routing,
  optional strict model-presence checks, and `/health` observability.
- Real backend token-usage accounting across workers, verifier, and synthesizer.

#### HTTP API
- OpenAI-compatible `POST /v1/chat/completions`, `GET /v1/models`, and `/health`.
- True token streaming for `direct` requests and role-split synthesizer output,
  with buffered SSE fallback for complex orchestration.
- Optional `stream_options.include_usage` and `stream_options.include_progress`
  (the `fugu_progress` extension event).
- Server-side tool calling: explicit client `tool_calls`, backend-generated
  synthesizer tool proposals, and one allow-listed execution round with
  `tools` / `tool_choice` pass-through.
- Server-level concurrency limit with an optional bounded request queue.

#### Agent integration and CLI
- `consult()` core plus the Claude Code MCP `consult_thug_fugu` tool.
- `fugu-local` CLI with `run` (plain and `--json`), `serve`, and
  `validate-config`, plus a coordinator evaluation harness.

### Security
- Loopback-only default bind with explicit `--allow-unsafe-bind` opt-in.
- Backend HTTP error bodies and endpoint credentials/query strings are redacted
  from user-visible output and health snapshots.
- Tool execution is allow-listed, timeout-bounded, output-truncated, and disabled
  by default.

[0.1.0]: https://github.com/masa-san-jp/Thug-Fugu/releases/tag/v0.1.0
