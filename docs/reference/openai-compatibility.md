# OpenAI Chat Completions Compatibility

## Status

Thug-Fugu implements a minimal subset of the OpenAI Chat Completions API for local tooling compatibility, including true backend-delta SSE for eligible direct requests, synthesizer-delta SSE after role-split workers complete, and buffered fallback for unsupported orchestration paths.

Endpoint:

```text
POST /v1/chat/completions
GET  /v1/models
```

## Supported request fields

| Field | Status | Notes |
|---|---|---|
| `model` | Supported | Echoed back in the response. The actual backend model is selected by Thug-Fugu config. |
| `messages` | Supported | Must be a list of objects with string `role` and string `content`. |
| `temperature` | Supported | Optional. Passed through to backend requests. |
| `max_tokens` | Supported | Optional. Passed as `max_tokens` for OpenAI-compatible backends and `num_predict` for Ollama. |
| `stream` | Partial | `false`/omitted returns JSON. `true` returns OpenAI-style SSE. Eligible `direct` requests stream backend deltas; eligible `role_split` requests stream synthesizer deltas after workers complete. Other paths use buffered fallback. |
| `stream_options.include_usage` | Partial | When `stream=true` and `include_usage=true`, emits a final usage chunk before `[DONE]`. Usage is backend-reported/aggregated when known, otherwise `0/0/0`. |
| `tools` | Shape-only | Rejected with 400 unless `tool_calling.enabled=true`. When enabled, tool schemas are validated and accepted, but tools are not yet forwarded to backends or executed. See `docs/design/tool-calling-support.md`. |
| `tool_choice` | Partial | `none`/`auto` accepted when tool calling is enabled. `required` and named function choice return 400 (not supported in shape-only mode). |
| `tool_calls` | Extension | Non-OpenAI request extension. When `tool_calling.enabled=true` and `execute=true`, explicit client-provided tool calls are executed against the local allow-list and injected as evidence before synthesis. |

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

`GET /v1/models` returns a minimal model list containing `fugu-local` plus configured `models[].name` and `model_pools[].name`. Chat requests may still use any `model` string; routing is controlled by the Thug-Fugu config.

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

- `stream: true` uses true incremental backend deltas when coordinator selects `direct`, verifier and request deadline are disabled, and every selected router member supports `stream_chat`.
- For eligible `role_split`, workers finish first and the synthesizer output then streams incrementally. Worker usage is aggregated with synthesizer usage.
- `parallel_ensemble`, verifier-enabled requests, request-deadline requests, missing/non-streaming synthesizers, and unsupported/mixed model pools retain buffered SSE. `stream_options.include_usage=true` emits the final available usage chunk in all paths.
- Backend-generated tool calling is not supported yet: tool schemas are validated, and explicit request `tool_calls` can execute locally, but tools are not forwarded to backends for automatic tool-call generation (see `docs/design/tool-calling-support.md`)
- No function calling
- No multimodal message content
- No token estimation when a backend omits usage

## Compatibility principle

Unsupported features should fail explicitly rather than being silently ignored. This keeps local client behavior predictable and avoids false assumptions about production-grade OpenAI API parity.
