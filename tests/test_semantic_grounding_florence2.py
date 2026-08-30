from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from python.semantic_grounding import ground_semantic_candidates
from python.semantic_grounding_florence2 import (
    OPEN_VOCABULARY_TASK,
    TransformersFlorence2Adapter,
)


class Florence2EvaluationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.model_dir = self.root / "florence2"
        self.model_dir.mkdir()
        (self.model_dir / "config.json").write_text("{}", encoding="utf-8")
        self.image_path = self.root / "fixture.png"
        Image.new("RGB", (200, 100), (240, 235, 225)).save(self.image_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_native_adapter_stays_local_and_parses_generated_boxes(self) -> None:
        load_calls = []

        class FakeTensor:
            def __init__(self, value=None) -> None:
                self.value = value
                self.to_calls = []

            def to(self, *args):
                self.to_calls.append(args)
                return self

        class FakeProcessor:
            @classmethod
            def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
                load_calls.append(("processor", path, kwargs))
                return cls()

            def __call__(self, **kwargs):  # type: ignore[no-untyped-def]
                self.call_kwargs = kwargs
                return {
                    "input_ids": FakeTensor([1]),
                    "pixel_values": FakeTensor([]),
                }

            def batch_decode(self, *_args, **_kwargs):
                return ["<s>fixture</s>"]

            def post_process_generation(self, text, **kwargs):  # type: ignore[no-untyped-def]
                self.post_process_call = (text, kwargs)
                return {
                    OPEN_VOCABULARY_TASK: {
                        "bboxes": [[20, 10, 120, 80]],
                        "bboxes_labels": ["bottle"],
                    }
                }

        class FakeModel:
            @classmethod
            def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
                load_calls.append(("model", path, kwargs))
                return cls()

            def to(self, _device):
                return self

            def eval(self):
                return self

            def generate(self, **kwargs):  # type: ignore[no-untyped-def]
                self.generate_kwargs = kwargs
                return FakeTensor([2])

        class NoGrad:
            def __enter__(self):
                return None

            def __exit__(self, *_args):
                return False

        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        fake_torch.no_grad = NoGrad
        fake_torch.float16 = "float16"
        fake_torch.float32 = "float32"
        fake_transformers = types.ModuleType("transformers")
        fake_transformers.Florence2Processor = FakeProcessor
        fake_transformers.Florence2ForConditionalGeneration = FakeModel

        adapter = TransformersFlorence2Adapter(self.model_dir)
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
        self.assertEqual(result["adapter_id"], "transformers-florence2-local-eval")
        self.assertEqual(result["candidates"][0]["bbox"], [0.1, 0.1, 0.5, 0.7])
        self.assertEqual(
            adapter._processor.call_kwargs["text"],
            OPEN_VOCABULARY_TASK + "bottle",
        )
        self.assertEqual(
            adapter._processor.post_process_call[1]["task"],
            OPEN_VOCABULARY_TASK,
        )
        self.assertEqual([call[0] for call in load_calls], ["processor", "model"])
        for _kind, path, kwargs in load_calls:
            self.assertEqual(path, str(self.model_dir))
            self.assertTrue(kwargs["local_files_only"])
            self.assertFalse(kwargs["trust_remote_code"])

    def test_untranslated_chinese_is_rejected_before_loading(self) -> None:
        adapter = TransformersFlorence2Adapter(self.model_dir)
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
