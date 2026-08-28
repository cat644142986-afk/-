#!/usr/bin/env python3
"""Create an offline result-review checkpoint in a new isolated data directory."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from asset_store import AssetStore  # noqa: E402
from atelier_ledger import AtelierLedger, idempotent_id  # noqa: E402
from memory_engine import MemoryEngine  # noqa: E402


def image_bytes(color: tuple[int, int, int], size: tuple[int, int] = (640, 640)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def seed_job(
    ledger: AtelierLedger,
    store: AssetStore,
    output_dir: Path,
    index: int,
) -> tuple[dict, str, str, str]:
    source_data = image_bytes((220, 90 + index * 20, 45))
    source = store.import_bytes(source_data, f"checkpoint-source-{index}.png", "product")
    job, _ = ledger.create_job(
        "single",
        [source["id"]],
        engine_key="offline-checkpoint",
        parameters={
            "model": "offline-checkpoint",
            "brief": {"objective": "验证结果与知识建议双向追溯"},
        },
        idempotency_key=f"feedback-checkpoint-job-{index}",
        title=f"反馈追溯验收 {index}",
    )
    item = job["items"][0]
    ledger.claim_job_item(item["id"])
    result_data = image_bytes((40 + index * 25, 135, 190))
    result_path = output_dir / f"checkpoint-result-{index}.png"
    result_path.write_bytes(result_data)
    result_id = ledger.commit_generation_results(
        item["generation_id"],
        source["id"],
        [{
            "path": str(result_path),
            "name": result_path.name,
            "role": "result_main",
            "mime": "image/png",
            "width": 640,
            "height": 640,
            "sha256": hashlib.sha256(result_data).hexdigest(),
            "metadata": {"offline_checkpoint": True},
        }],
        job_item_id=item["id"],
    )[0]
    return job, item["generation_id"], result_id, source["id"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise SystemExit(f"Refusing to seed a non-empty directory: {data_dir}")
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir = data_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = AtelierLedger(data_dir / "atelier.sqlite3")
    store = AssetStore(data_dir / "assets", ledger)
    memory = MemoryEngine(ledger)

    seeded = []
    for index in (1, 2):
        job, generation_id, result_id, source_id = seed_job(
            ledger, store, output_dir, index
        )
        review = ledger.submit_result_review(
            f"feedback-checkpoint-review-{index}",
            job_id=job["id"],
            generation_id=generation_id,
            result_asset_id=result_id,
            decision="adjust",
            reason_codes=["packaging_text"],
            note="包装文字变形",
            learning_action="record" if index == 1 else "suggest",
        )
        ledger.add_feedback(
            job["session_id"],
            "adjusted",
            generation_id=generation_id,
            asset_id=result_id,
            reason="包装文字变形",
            structured={
                "review_id": review["id"],
                "job_id": job["id"],
                "mode": job["mode"],
                "result_asset_id": result_id,
                "reason_codes": ["packaging_text"],
            },
            scope="result",
            feedback_id=idempotent_id("fb", f"result-review:{review['id']}"),
        )
        seeded.append({
            "job_id": job["id"],
            "session_id": job["session_id"],
            "generation_id": generation_id,
            "result_asset_id": result_id,
            "source_asset_id": source_id,
            "review_id": review["id"],
        })

    synthesis = memory.synthesize()
    suggestion = synthesis["suggestions"][0]
    active = seeded[-1]
    draft = ledger.save_workflow_draft(
        "single",
        expected_revision=1,
        selected_asset_ids=[active["source_asset_id"]],
        brief={"objective": "验证结果与知识建议双向追溯"},
        parameters={"model": "offline-checkpoint"},
        active_job_id=active["job_id"],
        current_generation_id=active["generation_id"],
        current_result_asset_id=active["result_asset_id"],
        ui_state={"checkpoint": "feedback-bidirectional"},
    )
    print(json.dumps({
        "data_dir": str(data_dir),
        "jobs": seeded,
        "suggestion_id": suggestion["id"],
        "draft_revision": draft["revision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
