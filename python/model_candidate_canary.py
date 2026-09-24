"""Exact-id Provider evidence for the focused image model candidates.

This is an additive, task-scoped projection over the sealed Overlay v1 and
offline candidate adapter evidence. It admits only reference-generate at the
single frozen 1:1/2K fixture; it does not rank quality or infer other tasks.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


PROVIDER_CANARY_SCHEMA_VERSION = "pa-model-candidate-provider-canary-v1"
PROVIDER_CANARY_REVISION = "2026-09-24.1"
FROZEN_FIXTURE_ID = "reference-generate-packaging-logo-v1"
FROZEN_REFERENCE_SHA256 = (
    "2a6afca49fcaf0e1d050ae3447e818db408e94ed781a928a50a9c3083bd2f56f"
)
REPORT_PATH = "docs/reports/pa-model-candidate-provider-canary-v1.json"


def _record(
    model_id: str,
    *,
    adapter_contract: str,
    task_id: str,
    remote_task_id: str,
    provider_elapsed_ms: float,
    result_sha256: str,
) -> dict[str, Any]:
    return {
        "provider_model_id": model_id,
        "adapter_contract": adapter_contract,
        "task_kind": "reference-generate",
        "status": "stable",
        "evidence_level": "provider-verified",
        "fixture": {
            "id": FROZEN_FIXTURE_ID,
            "reference_sha256": FROZEN_REFERENCE_SHA256,
            "ratio": "1:1",
            "resolution": "2k",
        },
        "provider_route": {
            "submit": "/v1/media/generate",
            "poll": "/v1/skills/task-status",
            "exact_model_id": model_id,
        },
        "task_id": task_id,
        "remote_task_id": remote_task_id,
        "provider_elapsed_ms": provider_elapsed_ms,
        "result_main_sha256": result_sha256,
        "last_verified_at": "2026-09-24T09:34:00+08:00",
        "evidence_source": {"kind": "provider-canary", "path": REPORT_PATH},
    }


PROVIDER_CANARY_RECORDS = {
    "tt-image-2": _record(
        "tt-image-2",
        adapter_contract="gpt-image-2",
        task_id="job_f01b7c67bc724a9ba7ed948fb80169a3",
        remote_task_id="148634594",
        provider_elapsed_ms=53825.919,
        result_sha256="e5ef4fedbdf5c2f3bd4288ce11afa13dc46b3748d6f0d09406583f5d79449b92",
    ),
    "banana-2": _record(
        "banana-2",
        adapter_contract="banana-2",
        task_id="job_1c7c16dbcd784646866595fd8f540fb0",
        remote_task_id="148640296",
        provider_elapsed_ms=35204.842,
        result_sha256="89acacbf44cf72aad3c6226b99c6d375146898131588e3ed49efb04386562e16",
    ),
    "banana-pro": _record(
        "banana-pro",
        adapter_contract="banana-pro",
        task_id="job_e668dd6932a6444491efc65d6c9a29f9",
        remote_task_id="148642217",
        provider_elapsed_ms=39957.692,
        result_sha256="c4e7f13e8c264fa24e6a23a6465eacf369d6da4c551b2ee074fa061f906483f8",
    ),
}


# Observed telemetry is intentionally kept outside provider_canary_definition():
# it is useful execution evidence, not an admission input or quality ranking, and
# adding it must not rewrite the sealed canary identity above.
PROVIDER_CANARY_TELEMETRY = {
    "tt-image-2": {
        "channel_group": "XT2",
        "gate_elapsed_seconds": 121.749,
        "billing": {"cost": 0.0359, "unit": "算力", "balance_delta": 0.04},
    },
    "banana-2": {
        "channel_group": "MC-限时特惠",
        "gate_elapsed_seconds": 95.697,
        "billing": {"cost": 0.1256, "unit": "算力", "balance_delta": 0.13},
    },
    "banana-pro": {
        "channel_group": "nano",
        "gate_elapsed_seconds": 85.724,
        "billing": {"cost": 0.1579, "unit": "算力", "balance_delta": 0.15},
    },
}


def provider_canary_telemetry(model_id: str) -> dict[str, Any] | None:
    telemetry = PROVIDER_CANARY_TELEMETRY.get(str(model_id or "").strip())
    if telemetry is None:
        return None
    record = PROVIDER_CANARY_RECORDS.get(str(model_id or "").strip()) or {}
    return {
        **copy.deepcopy(telemetry),
        "provider_elapsed_ms": record.get("provider_elapsed_ms"),
        "evidence_source": copy.deepcopy(record.get("evidence_source")),
        "interpretation": "single-canary telemetry; not a quality or preference ranking",
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def provider_canary_definition() -> dict[str, Any]:
    return {
        "schema_version": PROVIDER_CANARY_SCHEMA_VERSION,
        "revision": PROVIDER_CANARY_REVISION,
        "fixture_id": FROZEN_FIXTURE_ID,
        "fixture_reference_sha256": FROZEN_REFERENCE_SHA256,
        "records": copy.deepcopy(PROVIDER_CANARY_RECORDS),
        "provider_generation_calls": 3,
        "evidence_boundary": (
            "Exact Provider route evidence for reference-generate at 1:1/2K only; "
            "no quality ranking or other task-kind evidence is inferred."
        ),
    }


def provider_canary_sha256() -> str:
    return hashlib.sha256(
        _canonical_json(provider_canary_definition()).encode("utf-8")
    ).hexdigest()


def apply_provider_canary_overlay(
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
        record = PROVIDER_CANARY_RECORDS.get(model_id)
        if record is None:
            continue
        overlay = item.get("overlay") if isinstance(item.get("overlay"), dict) else {}
        adapter = overlay.get("adapter") if isinstance(overlay.get("adapter"), dict) else {}
        if str(adapter.get("contract") or "") != record["adapter_contract"]:
            continue
        task_kinds = copy.deepcopy(overlay.get("task_kinds") or {})
        task_kinds["reference-generate"] = {
            "status": "stable",
            "evidence_level": "provider-verified",
            "reason": (
                "The exact Catalog model id completed the frozen single-Result "
                "reference-generate canary through the current PA adapter and Governor."
            ),
        }
        tested_output = copy.deepcopy(overlay.get("tested_output") or {})
        tested_output.update({
            "pairs": [{"ratio": "1:1", "resolution": "2k"}],
            "ratios": ["1:1"],
            "resolutions": ["2k"],
        })
        evidence_sources = copy.deepcopy(overlay.get("evidence_sources") or [])
        evidence_sources.append(copy.deepcopy(record["evidence_source"]))
        adapter["status"] = "provider-verified"
        overlay.update({
            "status": "stable",
            "task_kinds": task_kinds,
            "reference_behavior": {
                "mode": "single-exact-image",
                "maximum_images": 1,
                "exact_asset_binding": "provider-verified",
                "evidence_level": "provider-verified",
            },
            "tested_output": tested_output,
            "adapter": adapter,
            "governor": {
                "adaptation": "existing-execution-context-and-compiled-prompt",
                "hard_constraint_precedence": "provider-verified",
                "evidence_level": "provider-verified",
            },
            "evidence_level": "provider-verified",
            "last_verified_at": record["last_verified_at"],
            "evidence_sources": evidence_sources,
        })
        item["overlay"] = overlay
        item["provider_canary"] = copy.deepcopy(record)
        applied.append(model_id)
    payload["provider_canary"] = {
        "schema_version": PROVIDER_CANARY_SCHEMA_VERSION,
        "revision": PROVIDER_CANARY_REVISION,
        "sha256": provider_canary_sha256(),
        "applied_model_ids": sorted(applied),
        "provider_generation_calls": 3,
        "task_kind": "reference-generate",
    }
    return payload
