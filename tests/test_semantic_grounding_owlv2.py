from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from python.semantic_grounding import ground_semantic_candidates
from python.semantic_grounding_owlv2 import TransformersOwlv2Adapter


class Owlv2EvaluationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.model_dir = self.root / "owlv2"
        self.model_dir.mkdir()
        (self.model_dir / "config.json").write_text("{}", encoding="utf-8")
        self.image_path = self.root / "fixture.png"
        Image.new("RGB", (200, 100), (240, 235, 225)).save(self.image_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adapter_is_local_only_and_normalizes_results(self) -> None:
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
                return {"input_ids": FakeTensor([1]), "pixel_values": FakeTensor([])}

            def post_process_grounded_object_detection(self, **kwargs):  # type: ignore[no-untyped-def]
                self.post_process_kwargs = kwargs
                return [{
                    "boxes": [FakeTensor([20, 10, 120, 80])],
                    "scores": [FakeTensor(0.86)],
                    "text_labels": ["a photo of bottle"],
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
        fake_transformers.Owlv2Processor = FakeProcessor
        fake_transformers.Owlv2ForObjectDetection = FakeModel

        adapter = TransformersOwlv2Adapter(self.model_dir)
        with mock.patch.dict(
            "sys.modules",
            {"torch": fake_torch, "transformers": fake_transformers},
        ):
            result = ground_semantic_candidates(
                self.image_path,
                "Bottle",
                1,
                adapter=adapter,
            )
        self.assertEqual(result["status"], "candidates")
        self.assertEqual(result["adapter_id"], "transformers-owlv2-local-eval")
        self.assertEqual(result["candidates"][0]["bbox"], [0.1, 0.1, 0.5, 0.7])
        self.assertEqual(
            adapter._processor.call_kwargs["text"],
            [["a photo of bottle"]],
        )
        self.assertEqual(
            adapter._processor.post_process_kwargs["text_labels"],
            [["a photo of bottle"]],
        )
        self.assertEqual([call[0] for call in load_calls], ["processor", "model"])
        for _kind, path, kwargs in load_calls:
            self.assertEqual(path, str(self.model_dir))
            self.assertTrue(kwargs["local_files_only"])
            self.assertFalse(kwargs["trust_remote_code"])

    def test_adapter_rejects_untranslated_chinese_before_loading(self) -> None:
        adapter = TransformersOwlv2Adapter(self.model_dir)
        result = ground_semantic_candidates(
            self.image_path,
            "瓶子",
            1,
            adapter=adapter,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "query_translation_required")
        self.assertIsNone(adapter._model)


if __name__ == "__main__":
    unittest.main()
