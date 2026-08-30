"""Reproducible, provider-neutral evidence for generation quality baselines.

This module deliberately does not estimate prices.  The current LK media
adapter does not return billing data, so a missing amount must remain visible
instead of being replaced by an invented number.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


GENERATION_TRACE_CONTRACT_VERSION = "generation-baseline-2026-08-30.1"
PROMPT_COMPILER_VERSION = "prompt_v1"
PROVIDER_ADAPTER_VERSION = "lk-media-generate-v1"


_CAPABILITY_CONTRACTS: dict[str, dict[str, Any]] = {
    "gpt-image-2": {
        "family": "gpt-image-2",
        "status": "request-shape-checked",
        "endpoint": "/v1/media/generate",
        "reference_parameter": "params.images[]",
        "output_parameters": ["size", "quality"],
        "response_mode": "async-task",
        "poll_endpoint": "/v1/skills/task-status",
    },
    "gemini-image": {
        "family": "gemini-image",
        "status": "request-shape-checked",
        "endpoint": "/v1/media/generate",
        "reference_parameter": "params.images[]",
        "output_parameters": ["aspectRatio", "imageSize"],
        "response_mode": "async-task",
        "poll_endpoint": "/v1/skills/task-status",
    },
    "generic-image": {
        "family": "generic-image",
        "status": "compatibility-only",
        "endpoint": "/v1/media/generate",
        "reference_parameter": "params.images[]",
        "output_parameters": ["size"],
        "response_mode": "async-task",
        "poll_endpoint": "/v1/skills/task-status",
    },
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prompt_snapshot(
    *,
    base_prompt: str,
    compiled_prompt: str,
    negative_prompt: str,
    knowledge_evidence: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Freeze both sides of knowledge enrichment without changing the prompt."""
    evidence = list(knowledge_evidence or [])
    return {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "prompt_version": PROMPT_COMPILER_VERSION,
        "base_prompt_sha256": hashlib.sha256(str(base_prompt).encode("utf-8")).hexdigest(),
        "compiled_prompt_sha256": hashlib.sha256(
            str(compiled_prompt).encode("utf-8")
        ).hexdigest(),
        "negative_prompt_sha256": hashlib.sha256(
            str(negative_prompt).encode("utf-8")
        ).hexdigest(),
        "knowledge_snapshot_sha256": canonical_sha256(evidence),
        "knowledge_evidence_count": len(evidence),
    }


def capability_contract(model: str, provider_family: str = "") -> dict[str, Any]:
    model_key = str(model or "").strip()
    family = str(provider_family or "").strip()
    if not family:
        if model_key.startswith("gpt-image-2") or model_key == "tt-image-2":
            family = "gpt-image-2"
        elif model_key.startswith("gemini-") and "image" in model_key:
            family = "gemini-image"
        else:
            family = "generic-image"
    contract = dict(_CAPABILITY_CONTRACTS.get(family, _CAPABILITY_CONTRACTS["generic-image"]))
    return {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "adapter_version": PROVIDER_ADAPTER_VERSION,
        "model": model_key,
        **contract,
        "billing_telemetry": "not-exposed-by-current-adapter",
    }


def unavailable_billing_evidence() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "amount": None,
        "currency": None,
        "source": "provider-response-has-no-billing-field",
    }


def summarize_trace_timings(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build an offline baseline summary from immutable trace payloads."""
    stages: list[dict[str, Any]] = []
    billable_calls = 0
    priced_calls = 0
    total_elapsed_ms = 0.0
    for trace in traces:
        output = trace.get("output") if isinstance(trace.get("output"), Mapping) else {}
        elapsed = output.get("elapsed_ms")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            elapsed_value = max(0.0, float(elapsed))
            total_elapsed_ms += elapsed_value
            stages.append({
                "stage": str(trace.get("stage") or ""),
                "status": str(trace.get("status") or ""),
                "elapsed_ms": round(elapsed_value, 3),
            })
        if str(trace.get("stage") or "").startswith(("provider.image.", "vlm.")):
            if str(trace.get("status") or "") != "skipped":
                billable_calls += 1
                billing = output.get("billing") if isinstance(output, Mapping) else None
                if isinstance(billing, Mapping) and isinstance(billing.get("amount"), (int, float)):
                    priced_calls += 1
    return {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "stage_count": len(stages),
        "stages": stages,
        "summed_stage_elapsed_ms": round(total_elapsed_ms, 3),
        "billable_call_count": billable_calls,
        "priced_call_count": priced_calls,
        "cost_baseline_status": "available" if billable_calls and priced_calls == billable_calls else "incomplete",
    }
