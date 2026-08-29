from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.model_artifacts import (  # noqa: E402
    default_external_model_path,
    load_artifact_manifest,
    validate_external_destination,
    verify_artifact,
    write_local_receipt,
)


DEFAULT_MANIFEST = (
    PROJECT_ROOT / "docs" / "model-artifacts" / "grounding-dino-tiny.json"
)


def _download(destination: Path, manifest: dict) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("huggingface-hub is required for the explicit download step") from exc
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=manifest["source"]["repo_id"],
        revision=manifest["source"]["revision"],
        local_dir=destination,
        allow_patterns=[item["path"] for item in manifest["files"]],
        token=False,
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly download or verify the development-only local semantic-grounding model. "
            "The Product Atelier application never calls this tool automatically."
        ),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--download", action="store_true")
    action.add_argument("--verify", action="store_true")
    action.add_argument("--inspect", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--destination",
        type=Path,
        help="External destination; defaults to PRODUCT_ATELIER_MODELS_DIR or ~/ProductAtelier-Models.",
    )
    args = parser.parse_args()

    manifest = load_artifact_manifest(args.manifest)
    destination = validate_external_destination(
        args.destination or default_external_model_path(manifest["artifact_id"]),
        PROJECT_ROOT,
    )
    if args.inspect:
        print(json.dumps({
            "status": "download_requires_explicit_flag",
            "artifact_id": manifest["artifact_id"],
            "source": manifest["source"],
            "destination": str(destination),
            "download_bytes": sum(int(item["bytes"]) for item in manifest["files"]),
            "files": manifest["files"],
            "packaging_policy": manifest["packaging_policy"],
        }, ensure_ascii=False, indent=2))
        return 0
    if args.download:
        _download(destination, manifest)
    verification = verify_artifact(destination, manifest)
    if verification["status"] == "verified":
        verification["receipt"] = str(write_local_receipt(destination, verification))
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0 if verification["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
