#!/usr/bin/env python3
"""Generate one ablation config per disableable DAG stage from a base
sequential_dag config (WP-7 ablation harness,
docs/plans/phase2-decision-implementation-plan.md section 8.5).

Each output config is the input config with exactly one
`coordinator.dag.stages[]` entry's `enabled` set to `false`; the resulting
bypass behavior for that stage (and any stage the DAG's own bypass rules
force-skip alongside it -- e.g. disabling `critic` also skips `reviser`,
since reviser's input contract assumes a critique exists) is defined by
`pipeline.py` / `docs/design/sequential-inference-dag.md`, not by this
script. `solver` and `writer` cannot be disabled
(`fugu_local.config.DAG_NON_DISABLEABLE_STAGES`) and are never ablated.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Dict, Optional

from fugu_local.config import DAG_NON_DISABLEABLE_STAGES, ConfigError, config_from_dict

DEFAULT_PREFIX = "06-ablation-no-"


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_config", type=Path, help="path to a base sequential_dag config JSON")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="output filename prefix")
    args = parser.parse_args(argv)

    base_config = json.loads(args.base_config.read_text(encoding="utf-8"))
    generated = generate_ablation_configs(base_config)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for stage_name, config_dict in generated.items():
        output_path = args.output_dir / f"{args.prefix}{stage_name}.json"
        output_path.write_text(
            json.dumps(config_dict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {output_path}")
    return 0


def generate_ablation_configs(base_config: dict) -> Dict[str, dict]:
    """Pure function: a base config dict in, ``{stage_name: config_dict}``
    out -- one entry per disableable DAG stage, each identical to the base
    config except that one stage's `enabled` is `false`. Raises
    ValueError if the base config has no `coordinator.dag.stages`, or if
    any generated config fails `fugu_local.config.config_from_dict`.

    Every generated config is validated before any is returned (and
    therefore before `main()` writes anything to disk), so a bad ablation
    never leaves a partial, inconsistent set of output files on disk."""

    stages = ((base_config.get("coordinator") or {}).get("dag") or {}).get("stages") or []
    if not stages:
        raise ValueError("base config has no coordinator.dag.stages to ablate")

    disableable = [
        stage["name"] for stage in stages if stage.get("name") not in DAG_NON_DISABLEABLE_STAGES
    ]
    if not disableable:
        raise ValueError("base config has no disableable stages (only solver/writer present)")

    generated: Dict[str, dict] = {}
    for stage_name in disableable:
        variant = copy.deepcopy(base_config)
        for stage in variant["coordinator"]["dag"]["stages"]:
            if stage.get("name") == stage_name:
                stage["enabled"] = False
        try:
            config_from_dict(variant)
        except ConfigError as exc:
            raise ValueError(
                f"generated ablation config for stage '{stage_name}' is invalid: {exc}"
            ) from exc
        generated[stage_name] = variant

    return generated


if __name__ == "__main__":
    raise SystemExit(main())
