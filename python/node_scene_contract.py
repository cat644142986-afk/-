"""Versioned node projection stored in the existing spatial Canvas ledger.

This is document metadata, not an asset, task or execution store. Media bytes,
runtime URLs, callbacks, selection and credentials never belong in a scene.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

NODE_SCENE_SCHEMA_VERSION = 2
NODE_SCENE_KIND = "pa-node-canvas"
NODE_TYPES = frozenset({"image", "video", "audio", "prompt", "group", "loop", "clip"})
REFERENCE_FIELDS = {
    "asset_id": "asset", "result_id": "result", "task_id": "task",
    "product_profile_version_id": "product_profile_version", "lineage_parent_id": "lineage_parent",
}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|file:|blob:|https?://|/(?:Users|home|var|tmp|private|Volumes)/)", re.I)
_FORBIDDEN = frozenset({"src", "url", "content_url", "thumbnail_url", "previewUrl", "storage_path",
                        "api_key", "apiKey", "token", "access_token", "authorization", "password", "credential"})
_DATA = frozenset({"name", "media", "outputs", "active_output", "draft", "text", "separator",
                   "llm", "plan", "clips", "execution"})
_MEDIA = frozenset({*REFERENCE_FIELDS, "role", "kind", "name", "width", "height", "mime", "sha256", "duration_seconds", "source_element_id"})


def _number(value: Any, label: str, minimum=-1e9, maximum=1e9) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} is out of range")
    return value


def _id(value: Any, label: str, pattern=_ID) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _json(value: Any, label: str, depth=0) -> Any:
    if depth > 24:
        raise ValueError(f"{label} is nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _number(value, label)
    if isinstance(value, str):
        if len(value) > 100000 or "base64," in value.lower() or value.lstrip().lower().startswith("data:") or _PATH.match(value.strip()):
            raise ValueError(f"{label} contains embedded media, runtime URL or a path")
        return value
    if isinstance(value, list):
        return [_json(item, f"{label}[{index}]", depth + 1) for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or key in _FORBIDDEN or key.startswith("on") and len(key) > 2 and key[2].isupper():
                raise ValueError(f"{label} contains a forbidden runtime/credential field")
            result[key] = _json(item, f"{label}.{key}", depth + 1)
        return result
    raise ValueError(f"{label} contains unsupported data")


def _media(value: Any, label: str) -> dict:
    if not isinstance(value, Mapping) or set(value) - _MEDIA:
        raise ValueError(f"{label} must contain only media identity metadata")
    result = _json(value, label)
    _id(result.get("asset_id"), f"{label}.asset_id", _REF)
    if result.get("source_element_id"):
        _id(result["source_element_id"], f"{label}.source_element_id")
    for field in REFERENCE_FIELDS:
        if result.get(field):
            _id(result[field], f"{label}.{field}", _REF)
    if result.get("kind", "image") not in {"image", "video", "audio"}:
        raise ValueError(f"{label}.kind is unsupported")
    if result.get("role") not in {"workspace_source", "result_main", "result_cutout", "result_video", "result_video_cover", "local_derivative"}:
        raise ValueError(f"{label}.role is unsupported")
    return result


def normalize_node_scene(value: Mapping[str, Any]) -> dict:
    required = {"schema_version", "kind", "nodes", "edges", "viewport"}
    if not required <= set(value) or set(value) - required - {"metadata"} or value.get("schema_version") != 2 or value.get("kind") != NODE_SCENE_KIND:
        raise ValueError("NodeScene identity/fields are unsupported")
    if not isinstance(value["nodes"], list) or len(value["nodes"]) > 5000:
        raise ValueError("NodeScene.nodes must contain at most 5000 nodes")
    if not isinstance(value["edges"], list) or len(value["edges"]) > 10000:
        raise ValueError("NodeScene.edges must contain at most 10000 edges")
    nodes, by_id = [], {}
    for raw in value["nodes"]:
        if not isinstance(raw, Mapping) or set(raw) - {"id", "type", "position", "parentId", "style", "data"}:
            raise ValueError("NodeScene node has unsupported fields")
        node_id = _id(raw.get("id"), "node.id")
        if node_id in by_id or raw.get("type") not in NODE_TYPES:
            raise ValueError("NodeScene contains duplicate id or unsupported node type")
        position, style, data = raw.get("position"), raw.get("style"), raw.get("data", {})
        if not isinstance(position, Mapping) or set(position) != {"x", "y"}:
            raise ValueError("node.position requires x/y")
        if not isinstance(style, Mapping) or set(style) != {"width", "height"}:
            raise ValueError("node.style requires width/height")
        if not isinstance(data, Mapping) or set(data) - _DATA:
            raise ValueError("node.data has unsupported fields")
        cleaned = _json(data, "node.data")
        if cleaned.get("media") is not None:
            cleaned["media"] = _media(cleaned["media"], "node.media")
        if "outputs" in cleaned:
            if not isinstance(cleaned["outputs"], list) or len(cleaned["outputs"]) > 100:
                raise ValueError("node.outputs must be a bounded list")
            cleaned["outputs"] = [_media(item, "node.outputs") for item in cleaned["outputs"]]
        draft = cleaned.get("draft", {})
        if not isinstance(draft, Mapping) or not isinstance(draft.get("references", []), list):
            raise ValueError("node.draft references must be a list")
        for ref in draft.get("references", []):
            _media(ref, "draft.reference")
        if "execution" in cleaned:
            if not isinstance(cleaned["execution"], Mapping):
                raise ValueError("node.execution must be an object")
            if cleaned["execution"].get("task_id"):
                _id(cleaned["execution"]["task_id"], "execution.task_id", _REF)
            tasks = cleaned["execution"].get("tasks", {})
            if not isinstance(tasks, Mapping) or len(tasks) > 500:
                raise ValueError("execution.tasks must be a bounded Task projection map")
            for task_id, projection in tasks.items():
                _id(task_id, "execution.tasks.id", _REF)
                if not isinstance(projection, Mapping) or projection.get("task_id") != task_id:
                    raise ValueError("execution Task projection identity does not match")
        node = {"id": node_id, "type": raw["type"],
                "position": {axis: _number(position[axis], "node.position") for axis in ("x", "y")},
                "style": {axis: _number(style[axis], "node.style", 1, 1e6) for axis in ("width", "height")}, "data": cleaned}
        if raw.get("parentId"):
            node["parentId"] = _id(raw["parentId"], "node.parentId")
        nodes.append(node)
        by_id[node_id] = node
    for node in nodes:
        visited, parent = {node["id"]}, node.get("parentId")
        while parent:
            if parent in visited or parent not in by_id or by_id[parent]["type"] != "group":
                raise ValueError("NodeScene parent is missing, not a group, or cyclic")
            visited.add(parent)
            parent = by_id[parent].get("parentId")
    edges, edge_ids, pairs, adjacency = [], set(), set(), {key: [] for key in by_id}
    for raw in value["edges"]:
        if not isinstance(raw, Mapping) or set(raw) - {"id", "source", "target", "sourceHandle", "targetHandle", "type", "kind"}:
            raise ValueError("NodeScene edge has unsupported fields")
        edge_id = _id(raw.get("id"), "edge.id")
        source, target = raw.get("source"), raw.get("target")
        pair = (source, target)
        if edge_id in edge_ids or pair in pairs or source == target or source not in by_id or target not in by_id:
            raise ValueError("NodeScene edge is duplicate, self-connected or dangling")
        if by_id[source]["type"] == "group" or by_id[target]["type"] == "group":
            raise ValueError("Group is not an execution input")
        if raw.get("kind", "input") not in {"input", "flow"}:
            raise ValueError("edge.kind is unsupported")
        edge = {"id": edge_id, "source": source, "target": target, "type": "reference", "kind": raw.get("kind", "input")}
        for field in ("sourceHandle", "targetHandle"):
            if raw.get(field):
                edge[field] = _id(raw[field], field)
        edges.append(edge)
        pairs.add(pair)
        edge_ids.add(edge_id)
        adjacency[source].append(target)
    # Kahn, not recursive traversal: large valid workflows must not overflow.
    indegree = {key: 0 for key in by_id}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    queue = [key for key, count in indegree.items() if count == 0]
    for key in queue:
        for target in adjacency[key]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(queue) != len(nodes):
        raise ValueError("NodeScene connections must be acyclic; loops use bounded plans")
    view = value["viewport"]
    if not isinstance(view, Mapping) or set(view) != {"x", "y", "zoom"}:
        raise ValueError("NodeScene.viewport requires x/y/zoom")
    result = {"schema_version": 2, "kind": NODE_SCENE_KIND, "nodes": nodes, "edges": edges,
              "viewport": {"x": _number(view["x"], "viewport.x"), "y": _number(view["y"], "viewport.y"),
                           "zoom": _number(view["zoom"], "viewport.zoom", .01, 30)}}
    if "metadata" in value:
        metadata = value["metadata"]
        if not isinstance(metadata, Mapping) or set(metadata) - {"templates"}:
            raise ValueError("NodeScene metadata has unsupported fields")
        templates = metadata.get("templates", [])
        if not isinstance(templates, list) or len(templates) > 100:
            raise ValueError("Canvas templates must be a bounded list")
        template_ids = set()
        for template in templates:
            if not isinstance(template, Mapping) or set(template) != {"id", "name", "category", "text"}:
                raise ValueError("Canvas template fields are unsupported")
            template_id = _id(template["id"], "template.id")
            if template_id in template_ids:
                raise ValueError("Canvas template identity is duplicate")
            template_ids.add(template_id)
            for field, limit in (("name", 120), ("category", 60), ("text", 1200)):
                text = template[field]
                if not isinstance(text, str) or len(text) > limit or field != "category" and not text.strip():
                    raise ValueError(f"Canvas template {field} is invalid")
        result["metadata"] = _json(metadata, "metadata")
    if len(json.dumps(result, ensure_ascii=False).encode("utf8")) > 8 * 1024 * 1024:
        raise ValueError("NodeScene exceeds the 8 MiB metadata limit")
    return result


def node_scene_references(scene: Mapping[str, Any]) -> list[dict]:
    result, seen = [], set()
    for node in scene["nodes"]:
        data = node["data"]
        media = [data.get("media") or {}, *data.get("outputs", []), *data.get("draft", {}).get("references", [])]
        for item in media:
            for field, kind in REFERENCE_FIELDS.items():
                ref = item.get(field)
                key = (node["id"], kind, ref)
                if ref and key not in seen:
                    seen.add(key)
                    result.append({"element_id": node["id"], "ref_kind": kind, "ref_id": ref})
        execution = data.get("execution", {})
        for task_id in {execution.get("task_id"), *execution.get("tasks", {})} - {None, ""}:
            key = (node["id"], "task", task_id)
            if key not in seen:
                seen.add(key)
                result.append({"element_id": key[0], "ref_kind": key[1], "ref_id": key[2]})
    return result


def node_scene_thumbnail(scene: Mapping[str, Any]) -> dict:
    nodes = scene["nodes"]
    return {"scene_kind": NODE_SCENE_KIND, "element_count": len(nodes),
            "image_count": sum(node["type"] == "image" for node in nodes),
            "video_count": sum(node["type"] == "video" for node in nodes),
            "audio_count": sum(node["type"] == "audio" for node in nodes),
            "frame_count": sum(node["type"] == "group" for node in nodes),
            "elements": [{"id": node["id"], "type": node["type"], "x": node["position"]["x"], "y": node["position"]["y"],
                          "width": node["style"]["width"], "height": node["style"]["height"]} for node in nodes[-12:]]}


def spatial_business_element(scene: Mapping[str, Any], element_id: str) -> dict | None:
    """One engine adapter for existing Native AI source validation.

    Never write this projection as a second scene or reinterpret draft refs as
    the object's own media. Result identity remains the exact current asset.
    """
    if scene.get("schema_version") != 2:
        return next((item for item in scene.get("elements", []) if not item.get("isDeleted") and item.get("id") == element_id), None)
    node = next((item for item in scene.get("nodes", []) if item.get("id") == element_id), None)
    if node is None or not node.get("data", {}).get("media"):
        return None
    media = node["data"]["media"]
    return {"id": node["id"], "type": node["type"], "customData": {field: media.get(field) for field in REFERENCE_FIELDS}}


def validate_node_execution_input(scene: Mapping[str, Any], parameters: Mapping[str, Any], *, source_element_id: str, source_asset_id: str) -> dict | None:
    """Re-resolve node inputs server-side. Geometry/unrelated edits are ignored.

    Existing v1 callers remain byte-compatible. A v2 caller cannot substitute
    a different draft, source, model or parameters after Governor preview.
    """
    if scene.get("schema_version") != 2:
        return None
    target_id = str(parameters.get("node_target_id") or "")
    by_id = {node["id"]: node for node in scene["nodes"]}
    target = by_id.get(target_id)
    if not target or target["type"] in {"group", "prompt"}:
        raise ValueError("node execution target is missing or incompatible")
    draft = {"prompt": "", "mode": "image", "ratio": "1:1", "resolution": "2k", "quality": "auto", "count": 1, "model": "", "references": [], **target["data"].get("draft", {})}
    visited, prompts, refs, ref_ids = {target_id}, [], [], set()
    def incoming(node_id):
        return [edge["source"] for edge in scene["edges"] if edge["target"] == node_id and edge.get("kind", "input") == "input"]
    def add_ref(media, element_id):
        if media and media.get("asset_id") not in ref_ids:
            ref_ids.add(media["asset_id"]); refs.append({**media, "sourceNodeId": element_id})
    pending = incoming(target_id)
    while pending:
        node_id = pending.pop(0)
        if node_id in visited: continue
        visited.add(node_id)
        node = by_id[node_id]
        if node["type"] == "prompt":
            text = node["data"].get("text", "")
            if text.strip(): prompts.append(text)
            pending.extend(incoming(node_id))
        elif node["data"].get("media"): add_ref(node["data"]["media"], node_id)
    for media in draft["references"]:
        candidates = [node for node in scene["nodes"] if node["data"].get("media", {}).get("asset_id") == media["asset_id"]]
        source = next((node for node in candidates if node["id"] == media["source_element_id"]), None) if media.get("source_element_id") else candidates[0] if len(candidates) == 1 else None
        add_ref(media, source["id"] if source else "")
    if not refs and target["data"].get("media") and draft.get("useCurrentMedia") is not False:
        add_ref(target["data"]["media"], target_id)
    text = "\n\n".join(part for part in [*prompts, draft["prompt"]] if part.strip())
    task_kind = "image-to-video" if draft["mode"] == "video" else "reference-generate" if refs else "text-to-image"
    semantic = {"targetId": target_id, "text": text, "references": refs, "taskKind": task_kind,
        "model": "offline-preview-v1" if draft["mode"] == "video" else draft["model"], "ratio": draft["ratio"],
        "resolution": "offline-fixture" if draft["mode"] == "video" else draft["resolution"], "quality": draft["quality"], "count": draft["count"]}
    if draft["mode"] == "video": semantic["duration_seconds"] = draft.get("duration_seconds", 5)
    try: submitted = json.loads(parameters.get("node_input_signature", ""))
    except (ValueError, TypeError): raise ValueError("node input snapshot is missing or invalid")
    if submitted != semantic:
        raise ValueError("node input changed; create a new Governor preview")
    if len(refs) != 1 or refs[0]["asset_id"] != source_asset_id or refs[0]["sourceNodeId"] != source_element_id:
        raise ValueError("node execution must use the exact current reference")
    if (semantic["model"] != parameters.get("model") or draft["ratio"] != parameters.get("output_ratio")
            or semantic["resolution"] != parameters.get("output_resolution")):
        raise ValueError("node parameters differ from the frozen draft")
    if re.sub(r"\s+", " ", text).strip() != re.sub(r"\s+", " ", str(parameters.get("brief", {}).get("user_request", ""))).strip():
        raise ValueError("compiled intent differs from current node input")
    return semantic
