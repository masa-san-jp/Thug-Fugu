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
- Optional request seeding: `ChatRequest.seed` is passed through to the Ollama
  (`options.seed`) and OpenAI-compatible (`seed`) backend payloads when set, and
  a new `orchestrator.seed` config key (overridable per call via `chat(seed=...)`)
  derives a distinct, deterministic seed per worker/verifier/synthesizer/
  coordinator request so same-model roles don't collapse to identical output.
  Omitted by default; existing configs and requests are unaffected. Seeding is
  best-effort — backends are not required to honor it.
- `scripts/evaluate_orchestration.py --repeats N`: stochastic repeats per
  `(condition, case)` seeded from a single base seed via
  `derive_seed(base_seed, "repeat#i")` (mutually exclusive with `--seeds`).
  Each result row now records `repeat_index`, `seed_sent` (whether the seed
  actually reached a real backend payload — always `false` for the offline
  `echo` backend), per-worker `worker_outputs[].passed` (the task grader
  applied to that worker's own output, for synthesizer damage/repair
  analysis), and a `stage_results` placeholder for the future sequential-DAG
  work. `summary.json` gained a deterministic paired-bootstrap 95% CI
  (`paired`) between the first condition and every other condition.
- New `src/fugu_local/answers.py` module: `normalize_answer` (Unicode NFKC,
  Markdown/code-fence stripping, answer-prefix removal, whitespace/case
  normalization, and number-format normalization for strings that are
  entirely numeric), `extract_final_answer`, `cluster_answers`, and
  `majority_vote`. Shared by ensemble voting and the evaluation harness's
  graders instead of each maintaining its own copy.
- Ensemble voting gains **normalized majority** voting (`coordinator.
  ensemble.normalize`, default `true`): equivalent answers like `42`,
  `**42**`, and `Answer: 42` now count as the same vote instead of splitting
  votes and effectively returning whichever candidate happened to come first.
  Set `normalize: false` to restore the previous exact-match voting.
- New `coordinator.ensemble.vote: "judge_tiebreak"` mode: runs normalized
  majority voting, and only when the winning cluster is tied with another
  makes one additional call to a judge role (`coordinator.ensemble.judge_role`,
  or a `roles[]` entry with `is_verifier: true`) to pick between the tied
  candidates. Falls back to the normalized-majority tie-break if the judge
  call fails or returns an unparseable choice. Judge calls receive a
  deterministic per-role seed, contribute their token usage to the final run
  total, and emit a content-free warning when fallback is required.
- `OrchestrationResult.vote_summary` (cluster count, winning vote count,
  whether normalization was applied, whether the judge was called) is now
  recorded for `parallel_ensemble` runs and included in structured run logs.
- New `scripts/validate_tasks.py` (WP-2a): validates the hard-benchmark-v2
  task schema (`family`/`difficulty`/`answer_type`/`grader.type`
  allow-lists; rejects `exec` and rubric graders outright), cross-file
  `id` uniqueness, a `gold` self-consistency check (does the task's own
  grader accept its own `gold`), and the calibration/dev/test split's
  structural rules (minimum sizes, per-family minimums, the 20% easy-task
  cap). The task files themselves are WP-2b and require human review of
  gold-answer correctness before use; this validator checks schema and
  self-consistency only, never answer correctness, and never touches
  `review_status`. See `docs/operations/benchmark-v2.md`.
- New `coordinator.default_pattern`/`rules[].pattern` value `sequential_dag`:
  a fixed 7-stage inference DAG (`planner` → `solver` → `verifier` →
  `critic` → `reviser` → `claim_judge` → `writer`, `src/fugu_local/
  stages.py`) where each stage's prompt is built from the accumulated,
  structured output of every prior completed stage instead of all roles
  receiving the same input (`src/fugu_local/pipeline.py`,
  `orchestrator.py::_run_sequential_dag`). Stage responses are parsed
  leniently (`stages.parse_stage_output`); parse failures degrade to the raw
  text instead of raising. Configured via `coordinator.dag.stages[]`
  (`name`, `role`, `enabled`, `fanout`) and `coordinator.dag.
  max_stage_tokens`; `solver` and `writer` cannot be disabled, `fanout` is
  only valid on `solver`, and disabling `critic` also skips `reviser` (its
  input contract requires a critique). Disabling any other stage applies a
  documented per-stage bypass rule rather than passing input straight
  through. Not combinable with `tool_calling.enabled` and not eligible for
  true token streaming (falls back to buffered SSE). `OrchestrationResult`
  gains `stage_results` (every stage call, in order) and `warnings`
  (e.g. deadline exceeded mid-DAG). See
  `docs/design/sequential-inference-dag.md` and
  `examples/fugu-local.sequential-dag.json`.

### Changed
- **Breaking**: `coordinator.ensemble.vote: "majority"` now normalizes
  answers before counting votes by default (`coordinator.ensemble.normalize`
  defaults to `true`). Previously, votes were counted by exact string match,
  so answers differing only in Markdown formatting, whitespace, or an
  "Answer:" prefix silently split their votes. Set
  `coordinator.ensemble.normalize: false` to restore the exact-match
  behavior used before this release.
- `scripts/evaluate_orchestration.py` summary schema is now `schema_version:
  3`. Per-condition `accuracy` is the mean of *per-task* pass rates
  (`task_scores`), not the mean of every individual run — averaging over runs
  instead of tasks silently double-counted whichever tasks got more
  repeats/seeds. `accuracy_ci95` (run-level Wilson interval) and per-domain
  `domains` are replaced by `accuracy_stderr` (task-level standard error) and
  `by_domain` (task-level per-domain accuracy); `total_tokens` is renamed
  `tokens_total`. Manifests and results from `schema_version` 1/2 can still be
  rerun via `--rerun-manifest`.
- `scripts/evaluate_orchestration.py`'s deterministic-answer normalization
  (`grader.normalize: true`) now delegates Markdown/code-fence stripping and
  whitespace/case/number normalization to `fugu_local.answers.normalize_answer`
  instead of a separate local implementation; LaTeX-specific handling stays
  local. `normalize: true` graders are now also slightly more lenient (e.g.
  an `exact` grader now matches case-insensitively, matching `contains` and
  `regex`'s existing case-insensitive behavior) since the shared
  normalization casefolds.

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
