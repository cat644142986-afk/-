from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image

try:
    from semantic_grounding import GroundingAdapterUnavailable, _contains_cjk
except ImportError:  # pragma: no cover - package imports used by tests and tools
    from python.semantic_grounding import GroundingAdapterUnavailable, _contains_cjk


OPEN_VOCABULARY_TASK = "<OPEN_VOCABULARY_DETECTION>"


class TransformersFlorence2Adapter:
    """Native, local-only Florence-2 adapter for isolated evaluation.

    Florence-2 produces deterministic structured boxes rather than calibrated
    detection probabilities. Candidates therefore receive a synthetic score of
    1.0 solely to pass through the shared evaluator; the adapter must be judged
    on its observed boxes and no-match behavior, never on that synthetic score.
    """

    adapter_id = "transformers-florence2-local-eval"

    def __init__(self, model_path: str | Path, *, device: str | None = None) -> None:
        path = Path(model_path).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        self.model_path = path
        self.requested_device = str(device or "").strip()
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"
        self._dtype: Any = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if not self.model_path.is_dir():
                raise GroundingAdapterUnavailable("model_path_missing")
            if not (self.model_path / "config.json").is_file():
                raise GroundingAdapterUnavailable("model_config_missing")
            try:
                import torch
                from transformers import (
                    Florence2ForConditionalGeneration,
                    Florence2Processor,
                )
            except Exception as exc:  # pragma: no cover - optional local runtime
                raise GroundingAdapterUnavailable("runtime_missing") from exc

            device = self.requested_device or (
                "cuda" if torch.cuda.is_available() else "cpu"
            )
            dtype = torch.float16 if device.startswith("cuda") else torch.float32
            try:
                processor = Florence2Processor.from_pretrained(
                    str(self.model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = Florence2ForConditionalGeneration.from_pretrained(
                    str(self.model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                    use_safetensors=True,
                    dtype=dtype,
                )
                model = model.to(device).eval()
            except Exception as exc:  # pragma: no cover - requires model files
                raise GroundingAdapterUnavailable("model_load_failed") from exc
            self._torch = torch
            self._processor = processor
            self._model = model
            self._device = device
            self._dtype = dtype

    def detect(
        self,
        image: Image.Image,
        query: str,
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> Sequence[Mapping[str, Any]]:
        del box_threshold, text_threshold
        prompt = str(query or "").strip().lower()
        if not prompt:
            return []
        if _contains_cjk(prompt):
            raise GroundingAdapterUnavailable("query_translation_required")
        self._load()
        task_prompt = OPEN_VOCABULARY_TASK + prompt
        with self._infer_lock:
            inputs = self._processor(
                text=task_prompt,
                images=image.convert("RGB"),
                return_tensors="pt",
            )
            prepared = {}
            for key, value in inputs.items():
                if not hasattr(value, "to"):
                    prepared[key] = value
                elif key == "pixel_values":
                    prepared[key] = value.to(self._device, self._dtype)
                else:
                    prepared[key] = value.to(self._device)
            with self._torch.no_grad():
                generated_ids = self._model.generate(
                    **prepared,
                    max_new_tokens=256,
                    num_beams=3,
                    do_sample=False,
                )
            generated_text = self._processor.batch_decode(
                generated_ids,
                skip_special_tokens=False,
            )[0]
            parsed = self._processor.post_process_generation(
                generated_text,
                task=OPEN_VOCABULARY_TASK,
                image_size=image.size,
            )
        answer = parsed.get(OPEN_VOCABULARY_TASK) or {}
        boxes = answer.get("bboxes") or []
        labels = answer.get("bboxes_labels") or answer.get("labels") or []
        candidates = []
        for index, box in enumerate(boxes):
            label = labels[index] if index < len(labels) else prompt
            candidates.append({
                "bbox_xyxy": [float(item) for item in box],
                "confidence": 1.0,
                "label": str(label or prompt),
            })
        return candidates
