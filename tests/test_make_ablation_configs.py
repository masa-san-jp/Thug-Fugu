import json
import tempfile
import unittest
from pathlib import Path

from scripts import make_ablation_configs

from fugu_local.config import config_from_dict


def _base_config():
    return {
        "models": [
            {"name": "echo-planner", "backend": "echo", "model": "planner-mock"},
            {"name": "echo-solver", "backend": "echo", "model": "solver-mock"},
            {"name": "echo-judge", "backend": "echo", "model": "judge-mock"},
            {"name": "echo-critic", "backend": "echo", "model": "critic-mock"},
            {"name": "echo-synth", "backend": "echo", "model": "synth-mock"},
        ],
        "roles": [
            {"name": "planner", "model": "echo-planner"},
            {"name": "solver", "model": "echo-solver"},
            {"name": "judge", "model": "echo-judge", "is_verifier": True},
            {"name": "critic", "model": "echo-critic"},
            {"name": "synthesizer", "model": "echo-synth", "is_synthesizer": True},
        ],
        "coordinator": {
            "enabled": True,
            "default_pattern": "sequential_dag",
            "dag": {
                "stages": [
                    {"name": "planner", "role": "planner"},
                    {"name": "solver", "role": "solver", "fanout": 2},
                    {"name": "verifier", "role": "judge"},
                    {"name": "critic", "role": "critic"},
                    {"name": "reviser", "role": "solver"},
                    {"name": "claim_judge", "role": "judge"},
                    {"name": "writer", "role": "synthesizer"},
                ]
            },
        },
    }


class GenerateAblationConfigsTests(unittest.TestCase):
    def test_generates_one_config_per_disableable_stage(self):
        generated = make_ablation_configs.generate_ablation_configs(_base_config())

        self.assertEqual(
            set(generated.keys()),
            {"planner", "verifier", "critic", "reviser", "claim_judge"},
        )

    def test_solver_and_writer_are_never_ablated(self):
        generated = make_ablation_configs.generate_ablation_configs(_base_config())

        self.assertNotIn("solver", generated)
        self.assertNotIn("writer", generated)

    def test_each_generated_config_disables_exactly_its_own_stage(self):
        generated = make_ablation_configs.generate_ablation_configs(_base_config())

        for stage_name, config_dict in generated.items():
            stages = config_dict["coordinator"]["dag"]["stages"]
            disabled = {s["name"] for s in stages if s.get("enabled") is False}
            self.assertEqual(disabled, {stage_name})

    def test_all_generated_configs_pass_config_from_dict(self):
        generated = make_ablation_configs.generate_ablation_configs(_base_config())

        for stage_name, config_dict in generated.items():
            config = config_from_dict(config_dict)
            disabled_stage = next(s for s in config.coordinator.dag.stages if s.name == stage_name)
            self.assertFalse(disabled_stage.enabled)

    def test_base_config_unmodified_by_generation(self):
        base = _base_config()
        original = json.dumps(base, sort_keys=True)

        make_ablation_configs.generate_ablation_configs(base)

        self.assertEqual(json.dumps(base, sort_keys=True), original)

    def test_raises_when_no_dag_stages_present(self):
        base = _base_config()
        base["coordinator"]["dag"]["stages"] = []

        with self.assertRaises(ValueError):
            make_ablation_configs.generate_ablation_configs(base)

    def test_raises_when_only_non_disableable_stages_present(self):
        base = _base_config()
        base["coordinator"]["dag"]["stages"] = [
            {"name": "solver", "role": "solver"},
            {"name": "writer", "role": "synthesizer"},
        ]

        with self.assertRaises(ValueError):
            make_ablation_configs.generate_ablation_configs(base)

    def test_raises_when_coordinator_missing_entirely(self):
        base = {"models": _base_config()["models"], "roles": _base_config()["roles"]}

        with self.assertRaises(ValueError):
            make_ablation_configs.generate_ablation_configs(base)


class MainCliTests(unittest.TestCase):
    def test_main_writes_one_file_per_disableable_stage_with_expected_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "05-sequential-dag.json"
            base_path.write_text(json.dumps(_base_config()), encoding="utf-8")
            output_dir = Path(tmp) / "out"

            code = make_ablation_configs.main([str(base_path), "--output-dir", str(output_dir)])

            self.assertEqual(code, 0)
            written = sorted(p.name for p in output_dir.iterdir())

        self.assertEqual(
            written,
            sorted(
                f"06-ablation-no-{stage}.json"
                for stage in ("planner", "verifier", "critic", "reviser", "claim_judge")
            ),
        )


if __name__ == "__main__":
    unittest.main()
