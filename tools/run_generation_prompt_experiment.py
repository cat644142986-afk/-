#!/usr/bin/env python3
"""Run the authorized paired prompt_v1/prompt_v3 quality experiment.

Each variant executes the same frozen two-stage image pipeline independently.
Provider calls are reserved before submission, and raw results are persisted
before a call is marked completed so an interrupted run cannot silently spend
the same stage twice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python import server  # noqa: E402
from python.generation_baseline import (  # noqa: E402
    PROMPT_V3_RENDER_PLAN_VERSION,
    compile_prompt_version,
)
from python.generation_quality_eval import (  # noqa: E402
    load_quality_manifest,
    paid_run_gate,
    validate_experiment_plan,
)
from python.generation_quality_gate import evaluate_generation_quality  # noqa: E402
from tools.run_generation_strategy_experiment import (  # noqa: E402
    atomic_write_json,
    canonical_sha256,
    file_sha256,
    load_state,
    reserve_call,
    trailing_failed_calls,
    update_call,
)


RUNNER_VERSION = "prompt-version-paid-ab-2026-09-01.1"
DEFAULT_CASE_IDS = (
    "packaging-text-brand-procedural",
    "multi-count-procedural",
    "transparent-bottle-real",
)
EXPECTED_VARIANTS = {
    "baseline-v1": "prompt_v1",
    "candidate-v3": "prompt_v3",
}


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _save_png_atomic(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        image.save(temporary, format="PNG")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _balance_snapshot() -> dict[str, Any]:
    try:
        response = server.api_request("GET", "/v1/skills/balance", timeout=15)
        data = response.get("data", response)
        return {"status": "available", "value": data.get("balance", response.get("balance"))}
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__}


def _variant_map(plan: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item.get("id") or ""): str((item.get("changes") or {}).get("prompt_version") or "")
        for item in plan.get("variants") or []
        if isinstance(item, Mapping)
    }


def validate_prompt_experiment(
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    value = validate_experiment_plan(plan)
    if value.get("variable_under_test") != "prompt_version":
        raise ValueError("this runner only accepts prompt_version experiments")
    fixed = dict(value.get("fixed_parameters") or {})
    if fixed.get("model") != "gpt-image-2":
        raise ValueError("this checkpoint is frozen to gpt-image-2")
    if fixed.get("generation_strategy") != "legacy_double_pass":
        raise ValueError("generation_strategy must stay frozen to legacy_double_pass")
    if int(fixed.get("stage_count") or 0) != 2:
        raise ValueError("stage_count must stay frozen to 2")
    if fixed.get("output_ratio") != "1:1" or fixed.get("output_resolution") != "2k":
        raise ValueError("output contract must stay frozen to 1:1/2k")
    model_snapshot = str(fixed.get("model_snapshot") or "")
    if not model_snapshot or "must-be-frozen" in model_snapshot:
        raise ValueError("model_snapshot must be frozen before execution")
    if _variant_map(value) != EXPECTED_VARIANTS:
        raise ValueError("variants must compare only baseline-v1 and candidate-v3")
    required_calls = len(DEFAULT_CASE_IDS) * len(EXPECTED_VARIANTS) * 2
    if int(value["budget"]["max_paid_calls"]) != required_calls:
        raise ValueError(f"paid call limit must be exactly {required_calls}")
    available_cases = {str(case.get("id") or "") for case in manifest.get("cases") or []}
    if not set(DEFAULT_CASE_IDS).issubset(available_cases):
        raise ValueError("manifest is missing one or more frozen experiment cases")
    return value


def case_context(
    case: Mapping[str, Any],
    model: str,
    prompt_version: str,
    output_spec: Mapping[str, Any],
) -> dict[str, Any]:
    coverage = {str(item) for item in case.get("coverage") or []}
    brief = dict(case.get("brief") or {})
    quantity = brief.get("quantity")
    packaging = bool({"packaging", "packaging-text"} & coverage)
    return {
        "mode": "single",
        "category": "packaging" if packaging else "general",
        "output_kind": "ecommerce-main",
        "platter": str(brief.get("platter") or "remove"),
        "angle": "auto",
        "fidelity": 40,
        "product_name": str(brief.get("product_name") or "产品"),
        "product_count": quantity,
        "quantity": quantity,
        "user_request": "; ".join(str(item) for item in case.get("invariants") or []),
        "source_cutoff": "truncation-completion" in coverage,
        "intent_locks": {
            "subject_shape": True,
            "product_count": True,
            "brand_color": "brand-color" in coverage,
            "packaging_text": "packaging-text" in coverage,
            "logo": False,
        },
        "approved_memory_rules": [],
        "model": model,
        "prompt_version": prompt_version,
        "output_spec": dict(output_spec),
    }


def _stage_snapshot(
    template_prompt: str,
    base_prompt: str,
    compiled_prompt: str,
    negative_prompt: str,
) -> dict[str, Any]:
    return {
        "template_sha256": _hash_text(template_prompt),
        "base_sha256": _hash_text(base_prompt),
        "compiled_sha256": _hash_text(compiled_prompt),
        "negative_sha256": _hash_text(negative_prompt),
        "template_characters": len(template_prompt),
        "base_characters": len(base_prompt),
        "compiled_characters": len(compiled_prompt),
        "negative_characters": len(negative_prompt),
    }


def build_prompts(
    case: Mapping[str, Any],
    model: str,
    prompt_version: str,
    output_spec: Mapping[str, Any],
) -> dict[str, Any]:
    context = case_context(case, model, prompt_version, output_spec)
    product_name = str(context["product_name"])
    platter = str(context["platter"])
    category = str(context["category"])
    negative = server.build_negative(platter)
    primary_template = server.make_prompt(
        server.build_single_prompt(product_name, platter, category, "auto"), 40
    )
    refine_template = server.make_prompt(
        server.build_stage2_prompt(product_name, platter, category, "auto"), 40
    )
    primary_base = compile_prompt_version(
        primary_template,
        prompt_version=prompt_version,
        context=context,
        stage="primary",
    )
    refine_base = compile_prompt_version(
        refine_template,
        prompt_version=prompt_version,
        context=context,
        stage="refine-1",
    )
    primary = server.KNOWLEDGE.enrich_prompt(primary_base, negative, context)
    refine = server.KNOWLEDGE.enrich_prompt(refine_base, negative, context)
    return {
        "context": context,
        "primary_prompt": primary["prompt"],
        "primary_negative": primary["negative_prompt"],
        "refine_prompt": refine["prompt"],
        "refine_negative": refine["negative_prompt"],
        "prompt_snapshot": {
            "prompt_version": prompt_version,
            "render_plan_version": (
                PROMPT_V3_RENDER_PLAN_VERSION
                if prompt_version == "prompt_v3"
                else "legacy-template"
            ),
            "primary": _stage_snapshot(
                primary_template,
                primary_base,
                primary["prompt"],
                primary["negative_prompt"],
            ),
            "refine": _stage_snapshot(
                refine_template,
                refine_base,
                refine["prompt"],
                refine["negative_prompt"],
            ),
        },
    }


def _completed_call_image(
    state: Mapping[str, Any],
    *,
    case_id: str,
    stage: str,
) -> Image.Image | None:
    for call in reversed(list(state.get("calls") or [])):
        if call.get("case_id") != case_id or call.get("stage") != stage:
            continue
        if call.get("status") != "completed":
            continue
        path = Path(str(call.get("result_path") or ""))
        expected_hash = str(call.get("result_sha256") or "")
        if not path.is_file() or not expected_hash or file_sha256(path) != expected_hash:
            raise RuntimeError(f"completed provider result is missing or changed: {case_id}/{stage}")
        with Image.open(path) as opened:
            return opened.copy()
    return None


def run_provider_call(
    state: dict[str, Any],
    state_path: Path,
    plan: dict[str, Any],
    *,
    case_id: str,
    stage: str,
    prompt: str,
    negative_prompt: str,
    reference: Image.Image,
    output_spec: Mapping[str, Any],
    result_path: Path,
) -> Image.Image:
    resumed = _completed_call_image(state, case_id=case_id, stage=stage)
    if resumed is not None:
        return resumed
    call = reserve_call(state, state_path, plan, case_id=case_id, stage=stage)
    call_index = int(call["call_index"])
    evidence: dict[str, Any] = {}
    started = time.perf_counter()

    def submitted(remote_task_id: str) -> None:
        update_call(
            state,
            state_path,
            call_index,
            status="submitted",
            remote_task_id=str(remote_task_id),
        )

    def captured(value: dict[str, Any]) -> None:
        evidence.clear()
        evidence.update(value)

    try:
        result = server.ai_i2i(
            prompt,
            reference,
            str(plan["fixed_parameters"]["model"]),
            negative_prompt=negative_prompt,
            stage=f"prompt-experiment-{case_id}-{stage}",
            tid_ref=f"paid-prompt-experiment-{call_index}",
            on_submitted=submitted,
            on_evidence=captured,
            output_spec=dict(output_spec),
        )
        _save_png_atomic(result, result_path)
        update_call(
            state,
            state_path,
            call_index,
            status="completed",
            completed_at_epoch_ms=round(time.time() * 1000),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            result_path=str(result_path.resolve()),
            result_sha256=file_sha256(result_path),
            result_width=result.width,
            result_height=result.height,
            evidence=evidence,
        )
        return result
    except Exception as exc:
        update_call(
            state,
            state_path,
            call_index,
            status="failed",
            completed_at_epoch_ms=round(time.time() * 1000),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            failure_type=type(exc).__name__,
            evidence=evidence,
        )
        raise


def artifact_record(
    case_id: str,
    variant_id: str,
    prompt_version: str,
    path: Path,
    quality: Mapping[str, Any],
    elapsed_ms: float,
) -> dict[str, Any]:
    with Image.open(path) as opened:
        width, height = opened.size
    return {
        "case_id": case_id,
        "variant": variant_id,
        "prompt_version": prompt_version,
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        "width": width,
        "height": height,
        "elapsed_ms": round(elapsed_ms, 3),
        "quality": dict(quality),
    }


def _valid_artifact(item: Mapping[str, Any]) -> bool:
    path = Path(str(item.get("path") or ""))
    return path.is_file() and str(item.get("sha256") or "") == file_sha256(path)


def build_blind_evidence(
    output_dir: Path,
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    seed: str,
) -> None:
    rng = random.Random(seed)
    artifacts = {
        (str(item["case_id"]), str(item["variant"])): Path(str(item["path"]))
        for item in state.get("artifacts") or []
    }
    cases_by_id = {str(case["id"]): case for case in manifest["cases"]}
    variant_ids = [str(item["id"]) for item in plan["variants"]]
    public_cases = []
    private_mapping = []
    contact_rows: list[tuple[str, Path, Path, Path]] = []
    blind_dir = output_dir / "blind"
    blind_dir.mkdir(parents=True, exist_ok=True)

    for case_id in DEFAULT_CASE_IDS:
        shuffled = list(variant_ids)
        rng.shuffle(shuffled)
        labels = ["A", "B"]
        case_dir = blind_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        label_paths: dict[str, Path] = {}
        outputs = []
        for label, variant_id in zip(labels, shuffled):
            source = artifacts[(case_id, variant_id)]
            destination = case_dir / f"{label}.png"
            shutil.copy2(source, destination)
            label_paths[label] = destination
            outputs.append({
                "label": label,
                "path": str(destination.resolve()),
                "sha256": file_sha256(destination),
            })
            private_mapping.append({
                "case_id": case_id,
                "label": label,
                "variant": variant_id,
                "source_sha256": file_sha256(source),
            })
        source_path = (
            ROOT / "tests" / "fixtures" / "generation_quality"
            / str(cases_by_id[case_id]["source"]["path"])
        ).resolve()
        contact_rows.append((case_id, source_path, label_paths["A"], label_paths["B"]))
        public_cases.append({
            "case_id": case_id,
            "source_path": str(source_path),
            "invariants": list(cases_by_id[case_id].get("invariants") or []),
            "outputs": outputs,
        })

    public = {
        "schema_version": "1.0",
        "experiment_id": str(plan["experiment_id"]),
        "cases": public_cases,
        "labels_are_blind": True,
        "prompt_versions_hidden": True,
    }
    private = {
        "schema_version": "1.0",
        "experiment_id": str(plan["experiment_id"]),
        "seed_sha256": _hash_text(seed),
        "mapping": private_mapping,
        "mapping_sha256": canonical_sha256(private_mapping),
    }
    atomic_write_json(output_dir / "blind-review.json", public)
    atomic_write_json(output_dir / "private-mapping.json", private)

    cell_width, cell_height, header = 512, 512, 34
    sheet = Image.new(
        "RGB",
        (cell_width * 3, (cell_height + header) * len(contact_rows)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    for row, (case_id, source_path, a_path, b_path) in enumerate(contact_rows):
        top = row * (cell_height + header)
        for column, (label, path) in enumerate((
            (f"SOURCE {case_id}", source_path),
            ("A", a_path),
            ("B", b_path),
        )):
            with Image.open(path) as opened:
                preview = ImageOps.contain(opened.convert("RGB"), (cell_width, cell_height))
            x = column * cell_width + (cell_width - preview.width) // 2
            y = top + header + (cell_height - preview.height) // 2
            sheet.paste(preview, (x, y))
            draw.text((column * cell_width + 8, top + 8), label, fill="black")
    sheet.save(output_dir / "blind-contact-sheet.jpg", "JPEG", quality=95)


def build_run_summary(
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    calls = list(state.get("calls") or [])
    artifacts = list(state.get("artifacts") or [])
    return {
        "schema_version": "1.0",
        "runner_version": RUNNER_VERSION,
        "experiment_id": str(plan["experiment_id"]),
        "status": str(state.get("status") or "unknown"),
        "authorization": {
            "max_paid_calls": int(plan["budget"]["max_paid_calls"]),
            "calls_used": len(calls),
            "successful_calls": sum(call.get("status") == "completed" for call in calls),
            "failed_calls": sum(call.get("status") == "failed" for call in calls),
            "billing_amount": None,
            "balance_before": state.get("balance_before"),
            "balance_after": state.get("balance_after"),
        },
        "artifacts": [
            {
                "case_id": item.get("case_id"),
                "variant": item.get("variant"),
                "sha256": item.get("sha256"),
                "width": item.get("width"),
                "height": item.get("height"),
                "elapsed_ms": item.get("elapsed_ms"),
                "quality": item.get("quality"),
            }
            for item in artifacts
        ],
        "prompt_snapshots": state.get("prompt_snapshots") or {},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blind-seed", required=True)
    parser.add_argument("--execute-paid", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    manifest = load_quality_manifest(args.manifest)
    plan = validate_prompt_experiment(plan, manifest)
    fixed = dict(plan["fixed_parameters"])
    required_calls = len(DEFAULT_CASE_IDS) * len(EXPECTED_VARIANTS) * 2
    schedule = [
        {"case_id": case_id, "variant": variant_id, "stages": ["primary", "refine"]}
        for case_id in DEFAULT_CASE_IDS
        for variant_id in EXPECTED_VARIANTS
    ]
    output_dir = args.output_dir.resolve()
    state_path = output_dir / "state.json"
    state = load_state(state_path, canonical_sha256(plan))
    dry_result = {
        "experiment_id": plan["experiment_id"],
        "runner_version": RUNNER_VERSION,
        "schedule": schedule,
        "required_calls": required_calls,
        "authorized_call_limit": int(plan["budget"]["max_paid_calls"]),
        "calls_already_used": len(state["calls"]),
        "state_status": state.get("status"),
        "paid_gate": paid_run_gate(plan, calls_already_used=len(state["calls"])),
    }
    if not args.execute_paid:
        print(json.dumps(dry_result, ensure_ascii=False))
        return 0
    if not dry_result["paid_gate"].get("allowed"):
        raise RuntimeError(f"paid execution is not authorized: {dry_result['paid_gate']}")

    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = output_dir / "runtime"
    (runtime_dir / "_tmp").mkdir(parents=True, exist_ok=True)
    server.OUTPUT_DIR = runtime_dir
    server.get_api_key()
    if any(call.get("status") in {"reserved", "submitted"} for call in state["calls"]):
        raise RuntimeError("unfinished provider call exists; inspect state before resuming")
    state.setdefault("runner_version", RUNNER_VERSION)
    state.setdefault("balance_before", _balance_snapshot())
    state.setdefault("prompt_snapshots", {})
    state.setdefault("deterministic_unusable_results", 0)
    state["manifest_sha256"] = file_sha256(args.manifest.resolve())
    state["plan_file_sha256"] = file_sha256(args.plan.resolve())
    atomic_write_json(state_path, state)

    cases_by_id = {str(case["id"]): case for case in manifest["cases"]}
    source_root = args.manifest.resolve().parent
    stop_requested = False
    for case_id in DEFAULT_CASE_IDS:
        case = cases_by_id[case_id]
        source_path = (source_root / str(case["source"]["path"])).resolve()
        if file_sha256(source_path) != case["source"]["sha256"]:
            raise ValueError(f"fixture sha256 mismatch: {case_id}")
        with Image.open(source_path) as opened:
            source = opened.convert("RGB").copy()
        output_spec = server.resolve_output_spec(
            str(fixed["model"]),
            str(fixed["output_ratio"]),
            str(fixed["output_resolution"]),
            source.size,
            explicit=True,
        )
        for variant_id, prompt_version in EXPECTED_VARIANTS.items():
            existing = next((
                item for item in state.get("artifacts") or []
                if item.get("case_id") == case_id
                and item.get("variant") == variant_id
                and _valid_artifact(item)
            ), None)
            if existing is not None:
                continue
            started = time.perf_counter()
            prompts = build_prompts(case, str(fixed["model"]), prompt_version, output_spec)
            state["prompt_snapshots"].setdefault(case_id, {})[variant_id] = prompts[
                "prompt_snapshot"
            ]
            atomic_write_json(state_path, state)
            variant_runtime = runtime_dir / case_id / variant_id
            try:
                primary = run_provider_call(
                    state,
                    state_path,
                    plan,
                    case_id=case_id,
                    stage=f"{variant_id}:primary",
                    prompt=prompts["primary_prompt"],
                    negative_prompt=prompts["primary_negative"],
                    reference=source,
                    output_spec=output_spec,
                    result_path=variant_runtime / "primary-raw.png",
                )
                refined = run_provider_call(
                    state,
                    state_path,
                    plan,
                    case_id=case_id,
                    stage=f"{variant_id}:refine",
                    prompt=prompts["refine_prompt"],
                    negative_prompt=prompts["refine_negative"],
                    reference=primary,
                    output_spec=output_spec,
                    result_path=variant_runtime / "refine-raw.png",
                )
                delivery = server.post_process_enhance(refined)
                artifact_path = output_dir / "artifacts" / case_id / f"{variant_id}.png"
                _save_png_atomic(delivery, artifact_path)
                quality = evaluate_generation_quality(
                    delivery,
                    output_spec,
                    context=prompts["context"],
                )
                state["artifacts"] = [
                    item for item in state.get("artifacts") or []
                    if not (
                        item.get("case_id") == case_id
                        and item.get("variant") == variant_id
                    )
                ] + [artifact_record(
                    case_id,
                    variant_id,
                    prompt_version,
                    artifact_path,
                    quality,
                    (time.perf_counter() - started) * 1000,
                )]
                state["deterministic_unusable_results"] = sum(
                    item.get("quality", {}).get("deterministic_status") == "fail"
                    for item in state["artifacts"]
                )
                state["consecutive_failures"] = 0
                atomic_write_json(state_path, state)
                if state["deterministic_unusable_results"] >= int(
                    plan["stop_conditions"]["unusable_results"]
                ):
                    state["status"] = "stopped-deterministic-unusable-results"
                    stop_requested = True
                    break
            except Exception as exc:
                state["consecutive_failures"] = trailing_failed_calls(state)
                state.setdefault("variant_failures", []).append({
                    "case_id": case_id,
                    "variant": variant_id,
                    "failure_type": type(exc).__name__,
                })
                if state["consecutive_failures"] >= int(
                    plan["stop_conditions"]["consecutive_failures"]
                ):
                    state["status"] = "stopped-consecutive-failures"
                    stop_requested = True
                atomic_write_json(state_path, state)
                if stop_requested:
                    break
        if stop_requested:
            break

    expected_artifacts = {
        (case_id, variant_id)
        for case_id in DEFAULT_CASE_IDS
        for variant_id in EXPECTED_VARIANTS
    }
    completed_artifacts = {
        (str(item.get("case_id")), str(item.get("variant")))
        for item in state.get("artifacts") or []
        if _valid_artifact(item)
    }
    if not str(state.get("status") or "").startswith("stopped-"):
        state["status"] = (
            "completed" if expected_artifacts.issubset(completed_artifacts) else "partial"
        )
    state["balance_after"] = _balance_snapshot()
    state["completed_at_epoch_ms"] = round(time.time() * 1000)
    atomic_write_json(state_path, state)
    if state["status"] == "completed":
        build_blind_evidence(output_dir, plan, manifest, state, seed=args.blind_seed)
    atomic_write_json(output_dir / "run-summary.json", build_run_summary(plan, state))
    print(json.dumps({
        **dry_result,
        "state": str(state_path),
        "status": state["status"],
        "calls_used": len(state["calls"]),
        "artifact_count": len(state.get("artifacts") or []),
        "balance_before": state.get("balance_before"),
        "balance_after": state.get("balance_after"),
    }, ensure_ascii=False))
    return 0 if state["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
