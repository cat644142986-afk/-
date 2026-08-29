from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


OPEN_IMAGES_VALIDATION_BASE = (
    "https://open-images-dataset.s3.amazonaws.com/validation"
)
OFFICIAL_SOURCES = {
    "boxes": {
        "url": "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv",
        "md5": "c5e8200df129ea6867e913e8b21fcab9",
    },
    "image_labels": {
        "url": "https://storage.googleapis.com/openimages/v5/validation-annotations-human-imagelabels-boxable.csv",
        "md5": "2e129f40a209e65dfe4342ed0e7bd5f2",
    },
    "image_metadata": {
        "url": "https://storage.googleapis.com/openimages/2018_04/validation/validation-images-with-rotation.csv",
        "md5": "643a54e43b0bab8acce8817b4a569780",
    },
}


def _file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_rows(path: Path, selected_ids: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            row for row in csv.DictReader(handle)
            if row.get("ImageID") in selected_ids
        ]


def _xywh(row: dict[str, str]) -> list[float]:
    left = float(row["XMin"])
    top = float(row["YMin"])
    return [
        round(left, 9),
        round(top, 9),
        round(float(row["XMax"]) - left, 9),
        round(float(row["YMax"]) - top, 9),
    ]


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    if selection.get("schema_version") != "1.0":
        raise ValueError("selection schema_version must be 1.0")
    selected_cases = selection.get("cases")
    if not isinstance(selected_cases, list) or not selected_cases:
        raise ValueError("selection requires cases")
    case_ids = [str(item.get("id") or "") for item in selected_cases]
    if any(not item for item in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("selection case ids must be non-empty and unique")
    image_ids = {str(item["image_id"]) for item in selected_cases}

    supplied_sources = {
        "boxes": args.boxes,
        "image_labels": args.labels,
        "image_metadata": args.image_metadata,
    }
    source_receipt = {}
    for name, path in supplied_sources.items():
        actual = _file_digest(path, "md5")
        expected = OFFICIAL_SOURCES[name]["md5"]
        if actual != expected:
            raise ValueError(f"{name} MD5 mismatch: expected {expected}, got {actual}")
        source_receipt[name] = {
            "url": OFFICIAL_SOURCES[name]["url"],
            "md5": actual,
            "bytes": path.stat().st_size,
        }

    boxes_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in _read_rows(args.boxes, image_ids):
        boxes_by_key[(row["ImageID"], row["LabelName"])].append(row)
    labels_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in _read_rows(args.labels, image_ids):
        labels_by_key[(row["ImageID"], row["LabelName"])].add(row["Confidence"])
    metadata_by_id = {
        row["ImageID"]: row
        for row in _read_rows(args.image_metadata, image_ids)
    }

    images: dict[str, dict[str, Any]] = {}
    for image_id in sorted(image_ids):
        image_path = args.image_root / f"{image_id}.jpg"
        if not image_path.is_file():
            raise FileNotFoundError(f"selected Open Images file is missing: {image_path}")
        metadata = metadata_by_id.get(image_id)
        if metadata is None:
            raise ValueError(f"Open Images metadata is missing for {image_id}")
        if metadata.get("Subset") != "validation":
            raise ValueError(f"{image_id} is not in the validation split")
        license_url = str(metadata.get("License") or "")
        if license_url != "https://creativecommons.org/licenses/by/2.0/":
            raise ValueError(f"{image_id} does not declare CC BY 2.0")
        with Image.open(image_path) as opened:
            canvas = [int(opened.width), int(opened.height)]
        images[image_id] = {
            "path": f"validation/{image_id}.jpg",
            "bytes": image_path.stat().st_size,
            "sha256": _file_digest(image_path),
            "canvas": canvas,
            "source_page": metadata["OriginalLandingURL"],
            "source_file": f"{OPEN_IMAGES_VALIDATION_BASE}/{image_id}.jpg",
            "original_url": metadata["OriginalURL"],
            "author": metadata.get("Author") or "Unknown",
            "author_profile": metadata.get("AuthorProfileURL") or "",
            "title": metadata.get("Title") or "",
            "license": "CC BY 2.0",
            "license_url": license_url,
            "retrieved": "2026-08-29",
        }

    cases = []
    for selected in selected_cases:
        image_id = str(selected["image_id"])
        label_mid = str(selected["label_mid"])
        all_box_rows = boxes_by_key.get((image_id, label_mid), [])
        box_rows = [
            row for row in all_box_rows
            if row["IsGroupOf"] == "0"
            and row["IsDepiction"] == "0"
            and row["IsInside"] == "0"
        ]
        excluded = [
            row for row in all_box_rows
            if row["IsGroupOf"] != "0"
            or row["IsDepiction"] != "0"
            or row["IsInside"] != "0"
        ]
        absent = bool(selected.get("expected_absent"))
        if absent:
            if "0" not in labels_by_key.get((image_id, label_mid), set()):
                raise ValueError(
                    f"{selected['id']} lacks an official negative image label"
                )
            if box_rows:
                raise ValueError(f"{selected['id']} is negative but has target boxes")
            expected = []
        else:
            if not box_rows:
                raise ValueError(f"{selected['id']} has no official target boxes")
            expected = [
                {
                    "label": selected["query"],
                    "bbox": _xywh(row),
                    "source": row["Source"],
                    "label_mid": label_mid,
                    "is_occluded": row["IsOccluded"] == "1",
                    "is_truncated": row["IsTruncated"] == "1",
                }
                for row in box_rows
            ]
        target_count = int(selected.get("target_count") or len(expected))
        if target_count < 1 or target_count > 8:
            raise ValueError(f"{selected['id']} has unsupported target_count")
        if expected and target_count != len(expected):
            raise ValueError(
                f"{selected['id']} target_count does not match official boxes"
            )
        case = {
            "id": selected["id"],
            "image_id": image_id,
            "tags": list(selected.get("tags") or []),
            "query": selected["query"],
            "model_query_hint": selected["model_query_hint"],
            "target_count": target_count,
            "canvas": images[image_id]["canvas"],
            "label_mid": label_mid,
            "expected": expected,
        }
        if absent:
            case["negative_label"] = {
                "confidence": 0,
                "source": "verification",
                "label_mid": label_mid,
            }
        if excluded:
            case["ignored_annotations"] = {
                "count": len(excluded),
                "reason": "Open Images group/depiction/inside boxes are outside the physical-product target contract",
            }
        cases.append(case)

    return {
        "schema_version": "1.0",
        "corpus_id": "product-atelier-open-images-v7-validation-v1",
        "corpus_kind": "licensed-photo-downloadable",
        "review": "official-validation-boxes-negative-labels-and-visual-selection",
        "description": (
            "Open Images V7 validation 扩展集：30 张许可元数据锁定的真实照片、"
            "30 个存在目标查询和 5 个官方负标签查询。图片按需下载，不随 Git 再分发。"
        ),
        "limitations": [
            "这是 30 张照片/35 查询的回归门禁，不足以单独证明生产质量",
            "Open Images 官方提示图片许可状态仍应逐张核验；本清单锁定了每张图的作者、来源页和 CC BY 2.0 元数据",
            "官方标注是目标框而不是透明蒙版；hair-fine-lines 标签只覆盖选物/框定位难度，不代表毛发边缘抠图已经验收",
            "同一原图中的未标注类别不能视为不存在；no-match 只采用 validation 中 confidence=0 的人工负标签",
        ],
        "required_coverage": [
            "food",
            "multiple-similar",
            "packaging",
            "transparent",
            "hair-fine-lines",
            "shadow",
            "occlusion",
            "complex-background",
            "small-object",
            "no-match",
            "hard-negative",
        ],
        "gates": {
            "iou_threshold": 0.5,
            "min_recall": 0.8,
            "min_precision": 0.85,
            "min_exact_count_accuracy": 0.75,
            "min_no_match_accuracy": 1.0,
            "min_recovery_rate": 1.0,
        },
        "sources": {
            "dataset_page": "https://storage.googleapis.com/openimages/web/factsfigures_v7.html",
            "download_page": "https://storage.googleapis.com/openimages/web/download_v7.html",
            "annotations_license": "CC BY 4.0",
            "images_listed_license": "CC BY 2.0",
            "files": source_receipt,
        },
        "selection_sha256": _file_digest(args.selection),
        "images": images,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the locked Open Images semantic-grounding manifest."
    )
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--boxes", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--image-metadata", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "generated",
        "output": str(args.output.resolve()),
        "image_count": len(manifest["images"]),
        "case_count": len(manifest["cases"]),
        "no_match_count": sum(not case["expected"] for case in manifest["cases"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
