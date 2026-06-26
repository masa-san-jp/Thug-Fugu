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
