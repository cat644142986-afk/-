from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.atelier_ledger import AtelierLedger, SCHEMA_VERSION  # noqa: E402


FORMAL_SOURCE_SCHEMA_VERSION = 7
LEGACY_SOURCE_SCHEMA_VERSIONS = (5,)
SUPPORTED_SOURCE_SCHEMA_VERSIONS = frozenset(
    (*LEGACY_SOURCE_SCHEMA_VERSIONS, FORMAL_SOURCE_SCHEMA_VERSION)
)
SOURCE_CONTENT_SENTINEL_KEY = "packaged_upgrade_source_sentinel"
SOURCE_BUSINESS_FIXTURE_PREFIX = "packaged-upgrade"
SOURCE_SIZE = (24, 18)
OUTPAINT_SIZE = (32, 24)
OUTPAINT_OFFSET = (4, 3)
SOURCE_COLOR = (220, 100, 40)
CANDIDATE_COLOR = (30, 120, 230)
VIDEO_SIZE = (320, 180)
VIDEO_DURATION_SECONDS = 3


def _seed_v5_business_graph(
    connection: sqlite3.Connection,
    *,
    fixture_id: str,
    source_version: int,
    fixture_time: str,
) -> None:
    profile_id = f"profile-{fixture_id}"
    profile_version_id = f"profile-version-{fixture_id}"
    profile_json = json.dumps(
        {
            "sku": f"SKU-V{source_version}",
            "approved_reference_ids": [f"ast-{fixture_id}"],
            "commercial_facts": {"category": "food"},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    profile_sha256 = hashlib.sha256(profile_json.encode("utf-8")).hexdigest()
    request_fingerprint = hashlib.sha256(
        f"profile-request-{fixture_id}".encode("utf-8")
    ).hexdigest()
    connection.execute(
        """
        INSERT INTO product_profiles(
            id, sku, current_version_id, current_revision, created_at, updated_at
        ) VALUES(?, ?, NULL, 0, ?, ?)
        """,
        (profile_id, f"SKU-V{source_version}", fixture_time, fixture_time),
    )
    connection.execute(
        """
        INSERT INTO product_profile_versions(
            id, profile_id, revision, parent_version_id, client_request_id,
            request_fingerprint, profile_json, profile_sha256, created_at
        ) VALUES(?, ?, 1, NULL, ?, ?, ?, ?, ?)
        """,
        (
            profile_version_id,
            profile_id,
            f"profile-request-{fixture_id}",
            request_fingerprint,
            profile_json,
            profile_sha256,
            fixture_time,
        ),
    )
    connection.execute(
        """
        INSERT INTO product_profile_version_assets(version_id, asset_id, role)
        VALUES(?, ?, 'approved_reference')
        """,
        (profile_version_id, f"ast-{fixture_id}"),
    )
    connection.execute(
        """
        UPDATE product_profiles
        SET current_version_id = ?, current_revision = 1
        WHERE id = ?
        """,
        (profile_version_id, profile_id),
    )
    connection.execute(
        """
        INSERT INTO job_snapshots(
            job_id, draft_id, draft_revision, mode, source_asset_ids_json,
            brief_json, intent_json, parameters_json, knowledge_refs_json,
            ui_context_json, created_at, command_id,
            canvas_document_version_id, canvas_operation_id,
            product_profile_version_id
        ) VALUES(?, NULL, 0, 'single', ?, '{"goal":"preserve"}',
                 '{"locked":true}', '{"fixture":true}', '["K-V5"]',
                 '{"surface":"migration-gate"}', ?,
                 'command:existing-generate-single', NULL, NULL, ?)
        """,
        (
            f"job-{fixture_id}",
            json.dumps([f"ast-{fixture_id}"], separators=(",", ":")),
            fixture_time,
            profile_version_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO execution_traces(
            id, job_id, job_item_id, generation_id, stage, status,
            user_input_json, compiled_prompt, applied_knowledge_json,
            ignored_fields_json, model, parameters_json, output_json,
            error_code, error_message, created_at, command_id,
            canvas_document_version_id, canvas_operation_id,
            product_profile_version_id
        ) VALUES(?, ?, NULL, NULL, 'compile', 'completed',
                 '{"fixture":true}', 'preserve profile binding', '["K-V5"]',
                 '[]', 'offline-fixture', '{"temperature":0}',
                 '{"accepted":true}', '', '', ?,
                 'command:existing-generate-single', NULL, NULL, ?)
        """,
        (f"trace-{fixture_id}", f"job-{fixture_id}", fixture_time, profile_version_id),
    )


def _create_source_database(path: Path, source_version: int) -> None:
    if source_version not in SUPPORTED_SOURCE_SCHEMA_VERSIONS:
        supported = ", ".join(
            f"v{version}" for version in sorted(SUPPORTED_SOURCE_SCHEMA_VERSIONS)
        )
        raise ValueError(
            f"unsupported packaged-upgrade fixture v{source_version}; expected {supported}"
        )

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        AtelierLedger._create_v1_schema(connection)
        AtelierLedger._write_schema_version(connection, 1)
        migration_steps = (
            (2, AtelierLedger._migrate_v1_to_v2),
            (3, AtelierLedger._migrate_v2_to_v3),
            (4, AtelierLedger._migrate_v3_to_v4),
            (5, AtelierLedger._migrate_v4_to_v5),
            (6, AtelierLedger._migrate_v5_to_v6),
            (7, AtelierLedger._migrate_v6_to_v7),
        )
        for target_version, migrate in migration_steps:
            if target_version > source_version:
                break
            migrate(connection)
            AtelierLedger._write_schema_version(connection, target_version)
        fixture_id = f"{SOURCE_BUSINESS_FIXTURE_PREFIX}-v{source_version}"
        fixture_time = "2026-09-03T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO sessions(
                id, mode, status, title, project_name, designer_profile,
                brand_profile, category, brief_json, intent_locks_json,
                started_at, updated_at
            ) VALUES(?, 'single', 'active', ?, 'migration-gate', 'fixture-designer',
                     'fixture-brand', 'food', '{"goal":"preserve"}',
                     '{"logo":true}', ?, ?)
            """,
            (f"ses-{fixture_id}", f"Schema v{source_version} business fixture", fixture_time, fixture_time),
        )
        connection.execute(
            """
            INSERT INTO assets(
                id, session_id, parent_asset_id, role, kind, path, name, mime,
                width, height, sha256, metadata_json, created_at
            ) VALUES(?, ?, NULL, 'source', 'image', ?, ?, 'image/png',
                     640, 480, ?, '{"fixture":true}', ?)
            """,
            (
                f"ast-{fixture_id}",
                f"ses-{fixture_id}",
                f"D:/fixtures/schema-v{source_version}.png",
                f"schema-v{source_version}.png",
                hashlib.sha256(fixture_id.encode("utf-8")).hexdigest(),
                fixture_time,
            ),
        )
        connection.execute(
            """
            INSERT INTO jobs(
                id, session_id, mode, status, priority, total_items,
                completed_items, failed_items, canceled_items,
                requested_concurrency, idempotency_key, parameters_json,
                created_at, queued_at, updated_at
            ) VALUES(?, ?, 'single', 'queued', 7, 1, 0, 0, 0, 1, ?,
                     '{"fixture":true}', ?, ?, ?)
            """,
            (
                f"job-{fixture_id}",
                f"ses-{fixture_id}",
                f"request-{fixture_id}",
                fixture_time,
                fixture_time,
                fixture_time,
            ),
        )
        if source_version == 5:
            _seed_v5_business_graph(
                connection,
                fixture_id=fixture_id,
                source_version=source_version,
                fixture_time=fixture_time,
            )
        connection.execute(
            "INSERT INTO ledger_meta(key, value) VALUES(?, ?)",
            (SOURCE_CONTENT_SENTINEL_KEY, f"schema-v{source_version}-content"),
        )
        connection.commit()
    finally:
        connection.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: Any | None = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> Any:
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(port: int, path: str, *, timeout: float = 3.0) -> Any:
    return _request_json(port, path, timeout=timeout)


def _get_bytes(port: int, path: str, *, timeout: float = 3.0) -> bytes:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", timeout=timeout
    ) as response:
        return response.read()


def _get_binary_response(
    port: int,
    path: str,
    *,
    timeout: float = 3.0,
) -> tuple[int, dict[str, str], bytes]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", timeout=timeout
    ) as response:
        return (
            int(response.status),
            {key.lower(): value for key, value in response.headers.items()},
            response.read(),
        )


def _wait_for_job(port: int, job_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = _get_json(port, f"/api/jobs/{job_id}", timeout=5)
        last = response.get("job") if isinstance(response, dict) else None
        if isinstance(last, dict) and last.get("status") in {"completed", "partial"}:
            return last
        if isinstance(last, dict) and last.get("status") in {"failed", "canceled"}:
            items = list(last.get("items") or [])
            first = items[0] if items else {}
            raise RuntimeError(
                "candidate video job failed: "
                f"status={last.get('status')} "
                f"code={first.get('error_code') or 'UNKNOWN'} "
                f"message={first.get('error_message') or ''}"
            )
        time.sleep(0.1)
    raise RuntimeError(f"candidate video job did not settle: {last}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().lower()


def _collect_packaged_video_evidence(port: int, job_id: str) -> dict[str, Any]:
    job_response = _get_json(port, f"/api/jobs/{job_id}", timeout=15)
    job = job_response.get("job") if isinstance(job_response, dict) else None
    if not isinstance(job, dict):
        raise RuntimeError("packaged video job response is invalid")
    items = list(job.get("items") or [])
    if len(items) != 1:
        raise RuntimeError("packaged video job must contain exactly one item")
    result_ids = list(items[0].get("result_asset_ids") or [])
    assets = [
        _get_json(port, f"/api/assets/{asset_id}", timeout=15)
        for asset_id in result_ids
    ]
    by_role = {str(asset.get("role") or ""): asset for asset in assets}
    video = by_role.get("result_video")
    cover = by_role.get("result_video_cover")
    if not isinstance(video, dict) or not isinstance(cover, dict):
        raise RuntimeError("packaged video job did not publish video and cover atomically")
    video_id = str(video.get("id") or "")
    return {
        "job": job,
        "progress": _get_json(port, f"/api/progress/{job_id}", timeout=15),
        "traces": _get_json(port, f"/api/jobs/{job_id}/traces", timeout=15),
        "video_asset": video,
        "cover_asset": cover,
        "inline": _get_binary_response(
            port, f"/api/assets/{video_id}/content", timeout=15
        ),
        "download": _get_binary_response(
            port, f"/api/assets/{video_id}/content?download=true", timeout=15
        ),
        "thumbnail": _get_binary_response(
            port, f"/api/assets/{video_id}/thumbnail?size=512", timeout=15
        ),
    }


def _validate_packaged_video_evidence(
    evidence: dict[str, Any],
    *,
    phase: str,
    expected_job_id: str,
    expected_source_id: str,
    expected_spatial_canvas_id: str,
) -> dict[str, Any]:
    job = evidence.get("job") or {}
    if str(job.get("id") or "") != expected_job_id:
        raise RuntimeError(f"{phase} video job identity changed")
    if job.get("status") != "completed" or float(job.get("progress") or 0) != 1.0:
        raise RuntimeError(f"{phase} video job did not complete")
    if int(job.get("requested_concurrency") or 0) != 1:
        raise RuntimeError(f"{phase} video job concurrency is not frozen to one")
    snapshot = job.get("snapshot") or {}
    if snapshot.get("command_id") != "command:image-to-video":
        raise RuntimeError(f"{phase} video job lost its command identity")
    if list(snapshot.get("source_asset_ids") or []) != [expected_source_id]:
        raise RuntimeError(f"{phase} video job source snapshot changed")
    parameters = job.get("parameters") or {}
    snapshot_parameters = snapshot.get("parameters") or {}
    expected_parameters = {
        "contract_version": "image-to-video-v1",
        "output_ratio": "16:9",
        "duration_seconds": VIDEO_DURATION_SECONDS,
        "motion_intensity": 3,
        "first_frame_asset_id": expected_source_id,
        "last_frame_asset_id": None,
        "provider": "offline-preview-v1",
        "provider_call_confirmed": False,
        "automatic_paid_retry": False,
    }
    if any(parameters.get(key) != value for key, value in expected_parameters.items()):
        raise RuntimeError(f"{phase} video parameters do not match the frozen contract")
    if (
        parameters.get("spatial_canvas_id") != expected_spatial_canvas_id
        or snapshot_parameters.get("spatial_canvas_id") != expected_spatial_canvas_id
    ):
        raise RuntimeError(f"{phase} video task lost its durable spatial canvas binding")

    items = list(job.get("items") or [])
    if len(items) != 1:
        raise RuntimeError(f"{phase} video job item count changed")
    item = items[0]
    attempts = list(item.get("attempts") or [])
    if (
        item.get("status") != "completed"
        or str(item.get("source_asset_id") or "") != expected_source_id
        or int(item.get("attempt_count") or 0) != 1
        or int(item.get("max_attempts") or 0) != 1
        or len(attempts) != 1
        or attempts[0].get("status") != "completed"
    ):
        raise RuntimeError(f"{phase} video job violated its single-attempt contract")

    video = evidence.get("video_asset") or {}
    cover = evidence.get("cover_asset") or {}
    video_id = str(video.get("id") or "")
    cover_id = str(cover.get("id") or "")
    result_ids = list(item.get("result_asset_ids") or [])
    if len(result_ids) != 2 or set(map(str, result_ids)) != {video_id, cover_id}:
        raise RuntimeError(f"{phase} video job result identities are incomplete")
    if (
        video.get("role") != "result_video"
        or video.get("kind") != "video"
        or video.get("mime") != "video/webm"
        or (video.get("width"), video.get("height")) != VIDEO_SIZE
        or int(video.get("duration_seconds") or 0) != VIDEO_DURATION_SECONDS
        or str(video.get("cover_asset_id") or "") != cover_id
        or str(video.get("lineage_parent_id") or "") != expected_source_id
    ):
        raise RuntimeError(f"{phase} packaged video asset contract is invalid")
    video_metadata = video.get("metadata") or {}
    if (
        video_metadata.get("contract_version") != "image-to-video-v1"
        or video_metadata.get("provider") != "offline-preview-v1"
        or video_metadata.get("offline_preview") is not True
        or video_metadata.get("automatic_paid_retry") is not False
    ):
        raise RuntimeError(f"{phase} packaged video metadata is invalid")
    if (
        video.get("content_url") != f"/api/assets/{video_id}/content"
        or video.get("stream_url") != f"/api/assets/{video_id}/content"
        or video.get("download_url")
        != f"/api/assets/{video_id}/content?download=true"
        or video.get("thumbnail_url") != f"/api/assets/{video_id}/thumbnail"
        or video.get("cover_url") != f"/api/assets/{cover_id}/thumbnail"
    ):
        raise RuntimeError(f"{phase} packaged video URLs are invalid")
    if (
        cover.get("role") != "result_video_cover"
        or cover.get("kind") != "image"
        or cover.get("mime") != "image/jpeg"
        or (cover.get("width"), cover.get("height")) != VIDEO_SIZE
        or str(cover.get("lineage_parent_id") or "") != expected_source_id
        or (cover.get("metadata") or {}).get("auxiliary_result") is not True
    ):
        raise RuntimeError(f"{phase} packaged video cover contract is invalid")

    progress = evidence.get("progress") or {}
    progress_results = progress.get("results") or {}
    public_video_results = list(progress_results.get("video") or [])
    exposed_result_ids = {
        str(asset.get("id") or "")
        for group in progress_results.values()
        for asset in list(group or [])
    }
    if (
        str(progress.get("task_id") or "") != expected_job_id
        or progress.get("status") != "completed"
        or float(progress.get("progress") or 0) != 1.0
        or list(progress_results.get("main") or [])
        or list(progress_results.get("cutout") or [])
        or [str(asset.get("id") or "") for asset in public_video_results] != [video_id]
        or exposed_result_ids != {video_id}
    ):
        raise RuntimeError(f"{phase} packaged video progress projection is invalid")

    traces = list((evidence.get("traces") or {}).get("traces") or [])
    provider_traces = [
        trace for trace in traces
        if trace.get("stage") == "provider.video.offline-preview"
    ]
    if len(provider_traces) != 1 or provider_traces[0].get("status") != "completed":
        raise RuntimeError(f"{phase} packaged video provider trace is missing")
    trace_output = provider_traces[0].get("output") or {}

    inline_status, inline_headers, inline_body = evidence.get("inline") or (0, {}, b"")
    download_status, download_headers, download_body = evidence.get("download") or (
        0, {}, b""
    )
    if (
        inline_status != 200
        or not str(inline_headers.get("content-type") or "").startswith("video/webm")
        or not str(inline_headers.get("content-disposition") or "").startswith("inline;")
        or download_status != 200
        or not str(download_headers.get("content-type") or "").startswith("video/webm")
        or not str(download_headers.get("content-disposition") or "").startswith("attachment;")
        or not inline_body
        or inline_body != download_body
    ):
        raise RuntimeError(f"{phase} packaged video inline/download contract is invalid")
    binary_sha256 = _sha256_bytes(inline_body)
    if (
        binary_sha256 != str(video.get("sha256") or "").lower()
        or binary_sha256 != str(trace_output.get("video_sha256") or "").lower()
        or len(inline_body) != int(video.get("size_bytes") or 0)
        or int(trace_output.get("network_call_count", -1)) != 0
    ):
        raise RuntimeError(f"{phase} packaged video SHA-256 evidence is invalid")

    thumbnail_status, thumbnail_headers, thumbnail_body = evidence.get("thumbnail") or (
        0, {}, b""
    )
    if (
        thumbnail_status != 200
        or not str(thumbnail_headers.get("content-type") or "").startswith("image/jpeg")
        or not thumbnail_body
    ):
        raise RuntimeError(f"{phase} packaged video thumbnail response is invalid")
    with Image.open(io.BytesIO(thumbnail_body)) as thumbnail_image:
        thumbnail_format = thumbnail_image.format
        thumbnail_size = thumbnail_image.size
        thumbnail_image.verify()
    if thumbnail_format != "JPEG" or thumbnail_size != VIDEO_SIZE:
        raise RuntimeError(f"{phase} packaged video thumbnail pixels are invalid")

    return {
        "job_id": expected_job_id,
        "video_asset_id": video_id,
        "cover_asset_id": cover_id,
        "result_asset_count": len(result_ids),
        "attempt_count": int(item.get("attempt_count") or 0),
        "trace_count": len(traces),
        "provider_trace_count": len(provider_traces),
        "network_call_count": int(trace_output.get("network_call_count") or 0),
        "video_bytes": len(inline_body),
        "video_sha256": binary_sha256.upper(),
        "thumbnail_bytes": len(thumbnail_body),
        "thumbnail_size": list(thumbnail_size),
    }


def _validate_packaged_video_restart(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    stable_json_keys = ("job", "progress", "traces", "video_asset", "cover_asset")
    changed = [key for key in stable_json_keys if first.get(key) != second.get(key)]
    if changed:
        raise RuntimeError(
            "packaged video evidence changed after restart: " + ", ".join(changed)
        )
    stable_binary_keys = ("inline", "download", "thumbnail")
    for key in stable_binary_keys:
        first_response = first.get(key) or (0, {}, b"")
        second_response = second.get(key) or (0, {}, b"")
        if first_response[0] != second_response[0] or first_response[2] != second_response[2]:
            raise RuntimeError(f"packaged video {key} bytes changed after restart")
        for header in ("content-type", "content-disposition", "content-length"):
            if first_response[1].get(header) != second_response[1].get(header):
                raise RuntimeError(
                    f"packaged video {key} {header} changed after restart"
                )
    return {
        "stable_json_projections": len(stable_json_keys),
        "stable_binary_projections": len(stable_binary_keys),
        "job_identity_preserved": True,
        "asset_identity_preserved": True,
        "sha256_preserved": True,
    }


def _request_error(
    port: int,
    path: str,
    *,
    method: str,
    payload: Any,
) -> tuple[int, Any]:
    try:
        _request_json(port, path, method=method, payload=payload)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        return int(exc.code), body
    raise RuntimeError(f"candidate unexpectedly accepted a failing request: {path}")


def _import_reference(port: int, path: Path) -> dict[str, Any]:
    boundary = f"ProductAtelierCandidate{time.time_ns():x}"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="file"; filename="candidate-reference.png"\r\n',
            b"Content-Type: image/png\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        )
    )
    result = _request_json(
        port,
        "/api/assets/import?collection=product",
        method="POST",
        body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=15,
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError("candidate asset import returned an invalid response")
    return result


def _product_profile(reference_id: str, *, revision: int = 0) -> dict[str, Any]:
    return {
        "id": "profile:candidate-transparent-bottle",
        "schema_version": 1,
        "sku": "CANDIDATE-BOTTLE-500",
        "name": "Candidate transparent bottle",
        "revision": revision,
        "category": "beverage",
        "specification": {
            "display": "500 ml x 1 bottle",
            "net_content": "500 ml",
            "unit_count": 1,
            "attributes": [],
        },
        "components": [
            {
                "id": "component:candidate-bottle",
                "name": "Transparent bottle",
                "role": "core",
                "policy": "must_preserve",
                "quantity": 1,
            },
            {
                "id": "component:candidate-label",
                "name": "Packaging label",
                "role": "label",
                "policy": "forbid_modify",
                "quantity": 1,
            },
        ],
        "materials": [
            {
                "component_id": "component:candidate-bottle",
                "material": "PET",
                "finish": "glossy",
                "transparent": True,
            }
        ],
        "brand_colors": [{"name": "Brand coral", "value": "#E86E4B"}],
        "packaging_texts": [
            {
                "id": "text:candidate-brand",
                "component_id": "component:candidate-label",
                "content": "CANDIDATE BRAND",
                "policy": "exact_preserve",
            }
        ],
        "logos": [
            {
                "id": "logo:candidate-primary",
                "component_id": "component:candidate-label",
                "name": "Candidate primary logo",
                "policy": "exact_preserve",
            }
        ],
        "platform_specs": [
            {
                "platform": "marketplace",
                "role": "main-image",
                "pixel_width": 1024,
                "pixel_height": 1024,
                "format": "png",
                "safe_area_percent": 5,
            }
        ],
        "selection_mode": "full_composition",
        "approved_reference_ids": [reference_id],
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }


def _canvas_document(source_id: str) -> dict[str, Any]:
    return {
        "id": "canvas:packaged-schema-upgrade",
        "schema_version": 1,
        "coordinate_system": {
            "unit": "canvas-pixel",
            "origin": "top-left",
            "x_axis": "right",
            "y_axis": "down",
        },
        "revision": 0,
        "active_artboard_id": "artboard:packaged-schema-upgrade",
        "source_asset_ids": [source_id],
        "artboards": [{
            "id": "artboard:packaged-schema-upgrade",
            "name": "Packaged schema upgrade",
            "rect": {"x": 0, "y": 0, "width": 24, "height": 18},
            "export": {
                "pixel_width": 24,
                "pixel_height": 18,
                "color_space": "srgb",
            },
        }],
        "layers": [{
            "id": "layer:packaged-schema-upgrade-source",
            "artboard_id": "artboard:packaged-schema-upgrade",
            "source": {
                "kind": "asset",
                "id": source_id,
                "proxy_ref": "proxy:thumbnail:512",
                "original_pixel_width": 24,
                "original_pixel_height": 18,
            },
            "transform": {
                "x": 0,
                "y": 0,
                "scale_x": 1,
                "scale_y": 1,
                "rotation_degrees": 0,
                "opacity": 1,
            },
            "z_index": 0,
            "visible": True,
            "locked": False,
        }],
        "operations": [],
        "undo_cursor": -1,
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }


def _write_test_images(data_dir: Path) -> tuple[Path, Path]:
    source_path = data_dir / "packaged-upgrade-source.png"
    output_root = data_dir / "output"
    output_root.mkdir(parents=True, exist_ok=True)
    candidate_path = output_root / "packaged-upgrade-outpaint-candidate.png"
    Image.new("RGB", SOURCE_SIZE, SOURCE_COLOR).save(source_path, "PNG")
    Image.new("RGB", OUTPAINT_SIZE, CANDIDATE_COLOR).save(candidate_path, "PNG")
    return source_path, candidate_path


def _register_outpaint_candidate(
    ledger_path: Path,
    data_dir: Path,
    source_id: str,
    candidate_path: Path,
) -> dict[str, Any]:
    ledger = AtelierLedger(ledger_path)
    if ledger.stats()["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("candidate fixture ledger is not on the packaged schema")
    source = ledger.get_asset(source_id)
    return ledger.add_asset(
        str(source["session_id"]),
        "result_main",
        parent_asset_id=source_id,
        path=str(candidate_path),
        name=candidate_path.name,
        mime="image/png",
        width=OUTPAINT_SIZE[0],
        height=OUTPAINT_SIZE[1],
        sha256=_sha256(candidate_path),
        metadata={"output_root": str(data_dir / "output")},
    )


def _wait_for_health(process: subprocess.Popen[bytes], port: int) -> dict[str, Any]:
    deadline = time.monotonic() + 45
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"candidate sidecar exited early with code {return_code}")
        try:
            health = _get_json(port, "/api/health")
            if isinstance(health, dict):
                return health
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"candidate sidecar health timed out: {last_error}")


def _stop_owned_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _start_candidate(
    executable: Path,
    sidecar_dir: Path,
    data_dir: Path,
    log_path: Path,
) -> tuple[subprocess.Popen[bytes], int, dict[str, Any]]:
    port = _free_port()
    environment = os.environ.copy()
    environment["PRODUCT_ATELIER_DATA_DIR"] = str(data_dir)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            [str(executable), str(port)],
            cwd=sidecar_dir,
            env=environment,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    finally:
        log_handle.close()
    try:
        return process, port, _wait_for_health(process, port)
    except Exception:
        _stop_owned_process(process)
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"candidate startup failed\n{tail}") from None


def _schema_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            raise RuntimeError(f"schema marker is missing: {path}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(f"ledger validation failed: {path}")
    return int(row[0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _snapshot_sqlite_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    return value


def _sqlite_content_snapshot(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        objects = [
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "table": str(row[2]),
                "sql": None if row[3] is None else str(row[3]),
            }
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ]
        tables: list[dict[str, Any]] = []
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table_name in table_names:
            quoted_table = _sqlite_identifier(table_name)
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted_table})")
            ]
            quoted_columns = ", ".join(_sqlite_identifier(column) for column in columns)
            rows = [
                [_snapshot_sqlite_value(value) for value in row]
                for row in connection.execute(
                    f"SELECT {quoted_columns} FROM {quoted_table} ORDER BY {quoted_columns}"
                )
            ]
            tables.append({"name": table_name, "columns": columns, "rows": rows})
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [
            [_snapshot_sqlite_value(value) for value in row]
            for row in connection.execute("PRAGMA foreign_key_check")
        ]
        return {
            "schema_version": AtelierLedger._read_schema_version(connection),
            "objects": objects,
            "tables": tables,
            "integrity_check": integrity,
            "foreign_key_check": foreign_keys,
        }
    finally:
        connection.close()


def _source_content_sentinel(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT value FROM ledger_meta WHERE key = ?",
            (SOURCE_CONTENT_SENTINEL_KEY,),
        ).fetchone()
        return "" if row is None else str(row[0])
    finally:
        connection.close()


def _source_business_content(path: Path, source_version: int) -> dict[str, Any]:
    fixture_id = f"{SOURCE_BUSINESS_FIXTURE_PREFIX}-v{source_version}"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        queries = {
            "session": (
                "SELECT id, mode, status, title, project_name, designer_profile, "
                "brand_profile, category, brief_json, intent_locks_json, started_at, "
                "updated_at, completed_at FROM sessions WHERE id = ?",
                f"ses-{fixture_id}",
            ),
            "asset": (
                "SELECT id, session_id, parent_asset_id, role, kind, path, name, mime, "
                "width, height, sha256, metadata_json, created_at, blob_id "
                "FROM assets WHERE id = ?",
                f"ast-{fixture_id}",
            ),
            "job": (
                "SELECT id, session_id, mode, status, priority, total_items, "
                "completed_items, failed_items, canceled_items, requested_concurrency, "
                "idempotency_key, parameters_json, created_at, queued_at, started_at, "
                "updated_at, completed_at "
                "FROM jobs WHERE id = ?",
                f"job-{fixture_id}",
            ),
            "product_profile": (
                "SELECT id, sku, current_version_id, current_revision, created_at, "
                "updated_at FROM product_profiles WHERE id = ?",
                f"profile-{fixture_id}",
            ),
            "product_profile_version": (
                "SELECT id, profile_id, revision, parent_version_id, client_request_id, "
                "request_fingerprint, profile_json, profile_sha256, created_at "
                "FROM product_profile_versions WHERE id = ?",
                f"profile-version-{fixture_id}",
            ),
            "product_profile_asset": (
                "SELECT version_id, asset_id, role FROM product_profile_version_assets "
                "WHERE version_id = ?",
                f"profile-version-{fixture_id}",
            ),
            "job_snapshot": (
                "SELECT job_id, draft_id, draft_revision, mode, source_asset_ids_json, "
                "brief_json, intent_json, parameters_json, knowledge_refs_json, "
                "ui_context_json, created_at, command_id, canvas_document_version_id, "
                "canvas_operation_id, product_profile_version_id "
                "FROM job_snapshots WHERE job_id = ?",
                f"job-{fixture_id}",
            ),
            "execution_trace": (
                "SELECT id, job_id, job_item_id, generation_id, stage, status, "
                "user_input_json, compiled_prompt, applied_knowledge_json, "
                "ignored_fields_json, model, parameters_json, output_json, error_code, "
                "error_message, created_at, command_id, canvas_document_version_id, "
                "canvas_operation_id, product_profile_version_id "
                "FROM execution_traces WHERE id = ?",
                f"trace-{fixture_id}",
            ),
        }
        if source_version != 5:
            for label in (
                "product_profile",
                "product_profile_version",
                "product_profile_asset",
                "job_snapshot",
                "execution_trace",
            ):
                queries.pop(label)
        content: dict[str, Any] = {}
        for label, (query, record_id) in queries.items():
            row = connection.execute(query, (record_id,)).fetchone()
            content[label] = None if row is None else dict(row)
        return content
    finally:
        connection.close()


@contextmanager
def _temporary_data_dir():
    prefix = "ProductAtelier-packaged-schema-upgrade-"
    path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if path.parent != temp_root or not path.name.startswith(prefix):
        raise RuntimeError(f"unsafe candidate temporary directory: {path}")
    try:
        yield path
    finally:
        deadline = time.monotonic() + 5
        while path.exists():
            try:
                shutil.rmtree(path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)


def _verify_packaged_legacy_migration(
    *,
    sidecar_dir: Path,
    executable: Path,
    manifest: dict[str, Any],
    packaged_schema: int,
    source_version: int,
) -> dict[str, Any]:
    if source_version not in LEGACY_SOURCE_SCHEMA_VERSIONS:
        raise ValueError(f"v{source_version} is not a supported legacy release schema")

    with _temporary_data_dir() as data_dir:
        ledger_path = data_dir / "atelier.sqlite3"
        log_path = data_dir / "candidate.log"
        _create_source_database(ledger_path, source_version)
        if _schema_version(ledger_path) != source_version:
            raise RuntimeError(f"isolated legacy fixture is not schema v{source_version}")

        source_fixture_sha256 = _sha256(ledger_path)
        source_fixture_content = _sqlite_content_snapshot(ledger_path)
        expected_source_sentinel = f"schema-v{source_version}-content"
        if _source_content_sentinel(ledger_path) != expected_source_sentinel:
            raise RuntimeError(f"legacy v{source_version} content sentinel is missing")
        expected_business_content = _source_business_content(ledger_path, source_version)
        missing_business_rows = sorted(
            label for label, row in expected_business_content.items() if row is None
        )
        if missing_business_rows:
            raise RuntimeError(
                f"legacy v{source_version} fixture lacks business rows: "
                + ", ".join(missing_business_rows)
            )

        first_process: subprocess.Popen[bytes] | None = None
        second_process: subprocess.Popen[bytes] | None = None
        try:
            first_process, _first_port, first_health = _start_candidate(
                executable, sidecar_dir, data_dir, log_path
            )
        finally:
            _stop_owned_process(first_process)

        backups = sorted(
            data_dir.glob(f"atelier.sqlite3.backup-v{source_version}-*.sqlite3")
        )
        all_backups = sorted(data_dir.glob("atelier.sqlite3.backup-v*-*.sqlite3"))
        if len(backups) != 1 or all_backups != backups:
            raise RuntimeError(
                f"expected exactly one migration backup from legacy schema v{source_version}, "
                f"found {len(all_backups)} total"
            )
        backup = backups[0]
        if _schema_version(backup) != source_version:
            raise RuntimeError(f"legacy backup is not schema v{source_version}")
        if _schema_version(ledger_path) != packaged_schema:
            raise RuntimeError(
                f"packaged sidecar did not migrate v{source_version} to v{packaged_schema}"
            )

        backup_content_before_restart = _sqlite_content_snapshot(backup)
        if backup_content_before_restart != source_fixture_content:
            raise RuntimeError(
                f"legacy v{source_version} backup content differs from the source fixture"
            )
        if (
            _source_content_sentinel(backup) != expected_source_sentinel
            or _source_content_sentinel(ledger_path) != expected_source_sentinel
        ):
            raise RuntimeError(
                f"legacy v{source_version} content was not preserved by the migration"
            )
        if _source_business_content(ledger_path, source_version) != expected_business_content:
            raise RuntimeError(
                f"legacy v{source_version} source business graph changed during migration"
            )
        backup_sha256_before_restart = _sha256(backup)

        try:
            second_process, _second_port, second_health = _start_candidate(
                executable, sidecar_dir, data_dir, log_path
            )
        finally:
            _stop_owned_process(second_process)

        backups_after_restart = sorted(
            data_dir.glob(f"atelier.sqlite3.backup-v{source_version}-*.sqlite3")
        )
        all_backups_after_restart = sorted(
            data_dir.glob("atelier.sqlite3.backup-v*-*.sqlite3")
        )
        if backups_after_restart != backups or all_backups_after_restart != backups:
            raise RuntimeError(
                f"idempotent v{packaged_schema} restart changed the v{source_version} backup set"
            )
        backup_sha256_after_restart = _sha256(backup)
        if backup_sha256_after_restart != backup_sha256_before_restart:
            raise RuntimeError(
                f"legacy v{source_version} backup bytes changed after restart"
            )
        if _sqlite_content_snapshot(backup) != backup_content_before_restart:
            raise RuntimeError(
                f"legacy v{source_version} backup content changed after restart"
            )
        if _source_content_sentinel(ledger_path) != expected_source_sentinel:
            raise RuntimeError(
                f"legacy v{source_version} source content changed after restart"
            )
        if _source_business_content(ledger_path, source_version) != expected_business_content:
            raise RuntimeError(
                f"legacy v{source_version} source business graph changed after restart"
            )

        for phase, health in (("first", first_health), ("second", second_health)):
            if health.get("status") != "ok":
                raise RuntimeError(f"legacy v{source_version} {phase} health is not ok")
            if health.get("service", {}).get("contract_version") != manifest.get(
                "contract_version"
            ):
                raise RuntimeError(
                    f"legacy v{source_version} {phase} health contract does not match manifest"
                )
            if health.get("service", {}).get("manifest_status") != "ok":
                raise RuntimeError(
                    f"legacy v{source_version} {phase} process rejected its manifest"
                )
            if int(health.get("ledger", {}).get("schema_version", 0)) != packaged_schema:
                raise RuntimeError(
                    f"legacy v{source_version} {phase} health does not report schema "
                    f"v{packaged_schema}"
                )

        return {
            "status": "passed",
            "schema_before": source_version,
            "schema_after": packaged_schema,
            "backup_source_schema": source_version,
            "backup_count_after_restart": len(backups_after_restart),
            "source_fixture_sha256": source_fixture_sha256,
            "backup_sha256_before_restart": backup_sha256_before_restart,
            "backup_sha256_after_restart": backup_sha256_after_restart,
            "backup_content_preserved": True,
            "source_content_preserved": True,
            "business_rows_preserved": sorted(expected_business_content),
            "restart_created_additional_backup": False,
            "packaged_process_starts": 2,
        }


def verify_candidate(sidecar_dir: Path) -> dict[str, Any]:
    sidecar_dir = sidecar_dir.resolve()
    executable = sidecar_dir / "python-server.exe"
    manifest_path = sidecar_dir / "sidecar-manifest.json"
    if not executable.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(f"candidate sidecar is incomplete: {sidecar_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    source_hashes = manifest.get("source_hashes") or {}
    required_sources = {
        "python/command_registry.py": "command registry",
        "python/canvas_export.py": "canvas export renderer",
        "python/local_edit_contract.py": "strict local edit compositor",
        "python/spatial_canvas_contract.py": "spatial canvas scene contract",
        "python/video_contract.py": "image-to-video contract",
    }
    fixture_root = ROOT / "python" / "video_fixtures" / "offline-preview-v1"
    fixture_source_keys = sorted(
        path.relative_to(ROOT).as_posix()
        for path in fixture_root.rglob("*")
        if path.is_file()
    )
    if not fixture_source_keys:
        raise RuntimeError("offline video fixture source tree is empty")
    required_sources.update({
        source_key: "offline video fixture"
        for source_key in fixture_source_keys
    })
    for source_key, label in required_sources.items():
        if source_key not in source_hashes:
            raise RuntimeError(f"sidecar manifest does not fingerprint {label}")
        if _sha256(ROOT / source_key) != str(source_hashes[source_key]).upper():
            raise RuntimeError(f"sidecar manifest {label} hash is stale")
    packaged_schema = int(manifest.get("ledger_schema_version", 0))
    if packaged_schema != SCHEMA_VERSION:
        raise RuntimeError(
            "candidate manifest schema does not match the current ledger source"
        )
    if packaged_schema <= FORMAL_SOURCE_SCHEMA_VERSION:
        raise RuntimeError("candidate manifest does not contain a schema upgrade")

    expected_commands = {
        "command:existing-generate-single",
        "command:existing-generate-multi-file",
        "command:existing-group-split",
        "command:existing-remove-background",
        "command:local-edit-generate",
        "command:image-to-video",
        "command:transform-layer",
        "command:toggle-layer",
        "command:toggle-layer-lock",
        "command:local-edit-compose",
    }

    legacy_migrations = {
        f"v{source_version}": _verify_packaged_legacy_migration(
            sidecar_dir=sidecar_dir,
            executable=executable,
            manifest=manifest,
            packaged_schema=packaged_schema,
            source_version=source_version,
        )
        for source_version in LEGACY_SOURCE_SCHEMA_VERSIONS
    }

    with _temporary_data_dir() as data_dir:
        ledger_path = data_dir / "atelier.sqlite3"
        log_path = data_dir / "candidate.log"
        source_path, candidate_path = _write_test_images(data_dir)
        _create_source_database(ledger_path, FORMAL_SOURCE_SCHEMA_VERSION)
        if _schema_version(ledger_path) != FORMAL_SOURCE_SCHEMA_VERSION:
            raise RuntimeError(
                "isolated source ledger is not the current formal schema "
                f"v{FORMAL_SOURCE_SCHEMA_VERSION}"
            )
        source_fixture_sha256 = _sha256(ledger_path)
        source_fixture_content = _sqlite_content_snapshot(ledger_path)
        expected_source_sentinel = (
            f"schema-v{FORMAL_SOURCE_SCHEMA_VERSION}-content"
        )
        if _source_content_sentinel(ledger_path) != expected_source_sentinel:
            raise RuntimeError("formal source fixture content sentinel is missing")

        first_process: subprocess.Popen[bytes] | None = None
        second_process: subprocess.Popen[bytes] | None = None
        try:
            first_process, first_port, first_health = _start_candidate(
                executable, sidecar_dir, data_dir, log_path
            )
            first_commands = _get_json(first_port, "/api/commands")
            first_canvas = _get_json(first_port, "/api/workspaces/single/canvas")
            initial_profiles = _get_json(first_port, "/api/product-profiles")
            reference = _import_reference(first_port, source_path)
            profile = _product_profile(str(reference["id"]))
            save_payload = {
                "expected_revision": 0,
                "client_request_id": "candidate-profile-save-v1",
                "profile": profile,
            }
            first_profile = _request_json(
                first_port,
                f"/api/product-profiles/{profile['id']}",
                method="PUT",
                payload=save_payload,
            )
            replayed_profile = _request_json(
                first_port,
                f"/api/product-profiles/{profile['id']}",
                method="PUT",
                payload=save_payload,
            )
            document = _canvas_document(str(reference["id"]))
            first_saved_canvas = _request_json(
                first_port,
                "/api/workspaces/single/canvas",
                method="PUT",
                payload={
                    "expected_revision": 0,
                    "client_request_id": "packaged-upgrade-canvas-v1",
                    "document": document,
                },
            )
            roi = _request_json(
                first_port,
                "/api/canvas-rois",
                method="POST",
                payload={
                    "canvas_document_id": document["id"],
                    "expected_canvas_revision": 1,
                    "source_layer_id": "layer:packaged-schema-upgrade-source",
                    "coordinate_space": "output-pixel",
                    "rect": {"x": 0, "y": 0, "width": 32, "height": 24},
                    "purpose": "outpaint",
                    "client_request_id": "packaged-upgrade-outpaint-roi",
                },
            )
            contract = {
                "schema_version": 1,
                "operation_id": "operation:packaged-upgrade-outpaint",
                "mode": "outpaint",
                "source_canvas_version_id": first_saved_canvas["current_version_id"],
                "source_layer_id": "layer:packaged-schema-upgrade-source",
                "source_sha256": reference["sha256"],
                "source_size": {"width": 24, "height": 18},
                "roi": {
                    "id": roi["id"],
                    "coordinate_space": "output-pixel",
                    "rect": {"x": 0, "y": 0, "width": 32, "height": 24},
                },
                "mask": None,
                "strict_pixel_protection": True,
                "outpaint": {
                    "output_width": 32,
                    "output_height": 24,
                    "source_x": 4,
                    "source_y": 3,
                    "transition_width": 0,
                },
                "cost": {
                    "mode": "paid",
                    "confirmed_call_count": 1,
                    "user_confirmation_required": True,
                    "user_confirmed": True,
                    "automatic_paid_retry": False,
                },
            }
            spec = _request_json(
                first_port,
                "/api/local-edit-specs",
                method="POST",
                payload={
                    "client_request_id": "packaged-upgrade-outpaint-spec",
                    "contract": contract,
                },
            )
            first_latest_spec = _get_json(
                first_port,
                "/api/local-edit-specs/latest?"
                f"canvas_version_id={first_saved_canvas['current_version_id']}&"
                "source_layer_id=layer%3Apackaged-schema-upgrade-source&"
                f"roi_id={roi['id']}&mode=outpaint",
            )
            spatial_canvas = _request_json(
                first_port,
                "/api/spatial-canvases",
                method="POST",
                payload={
                    "name": "安装包视频恢复画布",
                    "client_request_id": "packaged-upgrade-video-canvas-v1",
                },
            )
            spatial_canvas_id = str(spatial_canvas.get("id") or "")
            if not spatial_canvas_id:
                raise RuntimeError("packaged spatial canvas creation returned no id")
            video_payload = {
                "client_request_id": "packaged-upgrade-video-v1",
                "source_asset_ids": [reference["id"]],
                "spatial_canvas_id": spatial_canvas_id,
                "parameters": {
                    "contract_version": "image-to-video-v1",
                    "prompt": "镜头缓慢推进，保持商品包装、文字与颜色稳定",
                    "output_ratio": "16:9",
                    "duration_seconds": VIDEO_DURATION_SECONDS,
                    "motion_intensity": 3,
                    "first_frame_asset_id": reference["id"],
                    "last_frame_asset_id": None,
                    "provider": "offline-preview-v1",
                    "provider_call_confirmed": False,
                    "automatic_paid_retry": False,
                },
                "requested_concurrency": 1,
                "max_attempts": 4,
            }
            video_created = _request_json(
                first_port,
                "/api/commands/command:image-to-video/execute",
                method="POST",
                payload=video_payload,
                timeout=15,
            )
            video_job_id = str((video_created.get("job") or {}).get("id") or "")
            if not video_job_id:
                raise RuntimeError("packaged image-to-video command returned no job id")
            if video_created.get("created") is not True:
                raise RuntimeError("first packaged video command was incorrectly replayed")
            if (video_created.get("command") or {}).get("id") != "command:image-to-video":
                raise RuntimeError("first packaged video command identity is invalid")
            _wait_for_job(first_port, video_job_id)
            first_video_evidence = _collect_packaged_video_evidence(
                first_port, video_job_id
            )
        finally:
            _stop_owned_process(first_process)

        backups = sorted(
            data_dir.glob(
                "atelier.sqlite3."
                f"backup-v{FORMAL_SOURCE_SCHEMA_VERSION}-*.sqlite3"
            )
        )
        all_backups = sorted(data_dir.glob("atelier.sqlite3.backup-v*-*.sqlite3"))
        if len(backups) != 1 or all_backups != backups:
            raise RuntimeError(
                "expected exactly one migration backup from formal schema "
                f"v{FORMAL_SOURCE_SCHEMA_VERSION}, found {len(all_backups)} total"
            )
        backup = backups[0]
        if (
            _schema_version(backup) != FORMAL_SOURCE_SCHEMA_VERSION
            or _schema_version(ledger_path) != packaged_schema
        ):
            raise RuntimeError(
                "candidate did not preserve formal schema "
                f"v{FORMAL_SOURCE_SCHEMA_VERSION} and migrate to v{packaged_schema}"
            )
        backup_sha256_before_restart = _sha256(backup)
        backup_content_before_restart = _sqlite_content_snapshot(backup)
        if backup_content_before_restart != source_fixture_content:
            raise RuntimeError(
                "formal migration backup content differs from the pre-migration fixture"
            )
        if (
            _source_content_sentinel(backup) != expected_source_sentinel
            or _source_content_sentinel(ledger_path) != expected_source_sentinel
        ):
            raise RuntimeError("v7 source content was not preserved by the v8 migration")

        candidate = _register_outpaint_candidate(
            ledger_path,
            data_dir,
            str(reference["id"]),
            candidate_path,
        )
        result_files_before_compose = sorted((data_dir / "output").rglob("result-*.png"))

        try:
            second_process, second_port, second_health = _start_candidate(
                executable, sidecar_dir, data_dir, log_path
            )
            second_commands = _get_json(second_port, "/api/commands")
            second_canvas = _get_json(second_port, "/api/workspaces/single/canvas")
            restored_profile = _get_json(
                second_port, f"/api/product-profiles/{profile['id']}"
            )
            changed_profile = dict(restored_profile["profile"])
            changed_profile["selection_mode"] = "core_only"
            second_profile = _request_json(
                second_port,
                f"/api/product-profiles/{profile['id']}",
                method="PUT",
                payload={
                    "expected_revision": 1,
                    "client_request_id": "candidate-profile-save-v2",
                    "profile": changed_profile,
                },
            )
            profile_versions = _get_json(
                second_port, f"/api/product-profiles/{profile['id']}/versions"
            )
            restored_spec = _get_json(
                second_port,
                "/api/local-edit-specs/latest?"
                f"canvas_version_id={first_saved_canvas['current_version_id']}&"
                "source_layer_id=layer%3Apackaged-schema-upgrade-source&"
                f"roi_id={roi['id']}&mode=outpaint",
            )
            compose_payload = {
                "local_edit_spec_id": spec["id"],
                "candidate_asset_id": candidate["id"],
                "expected_canvas_revision": 1,
                "client_request_id": "packaged-upgrade-outpaint-compose",
            }
            composed = _request_json(
                second_port,
                "/api/workspaces/single/local-edit/compose",
                method="POST",
                payload=compose_payload,
                timeout=15,
            )
            result_files_after_compose = sorted(
                (data_dir / "output").rglob("result-*.png")
            )
            replayed_compose = _request_json(
                second_port,
                "/api/workspaces/single/local-edit/compose",
                method="POST",
                payload=compose_payload,
                timeout=15,
            )
            result_files_after_replay = sorted(
                (data_dir / "output").rglob("result-*.png")
            )
            stale_status, stale_body = _request_error(
                second_port,
                "/api/workspaces/single/local-edit/compose",
                method="POST",
                payload={
                    **compose_payload,
                    "client_request_id": "packaged-upgrade-outpaint-stale",
                },
            )
            result_content = _get_bytes(
                second_port,
                f"/api/assets/{composed['result_asset_id']}/content",
                timeout=15,
            )
            restored_video_job_response = _get_json(
                second_port, f"/api/jobs/{video_job_id}", timeout=15
            )
            if str((restored_video_job_response.get("job") or {}).get("id") or "") != video_job_id:
                raise RuntimeError("packaged restart did not restore the video job")
            replayed_video = _request_json(
                second_port,
                "/api/commands/command:image-to-video/execute",
                method="POST",
                payload=video_payload,
                timeout=15,
            )
            if replayed_video.get("created") is not False:
                raise RuntimeError("packaged video request did not replay after restart")
            if str((replayed_video.get("job") or {}).get("id") or "") != video_job_id:
                raise RuntimeError("packaged video replay changed the job identity")
            second_video_evidence = _collect_packaged_video_evidence(
                second_port, video_job_id
            )
        finally:
            _stop_owned_process(second_process)

        backups_after_restart = sorted(
            data_dir.glob(
                "atelier.sqlite3."
                f"backup-v{FORMAL_SOURCE_SCHEMA_VERSION}-*.sqlite3"
            )
        )
        all_backups_after_restart = sorted(
            data_dir.glob("atelier.sqlite3.backup-v*-*.sqlite3")
        )
        if backups_after_restart != backups or all_backups_after_restart != backups:
            raise RuntimeError(
                f"idempotent v{packaged_schema} restart created another migration backup"
            )
        backup_sha256_after_restart = _sha256(backup)
        backup_content_after_restart = _sqlite_content_snapshot(backup)
        if backup_sha256_after_restart != backup_sha256_before_restart:
            raise RuntimeError("formal migration backup bytes changed after restart")
        if backup_content_after_restart != backup_content_before_restart:
            raise RuntimeError("formal migration backup content changed after restart")
        if (
            _source_content_sentinel(backup) != expected_source_sentinel
            or _source_content_sentinel(ledger_path) != expected_source_sentinel
        ):
            raise RuntimeError("formal source content changed after candidate restart")
        for health in (first_health, second_health):
            if health.get("status") != "ok":
                raise RuntimeError("candidate health is not ok")
            if health.get("service", {}).get("contract_version") != manifest.get(
                "contract_version"
            ):
                raise RuntimeError("candidate health contract does not match manifest")
            if health.get("service", {}).get("manifest_status") != "ok":
                raise RuntimeError("candidate rejected its sidecar manifest")
            if int(health.get("ledger", {}).get("schema_version", 0)) != packaged_schema:
                raise RuntimeError("candidate health does not report the packaged schema")
        for commands in (first_commands, second_commands):
            if commands.get("contract_version") != "canvas-command-v1":
                raise RuntimeError("command API contract version is wrong")
            command_ids = {item.get("id") for item in commands.get("commands", [])}
            if command_ids != expected_commands:
                raise RuntimeError("command API registry is incomplete")
        first_video_metrics = _validate_packaged_video_evidence(
            first_video_evidence,
            phase="first process",
            expected_job_id=video_job_id,
            expected_source_id=str(reference["id"]),
            expected_spatial_canvas_id=spatial_canvas_id,
        )
        second_video_metrics = _validate_packaged_video_evidence(
            second_video_evidence,
            phase="second process",
            expected_job_id=video_job_id,
            expected_source_id=str(reference["id"]),
            expected_spatial_canvas_id=spatial_canvas_id,
        )
        video_restart_metrics = _validate_packaged_video_restart(
            first_video_evidence, second_video_evidence
        )
        empty_canvas = {
            "id": None,
            "document": None,
            "version": None,
            "proxies": [],
            "current_revision": 0,
            "current_version_id": None,
            "replayed": False,
        }
        if first_canvas != empty_canvas:
            raise RuntimeError(
                "formal-schema workspace unexpectedly restored a Fabric canvas"
            )
        if initial_profiles != {"profiles": [], "count": 0}:
            raise RuntimeError(
                "isolated formal-schema ledger unexpectedly contains profiles"
            )
        if int(first_profile.get("profile", {}).get("revision", 0)) != 1:
            raise RuntimeError("candidate product profile API did not create revision 1")
        if not replayed_profile.get("replayed"):
            raise RuntimeError("candidate product profile API did not replay idempotently")
        if int(restored_profile.get("profile", {}).get("revision", 0)) != 1:
            raise RuntimeError("candidate restart did not restore product profile revision 1")
        if int(second_profile.get("profile", {}).get("revision", 0)) != 2:
            raise RuntimeError("candidate product profile API did not create revision 2")
        revisions = [
            int(item.get("revision", 0))
            for item in profile_versions.get("versions", [])
        ]
        if revisions != [2, 1]:
            raise RuntimeError("candidate product profile history is not immutable and ordered")

        if int(first_saved_canvas.get("current_revision", 0)) != 1:
            raise RuntimeError("packaged canvas API did not create revision 1")
        if first_latest_spec.get("spec", {}).get("id") != spec["id"]:
            raise RuntimeError("packaged sidecar did not expose the frozen outpaint spec")
        if second_canvas.get("current_revision") != 1:
            raise RuntimeError("packaged sidecar restart did not restore canvas revision 1")
        if restored_spec.get("spec", {}).get("id") != spec["id"]:
            raise RuntimeError("packaged sidecar restart did not restore the outpaint spec")
        if composed.get("replayed"):
            raise RuntimeError("first packaged outpaint composition was incorrectly replayed")
        if not replayed_compose.get("replayed"):
            raise RuntimeError("packaged outpaint composition did not replay idempotently")
        if replayed_compose.get("result_asset_id") != composed.get("result_asset_id"):
            raise RuntimeError("packaged outpaint replay changed its result identity")
        if len(result_files_after_compose) != len(result_files_before_compose) + 1:
            raise RuntimeError("packaged outpaint did not publish exactly one result file")
        if result_files_after_replay != result_files_after_compose:
            raise RuntimeError("packaged outpaint replay published another result file")
        if stale_status != 409 or (stale_body or {}).get("detail", {}).get("code") != (
            "CANVAS_REVISION_CONFLICT"
        ):
            raise RuntimeError("packaged outpaint did not reject a stale canvas revision")

        receipt = composed.get("receipt") or {}
        result_asset = composed.get("result_asset") or {}
        final_canvas = composed.get("canvas") or {}
        if (
            result_asset.get("width") != OUTPAINT_SIZE[0]
            or result_asset.get("height") != OUTPAINT_SIZE[1]
        ):
            raise RuntimeError("packaged outpaint result dimensions are wrong")
        if receipt.get("protected_changed_pixels") != 0:
            raise RuntimeError("packaged outpaint changed protected source pixels")
        if receipt.get("new_area_changed_pixels") != 336:
            raise RuntimeError("packaged outpaint new-area pixel count is wrong")
        if final_canvas.get("current_revision") != 2:
            raise RuntimeError("packaged outpaint did not create canvas revision 2")
        artboard_rect = final_canvas.get("document", {}).get("artboards", [{}])[0].get("rect")
        if artboard_rect != {"x": 0, "y": 0, "width": 24, "height": 18}:
            raise RuntimeError("packaged outpaint changed the locked original artboard")

        with Image.open(source_path) as opened:
            source_image = opened.convert("RGBA")
        with Image.open(io.BytesIO(result_content)) as opened:
            result_image = opened.convert("RGBA")
        if result_image.size != OUTPAINT_SIZE:
            raise RuntimeError("packaged outpaint content dimensions are wrong")
        source_box = (
            OUTPAINT_OFFSET[0],
            OUTPAINT_OFFSET[1],
            OUTPAINT_OFFSET[0] + SOURCE_SIZE[0],
            OUTPAINT_OFFSET[1] + SOURCE_SIZE[1],
        )
        if ImageChops.difference(result_image.crop(source_box), source_image).getbbox():
            raise RuntimeError("packaged outpaint source region is not pixel-identical")
        manual_new_area_pixels = sum(
            1
            for y in range(OUTPAINT_SIZE[1])
            for x in range(OUTPAINT_SIZE[0])
            if not (
                source_box[0] <= x < source_box[2]
                and source_box[1] <= y < source_box[3]
            )
            and result_image.getpixel((x, y)) != (0, 0, 0, 0)
        )
        if manual_new_area_pixels != 336:
            raise RuntimeError("packaged outpaint content did not fill the expected new area")

        return {
            "status": "passed",
            "contract_version": manifest["contract_version"],
            "schema_before": FORMAL_SOURCE_SCHEMA_VERSION,
            "schema_after": packaged_schema,
            "backup_source_schema": FORMAL_SOURCE_SCHEMA_VERSION,
            "backup_count_after_restart": len(backups_after_restart),
            "source_fixture_sha256": source_fixture_sha256,
            "backup_sha256": backup_sha256_after_restart,
            "backup_sha256_before_restart": backup_sha256_before_restart,
            "backup_sha256_after_restart": backup_sha256_after_restart,
            "backup_content_preserved": True,
            "formal_source_content_preserved": True,
            "restart_created_additional_backup": False,
            "supported_source_schemas": sorted(SUPPORTED_SOURCE_SCHEMA_VERSIONS),
            "legacy_migrations": legacy_migrations,
            "command_contract": second_commands["contract_version"],
            "command_count": len(second_commands["commands"]),
            "empty_canvas_response": "stable-empty-envelope",
            "product_profile_api": "create-replay-restart-version-history",
            "product_profile_revisions": revisions,
            "manifest_tracks_command_registry": True,
            "manifest_tracks_local_edit_contract": True,
            "manifest_tracks_spatial_canvas_contract": True,
            "manifest_tracks_video_contract": True,
            "manifest_video_fixture_files": len(fixture_source_keys),
            "outpaint_api": "freeze-restart-compose-replay-stale-revision",
            "outpaint_result_size": list(OUTPAINT_SIZE),
            "outpaint_artboard_size": list(SOURCE_SIZE),
            "protected_changed_pixels": receipt["protected_changed_pixels"],
            "new_area_changed_pixels": receipt["new_area_changed_pixels"],
            "result_files_added": len(result_files_after_compose) - len(result_files_before_compose),
            "video_api": "create-complete-restart-replay-original-binary",
            "video_metrics": {
                "first_process": first_video_metrics,
                "second_process": second_video_metrics,
                "restart": video_restart_metrics,
                "idempotent_replay": replayed_video.get("created") is False,
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify packaged sidecar upgrades from supported release schemas "
            f"v{min(SUPPORTED_SOURCE_SCHEMA_VERSIONS)} and "
            f"v{max(SUPPORTED_SOURCE_SCHEMA_VERSIONS)} to v{SCHEMA_VERSION}, "
            "including immutable outpaint composition and offline video recovery."
        )
    )
    parser.add_argument(
        "--sidecar-dir",
        type=Path,
        default=ROOT / "src-tauri" / "bin" / "python-server",
    )
    args = parser.parse_args()
    print(json.dumps(verify_candidate(args.sidecar_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
