from __future__ import annotations

import unittest

from PIL import Image

from python.local_edit_contract import (
    LocalEditContractError,
    apply_strict_inpaint,
    apply_strict_outpaint,
    canvas_mask_fingerprint,
    image_fingerprint,
    normalize_local_edit_contract,
    render_canvas_mask_definition,
)


def solid(size: tuple[int, int], color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", size, color)


def inpaint_contract(mask: Image.Image) -> dict:
    return {
        "schema_version": 1,
        "operation_id": "operation:local-edit-1",
        "mode": "inpaint",
        "source_canvas_version_id": "canvas-version:source-1",
        "source_layer_id": "layer:source",
        "source_sha256": "F" * 64,
        "source_pixel_sha256": image_fingerprint(solid((6, 6), (220, 40, 30, 255))),
        "source_size": {"width": 6, "height": 6},
        "roi": {
            "id": "roi:local-edit-1",
            "coordinate_space": "source-pixel",
            "rect": {"x": 1, "y": 1, "width": 4, "height": 4},
        },
        "mask": {
            "id": "mask:local-edit-1",
            "roi_id": "roi:local-edit-1",
            "width": 6,
            "height": 6,
            "sha256": image_fingerprint(mask),
        },
        "strict_pixel_protection": True,
        "cost": {
            "mode": "free",
            "confirmed_call_count": 0,
            "user_confirmation_required": False,
            "user_confirmed": False,
            "automatic_paid_retry": False,
        },
    }


def outpaint_contract(*, transition_width: int = 0) -> dict:
    return {
        "schema_version": 1,
        "operation_id": "operation:outpaint-1",
        "mode": "outpaint",
        "source_canvas_version_id": "canvas-version:source-1",
        "source_layer_id": "layer:source",
        "source_sha256": "E" * 64,
        "source_pixel_sha256": image_fingerprint(solid((4, 4), (220, 40, 30, 255))),
        "source_size": {"width": 4, "height": 4},
        "roi": {
            "id": "roi:outpaint-1",
            "coordinate_space": "output-pixel",
            "rect": {"x": 0, "y": 0, "width": 8, "height": 6},
        },
        "mask": None,
        "strict_pixel_protection": True,
        "outpaint": {
            "output_width": 8,
            "output_height": 6,
            "source_x": 2,
            "source_y": 1,
            "transition_width": transition_width,
        },
        "cost": {
            "mode": "paid",
            "confirmed_call_count": 1,
            "user_confirmation_required": True,
            "user_confirmed": True,
            "automatic_paid_retry": False,
        },
    }


class LocalEditContractTests(unittest.TestCase):
    def test_vector_mask_renders_deterministically_and_never_escapes_roi(self) -> None:
        definition = {
            "schema_version": 1,
            "coordinate_space": "source-pixel",
            "width": 12,
            "height": 10,
            "base": "full",
            "strokes": [{
                "mode": "exclude",
                "radius": 1,
                "points": [{"x": 5, "y": 5}],
            }],
            "feather_radius": 1,
        }
        roi = {"x": 3, "y": 2, "width": 6, "height": 6}
        rendered = render_canvas_mask_definition(definition, roi)
        self.assertEqual(rendered.size, (12, 10))
        self.assertEqual(rendered.getpixel((0, 0)), 0)
        self.assertEqual(rendered.getpixel((11, 9)), 0)
        self.assertGreater(rendered.getpixel((3, 2)), 0)
        self.assertLess(rendered.getpixel((5, 5)), 255)
        self.assertEqual(canvas_mask_fingerprint(definition, roi), image_fingerprint(rendered))

    def test_contract_rejects_unknown_fields_and_out_of_bounds_roi(self) -> None:
        mask = Image.new("L", (6, 6), 0)
        payload = inpaint_contract(mask)
        payload["unexpected"] = True
        with self.assertRaisesRegex(LocalEditContractError, "unknown fields"):
            normalize_local_edit_contract(payload)

        payload = inpaint_contract(mask)
        payload["roi"]["rect"]["x"] = 5
        with self.assertRaises(LocalEditContractError) as invalid:
            normalize_local_edit_contract(payload)
        self.assertEqual(invalid.exception.code, "LOCAL_EDIT_ROI_OUT_OF_BOUNDS")

    def test_paid_contract_requires_explicit_confirmation_and_never_retries(self) -> None:
        payload = outpaint_contract()
        payload["cost"]["user_confirmed"] = False
        with self.assertRaises(LocalEditContractError) as unconfirmed:
            normalize_local_edit_contract(payload)
        self.assertEqual(unconfirmed.exception.code, "LOCAL_EDIT_COST_NOT_CONFIRMED")

        payload = outpaint_contract()
        payload["cost"]["automatic_paid_retry"] = True
        with self.assertRaises(LocalEditContractError) as retry:
            normalize_local_edit_contract(payload)
        self.assertEqual(retry.exception.code, "LOCAL_EDIT_AUTOMATIC_PAID_RETRY_FORBIDDEN")

    def test_file_and_decoded_pixel_fingerprints_are_distinct_required_facts(self) -> None:
        mask = Image.new("L", (6, 6), 0)
        payload = inpaint_contract(mask)
        normalized = normalize_local_edit_contract(payload)
        self.assertEqual(normalized["source_sha256"], "F" * 64)
        self.assertEqual(
            normalized["source_pixel_sha256"],
            image_fingerprint(solid((6, 6), (220, 40, 30, 255))),
        )

        missing_pixel = inpaint_contract(mask)
        missing_pixel.pop("source_pixel_sha256")
        with self.assertRaisesRegex(LocalEditContractError, "source_pixel_sha256"):
            normalize_local_edit_contract(missing_pixel)

    def test_strict_inpaint_restores_every_pixel_outside_mask(self) -> None:
        source = solid((6, 6), (220, 40, 30, 255))
        candidate = solid((6, 6), (20, 80, 220, 255))
        mask = Image.new("L", (6, 6), 0)
        for y in range(2, 4):
            for x in range(2, 4):
                mask.putpixel((x, y), 255)
        original_fingerprint = image_fingerprint(source)

        result, receipt = apply_strict_inpaint(
            source,
            candidate,
            mask,
            inpaint_contract(mask),
        )

        self.assertEqual(result.getpixel((2, 2)), (20, 80, 220, 255))
        self.assertEqual(result.getpixel((0, 0)), (220, 40, 30, 255))
        self.assertEqual(result.getpixel((4, 4)), (220, 40, 30, 255))
        self.assertEqual(receipt["outside_mask_changed_pixels"], 0)
        self.assertEqual(receipt["changed_pixels"], 4)
        self.assertEqual(receipt["source_sha256"], "F" * 64)
        self.assertEqual(receipt["source_pixel_sha256"], original_fingerprint)
        self.assertEqual(receipt["undo_source_sha256"], original_fingerprint)
        self.assertEqual(image_fingerprint(source), original_fingerprint)

    def test_inpaint_rejects_nonzero_mask_pixels_outside_roi(self) -> None:
        source = solid((6, 6), (220, 40, 30, 255))
        candidate = solid((6, 6), (20, 80, 220, 255))
        mask = Image.new("L", (6, 6), 0)
        mask.putpixel((0, 0), 255)
        with self.assertRaises(LocalEditContractError) as invalid:
            apply_strict_inpaint(source, candidate, mask, inpaint_contract(mask))
        self.assertEqual(invalid.exception.code, "LOCAL_EDIT_MASK_OUTSIDE_ROI")

    def test_contract_rejects_a_stale_source_version_with_the_same_size(self) -> None:
        source = solid((6, 6), (220, 40, 30, 255))
        source.putpixel((5, 5), (220, 40, 31, 255))
        candidate = solid((6, 6), (20, 80, 220, 255))
        mask = Image.new("L", (6, 6), 0)
        mask.putpixel((2, 2), 255)

        with self.assertRaises(LocalEditContractError) as stale:
            apply_strict_inpaint(source, candidate, mask, inpaint_contract(mask))
        self.assertEqual(stale.exception.code, "LOCAL_EDIT_SOURCE_FINGERPRINT_MISMATCH")

    def test_outpaint_only_writes_new_area_without_transition_band(self) -> None:
        source = solid((4, 4), (220, 40, 30, 255))
        candidate = solid((8, 6), (20, 80, 220, 255))
        source_fingerprint = image_fingerprint(source)

        result, receipt = apply_strict_outpaint(
            source,
            candidate,
            outpaint_contract(transition_width=0),
        )

        self.assertEqual(result.getpixel((0, 0)), (20, 80, 220, 255))
        self.assertEqual(result.getpixel((2, 1)), (220, 40, 30, 255))
        self.assertEqual(result.getpixel((5, 4)), (220, 40, 30, 255))
        self.assertEqual(receipt["protected_changed_pixels"], 0)
        self.assertEqual(receipt["transition_changed_pixels"], 0)
        self.assertEqual(receipt["new_area_changed_pixels"], 32)
        self.assertEqual(receipt["source_sha256"], "E" * 64)
        self.assertEqual(receipt["source_pixel_sha256"], source_fingerprint)
        self.assertEqual(receipt["undo_source_sha256"], source_fingerprint)

    def test_outpaint_transition_band_is_explicit_and_bounded(self) -> None:
        source = solid((4, 4), (220, 40, 30, 255))
        candidate = solid((8, 6), (20, 80, 220, 255))

        result, receipt = apply_strict_outpaint(
            source,
            candidate,
            outpaint_contract(transition_width=1),
        )

        self.assertEqual(result.getpixel((2, 1)), (20, 80, 220, 255))
        self.assertEqual(result.getpixel((3, 2)), (220, 40, 30, 255))
        self.assertEqual(result.getpixel((4, 3)), (220, 40, 30, 255))
        self.assertEqual(receipt["protected_changed_pixels"], 0)
        self.assertEqual(receipt["transition_changed_pixels"], 12)
        self.assertEqual(receipt["new_area_changed_pixels"], 32)

    def test_failed_outpaint_validation_cannot_mutate_the_source(self) -> None:
        source = solid((4, 4), (220, 40, 30, 255))
        before_pixels = source.tobytes()
        invalid_candidate = solid((7, 6), (20, 80, 220, 255))

        with self.assertRaises(LocalEditContractError) as invalid:
            apply_strict_outpaint(source, invalid_candidate, outpaint_contract())
        self.assertEqual(invalid.exception.code, "LOCAL_EDIT_CANDIDATE_SIZE_MISMATCH")
        self.assertEqual(source.tobytes(), before_pixels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
