from __future__ import annotations

import copy
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_packaged_schema_upgrade as packaged_gate  # noqa: E402


class PackagedSchemaUpgradeFixtureTests(unittest.TestCase):
    def test_supported_release_fixtures_upgrade_once_and_preserve_backup(self) -> None:
        source_versions = (
            *packaged_gate.LEGACY_SOURCE_SCHEMA_VERSIONS,
            packaged_gate.FORMAL_SOURCE_SCHEMA_VERSION,
        )
        for source_version in source_versions:
            with self.subTest(source_version=source_version):
                with tempfile.TemporaryDirectory(
                    prefix=f"ProductAtelier-schema-v{source_version}-"
                ) as temporary_dir:
                    ledger_path = Path(temporary_dir) / "atelier.sqlite3"
                    packaged_gate._create_source_database(ledger_path, source_version)
                    source_content = packaged_gate._sqlite_content_snapshot(ledger_path)
                    expected_sentinel = f"schema-v{source_version}-content"
                    self.assertEqual(
                        packaged_gate._source_content_sentinel(ledger_path),
                        expected_sentinel,
                    )

                    first = packaged_gate.AtelierLedger(ledger_path)
                    backups = sorted(
                        ledger_path.parent.glob(
                            f"atelier.sqlite3.backup-v{source_version}-*.sqlite3"
                        )
                    )

                    self.assertEqual(
                        packaged_gate._schema_version(ledger_path),
                        packaged_gate.SCHEMA_VERSION,
                    )
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(first.last_migration_backup, backups[0])
                    self.assertEqual(
                        packaged_gate._schema_version(backups[0]), source_version
                    )
                    self.assertEqual(
                        packaged_gate._sqlite_content_snapshot(backups[0]),
                        source_content,
                    )
                    self.assertEqual(
                        packaged_gate._source_content_sentinel(backups[0]),
                        expected_sentinel,
                    )
                    self.assertEqual(
                        packaged_gate._source_content_sentinel(ledger_path),
                        expected_sentinel,
                    )
                    backup_sha256 = packaged_gate._sha256(backups[0])

                    second = packaged_gate.AtelierLedger(ledger_path)
                    backups_after_restart = sorted(
                        ledger_path.parent.glob(
                            "atelier.sqlite3.backup-v*-*.sqlite3"
                        )
                    )

                    self.assertIsNone(second.last_migration_backup)
                    self.assertEqual(backups_after_restart, backups)
                    self.assertEqual(
                        packaged_gate._sha256(backups[0]), backup_sha256
                    )
                    self.assertEqual(
                        packaged_gate._sqlite_content_snapshot(backups[0]),
                        source_content,
                    )
                    self.assertEqual(
                        packaged_gate._source_content_sentinel(ledger_path),
                        expected_sentinel,
                    )

    def test_fixture_builder_rejects_non_release_schema(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="ProductAtelier-schema-unsupported-"
        ) as temporary_dir:
            ledger_path = Path(temporary_dir) / "atelier.sqlite3"
            with self.assertRaisesRegex(ValueError, "unsupported.*v6"):
                packaged_gate._create_source_database(ledger_path, 6)


