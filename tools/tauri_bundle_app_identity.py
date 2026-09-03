#!/usr/bin/env python3
"""Derive and validate Tauri's Windows bundle-type marker identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ALGORITHM_VERSION = "tauri-bundler-v2-bundle-type-marker-v1"
UNKNOWN_MARKER = b"__TAURI_BUNDLE_TYPE_VAR_UNK"
NSIS_MARKER = b"__TAURI_BUNDLE_TYPE_VAR_NSS"
MSI_MARKER = b"__TAURI_BUNDLE_TYPE_VAR_MSI"
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class BundleIdentityError(ValueError):
    """Raised when an app cannot satisfy the Tauri bundle identity contract."""


def _sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _marker_counts(data: bytes | bytearray) -> dict[str, int]:
    return {
        "unknown": data.count(UNKNOWN_MARKER),
        "nsis": data.count(NSIS_MARKER),
        "msi": data.count(MSI_MARKER),
    }


def derive_nsis_identity_bytes(data: bytes) -> dict[str, Any]:
    """Return the only valid NSIS-installed identity for an unbundled app."""

    counts = _marker_counts(data)
    if counts != {"unknown": 1, "nsis": 0, "msi": 0}:
        raise BundleIdentityError(
            "Unbundled app must contain exactly one UNK marker and no NSS/MSI "
            f"markers; found {counts}"
        )

    marker_offset = data.index(UNKNOWN_MARKER)
    installed = bytearray(data)
    marker_end = marker_offset + len(UNKNOWN_MARKER)
    installed[marker_offset:marker_end] = NSIS_MARKER
    if len(installed) != len(data):
        raise BundleIdentityError("Tauri marker replacement changed the app size")

    changed_offsets = [
        index
        for index, (before, after) in enumerate(zip(data, installed))
        if before != after
    ]
    expected_changed_offsets = [
        marker_offset + index
        for index, (before, after) in enumerate(zip(UNKNOWN_MARKER, NSIS_MARKER))
        if before != after
    ]
    if changed_offsets != expected_changed_offsets or len(changed_offsets) != 3:
        raise BundleIdentityError(
            "Tauri UNK to NSS replacement must change exactly the expected three bytes"
        )

    installed_counts = _marker_counts(installed)
    if installed_counts != {"unknown": 0, "nsis": 1, "msi": 0}:
        raise BundleIdentityError(
            f"Derived NSIS app has an invalid bundle marker state: {installed_counts}"
        )

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "source_app_sha256": _sha256(data),
        "source_app_size_bytes": len(data),
        "expected_installed_app_sha256": _sha256(installed),
        "expected_installed_app_size_bytes": len(installed),
        "marker_offset": marker_offset,
        "source_marker": UNKNOWN_MARKER.decode("ascii"),
        "installed_marker": NSIS_MARKER.decode("ascii"),
        "changed_byte_count": len(changed_offsets),
        "changed_byte_offsets": changed_offsets,
        "source_marker_counts": counts,
        "expected_installed_marker_counts": installed_counts,
    }


def derive_nsis_identity(path: str | Path) -> dict[str, Any]:
    app_path = Path(path)
    if not app_path.is_file():
        raise BundleIdentityError(f"App is missing: {app_path}")
    return derive_nsis_identity_bytes(app_path.read_bytes())


def validate_installed_nsis_bytes(data: bytes, expected_sha256: str) -> dict[str, Any]:
    """Validate an installed app against the precomputed NSIS identity."""

    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise BundleIdentityError(
            "Expected installed app SHA-256 must be 64 hex characters"
        )
    counts = _marker_counts(data)
    if counts != {"unknown": 0, "nsis": 1, "msi": 0}:
        raise BundleIdentityError(
            "Installed NSIS app must contain exactly one NSS marker and no UNK/MSI "
            f"markers; found {counts}"
        )
    actual_sha256 = _sha256(data)
    expected = expected_sha256.upper()
    if actual_sha256 != expected:
        raise BundleIdentityError(
            "Installed NSIS app SHA-256 does not match the precomputed identity: "
            f"expected {expected}, got {actual_sha256}"
        )
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "installed_app_sha256": actual_sha256,
        "installed_app_size_bytes": len(data),
        "marker_offset": data.index(NSIS_MARKER),
        "marker_counts": counts,
    }


def validate_installed_nsis(path: str | Path, expected_sha256: str) -> dict[str, Any]:
    app_path = Path(path)
    if not app_path.is_file():
        raise BundleIdentityError(f"Installed app is missing: {app_path}")
    return validate_installed_nsis_bytes(app_path.read_bytes(), expected_sha256)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("source", "installed"))
    parser.add_argument("--app", required=True)
    parser.add_argument("--expected-sha256")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.mode == "source":
            if args.expected_sha256:
                raise BundleIdentityError(
                    "--expected-sha256 is only valid in installed mode"
                )
            payload = derive_nsis_identity(args.app)
        else:
            if not args.expected_sha256:
                raise BundleIdentityError(
                    "--expected-sha256 is required in installed mode"
                )
            payload = validate_installed_nsis(args.app, args.expected_sha256)
    except (BundleIdentityError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
