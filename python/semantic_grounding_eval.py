from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

from PIL import Image, ImageColor, ImageDraw


REQUIRED_COVERAGE = {
    "food",
    "multiple-similar",
    "packaging",
    "transparent",
    "hair-fine-lines",
    "shadow",
    "occlusion",
    "no-match",
}


def _normalized_bbox(value: Any, *, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{field} must contain normalized x, y, width, height")
    result = [float(item) for item in value]
    x, y, width, height = result
    if (
        not all(math.isfinite(item) for item in result)
        or x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > 1.000001
        or y + height > 1.000001
    ):
        raise ValueError(f"{field} is outside normalized image bounds")
    return result


def _safe_relative_image_path(value: Any, *, field: str) -> Path:
    relative = Path(str(value or ""))
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise ValueError(f"{field} has an unsafe image path")
    return relative


def _verify_locked_image(
    image_path: Path,
    image: Mapping[str, Any],
    *,
    canvas: tuple[int, int],
    field: str,
) -> None:
    if not image_path.is_file() or image_path.stat().st_size != int(image["bytes"]):
        raise ValueError(f"{field} image is missing or has the wrong size")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if digest != str(image["sha256"]):
        raise ValueError(f"{field} image SHA-256 does not match")
    with Image.open(image_path) as opened:
        if opened.size != canvas:
            raise ValueError(f"{field} image dimensions do not match canvas")


def load_grounding_manifest(
    path: str | Path,
    *,
    image_root: str | Path | None = None,
    require_images: bool = False,
) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0":
        raise ValueError("semantic grounding manifest schema_version must be 1.0")
    corpus_kind = manifest.get("corpus_kind")
    if corpus_kind not in {
        "procedural-contract",
        "licensed-photo-baseline",
        "licensed-photo-downloadable",
    }:
        raise ValueError("unsupported semantic grounding corpus kind")
    locked_images = manifest.get("images")
    if corpus_kind == "licensed-photo-downloadable":
        if not isinstance(locked_images, dict) or not locked_images:
            raise ValueError("downloadable photo corpus requires locked images")
        if require_images and image_root is None:
            raise ValueError("downloadable photo corpus requires image_root")
    else:
        locked_images = {}
    resolved_image_root = Path(image_root).resolve() if image_root is not None else None
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("semantic grounding manifest requires cases")
    identifiers: set[str] = set()
    coverage: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index + 1} must be an object")
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in identifiers:
            raise ValueError(f"case {index + 1} has a missing or duplicate id")
        identifiers.add(case_id)
        query = str(case.get("query") or "").strip()
        model_query_hint = str(case.get("model_query_hint") or "").strip()
        target_count = int(case.get("target_count") or 0)
        canvas = case.get("canvas")
        if not query or not model_query_hint or target_count < 1 or target_count > 8:
            raise ValueError(f"{case_id} has an invalid query or target_count")
        if not isinstance(canvas, list) or len(canvas) != 2 or min(map(int, canvas)) < 32:
            raise ValueError(f"{case_id} has an invalid canvas")
        if corpus_kind == "licensed-photo-baseline":
            image = case.get("image")
            if not isinstance(image, dict):
                raise ValueError(f"{case_id} requires licensed image metadata")
            required_source = {
                "path", "bytes", "sha256", "source_page", "source_file",
                "author", "license", "retrieved",
            }
            if required_source - set(image):
                raise ValueError(f"{case_id} has incomplete licensed image metadata")
            relative = _safe_relative_image_path(
                image["path"], field=f"{case_id}.image.path"
            )
            image_path = (manifest_path.parent / relative).resolve()
            _verify_locked_image(
                image_path,
                image,
                canvas=tuple(int(value) for value in canvas),
                field=case_id,
            )
        elif corpus_kind == "licensed-photo-downloadable":
            image_id = str(case.get("image_id") or "")
            image = locked_images.get(image_id) if isinstance(locked_images, dict) else None
            if not image_id or not isinstance(image, dict):
                raise ValueError(f"{case_id} references an unknown locked image")
            required_source = {
                "path", "bytes", "sha256", "canvas", "source_page", "source_file",
                "author", "license", "license_url", "retrieved",
            }
            if required_source - set(image):
                raise ValueError(f"{case_id} has incomplete downloadable image metadata")
            relative = _safe_relative_image_path(
                image["path"], field=f"images.{image_id}.path"
            )
            locked_canvas = tuple(int(value) for value in image.get("canvas") or [])
            if len(locked_canvas) != 2 or locked_canvas != tuple(int(value) for value in canvas):
                raise ValueError(f"{case_id} canvas does not match its locked image")
            if len(str(image["sha256"])) != 64 or int(image["bytes"]) <= 0:
                raise ValueError(f"{case_id} has an invalid downloadable image lock")
            if resolved_image_root is not None:
                _verify_locked_image(
                    (resolved_image_root / relative).resolve(),
                    image,
                    canvas=locked_canvas,
                    field=case_id,
                )
        tags = {str(item) for item in case.get("tags") or []}
        coverage.update(tags)
        expected = case.get("expected")
        if not isinstance(expected, list):
            raise ValueError(f"{case_id} expected annotations must be a list")
        if len(expected) > target_count:
            raise ValueError(f"{case_id} expected count exceeds requested target_count")
        for item_index, item in enumerate(expected):
            if not isinstance(item, dict):
                raise ValueError(f"{case_id} expected item {item_index + 1} must be an object")
            _normalized_bbox(item.get("bbox"), field=f"{case_id}.expected[{item_index}].bbox")
    required_coverage = (
        REQUIRED_COVERAGE
        if corpus_kind == "procedural-contract"
        else {str(item) for item in manifest.get("required_coverage") or []}
    )
    missing = required_coverage - coverage
    if missing:
        raise ValueError(f"semantic grounding coverage is missing: {sorted(missing)}")
    manifest["coverage"] = sorted(coverage)
    manifest["manifest_path"] = str(manifest_path.resolve())
    return manifest


