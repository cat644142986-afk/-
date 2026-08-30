from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image

try:
    from semantic_grounding import (
        GroundingAdapterUnavailable,
        _contains_cjk,
        _plain_list,
        _plain_number,
    )
except ImportError:  # pragma: no cover - package imports used by tests and tools
    from python.semantic_grounding import (
        GroundingAdapterUnavailable,
        _contains_cjk,
        _plain_list,
        _plain_number,
    )


class TransformersOwlv2Adapter:
    """Lazy local-only OWLv2 adapter for isolated evaluation.

    This module is intentionally not imported by the application server or its
    sidecar build. The candidate remains evaluation-only until the frozen photo
    gates prove that it adds enough independent value to justify integration.
    """

    adapter_id = "transformers-owlv2-local-eval"

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
                from transformers import Owlv2ForObjectDetection, Owlv2Processor
            except Exception as exc:  # pragma: no cover - optional local runtime
                raise GroundingAdapterUnavailable("runtime_missing") from exc

            device = self.requested_device or (
                "cuda" if torch.cuda.is_available() else "cpu"
            )
            try:
                processor = Owlv2Processor.from_pretrained(
                    str(self.model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = Owlv2ForObjectDetection.from_pretrained(
                    str(self.model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = model.to(device).eval()
            except Exception as exc:  # pragma: no cover - requires model files
                raise GroundingAdapterUnavailable("model_load_failed") from exc
            self._torch = torch
            self._processor = processor
            self._model = model
            self._device = device

    def detect(
        self,
        image: Image.Image,
        query: str,
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> Sequence[Mapping[str, Any]]:
        del text_threshold  # OWLv2 exposes one score threshold for text queries.
        prompt = str(query or "").strip().lower()
        if not prompt:
            return []
        if _contains_cjk(prompt):
            raise GroundingAdapterUnavailable("query_translation_required")
        text_labels = [[f"a photo of {prompt}"]]
        self._load()
        with self._infer_lock:
            inputs = self._processor(
                images=image.convert("RGB"),
                text=text_labels,
                return_tensors="pt",
            )
            inputs = {
                key: value.to(self._device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
            with self._torch.no_grad():
                outputs = self._model(**inputs)
            result = self._processor.post_process_grounded_object_detection(
                outputs=outputs,
                threshold=box_threshold,
                target_sizes=[image.size[::-1]],
                text_labels=text_labels,
            )[0]
        candidates: list[dict[str, Any]] = []
        labels = result.get("text_labels") or result.get("labels", [])
        for box, score, label in zip(
            result.get("boxes", []),
            result.get("scores", []),
            labels,
        ):
            candidates.append({
                "bbox_xyxy": _plain_list(box),
                "confidence": _plain_number(score),
                "label": str(label or prompt),
            })
        return candidates
