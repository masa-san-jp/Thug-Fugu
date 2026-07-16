# Use Thug-Fugu from Claude Code (consultant pattern)

This integration follows README "pattern 2": your outer agent (Claude Code) keeps
tool execution and control flow, and delegates higher-quality multi-role reasoning
to Thug-Fugu. Thug-Fugu is exposed as an MCP tool named `consult_thug_fugu`.

## Prerequisites

- A local model server (Ollama) running, e.g.:

  ```bash
  OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=2 ollama serve
  ollama pull qwen2.5:0.5b
  ```

- Thug-Fugu installed with the MCP extra:

  ```bash
  pip install 'thug-fugu-local[mcp]'
  ```

  Or from a checkout:

  ```bash
  pip install -e '.[mcp]'
  ```

- A Thug-Fugu config pointing at your local model(s). See
  `examples/fugu-local.consult.json`.

## Register the MCP server with Claude Code

```bash
claude mcp add thug-fugu -- fugu-local-mcp --config /absolute/path/to/examples/fugu-local.consult.json
```

Alternatively, pass the config via environment variable:

```bash
claude mcp add thug-fugu --env FUGU_LOCAL_CONFIG=/absolute/path/to/config.json -- fugu-local-mcp
```

If you run from a source checkout without installing, use the module form:

```bash
claude mcp add thug-fugu \
  --env PYTHONPATH=/absolute/path/to/Thug-Fugu/src \
  --env FUGU_LOCAL_CONFIG=/absolute/path/to/config.json \
  -- python3 -m fugu_local.mcp_server
```

## Use it in Claude Code

Once registered, Claude Code can call the tool. Example prompts:

- "Use the thug-fugu consult tool to draft a design and review it from another angle."
- "Delegate this reasoning to thug-fugu and summarize its answer."

The tool accepts:

- `prompt` (string, required)
- `temperature` (number, optional)
- `max_tokens` (integer, optional)

It returns a JSON object:

```json
{
  "answer": "…",
  "pattern": "role_split",
  "selected_roles": ["thinker", "reviewer"],
  "synthesizer_role": "synthesizer",
  "run_id": "…",
  "latency_ms": 1234.5,
  "usage": {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60},
  "verification": {"passed": null, "warning": null, "attempts": []},
  "workers": [
    {"role": "thinker", "model": "ollama-general", "ok": true, "latency_ms": 800.0}
  ]
}
```

`usage` is aggregated backend-reported token usage (null when no backend reports it).
`verification` reports the verifier retry loop outcome when enabled.

## Async task pattern

If Claude Code launches background/async tasks that already use Ollama directly,
route the reasoning-heavy step through this tool instead. The host agent keeps:

- task scheduling
- tool execution
- retries / control loop

Thug-Fugu adds:

- multi-role decomposition and review
- synthesis into one answer
- optional adaptive pattern selection (`coordinator.enabled=true`)

## Notes and limits

- Tool calling inside Thug-Fugu is currently shape-only; let the host agent execute
  tools. Thug-Fugu is the reasoning consultant, not the tool executor (yet).
- Multi-role consultation is slower than a single call. Use
  `orchestrator.request_timeout_seconds` to bound latency and return partial
  results when a worker is slow.
- Keep the model server loopback-only unless you deliberately add TLS/auth.
