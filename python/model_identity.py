"""Thin, evidence-gated model identity resolution for Product Atelier.

Provider model ids are immutable execution facts.  PA canonical identities are
stable join keys, not guesses about an upstream vendor model.  Two raw ids may
share a canonical identity only after an explicit provider declaration or a
formal equivalence receipt is added to VERIFIED_EQUIVALENCES.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote


MODEL_IDENTITY_SCHEMA_VERSION = "pa-model-identity-v1"
MODEL_IDENTITY_REVISION = "2026-09-24.1"
LK_PROVIDER = "lk-ai-model-center"
CATALOG_IMAGE_SOURCE = "v1/media/models?type=image"

# Historical ids remain exact receipt/task facts.  Listing them here makes them
# resolvable without rewriting any ledger row or treating them as Catalog ids.
LEGACY_EXECUTION_IDS = (
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
    "gpt-image-2",
)

# Empty by design for v1.  A future entry must cite explicit provider metadata
# or a formal equivalence receipt; similarity, parameter shape and adapter
# family are insufficient.
VERIFIED_EQUIVALENCES: tuple[dict[str, Any], ...] = ()

UNRESOLVED_RELATIONS = (
    {
        "left_provider_model_id": "gpt-image-2",
        "right_provider_model_id": "tt-image-2",
        "kind": "legacy-to-current-candidate",
        "reason": (
            "The current Catalog exposes only tt-image-2 and owned_by=lingkeai; "
            "it contains no alias, upstream_model_id or canonical_model_id field. "
            "A pricing channel label containing gpt-image-2 and PA adapter-family "
            "compatibility are not identity evidence."
        ),
    },
    {
        "left_provider_model_id": "gemini-3.1-flash-image-preview",
        "right_provider_model_id": "banana-2",
        "kind": "legacy-to-current-candidate",
        "reason": (
            "The Catalog uses the Nano Banana 2 marketing family but does not "
            "declare the legacy Gemini id as an alias or upstream identity."
        ),
    },
    {
        "left_provider_model_id": "gemini-3-pro-image-preview",
        "right_provider_model_id": "banana-pro",
        "kind": "legacy-to-current-candidate",
        "reason": (
            "The Catalog uses the Nano Banana Pro marketing family but does not "
            "declare the legacy Gemini id as an alias or upstream identity."
        ),
    },
    {
        "left_provider_model_id": "tt-image-2",
        "right_provider_model_id": "tt-image-2-token",
        "kind": "provider-variant-candidate",
        "reason": (
            "The provider labels one id as Token/official routing, but does not "
            "publish a canonical upstream id proving output-model equivalence."
        ),
    },
    {
        "left_provider_model_id": "banana-2",
        "right_provider_model_id": "banana-2-token",
        "kind": "provider-variant-candidate",
        "reason": (
            "The ids share a marketing family but expose different reference "
            "limits and billing routes; no explicit equivalence field is present."
        ),
    },
    {
        "left_provider_model_id": "banana-pro",
        "right_provider_model_id": "banana-pro-token",
        "kind": "provider-variant-candidate",
        "reason": (
            "The ids share a marketing family but expose different reference "
            "limits and billing routes; no explicit equivalence field is present."
        ),
    },
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def canonical_model_id(provider: str, provider_model_id: str) -> str:
    """Create a PA join key without changing or interpreting the raw id."""
    provider_key = quote(str(provider or "").strip(), safe="._-")
    model_key = quote(str(provider_model_id or "").strip(), safe="._-")
    if not provider_key or not model_key:
        raise ValueError("provider and provider_model_id are required")
    return f"pa:image:{provider_key}:{model_key}"


def _verified_canonical_id(provider: str, provider_model_id: str) -> str | None:
    for relation in VERIFIED_EQUIVALENCES:
        members = relation.get("provider_model_ids")
        if (
            str(relation.get("provider") or "") == provider
            and isinstance(members, (list, tuple))
            and provider_model_id in members
        ):
            return str(relation.get("canonical_model_id") or "").strip() or None
    return None


def resolve_model_identity(
    provider_model_id: str,
    *,
    provider: str = LK_PROVIDER,
    catalog_present: bool = True,
    source: str = "catalog",
) -> dict[str, Any]:
    """Resolve one raw id while preserving it verbatim for receipts and traces."""
    raw_id = str(provider_model_id or "").strip()
    provider_id = str(provider or "").strip()
    if not raw_id or not provider_id:
        raise ValueError("provider and provider_model_id are required")
    verified = _verified_canonical_id(provider_id, raw_id)
    return {
        "provider": provider_id,
        "provider_model_id": raw_id,
        "canonical_model_id": verified or canonical_model_id(provider_id, raw_id),
        "resolution": "verified-equivalence" if verified else "exact-provider-id",
        "merge_allowed": bool(verified),
        "catalog_present": bool(catalog_present),
        "source": str(source or "catalog"),
        "upstream_identity": {
            "status": "unresolved",
            "provider": None,
            "model_id": None,
        },
    }


def _catalog_image_models(catalog: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    values = catalog.get("models") if isinstance(catalog, Mapping) else []
    if not isinstance(values, list):
        return []
    items = []
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
            items.append(item)
    return sorted(items, key=lambda item: str(item.get("provider_model_id") or ""))


def _parameter_signature(item: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    values = item.get("provider_parameters") if isinstance(item, Mapping) else []
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        options = value.get("options")
        result.append({
            "name": str(value.get("name") or ""),
            "required": value.get("required") is True,
            "option_values": [
                str(option.get("value"))
                for option in options or []
                if isinstance(option, Mapping)
            ],
        })
    return result


def identity_definition() -> dict[str, Any]:
    return {
        "schema_version": MODEL_IDENTITY_SCHEMA_VERSION,
        "revision": MODEL_IDENTITY_REVISION,
        "provider": LK_PROVIDER,
        "legacy_execution_ids": list(LEGACY_EXECUTION_IDS),
        "verified_equivalences": copy.deepcopy(list(VERIFIED_EQUIVALENCES)),
        "unresolved_relations": copy.deepcopy(list(UNRESOLVED_RELATIONS)),
        "merge_rule": (
            "Only explicit provider metadata or a formal equivalence receipt may "
            "place multiple raw ids under one canonical identity."
        ),
    }


def identity_sha256() -> str:
    return _sha256(identity_definition())


def build_model_identity_resolution(
    catalog: Mapping[str, Any] | None,
    *,
    normalized_catalog_sha256: str = "",
    catalog_fetched_at: str = "",
    catalog_status: str = "fresh",
) -> dict[str, Any]:
    catalog_models = _catalog_image_models(catalog)
    by_id = {
        str(item.get("provider_model_id") or ""): item for item in catalog_models
    }
    identities = [
        resolve_model_identity(model_id, catalog_present=True, source="catalog")
        for model_id in sorted(by_id)
    ]
    legacy_identities = [
        resolve_model_identity(
            model_id,
            catalog_present=model_id in by_id,
            source="historical-execution",
        )
        for model_id in LEGACY_EXECUTION_IDS
    ]
    relations = []
    for relation in UNRESOLVED_RELATIONS:
        left_id = str(relation["left_provider_model_id"])
        right_id = str(relation["right_provider_model_id"])
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        relations.append({
            **copy.deepcopy(relation),
            "status": "unresolved",
            "merge_allowed": False,
            "left_canonical_model_id": canonical_model_id(LK_PROVIDER, left_id),
            "right_canonical_model_id": canonical_model_id(LK_PROVIDER, right_id),
            "metadata_check": {
                "left_catalog_present": left is not None,
                "right_catalog_present": right is not None,
                "parameter_signatures_equal": (
                    _parameter_signature(left) == _parameter_signature(right)
                    if left is not None and right is not None
                    else None
                ),
                "explicit_alias_field_present": False,
                "explicit_upstream_identity_present": False,
            },
        })
    return {
        "schema_version": MODEL_IDENTITY_SCHEMA_VERSION,
        "revision": MODEL_IDENTITY_REVISION,
        "identity_sha256": identity_sha256(),
        "catalog_binding": {
            "normalized_catalog_sha256": (
                str(normalized_catalog_sha256 or "").strip().lower() or None
            ),
            "fetched_at": str(catalog_fetched_at or "") or None,
            "status": str(catalog_status or "unavailable"),
            "stale": str(catalog_status or "").lower() == "stale",
            "image_model_count": len(catalog_models),
        },
        "summary": {
            "catalog_identities": len(identities),
            "legacy_execution_identities": len(legacy_identities),
            "verified_alias_groups": len(VERIFIED_EQUIVALENCES),
            "unresolved_relations": len(relations),
        },
        "identities": identities,
        "legacy_execution_identities": legacy_identities,
        "relations": relations,
    }


def attach_identity_to_capability_overlay(
    capability_overlay: Mapping[str, Any],
    identity_resolution: Mapping[str, Any],
) -> dict[str, Any]:
    """Project raw Overlay entries through canonical ids without rewriting them."""
    payload = copy.deepcopy(dict(capability_overlay or {}))
    lookup: dict[str, dict[str, Any]] = {}
    for key in ("identities", "legacy_execution_identities"):
        values = identity_resolution.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, Mapping):
                lookup[str(item.get("provider_model_id") or "")] = dict(item)

    canonical: dict[str, dict[str, Any]] = {}

    def add(raw_id: str, overlay: Mapping[str, Any], catalog: Any = None) -> None:
        identity = lookup.get(raw_id) or resolve_model_identity(
            raw_id, catalog_present=catalog is not None
        )
        canonical_id = str(identity["canonical_model_id"])
        group = canonical.setdefault(canonical_id, {
            "canonical_model_id": canonical_id,
            "members": [],
            "capability_entries": [],
        })
        group["members"].append({
            "provider": identity["provider"],
            "provider_model_id": raw_id,
            "resolution": identity["resolution"],
            "catalog_present": identity["catalog_present"],
            **({"catalog": copy.deepcopy(catalog)} if catalog is not None else {}),
        })
        group["capability_entries"].append(copy.deepcopy(dict(overlay)))

    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    for item in models:
        if not isinstance(item, dict):
            continue
        catalog = item.get("catalog") if isinstance(item.get("catalog"), Mapping) else {}
        raw_id = str(catalog.get("provider_model_id") or "")
        identity = lookup.get(raw_id) or resolve_model_identity(raw_id)
        item["identity"] = copy.deepcopy(identity)
        add(raw_id, item.get("overlay") or {}, catalog)

    legacy = (
        payload.get("legacy_execution_models")
        if isinstance(payload.get("legacy_execution_models"), list)
        else []
    )
    for item in legacy:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("provider_model_id") or "")
        identity = lookup.get(raw_id) or resolve_model_identity(
            raw_id, catalog_present=False, source="historical-execution"
        )
        item["identity"] = copy.deepcopy(identity)
        add(raw_id, item)

    canonical_models = []
    for canonical_id in sorted(canonical):
        group = canonical[canonical_id]
        entries = group["capability_entries"]
        # Multiple entries can exist only after verified equivalence maps their
        # raw ids to one canonical id. Until then every group has one member.
        statuses = {str(item.get("status") or "blocked") for item in entries}
        group["aggregate"] = {
            "status": next(iter(statuses)) if len(statuses) == 1 else "experimental",
            "evidence_aggregation": (
                "single-raw-id" if len(entries) == 1 else "verified-equivalence-only"
            ),
            "raw_model_ids": [item["provider_model_id"] for item in group["members"]],
        }
        if len(entries) == 1:
            # The canonical projection is the read model for future consumers.
            # Keep the full PA capability judgment available there while the
            # raw Overlay entry remains the traceable source of truth.
            for key in (
                "task_kinds",
                "reference_behavior",
                "tested_output",
                "adapter",
                "governor",
                "evidence_level",
                "last_verified_at",
            ):
                if key in entries[0]:
                    group["aggregate"][key] = copy.deepcopy(entries[0][key])
        canonical_models.append(group)

    payload["identity_resolution"] = {
        "schema_version": identity_resolution.get("schema_version"),
        "revision": identity_resolution.get("revision"),
        "identity_sha256": identity_resolution.get("identity_sha256"),
        "relations": copy.deepcopy(identity_resolution.get("relations") or []),
    }
    payload["canonical_models"] = canonical_models
    payload.setdefault("summary", {})["canonical_models"] = len(canonical_models)
    payload["summary"]["composer_eligible_canonical_model_ids"] = [
        item["canonical_model_id"]
        for item in canonical_models
        if item["aggregate"]["status"] == "stable"
        and any(
            member.get("catalog_present")
            and (member.get("catalog") or {}).get("available_for_this_key") is True
            for member in item["members"]
        )
    ]
    return payload
