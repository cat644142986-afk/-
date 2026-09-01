from __future__ import annotations

import argparse
import hashlib
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


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.atelier_ledger import AtelierLedger  # noqa: E402


def _create_v4_database(path: Path) -> None:
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
    prefix = "ProductAtelier-v4-v5-candidate-"
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
    }
    for source_key, label in required_sources.items():
        if source_key not in source_hashes:
            raise RuntimeError(f"sidecar manifest does not fingerprint {label}")
        if _sha256(ROOT / source_key) != str(source_hashes[source_key]).upper():
            raise RuntimeError(f"sidecar manifest {label} hash is stale")
    if int(manifest.get("ledger_schema_version", 0)) != 5:
        raise RuntimeError("candidate manifest is not bound to schema v5")

    expected_commands = {
        "command:existing-generate-single",
        "command:existing-generate-multi-file",
        "command:existing-group-split",
        "command:existing-remove-background",
        "command:transform-layer",
        "command:toggle-layer",
        "command:toggle-layer-lock",
    }

    with _temporary_data_dir() as data_dir:
        ledger_path = data_dir / "atelier.sqlite3"
        log_path = data_dir / "candidate.log"
        _create_v4_database(ledger_path)
        if _schema_version(ledger_path) != 4:
            raise RuntimeError("isolated source ledger is not schema v4")

        first_process: subprocess.Popen[bytes] | None = None
        second_process: subprocess.Popen[bytes] | None = None
        try:
            first_process, first_port, first_health = _start_candidate(
                executable, sidecar_dir, data_dir, log_path
            )
            first_commands = _get_json(first_port, "/api/commands")
            first_canvas = _get_json(first_port, "/api/workspaces/single/canvas")
            initial_profiles = _get_json(first_port, "/api/product-profiles")
            reference = _import_reference(
                first_port,
                ROOT / "tests" / "fixtures" / "generation_quality" / "generated"
                / "packaging-text-brand.png",
            )
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
        finally:
            _stop_owned_process(first_process)

        backups = sorted(data_dir.glob("atelier.sqlite3.backup-v4-*.sqlite3"))
        if len(backups) != 1:
            raise RuntimeError(f"expected one v4 migration backup, found {len(backups)}")
        backup = backups[0]
        if _schema_version(backup) != 4 or _schema_version(ledger_path) != 5:
            raise RuntimeError("candidate did not preserve v4 backup and migrate to v5")

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
        finally:
            _stop_owned_process(second_process)

        backups_after_restart = sorted(
            data_dir.glob("atelier.sqlite3.backup-v4-*.sqlite3")
        )
        if backups_after_restart != backups:
            raise RuntimeError("idempotent v5 restart created another migration backup")
        for health in (first_health, second_health):
            if health.get("status") != "ok":
                raise RuntimeError("candidate health is not ok")
            if health.get("service", {}).get("contract_version") != manifest.get(
                "contract_version"
            ):
                raise RuntimeError("candidate health contract does not match manifest")
            if health.get("service", {}).get("manifest_status") != "ok":
                raise RuntimeError("candidate rejected its sidecar manifest")
            if int(health.get("ledger", {}).get("schema_version", 0)) != 5:
                raise RuntimeError("candidate health does not report schema v5")
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
        if first_canvas != empty_canvas or second_canvas != empty_canvas:
            raise RuntimeError("empty v4 workspace unexpectedly restored a canvas")
        if initial_profiles != {"profiles": [], "count": 0}:
            raise RuntimeError("migrated v4 ledger unexpectedly contains product profiles")
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

        return {
            "status": "passed",
            "contract_version": manifest["contract_version"],
            "schema_before": 4,
            "schema_after": 5,
            "backup_count_after_restart": len(backups_after_restart),
            "backup_sha256": _sha256(backup),
            "command_contract": second_commands["contract_version"],
            "command_count": len(second_commands["commands"]),
            "empty_canvas_response": "stable-empty-envelope",
            "product_profile_api": "create-replay-restart-version-history",
            "product_profile_revisions": revisions,
            "manifest_tracks_command_registry": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a packaged schema-v5 sidecar against an isolated v4 ledger."
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
