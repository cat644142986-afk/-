from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_ic6_installer_candidate.py"


class Ic6InstallerCandidateValidatorTests(unittest.TestCase):
    def test_cli_emits_hash_bound_candidate_and_packaging_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory) / "path with spaces"
            project_root = temp_root / "project root"
            candidate_dir = project_root / "build" / "portable-candidate-current"
            packaging_sidecar = temp_root / "packaging sidecar"
            candidate_dir.joinpath("python-server").mkdir(parents=True)
            packaging_sidecar.mkdir(parents=True)
            fake_release_tool = temp_root / "portable release fake.py"
            fake_release_tool.write_text(
                textwrap.dedent(
                    """
                    from pathlib import Path


                    def directory_inventory(root):
                        return {"root": str(Path(root)), "tree_sha256": "C" * 64}


                    def verify_candidate_identity(
                        *,
                        project_root,
                        candidate_dir,
                        expected_git_commit,
                        expected_candidate_identity_sha256,
                        _lock_held,
                    ):
                        return {
                            "candidate": {
                                "project_root": str(project_root),
                                "candidate_dir": str(candidate_dir),
                                "git_commit": expected_git_commit,
                                "expected_identity": expected_candidate_identity_sha256,
                                "lock_held": _lock_held,
                            },
                            "identity_receipt": {
                                "sha256": expected_candidate_identity_sha256,
                                "receipt": {"format_version": 1, "kind": "test"},
                            },
                        }
                    """
                ),
                encoding="utf-8",
            )
            git_commit = "a" * 40
            identity_sha256 = "B" * 64

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--portable-release-tool",
                    str(fake_release_tool),
                    "--project-root",
                    str(project_root),
                    "--candidate-dir",
                    str(candidate_dir),
                    "--expected-git-commit",
                    git_commit,
                    "--expected-candidate-identity-sha256",
                    identity_sha256,
                    "--packaging-sidecar",
                    str(packaging_sidecar),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            candidate = payload["canonical"]["candidate"]
            self.assertEqual(candidate["project_root"], str(project_root))
            self.assertEqual(candidate["candidate_dir"], str(candidate_dir))
            self.assertEqual(candidate["git_commit"], git_commit)
            self.assertEqual(candidate["expected_identity"], identity_sha256)
            self.assertTrue(candidate["lock_held"])
            self.assertEqual(payload["candidate_identity"]["sha256"], identity_sha256)
            self.assertEqual(
                payload["canonical"]["candidate_sidecar"]["root"],
                str(candidate_dir / "python-server"),
            )
            self.assertEqual(
                payload["packaging_sidecar"]["root"],
                str(packaging_sidecar),
            )
            canonical_json = json.dumps(
                payload["canonical"], sort_keys=True, separators=(",", ":")
            )
            self.assertEqual(
                payload["canonical_fingerprint"],
                hashlib.sha256(canonical_json.encode("utf-8")).hexdigest().upper(),
            )

    def test_cli_omits_optional_packaging_sidecar_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project_root = temp_root / "project"
            candidate_dir = project_root / "build" / "portable-candidate-current"
            candidate_dir.joinpath("python-server").mkdir(parents=True)
            fake_release_tool = temp_root / "portable_release.py"
            fake_release_tool.write_text(
                textwrap.dedent(
                    """
                    def directory_inventory(root):
                        return {"tree_sha256": "D" * 64}


                    def verify_candidate_identity(**arguments):
                        return {
                            "candidate": {
                                "expected_identity": arguments[
                                    "expected_candidate_identity_sha256"
                                ]
                            },
                            "identity_receipt": {"sha256": "E" * 64},
                        }
                    """
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--portable-release-tool",
                    str(fake_release_tool),
                    "--project-root",
                    str(project_root),
                    "--candidate-dir",
                    str(candidate_dir),
                    "--expected-git-commit",
                    "f" * 40,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIsNone(
                payload["canonical"]["candidate"]["expected_identity"]
            )
            self.assertNotIn("packaging_sidecar", payload)


if __name__ == "__main__":
    unittest.main()
