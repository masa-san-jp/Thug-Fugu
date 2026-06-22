import unittest

from fugu_local.config import ConfigError, config_from_dict


class ConfigTests(unittest.TestCase):
    def test_valid_minimal_config(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )

        self.assertEqual(config.models[0].name, "m")
        self.assertEqual(config.roles[0].name, "planner")
        self.assertEqual(config.orchestrator.selection_policy, "all")

    def test_rejects_unknown_backend(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "unknown", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                }
            )

    def test_rejects_missing_base_url_for_http_backend(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "ollama", "model": "llama"}],
                    "roles": [{"name": "planner", "model": "m"}],
                }
            )

    def test_rejects_unknown_role_model_reference(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "missing"}],
                }
            )

    def test_rejects_invalid_selection_policy(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                    "orchestrator": {"selection_policy": "smart"},
                }
            )

    def test_rejects_string_boolean_fields(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [
                        {"name": "planner", "model": "m", "always_include": "false"}
                    ],
                }
            )

    def test_rejects_string_numeric_fields(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [
                        {
                            "name": "m",
                            "backend": "echo",
                            "model": "mock",
                            "timeout_seconds": "120",
                        }
                    ],
                    "roles": [{"name": "planner", "model": "m"}],
                }
            )

    def test_rejects_boolean_integer_fields(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                    "orchestrator": {"max_parallel_workers": True},
                }
            )

    def test_accepts_integer_temperature_and_timeout(self):
        config = config_from_dict(
            {
                "models": [
                    {
                        "name": "m",
                        "backend": "echo",
                        "model": "mock",
                        "timeout_seconds": 30,
                    }
                ],
                "roles": [{"name": "planner", "model": "m"}],
                "orchestrator": {"temperature": 1},
            }
        )

        self.assertEqual(config.models[0].timeout_seconds, 30.0)
        self.assertEqual(config.orchestrator.temperature, 1.0)


if __name__ == "__main__":
    unittest.main()