class PackagedSchemaUpgradeVideoGateTests(unittest.TestCase):
    job_id = "job:packaged-video"
    source_id = "asset:source"
    video_id = "asset:video"
    cover_id = "asset:cover"
    spatial_canvas_id = "spatial:packaged-video"

    @staticmethod
    def _jpeg_bytes() -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", packaged_gate.VIDEO_SIZE, (32, 64, 96)).save(
            buffer, format="JPEG", quality=90
        )
        return buffer.getvalue()

    def _evidence(self) -> dict:
        video_bytes = b"offline-packaged-video-fixture-v1"
        video_sha256 = hashlib.sha256(video_bytes).hexdigest()
        thumbnail_bytes = self._jpeg_bytes()
        video_asset = {
            "id": self.video_id,
            "role": "result_video",
            "kind": "video",
            "mime": "video/webm",
            "width": packaged_gate.VIDEO_SIZE[0],
            "height": packaged_gate.VIDEO_SIZE[1],
            "duration_seconds": packaged_gate.VIDEO_DURATION_SECONDS,
            "cover_asset_id": self.cover_id,
            "lineage_parent_id": self.source_id,
            "metadata": {
                "contract_version": "image-to-video-v1",
                "provider": "offline-preview-v1",
                "offline_preview": True,
                "automatic_paid_retry": False,
            },
            "content_url": f"/api/assets/{self.video_id}/content",
            "stream_url": f"/api/assets/{self.video_id}/content",
            "download_url": f"/api/assets/{self.video_id}/content?download=true",
            "thumbnail_url": f"/api/assets/{self.video_id}/thumbnail",
            "cover_url": f"/api/assets/{self.cover_id}/thumbnail",
            "sha256": video_sha256,
            "size_bytes": len(video_bytes),
        }
        cover_asset = {
            "id": self.cover_id,
            "role": "result_video_cover",
            "kind": "image",
            "mime": "image/jpeg",
            "width": packaged_gate.VIDEO_SIZE[0],
            "height": packaged_gate.VIDEO_SIZE[1],
            "lineage_parent_id": self.source_id,
            "metadata": {"auxiliary_result": True},
        }
        job = {
            "id": self.job_id,
            "status": "completed",
            "progress": 1.0,
            "requested_concurrency": 1,
            "snapshot": {
                "command_id": "command:image-to-video",
                "source_asset_ids": [self.source_id],
                "parameters": {"spatial_canvas_id": self.spatial_canvas_id},
            },
            "parameters": {
                "contract_version": "image-to-video-v1",
                "output_ratio": "16:9",
                "duration_seconds": packaged_gate.VIDEO_DURATION_SECONDS,
                "motion_intensity": 3,
                "first_frame_asset_id": self.source_id,
                "last_frame_asset_id": None,
                "provider": "offline-preview-v1",
                "provider_call_confirmed": False,
                "automatic_paid_retry": False,
                "spatial_canvas_id": self.spatial_canvas_id,
            },
            "items": [
                {
                    "status": "completed",
                    "source_asset_id": self.source_id,
                    "attempt_count": 1,
                    "max_attempts": 1,
                    "attempts": [{"status": "completed"}],
                    "result_asset_ids": [self.video_id, self.cover_id],
                }
            ],
        }
        progress = {
            "task_id": self.job_id,
            "status": "completed",
            "progress": 1.0,
            "results": {
                "main": [],
                "cutout": [],
                "video": [{"id": self.video_id, "role": "result_video"}],
            },
        }
        traces = {
            "traces": [
                {
                    "stage": "provider.video.offline-preview",
                    "status": "completed",
                    "output": {
                        "video_sha256": video_sha256,
                        "network_call_count": 0,
                    },
                }
            ]
        }
        return {
            "job": job,
            "progress": progress,
            "traces": traces,
            "video_asset": video_asset,
            "cover_asset": cover_asset,
            "inline": (
                200,
                {
                    "content-type": "video/webm",
                    "content-disposition": 'inline; filename="preview.webm"',
                    "content-length": str(len(video_bytes)),
                },
                video_bytes,
            ),
            "download": (
                200,
                {
                    "content-type": "video/webm",
                    "content-disposition": 'attachment; filename="preview.webm"',
                    "content-length": str(len(video_bytes)),
                },
                video_bytes,
            ),
            "thumbnail": (
                200,
                {
                    "content-type": "image/jpeg",
                    "content-disposition": 'inline; filename="cover.jpg"',
                    "content-length": str(len(thumbnail_bytes)),
                },
                thumbnail_bytes,
            ),
        }

    def _validate(self, evidence: dict) -> dict:
        return packaged_gate._validate_packaged_video_evidence(
            evidence,
            phase="synthetic",
            expected_job_id=self.job_id,
            expected_source_id=self.source_id,
            expected_spatial_canvas_id=self.spatial_canvas_id,
        )

    def test_complete_video_evidence_returns_release_metrics(self) -> None:
        evidence = self._evidence()

        metrics = self._validate(evidence)

        self.assertEqual(metrics["job_id"], self.job_id)
        self.assertEqual(metrics["video_asset_id"], self.video_id)
        self.assertEqual(metrics["cover_asset_id"], self.cover_id)
        self.assertEqual(metrics["result_asset_count"], 2)
        self.assertEqual(metrics["attempt_count"], 1)
        self.assertEqual(metrics["provider_trace_count"], 1)
        self.assertEqual(metrics["network_call_count"], 0)
        self.assertEqual(metrics["thumbnail_size"], list(packaged_gate.VIDEO_SIZE))
        self.assertEqual(
            metrics["video_sha256"], evidence["video_asset"]["sha256"].upper()
        )

    def test_progress_must_not_expose_the_auxiliary_cover(self) -> None:
        evidence = self._evidence()
        evidence["progress"]["results"]["video"].append(
            {"id": self.cover_id, "role": "result_video_cover"}
        )

        with self.assertRaisesRegex(RuntimeError, "progress projection"):
            self._validate(evidence)

    def test_binary_and_sha256_drift_are_rejected(self) -> None:
        mutations = {
            "download bytes": lambda evidence: evidence.__setitem__(
                "download", (*evidence["download"][:2], b"different-video-bytes")
            ),
            "asset sha256": lambda evidence: evidence["video_asset"].__setitem__(
                "sha256", "0" * 64
            ),
            "trace sha256": lambda evidence: evidence["traces"]["traces"][0][
                "output"
            ].__setitem__("video_sha256", "f" * 64),
        }

        for label, mutate in mutations.items():
            with self.subTest(label=label):
                evidence = self._evidence()
                mutate(evidence)
                with self.assertRaises(RuntimeError):
                    self._validate(evidence)

    def test_restart_requires_stable_json_and_binary_evidence(self) -> None:
        mutations = {
            "job": lambda evidence: evidence["job"].__setitem__(
                "status", "failed"
            ),
            "asset": lambda evidence: evidence["video_asset"].__setitem__(
                "id", "asset:video-replaced"
            ),
            "progress": lambda evidence: evidence["progress"].__setitem__(
                "progress", 0.5
            ),
            "trace": lambda evidence: evidence["traces"]["traces"][0].__setitem__(
                "status", "failed"
            ),
            "binary": lambda evidence: evidence.__setitem__(
                "inline", (*evidence["inline"][:2], b"changed-after-restart")
            ),
        }

        first = self._evidence()
        metrics = packaged_gate._validate_packaged_video_restart(
            first, copy.deepcopy(first)
        )
        self.assertTrue(metrics["job_identity_preserved"])
        self.assertTrue(metrics["asset_identity_preserved"])
        self.assertTrue(metrics["sha256_preserved"])

        for label, mutate in mutations.items():
            with self.subTest(label=label):
                second = copy.deepcopy(first)
                mutate(second)
                with self.assertRaises(RuntimeError):
                    packaged_gate._validate_packaged_video_restart(first, second)


if __name__ == "__main__":
    unittest.main()
