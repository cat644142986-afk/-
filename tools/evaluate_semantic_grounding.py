from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
from statistics import mean
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
    TransformersGroundingDinoAdapter,
    ground_semantic_candidates,
    grounding_adapter_from_environment,
)
from python.semantic_query import resolve_semantic_query  # noqa: E402


DEFAULT_MANIFEST = (
    PROJECT_ROOT / "tests" / "fixtures" / "semantic_grounding" / "manifest.json"
)
OWLV2_MODEL_PATH_ENV = "PRODUCT_ATELIER_OWLV2_MODEL_PATH"
FLORENCE2_MODEL_PATH_ENV = "PRODUCT_ATELIER_FLORENCE2_MODEL_PATH"


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _runtime_before() -> tuple[dict, object | None]:
    runtime = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {},
    }
    for package in ("torch", "transformers", "huggingface-hub", "safetensors", "Pillow"):
        try:
            runtime["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            runtime["packages"][package] = "missing"
    try:
        import torch
    except ImportError:
        runtime["cuda"] = {"available": False}
        return runtime, None
    available = bool(torch.cuda.is_available())
    runtime["cuda"] = {
        "available": available,
        "device": torch.cuda.get_device_name(0) if available else None,
        "total_vram_mb": round(
            torch.cuda.get_device_properties(0).total_memory / 1024 / 1024,
            2,
        ) if available else 0,
    }
    if available:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    return runtime, torch


def _run_metadata(
    predictions: dict,
    *,
    query_field: str,
    resolve_query: bool,
    runtime: dict,
    torch_module: object | None,
    confidence_threshold: float,
    low_confidence_threshold: float,
    model_path: str,
) -> dict:
    latencies = [
        max(0.0, float(item.get("elapsed_ms") or 0.0))
        for item in predictions.values()
    ]
    hot = latencies[1:]
    cuda = runtime.get("cuda") or {}
    if torch_module is not None and cuda.get("available"):
        torch_module.cuda.synchronize()
        cuda["peak_allocated_mb"] = round(
            torch_module.cuda.max_memory_allocated() / 1024 / 1024,
            2,
        )
        cuda["peak_reserved_mb"] = round(
            torch_module.cuda.max_memory_reserved() / 1024 / 1024,
            2,
        )
    return {
        "query_field": query_field,
        "offline_query_resolution": resolve_query,
        "confidence_threshold": confidence_threshold,
        "low_confidence_threshold": low_confidence_threshold,
        "model_path": model_path,
        "cold_first_case_ms": round(latencies[0], 2) if latencies else 0.0,
        "hot_mean_ms": round(mean(hot), 2) if hot else 0.0,
        "hot_p95_ms": round(_percentile_95(hot), 2),
        "runtime": runtime,
    }


def _emit(payload: dict, output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _write_json(payload: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _photo_path(
    manifest: dict,
    case: dict,
    image_root: Path | None = None,
) -> Path:
    if manifest["corpus_kind"] == "licensed-photo-downloadable":
        if image_root is None:
            raise ValueError("downloadable photo corpus requires an image root")
        image = manifest["images"][case["image_id"]]
        return image_root / image["path"]
    return Path(manifest["manifest_path"]).parent / case["image"]["path"]


def _local_adapter(
    adapter_name: str,
    model_path: Path | None,
    device: str | None,
) -> tuple[object, str]:
    if adapter_name == "grounding-dino":
        configured = str(
            model_path or os.environ.get("PRODUCT_ATELIER_GROUNDING_MODEL_PATH", "")
        ).strip()
        if configured:
            return TransformersGroundingDinoAdapter(configured, device=device), configured
        return grounding_adapter_from_environment(), ""
    environment_name = (
        OWLV2_MODEL_PATH_ENV
        if adapter_name == "owlv2"
        else FLORENCE2_MODEL_PATH_ENV
    )
    configured = str(model_path or os.environ.get(environment_name, "")).strip()
    if not configured:
        raise ValueError(
            f"{adapter_name} evaluation requires --model-path or {environment_name}"
        )
    if adapter_name == "owlv2":
        from python.semantic_grounding_owlv2 import TransformersOwlv2Adapter

        return TransformersOwlv2Adapter(configured, device=device), configured
    from python.semantic_grounding_florence2 import TransformersFlorence2Adapter

    return TransformersFlorence2Adapter(configured, device=device), configured


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Validate Product Atelier's offline semantic-grounding contract or score predictions.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--image-root",
        type=Path,
        help=(
            "Downloaded image root for licensed-photo-downloadable corpora. "
            "Defaults to build/eval-corpora/<corpus_id>."
        ),
    )
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
        "--adapter",
        choices=("grounding-dino", "owlv2", "florence2"),
        default="grounding-dino",
        help="Local evaluation adapter; OWLv2 remains an evaluation-only candidate.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        help="Explicit local-only model directory for the selected adapter.",
    )
    parser.add_argument(
        "--device",
        help="Optional torch device override such as cpu or cuda.",
    )
    parser.add_argument(
        "--render-dir",
        type=Path,
        help="Optionally render the source-controlled procedural cases as PNG files.",
    )
    parser.add_argument(
        "--query-field",
        choices=("query", "model_query_hint"),
        default="query",
        help="Choose the original Chinese request or the controlled English model hint.",
    )
    parser.add_argument(
        "--resolve-query",
        action="store_true",
        help="Resolve the selected query through the same offline Chinese-to-model-query contract as the app.",
    )
    parser.add_argument("--output", type=Path, help="Optionally write the JSON report.")
    parser.add_argument(
        "--predictions-output",
        type=Path,
        help="Optionally preserve local prediction candidates and confidences for calibration.",
    )
    parser.add_argument("--confidence-threshold", type=float, default=0.75)
    parser.add_argument("--low-confidence-threshold", type=float, default=0.40)
    args = parser.parse_args()
    if args.predictions and args.run_local:
        parser.error("--predictions and --run-local are mutually exclusive")
    if not 0 <= args.low_confidence_threshold <= args.confidence_threshold <= 1:
        parser.error("confidence thresholds must satisfy 0 <= low <= confident <= 1")

    manifest = load_grounding_manifest(args.manifest)
    image_root = args.image_root
    if manifest["corpus_kind"] == "licensed-photo-downloadable":
        image_root = image_root or (
            PROJECT_ROOT / "build" / "eval-corpora" / str(manifest["corpus_id"])
        )
        if args.run_local:
            manifest = load_grounding_manifest(
                args.manifest,
                image_root=image_root,
                require_images=True,
            )
    if args.render_dir:
        if manifest["corpus_kind"] != "procedural-contract":
            parser.error("--render-dir only supports the procedural contract corpus")
        args.render_dir.mkdir(parents=True, exist_ok=True)
        for case in manifest["cases"]:
            render_semantic_fixture(case).save(args.render_dir / f"{case['id']}.png")
    if args.run_local:
        try:
            adapter, configured_model_path = _local_adapter(
                args.adapter,
                args.model_path,
                args.device,
            )
        except ValueError as exc:
            parser.error(str(exc))
        predictions = {}
        runtime, torch_module = _runtime_before()
        with tempfile.TemporaryDirectory(prefix="product-atelier-grounding-") as temp_dir:
            fixture_root = Path(temp_dir)
            for case in manifest["cases"]:
                if manifest["corpus_kind"] == "procedural-contract":
                    image_path = fixture_root / f"{case['id']}.png"
                    render_semantic_fixture(case).save(image_path)
                else:
                    image_path = _photo_path(manifest, case, image_root)
                query = str(case[args.query_field])
                mapping = resolve_semantic_query(query) if args.resolve_query else None
                model_query = str(mapping["model_query"]) if mapping else query
                if mapping and not model_query:
                    predictions[case["id"]] = {
                        "status": "query_unmapped",
                        "available": False,
                        "attempted": False,
                        "candidates": [],
                        "elapsed_ms": 0.0,
                        "query_mapping": mapping,
                    }
                    continue
                predictions[case["id"]] = ground_semantic_candidates(
                    image_path,
                    model_query,
                    case["target_count"],
                    adapter=adapter,
                    confidence_threshold=args.confidence_threshold,
                    low_confidence_threshold=args.low_confidence_threshold,
                )
                if mapping:
                    predictions[case["id"]]["query_mapping"] = mapping
        if args.predictions_output:
            _write_json(predictions, args.predictions_output)
        report = evaluate_grounding_predictions(manifest, predictions)
        report["adapter_id"] = getattr(adapter, "adapter_id", "unknown")
        report["run"] = _run_metadata(
            predictions,
            query_field=args.query_field,
            resolve_query=args.resolve_query,
            runtime=runtime,
            torch_module=torch_module,
            confidence_threshold=args.confidence_threshold,
            low_confidence_threshold=args.low_confidence_threshold,
            model_path=configured_model_path,
        )
        _emit(report, args.output)
        return 0 if report["passed"] else 1
    if not args.predictions:
        _emit({
            "status": "contract_valid",
            "corpus_id": manifest["corpus_id"],
            "corpus_kind": manifest["corpus_kind"],
            "case_count": len(manifest["cases"]),
            "coverage": manifest["coverage"],
            "limitations": manifest["limitations"],
        }, args.output)
        return 0
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = evaluate_grounding_predictions(manifest, predictions)
    _emit(report, args.output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
