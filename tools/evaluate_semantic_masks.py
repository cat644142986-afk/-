#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.semantic_mask_eval import (  # noqa: E402
    alpha_mask_metrics,
    audit_mask_correction_recovery,
    evaluate_mask_gates,
)


DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "semantic_mask_quality" / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0" or not isinstance(payload.get("cases"), list):
        raise ValueError("unsupported semantic mask manifest")
    return payload


def _model_path() -> Path:
    root = Path(os.environ.get("U2NET_HOME") or (Path.home() / ".u2net"))
    path = root / "birefnet-general.onnx"
    if not path.is_file():
        raise FileNotFoundError(
            f"local BiRefNet model is missing: {path}. "
            "The evaluator refuses automatic downloads."
        )
    return path.resolve()


def _providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:
        return []


def _segmenter(post_process_mask: bool):
    from rembg import new_session, remove

    started = time.perf_counter()
    session = new_session("birefnet-general")
    load_ms = round((time.perf_counter() - started) * 1000, 3)

    def segment(image: Image.Image) -> Image.Image:
        return remove(
            image.convert("RGBA"),
            session=session,
            alpha_matting=False,
            post_process_mask=post_process_mask,
        ).convert("RGBA")

    return segment, load_ms


def evaluate(manifest_path: Path, *, post_process_mask: bool) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    model_path = _model_path()
    segment, model_load_ms = _segmenter(post_process_mask)
    results: list[dict[str, Any]] = []
    default_gates = dict(manifest.get("gates") or {})
    for case in manifest["cases"]:
        image_path = (manifest_path.parent / case["image"]["path"]).resolve()
        actual_bytes = image_path.stat().st_size
        actual_sha = _sha256(image_path)
        if actual_bytes != int(case["image"]["bytes"]) or actual_sha != case["image"]["sha256"]:
            raise ValueError(f"fixture identity mismatch: {case['id']}")
        with Image.open(image_path) as opened:
            image = opened.convert("RGBA")
        if list(image.size) != list(case["canvas"]):
            raise ValueError(f"fixture dimensions mismatch: {case['id']}")
        started = time.perf_counter()
        segmented = segment(image)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        metrics = alpha_mask_metrics(segmented, case["regions"])
        gates = {**default_gates, **dict(case.get("gates") or {})}
        gate_result = evaluate_mask_gates(metrics, gates, elapsed_ms=elapsed_ms)
        correction = audit_mask_correction_recovery(
            segmented,
            case["regions"],
            radius=float(manifest.get("correction_radius", 0.012)),
        )
        results.append({
            "id": case["id"],
            "tags": list(case.get("tags") or []),
            "image": {
                "path": str(image_path.relative_to(ROOT)).replace("\\", "/"),
                "bytes": actual_bytes,
                "sha256": actual_sha,
            },
            "elapsed_ms": elapsed_ms,
            "metrics": metrics,
            "gates": gates,
            "gate_result": gate_result,
            "correction_recovery": correction,
            "passed": bool(gate_result["passed"] and correction["passed"]),
        })
    elapsed = [float(case["elapsed_ms"]) for case in results]
    passed = all(case["passed"] for case in results)
    return {
        "schema_version": "1.0",
        "corpus_id": manifest["corpus_id"],
        "claim_scope": manifest["claim_scope"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "onnxruntime_providers": _providers(),
        },
        "model": {
            "id": "rembg/birefnet-general",
            "file": model_path.name,
            "bytes": model_path.stat().st_size,
            "sha256": _sha256(model_path),
            "load_ms": model_load_ms,
            "post_process_mask": post_process_mask,
            "automatic_download_allowed": False,
        },
        "summary": {
            "case_count": len(results),
            "passed_case_count": sum(1 for case in results if case["passed"]),
            "failed_case_count": sum(1 for case in results if not case["passed"]),
            "mean_elapsed_ms": round(statistics.fmean(elapsed), 3) if elapsed else 0.0,
            "p95_elapsed_ms": _percentile(elapsed, 0.95),
            "passed": passed,
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the local BiRefNet semantic mask and correction recovery contract."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--post-process-mask",
        choices=("true", "false"),
        default="true",
        help="Evaluate rembg's binary post-processing on or off as one controlled variable.",
    )
    args = parser.parse_args()
    try:
        report = evaluate(
            args.manifest.resolve(),
            post_process_mask=args.post_process_mask == "true",
        )
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
