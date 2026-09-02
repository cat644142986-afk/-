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


SOURCE_SCHEMA_VERSION = 5
SOURCE_SIZE = (24, 18)
OUTPAINT_SIZE = (32, 24)
OUTPAINT_OFFSET = (4, 3)
SOURCE_COLOR = (220, 100, 40)
CANDIDATE_COLOR = (30, 120, 230)


def _create_v5_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        AtelierLedger._create_v1_schema(connection)
        AtelierLedger._write_schema_version(connection, 1)
        AtelierLedger._migrate_v1_to_v2(connection)
        AtelierLedger._write_schema_version(connection, 2)
        AtelierLedger._migrate_v2_to_v3(connection)
        AtelierLedger._write_schema_version(connection, 3)
        AtelierLedger._migrate_v3_to_v4(connection)
        AtelierLedger._write_schema_version(connection, 4)
        AtelierLedger._migrate_v4_to_v5(connection)
        AtelierLedger._write_schema_version(connection, SOURCE_SCHEMA_VERSION)
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
    }
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
    if packaged_schema <= SOURCE_SCHEMA_VERSION:
        raise RuntimeError("candidate manifest does not contain a schema upgrade")

    expected_commands = {
        "command:existing-generate-single",
        "command:existing-generate-multi-file",
        "command:existing-group-split",
        "command:existing-remove-background",
        "command:transform-layer",
        "command:toggle-layer",
        "command:toggle-layer-lock",
        "command:local-edit-compose",
    }

    with _temporary_data_dir() as data_dir:
        ledger_path = data_dir / "atelier.sqlite3"
        log_path = data_dir / "candidate.log"
        source_path, candidate_path = _write_test_images(data_dir)
        _create_v5_database(ledger_path)
        if _schema_version(ledger_path) != SOURCE_SCHEMA_VERSION:
            raise RuntimeError("isolated source ledger is not schema v5")

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
        finally:
            _stop_owned_process(first_process)

        backups = sorted(data_dir.glob("atelier.sqlite3.backup-v5-*.sqlite3"))
        if len(backups) != 1:
            raise RuntimeError(f"expected one v5 migration backup, found {len(backups)}")
        backup = backups[0]
        if (
            _schema_version(backup) != SOURCE_SCHEMA_VERSION
            or _schema_version(ledger_path) != packaged_schema
        ):
            raise RuntimeError(
                f"candidate did not preserve v5 backup and migrate to v{packaged_schema}"
            )

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
        finally:
            _stop_owned_process(second_process)

        backups_after_restart = sorted(
            data_dir.glob("atelier.sqlite3.backup-v5-*.sqlite3")
        )
        if backups_after_restart != backups:
            raise RuntimeError(
                f"idempotent v{packaged_schema} restart created another migration backup"
            )
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
            raise RuntimeError("schema-v5 workspace unexpectedly restored a canvas")
        if initial_profiles != {"profiles": [], "count": 0}:
            raise RuntimeError("isolated schema-v5 ledger unexpectedly contains profiles")
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
            "schema_before": SOURCE_SCHEMA_VERSION,
            "schema_after": packaged_schema,
            "backup_count_after_restart": len(backups_after_restart),
            "backup_sha256": _sha256(backup),
            "command_contract": second_commands["contract_version"],
            "command_count": len(second_commands["commands"]),
            "empty_canvas_response": "stable-empty-envelope",
            "product_profile_api": "create-replay-restart-version-history",
            "product_profile_revisions": revisions,
            "manifest_tracks_command_registry": True,
            "manifest_tracks_local_edit_contract": True,
            "manifest_tracks_spatial_canvas_contract": True,
            "outpaint_api": "freeze-restart-compose-replay-stale-revision",
            "outpaint_result_size": list(OUTPAINT_SIZE),
            "outpaint_artboard_size": list(SOURCE_SIZE),
            "protected_changed_pixels": receipt["protected_changed_pixels"],
            "new_area_changed_pixels": receipt["new_area_changed_pixels"],
            "result_files_added": len(result_files_after_compose) - len(result_files_before_compose),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a packaged sidecar upgrade from the current formal schema, "
            "including immutable outpaint composition."
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
