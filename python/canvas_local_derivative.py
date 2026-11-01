"""Local editor adapter: existing AssetStore + Asset ledger, no execution system."""
from __future__ import annotations
import hashlib
import os
import shutil
import uuid
from pathlib import Path
try:
    from node_scene_contract import spatial_business_element
except ImportError:
    from python.node_scene_contract import spatial_business_element


def apply_canvas_derivative(*, ledger, store, output_root, canvas_id, version_id,
                            element_id, source_asset_id, request_id, operation, stream):
    if operation not in {"crop", "outpaint", "mask", "brush", "grid", "fabric", "video-frame"}:
        raise ValueError("unsupported local pixel operation")
    canvas = ledger.get_spatial_canvas(canvas_id)
    try: version = ledger.get_spatial_canvas_version(version_id)
    except KeyError as exc: raise ValueError("source scene version is missing") from exc
    if version["document_id"] != canvas_id:
        raise ValueError("source version belongs to another Canvas")
    frozen_element = spatial_business_element(version["scene"], element_id)
    if not frozen_element or frozen_element.get("customData", {}).get("asset_id") != source_asset_id:
        raise ValueError("source version does not contain the exact source")
    element = spatial_business_element(canvas["scene"], element_id)
    # Geometry/new result branches do not alter the exact pixel input. A
    # response-lost replay is also valid after its result has been saved.
    replay_id = "ast_" + hashlib.sha256(("canvas-local-v1:" + request_id).encode()).hexdigest()[:32]
    try: previous = ledger.get_asset(replay_id)
    except KeyError: previous = None
    if not previous and (not element or element.get("customData", {}).get("asset_id") != source_asset_id):
        raise ValueError("local edit must use the exact source object in this Canvas")
    source = ledger.get_asset(source_asset_id)
    if operation == "video-frame" and source.get("kind") != "video":
        raise ValueError("frame extraction requires a video")
    if source.get("kind", "image") != "image" and operation != "video-frame":
        raise ValueError("this pixel editor requires an image")
    # The mature import pipeline bounds bytes, validates image/container and
    # pixels, hashes and atomically stores bytes. Never trust client filenames.
    imported = store.import_stream(stream, "canvas-local-result.png", "product")
    if imported["mime"] != "image/png":
        raise ValueError("local editor output must be a PNG")
    _, stored_path = store.resolve_asset_path(imported["id"])
    digest = imported["sha256"]
    target = Path(output_root) / "_canvas_local_edits" / (digest + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    with store._hash_lock("canvas-output:" + digest):
        if target.exists():
            if store._file_sha256(target) != digest:
                raise ValueError("local result storage conflict; existing file was not overwritten")
        else:
            temporary = target.with_name("." + digest + "." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(stored_path, temporary)
                with temporary.open("rb+") as handle: os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    receipt = {"adapter_version": "canvas-local-pixels-v1", "operation": operation,
               "canvas_id": canvas_id, "canvas_version_id": version_id, "source_element_id": element_id,
               "source_asset_id": source_asset_id, "source_sha256": source.get("sha256", ""),
               "result_sha256": digest, "provider_calls": 0}
    result = ledger.register_canvas_derivative(source_asset_id=source_asset_id,
        client_request_id=request_id, result={"path": str(target), "name": f"{Path(source.get('name', 'image')).stem}-{operation}.png",
        "width": imported["width"], "height": imported["height"], "sha256": digest}, receipt=receipt)
    return {"asset": result, "receipt": result["metadata"]}
