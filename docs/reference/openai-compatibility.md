# OpenAI Chat Completions Compatibility

## Status

Thug-Fugu implements a minimal subset of the OpenAI Chat Completions API for local tooling compatibility, including buffered SSE for `stream: true`.

Endpoint:

```text
POST /v1/chat/completions
```

## Supported request fields

| Field | Status | Notes |
|---|---|---|
| `model` | Supported | Echoed back in the response. The actual backend model is selected by Thug-Fugu config. |
| `messages` | Supported | Must be a list of objects with string `role` and string `content`. |
| `temperature` | Supported | Optional. Passed through to backend requests. |
| `max_tokens` | Supported | Optional. Passed as `max_tokens` for OpenAI-compatible backends and `num_predict` for Ollama. |
| `stream` | Partial | `false`/omitted returns JSON. `true` returns OpenAI-style SSE, but currently streams the already-completed final answer as buffered chunks rather than backend token deltas. |
| `stream_options.include_usage` | Partial | When `stream=true` and `include_usage=true`, emits a final usage chunk before `[DONE]`. Usage is backend-reported/aggregated when known, otherwise `0/0/0`. |
| `tools` | Shape-only | Rejected with 400 unless `tool_calling.enabled=true`. When enabled, tool schemas are validated and accepted, but tools are not yet forwarded to backends or executed. See `docs/design/tool-calling-support.md`. |
| `tool_choice` | Partial | `none`/`auto` accepted when tool calling is enabled. `required` and named function choice return 400 (not supported in shape-only mode). |

## Supported response fields

The server returns:

```json
{
  "id": "chatcmpl-local-...",
  "object": "chat.completion",
  "created": 0,
  "model": "fugu-local",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60}
}
```

`usage` is aggregated from backend-reported counts when available. Backends that do not report usage still produce OpenAI-compatible integer placeholders (`0/0/0`), which should be treated as unknown rather than measured zero.

## Error behavior

Errors use a minimal JSON shape:

```json
{
  "error": {"message": "..."}
}
```

Typical status codes:

| Status | Meaning |
|---|---|
| `400` | Invalid request JSON, invalid message shape, invalid field value, or unsupported request field. |
| `404` | Unknown endpoint. |
| `413` | Request body too large. |
| `429` | Too many concurrent chat completion requests, when concurrency limits are enabled. |
| `502` | All worker roles failed. |
| `500` | Unexpected server error. |

## Non-goals for the minimal API

The current minimal API does not attempt full OpenAI compatibility. In particular:

- `stream: true` is buffered SSE: worker fan-out and synthesizer still run to completion before the server emits SSE chunks. `stream_options.include_usage=true` can include final aggregated usage, but this is still emitted after generation completes.
- Tool calling is shape-only: schemas are validated when enabled, but tools are not forwarded to backends or executed yet (see `docs/design/tool-calling-support.md`)
- No function calling
- No multimodal message content
- No `/v1/models` endpoint
- No token estimation when a backend omits usage

## Compatibility principle

Unsupported features should fail explicitly rather than being silently ignored. This keeps local client behavior predictable and avoids false assumptions about production-grade OpenAI API parity.
