# Token Usage Accounting Policy

## Current status

Thug-Fugu aggregates backend-reported token usage when the backend provides it.

Supported sources:

- **OpenAI-compatible backends**: `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`
- **Ollama**: `prompt_eval_count` → prompt tokens, `eval_count` → completion tokens

The orchestrator sums usage across:

1. selected worker roles
2. optional verifier calls
3. optional synthesizer call

Worker retries and verifier retries are counted as separate backend calls. For example, if workers run, verifier fails, workers retry, verifier passes, and then synthesizer runs, all of those backend-reported usages are included.

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
total_prompt_tokens = sum(worker_attempt.prompt_tokens) + sum(verifier.prompt_tokens) + synthesizer.prompt_tokens
total_completion_tokens = sum(worker_attempt.completion_tokens) + sum(verifier.completion_tokens) + synthesizer.completion_tokens
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

## Streaming

For `stream=true`, set `stream_options.include_usage=true` to receive a final usage chunk before `[DONE]`.

The stream is still buffered SSE: usage is emitted after worker/synthesizer execution finishes, not as live token deltas.

## Remaining gaps

- Thug-Fugu does not estimate tokens when a backend omits usage. This intentionally avoids adding tokenizer dependencies.
