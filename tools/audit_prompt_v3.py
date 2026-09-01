#!/usr/bin/env python3
"""Audit prompt_v3 against frozen fixtures and read-only job metadata.

The audit never opens source images, reads API keys, or calls a provider. Raw
prompts and user requests are omitted from its report; only hashes, lengths,
and constraint coverage are retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.generation_baseline import (  # noqa: E402
    PROMPT_V3_RENDER_PLAN_VERSION,
    compile_prompt_version,
    prompt_v3_render_plan,
)


DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "generation_quality" / "manifest.json"
LEGACY_SENTINEL = "LEGACY_STACK_MUST_NOT_APPEAR"
MAX_AUTOMATIC_CHARACTERS = 460


def _decode_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fixture_context(case: Mapping[str, Any]) -> dict[str, Any]:
    coverage = {str(item) for item in case.get("coverage") or []}
    brief = _decode_object(case.get("brief"))
    invariants = [str(item).strip() for item in case.get("invariants") or [] if str(item).strip()]
    return {
        "product_name": str(brief.get("product_name") or "fixture product"),
        "quantity": brief.get("quantity"),
        "platter": str(brief.get("platter") or "auto"),
        "category": "packaging" if "packaging" in coverage else "food",
        "fidelity": 40,
        "model": "gpt-image-2",
        "output_kind": "ecommerce-main",
        "source_cutoff": "truncation-completion" in coverage,
        "user_request": "; ".join(invariants),
        "intent_locks": {
            "subject_shape": True,
            "product_count": True,
            "packaging_text": "packaging-text" in coverage,
            "brand_color": "brand-color" in coverage,
        },
        "output_spec": {"effective_ratio": "1:1", "requested_resolution": "2k"},
    }


def _audit_context(
    case_id: str,
    context: Mapping[str, Any],
    *,
    coverage: Iterable[str] = (),
) -> dict[str, Any]:
    coverage_set = {str(item) for item in coverage}
    primary_plan = prompt_v3_render_plan(context=context, stage="primary")
    primary = compile_prompt_version(
        LEGACY_SENTINEL,
        prompt_version="prompt_v3",
        context=context,
        stage="primary",
    )
    refine = compile_prompt_version(
        LEGACY_SENTINEL,
        prompt_version="prompt_v3",
        context=context,
        stage="refine-1",
    )
    request = str(primary_plan.get("user_request") or "")
    product_name = str(primary_plan.get("product_name") or "")
    count = primary_plan.get("product_count")
    platter = str(context.get("platter") or "auto")
    output_spec = context.get("output_spec") if isinstance(context.get("output_spec"), Mapping) else {}
    expected_ratio = str(
        output_spec.get("effective_ratio") or output_spec.get("ratio") or ""
    ).strip()
    expected_resolution = str(
        output_spec.get("requested_resolution")
        or output_spec.get("resolution")
        or output_spec.get("size")
        or ""
    ).strip()
    hard_text = "\n".join(str(item) for item in primary_plan.get("hard_constraints") or [])
    automatic_primary = len(primary) - (len(request) if request else 0)
    automatic_refine = len(refine) - (len(request) if request else 0)
    checks = {
        "render_plan_version": primary_plan.get("version") == PROMPT_V3_RENDER_PLAN_VERSION,
        "legacy_stack_excluded": LEGACY_SENTINEL not in primary and LEGACY_SENTINEL not in refine,
        "product_identity_preserved": bool(product_name) and product_name in primary and product_name in refine,
        "user_request_preserved": not request or (request in primary and request in refine),
        "product_count_preserved": count is None or (
            f"产品数量严格保持为{count}" in primary
            and f"产品数量严格保持为{count}" in refine
        ),
        "output_ratio_preserved": not expected_ratio or expected_ratio in primary,
        "output_resolution_preserved": (
            not expected_resolution or expected_resolution in primary
        ),
        "platter_rule_preserved": (
            platter not in {"keep", "remove"}
            or (platter == "keep" and "保留原有器皿类型" in primary)
            or (platter == "remove" and "移除器皿和托盘" in primary)
        ),
        "packaging_text_preserved": (
            "packaging-text" not in coverage_set
            or "可见文字、数字与品牌标志不变" in hard_text
        ),
        "brand_color_preserved": (
            "brand-color" not in coverage_set
            or "品牌主色" in hard_text
        ),
        "cutoff_recovery_preserved": (
            "truncation-completion" not in coverage_set
            or "补全原图被裁切的主体边缘" in primary
        ),
        "automatic_character_budget": (
            automatic_primary <= MAX_AUTOMATIC_CHARACTERS
            and automatic_refine <= MAX_AUTOMATIC_CHARACTERS
        ),
    }
    return {
        "case_key": case_id,
        "passed": all(checks.values()),
        "checks": checks,
        "primary": {
            "sha256": _sha256(primary),
            "characters": len(primary),
            "automatic_characters": automatic_primary,
        },
        "refine": {
            "sha256": _sha256(refine),
            "characters": len(refine),
            "automatic_characters": automatic_refine,
        },
    }


def audit_fixture_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    cases = [
        _audit_context(
            str(case.get("id") or f"fixture-{index + 1}"),
            _fixture_context(case),
            coverage=case.get("coverage") or [],
        )
        for index, case in enumerate(manifest.get("cases") or [])
        if isinstance(case, Mapping)
    ]
    return {
        "sample_count": len(cases),
        "passed_count": sum(bool(case["passed"]) for case in cases),
        "all_passed": bool(cases) and all(bool(case["passed"]) for case in cases),
        "cases": cases,
    }


def load_history_rows(database: Path, limit: int = 200) -> list[dict[str, Any]]:
    uri = f"file:{quote(database.resolve().as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT j.id, j.mode, j.parameters_json, s.category,
                   s.brief_json, s.intent_locks_json
            FROM jobs j
            JOIN sessions s ON s.id = j.session_id
            WHERE j.mode IN ('single', 'multi-file', 'group-split')
            ORDER BY j.created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    return [dict(row) for row in rows]


def _history_context(row: Mapping[str, Any]) -> dict[str, Any]:
    params = _decode_object(row.get("parameters_json"))
    brief = _decode_object(row.get("brief_json"))
    locks = _decode_object(row.get("intent_locks_json"))
    brief_output = _decode_object(brief.get("output_spec"))
    return {
        **brief,
        "product_name": str(params.get("product_name") or "参考图中的产品"),
        "quantity": (
            params.get("product_count")
            or params.get("quantity")
            or brief.get("product_count")
            or brief.get("quantity")
        ),
        "platter": str(params.get("platter") or brief.get("platter") or "auto"),
        "angle": str(params.get("angle") or brief.get("angle") or "auto"),
        "fidelity": int(params.get("fidelity", brief.get("fidelity", 40)) or 40),
        "model": str(params.get("model") or "gpt-image-2"),
        "category": str(row.get("category") or brief.get("category") or "general"),
        "output_kind": str(brief.get("output_kind") or "ecommerce-main"),
        "intent_locks": locks,
        "user_request": str(brief.get("user_request") or ""),
        "output_spec": {
            "ratio": str(params.get("output_ratio") or brief_output.get("ratio") or ""),
            "resolution": str(
                params.get("output_resolution")
                or brief_output.get("resolution")
                or brief_output.get("size")
                or ""
            ),
        },
    }


def audit_history_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    cases = []
    for row in rows:
        job_id = str(row.get("id") or "")
        case_key = _sha256(job_id)[:16] if job_id else "missing-job-id"
        cases.append(_audit_context(case_key, _history_context(row)))
    return {
        "sample_count": len(cases),
        "passed_count": sum(bool(case["passed"]) for case in cases),
        "all_passed": all(bool(case["passed"]) for case in cases),
        "cases": cases,
    }


def build_report(
    manifest: Mapping[str, Any],
    history_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    fixtures = audit_fixture_manifest(manifest)
    history = audit_history_rows(history_rows)
    return {
        "schema_version": "1.0",
        "render_plan_version": PROMPT_V3_RENDER_PLAN_VERSION,
        "mode": "offline-read-only",
        "privacy": {
            "images_read": False,
            "api_keys_read": False,
            "providers_called": False,
            "raw_prompts_included": False,
            "raw_user_requests_included": False,
            "job_ids": "sha256-prefix",
        },
        "limits": {"max_automatic_characters_per_stage": MAX_AUTOMATIC_CHARACTERS},
        "fixtures": fixtures,
        "history": history,
        "ready_for_paid_pilot": fixtures["all_passed"] and history["all_passed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--history-limit", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.manifest.is_file():
        parser.error(f"manifest does not exist: {args.manifest}")
    if args.db is not None and not args.db.is_file():
        parser.error(f"database does not exist: {args.db}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    history_rows = load_history_rows(args.db, args.history_limit) if args.db else []
    report = build_report(manifest, history_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "fixtures": {
            "samples": report["fixtures"]["sample_count"],
            "passed": report["fixtures"]["passed_count"],
        },
        "history": {
            "samples": report["history"]["sample_count"],
            "passed": report["history"]["passed_count"],
        },
        "ready_for_paid_pilot": report["ready_for_paid_pilot"],
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if report["ready_for_paid_pilot"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
