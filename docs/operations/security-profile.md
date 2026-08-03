# Operational Security Profile

## Scope

Thug-Fugu's built-in HTTP server is intended for local development and LAN-contained local LLM orchestration. It is not a hardened public internet service.

## Defaults

- Bind to `127.0.0.1` by default.
- Do not include built-in TLS termination.
- Do not include built-in user authentication yet.
- Keep request logging quiet by default to avoid leaking prompt content.

## Safe local use

Recommended command:

```bash
PYTHONPATH=src python3 -m fugu_local serve \
  --config examples/fugu-local.ollama.json \
  --host 127.0.0.1 \
  --port 8080
```

This keeps the API reachable only from the local machine.

## LAN / private network use

If binding to a LAN address, `0.0.0.0`, `::`, or a non-loopback hostname, treat the server as unauthenticated by default. These bind targets require an explicit CLI opt-in:

```bash
PYTHONPATH=src python3 -m fugu_local serve \
  --config examples/fugu-local.ollama.json \
  --host 0.0.0.0 \
  --allow-unsafe-bind
```

Only use this for deliberate private-network deployments or behind a reverse proxy with appropriate controls.

Recommended controls:

- Restrict inbound traffic with host firewall rules.
- Prefer private overlay networks over open LAN exposure.
- Do not expose Ollama or other backend LLM servers directly to untrusted clients.
- Keep backend `base_url` values scoped to private addresses.

## External exposure

External internet exposure is not recommended for the built-in server alone.

If external exposure is required, place it behind a reverse proxy that provides:

- TLS termination
- Authentication
- Request size limits
- Rate limiting
- Access logs suitable for the deployment environment

## API keys in config

Use environment-variable expansion instead of committing raw API keys:

```json
{
  "api_key": "${OPENAI_COMPATIBLE_API_KEY}"
}
```

Do not commit `.env` files or machine-local secret files.

## Prompt and output sensitivity

Requests, worker outputs, and synthesizer prompts can contain sensitive content. Do not enable verbose logging in shared environments unless logs are protected and retention is defined.

## Future hardening work

Potential future work:

- Built-in API-key authentication
- Optional CORS policy controls
- Explicit unsafe-bind warning for non-localhost hosts
- Structured but redacted request logs

## Error redaction

Backend HTTP response bodies are redacted from raised backend errors and HTTP responses because local LLM servers can echo prompts, completions, request metadata, or credentials in error bodies. Error messages keep concise diagnostics such as status code and backend host/path, but drop query strings, fragments, and raw response bodies.

## Code execution verification is not implemented

The `sequential_dag` verifier stage (`docs/design/sequential-inference-dag.md`) supports its LLM self-report with in-process mechanical checks (`src/fugu_local/verifiers.py`: `ConstraintVerifier` for regex/length/numeric-range/JSON-parseability, `CitationVerifier` for in-context evidence matching). Neither check executes model-generated code, opens a network socket, or writes to disk.

A `PythonExecVerifier` that runs model-generated code via `subprocess` was considered and rejected (WP-5, `docs/plans/phase2-decision-implementation-plan.md`). Restricting a subprocess with `python -I -S`, a temporary directory, and a trimmed environment does not stop generated code from reading or deleting arbitrary files, making outbound network connections, spawning further processes, or exhausting CPU/memory/process-count limits — and making the interpreter unreachable from the HTTP server does not help, since a local CLI invocation already carries the invoking user's full permissions. WP-2 also scopes the Phase 2 decision-set grader to deterministic checks only (`docs/plans/phase2-decision-implementation-plan.md` §3.3), so code execution verification is not required for the Phase 2 Go/Pivot/No-Go decision.

Code execution verification must not be implemented until **all** of the following hold, and even then only as a separate, explicitly human-gated proposal:

- Execution runs inside an OS-level sandbox (e.g. a container) with network access blocked, a read-only filesystem, a non-privileged user, and CPU/memory/process-count limits all configured.
- Execution goes only through a sandbox runner the user supplies and configures externally; this package must never invoke `subprocess` directly for model-generated code.
- The default tool registry (`src/fugu_local/tools.py`) is not extended to add it.
- It cannot be enabled from the HTTP server request path.
- Whether to adopt it at all is a HUMAN GATE decision (`docs/plans/phase2-decision-implementation-plan.md` §0.6), not something an agent decides unilaterally.
