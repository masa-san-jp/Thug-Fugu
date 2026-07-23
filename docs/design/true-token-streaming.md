# True Token Streaming Spec

## 1. Purpose

Replace buffered SSE with real incremental streaming for the OpenAI-compatible
HTTP endpoint where possible.

The current implementation runs all workers and synthesis to completion, then
emits a small number of SSE chunks. This is compatible enough for many clients
but does not improve perceived latency.

## 2. Current state

- `stream=true` returns OpenAI-style SSE.
- `direct` pattern streams backend deltas incrementally when every router member
  supports streaming.
- `role_split` runs workers to completion and then streams synthesizer deltas.
- `parallel_ensemble`, verifier-enabled requests, request-deadline requests,
  missing/unsupported synthesizers, and unsupported backends use buffered SSE.
- `stream_options.include_usage=true` can emit a final usage chunk.
- Ollama, OpenAI-compatible, and echo adapters implement `stream_chat`.

Status: Phases 1 through 3 are implemented.

## 3. Goals

- Stream tokens from direct single-backend calls when possible.
- Preserve existing buffered fallback for multi-worker orchestration.
- Keep role fan-out and synthesis correctness intact.
- Avoid breaking existing clients that expect current SSE shape.

## 4. Non-goals

- Do not stream multiple workers interleaved in the first slice.
- Do not stream synthesizer input while workers are still running.
- Do not require tokenizer dependencies.
- Do not implement WebSockets.

## 5. Design challenge

Thug-Fugu is an orchestrator, not a simple single model proxy:

```text
user request
  -> N worker calls
  -> optional verifier loop
  -> optional synthesizer call
  -> final answer
```

True streaming is straightforward only when there is exactly one backend call
whose output is also the final output.

## 6. Proposed phased plan

### Phase 1: stream direct pattern only

When coordinator selects `direct` and exactly one role is called:

- call backend streaming API
- convert backend chunks to OpenAI-compatible SSE
- aggregate usage if backend reports final usage

Fallback to buffered SSE when:

- `role_split`
- `parallel_ensemble`
- verifier enabled
- synthesizer is needed
- backend does not support streaming

Status: implemented. The server primes the first backend chunk before sending
SSE headers so early backend failures remain normal JSON 502 responses. Errors
after headers emit a redacted terminal SSE error and `[DONE]`. Router failover
is allowed only before the first emitted chunk.

### Phase 2: stream synthesizer output

For `role_split`:

1. run workers to completion
2. build synthesis prompt
3. stream synthesizer output token-by-token

This reduces perceived latency for long final answers, while keeping worker
fan-out deterministic.

Status: implemented. Streaming eligibility is checked before workers run.
Worker usage is aggregated with the final synthesizer usage chunk. If the
synthesizer fails before the first chunk, the server emits the deterministic
worker merge through the buffered SSE fallback without rerunning workers.

### Phase 3: progress events

Optionally emit non-content progress events:

```text
event: fugu_progress
data: {"phase":"workers_done","ok":2,"failed":0}
```

This should be opt-in because not all OpenAI clients accept custom SSE events.

Status: implemented. Clients opt in with
`stream_options.include_progress=true`. Eligible `role_split` streams emit:

```text
event: fugu_progress
data: {"phase":"workers_done","ok":2,"failed":0}
```

before the assistant role/content chunks. Direct streams and requests without
the option do not emit custom events.

## 7. Backend support

### Ollama

Ollama `/api/chat` supports streaming. Adapter needs:

- `stream_chat(request) -> iterator[ChatStreamChunk]`
- final chunk usage extraction (`eval_count`, `prompt_eval_count`)

### OpenAI-compatible

OpenAI-compatible `/v1/chat/completions` supports `stream=true` in many servers,
but details vary. Adapter needs:

- SSE parser
- chunk content extraction
- final usage chunk support when server provides it
- graceful fallback when unsupported

### Echo backend

Echo can simulate streaming for tests by yielding content in one or more chunks.

## 8. API design

Add optional protocol method:

```python
class StreamingLLMBackend(Protocol):
    def stream_chat(self, request: ChatRequest) -> Iterable[ChatStreamChunk]: ...
```

Data type:

```python
@dataclass(frozen=True)
class ChatStreamChunk:
    delta: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[TokenUsage] = None
```

Routers delegate `stream_chat` only when every selected route supports it.

## 9. Server behavior

Decision logic:

```text
if stream=true and plan is direct and backend supports stream_chat:
    stream direct backend chunks
else:
    current buffered SSE path
```

This preserves compatibility and keeps implementation risk low.

## 10. Test plan

1. direct pattern streams multiple SSE chunks before completion
2. buffered fallback still works for role_split
3. final usage chunk is emitted with `stream_options.include_usage=true`
4. backend streaming error before headers returns JSON error
5. backend streaming error after headers emits safe terminal SSE error or closes
   predictably
6. OpenAI-compatible SSE parser handles `[DONE]`
7. Ollama final usage maps to `TokenUsage`

## 11. Risks

- Streaming errors after headers cannot be returned as normal JSON.
- Interleaving multiple roles can confuse clients and users.
- Backend streaming APIs differ.
- Synthesis streaming can still have long initial latency because workers must
  finish first.

## 12. Acceptance criteria

- Direct single-call requests can produce token-level SSE chunks with lower
  time-to-first-token.
- Existing buffered behavior remains for complex orchestration.
- CI covers streaming and fallback paths.
