# -*- coding: utf-8 -*-
"""Strict, engine-facing contract for Product Atelier spatial canvas scenes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any


SPATIAL_SCENE_SCHEMA_VERSION = 1
SPATIAL_CUSTOM_DATA_FIELDS = (
    "asset_id",
    "result_id",
    "task_id",
    "product_profile_version_id",
    "lineage_parent_id",
)

_ELEMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ELEMENT_TYPE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REFERENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_ABSOLUTE_PATH = re.compile(
    r"^(?:[A-Za-z]:[\\/]|file:/{2,}|/(?:Users|home|var|tmp|private|Volumes)/)",
    re.IGNORECASE,
)
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MAX_SCENE_BYTES = 8 * 1024 * 1024
_MAX_ELEMENTS = 5000

DEFAULT_SPATIAL_APP_STATE = {
    "viewBackgroundColor": "#d4d0cb",
    "currentItemRoughness": 0,
    "currentItemStrokeStyle": "solid",
    "currentItemFillStyle": "solid",
    "gridSize": 20,
    "gridStep": 5,
    "gridModeEnabled": False,
    "zoom": {"value": 1},
    "scrollX": 0,
    "scrollY": 0,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _finite_number(value: Any, label: str, *, minimum: float | None = None,
                   maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} is below its minimum")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return number


def _safe_string(value: str, label: str, *, maximum: int = 100000) -> str:
    if len(value) > maximum:
        raise ValueError(f"{label} is too long")
    lowered = value.strip().lower()
    if lowered.startswith("data:") or "base64," in lowered:
        raise ValueError(f"{label} cannot contain embedded data")
    if _ABSOLUTE_PATH.match(value.strip()):
        raise ValueError(f"{label} cannot contain an absolute path")
    return value


def _safe_json_value(value: Any, label: str, *, depth: int = 0) -> Any:
    if depth > 32:
        raise ValueError(f"{label} is nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        _finite_number(value, label)
        return value
    if isinstance(value, str):
        return _safe_string(value, label)
    if isinstance(value, list):
        return [
            _safe_json_value(item, f"{label}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{label} keys must be non-empty strings")
            normalized[key] = _safe_json_value(
                item, f"{label}.{key}", depth=depth + 1
            )
        return normalized
    raise ValueError(f"{label} contains an unsupported JSON value")


def _normalize_custom_data(value: Any, label: str) -> dict[str, str | None]:
    if value is None:
        return {field: None for field in SPATIAL_CUSTOM_DATA_FIELDS}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - set(SPATIAL_CUSTOM_DATA_FIELDS))
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(unknown)}")
    normalized: dict[str, str | None] = {}
    for field in SPATIAL_CUSTOM_DATA_FIELDS:
        candidate = value.get(field)
        if candidate in (None, ""):
            normalized[field] = None
            continue
        if not isinstance(candidate, str) or not _REFERENCE_ID.fullmatch(candidate):
            raise ValueError(f"{label}.{field} is not a safe business reference")
        normalized[field] = candidate
    return normalized


def _normalize_app_state(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("SpatialScene.app_state must be an object")
    state = dict(DEFAULT_SPATIAL_APP_STATE)
    for field in (
        "viewBackgroundColor",
        "currentItemRoughness",
        "currentItemStrokeStyle",
        "currentItemFillStyle",
        "gridSize",
        "gridStep",
        "gridModeEnabled",
        "zoom",
        "scrollX",
        "scrollY",
    ):
        if field in value:
            state[field] = value[field]

    if not isinstance(state["viewBackgroundColor"], str) or not _COLOR.fullmatch(
        state["viewBackgroundColor"]
    ):
        raise ValueError("SpatialScene.app_state.viewBackgroundColor is invalid")
    roughness = state["currentItemRoughness"]
    if isinstance(roughness, bool) or not isinstance(roughness, int) or roughness not in {0, 1, 2}:
        raise ValueError("SpatialScene.app_state.currentItemRoughness is invalid")
    for field in ("currentItemStrokeStyle", "currentItemFillStyle"):
        if not isinstance(state[field], str) or not 1 <= len(state[field]) <= 40:
            raise ValueError(f"SpatialScene.app_state.{field} is invalid")
    for field in ("gridSize", "gridStep"):
        if state[field] is not None:
            if isinstance(state[field], bool) or not isinstance(state[field], int):
                raise ValueError(f"SpatialScene.app_state.{field} must be an integer or null")
            if not 1 <= state[field] <= 512:
                raise ValueError(f"SpatialScene.app_state.{field} is out of range")
    if not isinstance(state["gridModeEnabled"], bool):
        raise ValueError("SpatialScene.app_state.gridModeEnabled must be a boolean")
    zoom = state["zoom"]
    if not isinstance(zoom, Mapping) or set(zoom) != {"value"}:
        raise ValueError("SpatialScene.app_state.zoom must contain only value")
    state["zoom"] = {
        "value": _finite_number(
            zoom["value"], "SpatialScene.app_state.zoom.value", minimum=0.01, maximum=30
        )
    }
    for field in ("scrollX", "scrollY"):
        state[field] = _finite_number(
            state[field], f"SpatialScene.app_state.{field}", minimum=-1e9, maximum=1e9
        )
    return json.loads(_canonical_json(state))


def empty_spatial_scene() -> dict[str, Any]:
    return {
        "schema_version": SPATIAL_SCENE_SCHEMA_VERSION,
        "elements": [],
        "app_state": json.loads(_canonical_json(DEFAULT_SPATIAL_APP_STATE)),
        "files": {},
    }


def normalize_spatial_scene(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical Excalidraw scene without file bytes or transient app state."""
    if not isinstance(value, Mapping):
        raise ValueError("SpatialScene must be an object")
    item = dict(value)
    required = {"schema_version", "elements", "app_state"}
    allowed = required | {"files"}
    missing = sorted(required - set(item))
    unknown = sorted(set(item) - allowed)
    if missing:
        raise ValueError(f"SpatialScene is missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"SpatialScene has unknown fields: {', '.join(unknown)}")
    if item["schema_version"] != SPATIAL_SCENE_SCHEMA_VERSION:
        raise ValueError("SpatialScene.schema_version is unsupported")
    files = item.get("files", {})
    if not isinstance(files, Mapping) or files:
        raise ValueError("SpatialScene.files must remain empty")
    if not isinstance(item["elements"], list) or len(item["elements"]) > _MAX_ELEMENTS:
        raise ValueError(f"SpatialScene.elements must contain at most {_MAX_ELEMENTS} items")

    normalized_elements: list[dict[str, Any]] = []
    element_ids: set[str] = set()
    for index, raw in enumerate(item["elements"]):
        label = f"SpatialScene.elements[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{label} must be an object")
        element = _safe_json_value(raw, label)
        for field in ("id", "type", "x", "y", "width", "height"):
            if field not in element:
                raise ValueError(f"{label} is missing field: {field}")
        if not isinstance(element["id"], str) or not _ELEMENT_ID.fullmatch(element["id"]):
            raise ValueError(f"{label}.id is invalid")
        if element["id"] in element_ids:
            raise ValueError(f"duplicate spatial element id: {element['id']}")
        element_ids.add(element["id"])
        if not isinstance(element["type"], str) or not _ELEMENT_TYPE.fullmatch(element["type"]):
            raise ValueError(f"{label}.type is invalid")
        for field in ("x", "y"):
            _finite_number(element[field], f"{label}.{field}", minimum=-1e9, maximum=1e9)
        for field in ("width", "height"):
            _finite_number(element[field], f"{label}.{field}", minimum=0, maximum=1e9)
        if element.get("boundElements") is None:
            element["boundElements"] = []
        if "customData" in element:
            element["customData"] = _normalize_custom_data(
                element["customData"], f"{label}.customData"
            )
        normalized_elements.append(element)

    normalized = {
        "schema_version": SPATIAL_SCENE_SCHEMA_VERSION,
        "elements": normalized_elements,
        "app_state": _normalize_app_state(item["app_state"]),
        "files": {},
    }
    encoded = _canonical_json(normalized).encode("utf-8")
    if len(encoded) > _MAX_SCENE_BYTES:
        raise ValueError("SpatialScene exceeds the 8 MiB metadata limit")
    return json.loads(encoded)


