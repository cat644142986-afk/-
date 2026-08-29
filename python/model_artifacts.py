from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


LOCAL_RECEIPT_NAME = "product-atelier-model-receipt.json"


def load_artifact_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0":
        raise ValueError("model artifact manifest schema_version must be 1.0")
    source = manifest.get("source")
    policy = manifest.get("packaging_policy")
    files = manifest.get("files")
    if not isinstance(source, dict) or not str(source.get("repo_id") or "").strip():
        raise ValueError("model artifact manifest requires a source repo_id")
    revision = str(source.get("revision") or "")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("model artifact source revision must be a full lowercase git SHA")
    if not isinstance(policy, dict) or not policy.get("development_only"):
        raise ValueError("model artifact must remain development-only")
    if policy.get("include_in_formal_sidecar") is not False:
        raise ValueError("model artifact must remain excluded from the formal sidecar")
    if policy.get("automatic_application_download") is not False:
        raise ValueError("model artifact must not be downloaded by the application")
    if not isinstance(files, list) or not files:
        raise ValueError("model artifact manifest requires files")
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"model artifact file {index + 1} must be an object")
        relative = Path(str(item.get("path") or ""))
        if (
            not relative.name
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in seen
        ):
            raise ValueError(f"model artifact file {index + 1} has an unsafe path")
        seen.add(relative.as_posix())
        if int(item.get("bytes") or 0) < 1:
            raise ValueError(f"model artifact file {relative} has an invalid size")
        digest = str(item.get("sha256") or "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"model artifact file {relative} has an invalid SHA-256")
    if "model.safetensors" not in seen or "pytorch_model.bin" in seen:
        raise ValueError("only safetensors model weights are permitted")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def default_external_model_path(artifact_id: str) -> Path:
    configured_root = str(os.environ.get("PRODUCT_ATELIER_MODELS_DIR", "") or "").strip()
    root = Path(configured_root).expanduser() if configured_root else Path.home() / "ProductAtelier-Models"
    return (root / artifact_id).resolve()


def validate_external_destination(destination: str | Path, project_root: str | Path) -> Path:
    target = Path(destination).expanduser().resolve()
    project = Path(project_root).resolve()
    home = Path.home().resolve()
    if target == Path(target.anchor) or target == home:
        raise ValueError("model destination cannot be a filesystem root or the user home")
    try:
        target.relative_to(project)
    except ValueError:
        pass
    else:
        raise ValueError("model destination must stay outside the source repository")
    return target


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(destination: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(destination).resolve()
    results = []
    for expected in manifest["files"]:
        relative = str(expected["path"])
        target = root / relative
        exists = target.is_file()
        size = target.stat().st_size if exists else 0
        digest = sha256_file(target) if exists and size == int(expected["bytes"]) else ""
        ok = (
            exists
            and size == int(expected["bytes"])
            and digest == str(expected["sha256"])
        )
        results.append({
            "path": relative,
            "exists": exists,
            "bytes": size,
            "sha256": digest,
            "ok": ok,
        })
    return {
        "status": "verified" if all(item["ok"] for item in results) else "invalid",
        "artifact_id": manifest.get("artifact_id"),
        "destination": str(root),
        "source_revision": manifest["source"]["revision"],
        "license": manifest["source"]["license"],
        "files": results,
    }


def write_local_receipt(destination: str | Path, verification: Mapping[str, Any]) -> Path:
    if verification.get("status") != "verified":
        raise ValueError("cannot write a receipt for an unverified model artifact")
    root = Path(destination).resolve()
    receipt = root / LOCAL_RECEIPT_NAME
    payload = dict(verification)
    payload["receipt_kind"] = "local-external-model"
    receipt.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt
