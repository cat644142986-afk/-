from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.semantic_grounding_eval import (  # noqa: E402
    evaluate_grounding_predictions,
    load_grounding_manifest,
    render_semantic_fixture,
)
from python.semantic_grounding import (  # noqa: E402
    ground_semantic_candidates,
    grounding_adapter_from_environment,
)


DEFAULT_MANIFEST = (
    PROJECT_ROOT / "tests" / "fixtures" / "semantic_grounding" / "manifest.json"
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Validate Product Atelier's offline semantic-grounding contract or score predictions.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--predictions",
        type=Path,
        help="JSON object keyed by case id; omission validates the contract only.",
    )
    parser.add_argument(
        "--run-local",
        action="store_true",
        help="Run the explicitly configured local-only adapter on all contract cases.",
    )
    parser.add_argument(
        "--render-dir",
        type=Path,
        help="Optionally render the source-controlled procedural cases as PNG files.",
    )
    args = parser.parse_args()
    if args.predictions and args.run_local:
        parser.error("--predictions and --run-local are mutually exclusive")

    manifest = load_grounding_manifest(args.manifest)
    if args.render_dir:
        args.render_dir.mkdir(parents=True, exist_ok=True)
        for case in manifest["cases"]:
            render_semantic_fixture(case).save(args.render_dir / f"{case['id']}.png")
    if args.run_local:
        adapter = grounding_adapter_from_environment()
        predictions = {}
        with tempfile.TemporaryDirectory(prefix="product-atelier-grounding-") as temp_dir:
            fixture_root = Path(temp_dir)
            for case in manifest["cases"]:
                image_path = fixture_root / f"{case['id']}.png"
                render_semantic_fixture(case).save(image_path)
                predictions[case["id"]] = ground_semantic_candidates(
                    image_path,
                    case["query"],
                    case["target_count"],
                    adapter=adapter,
                )
        report = evaluate_grounding_predictions(manifest, predictions)
        report["adapter_id"] = getattr(adapter, "adapter_id", "unknown")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    if not args.predictions:
        print(json.dumps({
            "status": "contract_valid",
            "corpus_id": manifest["corpus_id"],
            "corpus_kind": manifest["corpus_kind"],
            "case_count": len(manifest["cases"]),
            "coverage": manifest["coverage"],
            "limitations": manifest["limitations"],
        }, ensure_ascii=False, indent=2))
        return 0
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = evaluate_grounding_predictions(manifest, predictions)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