def spatial_scene_sha256(scene: Mapping[str, Any]) -> str:
    normalized = normalize_spatial_scene(scene)
    return hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()


def spatial_scene_references(scene: Mapping[str, Any]) -> list[dict[str, str]]:
    normalized = normalize_spatial_scene(scene)
    references: list[dict[str, str]] = []
    kind_for_field = {
        "asset_id": "asset",
        "result_id": "result",
        "task_id": "task",
        "product_profile_version_id": "product_profile_version",
        "lineage_parent_id": "lineage_parent",
    }
    for element in normalized["elements"]:
        custom_data = element.get("customData") or {}
        for field, kind in kind_for_field.items():
            ref_id = custom_data.get(field)
            if ref_id:
                references.append({
                    "element_id": str(element["id"]),
                    "ref_kind": kind,
                    "ref_id": str(ref_id),
                })
    return references


def spatial_scene_thumbnail(scene: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_spatial_scene(scene)
    visible = [
        element for element in normalized["elements"] if not element.get("isDeleted", False)
    ]
    geometry = [
        {
            "id": str(element["id"]),
            "type": str(element["type"]),
            "x": element["x"],
            "y": element["y"],
            "width": element["width"],
            "height": element["height"],
        }
        for element in visible[-12:]
    ]
    return {
        "element_count": len(visible),
        "image_count": sum(element["type"] == "image" for element in visible),
        "video_count": sum(
            element["type"] == "embeddable"
            and (element.get("customData") or {}).get("asset_id") is not None
            for element in visible
        ),
        "frame_count": sum(element["type"] == "frame" for element in visible),
        "elements": geometry,
    }
