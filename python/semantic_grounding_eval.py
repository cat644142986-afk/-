from __future__ import annotations

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


def load_grounding_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0":
        raise ValueError("semantic grounding manifest schema_version must be 1.0")
    if manifest.get("corpus_kind") != "procedural-contract":
        raise ValueError("the source-controlled corpus must remain an explicit contract corpus")
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
        target_count = int(case.get("target_count") or 0)
        canvas = case.get("canvas")
        if not query or target_count < 1 or target_count > 8:
            raise ValueError(f"{case_id} has an invalid query or target_count")
        if not isinstance(canvas, list) or len(canvas) != 2 or min(map(int, canvas)) < 32:
            raise ValueError(f"{case_id} has an invalid canvas")
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
    missing = REQUIRED_COVERAGE - coverage
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
    exact_counts = 0
    recoverable_cases = 0
    no_match_total = 0
    no_match_correct = 0
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
        exact_count = len(predicted) == len(expected)
        exact_counts += int(exact_count)
        recoverable = status not in {"failed", "unavailable", "missing"}
        recoverable_cases += int(recoverable)
        if not expected:
            no_match_total += 1
            no_match_correct += int(not predicted and status == "no_match")
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
            "mean_iou": round(mean(case_ious), 4) if case_ious else 0.0,
            "elapsed_ms": round(latency, 2),
        })
    recall = true_positive / max(1, true_positive + false_negative)
    precision = true_positive / max(1, true_positive + false_positive)
    exact_count_accuracy = exact_counts / max(1, len(cases))
    no_match_accuracy = no_match_correct / max(1, no_match_total)
    recovery_rate = recoverable_cases / max(1, len(cases))
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
