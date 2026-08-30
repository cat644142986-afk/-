from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from python.model_artifacts import (
    LOCAL_RECEIPT_NAME,
    load_artifact_manifest,
    validate_external_destination,
    verify_artifact,
    write_local_receipt,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PINNED_MANIFEST = (
    PROJECT_ROOT / "docs" / "model-artifacts" / "grounding-dino-tiny.json"
)
OWLV2_PINNED_MANIFEST = (
    PROJECT_ROOT / "docs" / "model-artifacts" / "owlv2-base-patch16-ensemble.json"
)


def _entry(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


class ModelArtifactTests(unittest.TestCase):
    def test_pinned_manifest_is_reproducible_safe_and_optional_only(self) -> None:
        manifest = load_artifact_manifest(PINNED_MANIFEST)
        paths = {item["path"] for item in manifest["files"]}
        self.assertEqual(
            manifest["source"]["revision"],
            "a2bb814dd30d776dcf7e30523b00659f4f141c71",
        )
        self.assertEqual(manifest["source"]["license"], "apache-2.0")
        self.assertIn("model.safetensors", paths)
        self.assertNotIn("pytorch_model.bin", paths)
        self.assertEqual(manifest["distribution"], "optional-external-pack")
        self.assertTrue(manifest["packaging_policy"]["optional_external_pack"])
        self.assertFalse(manifest["packaging_policy"]["include_in_formal_sidecar"])
        self.assertFalse(manifest["packaging_policy"]["automatic_application_download"])
        grounding_requirements = (
            PROJECT_ROOT / "python" / "requirements-grounding.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("transformers==5.15.0", grounding_requirements)
        self.assertNotIn("requirements-grounding.txt", (
            PROJECT_ROOT / "python" / "requirements-build.txt"
        ).read_text(encoding="utf-8"))

    def test_owlv2_candidate_is_pinned_and_cannot_enter_a_release(self) -> None:
        manifest = load_artifact_manifest(OWLV2_PINNED_MANIFEST)
        paths = {item["path"] for item in manifest["files"]}
        self.assertEqual(
            manifest["source"]["revision"],
            "cfd3195ba4ea9592eec887ded089f4c08eff231d",
        )
        self.assertEqual(manifest["source"]["license"], "apache-2.0")
        self.assertEqual(manifest["distribution"], "development-baseline")
        self.assertTrue(manifest["packaging_policy"]["development_only"])
        self.assertFalse(manifest["packaging_policy"]["optional_external_pack"])
        self.assertFalse(manifest["packaging_policy"]["include_in_formal_sidecar"])
        self.assertFalse(manifest["packaging_policy"]["automatic_application_download"])
        self.assertIn("model.safetensors", paths)
        self.assertNotIn("pytorch_model.bin", paths)
        self.assertEqual(
            next(
                item["sha256"]
                for item in manifest["files"]
                if item["path"] == "model.safetensors"
            ),
            "e1e130b9e404cf91a75ad45644c1da9d7fa5284085eecc864266a6923efb99e7",
        )

    def test_destination_inside_repository_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the source repository"):
            validate_external_destination(PROJECT_ROOT / "models" / "fixture", PROJECT_ROOT)

    def test_verify_and_receipt_require_exact_size_and_sha256(self) -> None:
        config = b"{}"
        weights = b"safe-model-fixture"
        manifest = {
            "artifact_id": "fixture",
            "source": {"revision": "a" * 40, "license": "apache-2.0"},
            "files": [_entry("config.json", config), _entry("model.safetensors", weights)],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.json").write_bytes(config)
            (root / "model.safetensors").write_bytes(weights)
            verified = verify_artifact(root, manifest)
            self.assertEqual(verified["status"], "verified")
            receipt = write_local_receipt(root, verified)
            self.assertEqual(receipt.name, LOCAL_RECEIPT_NAME)
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["status"],
                "verified",
            )
            (root / "model.safetensors").write_bytes(b"tampered")
            invalid = verify_artifact(root, manifest)
            self.assertEqual(invalid["status"], "invalid")
            with self.assertRaisesRegex(ValueError, "unverified"):
                write_local_receipt(root, invalid)


if __name__ == "__main__":
    unittest.main()
