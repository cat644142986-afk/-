# -*- coding: utf-8 -*-
"""Deterministic execution-context contract for Prompt Governor G4A.

The governor does not introduce another prompt template.  It freezes the
already-resolved user intent, canvas binding, product profile, approved
knowledge and provider route so preview, queued work and execution share one
auditable context.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


EXECUTION_CONTEXT_CONTRACT_VERSION = "execution-context-v1"
EXECUTION_CONTEXT_POLICY_VERSION = "priority-and-provenance-v1"
EXECUTION_CONTEXT_PRIORITY = (
    "user-intent",
    "canvas-context",
    "product-profile",
    "approved-knowledge",
    "skill",
    "case",
    "provider-adapter",
)


class ExecutionContextError(ValueError):
    """Raised when a frozen context is malformed or has been tampered with."""


class ExecutionContextConflictError(ExecutionContextError):
    """Raised when a submitted preview no longer matches authoritative context."""

    def __init__(self, submitted_sha256: str, current_sha256: str) -> None:
        super().__init__("execution context changed after preview; compile a fresh preview")
        self.submitted_sha256 = submitted_sha256
        self.current_sha256 = current_sha256


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, *, maximum: int = 600) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    return compact[:maximum]


def _reference(value: Any, *, maximum: int = 200) -> str:
    candidate = _text(value, maximum=maximum)
    if not candidate:
        return ""
    lowered = candidate.casefold()
    if (
        lowered.startswith(("data:", "file:", "http:", "https:"))
        or re.match(r"^[a-zA-Z]:[\\/]", candidate)
        or candidate.startswith(("/", "\\\\"))
    ):
        raise ExecutionContextError("execution-context references must not contain paths or URLs")
    return candidate


def _normalized_source(value: Any) -> dict[str, str] | None:
    if isinstance(value, str):
        source_id = _reference(value)
        return {"id": source_id, "title": source_id, "relative_path": ""} if source_id else None
    if not isinstance(value, Mapping):
        return None
    source_id = _reference(value.get("id"))
    if not source_id:
        return None
    relative_path = _text(value.get("relative_path"), maximum=240).replace("\\", "/")
    if (
        relative_path.casefold().startswith(("data:", "file:", "http:", "https:"))
        or re.match(r"^[a-zA-Z]:/", relative_path)
        or relative_path.startswith(("/", "//"))
        or ".." in relative_path.split("/")
    ):
        relative_path = ""
    return {
        "id": source_id,
        "title": _text(value.get("title") or source_id, maximum=180),
        "relative_path": relative_path,
    }


def _normalized_rule(value: Any, *, kind: str) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        text = _text(value.get("text"), maximum=600)
        source = _normalized_source(value.get("source"))
    else:
        text = _text(value, maximum=600)
        source = None
    if not text:
        return None
    identity = {
        "kind": kind,
        "text": text,
        "source_id": source.get("id", "") if source else "",
    }
    item: dict[str, Any] = {
        "id": f"ctx:{_sha256(identity)[:24]}",
        "kind": kind,
        "text": text,
    }
    if source:
        item["source"] = source
    return item


def _normalized_rules(values: Iterable[Any] | None, *, kind: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values or []:
        item = _normalized_rule(value, kind=kind)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            result.append(item)
    return result


def _prompt_rule_payload(item: Mapping[str, Any]) -> dict[str, Any] | str:
    source = item.get("source")
    if isinstance(source, Mapping):
        return {"text": str(item["text"]), "source": dict(source)}
    return str(item["text"])


def execution_context_sha256(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop("context_sha256", None)
    # Binding is lifecycle metadata, not execution identity. A preview promoted to
    # an immutable job snapshot must retain the same context hash.
    payload.pop("binding", None)
    return _sha256(payload)


def build_execution_context(
    *,
    context: Mapping[str, Any] | None,
    knowledge_bundle: Mapping[str, Any] | None,
    mode: str,
    source_asset_ids: Iterable[str] | None = None,
    command_id: str = "",
    canvas_context: Mapping[str, Any] | None = None,
    product_profile_context: Mapping[str, Any] | None = None,
    provider_context: Mapping[str, Any] | None = None,
    binding: str = "preview",
) -> dict[str, Any]:
    raw_context = dict(context or {})
    bundle = dict(knowledge_bundle or {})
    brief = dict(bundle.get("creative_brief") or raw_context.get("brief") or raw_context)
    canvas = dict(canvas_context or {})
    profile = dict(product_profile_context or {})
    provider = dict(provider_context or {})

    intent_locks = _normalized_rules(
        bundle.get("intent_lock_rules") or [], kind="intent-lock"
    )
    positive_rules = _normalized_rules(
        bundle.get("positive_rules") or [], kind="positive-rule"
    )
    negative_rules = _normalized_rules(
        bundle.get("negative_rules") or [], kind="negative-rule"
    )
    sources = [
        source
        for source in (_normalized_source(item) for item in bundle.get("sources") or [])
        if source is not None
    ]
    source_ids: set[str] = set()
    unique_sources: list[dict[str, str]] = []
    for item in sources:
        if item["id"] in source_ids:
            continue
        source_ids.add(item["id"])
        unique_sources.append(item)
    sources = unique_sources

    user_intent = {
        "objective": _text(brief.get("objective") or raw_context.get("objective"), maximum=600),
        "user_request": _text(brief.get("user_request") or raw_context.get("user_request"), maximum=1200),
        "output_kind": _text(brief.get("output_kind") or raw_context.get("output_kind"), maximum=120),
        "output_spec": copy.deepcopy(
            brief.get("output_spec")
            if isinstance(brief.get("output_spec"), Mapping)
            else raw_context.get("output_spec")
            if isinstance(raw_context.get("output_spec"), Mapping)
            else {}
        ),
    }
    product_profile = None
    if profile:
        product_profile = {
            "profile_id": _reference(profile.get("profile_id")),
            "version_id": _reference(profile.get("version_id")),
            "revision": max(0, int(profile.get("revision") or 0)),
            "sku": _text(profile.get("sku"), maximum=120),
            "name": _text(profile.get("name"), maximum=160),
        }
    normalized_source_asset_ids: list[str] = []
    for item in source_asset_ids or []:
        source_asset_id = _reference(item)
        if source_asset_id and source_asset_id not in normalized_source_asset_ids:
            normalized_source_asset_ids.append(source_asset_id)
    normalized_canvas = {
        "document_id": _reference(canvas.get("document_id")),
        "expected_revision": (
            max(0, int(canvas["expected_revision"]))
            if canvas.get("expected_revision") is not None
            else None
        ),
        "operation_id": _reference(canvas.get("operation_id")),
        "source_asset_ids": normalized_source_asset_ids,
    }
    provider_adapter = {
        "model": _text(provider.get("model"), maximum=160),
        "family": _text(provider.get("family"), maximum=120),
        "adapter_version": _text(provider.get("adapter_version"), maximum=120),
        "requested_prompt_version": _text(
            provider.get("requested_prompt_version"), maximum=80
        ),
        "effective_prompt_version": _text(
            provider.get("effective_prompt_version"), maximum=80
        ),
        "route_reason": _text(provider.get("route_reason"), maximum=160),
        "generation_strategy": _text(provider.get("generation_strategy"), maximum=80),
        "material_profile": _text(provider.get("material_profile"), maximum=80),
    }
    conflicts = [
        {
            "field": _text(item.get("field"), maximum=80),
            "winner": _text(item.get("winner"), maximum=80),
            "message": _text(item.get("message"), maximum=300),
        }
        for item in bundle.get("conflicts") or []
        if isinstance(item, Mapping)
    ]
    ignored = [
        {
            "id": _reference(item.get("id")) if item.get("id") else "",
            "reason": _text(item.get("reason"), maximum=120),
            "text": _text(item.get("text"), maximum=300),
        }
        for item in bundle.get("ignored_rules") or []
        if isinstance(item, Mapping)
    ]

    result: dict[str, Any] = {
        "contract_version": EXECUTION_CONTEXT_CONTRACT_VERSION,
        "policy_version": EXECUTION_CONTEXT_POLICY_VERSION,
        "binding": binding if binding in {"preview", "job-snapshot"} else "preview",
        "priority_order": list(EXECUTION_CONTEXT_PRIORITY),
        "workflow": {
            "mode": _text(mode, maximum=80),
            "command_id": _reference(command_id),
        },
        "user_intent": user_intent,
        "canvas_context": normalized_canvas,
        "product_profile": product_profile,
        "approved_context": {
            "intent_locks": intent_locks,
            "positive_rules": positive_rules,
            "negative_rules": negative_rules,
            "sources": sources,
        },
        "provider_adapter": provider_adapter,
        "conflicts": conflicts,
        "ignored": ignored,
        "extensions": {
            "skills": [],
            "cases": [],
            "status": "reserved-not-active",
        },
        "summary": {
            "intent_lock_count": len(intent_locks),
            "positive_rule_count": len(positive_rules),
            "negative_rule_count": len(negative_rules),
            "source_count": len(sources),
            "conflict_count": len(conflicts),
            "ignored_count": len(ignored),
        },
        "prompt_inputs": {
            "creative_brief": copy.deepcopy(bundle.get("creative_brief") or brief),
            "intent_lock_rules": [str(item["text"]) for item in intent_locks],
            "positive_rules": [_prompt_rule_payload(item) for item in positive_rules],
            "negative_rules": [_prompt_rule_payload(item) for item in negative_rules],
            "sources": copy.deepcopy(sources),
            "conflicts": copy.deepcopy(conflicts),
            "ignored_rules": copy.deepcopy(ignored),
            "fallback": bool(bundle.get("fallback", False)),
        },
    }
    result["context_sha256"] = execution_context_sha256(result)
    validate_execution_context(result)
    return result


def validate_execution_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionContextError("execution context must be an object")
    context = copy.deepcopy(dict(value))
    if context.get("contract_version") != EXECUTION_CONTEXT_CONTRACT_VERSION:
        raise ExecutionContextError("execution context contract version is unsupported")
    if context.get("policy_version") != EXECUTION_CONTEXT_POLICY_VERSION:
        raise ExecutionContextError("execution context policy version is unsupported")
    if context.get("priority_order") != list(EXECUTION_CONTEXT_PRIORITY):
        raise ExecutionContextError("execution context priority order is invalid")
    if context.get("binding") not in {"preview", "job-snapshot"}:
        raise ExecutionContextError("execution context binding is invalid")
    expected = execution_context_sha256(context)
    if not re.fullmatch(r"[0-9a-f]{64}", str(context.get("context_sha256") or "")):
        raise ExecutionContextError("execution context hash is invalid")
    if context["context_sha256"] != expected:
        raise ExecutionContextError("execution context hash does not match its content")
    extensions = context.get("extensions")
    if not isinstance(extensions, Mapping):
        raise ExecutionContextError("execution context extensions are invalid")
    if (
        extensions.get("status") != "reserved-not-active"
        or extensions.get("skills") != []
        or extensions.get("cases") != []
    ):
        raise ExecutionContextError("skill and case execution are not active in G4A M1")
    for key in (
        "workflow", "user_intent", "canvas_context", "approved_context",
        "provider_adapter", "summary", "prompt_inputs",
    ):
        if not isinstance(context.get(key), Mapping):
            raise ExecutionContextError(f"execution context {key} is invalid")
    if not isinstance(context.get("conflicts"), list) or not isinstance(
        context.get("ignored"), list
    ):
        raise ExecutionContextError("execution context decisions are invalid")
    approved = context["approved_context"]
    for key in ("intent_locks", "positive_rules", "negative_rules", "sources"):
        if not isinstance(approved.get(key), list):
            raise ExecutionContextError("execution context approved rules are invalid")
    summary = context["summary"]
    expected_counts = {
        "intent_lock_count": len(approved["intent_locks"]),
        "positive_rule_count": len(approved["positive_rules"]),
        "negative_rule_count": len(approved["negative_rules"]),
        "source_count": len(approved["sources"]),
        "conflict_count": len(context["conflicts"]),
        "ignored_count": len(context["ignored"]),
    }
    if any(summary.get(key) != count for key, count in expected_counts.items()):
        raise ExecutionContextError("execution context summary does not match its content")
    prompt_inputs = context["prompt_inputs"]
    if not isinstance(prompt_inputs, Mapping):
        raise ExecutionContextError("execution context prompt inputs are invalid")
    expected_prompt_inputs = {
        "intent_lock_rules": [str(item.get("text") or "") for item in approved["intent_locks"]],
        "positive_rules": [_prompt_rule_payload(item) for item in approved["positive_rules"]],
        "negative_rules": [_prompt_rule_payload(item) for item in approved["negative_rules"]],
        "sources": approved["sources"],
        "conflicts": context["conflicts"],
        "ignored_rules": context["ignored"],
    }
    if any(prompt_inputs.get(key) != expected for key, expected in expected_prompt_inputs.items()):
        raise ExecutionContextError("execution context prompt inputs do not match approved context")
    return context


def knowledge_bundle_from_execution_context(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    context = validate_execution_context(value)
    prompt_inputs = copy.deepcopy(dict(context["prompt_inputs"]))
    prompt_inputs["execution_context"] = {
        "contract_version": context["contract_version"],
        "policy_version": context["policy_version"],
        "binding": context["binding"],
        "context_sha256": context["context_sha256"],
    }
    return prompt_inputs
