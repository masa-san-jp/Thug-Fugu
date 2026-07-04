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
                    "roles": [{"name": "planner", "model": "m", "always_include": "false"}],
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


class CoordinatorConfigTests(unittest.TestCase):
    def test_accepts_disabled_default_coordinator_for_backward_compatibility(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )

        self.assertFalse(config.coordinator.enabled)
        self.assertEqual(config.coordinator.default_pattern, "role_split")

    def test_accepts_enabled_coordinator_rules_and_ensemble(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
                "coordinator": {
                    "enabled": True,
                    "meta_model": "m",
                    "default_pattern": "direct",
                    "rules": [{"match": ["compare", "比較"], "pattern": "parallel_ensemble"}],
                    "ensemble": {"n": 2, "vote": "majority"},
                },
            }
        )

        self.assertTrue(config.coordinator.enabled)
        self.assertEqual(config.coordinator.meta_model, "m")
        self.assertEqual(config.coordinator.rules[0].pattern, "parallel_ensemble")
        self.assertEqual(config.coordinator.ensemble.n, 2)
        self.assertEqual(config.coordinator.ensemble.vote, "majority")

    def test_rejects_unknown_coordinator_pattern(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                    "coordinator": {"default_pattern": "magic"},
                }
            )

    def test_rejects_unknown_meta_model(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                    "coordinator": {"enabled": True, "meta_model": "missing"},
                }
            )


class RequestTimeoutConfigTests(unittest.TestCase):
    def test_request_timeout_defaults_to_none(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )

        self.assertIsNone(config.orchestrator.request_timeout_seconds)

    def test_accepts_positive_request_timeout(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
                "orchestrator": {"request_timeout_seconds": 5},
            }
        )

        self.assertEqual(config.orchestrator.request_timeout_seconds, 5.0)

    def test_rejects_non_positive_request_timeout(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                    "orchestrator": {"request_timeout_seconds": 0},
                }
            )


class ModelPoolConfigTests(unittest.TestCase):
    def _with_pool(self, pool):
        return {
            "models": [{"name": "m", "backend": "echo", "model": "mock"}],
            "model_pools": [pool],
            "roles": [{"name": "worker", "model": "fast"}],
        }

    def test_accepts_pool_and_role_reference(self):
        config = config_from_dict(
            self._with_pool(
                {
                    "name": "fast",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "endpoints": ["http://127.0.0.1:11434", "http://127.0.0.1:11435"],
                    "policy": "least_busy",
                }
            )
        )

        self.assertEqual(config.model_pools[0].name, "fast")
        self.assertEqual(config.model_pools[0].policy, "least_busy")
        self.assertEqual(len(config.model_pools[0].endpoints), 2)

    def test_rejects_pool_without_endpoints(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._with_pool(
                    {
                        "name": "fast",
                        "backend": "ollama",
                        "model": "gpt-oss:20b",
                        "endpoints": [],
                    }
                )
            )

    def test_rejects_pool_unknown_policy(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._with_pool(
                    {
                        "name": "fast",
                        "backend": "ollama",
                        "model": "gpt-oss:20b",
                        "endpoints": ["http://127.0.0.1:11434"],
                        "policy": "magic",
                    }
                )
            )

    def test_rejects_name_collision_between_model_and_pool(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "dup", "backend": "echo", "model": "mock"}],
                    "model_pools": [
                        {
                            "name": "dup",
                            "backend": "ollama",
                            "model": "gpt-oss:20b",
                            "endpoints": ["http://127.0.0.1:11434"],
                        }
                    ],
                    "roles": [{"name": "worker", "model": "dup"}],
                }
            )


class ToolCallingConfigTests(unittest.TestCase):
    def _base(self, tool_calling):
        return {
            "models": [{"name": "m", "backend": "echo", "model": "mock"}],
            "roles": [{"name": "planner", "model": "m"}],
            "tool_calling": tool_calling,
        }

    def test_defaults_disabled(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        self.assertFalse(config.tool_calling.enabled)
        self.assertEqual(config.tool_calling.mode, "disabled")

    def test_enabled_synthesizer_only(self):
        config = config_from_dict(
            self._base({"enabled": True, "mode": "synthesizer_only", "allowed_tools": ["lookup"]})
        )
        self.assertTrue(config.tool_calling.enabled)
        self.assertEqual(config.tool_calling.mode, "synthesizer_only")
        self.assertEqual(config.tool_calling.allowed_tools, ["lookup"])

    def test_rejects_mode_mismatch_when_disabled(self):
        with self.assertRaises(ConfigError):
            config_from_dict(self._base({"enabled": False, "mode": "synthesizer_only"}))

    def test_rejects_disabled_mode_when_enabled(self):
        with self.assertRaises(ConfigError):
            config_from_dict(self._base({"enabled": True, "mode": "disabled"}))

    def test_rejects_execute_true(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._base({"enabled": True, "mode": "synthesizer_only", "execute": True})
            )

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ConfigError):
            config_from_dict(self._base({"enabled": True, "mode": "all_workers"}))

    def test_rejects_whitespace_only_allowed_tool(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._base({"enabled": True, "mode": "synthesizer_only", "allowed_tools": ["  "]})
            )

    def test_rejects_invalid_allowed_tool_name(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._base(
                    {"enabled": True, "mode": "synthesizer_only", "allowed_tools": ["bad name!"]}
                )
            )
