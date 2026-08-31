from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from python.generation_quality_gate import evaluate_generation_quality


def square_spec() -> dict:
    return {
        "effective_ratio_value": 1.0,
        "effective_ratio": "1:1",
        "requested_resolution": "2k",
        "provider_size": "512x512",
    }


def centered_product(*, background=(255, 255, 255)) -> Image.Image:
    image = Image.new("RGB", (512, 512), background)
    ImageDraw.Draw(image).rectangle((128, 96, 384, 416), fill=(90, 45, 25))
    return image


class GenerationQualityGateTests(unittest.TestCase):
    def test_clean_white_background_product_passes_deterministic_checks(self) -> None:
        report = evaluate_generation_quality(centered_product(), square_spec())

        self.assertEqual(report["deterministic_status"], "pass")
        self.assertEqual(report["blocking_failures"], [])
        self.assertFalse(report["retry"]["authorized"])
        self.assertIn("reference_fidelity", report["unverified_axes"])
        self.assertIn("product_count", report["unverified_axes"])

    def test_gray_border_is_reported_as_white_background_failure(self) -> None:
        report = evaluate_generation_quality(
            centered_product(background=(230, 230, 230)), square_spec()
        )

        self.assertEqual(report["deterministic_status"], "fail")
        self.assertIn("white-background-border", report["blocking_failures"])

    def test_subject_touching_canvas_edge_is_reported_as_crop_risk(self) -> None:
        image = Image.new("RGB", (512, 512), "white")
        ImageDraw.Draw(image).rectangle((0, 80, 300, 430), fill=(30, 60, 90))
        report = evaluate_generation_quality(image, square_spec())

        self.assertEqual(report["deterministic_status"], "fail")
        self.assertIn("subject-border-contact", report["blocking_failures"])

    def test_blank_output_and_undersized_output_are_blocking_failures(self) -> None:
        blank = evaluate_generation_quality(Image.new("RGB", (512, 512), "white"), square_spec())
        self.assertIn("subject-presence", blank["blocking_failures"])

        small_spec = {**square_spec(), "provider_size": "2048x2048"}
        small = evaluate_generation_quality(centered_product(), small_spec)
        self.assertIn("resolution-floor", small["blocking_failures"])

    def test_wrong_aspect_is_detected_and_packaging_axes_remain_unverified(self) -> None:
        spec = {
            **square_spec(),
            "effective_ratio": "16:9",
            "effective_ratio_value": 16 / 9,
        }
        report = evaluate_generation_quality(
            centered_product(),
            spec,
            context={
                "category": "packaging",
                "intent_locks": {"packaging_text": True, "logo": True},
            },
        )

        self.assertIn("aspect-ratio", report["blocking_failures"])
        self.assertIn("packaging_text", report["unverified_axes"])
        self.assertIn("brand_logo", report["unverified_axes"])


if __name__ == "__main__":
    unittest.main()
