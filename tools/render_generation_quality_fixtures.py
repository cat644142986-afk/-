#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.generation_quality_eval import (  # noqa: E402
    load_quality_manifest,
    png_bytes,
    render_procedural_fixture,
    sha256_bytes,
)


DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "generation_quality" / "manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    manifest = load_quality_manifest(args.manifest)
    fixture_root = args.manifest.parent
    receipts = []
    for case in manifest["cases"]:
        source = case["source"]
        if source.get("type") != "procedural":
            continue
        payload = png_bytes(render_procedural_fixture(str(source["scene"])))
        digest = sha256_bytes(payload)
        expected = str(source.get("sha256") or "").lower()
        destination = fixture_root / str(source["path"])
        if expected and digest != expected:
            raise ValueError(f"fixture hash mismatch for {case['id']}: {digest} != {expected}")
        if args.write:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        elif not destination.is_file() or sha256_bytes(destination.read_bytes()) != digest:
            raise FileNotFoundError(f"fixture is missing or stale: {destination}")
        receipts.append({
            "id": case["id"],
            "path": str(destination.relative_to(ROOT)),
            "bytes": len(payload),
            "sha256": digest,
        })
    print(json.dumps(receipts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
