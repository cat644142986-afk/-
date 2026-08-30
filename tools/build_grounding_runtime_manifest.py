from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MANIFEST_NAME = "grounding-runtime-manifest.json"
CONTRACT_VERSION = "2026-08-30.1"
SOURCE_FILES = (
    "python/grounding_runtime_worker.py",
    "python/semantic_grounding.py",
    "python/grounding_runtime.py",
    "python/requirements-grounding.txt",
    "grounding-runtime.spec",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(project_root: Path) -> tuple[str, list[dict]]:
    digest = hashlib.sha256()
    sources = []
    for relative in SOURCE_FILES:
        path = project_root / relative
        file_hash = _sha256(path)
        sources.append({"path": relative, "bytes": path.stat().st_size, "sha256": file_hash})
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), sources


def _inventory(runtime_root: Path, output: Path) -> list[dict]:
    files = []
    for path in sorted(runtime_root.rglob("*")):
        if path == output or path.name.startswith(f".{MANIFEST_NAME}."):
            continue
        if path.is_symlink():
            raise ValueError(f"runtime pack cannot contain links: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(runtime_root).as_posix()
        files.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    if not files:
        raise ValueError("runtime pack is empty")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Lock a built Product Atelier grounding runtime pack.")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runtime-id", default="grounding-dino-transformers-windows-amd64-v1")
    parser.add_argument("--model-artifact-id", default="grounding-dino-tiny-a2bb814")
    args = parser.parse_args()
    runtime_root = args.runtime_root.resolve()
    project_root = args.project_root.resolve()
    output = (args.output or runtime_root / MANIFEST_NAME).resolve()
    if output.parent != runtime_root:
        parser.error("runtime manifest must be written at the runtime root")
    commit = str(args.git_commit).strip().lower()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        parser.error("--git-commit must be a full lowercase SHA")
    entry_name = "grounding-runtime.exe" if os.name == "nt" else "grounding-runtime"
    entrypoint_path = runtime_root / entry_name
    if not entrypoint_path.is_file():
        parser.error(f"runtime entrypoint is missing: {entrypoint_path}")
    files = _inventory(runtime_root, output)
    entrypoint = next((item for item in files if item["path"] == entry_name), None)
    if entrypoint is None:
        parser.error("runtime entrypoint is missing from inventory")
    fingerprint, sources = _source_fingerprint(project_root)
    packages = {}
    for package in ("torch", "transformers", "tokenizers", "safetensors", "huggingface-hub", "PyInstaller"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "missing"
    manifest = {
        "schema_version": "1.0",
        "runtime_id": args.runtime_id,
        "contract_version": CONTRACT_VERSION,
        "platform": {
            "system": platform.system().lower(),
            "machine": platform.machine().lower(),
        },
        "entrypoint": dict(entrypoint),
        "supported_model_artifact_ids": [args.model_artifact_id],
        "source": {
            "git_commit": commit,
            "source_fingerprint": fingerprint,
            "files": sources,
        },
        "packages": packages,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": files,
    }
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{MANIFEST_NAME}.",
        suffix=".tmp",
        dir=runtime_root,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(output)
    print(json.dumps({
        "status": "generated",
        "runtime_id": manifest["runtime_id"],
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "output": str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

