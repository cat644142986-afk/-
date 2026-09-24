"""Explainable Composer routing over already-admitted image models.

The router is intentionally thin: admission remains the authority for model
capability and exact parameter compatibility.  This module only recommends
one of those admitted routes from bounded operational evidence.  It never
substitutes a model at execution time and never treats a single canary as a
quality comparison.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


SMART_ROUTER_SCHEMA_VERSION = "pa-smart-router-v1"
SMART_ROUTER_REVISION = "2026-09-24.1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def smart_router_policy_definition() -> dict[str, Any]:
    return {
        "schema_version": SMART_ROUTER_SCHEMA_VERSION,
        "revision": SMART_ROUTER_REVISION,
        "scope": {
            "task_kind": "reference-generate",
            "output_ratio": "1:1",
            "output_resolution": "2k",
        },
        "rules": [
            "consider only models already admitted for the exact task and parameters",
            "prefer the lowest comparable observed billed cost",
            "use observed provider latency only to break an equal-cost tie",
            "fall back to the admission default, then stable eligible order, when comparable evidence is absent",
            "preserve an eligible manual selection without substitution",
        ],
        "evidence_boundary": (
            "Cost and latency are exact single-canary operational observations. "
            "They are not model-quality evidence and do not create a quality ranking."
        ),
    }


def smart_router_policy_sha256() -> str:
    return _sha256(smart_router_policy_definition())


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _operational_candidate(model: Mapping[str, Any]) -> dict[str, Any]:
    telemetry = model.get("telemetry")
    telemetry = telemetry if isinstance(telemetry, Mapping) else {}
    billing = telemetry.get("billing")
    billing = billing if isinstance(billing, Mapping) else {}
    return {
        "provider_model_id": str(model.get("provider_model_id") or ""),
        "observed_billing": {
            "cost": _number(billing.get("cost")),
            "unit": str(billing.get("unit") or ""),
        },
        "observed_provider_elapsed_ms": _number(telemetry.get("provider_elapsed_ms")),
        "evidence_source": copy.deepcopy(telemetry.get("evidence_source")),
    }


def build_smart_route(selection: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recommend one admitted route without changing the selected model."""
    payload = selection if isinstance(selection, Mapping) else {}
    eligible_ids = [
        str(value) for value in payload.get("eligible_provider_model_ids", [])
        if str(value or "")
    ]
    if str(payload.get("status") or "") != "ready":
        eligible_ids = []
    models = payload.get("models")
    models = models if isinstance(models, list) else []
    candidates = [
        _operational_candidate(item)
        for item in models
        if isinstance(item, Mapping)
        and str(item.get("provider_model_id") or "") in eligible_ids
    ]

    recommended_id = ""
    reason_codes: list[str] = []
    summary = "当前任务与参数没有已验证模型"
    if candidates:
        units = {
            item["observed_billing"]["unit"]
            for item in candidates
            if item["observed_billing"]["cost"] is not None
            and item["observed_billing"]["unit"]
        }
        priced = [
            item for item in candidates
            if item["observed_billing"]["cost"] is not None
        ]
        comparable = priced if len(units) == 1 and len(priced) == len(candidates) else []
        if comparable:
            selected = min(
                comparable,
                key=lambda item: (
                    item["observed_billing"]["cost"],
                    item["observed_provider_elapsed_ms"]
                    if item["observed_provider_elapsed_ms"] is not None else float("inf"),
                    eligible_ids.index(item["provider_model_id"]),
                ),
            )
            recommended_id = selected["provider_model_id"]
            reason_codes = ["EXACT_CAPABILITY_AND_PARAMETERS", "LOWEST_OBSERVED_COST"]
            same_cost = [
                item for item in comparable
                if item["observed_billing"]["cost"] == selected["observed_billing"]["cost"]
            ]
            if len(same_cost) > 1:
                reason_codes.append("LOWEST_OBSERVED_LATENCY_TIEBREAK")
            fastest = min(
                (
                    item for item in candidates
                    if item["observed_provider_elapsed_ms"] is not None
                ),
                key=lambda item: item["observed_provider_elapsed_ms"],
                default=None,
            )
            cost = selected["observed_billing"]["cost"]
            unit = selected["observed_billing"]["unit"]
            summary = f"参数已验证；按可比的单次实付证据推荐 {recommended_id}（{cost:g} {unit}）"
            if fastest and fastest["provider_model_id"] != recommended_id:
                seconds = fastest["observed_provider_elapsed_ms"] / 1000
                summary += f"；单次最低耗时证据为 {fastest['provider_model_id']}（{seconds:.1f} 秒）"
        else:
            with_latency = [
                item for item in candidates
                if item["observed_provider_elapsed_ms"] is not None
            ]
            if with_latency:
                selected = min(
                    with_latency,
                    key=lambda item: (
                        item["observed_provider_elapsed_ms"],
                        eligible_ids.index(item["provider_model_id"]),
                    ),
                )
                recommended_id = selected["provider_model_id"]
                reason_codes = ["EXACT_CAPABILITY_AND_PARAMETERS", "LOWEST_OBSERVED_LATENCY"]
                summary = f"价格证据不可比；按单次 Provider 耗时证据推荐 {recommended_id}"
            else:
                default_id = str(payload.get("default_provider_model_id") or "")
                recommended_id = default_id if default_id in eligible_ids else eligible_ids[0]
                reason_codes = ["EXACT_CAPABILITY_AND_PARAMETERS", "DETERMINISTIC_ADMISSION_FALLBACK"]
                summary = f"暂无可比运行证据；沿用 Admission 确定顺序推荐 {recommended_id}"

    decision_material = {
        "policy_sha256": smart_router_policy_sha256(),
        "selection_sha256": str(payload.get("selection_sha256") or ""),
        "request": copy.deepcopy(payload.get("request")),
        "eligible_provider_model_ids": eligible_ids,
        "recommended_provider_model_id": recommended_id or None,
        "reason_codes": reason_codes,
        "operational_candidates": candidates,
    }
    return {
        "schema_version": SMART_ROUTER_SCHEMA_VERSION,
        "revision": SMART_ROUTER_REVISION,
        "policy_sha256": smart_router_policy_sha256(),
        "decision_sha256": _sha256(decision_material),
        "status": "ready" if recommended_id else "unsupported",
        "recommended_provider_model_id": recommended_id or None,
        "reason_codes": reason_codes,
        "summary": summary,
        "operational_candidates": candidates,
        "policy": {
            "manual_override_allowed": True,
            "no_execution_fallback": True,
            "no_quality_ranking": True,
        },
        "evidence_boundary": smart_router_policy_definition()["evidence_boundary"],
    }


def attach_smart_route(selection: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = copy.deepcopy(dict(selection or {}))
    payload["routing"] = build_smart_route(payload)
    return payload
