# Endpoint runtime profile

`EndpointRuntimeProfile` is the backend-neutral static contract for the
parallel execution work packages. It is defined in
`src/fugu_local/runtime_profile.py` and normalized by `config.py`.

## Configuration forms

Existing configurations remain valid without edits:

```json
{
  "models": [
    {
      "name": "local",
      "backend": "ollama",
      "model": "llama3",
      "base_url": "http://127.0.0.1:11434"
    }
  ],
  "roles": [{"name": "worker", "model": "local"}]
}
```

The default profile for that model has `max_inflight: 1`, `weight: 1.0`, all
capabilities set to `unknown`, and `value_source: "default"`.

Direct models may opt into the object form with `endpoint`:

```json
{
  "name": "local",
  "backend": "ollama",
  "model": "llama3",
  "endpoint": {
    "url": "http://127.0.0.1:11434",
    "max_inflight": 2,
    "weight": 1.0,
    "runtime": "ollama",
    "capabilities": {
      "streaming": "supported",
      "tool_calling": "unknown",
      "usage_accounting": "supported",
      "structured_output": "unknown",
      "prefix_cache": "unknown"
    },
    "value_source": "configured"
  }
}
```

Pool endpoints accept both forms in the same list:

```json
{
  "name": "fast-pool",
  "backend": "ollama",
  "model": "llama3",
  "endpoints": [
    "http://127.0.0.1:11434",
    {
      "url": "http://[::1]:11435/",
      "max_inflight": 2,
      "weight": 1.5,
      "capabilities": {"streaming": "supported"}
    }
  ]
}
```

`ModelPoolConfig.endpoints` remains a list of raw URL strings for existing
routing, health, and server-plan code. The normalized profiles are available
through `ModelPoolConfig.runtime_profiles()`. Direct models expose the same
method, returning zero or one profile, and `FuguLocalConfig.runtime_profiles()`
returns the aggregate without requiring callers to inspect config dictionaries.
This is the interface for #88; admission control is not part of this change.

## Profile contract

Every profile contains:

- `identity`: canonical scheme/host/port/path with userinfo, query, and
  fragment removed;
- `backend` and `runtime`: configured implementation and runtime kinds;
- `model`: the model label associated with the endpoint;
- `max_inflight`: positive integer, default `1`;
- `weight`: finite positive number, default `1.0`;
- `capabilities`: `streaming`, `tool_calling`, `usage_accounting`,
  `structured_output`, and `prefix_cache`, each `supported`, `unsupported`, or
  `unknown`;
- `value_source`: `configured`, `probed`, or `default`.

`unknown` is conservative: `RuntimeCapabilities.supports(name)` returns true
only for `supported`. This static WP does not turn an unknown capability into
an optimistic assumption, and it does not perform live probes. A future probe
may create a profile with `value_source: "probed"` while retaining the same
shape.

Canonical identity makes these equivalent:

```text
http://LOCALHOST:80/
http://localhost
```

IPv6 brackets are retained in the canonical output. Credentials, API keys in
query strings, and URL fragments never appear in `EndpointRuntimeProfile` or
its `to_dict()` output. The raw URL remains available to the existing backend
configuration path only when required for actual connection behavior; it is
not copied into the profile, health snapshot, or logging contract.

For the performance SSOT, the physical slot count for a profile set is the
sum of `max_inflight` across independent endpoints. `max_parallel_workers` is
only an orchestration request limit and must not be used as a substitute for
this count.

Scheme-relative URLs also have userinfo, query, and fragment removed from
their profile identity. Legacy labels remain accepted, but labels containing
`@` without a parseable host are rejected with an error that omits the input.

## Runtime naming and limits

`ollama`, `llama.cpp`, `vLLM`, and other runtime values are configuration
labels, not claims about measured capability. Whether a runtime supports
streaming, tool calling, usage accounting, structured output, or prefix
caching depends on its version, model, server settings, and deployment.
Measurements and live health probing belong to later work packages; this
profile only makes their eventual values representable and safely consumable.
