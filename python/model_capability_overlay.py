"""Product Atelier image-model capability and evidence overlay.

Provider Catalog records provider claims and dynamic account availability.  This
module records only PA-owned judgements: which task semantics the existing
adapter can safely express, what has actually been verified, and whether a
model is mature enough to surface in PA.  It deliberately contains no price,
balance, default-model, or routing recommendation.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

try:
    from generation_baseline import PROVIDER_ADAPTER_VERSION, capability_contract
except ImportError:  # Allows importing as python.model_capability_overlay in tests.
    from python.generation_baseline import PROVIDER_ADAPTER_VERSION, capability_contract


OVERLAY_SCHEMA_VERSION = "pa-image-capability-overlay-v1"
OVERLAY_REVISION = "2026-09-23.1"
CATALOG_IMAGE_SOURCE = "v1/media/models?type=image"
TASK_KINDS = (
    "text-to-image",
    "edit-current-image",
    "reference-generate",
    "variant",
)
MODEL_STATUSES = frozenset({"stable", "experimental", "candidate", "blocked"})
TASK_STATUSES = frozenset({"stable", "experimental", "candidate", "blocked"})
EVIDENCE_LEVELS = frozenset({
    "provider-verified",
    "packaged-offline",
    "adapter-contract",
    "catalog-only",
    "none",
})

# The source Catalog snapshot inspected for overlay v1.  A later Catalog stays
# usable, but is reported as a different binding and any new model receives the
# conservative catalog-only candidate policy.
CATALOG_V1_BINDING = {
    "provider": "lk-ai-model-center",
    "normalized_catalog_sha256": (
        "486a4b7c07c74e869e4adbaaea264390836b89a66e69147f4958a94e065d7cf8"
    ),
    "fetched_at": "2026-09-23T12:34:30.511+00:00",
    "image_model_count": 19,
}

CATALOG_V1_IMAGE_MODEL_IDS = (
    "banana-2",
    "banana-2-token",
    "banana-pro",
    "banana-pro-token",
    "doubao-seedream-4-5-251128",
    "doubao-seedream-5-0-260128",
    "doubao-seedream-5-0-pro-260628",
    "gk-image-2.0",
    "kling-v3",
    "kling-v3-omni",
    "mj_imagine",
    "qwen-image",
    "tt-image-2",
    "tt-image-2-token",
    "tt-image-2.5",
    "tt-image-2.5-token",
    "vidu-image-2",
    "wan2.6-image",
    "wan2.7-image",
)


def _task(status: str, evidence_level: str, reason: str) -> dict[str, str]:
    if status not in TASK_STATUSES:
        raise ValueError(f"unsupported task status: {status}")
    if evidence_level not in EVIDENCE_LEVELS:
        raise ValueError(f"unsupported evidence level: {evidence_level}")
    return {
        "status": status,
        "evidence_level": evidence_level,
        "reason": reason,
    }


def _blocked_text_to_image() -> dict[str, str]:
    return _task(
        "blocked",
        "none",
        "PA has not closed a durable source-free image task workflow; provider catalog tags are not product evidence",
    )


def _generic_candidate(model_id: str) -> dict[str, Any]:
    # Overlay v1 is sealed. New exact-id candidate adapters are projected by
    # model_candidate_validation instead of mutating this historical baseline.
    contract = capability_contract(model_id, "generic-image")
    return {
        "provider_model_id": model_id,
        "status": "candidate",
        "task_kinds": {
            "text-to-image": _blocked_text_to_image(),
            "edit-current-image": _task(
                "candidate", "catalog-only",
                "provider claims image input support, but PA has no model-specific request mapping or receipt",
            ),
            "reference-generate": _task(
                "candidate", "catalog-only",
                "provider claims reference input support, but exact PA binding has not been adapted or verified",
            ),
            "variant": _task(
                "candidate", "catalog-only",
                "the existing Result lineage workflow is reusable only after a model-specific adapter is verified",
            ),
        },
        "reference_behavior": {
            "mode": "unverified",
            "maximum_images": None,
            "exact_asset_binding": "pa-supported-adapter-unverified",
            "evidence_level": "catalog-only",
        },
        "tested_output": {
            "pairs": [],
            "ratios": [],
            "resolutions": [],
        },
        "adapter": {
            "contract": str(contract["family"]),
            "version": PROVIDER_ADAPTER_VERSION,
            "status": "compatibility-only",
            "provider_parameters_mapped": False,
        },
        "governor": {
            "adaptation": "compiled-prompt-core-only",
            "hard_constraint_precedence": "not-provider-verified",
            "evidence_level": "adapter-contract",
        },
        "evidence_level": "catalog-only",
        "last_verified_at": None,
        "evidence_sources": [],
    }


def _tt_image_2_experimental() -> dict[str, Any]:
    contract = capability_contract("tt-image-2")
    return {
        "provider_model_id": "tt-image-2",
        "status": "experimental",
        "task_kinds": {
            "text-to-image": _blocked_text_to_image(),
            "edit-current-image": _task(
                "experimental", "adapter-contract",
                "the dedicated GPT Image 2 request shape compiles, but the exact tt-image-2 id has no PA provider receipt",
            ),
            "reference-generate": _task(
                "experimental", "adapter-contract",
                "exact reference binding is supported by PA, but this provider model id has only offline contract evidence",
            ),
            "variant": _task(
                "experimental", "adapter-contract",
                "the stable Result workflow is reusable, but evidence for gpt-image-2 must not be inherited across ids",
            ),
        },
        "reference_behavior": {
            "mode": "single-exact-image",
            "maximum_images": 1,
            "exact_asset_binding": "supported",
            "evidence_level": "adapter-contract",
        },
        "tested_output": {
            "pairs": [],
            "ratios": [],
            "resolutions": [],
        },
        "adapter": {
            "contract": str(contract["family"]),
            "version": PROVIDER_ADAPTER_VERSION,
            "status": "request-shape-checked",
            "provider_parameters_mapped": True,
        },
        "governor": {
            "adaptation": "existing-execution-context-and-compiled-prompt",
            "hard_constraint_precedence": "contract-checked",
            "evidence_level": "adapter-contract",
        },
        "evidence_level": "adapter-contract",
        "last_verified_at": None,
        "evidence_sources": [],
    }


def _legacy_gpt_image_2() -> dict[str, Any]:
    contract = capability_contract("gpt-image-2")
    return {
        "provider_model_id": "gpt-image-2",
        "status": "stable",
        "catalog_presence": "legacy-execution-id-not-in-catalog-v1",
        "task_kinds": {
            "text-to-image": _blocked_text_to_image(),
            "edit-current-image": _task(
                "stable", "provider-verified",
                "bounded paid A/B runs verified exact source-image editing and protected packaging cases",
            ),
            "reference-generate": _task(
                "experimental", "packaged-offline",
                "Creative Workflow reference binding passed packaged Gate without an additional provider call",
            ),
            "variant": _task(
                "stable", "provider-verified",
                "the Result-to-variant provider stage and combined downstream recovery evidence were validated",
            ),
        },
        "reference_behavior": {
            "mode": "single-exact-image",
            "maximum_images": 1,
            "exact_asset_binding": "provider-verified",
            "evidence_level": "provider-verified",
        },
        "tested_output": {
            "pairs": [{
                "ratio": "1:1",
                "resolution": "2k",
                "actual_pixels": "2048x2048",
                "evidence_level": "provider-verified",
            }],
            "ratios": ["1:1"],
            "resolutions": ["2k"],
        },
        "adapter": {
            "contract": str(contract["family"]),
            "version": PROVIDER_ADAPTER_VERSION,
            "status": "provider-verified",
            "provider_parameters_mapped": True,
        },
        "governor": {
            "adaptation": "existing-execution-context-and-compiled-prompt",
            "hard_constraint_precedence": "provider-verified",
            "evidence_level": "provider-verified",
        },
        "evidence_level": "provider-verified",
        "last_verified_at": "2026-09-19T09:48:09.991+00:00",
        "evidence_sources": [
            {
                "kind": "tracked-paid-ab",
                "path": "docs/reports/generation-strategy-paid-ab-2026-08-31.json",
                "sha256": "2aeb1e59817a8c2004de7f10e8cd877f144273818aafe9c235eb50ae830e32f2",
            },
            {
                "kind": "tracked-paid-ab",
                "path": "docs/reports/generation-prompt-paid-ab-2026-09-01.json",
                "sha256": "bb1e85ab4ea8f8d0500dd150530485cd031e041122adb1f3a9d39342942ad0e6",
            },
            {
                "kind": "provider-stage-completed-downstream-combination-validated",
                "path": "build/g4b-result-variation-provider-canary-20260919-174654/provider-canary-receipt.json",
                "sha256": "dde745147c58d7fb19378f0fa540140d25dcb4c9c4d6193ef49cbdaabfca6a15",
                "boundary": "the receipt status reflects a later harness interruption; its single provider stage completed",
            },
        ],
    }


def _legacy_gemini_flash() -> dict[str, Any]:
    contract = capability_contract("gemini-3.1-flash-image-preview")
    return {
        "provider_model_id": "gemini-3.1-flash-image-preview",
        "status": "experimental",
        "catalog_presence": "legacy-execution-id-not-in-catalog-v1",
        "task_kinds": {
            "text-to-image": _blocked_text_to_image(),
            "edit-current-image": _task(
                "experimental", "provider-verified",
                "one bounded outpaint canary verified the exact model id, not the general Canvas modification workflow",
            ),
            "reference-generate": _task(
                "candidate", "adapter-contract",
                "reference request shape exists, but the Creative Workflow has no exact-model provider receipt",
            ),
            "variant": _task(
                "candidate", "adapter-contract",
                "Result lineage is reusable, but the exact model has no Result-variant provider evidence",
            ),
        },
        "reference_behavior": {
            "mode": "single-exact-image",
            "maximum_images": 1,
            "exact_asset_binding": "verified-for-bounded-outpaint-only",
            "evidence_level": "provider-verified",
        },
        "tested_output": {
            "pairs": [{
                "ratio": "4:3",
                "resolution": "2k",
                "actual_pixels": "2400x1792",
                "evidence_level": "provider-verified",
            }],
            "ratios": ["4:3"],
            "resolutions": ["2k"],
        },
        "adapter": {
            "contract": str(contract["family"]),
            "version": PROVIDER_ADAPTER_VERSION,
            "status": "provider-verified-bounded-path",
            "provider_parameters_mapped": True,
        },
        "governor": {
            "adaptation": "compiled-prompt-core-only",
            "hard_constraint_precedence": "not-verified-on-current-canvas-workflow",
            "evidence_level": "adapter-contract",
        },
        "evidence_level": "provider-verified",
        "last_verified_at": "2026-09-09T10:38:26.635968+00:00",
        "evidence_sources": [{
            "kind": "validated-live-provider-canary",
            "path": "build/pwc3c-live-canary-20260909-1836/pwc3c-live-provider-canary.json",
            "sha256": "b42efcb93dfd3231ec1052074ce5732a9511b39d6b6d95b717e4a3c384012555",
        }],
    }


LEGACY_EXECUTION_OVERLAYS = {
    "gpt-image-2": _legacy_gpt_image_2(),
    "gemini-3.1-flash-image-preview": _legacy_gemini_flash(),
}


def _validate_overlay(entry: Mapping[str, Any]) -> None:
    if str(entry.get("status") or "") not in MODEL_STATUSES:
        raise ValueError("overlay model status is invalid")
    tasks = entry.get("task_kinds")
    if not isinstance(tasks, Mapping) or tuple(tasks.keys()) != TASK_KINDS:
        raise ValueError("overlay task kinds must use the frozen v1 order")
    for task in tasks.values():
        if not isinstance(task, Mapping):
            raise ValueError("overlay task entry must be an object")
        if str(task.get("status") or "") not in TASK_STATUSES:
            raise ValueError("overlay task status is invalid")
        if str(task.get("evidence_level") or "") not in EVIDENCE_LEVELS:
            raise ValueError("overlay evidence level is invalid")
    if str(entry.get("evidence_level") or "") not in EVIDENCE_LEVELS:
        raise ValueError("overlay evidence level is invalid")


def model_overlay(model_id: str) -> dict[str, Any]:
    """Return the PA-owned overlay for one current or legacy image model id."""
    key = str(model_id or "").strip()
    if key in LEGACY_EXECUTION_OVERLAYS:
        item = copy.deepcopy(LEGACY_EXECUTION_OVERLAYS[key])
    elif key == "tt-image-2":
        item = _tt_image_2_experimental()
    else:
        item = _generic_candidate(key)
    _validate_overlay(item)
    return item


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def overlay_definition() -> dict[str, Any]:
    """Return a deterministic PA-only definition, without Catalog facts."""
    definition = {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "revision": OVERLAY_REVISION,
        "task_kinds": list(TASK_KINDS),
        "catalog_v1_binding": copy.deepcopy(CATALOG_V1_BINDING),
        "catalog_v1_models": [
            model_overlay(model_id) for model_id in CATALOG_V1_IMAGE_MODEL_IDS
        ],
        "legacy_execution_models": [
            model_overlay(model_id) for model_id in sorted(LEGACY_EXECUTION_OVERLAYS)
        ],
    }
    return definition


def overlay_sha256() -> str:
    return _canonical_sha256(overlay_definition())


def _catalog_image_models(catalog: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    values = catalog.get("models") if isinstance(catalog, Mapping) else []
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        sources = item.get("source_catalogs")
        modalities = item.get("modalities")
        if (
            isinstance(sources, list) and CATALOG_IMAGE_SOURCE in sources
        ) or (
            isinstance(modalities, list) and "image" in modalities
        ):
            result.append(item)
    return sorted(result, key=lambda item: str(item.get("provider_model_id") or ""))


def _catalog_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep dynamic provider facts visibly outside the PA overlay."""
    return {
        "provider_model_id": str(item.get("provider_model_id") or ""),
        "display_name": str(item.get("display_name") or item.get("provider_model_id") or ""),
        "available_for_this_key": item.get("available_for_this_key") is not False,
        "tags": copy.deepcopy(item.get("tags") or []),
        "provider_parameters": copy.deepcopy(item.get("provider_parameters") or []),
        "pricing_available": isinstance(item.get("pricing"), Mapping),
        "source_catalogs": copy.deepcopy(item.get("source_catalogs") or []),
    }


