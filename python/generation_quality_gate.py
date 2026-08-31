"""Deterministic, local-only checks for generated ecommerce images.

The gate deliberately does not judge aesthetics, text accuracy, product count,
or reference fidelity. Those axes need OCR, detection, comparison evidence, or
human review. A completed report never authorizes a paid retry by itself.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from PIL import Image


QUALITY_GATE_CONTRACT_VERSION = "generation-quality-gate-2026-08-31.1"
_WHITE_MIN_CHANNEL = 245
_WHITE_MAX_SPREAD = 10


def _check(
    check_id: str,
    status: str,
    *,
    measured: Mapping[str, Any],
    threshold: Mapping[str, Any],
    message: str,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "measured": dict(measured),
        "threshold": dict(threshold),
        "message": message,
    }


def _expected_long_edge(output_spec: Mapping[str, Any]) -> int:
    provider_size = str(output_spec.get("provider_size") or "").lower()
    if "x" in provider_size:
        try:
            width, height = (int(part) for part in provider_size.split("x", 1))
            return max(width, height)
        except (TypeError, ValueError):
            pass
    resolution = str(output_spec.get("requested_resolution") or "2k").lower()
    return 4096 if resolution == "4k" else 2048


def _is_white_like(pixel: tuple[int, int, int]) -> bool:
    return (
        min(pixel) >= _WHITE_MIN_CHANNEL
        and max(pixel) - min(pixel) <= _WHITE_MAX_SPREAD
    )


def _border_and_subject_metrics(image: Image.Image) -> dict[str, Any]:
    sample = image.convert("RGB")
    sample.thumbnail((512, 512), Image.Resampling.NEAREST)
    width, height = sample.size
    pixels = sample.load()
    border_width = max(1, round(min(width, height) * 0.02))
    foreground_pixels = 0
    total_pixels = max(1, width * height)
    side_totals = {
        "top": width * border_width,
        "bottom": width * border_width,
        "left": height * border_width,
        "right": height * border_width,
    }
    side_foreground = {key: 0 for key in side_totals}
    border_pixels: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            foreground = not _is_white_like(pixels[x, y])
            if foreground:
                foreground_pixels += 1
            if y < border_width:
                border_pixels.add((x, y))
                if foreground:
                    side_foreground["top"] += 1
            if y >= height - border_width:
                border_pixels.add((x, y))
                if foreground:
                    side_foreground["bottom"] += 1
            if x < border_width:
                border_pixels.add((x, y))
                if foreground:
                    side_foreground["left"] += 1
            if x >= width - border_width:
                border_pixels.add((x, y))
                if foreground:
                    side_foreground["right"] += 1

    nonwhite_border = sum(
        1 for x, y in border_pixels if not _is_white_like(pixels[x, y])
    )
    border_total = max(1, len(border_pixels))
    side_contact = {
        key: round(side_foreground[key] / max(1, side_totals[key]), 6)
        for key in side_totals
    }
    return {
        "sample_width": width,
        "sample_height": height,
        "border_width": border_width,
        "white_border_ratio": round(1 - nonwhite_border / border_total, 6),
        "foreground_area_ratio": round(foreground_pixels / total_pixels, 6),
        "side_contact_ratio": side_contact,
        "max_side_contact_ratio": max(side_contact.values(), default=0.0),
    }


def evaluate_generation_quality(
    image: Image.Image,
    output_spec: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return local evidence without triggering, scheduling, or pricing a retry."""
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL image")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")

    checks: list[dict[str, Any]] = []
    expected_ratio = float(output_spec.get("effective_ratio_value") or width / height)
    actual_ratio = width / height
    ratio_error = abs(math.log(max(actual_ratio, 1e-9) / max(expected_ratio, 1e-9)))
    ratio_error_percent = (math.exp(ratio_error) - 1) * 100
    checks.append(_check(
        "aspect-ratio",
        "pass" if ratio_error_percent <= 4 else "fail",
        measured={
            "width": width,
            "height": height,
            "ratio": round(actual_ratio, 6),
            "error_percent": round(ratio_error_percent, 3),
        },
        threshold={"max_error_percent": 4},
        message=(
            "输出比例符合有效规格"
            if ratio_error_percent <= 4
            else "输出比例偏离有效规格"
        ),
    ))

    expected_long_edge = _expected_long_edge(output_spec)
    minimum_long_edge = round(expected_long_edge * 0.75)
    actual_long_edge = max(width, height)
    checks.append(_check(
        "resolution-floor",
        "pass" if actual_long_edge >= minimum_long_edge else "fail",
        measured={
            "width": width,
            "height": height,
            "long_edge": actual_long_edge,
        },
        threshold={
            "expected_long_edge": expected_long_edge,
            "minimum_long_edge": minimum_long_edge,
        },
        message=(
            "输出像素达到当前档位的最低门槛"
            if actual_long_edge >= minimum_long_edge
            else "输出像素明显低于当前档位"
        ),
    ))

    metrics = _border_and_subject_metrics(image)
    white_ratio = float(metrics["white_border_ratio"])
    white_status = "pass" if white_ratio >= 0.90 else (
        "review" if white_ratio >= 0.72 else "fail"
    )
    checks.append(_check(
        "white-background-border",
        white_status,
        measured={"white_border_ratio": white_ratio},
        threshold={"pass_minimum": 0.90, "review_minimum": 0.72},
        message={
            "pass": "画布边界接近纯白背景",
            "review": "画布边界存在少量偏色或主体接触，需要复核",
            "fail": "画布边界大面积不是纯白",
        }[white_status],
    ))

    max_contact = float(metrics["max_side_contact_ratio"])
    crop_status = "pass" if max_contact < 0.03 else (
        "review" if max_contact < 0.12 else "fail"
    )
    checks.append(_check(
        "subject-border-contact",
        crop_status,
        measured={
            "max_side_contact_ratio": max_contact,
            "side_contact_ratio": metrics["side_contact_ratio"],
        },
        threshold={"review_from": 0.03, "fail_from": 0.12},
        message={
            "pass": "未发现主体明显接触画布边界",
            "review": "主体或背景污染接近画布边界，需要复核",
            "fail": "主体疑似被裁切或背景严重污染",
        }[crop_status],
    ))

    foreground_ratio = float(metrics["foreground_area_ratio"])
    subject_status = "fail" if foreground_ratio < 0.005 else (
        "review" if foreground_ratio < 0.02 else "pass"
    )
    checks.append(_check(
        "subject-presence",
        subject_status,
        measured={"foreground_area_ratio": foreground_ratio},
        threshold={"review_minimum": 0.005, "pass_minimum": 0.02},
        message={
            "pass": "画面中存在可检测的非白主体区域",
            "review": "主体区域过小，需要确认是否漏生成",
            "fail": "画面接近空白，未检测到有效主体",
        }[subject_status],
    ))

    values = dict(context or {})
    locks = values.get("intent_locks")
    locks = locks if isinstance(locks, Mapping) else {}
    unverified_axes = ["reference_fidelity", "product_count", "edge_quality"]
    if str(values.get("category") or "").lower() == "packaging" or locks.get("packaging_text"):
        unverified_axes.append("packaging_text")
    if locks.get("logo"):
        unverified_axes.append("brand_logo")

    blocking_failures = [item["id"] for item in checks if item["status"] == "fail"]
    review_warnings = [item["id"] for item in checks if item["status"] == "review"]
    return {
        "contract_version": QUALITY_GATE_CONTRACT_VERSION,
        "deterministic_status": "fail" if blocking_failures else (
            "review" if review_warnings else "pass"
        ),
        "checks": checks,
        "blocking_failures": blocking_failures,
        "review_warnings": review_warnings,
        "unverified_axes": unverified_axes,
        "coverage": {
            "verified_axes": [item["id"] for item in checks],
            "unverified_axes": unverified_axes,
        },
        "retry": {
            "authorized": False,
            "reason": "local-evidence-only; paid retry requires an approved policy and budget",
        },
    }
