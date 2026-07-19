# HTTP Server-Side Tool Execution Spec

## 1. Purpose

Add allow-listed local tool execution to the OpenAI-compatible HTTP endpoint
`POST /v1/chat/completions`.

The current HTTP path validates `tools` / `tool_choice` in shape-only mode but
does not execute tools. The `consult()` / MCP path already supports allow-listed
local execution. This spec defines how to bring that capability to HTTP without
turning the HTTP server into an arbitrary tool runner.

## 2. Current state

- `tool_calling.enabled=false` by default.
- HTTP accepts tool schemas only when `tool_calling.enabled=true`.
- HTTP rejects `tool_choice=required` and named function choice.
- `consult(config, prompt, tool_calls=[...])` executes allow-listed local tools
  when `tool_calling.enabled=true` and `tool_calling.execute=true`.
- Tool registry is local Python code in `src/fugu_local/tools.py`.

## 3. Goals

- Support HTTP server-side execution of explicit client-provided `tool_calls`.
- Reuse existing allow-list, timeout, output truncation, and tool result
  formatting behavior from `consult()`.
- Preserve safe default behavior: no tools unless explicitly configured.
- Keep the first HTTP implementation deterministic and easy to test.
- Avoid backend-specific tool generation in the first implementation.

## 4. Non-goals

- Do not execute arbitrary shell commands or user-provided code.
- Do not implement backend pass-through tool generation in this phase.
- Do not infer tool calls from assistant text in this phase.
- Do not implement multi-round autonomous tool loops in HTTP.
- Do not expose side-effecting tools without a separate opt-in design.

## 5. Proposed first slice: explicit tool_calls input

### 5.1 Request shape

Add optional non-OpenAI extension field:

```json
{
  "messages": [{"role": "user", "content": "Use this evidence"}],
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "lookup_static",
        "arguments": "{\"key\":\"project\"}"
      }
    }
  ]
}
```

Rationale:

- It reuses existing `parse_tool_calls()` and `execute_tool_calls()`.
- It lets an outer agent supply tool calls explicitly.
- It avoids asking local LLM backends to generate OpenAI tool-call JSON.
- It is immediately useful for agent runtimes that already choose tools
  themselves.

### 5.2 Config requirements

Execution requires all of:

```json
{
  "tool_calling": {
    "enabled": true,
    "mode": "synthesizer_only",
    "execute": true,
    "allowed_tools": ["lookup_static"],
    "timeout_seconds": 5,
    "max_output_chars": 4000
  }
}
```

If `tool_calls` are present and execution is disabled, return HTTP 400.

### 5.3 Response shape

Return normal chat completion response with final synthesized content.

Add a Thug-Fugu extension object:

```json
{
  "thug_fugu": {
    "tool_results": [
      {
        "tool_call_id": "call_1",
        "name": "lookup_static",
        "content": "...",
        "truncated": false,
        "error": ""
      }
    ],
    "verification": {"passed": true, "warning": null, "attempts": []}
  }
}
```

OpenAI-compatible top-level fields remain unchanged.

## 6. Later slices

### Slice 2: assistant tool proposals

Support model-generated assistant messages containing `tool_calls` when a
backend returns them. This requires:

- preserving `tool_calls` in `ChatResponse`
- preserving finish reason `tool_calls`
- deciding whether HTTP responds with a proposal or executes it

This is more complex and should not be mixed into Slice 1.

### Slice 3: backend pass-through

Pass `tools` / `tool_choice` to OpenAI-compatible backends that support tool
calling. Ollama support should be backend-version gated and may need a different
adapter strategy.

## 7. Internal design

### 7.1 Server validation

Add validation for `tool_calls`:

- must be a list
- each item must be an object
- `type` must be `function`
- `function.name` must match the existing tool name regex
- `function.arguments` must be a JSON string or object accepted by
  `parse_tool_calls()`

### 7.2 Execution path

Pseudo flow:

```text
HTTP request
  -> validate normal chat request
  -> messages_from_dicts()
  -> if tool_calls:
       require tool_calling.enabled && execute
       parse_tool_calls()
       execute_tool_calls()
       append tool evidence user message
  -> orchestrator.chat()
  -> response includes content, usage, thug_fugu.tool_results
```

This mirrors `consult()` while keeping server response OpenAI-compatible.

### 7.3 Streaming

For the first slice:

- execute tools before emitting SSE
- keep buffered SSE behavior
- optionally include final usage chunk if requested
- do not stream tool events

## 8. Safety

- No execution unless `tool_calling.execute=true`.
- Only allow-listed registry tools execute.
- Tool outputs are untrusted evidence and must be framed as such.
- Tool errors are captured as tool result errors; they do not become server 500s.
- Tool timeout and output truncation are mandatory.
- Do not log tool arguments or outputs at INFO.

## 9. Test plan

Unit / integration tests:

1. HTTP rejects `tool_calls` when execution disabled.
2. HTTP executes allowed `echo` tool and includes `thug_fugu.tool_results`.
3. HTTP denies disallowed tool with captured tool error or 400 (choose one and
   keep consistent).
4. malformed arguments return 400.
5. tool timeout is captured.
6. oversized tool output is truncated.
7. streaming request with `tool_calls` still returns SSE.
8. response remains JSON-serializable.

## 10. Acceptance criteria

- `POST /v1/chat/completions` can execute an explicit allow-listed local tool
  call and synthesize a final answer.
- Existing shape-only `tools` behavior remains backward compatible.
- No tool executes unless `tool_calling.execute=true`.
- Full test suite and CI pass.

## 11. Implementation estimate

- Slice 1: small/medium (server validation + reuse consult/tool helpers)
- Slice 2: medium/high (requires response schema expansion)
- Slice 3: high (backend-specific behavior)

