# Active Health Polling and Queue Spec

## 1. Purpose

Add active endpoint health polling and optional request queueing for model pools.

Current model pools support failover, least-busy routing, and passive cooldown
after failures. This is enough for many local setups, but it still discovers
dead endpoints only on user traffic and has no backpressure queue.

Status:

- Passive health state based on cooldown/failure tracking is exposed through
  `/health`.
- Ollama and OpenAI-compatible active probes, health-aware routing, strict
  model presence checks, and bounded HTTP queueing are implemented.

## 2. Current state

- `model_pools[]` groups multiple endpoints under one logical model.
- policies: `round_robin`, `least_busy`
- failed endpoint can be passively deprioritized with `cooldown_seconds`
- optional Ollama `/api/tags` or OpenAI-compatible `/v1/models` background polling
- optional bounded HTTP request queue (`server.queue`)

## 3. Goals

- Detect dead endpoints before assigning user requests.
- Track endpoint health state in-process.
- Provide optional bounded queue for HTTP requests when capacity is temporarily
  saturated.
- Preserve simple default behavior for local single-user setups.

## 4. Non-goals

- Do not implement distributed consensus.
- Do not auto-download or auto-place models.
- Do not replace external process supervisors.
- Do not build a full scheduler for multi-tenant serving.

## 5. Health model

Each `RouterMember` gets a health state:

```text
healthy | degraded | unhealthy | unknown
```

Suggested fields:

```python
last_probe_at: float
last_success_at: float
last_failure_at: float
consecutive_probe_failures: int
in_flight: int
health_state: str
```

Passive request failures and active probe failures both update the same state.

## 6. Config proposal

```json
{
  "model_pools": [
    {
      "name": "fast",
      "backend": "ollama",
      "model": "gpt-oss:20b",
      "endpoints": ["http://127.0.0.1:11434"],
      "policy": "least_busy",
      "cooldown_seconds": 30,
      "health": {
        "enabled": true,
        "interval_seconds": 30,
        "timeout_seconds": 2,
        "failure_threshold": 2,
        "success_threshold": 1
      }
    }
  ],
  "server": {
    "queue": {
      "enabled": true,
      "max_size": 16,
      "timeout_seconds": 30
    }
  }
}
```

Keep defaults disabled to preserve current local behavior.

## 7. Probe strategy

### Ollama

Probe:

```text
GET /api/tags
```

Healthy when:

- endpoint responds 2xx
- optional: configured model appears in model list

### OpenAI-compatible

Probe options:

1. `GET /v1/models` if available
2. fallback to a cheap chat completion only if explicitly enabled

Default should avoid generating tokens.

## 8. Router behavior

Routing order:

1. healthy members
2. unknown members
3. degraded members
4. unhealthy members

Never permanently exclude all members. If every member is unhealthy, still
attempt them in deterministic order so recovery is possible and errors are
fresh.

## 9. Background polling lifecycle

`FuguLocalOrchestrator` can own a `HealthMonitor`:

```python
monitor = HealthMonitor(routers, config)
monitor.start()
...
monitor.stop()
```

For CLI one-shot runs, active health polling is not needed. For HTTP server,
start monitor in `serve()` and stop on shutdown.

Implementation must avoid non-daemon thread leaks in tests.

## 10. Queue design

The existing HTTP server has `max_concurrent_requests`; overflow returns 429.

Add optional queue:

- bounded `queue.Queue(max_size)`
- if concurrency slots exhausted:
  - wait up to `queue.timeout_seconds`
  - if slot opens, process request
  - else return 429
- default disabled

This is HTTP-server level backpressure, not model-level scheduling.

## 11. Observability

Expose in `/health`:

```json
{
  "model_pools": {
    "fast": [
      {
        "endpoint": "http://127.0.0.1:11434",
        "state": "healthy",
        "busy": 1,
        "last_probe_at": 123456.0,
        "consecutive_failures": 0
      }
    ]
  },
  "queue": {
    "enabled": true,
    "size": 0,
    "max_size": 16
  }
}
```

Do not include prompts, completions, or credentials.

## 12. Implementation phases

### Phase 1: health state in router

- Add health fields to `RouterMember`
- Add state ordering to routing
- Add tests for healthy/degraded/unhealthy ordering

Status: implemented for passive cooldown state (`healthy` / `degraded`) and
`/health` observability.

### Phase 2: active probes

- Add `HealthMonitor`
- Implement Ollama `/api/tags` probe
- Add fake probe tests
- Expose health in `/health`

Status: implemented. The HTTP server performs an initial synchronous probe
before accepting requests, then starts a daemon monitor thread. Shutdown stops
and joins the monitor. One-shot CLI runs do not start it.

### Phase 3: optional HTTP queue

- Add server queue config
- Add queue wait path before returning 429
- Add concurrency tests

Status: implemented. `server.queue` adds a bounded wait before returning 429.
Disabled by default. `/health` reports queue size and limits.

### Phase 4: OpenAI-compatible probes

- Add `/v1/models` probe support
- Add config for strict model presence checks

Status: implemented. OpenAI-compatible pools probe their configured base
URL plus `/v1/models` and forward the configured bearer token. `require_model`
supports strict model presence checks for both OpenAI-compatible and Ollama
responses.

## 13. Risks

- Background threads can leak in tests.
- Polling can add load or log noise.
- Health endpoints differ across local servers.
- Queueing can hide overload if defaults are too generous.

## 14. Acceptance criteria

- Dead endpoint is marked unhealthy before user traffic hits it.
- `/health` reports model pool state without sensitive content.
- Queue is disabled by default and bounded when enabled.
- Existing behavior remains unchanged when health/queue config is absent.
