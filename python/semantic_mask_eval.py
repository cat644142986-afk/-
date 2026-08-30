from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops, ImageOps, ImageStat

try:
    from semantic_cutout import apply_confirmed_regions, apply_mask_edits
except ImportError:  # pragma: no cover - package import in tests
    from python.semantic_cutout import apply_confirmed_regions, apply_mask_edits


def _ratio(value: int | float, total: int | float) -> float:
    return round(float(value) / max(1.0, float(total)), 8)


def alpha_mask_metrics(
    segmented: Image.Image,
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    rgba = segmented.convert("RGBA")
    raw_alpha = rgba.getchannel("A")
    selected = apply_confirmed_regions(rgba, regions)
    alpha = selected.getchannel("A")
    histogram = alpha.histogram()
    total_pixels = alpha.width * alpha.height
    nonzero_pixels = total_pixels - histogram[0]
    opaque_pixels = histogram[255]
    soft_pixels = sum(histogram[1:255])
    low_alpha_pixels = sum(histogram[1:5])
    transition_pixels = sum(histogram[5:251])
    near_opaque_pixels = sum(histogram[251:255])
    alpha_level_count = sum(1 for count in histogram if count)

    region_source = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    region_alpha = apply_confirmed_regions(region_source, regions).getchannel("A")
    region_histogram = region_alpha.histogram()
    region_pixels = total_pixels - region_histogram[0]
    outside = ImageChops.multiply(alpha, ImageOps.invert(region_alpha))
    outside_histogram = outside.histogram()
    outside_pixels = total_pixels - outside_histogram[0]
    bbox = alpha.getbbox()
    raw_bbox = raw_alpha.getbbox()
    normalized_bbox = None
    normalized_raw_bbox = None
    if bbox:
        left, top, right, bottom = bbox
        normalized_bbox = [
            round(left / alpha.width, 6),
            round(top / alpha.height, 6),
            round((right - left) / alpha.width, 6),
            round((bottom - top) / alpha.height, 6),
        ]
    if raw_bbox:
        left, top, right, bottom = raw_bbox
        normalized_raw_bbox = [
            round(left / alpha.width, 6),
            round(top / alpha.height, 6),
            round((right - left) / alpha.width, 6),
            round((bottom - top) / alpha.height, 6),
        ]
    return {
        "width": alpha.width,
        "height": alpha.height,
        "total_pixels": total_pixels,
        "region_pixels": region_pixels,
        "nonzero_pixels": nonzero_pixels,
        "opaque_pixels": opaque_pixels,
        "soft_pixels": soft_pixels,
        "low_alpha_pixels": low_alpha_pixels,
        "transition_pixels": transition_pixels,
        "near_opaque_pixels": near_opaque_pixels,
        "alpha_level_count": alpha_level_count,
        "outside_region_nonzero_pixels": outside_pixels,
        "foreground_fraction": _ratio(nonzero_pixels, total_pixels),
        "region_coverage": _ratio(nonzero_pixels, region_pixels),
        "soft_edge_fraction": _ratio(soft_pixels, nonzero_pixels),
        "opaque_fraction": _ratio(opaque_pixels, nonzero_pixels),
        "mean_foreground_alpha": round(
            ImageStat.Stat(alpha).sum[0] / max(1, nonzero_pixels * 255), 8
        ),
        "alpha_bbox": normalized_bbox,
        "raw_alpha_bbox": normalized_raw_bbox,
        "alpha_sum": round(ImageStat.Stat(alpha).sum[0], 3),
    }


def evaluate_mask_gates(
    metrics: dict[str, Any],
    gates: dict[str, Any],
    *,
    elapsed_ms: float | None = None,
) -> dict[str, Any]:
    checks = {
        "nonempty": int(metrics.get("nonzero_pixels") or 0)
        >= int(gates.get("min_nonzero_pixels", 1)),
        "region_coverage_min": float(metrics.get("region_coverage") or 0)
        >= float(gates.get("min_region_coverage", 0)),
        "region_coverage_max": float(metrics.get("region_coverage") or 0)
        <= float(gates.get("max_region_coverage", 1)),
        "soft_pixels": int(metrics.get("soft_pixels") or 0)
        >= int(gates.get("min_soft_pixels", 0)),
        "transition_pixels": int(metrics.get("transition_pixels") or 0)
        >= int(gates.get("min_transition_pixels", 0)),
        "alpha_levels": int(metrics.get("alpha_level_count") or 0)
        >= int(gates.get("min_alpha_level_count", 1)),
        "mean_foreground_alpha": float(metrics.get("mean_foreground_alpha") or 0)
        >= float(gates.get("min_mean_foreground_alpha", 0)),
        "outside_region": int(metrics.get("outside_region_nonzero_pixels") or 0)
        <= int(gates.get("max_outside_region_nonzero_pixels", 0)),
    }
    if elapsed_ms is not None and gates.get("max_elapsed_ms") is not None:
        checks["elapsed"] = float(elapsed_ms) <= float(gates["max_elapsed_ms"])
    failed = [name for name, passed in checks.items() if not passed]
    return {"passed": not failed, "checks": checks, "failed_checks": failed}


def _strong_alpha_point(alpha: Image.Image) -> tuple[float, float]:
    extrema = alpha.getextrema()
    if not extrema or extrema[1] <= 0:
        raise ValueError("alpha mask is empty")
    sample = alpha.copy()
    sample.thumbnail((600, 600), Image.Resampling.LANCZOS)
    sample_width, sample_height = sample.size
    values = sample.tobytes()
    strongest = max(range(len(values)), key=values.__getitem__)
    x = strongest % sample_width
    y = strongest // sample_width
    return (
        round(x / max(1, sample_width - 1), 6),
        round(y / max(1, sample_height - 1), 6),
    )


def audit_mask_correction_recovery(
    segmented: Image.Image,
    regions: list[dict[str, Any]],
    *,
    radius: float = 0.012,
) -> dict[str, Any]:
    selected = apply_confirmed_regions(segmented, regions)
    alpha = selected.getchannel("A")
    point = _strong_alpha_point(alpha)
    baseline_sum = ImageStat.Stat(alpha).sum[0]
    excluded = apply_mask_edits(
        selected,
        [{"mode": "exclude", "points": [point], "radius": radius}],
        regions,
    )
    excluded_sum = ImageStat.Stat(excluded.getchannel("A")).sum[0]
    restored = apply_mask_edits(
        excluded,
        [{"mode": "include", "points": [point], "radius": radius}],
        regions,
    )
    restored_sum = ImageStat.Stat(restored.getchannel("A")).sum[0]
    checks = {
        "exclude_reduces_alpha": excluded_sum < baseline_sum,
        "include_restores_alpha": restored_sum > excluded_sum,
        "restored_nonempty": restored.getchannel("A").getbbox() is not None,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "point": list(point),
        "radius": radius,
        "baseline_alpha_sum": round(baseline_sum, 3),
        "excluded_alpha_sum": round(excluded_sum, 3),
        "restored_alpha_sum": round(restored_sum, 3),
    }
