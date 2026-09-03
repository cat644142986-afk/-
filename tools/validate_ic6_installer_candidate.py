#!/usr/bin/env python3
"""Build immutable IC6 candidate evidence for the installer entry point."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence


def load_portable_release_tool(tool_path: str | Path) -> ModuleType:
    resolved_tool = Path(tool_path).expanduser().resolve(strict=True)
    spec = importlib.util.spec_from_file_location("ic6_portable_release", resolved_tool)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load candidate validator: {resolved_tool}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_candidate_evidence(
    release_module: ModuleType,
    *,
    project_root: str | Path,
    candidate_dir: str | Path,
    expected_git_commit: str,
    expected_candidate_identity_sha256: str | None = None,
    packaging_sidecar: str | Path | None = None,
) -> dict[str, Any]:
    project_path = Path(project_root)
    candidate_path = Path(candidate_dir)
    verified = release_module.verify_candidate_identity(
        project_root=project_path,
        candidate_dir=candidate_path,
        expected_git_commit=expected_git_commit,
        expected_candidate_identity_sha256=expected_candidate_identity_sha256,
        _lock_held=True,
    )
    canonical = {
        "candidate": verified["candidate"],
        "candidate_sidecar": release_module.directory_inventory(
            candidate_path / "python-server"
        ),
    }
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    payload = {
        "canonical": canonical,
        "canonical_fingerprint": hashlib.sha256(
            canonical_json.encode("utf-8")
        ).hexdigest().upper(),
        "candidate_identity": verified["identity_receipt"],
    }
    if packaging_sidecar is not None:
        payload["packaging_sidecar"] = release_module.directory_inventory(
            Path(packaging_sidecar)
        )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-release-tool", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-candidate-identity-sha256")
    parser.add_argument("--packaging-sidecar")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    release_module = load_portable_release_tool(args.portable_release_tool)
    payload = build_candidate_evidence(
        release_module,
        project_root=args.project_root,
        candidate_dir=args.candidate_dir,
        expected_git_commit=args.expected_git_commit,
        expected_candidate_identity_sha256=(
            args.expected_candidate_identity_sha256
        ),
        packaging_sidecar=args.packaging_sidecar,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
