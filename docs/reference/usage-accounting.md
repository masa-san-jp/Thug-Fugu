# Token Usage Accounting Policy

## Current status

Thug-Fugu currently returns placeholder usage values in the OpenAI-compatible response:

```json
{
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

These values should be treated as **unknown**, not as measured token counts.

## Why usage is currently unknown

Thug-Fugu fans a single request out to multiple worker roles and may then call a synthesizer role. This means a single user request can produce several backend requests:

1. One request per selected worker role
2. Optionally one synthesizer request
3. Optional fallback deterministic merge if synthesis fails

Accurate accounting requires combining usage from all backend calls. Backends differ in whether and how they report token usage.

## Policy

Until backend usage aggregation is implemented:

- `usage` values are compatibility placeholders.
- Do not use them for billing, capacity planning, or performance analysis.
- Prefer wall-clock latency and backend logs for operational monitoring.

## Future implementation guidance

When implementing real usage accounting:

1. Preserve raw backend usage where available.
2. Track worker usage separately from synthesizer usage.
3. Aggregate usage into OpenAI-compatible top-level response values.
4. Expose detailed role-level usage in an optional debug or metadata path.
5. Avoid adding tokenizer dependencies unless explicitly accepted by the project.

## Proposed aggregation model

```text
total_prompt_tokens = sum(worker.prompt_tokens) + synthesizer.prompt_tokens
total_completion_tokens = sum(worker.completion_tokens) + synthesizer.completion_tokens
total_tokens = total_prompt_tokens + total_completion_tokens
```

If a backend does not report usage, mark that role as unknown and avoid presenting the aggregate as exact.

## Open question

For unknown usage, the project still needs to decide whether the OpenAI-compatible API should return:

- `0` placeholders
- `null` values
- omitted `usage`
- role-level metadata outside the OpenAI-compatible shape
