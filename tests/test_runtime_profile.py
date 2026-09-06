import unittest

from fugu_local.runtime_profile import (
    EndpointRuntimeProfile,
    RuntimeCapabilities,
    canonical_endpoint_identity,
)


class EndpointIdentityTests(unittest.TestCase):
    def test_scheme_relative_identity_redacts_userinfo(self):
        profile = EndpointRuntimeProfile.from_endpoint_config(
            "//user:password@[2001:DB8::1]:11434/v1/?token=secret#fragment",
            backend="ollama",
            model="example",
        )
        self.assertEqual(profile.identity, "//[2001:db8::1]:11434/v1")
        self.assertNotIn("password", str(profile.to_dict()))
        self.assertNotIn("secret", str(profile.to_dict()))

    def test_ambiguous_credential_bearing_labels_are_rejected_safely(self):
        for endpoint in ("user:password@127.0.0.1:11434", "http:///user:password@/"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError) as caught:
                    canonical_endpoint_identity(endpoint)
                self.assertNotIn("password", str(caught.exception))

    def test_legacy_label_remains_supported(self):
        self.assertEqual(canonical_endpoint_identity("endpoint-a/?token=secret"), "endpoint-a")

    def test_redacts_credentials_query_fragment_and_normalizes_ipv6(self):
        identity = canonical_endpoint_identity(
            "HTTPS://user:password@[2001:DB8::1]:443/v1/?token=secret#fragment"
        )
        self.assertEqual(identity, "https://[2001:db8::1]/v1")
        self.assertNotIn("password", identity)
        self.assertNotIn("secret", identity)

    def test_default_ports_and_trailing_slash_are_equivalent(self):
        self.assertEqual(
            canonical_endpoint_identity("http://LOCALHOST:80/"),
            canonical_endpoint_identity("http://localhost"),
        )
        self.assertEqual(
            canonical_endpoint_identity("http://localhost:11434/"),
            canonical_endpoint_identity("http://localhost:11434"),
        )


class RuntimeCapabilitiesTests(unittest.TestCase):
    def test_unknown_is_not_supported(self):
        capabilities = RuntimeCapabilities.from_mapping({"streaming": "unknown"})
        self.assertFalse(capabilities.supports("streaming"))
        self.assertTrue(
            RuntimeCapabilities.from_mapping({"streaming": "supported"}).supports("streaming")
        )

    def test_invalid_capability_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "streaming"):
            RuntimeCapabilities.from_mapping({"streaming": "maybe"})
        with self.assertRaisesRegex(ValueError, "unsupported capability"):
            RuntimeCapabilities.from_mapping({"vision": "supported"})


class EndpointRuntimeProfileTests(unittest.TestCase):
    def test_object_profile_has_capacity_weight_source_and_safe_identity(self):
        profile = EndpointRuntimeProfile.from_endpoint_config(
            {
                "url": "http://user:secret@127.0.0.1:11434/?token=secret",
                "runtime": "ollama",
                "max_inflight": 4,
                "weight": 1.5,
                "capabilities": {
                    "streaming": "supported",
                    "tool_calling": "unknown",
                },
            },
            backend="ollama",
            model="llama3",
        )
        self.assertEqual(profile.max_inflight, 4)
        self.assertEqual(profile.weight, 1.5)
        self.assertEqual(profile.value_source, "configured")
        self.assertEqual(profile.identity, "http://127.0.0.1:11434")
        self.assertTrue(profile.capabilities.supports("streaming"))
        self.assertNotIn("secret", str(profile.to_dict()))

    def test_invalid_capacity_weight_and_source_are_rejected(self):
        for field, value in (("max_inflight", 0), ("max_inflight", True), ("weight", 0)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    EndpointRuntimeProfile.from_endpoint_config(
                        {"url": "http://127.0.0.1:11434", field: value},
                        backend="ollama",
                        model="llama3",
                    )
        with self.assertRaisesRegex(ValueError, "value_source"):
            EndpointRuntimeProfile.from_endpoint_config(
                {"url": "http://127.0.0.1:11434", "value_source": "guessed"},
                backend="ollama",
                model="llama3",
            )


if __name__ == "__main__":
    unittest.main()
