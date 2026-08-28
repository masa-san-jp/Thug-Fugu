# Runtime conformance and backend exchange

Thug-Fugu keeps Ollama as the simplest local default, but its parallel
execution goal should not depend on a single server. This document defines the
repeatable check for exchanging the runtime by configuration only. The runner
does not add runtime-specific APIs to the orchestrator and never installs a
server or downloads a model.

## One command, one artifact

The source of truth is the manifest plus the runner schema in
[`scripts/check_runtime_conformance.py`](../../scripts/check_runtime_conformance.py).
Each file in [`examples/runtimes/`](../../examples/runtimes/) is both a valid
Thug-Fugu config and a runtime recipe.

Offline contract check:

```bash
python3 scripts/check_runtime_conformance.py \
  --manifest examples/runtimes/ollama.json \
  --fixture tests/fixtures/runtime-conformance/ollama.json \
  --output artifacts/runtime-conformance-ollama.json
```

Live check (requires an already-running server and an available model):

```bash
python3 scripts/check_runtime_conformance.py \
  --manifest examples/runtimes/ollama.json \
  --runtime-version "$(ollama version 2>/dev/null || true)" \
  --output artifacts/runtime-conformance-ollama-live.json
```

The runner probes health/models, one non-stream chat request, and one stream
chat request. It records version, model, quantization, launch command/flags,
and sanitized hardware metadata. The output has a fixed `schema_version` and
contains every capability below. A field is never silently dropped:
`supported`, `unsupported`, and `unknown` are all meaningful results.

`--manifest` may be repeated to create a
`runtime_conformance_bundle` containing a matrix. A fixture bundle is useful
for CI protocol contracts but remains `unverified`; only a live artifact is
marked `real_runtime: true`.

## Capability matrix

The table is the required comparison surface. It is a checklist, not a claim
that every server supports every feature. The generated artifact is the
authoritative result for a particular runtime version, model, quantization,
and machine.

| Capability | Ollama native `/api/chat` | Generic OpenAI `/v1/chat/completions` | llama.cpp server | vLLM | SGLang |
| --- | --- | --- | --- | --- | --- |
| Non-stream | probe | probe | probe | probe | probe |
| Stream | probe (NDJSON) | probe (SSE) | probe (SSE) | probe (SSE) | probe (SSE) |
| Usage | response field | `usage` field | `usage` field | `usage` field | `usage` field |
| Seed pass-through | explicit result required | explicit result required | explicit result required | explicit result required | explicit result required |
| Tool schema/call shape | explicit result required | explicit result required | explicit result required | explicit result required | explicit result required |
| Structured output | explicit result required | explicit result required | explicit result required | explicit result required | explicit result required |
| Max concurrency / queue behavior | measured observation | measured observation | measured observation | measured observation | measured observation |
| Warm/cold behavior | measured observation | measured observation | measured observation | measured observation | measured observation |
| Prefix-cache visibility | measured observation | measured observation | measured observation | measured observation | measured observation |
| Health/models | `/api/tags` | `/v1/models` | `/v1/models` | `/v1/models` | `/v1/models` |
| Timeout/error shape | explicit result required | explicit result required | explicit result required | explicit result required | explicit result required |

The runner intentionally does not infer semantic support from a successful
HTTP status. For example, accepting a `seed` key is not proof that the server
honors deterministic sampling; leave it `unknown` until a controlled fixture or
runtime observation demonstrates the behavior. Likewise, concurrency and
prefix-cache visibility need a performance artifact from the benchmark work
package (#126), not a conformance artifact.

## Launch and configuration recipes

The recipes are intentionally unverified placeholders:

- **Ollama / Apple Silicon personal use:** start `ollama serve`, pull a local
  model separately, and edit `models[0].model`. This is the lowest operational
  overhead path.
- **GPU server:** start llama.cpp, vLLM, or SGLang with the server-specific
  device, batching, cache, and scheduling flags. Edit the model identifier and
  keep the Thug-Fugu `backend` as `openai-compatible`.
- **Generic OpenAI-compatible:** point the same config at an existing server;
  the server remains responsible for authentication and scheduling.

The config exchange is only `models[].backend`, `models[].model`, and
`models[].base_url`; no orchestrator source change is needed. `api_key` should
be `${ENV_VAR}` and is never copied into an artifact.

## Verification gates and sanitized artifacts

The issue acceptance gate requires a live Ollama artifact and at least one live
OpenAI-compatible artifact. This checkout has no permission to install models
or claim hardware measurements, so CI fixtures and unverified recipes must not
be reported as that gate being met. A real run should supply:

- exact runtime/server version and launch flags;
- model identifier and quantization;
- CPU/GPU/RAM/VRAM and power-measurement setup when relevant;
- the generated JSON artifact, after checking that secrets, hostnames, local
  paths, prompts, and completions are absent.

Apple Silicon personal-use results and GPU-server results are separate evidence
classes. Do not use one as a proxy for the other or declare a performance winner
from this compatibility check.
