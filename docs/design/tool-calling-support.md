# Tool calling support design

Status: implemented for shape validation, explicit client-provided tool calls,
backend-generated synthesizer proposals, and one-round allow-listed execution.
Multi-round autonomous loops and worker-side tools remain intentionally
unsupported. Tracks issue #8.

## 1. Goal

Add OpenAI-compatible tool-calling shapes to Thug-Fugu without compromising the
multi-role orchestration model or local-only safety posture.

Tool calling is not a simple backend pass-through: Thug-Fugu fans a user request out
to multiple workers and may synthesize their outputs. A tool call requested by one
worker can have side effects, can leak local data, and can conflict with another
worker's answer. Therefore this design separates **tool proposal**, **tool
execution**, and **tool-result synthesis**.

## 2. Non-goals for the first implementation

- No arbitrary shell execution.
- No network-capable built-in tools by default.
- No automatic execution of side-effecting tools without an explicit allow-list.
- No tool calls from every worker in parallel in the default mode.
- No full OpenAI parity for complex multi-turn tool loops in the first slice.

## 3. Request schema

The HTTP API will accept the standard Chat Completions fields:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "tool_name",
        "description": "short description",
        "parameters": {"type": "object", "properties": {}}
      }
    }
  ],
  "tool_choice": "none | auto | required | {\"type\":\"function\",\"function\":{\"name\":\"tool_name\"}}"
}
```

Validation rules:

- `tools` must be a list.
- Only `type: "function"` is accepted.
- `function.name` must match `^[A-Za-z0-9_-]{1,64}$`.
- `function.parameters` must be a JSON object when provided.
- `tool_choice` may be `none`, `auto`, `required`, or a named function choice.
- Unknown modes return HTTP 400.

## 4. Tool routing decision

Initial implementation should support one explicit policy in config:

```json
{
  "tool_calling": {
    "enabled": true,
    "mode": "synthesizer_only",
    "execute": false,
    "allowed_tools": ["lookup_note", "read_project_file"]
  }
}
```

Modes:

| Mode | Behavior | First implementation |
|---|---|---|
| `disabled` | Reject `tools` / `tool_choice` with HTTP 400 | Already current behavior |
| `synthesizer_only` | Workers answer normally. Synthesizer may propose/perform tools after seeing worker outputs. | Recommended first implementation |
| `planner_only` | A planner proposes tool calls before workers run. Tool results are injected into worker context. | Later |
| `all_workers` | All workers receive tools and may call them independently. | Not recommended initially |

Decision: **use `synthesizer_only` for the first implementation**.

Rationale:

- It avoids N workers independently calling the same tool.
- It keeps worker fan-out pure and side-effect-free.
- It centralizes tool risk at the final aggregation point.
- It is easiest to keep compatible with existing role/pool/failover behavior.

## 5. Tool execution policy

First implementation has two phases:

### Phase A: shape-only / proposal mode

- Accept `tools` and `tool_choice` when `tool_calling.enabled=true`.
- Do not execute tools server-side.
- The model may return `tool_calls` in an assistant message.
- HTTP response preserves OpenAI-compatible `tool_calls` shape.
- If tool execution is requested but not enabled, return a clear assistant message or HTTP 400 depending on `tool_choice`.

This phase is useful for clients that execute tools themselves.

### Phase B: local allow-listed execution

> Status: implemented for `consult(config, prompt, tool_calls=[...])`, the MCP
> `consult_thug_fugu` tool, explicit HTTP `tool_calls`, and one backend-generated
> synthesizer tool round. OpenAI-compatible backends receive `tools` and
> `tool_choice`; native Ollama receives `tools`.

- Execute only registered local tools whose names appear in `allowed_tools`.
- Tool registry is process-local Python code, not user-supplied shell.
- Tool arguments are JSON-decoded to an object; each registered tool validates its own argument shape in this first slice.
- Tool output is captured as structured `ToolResult` evidence and injected into a follow-up synthesis message. A richer `tool` role message type remains future work.
- The synthesizer/orchestrator is called again with tool results and produces the final answer.
- Side-effecting tools require explicit config opt-in, e.g. `side_effects: true`.

## 6. Response schema

### Non-streaming tool proposal

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_local_...",
            "type": "function",
            "function": {
              "name": "lookup_note",
              "arguments": "{\"query\":\"...\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

### Tool result representation inside orchestration

A richer future representation could use native tool messages:

```json
{"role": "tool", "tool_call_id": "call_local_...", "content": "..."}
```

Current consult/MCP/HTTP execution injects structured, clearly framed tool
evidence as a follow-up user message for re-synthesis. Native `tool` role
messages remain a possible compatibility improvement.

## 7. Streaming behavior

Tool-calling requests use buffered SSE:

- If the final result is a tool proposal, emit buffered SSE chunks containing the
  `tool_calls` delta and finish with `finish_reason: "tool_calls"`.
- Do not interleave live tool execution events in the first implementation.
- Direct/synthesizer token streaming remains disabled for requests carrying
  backend tool schemas so tool proposals and execution remain deterministic.

## 8. Safety boundaries

- Default `tool_calling.enabled=false`.
- Reject tools explicitly when disabled; do not silently ignore them.
- Built-in server must not expose arbitrary file/network/shell tools by default.
- Tool names and JSON arguments are logged only as non-sensitive summaries unless
  debug logging is explicitly enabled.
- Tool outputs are treated as untrusted content during synthesis.
- Server-side tool execution must have a timeout and maximum output size.

## 9. Implementation split

### PR 1: schema and shape-only support

- Add config: `tool_calling.enabled`, `mode`, `execute`, `allowed_tools`.
- Add request validation for `tools` and `tool_choice`.
- Add typed data structures for tool definitions and tool calls.
- Support `synthesizer_only` proposal mode without server-side execution.
- Preserve current HTTP 400 when disabled.
- Add docs and tests.

### PR 2: local allow-listed tool execution

- Add tool registry abstraction.
- Execute allowed local tools with timeout/output limits.
- Inject `tool` messages and resynthesize.
- Add tests for success, denied tool, timeout, malformed args, and output limit.

### PR 3: backend-specific tool call pass-through

- OpenAI-compatible backend: pass `tools` / `tool_choice` where supported.
- Ollama: add a strategy for models/APIs that support tools; otherwise use prompt-based proposal.
- Explicitly document unsupported backend modes.

## 10. Acceptance criteria mapping for issue #8

- Tool behavior specified: this document.
- Request/response schema documented: sections 3 and 6.
- Unsupported modes fail explicitly: sections 3, 4, and 8.
- Implementation can be split: section 9.
