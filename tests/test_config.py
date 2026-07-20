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

    def test_accepts_verifier_role_and_verify_config(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [
                    {"name": "worker", "model": "m"},
                    {"name": "verifier", "model": "m", "is_verifier": True},
                ],
                "coordinator": {"verify": {"enabled": True, "max_retries": 2}},
            }
        )

        self.assertTrue(config.roles[1].is_verifier)
        self.assertTrue(config.coordinator.verify.enabled)
        self.assertEqual(config.coordinator.verify.max_retries, 2)

    def test_accepts_explicit_verify_role_name(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [
                    {"name": "worker", "model": "m"},
                    {"name": "reviewer", "model": "m"},
                ],
                "coordinator": {"verify": {"enabled": True, "max_retries": 1, "role": "reviewer"}},
            }
        )

        self.assertEqual(config.coordinator.verify.role, "reviewer")

    def test_rejects_enabled_verify_without_verifier_role(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [{"name": "planner", "model": "m"}],
                    "coordinator": {"verify": {"enabled": True}},
                }
            )

    def test_rejects_negative_verify_retry_budget(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                    "roles": [
                        {"name": "worker", "model": "m"},
                        {"name": "verifier", "model": "m", "is_verifier": True},
                    ],
                    "coordinator": {"verify": {"enabled": True, "max_retries": -1}},
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


class ServerQueueConfigTests(unittest.TestCase):
    def _base(self, server=None):
        raw = {
            "models": [{"name": "m", "backend": "echo", "model": "mock"}],
            "roles": [{"name": "planner", "model": "m"}],
        }
        if server is not None:
            raw["server"] = server
        return raw

    def test_queue_defaults_disabled(self):
        config = config_from_dict(self._base())

        self.assertFalse(config.server.queue.enabled)
        self.assertEqual(config.server.queue.max_size, 16)
        self.assertEqual(config.server.queue.timeout_seconds, 30.0)

    def test_accepts_bounded_queue_config(self):
        config = config_from_dict(
            self._base(
                {
                    "queue": {
                        "enabled": True,
                        "max_size": 3,
                        "timeout_seconds": 1.5,
                    }
                }
            )
        )

        self.assertTrue(config.server.queue.enabled)
        self.assertEqual(config.server.queue.max_size, 3)
        self.assertEqual(config.server.queue.timeout_seconds, 1.5)

    def test_rejects_non_positive_queue_values(self):
        for field, value in (("max_size", 0), ("timeout_seconds", 0)):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError):
                    config_from_dict(
                        self._base(
                            {
                                "queue": {
                                    "enabled": True,
                                    field: value,
                                }
                            }
                        )
                    )

    def test_rejects_boolean_queue_size(self):
        with self.assertRaises(ConfigError):
            config_from_dict(self._base({"queue": {"enabled": True, "max_size": True}}))


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
        self.assertEqual(config.model_pools[0].cooldown_seconds, 0.0)
        self.assertFalse(config.model_pools[0].health.enabled)

    def test_accepts_pool_cooldown_seconds(self):
        config = config_from_dict(
            self._with_pool(
                {
                    "name": "fast",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "endpoints": ["http://127.0.0.1:11434"],
                    "cooldown_seconds": 30,
                }
            )
        )

        self.assertEqual(config.model_pools[0].cooldown_seconds, 30.0)

    def test_accepts_active_health_config(self):
        config = config_from_dict(
            self._with_pool(
                {
                    "name": "fast",
                    "backend": "ollama",
                    "model": "gpt-oss:20b",
                    "endpoints": ["http://127.0.0.1:11434"],
                    "health": {
                        "enabled": True,
                        "interval_seconds": 10,
                        "timeout_seconds": 1,
                        "failure_threshold": 3,
                        "success_threshold": 2,
                    },
                }
            )
        )

        health = config.model_pools[0].health
        self.assertTrue(health.enabled)
        self.assertEqual(health.interval_seconds, 10.0)
        self.assertEqual(health.timeout_seconds, 1.0)
        self.assertEqual(health.failure_threshold, 3)
        self.assertEqual(health.success_threshold, 2)

    def test_rejects_non_positive_active_health_values(self):
        for field, value in (
            ("interval_seconds", 0),
            ("timeout_seconds", 0),
            ("failure_threshold", 0),
            ("success_threshold", 0),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError):
                    config_from_dict(
                        self._with_pool(
                            {
                                "name": "fast",
                                "backend": "ollama",
                                "model": "gpt-oss:20b",
                                "endpoints": ["http://127.0.0.1:11434"],
                                "health": {"enabled": True, field: value},
                            }
                        )
                    )

    def test_rejects_active_health_for_unsupported_backend(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._with_pool(
                    {
                        "name": "fast",
                        "backend": "openai-compatible",
                        "model": "local-model",
                        "endpoints": ["http://127.0.0.1:1234/v1"],
                        "health": {"enabled": True},
                    }
                )
            )

    def test_rejects_negative_pool_cooldown_seconds(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._with_pool(
                    {
                        "name": "fast",
                        "backend": "ollama",
                        "model": "gpt-oss:20b",
                        "endpoints": ["http://127.0.0.1:11434"],
                        "cooldown_seconds": -1,
                    }
                )
            )

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


class ToolCallingExecutionConfigTests(unittest.TestCase):
    def _base(self, tool_calling):
        return {
            "models": [{"name": "m", "backend": "echo", "model": "mock"}],
            "roles": [{"name": "planner", "model": "m"}],
            "tool_calling": tool_calling,
        }

    def test_execute_requires_allowed_tools(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._base(
                    {
                        "enabled": True,
                        "mode": "synthesizer_only",
                        "execute": True,
                        "allowed_tools": [],
                    }
                )
            )

    def test_execute_accepted_with_allowed_tools(self):
        config = config_from_dict(
            self._base(
                {
                    "enabled": True,
                    "mode": "synthesizer_only",
                    "execute": True,
                    "allowed_tools": ["echo"],
                    "timeout_seconds": 3,
                    "max_output_chars": 1000,
                }
            )
        )
        self.assertTrue(config.tool_calling.execute)
        self.assertEqual(config.tool_calling.timeout_seconds, 3.0)
        self.assertEqual(config.tool_calling.max_output_chars, 1000)

    def test_rejects_non_positive_timeout(self):
        with self.assertRaises(ConfigError):
            config_from_dict(
                self._base(
                    {
                        "enabled": True,
                        "mode": "synthesizer_only",
                        "execute": True,
                        "allowed_tools": ["echo"],
                        "timeout_seconds": 0,
                    }
                )
            )
