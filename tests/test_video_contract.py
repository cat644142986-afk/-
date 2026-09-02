from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from python.video_contract import (
    IMAGE_TO_VIDEO_CONTRACT_VERSION,
    OFFLINE_VIDEO_PROVIDER,
    VideoContractError,
    normalize_image_to_video_parameters,
)
from tools.generate_offline_video_fixtures import read_webm_duration_seconds


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "python" / "video_fixtures" / OFFLINE_VIDEO_PROVIDER


class ImageToVideoContractTests(unittest.TestCase):
    def test_freezes_all_first_phase_parameters_without_paid_retry(self) -> None:
        contract = normalize_image_to_video_parameters(
            {
                "prompt": "镜头缓慢向前推进，包装保持稳定",
                "output_ratio": "16:9",
                "duration_seconds": 5,
                "motion_intensity": 4,
                "first_frame_asset_id": "ast_first",
                "last_frame_asset_id": "ast_last",
                "provider": OFFLINE_VIDEO_PROVIDER,
                "provider_call_confirmed": False,
                "automatic_paid_retry": False,
            },
            source_asset_id="ast_first",
        )

        self.assertEqual(contract["contract_version"], IMAGE_TO_VIDEO_CONTRACT_VERSION)
        self.assertEqual(contract["prompt"], "镜头缓慢向前推进，包装保持稳定")
        self.assertEqual(contract["output_ratio"], "16:9")
        self.assertEqual(contract["duration_seconds"], 5)
        self.assertEqual(contract["motion_intensity"], 4)
        self.assertEqual(contract["first_frame_asset_id"], "ast_first")
        self.assertEqual(contract["last_frame_asset_id"], "ast_last")
        self.assertEqual(contract["provider"], OFFLINE_VIDEO_PROVIDER)
        self.assertFalse(contract["provider_call_confirmed"])
        self.assertFalse(contract["automatic_paid_retry"])

    def test_defaults_remain_a_real_frozen_contract(self) -> None:
        contract = normalize_image_to_video_parameters(
            {"prompt": "商品轻微旋转并保持包装文字清晰"},
            source_asset_id="ast_source",
        )

        self.assertEqual(contract["output_ratio"], "1:1")
        self.assertEqual(contract["duration_seconds"], 5)
        self.assertEqual(contract["motion_intensity"], 3)
        self.assertEqual(contract["first_frame_asset_id"], "ast_source")
        self.assertIsNone(contract["last_frame_asset_id"])
        self.assertEqual(contract["provider"], OFFLINE_VIDEO_PROVIDER)
        self.assertFalse(contract["provider_call_confirmed"])
        self.assertFalse(contract["automatic_paid_retry"])

    def test_rejects_unfrozen_or_unsupported_values(self) -> None:
        invalid = (
            ({"prompt": "x"}, "VIDEO_PROMPT_INVALID"),
            ({"prompt": "有效描述", "output_ratio": "2:1"}, "VIDEO_RATIO_UNSUPPORTED"),
            ({"prompt": "有效描述", "duration_seconds": 7}, "VIDEO_DURATION_UNSUPPORTED"),
            ({"prompt": "有效描述", "duration_seconds": 5.9}, "VIDEO_DURATION_UNSUPPORTED"),
            ({"prompt": "有效描述", "duration_seconds": True}, "VIDEO_DURATION_UNSUPPORTED"),
            ({"prompt": "有效描述", "duration_seconds": "5"}, "VIDEO_DURATION_UNSUPPORTED"),
            ({"prompt": "有效描述", "duration_seconds": "5.0"}, "VIDEO_DURATION_UNSUPPORTED"),
            ({"prompt": "有效描述", "motion_intensity": 11}, "VIDEO_MOTION_INVALID"),
            ({"prompt": "有效描述", "provider": "unapproved-cloud"}, "VIDEO_PROVIDER_UNSUPPORTED"),
            ({"prompt": "有效描述", "provider_call_confirmed": True}, "VIDEO_COST_POLICY_INVALID"),
            ({"prompt": "有效描述", "automatic_paid_retry": True}, "VIDEO_COST_POLICY_INVALID"),
            (
                {"prompt": "有效描述", "contract_version": "image-to-video-v0"},
                "VIDEO_CONTRACT_VERSION_MISMATCH",
            ),
            (
                {"prompt": "有效描述", "first_frame_asset_id": "ast_other"},
                "VIDEO_FIRST_FRAME_MISMATCH",
            ),
        )
        for parameters, code in invalid:
            with self.subTest(parameters=parameters), self.assertRaises(VideoContractError) as raised:
                normalize_image_to_video_parameters(parameters, source_asset_id="ast_source")
            self.assertEqual(raised.exception.code, code)

    def test_rejects_unknown_fields_so_snapshot_drift_is_visible(self) -> None:
        with self.assertRaises(VideoContractError) as raised:
            normalize_image_to_video_parameters(
                {"prompt": "有效的视频描述", "silent_provider_fallback": True},
                source_asset_id="ast_source",
            )
        self.assertEqual(raised.exception.code, "VIDEO_PARAMETERS_INVALID")

    def test_rejects_paths_data_uris_and_base64_as_frame_references(self) -> None:
        invalid_references = (
            r"C:\Users\64414\Desktop\first.png",
            "/tmp/first.png",
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
            "A" * 120,
        )
        for reference in invalid_references:
            with self.subTest(reference=reference), self.assertRaises(VideoContractError) as raised:
                normalize_image_to_video_parameters(
                    {
                        "prompt": "保持商品外观并轻微推进镜头",
                        "last_frame_asset_id": reference,
                    },
                    source_asset_id="ast_source",
                )
            self.assertEqual(raised.exception.code, "VIDEO_FRAME_INVALID")

    def test_offline_fixture_matrix_has_finite_container_durations(self) -> None:
        fixtures = sorted(FIXTURE_ROOT.glob("*/*.webm"))
        self.assertEqual(len(fixtures), 20)
        self.assertLess(sum(path.stat().st_size for path in fixtures), 1_000_000)
        expected_ratios = {"1x1", "16x9", "9x16", "4x3", "3x4"}
        self.assertEqual({path.parent.name for path in fixtures}, expected_ratios)
        for path in fixtures:
            with self.subTest(path=path):
                expected_seconds = int(path.stem.removesuffix("s"))
                self.assertIn(expected_seconds, {3, 5, 8, 10})
                self.assertAlmostEqual(
                    read_webm_duration_seconds(path.read_bytes()),
                    expected_seconds,
                    places=3,
                )

    def test_offline_fixture_manifest_locks_bytes_and_media_contract(self) -> None:
        manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        fixtures = manifest["fixtures"]
        self.assertEqual(manifest["provider"], OFFLINE_VIDEO_PROVIDER)
        self.assertEqual(manifest["fixture_count"], 20)
        self.assertEqual(len(fixtures), 20)
        self.assertEqual(
            manifest["total_bytes"],
            sum(int(item["size_bytes"]) for item in fixtures),
        )
        expected_dimensions = {
            "1x1": (320, 320),
            "16x9": (320, 180),
            "9x16": (180, 320),
            "4x3": (320, 240),
            "3x4": (240, 320),
        }
        for item in fixtures:
            with self.subTest(path=item["path"]):
                path = FIXTURE_ROOT / item["path"]
                content = path.read_bytes()
                self.assertEqual(item["size_bytes"], len(content))
                self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(
                    (item["width"], item["height"]),
                    expected_dimensions[path.parent.name],
                )
                self.assertAlmostEqual(
                    read_webm_duration_seconds(content),
                    item["duration_seconds"],
                    places=3,
                )


if __name__ == "__main__":
    unittest.main()