def build_image_capability_overlay(
    catalog: Mapping[str, Any] | None,
    *,
    normalized_catalog_sha256: str = "",
    catalog_fetched_at: str = "",
    catalog_status: str = "fresh",
) -> dict[str, Any]:
    """Join dynamic Catalog facts to the immutable PA evidence overlay."""
    catalog_models = _catalog_image_models(catalog)
    models = []
    for catalog_model in catalog_models:
        model_id = str(catalog_model.get("provider_model_id") or "").strip()
        models.append({
            "catalog": _catalog_projection(catalog_model),
            "overlay": model_overlay(model_id),
        })
    counts = {status: 0 for status in sorted(MODEL_STATUSES)}
    for item in models:
        counts[str(item["overlay"]["status"])] += 1
    current_ids = [str(item["catalog"]["provider_model_id"]) for item in models]
    expected_ids = list(CATALOG_V1_IMAGE_MODEL_IDS)
    normalized_hash = str(normalized_catalog_sha256 or "").strip().lower()
    binding_matches = (
        normalized_hash == str(CATALOG_V1_BINDING["normalized_catalog_sha256"])
        and current_ids == expected_ids
    )
    legacy_models = [
        model_overlay(model_id) for model_id in sorted(LEGACY_EXECUTION_OVERLAYS)
    ]
    return {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "revision": OVERLAY_REVISION,
        "overlay_sha256": overlay_sha256(),
        "catalog_binding": {
            "normalized_catalog_sha256": normalized_hash or None,
            "fetched_at": str(catalog_fetched_at or "") or None,
            "status": str(catalog_status or "unavailable"),
            "stale": str(catalog_status or "").lower() == "stale",
            "matches_overlay_v1_source": binding_matches,
            "expected_image_model_count": CATALOG_V1_BINDING["image_model_count"],
            "actual_image_model_count": len(models),
            "added_model_ids": sorted(set(current_ids) - set(expected_ids)),
            "missing_model_ids": sorted(set(expected_ids) - set(current_ids)),
        },
        "summary": {
            "catalog_image_models": len(models),
            "status_counts": counts,
            "catalog_models_ready_for_composer": [
                item["catalog"]["provider_model_id"]
                for item in models
                if item["overlay"]["status"] == "stable"
            ],
            "legacy_execution_models_ready_for_composer": [
                item["provider_model_id"]
                for item in legacy_models
                if item["status"] == "stable"
            ],
        },
        "models": models,
        "legacy_execution_models": legacy_models,
    }


for _entry in overlay_definition()["catalog_v1_models"]:
    _validate_overlay(_entry)
for _entry in LEGACY_EXECUTION_OVERLAYS.values():
    _validate_overlay(_entry)
