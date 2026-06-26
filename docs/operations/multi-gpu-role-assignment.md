# Multiple-GPU role/model assignment

This guide covers the current, static way to get true parallel execution on a
single multi-GPU host: run one local LLM server per GPU on a different port, then
point each Thug-Fugu role at the corresponding `models[].base_url`.

This is the GPU-local version of the same horizontal pattern described in
`docs/design/distributed-inference.md`. It requires no scheduler changes because
Thug-Fugu already runs worker roles concurrently and each model entry has an
independent endpoint URL.

## When this helps

If all roles point to one Ollama instance backed by one GPU, `ThreadPoolExecutor`
submits workers concurrently but the GPU often serializes actual inference. With
multiple Ollama instances pinned to different GPUs, independent roles can run on
separate devices and reduce wall-clock latency.

## Topology

```text
Thug-Fugu coordinator
  ├─ planner  -> http://127.0.0.1:11434 -> Ollama process pinned to GPU 0
  ├─ reviewer -> http://127.0.0.1:11435 -> Ollama process pinned to GPU 1
  └─ synth    -> http://127.0.0.1:11434 -> usually after workers finish
```

## Start one Ollama instance per GPU

Run these in separate terminals on a Linux/NVIDIA host. Do not stop an existing
system service blindly; if Ollama is already managed by your OS, either disable it
intentionally or choose unused ports for the manual instances.

Terminal 1:

```bash
CUDA_VISIBLE_DEVICES=0 \
OLLAMA_HOST=127.0.0.1:11434 \
ollama serve
```

Terminal 2:

```bash
CUDA_VISIBLE_DEVICES=1 \
OLLAMA_HOST=127.0.0.1:11435 \
ollama serve
```

Then pull/load the same model through each endpoint if needed:

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama pull gpt-oss:20b
OLLAMA_HOST=127.0.0.1:11435 ollama pull gpt-oss:20b
```

Notes:

- On non-NVIDIA runtimes, use the runtime-specific device pinning mechanism
  instead of `CUDA_VISIBLE_DEVICES`.
- On macOS/Metal, per-process GPU pinning is generally not equivalent to CUDA
  device pinning; use this guide mainly for Linux/NVIDIA multi-GPU hosts.
- Keep endpoints loopback-only unless you deliberately add private-network or
  reverse-proxy controls.

## Configure role-to-GPU assignment

Use `examples/fugu-local.multi-gpu.json` as a template. The important part is
that each logical model points to a different port:

```json
{
  "models": [
    {
      "name": "gpu0-planner",
      "backend": "ollama",
      "model": "gpt-oss:20b",
      "base_url": "http://127.0.0.1:11434"
    },
    {
      "name": "gpu1-reviewer",
      "backend": "ollama",
      "model": "gpt-oss:20b",
      "base_url": "http://127.0.0.1:11435"
    }
  ],
  "roles": [
    {"name": "planner", "model": "gpu0-planner", "always_include": true},
    {"name": "reviewer", "model": "gpu1-reviewer", "always_include": true}
  ],
  "orchestrator": {"selection_policy": "all", "max_parallel_workers": 2}
}
```

Run:

```bash
PYTHONPATH=src python3 -m fugu_local run \
  --config examples/fugu-local.multi-gpu.json \
  "設計案を作り、別視点でレビューして"
```

## Measure the speedup

Use the benchmark helper to compare a single-endpoint baseline against the
multi-GPU config:

```bash
PYTHONPATH=src python3 scripts/benchmark_parallel_roles.py \
  --config examples/fugu-local.gpt-oss.json \
  --config examples/fugu-local.multi-gpu.json \
  --prompt "設計案を作り、別視点でレビューして" \
  --runs 3 \
  --csv /tmp/thug-fugu-multi-gpu.csv
```

The script prints per-run wall time and worker latencies, and writes CSV rows you
can paste into the issue. The acceptance check for issue #1 should compare:

1. A baseline where both worker roles hit the same Ollama endpoint/GPU.
2. A distributed config where worker roles hit different ports/GPUs.

## Expected evidence for issue #1

Attach the following to the issue or PR comment:

- GPU topology and model names.
- The two config files used.
- Benchmark CSV output.
- Observed wall-clock improvement for the multi-GPU config.
- Any GPU utilization evidence from `nvidia-smi`, `nvtop`, or equivalent.

## Future work

This is static assignment. Dynamic model pools, least-busy routing, health checks,
and failover are tracked separately in issue #9.
