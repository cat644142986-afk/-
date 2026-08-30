#!/usr/bin/env python3
"""Build reviewer-safe labels and a separate private mapping without paid calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.generation_quality_eval import (  # noqa: E402
    build_blind_review_packet,
    paid_run_gate,
    validate_experiment_plan,
)


DEFAULT_PLAN = ROOT / "tests" / "fixtures" / "generation_quality" / "experiment-template.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a blind-review packet and private identity mapping."
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--seed", required=True, help="Private deterministic randomization seed")
    parser.add_argument("--reviewer-output", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing output files. Omitted by default to preserve evidence.",
    )
    return parser.parse_args()


def write_new_json(path: Path, value: object, *, force: bool) -> None:
    resolved = path.resolve()
    if resolved.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing evidence: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    validate_experiment_plan(plan)
    reviewer_packet, private_mapping = build_blind_review_packet(plan, seed=args.seed)
    if args.reviewer_output.resolve() == args.mapping_output.resolve():
        raise ValueError("reviewer and private mapping outputs must be separate files")
    write_new_json(args.reviewer_output, reviewer_packet, force=args.force)
    write_new_json(args.mapping_output, private_mapping, force=args.force)
    gate = paid_run_gate(plan)
    print(json.dumps({
        "reviewer_output": str(args.reviewer_output.resolve()),
        "mapping_output": str(args.mapping_output.resolve()),
        "paid_run_gate": gate,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
