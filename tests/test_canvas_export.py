from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from python.canvas_export import CanvasExportError, render_canvas_png


def layer(
    source_id: str,
    size: tuple[int, int],
    *,
    x: float,
    y: float,
    z_index: int,
    rotation: float = 0,
    opacity: float = 1,
    visible: bool = True,
) -> dict:
    return {
        "id": f"layer:{source_id}",
        "artboard_id": "artboard:test",
        "source": {
            "kind": "asset",
            "id": source_id,
            "proxy_ref": "proxy:thumbnail:512",
            "original_pixel_width": size[0],
            "original_pixel_height": size[1],
        },
        "transform": {
            "x": x,
            "y": y,
            "scale_x": 1,
            "scale_y": 1,
            "rotation_degrees": rotation,
            "opacity": opacity,
        },
        "z_index": z_index,
        "visible": visible,
        "locked": False,
    }


def document(layers: list[dict], *, width: int = 40, height: int = 30) -> dict:
    return {
        "active_artboard_id": "artboard:test",
        "artboards": [{
            "id": "artboard:test",
            "name": "测试画板",
            "rect": {"x": 10, "y": 20, "width": width, "height": height},
            "export": {
                "pixel_width": width * 2,
                "pixel_height": height * 2,
                "color_space": "srgb",
            },
        }],
        "layers": layers,
    }


class CanvasExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.paths: dict[str, Path] = {}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_source(self, source_id: str, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
        path = self.root / f"{source_id}.png"
        Image.new("RGBA", size, color).save(path, "PNG")
        self.paths[source_id] = path

    def resolve(self, source: dict) -> Path:
        return self.paths[source["id"]]

    def test_rotation_opacity_visibility_z_order_and_artboard_scaling(self) -> None:
        self.add_source("base", (40, 30), (255, 0, 0, 255))
        self.add_source("yellow", (10, 5), (255, 255, 0, 255))
        self.add_source("green", (10, 5), (0, 255, 0, 255))
        self.add_source("hidden", (40, 30), (0, 0, 255, 255))
        layers = [
            layer("base", (40, 30), x=10, y=20, z_index=0),
            layer("yellow", (10, 5), x=20, y=25, z_index=1, rotation=90),
            layer("green", (10, 5), x=20, y=25, z_index=2, rotation=90, opacity=0.5),
            layer("hidden", (40, 30), x=10, y=20, z_index=3, visible=False),
        ]

        content, metadata = render_canvas_png(document(layers), self.resolve)

        with Image.open(io.BytesIO(content)) as exported:
            self.assertEqual(exported.size, (80, 60))
            self.assertEqual(exported.mode, "RGBA")
            self.assertEqual(exported.getpixel((14, 20)), (127, 255, 0, 255))
            self.assertEqual(exported.getpixel((30, 20)), (255, 0, 0, 255))
        self.assertEqual(metadata["rendered_layer_count"], 3)
        self.assertEqual(metadata["source"], "original-assets")

    def test_output_is_clipped_to_exact_transparent_artboard(self) -> None:
        self.add_source("small", (4, 4), (20, 40, 220, 255))
        content, _ = render_canvas_png(
            document([layer("small", (4, 4), x=11, y=21, z_index=0)], width=6, height=6),
            self.resolve,
        )

        with Image.open(io.BytesIO(content)) as exported:
            self.assertEqual(exported.size, (12, 12))
            self.assertEqual(exported.getpixel((0, 0))[3], 0)
            self.assertEqual(exported.getpixel((4, 4)), (20, 40, 220, 255))
            self.assertEqual(exported.getpixel((11, 11))[3], 0)

    def test_source_errors_do_not_expose_resolved_paths(self) -> None:
        missing_path = self.root / "private" / "customer-source.png"
        payload = document([layer("missing", (4, 4), x=10, y=20, z_index=0)], width=4, height=4)

        with self.assertRaises(CanvasExportError) as raised:
            render_canvas_png(payload, lambda _: missing_path)

        self.assertEqual(raised.exception.code, "CANVAS_SOURCE_UNAVAILABLE")
        self.assertNotIn(str(self.root), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
