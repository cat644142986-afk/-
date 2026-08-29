from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.semantic_grounding_eval import load_grounding_manifest  # noqa: E402


DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "semantic_grounding_openimages"
    / "manifest.json"
)
ALLOWED_IMAGE_HOSTS = {
    "open-images-dataset.s3.amazonaws.com",
    "storage.googleapis.com",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_path(destination: Path, image: Mapping[str, Any]) -> Path:
    relative = Path(str(image["path"]))
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise ValueError(f"unsafe corpus image path: {relative}")
    return destination / relative


def _verify_image(path: Path, image: Mapping[str, Any]) -> str:
    if not path.is_file():
        return "missing"
    if path.stat().st_size != int(image["bytes"]):
        return "wrong_size"
    if _sha256(path) != str(image["sha256"]):
        return "wrong_hash"
    with Image.open(path) as opened:
        if list(opened.size) != [int(item) for item in image["canvas"]]:
            return "wrong_dimensions"
    return "verified"


def inspect_corpus(manifest: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    statuses: dict[str, str] = {}
    for image_id, image in manifest["images"].items():
        statuses[image_id] = _verify_image(
            _target_path(destination, image), image
        )
    return {
        "verified": sorted(key for key, value in statuses.items() if value == "verified"),
        "missing": sorted(key for key, value in statuses.items() if value == "missing"),
        "invalid": {
            key: value
            for key, value in sorted(statuses.items())
            if value not in {"verified", "missing"}
        },
    }


def _download_one(
    image_id: str,
    image: Mapping[str, Any],
    destination: Path,
) -> tuple[str, str]:
    target = _target_path(destination, image)
    current = _verify_image(target, image)
    if current == "verified":
        return image_id, "reused"
    if current != "missing":
        raise ValueError(
            f"{target} is {current}; move it aside before downloading a locked replacement"
        )
    url = str(image["source_file"])
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise ValueError(f"untrusted corpus image URL for {image_id}")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "ProductAtelier-EvalCorpus/1.0"},
        )
        with urllib.request.urlopen(request, timeout=90) as response, part.open("xb") as output:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme != "https" or final_url.hostname not in ALLOWED_IMAGE_HOSTS:
                raise ValueError(f"untrusted redirect while downloading {image_id}")
            expected_bytes = int(image["bytes"])
            advertised = response.headers.get("Content-Length")
            if advertised is not None and int(advertised) != expected_bytes:
                raise ValueError(f"download size header does not match the lock for {image_id}")
            written = 0
            while True:
                chunk = response.read(min(1024 * 1024, expected_bytes - written + 1))
                if not chunk:
                    break
                written += len(chunk)
                if written > expected_bytes:
                    raise ValueError(f"download exceeded the locked size for {image_id}")
                output.write(chunk)
            if written != expected_bytes:
                raise ValueError(f"download ended before the locked size for {image_id}")
        status = _verify_image(part, image)
        if status != "verified":
            raise ValueError(f"downloaded {image_id} failed its lock: {status}")
        if target.exists():
            if _verify_image(target, image) == "verified":
                return image_id, "reused"
            raise ValueError(f"target appeared during download and is not verified: {target}")
        part.replace(target)
        return image_id, "downloaded"
    finally:
        part.unlink(missing_ok=True)


def _write_receipt(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    destination: Path,
) -> Path:
    inspection = inspect_corpus(manifest, destination)
    if inspection["missing"] or inspection["invalid"]:
        raise ValueError("cannot write a receipt for an incomplete corpus")
    payload = {
        "schema_version": "1.0",
        "corpus_id": manifest["corpus_id"],
        "manifest_sha256": _sha256(manifest_path),
        "image_count": len(inspection["verified"]),
        "total_bytes": sum(
            int(item["bytes"]) for item in manifest["images"].values()
        ),
        "files": [
            {
                "image_id": image_id,
                "path": manifest["images"][image_id]["path"],
                "sha256": manifest["images"][image_id]["sha256"],
            }
            for image_id in inspection["verified"]
        ],
    }
    destination.mkdir(parents=True, exist_ok=True)
    receipt = destination / "_receipt.json"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=".receipt-",
        suffix=".json",
        dir=destination,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect, download, or verify Product Atelier's locked Open Images "
            "semantic-grounding evaluation corpus."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--destination",
        type=Path,
        help="Defaults to build/eval-corpora/<corpus_id>.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    manifest = load_grounding_manifest(args.manifest)
    if manifest["corpus_kind"] != "licensed-photo-downloadable":
        parser.error("bootstrap only supports licensed-photo-downloadable corpora")
    destination = (
        args.destination
        or PROJECT_ROOT / "build" / "eval-corpora" / str(manifest["corpus_id"])
    ).resolve()
    if args.workers < 1 or args.workers > 16:
        parser.error("--workers must be between 1 and 16")

    if args.download:
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(_download_one, image_id, image, destination): image_id
                for image_id, image in manifest["images"].items()
            }
            for future in as_completed(futures):
                image_id, status = future.result()
                results.append({"image_id": image_id, "status": status})
        receipt = _write_receipt(args.manifest, manifest, destination)
        print(json.dumps({
            "status": "ready",
            "corpus_id": manifest["corpus_id"],
            "destination": str(destination),
            "downloaded": sum(item["status"] == "downloaded" for item in results),
            "reused": sum(item["status"] == "reused" for item in results),
            "receipt": str(receipt),
        }, ensure_ascii=False, indent=2))
        return 0

    inspection = inspect_corpus(manifest, destination)
    complete = not inspection["missing"] and not inspection["invalid"]
    print(json.dumps({
        "status": "verified" if complete else "incomplete",
        "corpus_id": manifest["corpus_id"],
        "destination": str(destination),
        "verified_count": len(inspection["verified"]),
        "missing": inspection["missing"],
        "invalid": inspection["invalid"],
    }, ensure_ascii=False, indent=2))
    if args.verify and not complete:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
