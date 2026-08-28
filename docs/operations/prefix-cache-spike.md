# Shared-prefix / prefix-cache spike

This spike measures whether changing the prompt shape can reduce fan-out
prefill work while preserving instruction priority and role separation. It is
research instrumentation only: `src/fugu_local/` is intentionally unchanged,
and the artifact never decides Go or No-Go.

## Reproducible command

The runner compares three variants for the same case, worker count, and repeat
count:

- `current`: each worker starts with its role-specific system instruction and
  receives the shared context in the user message;
- `common_prefix`: the shared context is the identical first system message,
  followed by the role-specific system instruction and user task;
- `runtime_native`: the same portable common-prefix shape, run against the
  runtime's native cache launch configuration.

Offline contract fixture:

```bash
python3 scripts/benchmark_prefix_cache.py \
  --manifest tests/fixtures/prefix-cache/manifest-ollama.json \
  --fixture tests/fixtures/prefix-cache/ollama.json \
  --cases short \
  --workers 2 \
  --repeats 2 \
  --output artifacts/prefix-cache-fixture.json
```

Live run (requires an already-running server and an available model):

```bash
python3 scripts/benchmark_prefix_cache.py \
  --manifest /path/to/prefix-cache-manifest.json \
  --cases short,long_context,japanese,coding,prompt_injection \
  --workers 2 \
  --repeats 3 \
  --timeout 120 \
  --output artifacts/prefix-cache-live.json
```

The manifest records runtime, model, quantization, endpoint, launch command,
launch flags, and hardware metadata. It may contain `api_key_env` or a normal
Thug-Fugu model config with `${ENV_VAR}` auth; keys are read only for the live
request and are never written to the artifact. No server is launched and no
model is downloaded by the runner.

## Metrics and interpretation

Each successful worker record contains TTFT (`ttft_ms`), total request time
(`wall_ms`), runtime-reported prompt evaluation time when available
(`prompt_eval_time_ms`), prompt token count when available, and cache-hit
evidence. Variant summaries report medians and a worker makespan median. The
comparison section reports reduction percentages only when both medians are
numeric; otherwise the value is `null`.

Cache evidence has three explicit forms:

- `direct`: the runtime returned an explicit cache-hit field;
- `estimated`: a repeat-to-repeat prompt-eval timing heuristic, explicitly not
  proof of a cache hit;
- `unknown`: the runtime did not expose a hit signal and timing was insufficient
  for estimation.

Ollama normally does not expose a portable prefix-cache hit field in this
artifact contract. That limitation is recorded rather than omitted. Runtime
native support is also recorded as `supported` / `unsupported` / `unknown` from
the manifest and must be confirmed by the measured artifact; a launch flag by
itself is not evidence.

## Safety and quality controls

The portable message contract is deliberately narrow:

```text
system: shared context (identical across workers)
system: role-specific instruction
user:   task and untrusted input
```

The artifact records static checks that the shared prefix is identical, the
role instruction remains a system message, and user text is never promoted to
system priority. Actual system-instruction priority, role separation, and
prompt-injection resistance remain `unknown` until graded live outputs are
reviewed. Raw prompts and completions are omitted from the artifact.

## Go / No-Go gate

This is a HUMAN GATE. The issue's Go threshold is at least 15% median reduction
in long-context fan-out worker-stage latency or prompt-eval time on one
supported runtime, with no quality or instruction-following regression. The
artifact always emits `go_no_go.decision: "human_gate"`; it must not be used as
a marketing claim or as a fixed default.

If the real evidence is below 15%, cache support is too runtime-specific, or
instruction semantics cannot be preserved, record No-Go in the issue and do
not implement a prompt-shape change. If Go is justified, open a separate
implementation issue that defines a safe fallback to `current` whenever the
runtime lacks the required cache behavior.

Apple Silicon personal-use measurements and GPU-server measurements are
separate evidence classes. Record exact server version, model, quantization,
launch flags, hardware, and power-measurement setup before comparing them.
