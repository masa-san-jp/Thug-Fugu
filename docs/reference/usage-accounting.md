# Token Usage Accounting Policy

## Current status

Thug-Fugu aggregates backend-reported token usage when the backend provides it.

Supported sources:

- **OpenAI-compatible backends**: `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`
- **Ollama**: `prompt_eval_count` → prompt tokens, `eval_count` → completion tokens

The orchestrator sums usage across:

1. selected worker roles
2. optional verifier calls only when those calls are represented as worker/synthesis usage in future revisions
3. optional synthesizer call

At present, worker and synthesizer usage are aggregated. Verifier call usage is not included in the top-level aggregate yet.

## OpenAI-compatible response

When usage is known, `/v1/chat/completions` returns aggregated counts:

```json
{
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 18,
    "total_tokens": 60
  }
}
```

When no backend reports usage, Thug-Fugu keeps OpenAI-compatible integer placeholders:

```json
{
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

Treat `0/0/0` as **unknown**, not as a measured zero.

## Aggregation model

```text
total_prompt_tokens = sum(worker.prompt_tokens) + synthesizer.prompt_tokens
total_completion_tokens = sum(worker.completion_tokens) + synthesizer.completion_tokens
total_tokens = total_prompt_tokens + total_completion_tokens
```

If any participating call lacks prompt/completion counts, that field is unknown internally. The OpenAI-compatible HTTP shape still emits integer placeholders for unknown fields.

## Programmatic API

`OrchestrationResult.usage` contains a `TokenUsage` object when at least one backend reports usage:

```python
result = orchestrator.chat(messages)
if result.usage:
    print(result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens)
```

Each successful `WorkerResult` may also carry role-level `usage`.

## Remaining gaps

- Verifier call usage is not yet included in the aggregate.
- Streaming responses do not emit final usage chunks; stream mode is currently buffered SSE.
- Thug-Fugu does not estimate tokens when a backend omits usage. This intentionally avoids adding tokenizer dependencies.
