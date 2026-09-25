#!/usr/bin/env python3
"""Seed isolated, non-provider Growth System evidence for packaged acceptance."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.atelier_ledger import AtelierLedger


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zk9sAAAAASUVORK5CYII="
)


def seed(data_dir: Path, knowledge_root: Path) -> dict[str, str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    lesson_dir = knowledge_root / "20 知识库" / "经验教训"
    lesson_dir.mkdir(parents=True, exist_ok=True)
    lesson = lesson_dir / "PACKAGED-GROWTH-饮料主图经验.md"
    lesson.write_text(
        """---
id: LES-PACKAGED-GROWTH
page_type: lesson
created: 2026-09-24
updated: 2026-09-24
review: approved
summary: 饮料主图使用克制留白和清楚包装文字
project_scope: [PA Tea Launch]
source_ids: []
tags: [食品, 饮料, 主图]
---
# 饮料主图经验

- 包装四周保留克制留白，并保持文字和 Logo 清楚可读（PAGROWTHKNOWLEDGEMARKER）

关联 [[品牌调性维度|品牌调性]]。
""",
        encoding="utf-8",
    )

    ledger = AtelierLedger(data_dir / "atelier.sqlite3")
    prior = ledger.create_session(
        "single",
        title="山茶饮料历史主图",
        project_name="PA Tea Launch",
        brand_profile="PA Tea",
        category="food",
        brief={"product_name": "山茶饮料", "output_kind": "ecommerce-main"},
    )
    prior_source = ledger.add_asset(
        prior["id"],
        "source",
        name="tea-prior-source.png",
        mime="image/png",
        data=PNG_1X1,
    )
    generation = ledger.add_generation(
        prior["id"],
        model="offline-growth-fixture",
        prompt="历史山茶饮料主图",
        parameters={"output_kind": "ecommerce-main", "style": "clean-commercial"},
        status="completed",
    )
    result = ledger.add_asset(
        prior["id"],
        "result_main",
        parent_asset_id=prior_source["id"],
        name="tea-prior-result.png",
        mime="image/png",
        data=PNG_1X1,
        metadata={"generation_id": generation["id"], "fixture": "growth-system-v1"},
    )
    ledger.update_generation(
        generation["id"],
        result_asset_ids=[result["id"]],
    )
    feedback = ledger.add_feedback(
        prior["id"],
        "adopted",
        generation_id=generation["id"],
        asset_id=result["id"],
        reason="采用克制留白和自然柔光，包装文字保持清楚（PAGROWTHCASEMARKER）",
        structured={
            "result_asset_id": result["id"],
            "reason_codes": ["composition_ready", "packaging_clean"],
            "fixture": "growth-system-v1",
        },
        scope="result",
    )
    current = ledger.create_session(
        "single",
        title="山茶饮料下一张主图",
        project_name="PA Tea Launch",
        brand_profile="PA Tea",
        category="food",
        brief={"product_name": "山茶饮料", "output_kind": "ecommerce-main"},
    )
    current_source = ledger.add_asset(
        current["id"],
        "source",
        name="tea-current-source.png",
        mime="image/png",
        data=PNG_1X1,
    )
    return {
        "prior_session_id": prior["id"],
        "prior_generation_id": generation["id"],
        "prior_result_asset_id": result["id"],
        "feedback_id": feedback["id"],
        "current_session_id": current["id"],
        "current_source_asset_id": current_source["id"],
        "knowledge_id": "LES-PACKAGED-GROWTH",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--knowledge-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(seed(args.data_dir.resolve(), args.knowledge_root.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
