"""Deterministic offline fixtures and blind-score validation for R7."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw


FIXTURE_RENDERER_VERSION = "generation-quality-fixture-2026-08-30.1"
SCORE_AXES = (
    "subject_fidelity",
    "structure_and_count",
    "packaging_text",
    "brand_color",
    "composition",
    "material",
    "lighting",
    "background_cleanliness",
    "edge_quality",
    "commercial_usability",
)


def render_procedural_fixture(scene: str, size: tuple[int, int] = (640, 640)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    width, height = size
    scene = str(scene)

    if scene == "packaging-text-brand":
        draw.rounded_rectangle((180, 90, 460, 560), radius=30, fill="#FF6548", outline="#17202A", width=6)
        draw.rectangle((215, 185, 425, 385), fill="#FFF8E8", outline="#17202A", width=4)
        draw.text((270, 225), "PA TEA", fill="#17202A")
        draw.text((292, 285), "250g", fill="#17202A")
        draw.ellipse((285, 420, 355, 490), fill="#2E8B57")
    elif scene == "multi-product-count":
        colors = ("#FF6548", "#2E8B57", "#315E9E")
        for index, color in enumerate(colors):
            left = 90 + index * 185
            draw.rounded_rectangle((left, 190, left + 135, 510), radius=24, fill=color, outline="#17202A", width=5)
            draw.rectangle((left + 32, 140, left + 103, 205), fill="#D9D9D9", outline="#17202A", width=4)
    elif scene == "reflective-material":
        draw.rounded_rectangle((215, 100, 425, 555), radius=70, fill="#C5CBD3", outline="#3A414B", width=5)
        draw.rectangle((270, 55, 370, 130), fill="#8F98A3", outline="#3A414B", width=4)
        draw.rectangle((245, 135, 275, 520), fill="#F8FAFD")
        draw.rectangle((305, 125, 330, 535), fill="#6F7883")
        draw.rectangle((370, 145, 395, 510), fill="#FFFFFF")
    elif scene == "vessel-preservation":
        draw.ellipse((110, 270, 530, 540), fill="#DDE8F0", outline="#264653", width=8)
        draw.ellipse((145, 245, 495, 455), fill="#FFF8E8", outline="#264653", width=6)
        for center_x, center_y, radius, color in (
            (250, 335, 70, "#D96C3D"),
            (340, 320, 75, "#E9A23B"),
            (390, 370, 60, "#5D8C4A"),
        ):
            draw.ellipse((center_x-radius, center_y-radius, center_x+radius, center_y+radius), fill=color)
    elif scene == "cropped-completion":
        draw.rounded_rectangle((430, 120, 710, 560), radius=35, fill="#FFCB47", outline="#17202A", width=6)
        draw.rectangle((475, 220, 620, 390), fill="#FFF8E8", outline="#17202A", width=4)
        draw.text((510, 275), "SNACK", fill="#17202A")
    elif scene == "complex-shadow":
        draw.ellipse((225, 420, 590, 555), fill="#BEC4CC")
        draw.rounded_rectangle((180, 120, 420, 500), radius=45, fill="#6B8E5A", outline="#17202A", width=6)
        draw.rectangle((225, 210, 375, 355), fill="#FFF8E8", outline="#17202A", width=4)
        draw.ellipse((285, 70, 335, 125), fill="#D0D4D8", outline="#17202A", width=4)
    else:
        raise ValueError(f"unsupported procedural fixture scene: {scene}")
    return image


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_scorecard(scorecard: Mapping[str, Any]) -> dict[str, float]:
    missing = [axis for axis in SCORE_AXES if axis not in scorecard]
    extra = [axis for axis in scorecard if axis not in SCORE_AXES]
    if missing or extra:
        raise ValueError(f"scorecard axes mismatch: missing={missing}, extra={extra}")
    normalized = {}
    for axis in SCORE_AXES:
        value = scorecard[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"score must be numeric: {axis}")
        number = float(value)
        if not 0 <= number <= 5:
            raise ValueError(f"score must be between 0 and 5: {axis}")
        normalized[axis] = number
    return normalized


def load_quality_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported generation quality manifest")
    if payload.get("renderer_version") != FIXTURE_RENDERER_VERSION:
        raise ValueError("generation quality renderer version mismatch")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("generation quality manifest has no cases")
    ids = [str(case.get("id") or "") for case in cases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("generation quality case ids must be unique and non-empty")
    if tuple(payload.get("score_axes") or ()) != SCORE_AXES:
        raise ValueError("generation quality score axes changed without a contract bump")
    return payload
