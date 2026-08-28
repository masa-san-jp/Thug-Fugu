import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_runtime_conformance as conformance


def _manifest(runtime="ollama"):
    backend = "ollama" if runtime == "ollama" else "openai-compatible"
    return {
        "runtime_conformance": {
            "runtime": runtime,
            "model": "fixture-model",
            "quantization": "Q4_K_M",
            "runtime_version": "fixture-runtime-1.0",
            "base_url": "https://user:password@remote.example.test:1234/api?token=secret",
            "launch": {
                "command": ["runtime-server", "--model", "/Users/alice/models/model.gguf"],
                "flags": ["--api-key=top-secret"],
            },
            "hardware": {
                "gpu": "test-gpu",
                "model_path": "/Users/alice/models/model.gguf",
                "api_key": "do-not-write",
            },
        },
        "models": [
            {
                "name": "fixture",
                "backend": backend,
                "model": "fixture-model",
                "base_url": "http://127.0.0.1:1234",
            }
        ],
        "roles": [{"name": "worker", "model": "fixture"}],
    }


def _fixture(runtime="ollama"):
    if runtime == "ollama":
        return {
            "responses": {
                "health_models": {"status": 200, "body": {"models": [{"name": "fixture-model"}]}},
                "non_stream": {
                    "status": 200,
                    "body": {
                        "message": {"role": "assistant", "content": "ok"},
                        "prompt_eval_count": 3,
                        "eval_count": 2,
                    },
                },
                "stream": {
                    "status": 200,
                    "lines": [
                        '{"message":{"role":"assistant","content":"ok"},"done":false}',
                        '{"done":true}',
                    ],
                },
            },
            "capabilities": {
                "seed": {"status": "unsupported", "evidence": "fixture server rejects seed"},
                "tools": "unknown",
            },
        }
    return {
        "runtime_version": "openai-fixture-2.0",
        "responses": {
            "health_models": {"status": 200, "body": {"data": [{"id": "fixture-model"}]}},
            "non_stream": {
                "status": 200,
                "body": {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                },
            },
            "stream": {
                "status": 200,
                "lines": ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"],
            },
        },
        "capabilities": {
            "structured_output": {"status": "supported", "evidence": "fixture JSON response"},
            "max_concurrency": "unknown",
        },
    }


class RuntimeConformanceTests(unittest.TestCase):
    def test_fixture_ollama_populates_every_capability_and_redacts_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            fixture_path = Path(__file__).parent / "fixtures/runtime-conformance/ollama.json"

            artifact = conformance.run_manifest(manifest_path, fixture_path=fixture_path)

        self.assertEqual(artifact["artifact_type"], "runtime_conformance")
        self.assertFalse(artifact["verification"]["real_runtime"])
        self.assertEqual(artifact["verification"]["status"], "unverified")
        self.assertEqual(set(artifact["capabilities"]), set(conformance.CAPABILITIES))
        self.assertEqual(artifact["capabilities"]["non_stream"]["status"], "supported")
        self.assertEqual(artifact["capabilities"]["stream"]["status"], "supported")
        self.assertEqual(artifact["capabilities"]["usage"]["status"], "supported")
        self.assertEqual(artifact["capabilities"]["seed"]["status"], "unknown")
        self.assertEqual(artifact["endpoint"], "https://<host>:1234/api")
        self.assertEqual(artifact["launch"]["command"][2], "<path>")
        self.assertEqual(artifact["hardware"]["model_path"], "<path>")
        self.assertEqual(artifact["hardware"]["api_key"], "<redacted>")
        serialized = json.dumps(artifact)
        self.assertNotIn("password", serialized)
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("/Users/alice", serialized)

    def test_fixture_openai_compatible_protocol_and_explicit_unknowns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            fixture_path = root / "fixture.json"
            manifest_path.write_text(json.dumps(_manifest("vLLM")), encoding="utf-8")
            fixture_path.write_text(json.dumps(_fixture("vLLM")), encoding="utf-8")

            artifact = conformance.run_manifest(manifest_path, fixture_path=fixture_path)

        self.assertEqual(artifact["runtime"]["name"], "vLLM")
        self.assertEqual(artifact["runtime"]["family"], "openai-compatible")
        self.assertEqual(artifact["runtime"]["version"], "openai-fixture-2.0")
        self.assertEqual(artifact["capabilities"]["health_models"]["status"], "supported")
        self.assertEqual(artifact["capabilities"]["structured_output"]["status"], "supported")
        self.assertEqual(artifact["capabilities"]["queue_behavior"]["status"], "unknown")

    def test_fixture_rejects_unknown_capability_instead_of_silently_ignoring_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            fixture_path = root / "fixture.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            fixture = _fixture()
            fixture["capabilities"]["invented_field"] = "supported"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

            with self.assertRaises(conformance.ConformanceError):
                conformance.run_manifest(manifest_path, fixture_path=fixture_path)

    def test_cli_writes_bundle_for_multiple_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_paths = []
            for runtime in ("ollama", "vLLM"):
                path = root / (runtime + ".json")
                path.write_text(json.dumps(_manifest(runtime)), encoding="utf-8")
                manifest_paths.append(str(path))
            output = root / "artifact.json"

            result = conformance.main(
                [
                    "--manifest",
                    manifest_paths[0],
                    "--manifest",
                    manifest_paths[1],
                    "--output",
                    str(output),
                ]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(payload["artifact_type"], "runtime_conformance_bundle")
        self.assertEqual(
            [item["runtime"]["name"] for item in payload["runtimes"]], ["ollama", "vLLM"]
        )

    def test_stream_shape_accepts_ollama_ndjson_and_openai_sse(self):
        self.assertTrue(
            conformance._shape_stream(
                "ollama", ['{"message":{"content":"x"}}', '{"done":true}']
            )
        )
        self.assertTrue(
            conformance._shape_stream(
                "openai-compatible", ['data: {"choices":[]}', "data: [DONE]"]
            )
        )
        self.assertFalse(conformance._shape_stream("openai-compatible", ["not-json"]))

    def test_live_artifact_is_not_verified_when_a_required_probe_is_unknown(self):
        capabilities = conformance._empty_capabilities("test")
        capabilities["health_models"] = {"status": "supported", "evidence": "test"}
        capabilities["non_stream"] = {"status": "supported", "evidence": "test"}
        artifact = conformance._build_artifact(
            runtime="ollama",
            model="fixture-model",
            base_url="http://127.0.0.1:11434",
            metadata={"launch": {}},
            hardware={"source": "test"},
            capabilities=capabilities,
            source="live",
            runtime_version="test",
            quantization="unknown",
            notes=[],
        )

        self.assertFalse(artifact["verification"]["real_runtime"])
        self.assertEqual(artifact["verification"]["status"], "unverified")
        self.assertFalse(artifact["verification"]["required_checks_passed"])


if __name__ == "__main__":
    unittest.main()
