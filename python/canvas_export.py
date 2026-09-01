from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image, ImageOps


class CanvasExportError(RuntimeError):
    code = "CANVAS_EXPORT_FAILED"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code or self.code


def _active_artboard(document: Mapping[str, Any], artboard_id: str | None) -> dict[str, Any]:
    selected_id = str(artboard_id or document.get("active_artboard_id") or "").strip()
    for artboard in document.get("artboards") or []:
        if str(artboard.get("id")) == selected_id:
            return dict(artboard)
    raise CanvasExportError("The requested canvas artboard does not exist", code="CANVAS_ARTBOARD_NOT_FOUND")


def _source_image(
    layer: Mapping[str, Any],
    resolve_source_path: Callable[[Mapping[str, Any]], Path],
) -> Image.Image:
    source = layer["source"]
    source_id = str(source["id"])
    path = Path(resolve_source_path(source))
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            image.load()
    except CanvasExportError:
        raise
    except Exception as exc:
        raise CanvasExportError(
            f"Canvas source is unavailable: {source_id}",
            code="CANVAS_SOURCE_UNAVAILABLE",
        ) from exc

    expected_size = (
        int(source["original_pixel_width"]),
        int(source["original_pixel_height"]),
    )
    if image.size != expected_size:
        raise CanvasExportError(
            f"Canvas source dimensions changed: {source_id}",
            code="CANVAS_SOURCE_DIMENSION_MISMATCH",
        )
    return image


def _layer_affine(
    artboard: Mapping[str, Any],
    transform: Mapping[str, Any],
) -> tuple[float, float, float, float, float, float]:
    rect = artboard["rect"]
    export = artboard["export"]
    output_scale_x = float(export["pixel_width"]) / float(rect["width"])
    output_scale_y = float(export["pixel_height"]) / float(rect["height"])
    layer_scale_x = float(transform["scale_x"])
    layer_scale_y = float(transform["scale_y"])
    angle = math.radians(float(transform["rotation_degrees"]))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    offset_x = float(rect["x"]) - float(transform["x"])
    offset_y = float(rect["y"]) - float(transform["y"])

    # Pillow consumes the inverse map: output pixel -> original source pixel.
    return (
        cosine / (layer_scale_x * output_scale_x),
        sine / (layer_scale_x * output_scale_y),
        (cosine * offset_x + sine * offset_y) / layer_scale_x,
        -sine / (layer_scale_y * output_scale_x),
        cosine / (layer_scale_y * output_scale_y),
        (-sine * offset_x + cosine * offset_y) / layer_scale_y,
    )


def render_canvas_png(
    document: Mapping[str, Any],
    resolve_source_path: Callable[[Mapping[str, Any]], Path],
    *,
    artboard_id: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    artboard = _active_artboard(document, artboard_id)
    export = artboard["export"]
    if export["color_space"] != "srgb":
        raise CanvasExportError(
            "This canvas color space cannot be exported yet",
            code="CANVAS_COLOR_SPACE_UNSUPPORTED",
        )

    output_size = (int(export["pixel_width"]), int(export["pixel_height"]))
    canvas = Image.new("RGBA", output_size, (0, 0, 0, 0))
    indexed_layers = list(enumerate(document.get("layers") or []))
    indexed_layers.sort(key=lambda item: (int(item[1]["z_index"]), item[0]))
    rendered_layer_count = 0

    for _, layer in indexed_layers:
        if str(layer["artboard_id"]) != str(artboard["id"]) or not layer["visible"]:
            continue
        image = _source_image(layer, resolve_source_path)
        transformed = image.transform(
            output_size,
            Image.Transform.AFFINE,
            _layer_affine(artboard, layer["transform"]),
            resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        opacity = float(layer["transform"]["opacity"])
        if opacity < 1:
            alpha = transformed.getchannel("A").point(
                lambda value: max(0, min(255, round(value * opacity)))
            )
            transformed.putalpha(alpha)
        canvas = Image.alpha_composite(canvas, transformed)
        rendered_layer_count += 1

    buffer = io.BytesIO()
    canvas.save(buffer, "PNG", compress_level=6)
    payload = buffer.getvalue()
    return payload, {
        "artboard_id": str(artboard["id"]),
        "pixel_width": output_size[0],
        "pixel_height": output_size[1],
        "color_space": "srgb",
        "rendered_layer_count": rendered_layer_count,
        "source": "original-assets",
    }