def _color(value: Any) -> tuple[int, ...]:
    return ImageColor.getcolor(str(value or "#000000"), "RGBA")


def render_semantic_fixture(case: Mapping[str, Any]) -> Image.Image:
    width, height = (int(value) for value in case["canvas"])
    image = Image.new("RGBA", (width, height), _color(case.get("background", "#ffffff")))
    draw = ImageDraw.Draw(image, "RGBA")
    for shape in case.get("shapes") or []:
        kind = str(shape.get("kind") or "")
        fill = _color(shape.get("fill", "#000000"))
        outline = _color(shape["outline"]) if shape.get("outline") else None
        line_width = max(1, int(shape.get("width") or 1))
        if kind == "rectangle":
            draw.rectangle(shape["box"], fill=fill, outline=outline, width=line_width)
        elif kind == "ellipse":
            draw.ellipse(shape["box"], fill=fill, outline=outline, width=line_width)
        elif kind == "polygon":
            draw.polygon(shape["points"], fill=fill)
            if outline:
                draw.line([*shape["points"], shape["points"][0]], fill=outline, width=line_width)
        elif kind == "line":
            draw.line(shape["points"], fill=fill, width=line_width, joint="curve")
        elif kind == "text":
            draw.text(shape["at"], str(shape.get("text") or ""), fill=fill)
        else:
            raise ValueError(f"unsupported procedural fixture shape: {kind}")
    return image.convert("RGB")


