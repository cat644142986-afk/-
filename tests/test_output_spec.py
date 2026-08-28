import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


MODULE_DATA_DIR = tempfile.TemporaryDirectory()
os.environ.setdefault("PRODUCT_ATELIER_DATA_DIR", MODULE_DATA_DIR.name)
os.environ.setdefault(
    "PRODUCT_ATELIER_LEGACY_CONFIG", str(Path(MODULE_DATA_DIR.name) / "no-legacy-config.json")
)
os.environ.setdefault(
    "PRODUCT_ATELIER_KNOWLEDGE_BASE", str(Path(MODULE_DATA_DIR.name) / "no-knowledge-vault")
)

from python import server  # noqa: E402


class OutputSpecTests(unittest.TestCase):
    def test_gpt_preserves_original_landscape_ratio_with_provider_size(self) -> None:
        spec = server.resolve_output_spec(
            "gpt-image-2", "original", "4k", (1500, 1000), explicit=True
        )

        width, height = (int(part) for part in spec["provider_size"].split("x"))
        self.assertEqual(spec["provider_family"], "gpt-image-2")
        self.assertEqual(spec["requested_ratio"], "original")
        self.assertAlmostEqual(width / height, 1.5, delta=0.015)
        self.assertEqual(width % 16, 0)
        self.assertEqual(height % 16, 0)
        self.assertEqual(spec["provider_params"]["size"], spec["provider_size"])
        self.assertEqual(spec["provider_params"]["quality"], "high")

    def test_gemini_original_ratio_uses_nearest_native_ratio_without_square_fallback(self) -> None:
        spec = server.resolve_output_spec(
            "gemini-3.1-flash-image-preview",
            "original",
            "2k",
            (1000, 1400),
            explicit=True,
        )

        self.assertEqual(spec["effective_ratio"], "3:4")
        self.assertEqual(spec["provider_params"], {"aspectRatio": "3:4", "imageSize": "2K"})
        self.assertNotEqual(spec["effective_ratio"], "1:1")

    def test_named_ratio_and_resolution_map_to_each_models_real_contract(self) -> None:
        gpt = server.resolve_output_spec("gpt-image-2", "16:9", "4k", (900, 1600))
        gemini = server.resolve_output_spec(
            "gemini-3.1-flash-image-preview", "16:9", "4k", (900, 1600)
        )

        self.assertEqual(gpt["provider_params"]["size"], "3840x2160")
        self.assertEqual(gemini["provider_params"]["aspectRatio"], "16:9")
        self.assertEqual(gemini["provider_params"]["imageSize"], "4K")

    def test_submit_uses_plural_reference_array_and_top_level_prompt(self) -> None:
        spec = server.resolve_output_spec("gpt-image-2", "4:5", "2k", (800, 1000))
        with mock.patch.object(
            server,
            "api_request",
            return_value={"code": 200, "data": {"task_id": "task-1"}},
        ) as request:
            task_id = server.submit_generate(
                "commercial product",
                "gpt-image-2",
                "data:image/png;base64,AAAA",
                output_spec=spec,
            )

        self.assertEqual(task_id, "task-1")
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["prompt"], "commercial product")
        self.assertEqual(body["params"]["images"], ["data:image/png;base64,AAAA"])
        self.assertEqual(body["params"]["size"], "2048x2560")
        self.assertNotIn("image", body["params"])
        self.assertNotIn("imageSize", body["params"])

    def test_measurement_detects_provider_square_fallback(self) -> None:
        spec = server.resolve_output_spec("gpt-image-2", "16:9", "2k", (1600, 900))
        measurement = server._output_measurement(Image.new("RGB", (1024, 1024)), spec)

        self.assertFalse(measurement["aspect_matches"])
        self.assertEqual(measurement["actual_ratio"], "1:1")

    def test_invalid_user_choice_is_rejected_before_provider_work(self) -> None:
        with self.assertRaises(server.JobExecutionError) as caught:
            server.resolve_output_spec("gpt-image-2", "7:5", "2k", (700, 500))
        self.assertEqual(caught.exception.code, "INVALID_OUTPUT_RATIO")


if __name__ == "__main__":
    unittest.main()
