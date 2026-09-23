"""Composer admission and validation planning for image model identities.

This layer is deliberately read-only. It combines current Provider Catalog
route facts with PA canonical identity, adapter, and task evidence without
changing the Overlay, routing policy, Composer UI, or historical receipts.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


ADMISSION_SCHEMA_VERSION = "pa-composer-admission-v1"
ADMISSION_REVISION = "2026-09-24.1"
FOCUS_CANDIDATE_IDS = ("tt-image-2", "banana-2", "banana-pro")
ADMISSION_CATEGORIES = (
    "eligible",
    "needs_identity",
    "needs_adapter",
    "needs_offline_gate",
    "needs_canary",
    "legacy_only",
)
TARGET_TASK_KIND = "reference-generate"
REQUIRED_EVIDENCE_LEVEL = "provider-verified"
EVIDENCE_RANK = {
    "none": 0,
    "catalog-only": 1,
    "adapter-contract": 2,
    "packaged-offline": 3,
    "provider-verified": 4,
}
RESOLVED_IDENTITY_KINDS = frozenset({"exact-provider-id", "verified-equivalence"})
ADAPTER_READY_STATUSES = frozenset({
    "request-shape-checked",
    "provider-verified",
    "provider-verified-bounded-path",
})


VALIDATION_PLANS: dict[str, dict[str, Any]] = {
    "tt-image-2": {
        "offline_actions": [
            "Retain the existing gpt-image-2 adapter-family request compiler for this exact Catalog id.",
            "Keep exact reference binding, size/quality mapping, Governor compilation, max_attempts=1 and receipt fixtures green.",
        ],
        "offline_can_complete": [
            "adapter contract",
            "exact input binding",
            "size and quality request mapping",
            "Governor hard-constraint compilation",
        ],
        "offline_cannot_complete": [
            "prove that the current tt-image-2 route accepts the compiled request and returns a usable image",
        ],
        "minimum_canary_task_kind": "reference-generate",
        "minimum_canary_scope": (
            "One explicit single-attempt run using one exact Result reference with "
            "packaging text/logo protection at 1:1 and 2K; retain request, Provider "
            "receipt, actual pixels, Result and lineage evidence. Admission applies "
            "only to reference-generate."
        ),
        "composer_value": "high",
        "recommendation": (
            "Worth admitting first after one exact-id canary because the current route, "
            "adapter and PA workflow are already aligned. Legacy gpt-image-2 quality "
            "evidence is context only and is not inherited."
        ),
    },
    "banana-2": {
        "offline_actions": [
            "Add a banana image adapter contract using Catalog-declared images, aspectRatio and imageSize parameters.",
            "Map only PA-supported ratio/resolution choices; omit web_search and thinkingLevel from Composer v1.",
            "Fixture-test exact Asset/Result binding, Governor compilation, response parsing and max_attempts=1 without a Provider call.",
        ],
        "offline_can_complete": [
            "model-specific request mapping",
            "parameter validation",
            "exact input binding",
            "Governor and receipt contract",
        ],
        "offline_cannot_complete": [
            "prove exact-route reference fidelity, actual output dimensions or successful response parsing against live output",
        ],
        "minimum_canary_task_kind": "reference-generate",
        "minimum_canary_scope": (
            "After the offline gate, one explicit single-attempt run using one exact "
            "Result reference with protected packaging text/logo at 1:1 and 2K. "
            "Admission applies only to reference-generate."
        ),
        "composer_value": "high",
        "recommendation": (
            "Worth validating after tt-image-2 as a speed/cost-oriented manual choice, "
            "subject to its own exact-id canary."
        ),
    },
    "banana-pro": {
        "offline_actions": [
            "Add a banana image adapter contract using Catalog-declared images, aspectRatio and imageSize parameters.",
            "Fixture-test 1K/2K/4K mapping, exact Asset/Result binding, Governor compilation, response parsing and max_attempts=1.",
        ],
        "offline_can_complete": [
            "model-specific request mapping",
            "parameter validation",
            "exact input binding",
            "Governor and receipt contract",
        ],
        "offline_cannot_complete": [
            "prove exact-route reference fidelity, actual output dimensions or successful response parsing against live output",
        ],
        "minimum_canary_task_kind": "reference-generate",
        "minimum_canary_scope": (
            "After the offline gate, one explicit single-attempt run using one exact "
            "Result reference with protected packaging text/logo at 1:1 and 2K. "
            "Do not reuse banana-2 evidence; admission applies only to reference-generate."
        ),
        "composer_value": "high",
        "recommendation": (
            "Worth validating as the quality-oriented manual choice, but only after "
            "the shared parameter-family adapter has passed offline gates and with a "
            "separate exact-id canary."
        ),
    },
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def admission_definition() -> dict[str, Any]:
    return {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "revision": ADMISSION_REVISION,
        "focus_candidate_ids": list(FOCUS_CANDIDATE_IDS),
        "categories": list(ADMISSION_CATEGORIES),
        "target_task_kind": TARGET_TASK_KIND,
        "required_evidence_level": REQUIRED_EVIDENCE_LEVEL,
        "resolved_identity_kinds": sorted(RESOLVED_IDENTITY_KINDS),
        "adapter_ready_statuses": sorted(ADAPTER_READY_STATUSES),
        "validation_plans": copy.deepcopy(VALIDATION_PLANS),
        "admission_rule": [
            "current Catalog route exists",
            "canonical identity resolved",
            "adapter contract exists",
            "required task capability evidence exists",
            "model and task status are not blocked",
        ],
    }


def admission_sha256() -> str:
    return _sha256(admission_definition())


def _route_check(catalog: Mapping[str, Any]) -> dict[str, Any]:
    sources = catalog.get("source_catalogs")
    source_values = list(sources) if isinstance(sources, list) else []
    return {
        "passed": (
            bool(catalog)
            and catalog.get("available_for_this_key") is True
            and "v1/media/models?type=image" in source_values
        ),
        "available_for_this_key": catalog.get("available_for_this_key") is True,
        "source_catalogs": copy.deepcopy(source_values),
    }


def _identity_check(identity: Mapping[str, Any]) -> dict[str, Any]:
    resolution = str(identity.get("resolution") or "")
    canonical_id = str(identity.get("canonical_model_id") or "")
    return {
        "passed": bool(canonical_id) and resolution in RESOLVED_IDENTITY_KINDS,
        "canonical_model_id": canonical_id or None,
        "resolution": resolution or None,
        "raw_provider_model_id": str(identity.get("provider_model_id") or "") or None,
    }


def _adapter_check(overlay: Mapping[str, Any]) -> dict[str, Any]:
    adapter = overlay.get("adapter")
    adapter = adapter if isinstance(adapter, Mapping) else {}
    status = str(adapter.get("status") or "")
    return {
        "passed": (
            bool(adapter.get("contract"))
            and bool(adapter.get("version"))
            and adapter.get("provider_parameters_mapped") is True
            and status in ADAPTER_READY_STATUSES
        ),
        "contract": str(adapter.get("contract") or "") or None,
        "version": str(adapter.get("version") or "") or None,
        "status": status or None,
        "provider_parameters_mapped": adapter.get("provider_parameters_mapped") is True,
    }


def _capability_check(
    overlay: Mapping[str, Any], *, task_kind: str, required_evidence_level: str
) -> dict[str, Any]:
    tasks = overlay.get("task_kinds")
    tasks = tasks if isinstance(tasks, Mapping) else {}
    task = tasks.get(task_kind)
    task = task if isinstance(task, Mapping) else {}
    model_status = str(overlay.get("status") or "blocked")
    task_status = str(task.get("status") or "blocked")
    evidence_level = str(task.get("evidence_level") or "none")
    required_rank = EVIDENCE_RANK.get(required_evidence_level, 999)
    actual_rank = EVIDENCE_RANK.get(evidence_level, -1)
    not_blocked = model_status != "blocked" and task_status != "blocked"
    return {
        "passed": not_blocked and actual_rank >= required_rank,
        "not_blocked": not_blocked,
        "model_status": model_status,
        "task_kind": task_kind,
        "task_status": task_status,
        "evidence_level": evidence_level,
        "required_evidence_level": required_evidence_level,
        "reason": str(task.get("reason") or "") or None,
    }


def _category(checks: Mapping[str, Mapping[str, Any]]) -> str:
    if not checks["catalog_route"]["passed"]:
        return "legacy_only"
    if not checks["canonical_identity"]["passed"]:
        return "needs_identity"
    if not checks["adapter_contract"]["passed"]:
        return "needs_adapter"
    capability = checks["task_capability"]
    if not capability["not_blocked"]:
        return "needs_offline_gate"
    evidence = str(capability["evidence_level"])
    if EVIDENCE_RANK.get(evidence, -1) < EVIDENCE_RANK["adapter-contract"]:
        return "needs_offline_gate"
    if not capability["passed"]:
        return "needs_canary"
    return "eligible"


def evaluate_model_admission(
    model: Mapping[str, Any],
    *,
    task_kind: str = TARGET_TASK_KIND,
    required_evidence_level: str = REQUIRED_EVIDENCE_LEVEL,
) -> dict[str, Any]:
    catalog = model.get("catalog")
    catalog = catalog if isinstance(catalog, Mapping) else {}
    overlay = model.get("overlay")
    overlay = overlay if isinstance(overlay, Mapping) else model
    identity = model.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    raw_id = str(
        catalog.get("provider_model_id")
        or model.get("provider_model_id")
        or overlay.get("provider_model_id")
        or ""
    )
    checks = {
        "catalog_route": _route_check(catalog),
        "canonical_identity": _identity_check(identity),
        "adapter_contract": _adapter_check(overlay),
        "task_capability": _capability_check(
            overlay,
            task_kind=task_kind,
            required_evidence_level=required_evidence_level,
        ),
    }
    category = _category(checks)
    return {
        "provider_model_id": raw_id,
        "canonical_model_id": checks["canonical_identity"]["canonical_model_id"],
        "target_task_kind": task_kind,
        "category": category,
        "eligible": category == "eligible",
        "checks": checks,
        "validation_plan": copy.deepcopy(VALIDATION_PLANS.get(raw_id)),
    }


def build_composer_admission(
    capability_projection: Mapping[str, Any],
    *,
    task_kind: str = TARGET_TASK_KIND,
    required_evidence_level: str = REQUIRED_EVIDENCE_LEVEL,
) -> dict[str, Any]:
    models = capability_projection.get("models")
    models = models if isinstance(models, list) else []
    by_id = {
        str((item.get("catalog") or {}).get("provider_model_id") or ""): item
        for item in models
        if isinstance(item, Mapping) and isinstance(item.get("catalog"), Mapping)
    }
    evaluated = [
        evaluate_model_admission(
            by_id.get(model_id, {"provider_model_id": model_id}),
            task_kind=task_kind,
            required_evidence_level=required_evidence_level,
        )
        for model_id in FOCUS_CANDIDATE_IDS
    ]

    legacy_values = capability_projection.get("legacy_execution_models")
    legacy_values = legacy_values if isinstance(legacy_values, list) else []
    legacy_only = []
    for item in legacy_values:
        if not isinstance(item, Mapping):
            continue
        legacy_only.append({
            "provider_model_id": str(item.get("provider_model_id") or ""),
            "canonical_model_id": str(
                ((item.get("identity") or {}).get("canonical_model_id") or "")
            ) or None,
            "category": "legacy_only",
            "reason": "Exact historical execution evidence exists, but no current Catalog image route exists.",
        })

    categories = {category: [] for category in ADMISSION_CATEGORIES}
    for item in evaluated:
        categories[item["category"]].append(item["provider_model_id"])
    categories["legacy_only"].extend(
        item["provider_model_id"] for item in legacy_only
    )
    return {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "revision": ADMISSION_REVISION,
        "admission_sha256": admission_sha256(),
        "source_binding": {
            "overlay_sha256": capability_projection.get("overlay_sha256"),
            "identity_sha256": (
                capability_projection.get("identity_resolution") or {}
            ).get("identity_sha256"),
            "normalized_catalog_sha256": (
                capability_projection.get("catalog_binding") or {}
            ).get("normalized_catalog_sha256"),
        },
        "policy": {
            "target_task_kind": task_kind,
            "required_evidence_level": required_evidence_level,
            "admission_rule": admission_definition()["admission_rule"],
            "task_scoped": True,
            "evidence_is_not_inherited_across_canonical_identities": True,
        },
        "summary": {
            "evaluated_current_models": len(evaluated),
            "categories": categories,
            "eligible_canonical_model_ids": [
                item["canonical_model_id"] for item in evaluated if item["eligible"]
            ],
        },
        "candidates": evaluated,
        "legacy_only": legacy_only,
    }
