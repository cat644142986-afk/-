#!/usr/bin/env python3
"""Run the authorized paired single-pass/double-pass quality experiment.

Each case shares one primary provider result: the single-pass artifact is the
locally enhanced primary result, while the double-pass artifact refines that
same unenhanced primary result once. This isolates the value of the second
paid stage and uses two calls per case instead of three.
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
from typing import Any

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python import server  # noqa: E402
from python.generation_quality_eval import (  # noqa: E402
    load_quality_manifest,
    paid_run_gate,
    validate_experiment_plan,
)
from python.generation_quality_gate import evaluate_generation_quality  # noqa: E402


DEFAULT_CASE_IDS = (
    "packaging-text-brand-procedural",
    "multi-count-procedural",
    "transparent-bottle-real",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_state(path: Path, plan_sha256: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": "1.0",
            "plan_sha256": plan_sha256,
            "calls": [],
            "artifacts": [],
            "status": "ready",
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("plan_sha256") != plan_sha256:
        raise ValueError("existing experiment state belongs to a different plan")
    return value


def reserve_call(
    state: dict[str, Any],
    state_path: Path,
    plan: dict[str, Any],
    *,
    case_id: str,
    stage: str,
) -> dict[str, Any]:
    gate = paid_run_gate(plan, calls_already_used=len(state["calls"]))
    if not gate.get("allowed"):
        raise RuntimeError(f"paid gate blocked call: {gate.get('reason')}")
    entry = {
        "call_index": len(state["calls"]) + 1,
        "case_id": case_id,
        "stage": stage,
        "status": "reserved",
        "reserved_at_epoch_ms": round(time.time() * 1000),
    }
    state["calls"].append(entry)
    state["status"] = "running"
    atomic_write_json(state_path, state)
    return entry


def update_call(
    state: dict[str, Any],
    state_path: Path,
    call_index: int,
    **changes: Any,
) -> None:
    entry = state["calls"][call_index - 1]
    entry.update(changes)
    atomic_write_json(state_path, state)


def trailing_failed_calls(state: dict[str, Any]) -> int:
    """Count provider failures since the most recent completed call."""
    count = 0
    for call in reversed(state.get("calls") or []):
        status = call.get("status")
        if status == "failed":
            count += 1
            continue
        if status == "completed":
            break
    return count


def case_context(case: dict[str, Any], model: str, output_spec: dict[str, Any]) -> dict[str, Any]:
    coverage = set(case.get("coverage") or [])
    brief = dict(case.get("brief") or {})
    packaging = bool({"packaging", "packaging-text"} & coverage)
    locks = {
        "subject_shape": True,
        "product_count": True,
        "brand_color": "brand-color" in coverage,
        "packaging_text": "packaging-text" in coverage,
        "logo": False,
    }
    return {
        "mode": "single",
        "category": "packaging" if packaging else "general",
        "output_kind": "ecommerce-main",
        "platter": str(brief.get("platter") or "remove"),
        "angle": "auto",
        "fidelity": 40,
        "product_name": str(brief.get("product_name") or "产品"),
        "intent_locks": locks,
        "approved_memory_rules": [],
        "model": model,
        "prompt_version": "prompt_v1",
        "output_spec": output_spec,
    }


def build_prompts(case: dict[str, Any], model: str, output_spec: dict[str, Any]) -> dict[str, Any]:
    context = case_context(case, model, output_spec)
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
    primary = server.KNOWLEDGE.enrich_prompt(primary_template, negative, context)
    refine = server.KNOWLEDGE.enrich_prompt(refine_template, negative, context)
    return {
        "context": context,
        "primary_prompt": primary["prompt"],
        "primary_negative": primary["negative_prompt"],
        "refine_prompt": refine["prompt"],
        "refine_negative": refine["negative_prompt"],
        "prompt_snapshot": {
            "primary_sha256": hashlib.sha256(primary["prompt"].encode("utf-8")).hexdigest(),
            "primary_negative_sha256": hashlib.sha256(primary["negative_prompt"].encode("utf-8")).hexdigest(),
            "refine_sha256": hashlib.sha256(refine["prompt"].encode("utf-8")).hexdigest(),
            "refine_negative_sha256": hashlib.sha256(refine["negative_prompt"].encode("utf-8")).hexdigest(),
            "primary_characters": len(primary["prompt"]),
            "primary_negative_characters": len(primary["negative_prompt"]),
            "refine_characters": len(refine["prompt"]),
            "refine_negative_characters": len(refine["negative_prompt"]),
        },
    }


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
    output_spec: dict[str, Any],
) -> tuple[Image.Image, dict[str, Any]]:
    call = reserve_call(state, state_path, plan, case_id=case_id, stage=stage)
    call_index = int(call["call_index"])
    evidence: dict[str, Any] = {}

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
            stage=f"experiment-{case_id}-{stage}",
            tid_ref=f"paid-experiment-{call_index}",
            on_submitted=submitted,
            on_evidence=captured,
            output_spec=output_spec,
        )
    except Exception as exc:
        update_call(
            state,
            state_path,
            call_index,
            status="failed",
            failure_type=type(exc).__name__,
            evidence=evidence,
        )
        raise
    update_call(
        state,
        state_path,
        call_index,
        status="completed",
        completed_at_epoch_ms=round(time.time() * 1000),
        evidence=evidence,
    )
    return result, evidence


def artifact_record(
    case_id: str,
    variant: str,
    path: Path,
    quality: dict[str, Any],
) -> dict[str, Any]:
    with Image.open(path) as image:
        width, height = image.size
    return {
        "case_id": case_id,
        "variant": variant,
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        "width": width,
        "height": height,
        "quality": quality,
    }


def build_blind_evidence(
    output_dir: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    *,
    seed: str,
) -> None:
    rng = random.Random(seed)
    artifact_lookup = {
        (item["case_id"], item["variant"]): Path(item["path"])
        for item in state["artifacts"]
    }
    cases_by_id = {case["id"]: case for case in manifest["cases"]}
    public_cases = []
    private_mapping = []
    contact_rows: list[tuple[str, Path, Path, Path]] = []
    blind_dir = output_dir / "blind"
    blind_dir.mkdir(parents=True, exist_ok=True)

    for case_id in DEFAULT_CASE_IDS:
        labels = ["A", "B"]
        variants = ["single_pass", "legacy_double_pass"]
        rng.shuffle(variants)
        case_dir = blind_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        label_paths: dict[str, Path] = {}
        for label, variant in zip(labels, variants):
            source = artifact_lookup[(case_id, variant)]
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
                "variant": variant,
                "source_sha256": file_sha256(source),
            })
        source_path = (
            Path(__file__).resolve().parents[1]
            / "tests" / "fixtures" / "generation_quality"
            / cases_by_id[case_id]["source"]["path"]
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
        "experiment_id": "r7-single-vs-double-pass-2026-08-31",
        "cases": public_cases,
        "labels_are_blind": True,
    }
    private = {
        "schema_version": "1.0",
        "seed_sha256": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
        "mapping": private_mapping,
    }
    private["mapping_sha256"] = canonical_sha256(private_mapping)
    atomic_write_json(output_dir / "blind-review.json", public)
    atomic_write_json(output_dir / "private-mapping.json", private)

    cell_width, cell_height = 512, 512
    header = 34
    sheet = Image.new(
        "RGB",
        (cell_width * 3, (cell_height + header) * len(contact_rows)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    for row, (case_id, source_path, a_path, b_path) in enumerate(contact_rows):
        top = row * (cell_height + header)
        for column, (label, path) in enumerate((
            (f"SOURCE {case_id}", source_path), ("A", a_path), ("B", b_path)
        )):
            with Image.open(path) as image:
                preview = ImageOps.contain(image.convert("RGB"), (cell_width, cell_height))
            x = column * cell_width + (cell_width - preview.width) // 2
            y = top + header + (cell_height - preview.height) // 2
            sheet.paste(preview, (x, y))
            draw.text((column * cell_width + 8, top + 8), label, fill="black")
    sheet.save(output_dir / "blind-contact-sheet.jpg", "JPEG", quality=94)


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
    plan = validate_experiment_plan(json.loads(args.plan.read_text(encoding="utf-8")))
    manifest = load_quality_manifest(args.manifest)
    if plan.get("variable_under_test") != "generation_strategy":
        raise ValueError("this runner only accepts generation_strategy experiments")
    fixed = dict(plan.get("fixed_parameters") or {})
    if fixed.get("prompt_version") != "prompt_v1":
        raise ValueError("prompt_version must stay frozen to prompt_v1")
    if fixed.get("model") != "gpt-image-2":
        raise ValueError("this checkpoint is frozen to gpt-image-2")
    required_calls = len(DEFAULT_CASE_IDS) * 2
    if int(plan["budget"]["max_paid_calls"]) < required_calls:
        raise ValueError(f"at least {required_calls} calls are required for the paired plan")
    case_ids = {case["id"] for case in manifest["cases"]}
    if not set(DEFAULT_CASE_IDS).issubset(case_ids):
        raise ValueError("manifest is missing one or more frozen experiment cases")

    schedule = [
        {"case_id": case_id, "stages": ["primary", "refine"], "paid_calls": 2}
        for case_id in DEFAULT_CASE_IDS
    ]
    output_dir = args.output_dir.resolve()
    state_path = output_dir / "state.json"
    plan_sha256 = canonical_sha256(plan)
    state = load_state(state_path, plan_sha256)
    dry_result = {
        "experiment_id": plan["experiment_id"],
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
    server.get_api_key()  # Reads the saved key without printing or copying it.
    if any(call.get("status") in {"reserved", "submitted"} for call in state["calls"]):
        raise RuntimeError("unfinished provider call exists; inspect state before resuming")
    completed_keys = {
        (item["case_id"], item["variant"])
        for item in state.get("artifacts") or []
        if Path(item["path"]).is_file() and file_sha256(Path(item["path"])) == item["sha256"]
    }
    cases_by_id = {case["id"]: case for case in manifest["cases"]}
    source_root = args.manifest.resolve().parent
    consecutive_failures = trailing_failed_calls(state)
    state["consecutive_failures"] = consecutive_failures
    state.setdefault("deterministic_unusable_results", 0)
    atomic_write_json(state_path, state)

    for case_id in DEFAULT_CASE_IDS:
        if {
            (case_id, "single_pass"),
            (case_id, "legacy_double_pass"),
        }.issubset(completed_keys):
            continue
        case = cases_by_id[case_id]
        source_path = (source_root / case["source"]["path"]).resolve()
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
        prompts = build_prompts(case, str(fixed["model"]), output_spec)
        case_dir = output_dir / "artifacts" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            primary_raw, _ = run_provider_call(
                state,
                state_path,
                plan,
                case_id=case_id,
                stage="primary",
                prompt=prompts["primary_prompt"],
                negative_prompt=prompts["primary_negative"],
                reference=source,
                output_spec=output_spec,
            )
            single_delivery = server.post_process_enhance(primary_raw)
            single_path = case_dir / "single-pass.png"
            single_delivery.save(single_path, "PNG")
            single_quality = evaluate_generation_quality(
                single_delivery, output_spec, context=prompts["context"]
            )

            refined_raw, _ = run_provider_call(
                state,
                state_path,
                plan,
                case_id=case_id,
                stage="refine",
                prompt=prompts["refine_prompt"],
                negative_prompt=prompts["refine_negative"],
                reference=primary_raw,
                output_spec=output_spec,
            )
            double_delivery = server.post_process_enhance(refined_raw)
            double_path = case_dir / "double-pass.png"
            double_delivery.save(double_path, "PNG")
            double_quality = evaluate_generation_quality(
                double_delivery, output_spec, context=prompts["context"]
            )
            state["artifacts"] = [
                item for item in state["artifacts"] if item["case_id"] != case_id
            ] + [
                artifact_record(case_id, "single_pass", single_path, single_quality),
                artifact_record(case_id, "legacy_double_pass", double_path, double_quality),
            ]
            state.setdefault("prompt_snapshots", {})[case_id] = prompts["prompt_snapshot"]
            state["deterministic_unusable_results"] = sum(
                1
                for item in state["artifacts"]
                if item.get("quality", {}).get("deterministic_status") == "fail"
            )
            state["consecutive_failures"] = 0
            atomic_write_json(state_path, state)
            consecutive_failures = 0
            if state["deterministic_unusable_results"] >= int(
                plan["stop_conditions"]["unusable_results"]
            ):
                state["status"] = "stopped-deterministic-unusable-results"
                atomic_write_json(state_path, state)
                break
        except Exception:
            consecutive_failures = trailing_failed_calls(state)
            state["consecutive_failures"] = consecutive_failures
            atomic_write_json(state_path, state)
            if consecutive_failures >= int(plan["stop_conditions"]["consecutive_failures"]):
                state["status"] = "stopped-consecutive-failures"
                atomic_write_json(state_path, state)
                raise
            continue

    complete_cases = {
        item["case_id"] for item in state["artifacts"]
        if item["variant"] == "legacy_double_pass"
    }
    if not str(state.get("status") or "").startswith("stopped-"):
        state["status"] = (
            "completed" if set(DEFAULT_CASE_IDS).issubset(complete_cases) else "partial"
        )
    state["completed_at_epoch_ms"] = round(time.time() * 1000)
    atomic_write_json(state_path, state)
    if state["status"] == "completed":
        build_blind_evidence(output_dir, manifest, state, seed=args.blind_seed)
    print(json.dumps({
        **dry_result,
        "state": str(state_path),
        "status": state["status"],
        "calls_used": len(state["calls"]),
        "artifact_count": len(state["artifacts"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
