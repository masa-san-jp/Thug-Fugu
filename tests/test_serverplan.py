import shlex
import unittest

from fugu_local.config import config_from_dict
from fugu_local.serverplan import derive_server_plan, render_ollama_commands


def _config(models):
    return config_from_dict(
        {
            "models": models,
            "roles": [{"name": "r", "model": models[0]["name"]}],
        }
    )


class ServerPlanTests(unittest.TestCase):
    def test_single_endpoint_groups_models(self):
        config = _config(
            [
                {
                    "name": "a",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "base_url": "http://127.0.0.1:11434",
                },
                {
                    "name": "b",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "base_url": "http://127.0.0.1:11434/",
                },
            ]
        )

        plan = derive_server_plan(config)

        self.assertEqual(len(plan), 1)
        endpoint = plan[0]
        self.assertEqual(endpoint.base_url, "http://127.0.0.1:11434")
        self.assertEqual(endpoint.host, "127.0.0.1")
        self.assertEqual(endpoint.port, 11434)
        self.assertEqual(endpoint.models, ["gpt-oss:20b"])

    def test_multiple_endpoints_preserve_order_and_dedupe_models(self):
        config = _config(
            [
                {
                    "name": "a",
                    "backend": "ollama",
                    "model": "m1",
                    "base_url": "http://127.0.0.1:11434",
                },
                {
                    "name": "b",
                    "backend": "ollama",
                    "model": "m2",
                    "base_url": "http://127.0.0.1:11435",
                },
                {
                    "name": "c",
                    "backend": "ollama",
                    "model": "m1b",
                    "base_url": "http://127.0.0.1:11434",
                },
            ]
        )

        plan = derive_server_plan(config)

        self.assertEqual([e.port for e in plan], [11434, 11435])
        self.assertEqual(plan[0].models, ["m1", "m1b"])
        self.assertEqual(plan[1].models, ["m2"])

    def test_skips_non_ollama_and_missing_base_url(self):
        config = config_from_dict(
            {
                "models": [
                    {"name": "echo", "backend": "echo", "model": "mock"},
                    {
                        "name": "oai",
                        "backend": "openai-compatible",
                        "model": "x",
                        "base_url": "http://127.0.0.1:1234",
                    },
                    {
                        "name": "olm",
                        "backend": "ollama",
                        "model": "gpt-oss:20b",
                        "base_url": "http://127.0.0.1:11434",
                    },
                ],
                "roles": [{"name": "r", "model": "olm"}],
            }
        )

        plan = derive_server_plan(config)

        self.assertEqual([e.base_url for e in plan], ["http://127.0.0.1:11434"])

    def test_render_ollama_commands_with_num_parallel(self):
        config = _config(
            [
                {
                    "name": "a",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "base_url": "http://127.0.0.1:11434",
                }
            ]
        )
        endpoint = derive_server_plan(config)[0]

        commands = render_ollama_commands(endpoint, num_parallel=4)

        self.assertEqual(
            commands[0],
            "OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=4 ollama serve",
        )
        self.assertEqual(
            commands[1],
            "OLLAMA_HOST=127.0.0.1:11434 ollama pull gpt-oss:20b",
        )

    def test_render_ollama_commands_quotes_shell_metacharacters(self):
        config = _config(
            [
                {
                    "name": "a",
                    "backend": "ollama",
                    "model": "bad; echo pwned",
                    "base_url": "http://127.0.0.1:11434",
                }
            ]
        )
        endpoint = derive_server_plan(config)[0]

        commands = render_ollama_commands(endpoint)

        pull = commands[1]
        # The payload must be a single quoted argument, never a separate shell command.
        self.assertIn(shlex.quote("bad; echo pwned"), pull)
        # shlex tokenization proves it is one argument, not an injected command chain.
        tokens = shlex.split(pull)
        self.assertEqual(
            tokens, ["OLLAMA_HOST=127.0.0.1:11434", "ollama", "pull", "bad; echo pwned"]
        )

    def test_render_ollama_commands_rejects_non_positive_parallel(self):
        config = _config(
            [
                {
                    "name": "a",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "base_url": "http://127.0.0.1:11434",
                }
            ]
        )
        endpoint = derive_server_plan(config)[0]

        with self.assertRaises(ValueError):
            render_ollama_commands(endpoint, num_parallel=0)


if __name__ == "__main__":
    unittest.main()
