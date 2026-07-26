# HTTP Server-Side Tool Execution Spec

## 1. Purpose

Add allow-listed local tool execution to the OpenAI-compatible HTTP endpoint
`POST /v1/chat/completions`.

Status:

- Slice 1 (explicit HTTP `tool_calls` input) is implemented.
- Slice 2 (backend-generated assistant tool proposals) and Slice 3 (backend
  `tools` / `tool_choice` pass-through) are implemented for `synthesizer_only`
  mode with a single tool round.

The HTTP path validates and passes `tools` to the configured synthesizer, and
can execute one generated allow-listed tool round. The `consult()` / MCP path
also supports allow-listed local execution. This spec defines the safety
boundary that prevents the HTTP server from becoming an arbitrary tool runner.

## 2. Current state

- `tool_calling.enabled=false` by default.
- HTTP accepts tool schemas only when `tool_calling.enabled=true`.
- HTTP accepts validated `none`, `auto`, `required`, and named function choices.
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

## 5. Implemented first slice: explicit tool_calls input

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

## 6. Implemented backend-generated tool slice

### Slice 2: assistant tool proposals

Support model-generated assistant messages containing `tool_calls` when a
backend returns them. This requires:

- preserving `tool_calls` in `ChatResponse`
- preserving finish reason `tool_calls`
- deciding whether HTTP responds with a proposal or executes it

Implemented behavior:

- `execute=false`: return the assistant proposal with OpenAI-compatible
  `message.tool_calls` and `finish_reason: tool_calls`.
- `execute=true`: execute allow-listed calls, inject redacted/truncated results
  as untrusted evidence, then call the synthesizer once more for the final
  answer.
- The loop is limited to one generated tool round. A second proposal fails
  explicitly rather than executing autonomously.

### Slice 3: backend pass-through

`tools` / `tool_choice` are passed to OpenAI-compatible backends. Native Ollama
receives `tools`; `tool_choice` is not sent because the native `/api/chat`
schema does not consistently support it. Backend tool-call arguments are
normalized to OpenAI-compatible JSON strings.

Safety/compatibility limits:

- Only the configured synthesizer receives tool schemas.
- Workers remain tool-free and side-effect-free.
- Requests with backend tools use buffered SSE; tool-call delta generation is
  emitted after the backend response completes.
- `auto` without a synthesizer retains the previous shape-only fallback.
- `required` and named choices require a synthesizer role.

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
