"""Additive offline evidence for focused Composer model candidates.

The sealed Capability Overlay v1 remains unchanged. This projection records
new exact-id adapter evidence produced by Candidate Validation Execution and is
applied before canonical identity aggregation and Composer admission.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

try:
    from generation_baseline import PROVIDER_ADAPTER_VERSION
except ImportError:  # Allows importing as python.model_candidate_validation.
    from python.generation_baseline import PROVIDER_ADAPTER_VERSION


CANDIDATE_VALIDATION_SCHEMA_VERSION = "pa-model-candidate-validation-v1"
CANDIDATE_VALIDATION_REVISION = "2026-09-24.1"
FROZEN_FIXTURE_ID = "reference-generate-packaging-logo-v1"
FROZEN_REFERENCE_SHA256 = (
    "2a6afca49fcaf0e1d050ae3447e818db408e94ed781a928a50a9c3083bd2f56f"
)


def _record(model_id: str) -> dict[str, Any]:
    return {
        "provider_model_id": model_id,
        "adapter": {
            "contract": model_id,
            "version": PROVIDER_ADAPTER_VERSION,
            "status": "request-shape-checked",
            "provider_parameters_mapped": True,
            "request_parameters": ["prompt", "images", "aspectRatio", "imageSize"],
        },
        "task_evidence": {
            "reference-generate": {
                "status": "experimental",
                "evidence_level": "adapter-contract",
                "reason": (
                    "The exact model id passed the frozen Result-reference request, "
                    "Governor, output-parameter, receipt and lineage fixture without "
                    "network access; exact-route Provider behavior still requires one canary."
                ),
            }
        },
        "reference_behavior": {
            "mode": "single-exact-image",
            "maximum_images": 1,
            "exact_asset_binding": "offline-gate-verified",
            "evidence_level": "adapter-contract",
        },
        "governor": {
            "adaptation": "existing-execution-context-and-compiled-prompt",
            "hard_constraint_precedence": "offline-gate-verified",
            "evidence_level": "adapter-contract",
        },
        "tested_output": {
            "pairs": [],
            "ratios": [],
            "resolutions": [],
            "offline_request_pairs": [{
                "ratio": "1:1",
                "resolution": "2k",
                "provider_params": {"aspectRatio": "1:1", "imageSize": "2K"},
            }],
        },
        "fixture": {
            "id": FROZEN_FIXTURE_ID,
            "reference_sha256": FROZEN_REFERENCE_SHA256,
        },
        "last_verified_at": "2026-09-24T00:00:00+08:00",
        "evidence_sources": [{
            "kind": "offline-candidate-adapter-gate",
            "path": "docs/reports/pa-model-candidate-offline-gate-v1.json",
        }],
    }


CANDIDATE_VALIDATION_RECORDS = {
    "banana-2": _record("banana-2"),
    "banana-pro": _record("banana-pro"),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def candidate_validation_definition() -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_VALIDATION_SCHEMA_VERSION,
        "revision": CANDIDATE_VALIDATION_REVISION,
        "fixture_id": FROZEN_FIXTURE_ID,
        "fixture_reference_sha256": FROZEN_REFERENCE_SHA256,
        "records": copy.deepcopy(CANDIDATE_VALIDATION_RECORDS),
        "evidence_boundary": (
            "Exact-id offline adapter evidence only; no Provider behavior, quality "
            "ranking, or other task kind is inferred."
        ),
    }


def candidate_validation_sha256() -> str:
    return hashlib.sha256(
        _canonical_json(candidate_validation_definition()).encode("utf-8")
    ).hexdigest()


def apply_candidate_validation_overlay(
    capability_overlay: Mapping[str, Any],
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(capability_overlay or {}))
    applied = []
    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    for item in models:
        if not isinstance(item, dict):
            continue
        catalog = item.get("catalog") if isinstance(item.get("catalog"), Mapping) else {}
        model_id = str(catalog.get("provider_model_id") or "")
        record = CANDIDATE_VALIDATION_RECORDS.get(model_id)
        if record is None:
            continue
        overlay = item.get("overlay") if isinstance(item.get("overlay"), dict) else {}
        task_kinds = overlay.get("task_kinds")
        task_kinds = copy.deepcopy(task_kinds) if isinstance(task_kinds, Mapping) else {}
        task_kinds.update(copy.deepcopy(record["task_evidence"]))
        overlay.update({
            "status": "experimental",
            "task_kinds": task_kinds,
            "reference_behavior": copy.deepcopy(record["reference_behavior"]),
            "tested_output": copy.deepcopy(record["tested_output"]),
            "adapter": copy.deepcopy(record["adapter"]),
            "governor": copy.deepcopy(record["governor"]),
            "evidence_level": "adapter-contract",
            "last_verified_at": record["last_verified_at"],
            "evidence_sources": copy.deepcopy(record["evidence_sources"]),
        })
        item["overlay"] = overlay
        item["candidate_validation"] = copy.deepcopy(record)
        applied.append(model_id)
    payload["candidate_validation"] = {
        "schema_version": CANDIDATE_VALIDATION_SCHEMA_VERSION,
        "revision": CANDIDATE_VALIDATION_REVISION,
        "sha256": candidate_validation_sha256(),
        "applied_model_ids": sorted(applied),
        "provider_generation_calls": 0,
    }
    return payload