def box_iou(first: Any, second: Any) -> float:
    ax, ay, aw, ah = _normalized_bbox(first, field="first bbox")
    bx, by, bw, bh = _normalized_bbox(second, field="second bbox")
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ax + aw, bx + bw)
    bottom = min(ay + ah, by + bh)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def _match_boxes(expected: list[list[float]], predicted: list[list[float]], threshold: float):
    pairs = sorted(
        (
            (box_iou(expected_box, predicted_box), expected_index, predicted_index)
            for expected_index, expected_box in enumerate(expected)
            for predicted_index, predicted_box in enumerate(predicted)
        ),
        reverse=True,
    )
    matched_expected: set[int] = set()
    matched_predicted: set[int] = set()
    matched_ious: list[float] = []
    for iou, expected_index, predicted_index in pairs:
        if iou < threshold:
            break
        if expected_index in matched_expected or predicted_index in matched_predicted:
            continue
        matched_expected.add(expected_index)
        matched_predicted.add(predicted_index)
        matched_ious.append(iou)
    return matched_expected, matched_predicted, matched_ious


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def evaluate_grounding_predictions(
    manifest: Mapping[str, Any],
    predictions: Mapping[str, Any],
) -> dict[str, Any]:
    gates = dict(manifest.get("gates") or {})
    iou_threshold = float(gates.get("iou_threshold", 0.5))
    true_positive = false_positive = false_negative = 0
    assisted_true_positive = assisted_false_positive = assisted_false_negative = 0
    exact_counts = 0
    assisted_exact_counts = 0
    recoverable_cases = 0
    no_match_total = 0
    no_match_correct = 0
    assisted_no_match_correct = 0
    review_candidate_total = 0
    matched_ious: list[float] = []
    latencies: list[float] = []
    case_results = []
    cases = list(manifest.get("cases") or [])
    for case in cases:
        case_id = str(case["id"])
        prediction = predictions.get(case_id) if isinstance(predictions, Mapping) else None
        prediction = prediction if isinstance(prediction, Mapping) else {}
        status = str(prediction.get("status") or "missing")
        raw_candidates = prediction.get("candidates")
        raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
        predicted = []
        for index, candidate in enumerate(raw_candidates):
            if not isinstance(candidate, Mapping):
                raise ValueError(f"{case_id} prediction {index + 1} must be an object")
            predicted.append(_normalized_bbox(
                candidate.get("bbox"),
                field=f"{case_id}.predictions[{index}].bbox",
            ))
        raw_review_candidates = prediction.get("review_candidates")
        raw_review_candidates = (
            raw_review_candidates if isinstance(raw_review_candidates, list) else []
        )
        review_predicted = []
        for index, candidate in enumerate(raw_review_candidates):
            if not isinstance(candidate, Mapping):
                raise ValueError(f"{case_id} review prediction {index + 1} must be an object")
            review_predicted.append(_normalized_bbox(
                candidate.get("bbox"),
                field=f"{case_id}.review_predictions[{index}].bbox",
            ))
        review_candidate_total += len(review_predicted)
        expected = [
            _normalized_bbox(item["bbox"], field=f"{case_id}.expected.bbox")
            for item in case.get("expected") or []
        ]
        matched_expected, matched_predicted, case_ious = _match_boxes(
            expected,
            predicted,
            iou_threshold,
        )
        case_tp = len(matched_expected)
        case_fp = len(predicted) - len(matched_predicted)
        case_fn = len(expected) - len(matched_expected)
        true_positive += case_tp
        false_positive += case_fp
        false_negative += case_fn
        matched_ious.extend(case_ious)
        assisted_predicted = predicted + review_predicted
        assisted_expected, assisted_predictions, _assisted_ious = _match_boxes(
            expected,
            assisted_predicted,
            iou_threshold,
        )
        case_assisted_tp = len(assisted_expected)
        case_assisted_fp = len(assisted_predicted) - len(assisted_predictions)
        case_assisted_fn = len(expected) - len(assisted_expected)
        assisted_true_positive += case_assisted_tp
        assisted_false_positive += case_assisted_fp
        assisted_false_negative += case_assisted_fn
        exact_count = len(predicted) == len(expected)
        exact_counts += int(exact_count)
        assisted_exact_count = len(assisted_predicted) == len(expected)
        assisted_exact_counts += int(assisted_exact_count)
        recoverable = status not in {"failed", "unavailable", "missing"}
        recoverable_cases += int(recoverable)
        if not expected:
            no_match_total += 1
            no_match_correct += int(
                not predicted and status in {"no_match", "low_confidence"}
            )
            assisted_no_match_correct += int(not assisted_predicted)
        latency = max(0.0, float(prediction.get("elapsed_ms") or 0.0))
        latencies.append(latency)
        case_results.append({
            "id": case_id,
            "status": status,
            "expected_count": len(expected),
            "predicted_count": len(predicted),
            "true_positive": case_tp,
            "false_positive": case_fp,
            "false_negative": case_fn,
            "exact_count": exact_count,
            "recoverable": recoverable,
            "review_candidate_count": len(review_predicted),
            "assisted_true_positive": case_assisted_tp,
            "assisted_false_positive": case_assisted_fp,
            "assisted_false_negative": case_assisted_fn,
            "assisted_exact_count": assisted_exact_count,
            "mean_iou": round(mean(case_ious), 4) if case_ious else 0.0,
            "elapsed_ms": round(latency, 2),
        })
    recall = true_positive / max(1, true_positive + false_negative)
    precision = true_positive / max(1, true_positive + false_positive)
    exact_count_accuracy = exact_counts / max(1, len(cases))
    no_match_accuracy = no_match_correct / max(1, no_match_total)
    recovery_rate = recoverable_cases / max(1, len(cases))
    assisted_recall = assisted_true_positive / max(
        1, assisted_true_positive + assisted_false_negative
    )
    assisted_precision = assisted_true_positive / max(
        1, assisted_true_positive + assisted_false_positive
    )
    assisted_exact_count_accuracy = assisted_exact_counts / max(1, len(cases))
    assisted_no_match_accuracy = assisted_no_match_correct / max(1, no_match_total)
    metrics = {
        "case_count": len(cases),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "mean_matched_iou": round(mean(matched_ious), 4) if matched_ious else 0.0,
        "exact_count_accuracy": round(exact_count_accuracy, 4),
        "no_match_accuracy": round(no_match_accuracy, 4),
        "recovery_rate": round(recovery_rate, 4),
        "review_candidate_count": review_candidate_total,
        "review_assisted_recall": round(assisted_recall, 4),
        "review_assisted_precision": round(assisted_precision, 4),
        "review_assisted_exact_count_accuracy": round(assisted_exact_count_accuracy, 4),
        "review_assisted_no_match_accuracy": round(assisted_no_match_accuracy, 4),
        "mean_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": round(_percentile_95(latencies), 2),
    }
    checks = {
        "recall": recall >= float(gates.get("min_recall", 0)),
        "precision": precision >= float(gates.get("min_precision", 0)),
        "exact_count_accuracy": exact_count_accuracy >= float(
            gates.get("min_exact_count_accuracy", 0)
        ),
        "no_match_accuracy": no_match_accuracy >= float(
            gates.get("min_no_match_accuracy", 0)
        ),
        "recovery_rate": recovery_rate >= float(gates.get("min_recovery_rate", 0)),
    }
    return {
        "corpus_id": manifest.get("corpus_id"),
        "corpus_kind": manifest.get("corpus_kind"),
        "metrics": metrics,
        "gates": gates,
        "checks": checks,
        "passed": all(checks.values()),
        "case_results": case_results,
        "limitations": list(manifest.get("limitations") or []),
    }
