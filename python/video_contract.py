# -*- coding: utf-8 -*-
"""Frozen image-to-video parameters for the first offline video provider."""

from __future__ import annotations

import re
from typing import Any, Mapping


IMAGE_TO_VIDEO_CONTRACT_VERSION = "image-to-video-v1"
OFFLINE_VIDEO_PROVIDER = "offline-preview-v1"
VIDEO_OUTPUT_RATIOS = frozenset({"1:1", "16:9", "9:16", "4:3", "3:4"})
VIDEO_DURATION_SECONDS = frozenset({3, 5, 8, 10})

_ALLOWED_PARAMETER_KEYS = frozenset({
    "contract_version",
    "prompt",
    "output_ratio",
    "duration_seconds",
    "motion_intensity",
    "first_frame_asset_id",
    "last_frame_asset_id",
    "provider",
    "provider_call_confirmed",
    "automatic_paid_retry",
    "output_root",
})


class VideoContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _fail(code: str, message: str) -> None:
    raise VideoContractError(code, message)


def _asset_id(value: Any, label: str, *, optional: bool = False) -> str | None:
    normalized = str(value or "").strip()
    if optional and not normalized:
        return None
    if not re.fullmatch(r"ast[_:][A-Za-z0-9:_-]{1,155}", normalized):
        _fail("VIDEO_FRAME_INVALID", f"{label} is invalid")
    return normalized


def normalize_image_to_video_parameters(
    parameters: Mapping[str, Any] | None,
    *,
    source_asset_id: str,
) -> dict[str, Any]:
    raw = dict(parameters or {})
    unknown = sorted(set(raw) - _ALLOWED_PARAMETER_KEYS)
    if unknown:
        _fail(
            "VIDEO_PARAMETERS_INVALID",
            f"unsupported image-to-video parameters: {', '.join(unknown)}",
        )

    requested_contract = str(
        raw.get("contract_version") or IMAGE_TO_VIDEO_CONTRACT_VERSION
    ).strip()
    if requested_contract != IMAGE_TO_VIDEO_CONTRACT_VERSION:
        _fail(
            "VIDEO_CONTRACT_VERSION_MISMATCH",
            "video contract version is unsupported",
        )

    prompt = re.sub(r"\s+", " ", str(raw.get("prompt") or "")).strip()
    if not 2 <= len(prompt) <= 600:
        _fail("VIDEO_PROMPT_INVALID", "video prompt must contain 2 to 600 characters")

    output_ratio = str(raw.get("output_ratio") or "1:1").strip()
    if output_ratio not in VIDEO_OUTPUT_RATIOS:
        _fail("VIDEO_RATIO_UNSUPPORTED", "video output ratio is unsupported")

    duration_seconds = raw.get("duration_seconds", 5)
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int):
        _fail("VIDEO_DURATION_UNSUPPORTED", "video duration is unsupported")
    if duration_seconds not in VIDEO_DURATION_SECONDS:
        _fail("VIDEO_DURATION_UNSUPPORTED", "video duration is unsupported")

    value = raw.get("motion_intensity", 3)
    if isinstance(value, bool):
        _fail("VIDEO_MOTION_INVALID", "motion intensity must be an integer from 1 to 10")
    try:
        motion_intensity = int(value)
    except (TypeError, ValueError):
        _fail("VIDEO_MOTION_INVALID", "motion intensity must be an integer from 1 to 10")
    if motion_intensity < 1 or motion_intensity > 10 or str(value).strip() != str(motion_intensity):
        _fail("VIDEO_MOTION_INVALID", "motion intensity must be an integer from 1 to 10")

    source_id = _asset_id(source_asset_id, "source_asset_id")
    first_frame_id = _asset_id(
        raw.get("first_frame_asset_id") or source_id,
        "first_frame_asset_id",
    )
    if first_frame_id != source_id:
        _fail("VIDEO_FIRST_FRAME_MISMATCH", "first frame must match the selected source asset")
    last_frame_id = _asset_id(
        raw.get("last_frame_asset_id"),
        "last_frame_asset_id",
        optional=True,
    )

    provider = str(raw.get("provider") or OFFLINE_VIDEO_PROVIDER).strip()
    if provider != OFFLINE_VIDEO_PROVIDER:
        _fail("VIDEO_PROVIDER_UNSUPPORTED", "video provider is not authorized for this build")
    if raw.get("provider_call_confirmed", False) is not False:
        _fail("VIDEO_COST_POLICY_INVALID", "offline video preview cannot record a paid call")
    if raw.get("automatic_paid_retry", False) is not False:
        _fail("VIDEO_COST_POLICY_INVALID", "video tasks cannot authorize automatic paid retry")

    result = {
        "contract_version": IMAGE_TO_VIDEO_CONTRACT_VERSION,
        "prompt": prompt,
        "output_ratio": output_ratio,
        "duration_seconds": duration_seconds,
        "motion_intensity": motion_intensity,
        "first_frame_asset_id": first_frame_id,
        "last_frame_asset_id": last_frame_id,
        "provider": provider,
        "provider_call_confirmed": False,
        "automatic_paid_retry": False,
    }
    if "output_root" in raw:
        result["output_root"] = str(raw["output_root"])
    return result
