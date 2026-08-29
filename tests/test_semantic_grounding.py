from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from python.semantic_grounding import (
    GROUNDING_MODEL_PATH_ENV,
    TransformersGroundingDinoAdapter,
    UnavailableGroundingAdapter,
    ground_semantic_candidates,
    grounding_adapter_from_environment,
)


class FakeGroundingAdapter:
    adapter_id = "fixture-grounding"

    def __init__(self, candidates=None, *, error: Exception | None = None) -> None:
        self.candidates = list(candidates or [])
        self.error = error
        self.calls = 0

    def detect(self, image, query, *, box_threshold, text_threshold):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.image_size = image.size
        self.query = query
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold
        if self.error:
            raise self.error
        return self.candidates


class SemanticGroundingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temp_dir.name) / "fixture.png"
        Image.new("RGB", (200, 100), (235, 226, 210)).save(self.image_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unconfigured_environment_is_honestly_unavailable(self) -> None:
        adapter = grounding_adapter_from_environment({})
        self.assertIsInstance(adapter, UnavailableGroundingAdapter)
        result = ground_semantic_candidates(
            self.image_path,
            "汉堡",
            2,
            adapter=adapter,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["available"])
        self.assertFalse(result["attempted"])
        self.assertEqual(result["candidates"], [])
        self.assertIn("手动框选", result["message"])

    def test_configured_path_creates_a_lazy_local_only_adapter(self) -> None:
        model_dir = Path(self.temp_dir.name) / "grounding-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        adapter = grounding_adapter_from_environment({
            GROUNDING_MODEL_PATH_ENV: str(model_dir),
        })
        self.assertIsInstance(adapter, TransformersGroundingDinoAdapter)
        self.assertEqual(adapter.model_path, model_dir)
        self.assertIsNone(adapter._model)
        with mock.patch.dict(
            "python.semantic_grounding.os.environ",
            {GROUNDING_MODEL_PATH_ENV: str(model_dir)},
        ):
            self.assertIs(
                grounding_adapter_from_environment(),
                grounding_adapter_from_environment(),
            )

    def test_transformers_adapter_enforces_local_files_and_returns_pixel_boxes(self) -> None:
        model_dir = Path(self.temp_dir.name) / "grounding-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        load_calls = []

        class FakeTensor:
            def __init__(self, value=None) -> None:
                self.value = value

            def to(self, _device):  # type: ignore[no-untyped-def]
                return self

            def detach(self):
                return self

            def cpu(self):
                return self

            def tolist(self):
                return self.value

            def item(self):
                return self.value

        class FakeProcessor:
            @classmethod
            def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
                load_calls.append(("processor", path, kwargs))
                return cls()

            def __call__(self, **kwargs):  # type: ignore[no-untyped-def]
                self.call_kwargs = kwargs
                return {"input_ids": FakeTensor([1, 2, 3]), "pixel_values": FakeTensor([])}

            def post_process_grounded_object_detection(self, *_args, **_kwargs):
                return [{
                    "boxes": [FakeTensor([20, 10, 120, 80])],
                    "scores": [FakeTensor(0.82)],
                    "labels": ["burger"],
                }]

        class FakeModel:
            @classmethod
            def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
                load_calls.append(("model", path, kwargs))
                return cls()

            def to(self, _device):
                return self

            def eval(self):
                return self

            def __call__(self, **_kwargs):  # type: ignore[no-untyped-def]
                return object()

        class NoGrad:
            def __enter__(self):
                return None

            def __exit__(self, *_args):
                return False

        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        fake_torch.no_grad = NoGrad
        fake_transformers = types.ModuleType("transformers")
        fake_transformers.AutoProcessor = FakeProcessor
        fake_transformers.AutoModelForZeroShotObjectDetection = FakeModel

        adapter = TransformersGroundingDinoAdapter(model_dir)
        with mock.patch.dict(
            "sys.modules",
            {"torch": fake_torch, "transformers": fake_transformers},
        ):
            result = ground_semantic_candidates(
                self.image_path,
                "Hamburger",
                1,
                adapter=adapter,
            )
        self.assertEqual(result["status"], "candidates")
        self.assertEqual(result["candidates"][0]["bbox"], [0.1, 0.1, 0.5, 0.7])
        self.assertEqual(adapter._processor.call_kwargs["text"], "hamburger.")
        self.assertEqual([call[0] for call in load_calls], ["processor", "model"])
        for _kind, path, kwargs in load_calls:
            self.assertEqual(path, str(model_dir))
            self.assertTrue(kwargs["local_files_only"])
            self.assertFalse(kwargs["trust_remote_code"])

    def test_local_english_model_refuses_untranslated_chinese_instead_of_faking_semantics(self) -> None:
        model_dir = Path(self.temp_dir.name) / "grounding-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        adapter = TransformersGroundingDinoAdapter(model_dir)
        result = ground_semantic_candidates(
            self.image_path,
            "两个汉堡",
            2,
            adapter=adapter,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "query_translation_required")
        self.assertFalse(result["attempted"])
        self.assertIsNone(adapter._model)
        self.assertIn("不能直接理解中文", result["message"])

    def test_confident_candidates_are_normalized_sorted_and_limited_to_count(self) -> None:
        adapter = FakeGroundingAdapter([
            {"bbox_xyxy": [110, 10, 190, 90], "confidence": 0.73, "label": "burger"},
            {"bbox_xyxy": [10, 15, 90, 95], "confidence": 0.91, "label": "burger"},
            {"bbox_xyxy": [80, 20, 130, 70], "confidence": 0.62, "label": "burger"},
        ])
        result = ground_semantic_candidates(
            self.image_path,
            "汉堡",
            2,
            adapter=adapter,
        )
        self.assertEqual(result["status"], "candidates")
        self.assertTrue(result["available"])
        self.assertTrue(result["attempted"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual([item["confidence"] for item in result["candidates"]], [0.91, 0.73])
        self.assertEqual(result["candidates"][0]["bbox"], [0.05, 0.15, 0.4, 0.8])
        self.assertTrue(all(item["origin"] == "automatic" for item in result["candidates"]))

    def test_partial_or_weak_candidates_stay_low_confidence(self) -> None:
        adapter = FakeGroundingAdapter([
            {"bbox_xyxy": [20, 10, 80, 70], "confidence": 0.34, "label": "burger"},
        ])
        result = ground_semantic_candidates(
            self.image_path,
            "汉堡",
            2,
            adapter=adapter,
        )
        self.assertEqual(result["status"], "low_confidence")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("补充框选", result["message"])

    def test_no_match_and_runtime_failure_both_preserve_manual_recovery(self) -> None:
        no_match = ground_semantic_candidates(
            self.image_path,
            "玻璃杯",
            1,
            adapter=FakeGroundingAdapter([]),
        )
        self.assertEqual(no_match["status"], "no_match")
        self.assertTrue(no_match["available"])
        self.assertEqual(no_match["candidates"], [])
        self.assertIn("手动框选", no_match["message"])

        failed = ground_semantic_candidates(
            self.image_path,
            "玻璃杯",
            1,
            adapter=FakeGroundingAdapter(error=RuntimeError("fixture failure")),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["available"])
        self.assertTrue(failed["attempted"])
        self.assertEqual(failed["reason"], "inference_failed")
        self.assertNotIn("fixture failure", failed["message"])
        self.assertIn("仍可继续处理", failed["message"])


if __name__ == "__main__":
    unittest.main()
