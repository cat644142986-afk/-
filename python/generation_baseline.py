"""Reproducible, provider-neutral evidence for generation quality baselines.

This module deliberately does not estimate prices.  The current LK media
adapter does not return billing data, so a missing amount must remain visible
instead of being replaced by an invented number.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable, Mapping


GENERATION_TRACE_CONTRACT_VERSION = "generation-baseline-2026-09-01.3"
PROMPT_COMPILER_VERSION = "prompt_v1"
PROMPT_V1 = "prompt_v1"
PROMPT_V2 = "prompt_v2"
PROMPT_V3 = "prompt_v3"
SUPPORTED_PROMPT_VERSIONS = frozenset({PROMPT_V1, PROMPT_V2, PROMPT_V3})
PROMPT_V2_FEATURE_ENV = "PRODUCT_ATELIER_ENABLE_PROMPT_V2"
PROMPT_V3_FEATURE_ENV = "PRODUCT_ATELIER_ENABLE_PROMPT_V3"
LEGACY_DOUBLE_PASS = "legacy_double_pass"
SINGLE_PASS = "single_pass"
SUPPORTED_GENERATION_STRATEGIES = frozenset({LEGACY_DOUBLE_PASS, SINGLE_PASS})
SINGLE_PASS_FEATURE_ENV = "PRODUCT_ATELIER_ENABLE_SINGLE_PASS"
PROVIDER_ADAPTER_VERSION = "lk-media-generate-v1"
PROMPT_V3_RENDER_PLAN_VERSION = "prompt-v3-render-plan-2026-09-01.1"
MATERIAL_PROMPT_ROUTE_VERSION = "prompt-material-route-2026-09-01.1"
MATERIAL_PROFILES = frozenset({"unknown", "opaque", "transparent", "reflective", "mixed"})


_CAPABILITY_CONTRACTS: dict[str, dict[str, Any]] = {
    "gpt-image-2": {
        "family": "gpt-image-2",
        "status": "request-shape-checked",
        "endpoint": "/v1/media/generate",
        "reference_parameter": "params.images[]",
        "output_parameters": ["size", "quality"],
        "response_mode": "async-task",
        "poll_endpoint": "/v1/skills/task-status",
    },
    "gemini-image": {
        "family": "gemini-image",
        "status": "request-shape-checked",
        "endpoint": "/v1/media/generate",
        "reference_parameter": "params.images[]",
        "output_parameters": ["aspectRatio", "imageSize"],
        "response_mode": "async-task",
        "poll_endpoint": "/v1/skills/task-status",
    },
    "generic-image": {
        "family": "generic-image",
        "status": "compatibility-only",
        "endpoint": "/v1/media/generate",
        "reference_parameter": "params.images[]",
        "output_parameters": ["size"],
        "response_mode": "async-task",
        "poll_endpoint": "/v1/skills/task-status",
    },
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _environment_flag(
    name: str,
    environment: Mapping[str, str] | None = None,
) -> bool:
    source = environment if environment is not None else os.environ
    return str(source.get(name, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def prompt_v2_enabled(environment: Mapping[str, str] | None = None) -> bool:
    return _environment_flag(PROMPT_V2_FEATURE_ENV, environment)


def prompt_v3_enabled(environment: Mapping[str, str] | None = None) -> bool:
    return _environment_flag(PROMPT_V3_FEATURE_ENV, environment)


def single_pass_enabled(environment: Mapping[str, str] | None = None) -> bool:
    return _environment_flag(SINGLE_PASS_FEATURE_ENV, environment)


def normalize_prompt_version(
    requested: Any,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    version = str(requested or PROMPT_V1).strip().lower()
    if version not in SUPPORTED_PROMPT_VERSIONS:
        raise ValueError(f"unsupported prompt version: {version}")
    if version == PROMPT_V2 and not prompt_v2_enabled(environment):
        raise ValueError(
            f"{PROMPT_V2} is disabled; enable {PROMPT_V2_FEATURE_ENV} only for an approved A/B"
        )
    if version == PROMPT_V3 and not prompt_v3_enabled(environment):
        raise ValueError(
            f"{PROMPT_V3} is disabled; enable {PROMPT_V3_FEATURE_ENV} only for an approved A/B"
        )
    return version


def normalize_generation_strategy(
    requested: Any,
    *,
    environment: Mapping[str, str] | None = None,
    allow_existing_single_pass: bool = False,
) -> str:
    strategy = str(requested or LEGACY_DOUBLE_PASS).strip().lower()
    if strategy not in SUPPORTED_GENERATION_STRATEGIES:
        raise ValueError(f"unsupported generation strategy: {strategy}")
    if (
        strategy == SINGLE_PASS
        and not allow_existing_single_pass
        and not single_pass_enabled(environment)
    ):
        raise ValueError(
            f"{SINGLE_PASS} is disabled; enable {SINGLE_PASS_FEATURE_ENV} only for an approved A/B"
        )
    return strategy


def _material_profile(context: Mapping[str, Any] | None) -> tuple[str, str]:
    values = dict(context or {})
    raw = values.get("material_profile")
    source = "material_profile" if raw is not None else "none"
    if isinstance(raw, Mapping):
        raw = raw.get("profile", raw.get("type"))
        source = "material_profile.profile"
    profile = str(raw or "unknown").strip().lower()
    aliases = {
        "opaque-material": "opaque",
        "transparent-material": "transparent",
        "reflective-material": "reflective",
        "mixed-material": "mixed",
    }
    profile = aliases.get(profile, profile)
    if profile not in MATERIAL_PROFILES:
        return "unknown", "invalid-material-profile"
    return profile, source


def resolve_material_prompt_route(
    requested_prompt_version: Any,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Restrict compact prompts to explicit, low-risk material evidence.

    This router never upgrades a baseline request to prompt_v3. It only decides
    whether an already experiment-gated prompt_v3 request may remain compact.
    Missing or sensitive material evidence falls back before provider work.
    """
    requested = str(requested_prompt_version or PROMPT_V1).strip().lower()
    if requested not in SUPPORTED_PROMPT_VERSIONS:
        raise ValueError(f"unsupported prompt version: {requested}")
    values = dict(context or {})
    profile, evidence_source = _material_profile(values)
    locks = values.get("intent_locks")
    locks = locks if isinstance(locks, Mapping) else {}
    count = _prompt_product_count(values)
    category = _clean_prompt_value(values.get("category") or "general").lower()
    structured_benefit = bool(
        category == "packaging"
        or (count is not None and count > 1)
        or locks.get("packaging_text")
        or locks.get("brand_color")
    )

    effective = requested
    eligible = False
    reason = "requested-version-preserved"
    if requested == PROMPT_V3:
        if profile in {"transparent", "reflective", "mixed"}:
            effective = PROMPT_V1
            reason = "sensitive-material-baseline"
        elif profile != "opaque":
            effective = PROMPT_V1
            reason = "material-evidence-required"
        elif not structured_benefit:
            effective = PROMPT_V1
            reason = "compact-benefit-not-demonstrated"
        else:
            eligible = True
            reason = "eligible-opaque-structured"

    return {
        "contract_version": MATERIAL_PROMPT_ROUTE_VERSION,
        "requested_prompt_version": requested,
        "effective_prompt_version": effective,
        "material_profile": profile,
        "material_evidence_source": evidence_source,
        "compact_candidate_eligible": eligible,
        "structured_benefit_signal": structured_benefit,
        "reason": reason,
        "provider_retry_authorized": False,
    }


def prompt_adapter_profile(model: str) -> dict[str, str]:
    model_key = str(model or "").strip().lower()
    if model_key.startswith("gemini-") and "image" in model_key:
        return {
            "id": "gemini-image-compact-v1",
            "directive": "以参考图为主体结构依据，先保持产品身份一致，再完成场景修改",
        }
    if model_key.startswith("gpt-image-2") or model_key == "tt-image-2":
        return {
            "id": "gpt-image-2-compact-v1",
            "directive": "按明确的自然语言约束编辑参考图，未点名的主体内容保持不变",
        }
    return {
        "id": "generic-image-compact-v1",
        "directive": "以参考图为主体依据，只执行列出的修改，不重构未点名内容",
    }


def _clean_prompt_value(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _prompt_product_count(values: Mapping[str, Any]) -> int | None:
    raw = values.get("product_count", values.get("quantity"))
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    return count if 1 <= count <= 24 else None


def prompt_v3_render_plan(
    *,
    context: Mapping[str, Any] | None,
    stage: str,
) -> dict[str, Any]:
    """Build the inspectable render plan used by the compact prompt candidate."""
    values = dict(context or {})
    product_name = _clean_prompt_value(
        values.get("product_name") or "参考图中的产品"
    )
    output_kind = _clean_prompt_value(
        values.get("output_kind") or "ecommerce-main"
    )
    angle = _clean_prompt_value(values.get("angle") or "auto").lower()
    platter = _clean_prompt_value(values.get("platter") or "auto").lower()
    fidelity = max(0, min(int(values.get("fidelity", 40)), 100))
    model_profile = prompt_adapter_profile(str(values.get("model") or ""))
    stage_key = _clean_prompt_value(stage or "primary").lower()
    user_request = _clean_prompt_value(
        values.get("user_request")
        or values.get("adjustment_instruction")
        or ""
    )

    if "adjust" in stage_key:
        objective = (
            f"只对{product_name}执行本次要求：{user_request}"
            if user_request
            else f"只修正{product_name}的指定问题"
        )
    elif "refine" in stage_key or stage_key.startswith("2-"):
        objective = f"只对{product_name}做轻度交付精修，正确内容保持不变"
        if user_request:
            objective += f"；继续满足本次要求：{user_request}"
    else:
        objective = f"将{product_name}制作成可直接交付的电商主图"
        if user_request:
            objective += f"；本次要求（最高优先）：{user_request}"

    category = _clean_prompt_value(values.get("category") or "general").lower()
    locks = values.get("intent_locks")
    locks = locks if isinstance(locks, Mapping) else {}
    product_count = _prompt_product_count(values)
    hard_constraints = ["主体外形、结构与关键识别特征不变"]
    hard_constraints.append(
        f"产品数量严格保持为{product_count}，不得增减或合并"
        if product_count is not None
        else "保持参考图中的产品数量，不得增减或合并"
    )
    hard_constraints.append(
        "品牌主色、产品固有色与关键材质不变"
        if locks.get("brand_color")
        else "产品固有颜色与关键材质不变"
    )
    if category == "packaging" or locks.get("packaging_text") or locks.get("logo"):
        hard_constraints.append("包装轮廓、可见文字、数字与品牌标志不变")

    angle_rule = {
        "keep": "保持原图角度和透视",
        "front": "正面平视，包装正面清晰",
        "45top": "约45度俯视",
        "30side": "约30度斜侧视角",
        "90top": "正俯视平铺",
    }.get(angle, "选择可信的商业展示角度")
    platter_rule = {
        "keep": "保留原有器皿类型",
        "remove": "移除器皿和托盘",
    }.get(platter, "器皿处理服从产品类型，不增加无关容器")
    allowed_change = (
        "仅调整背景和光线" if fidelity <= 25
        else "可轻调光影与构图" if fidelity <= 50
        else "可增强材质与商业表现" if fidelity <= 75
        else "可明显美化，但产品身份必须不变"
    )
    if values.get("source_cutoff"):
        allowed_change += "；补全原图被裁切的主体边缘，不发明第二个产品"
    output_spec = (
        values.get("output_spec")
        if isinstance(values.get("output_spec"), Mapping)
        else {}
    )
    ratio = _clean_prompt_value(
        output_spec.get("effective_ratio")
        or output_spec.get("ratio")
        or ""
    )
    resolution = _clean_prompt_value(
        output_spec.get("requested_resolution")
        or output_spec.get("resolution")
        or output_spec.get("size")
        or ""
    )
    output_bits = [output_kind]
    if ratio:
        output_bits.append(ratio)
    if resolution:
        output_bits.append(resolution)

    return {
        "version": PROMPT_V3_RENDER_PLAN_VERSION,
        "stage": stage_key,
        "product_name": product_name,
        "product_count": product_count,
        "user_request": user_request,
        "objective": objective,
        "hard_constraints": hard_constraints,
        "allowed_change": allowed_change,
        "platter_rule": platter_rule,
        "angle_rule": angle_rule,
        "background_rule": "纯白干净背景，柔和均匀光线，主体清晰居中",
        "output_bits": output_bits,
        "finish_rule": "边缘自然，透明和反光材质真实",
        "adapter_profile": model_profile,
    }


def _compile_prompt_v3(
    *,
    context: Mapping[str, Any] | None,
    stage: str,
) -> str:
    """Compile a compact render plan without carrying the legacy word stack.

    This candidate intentionally ignores ``template_prompt``. The original
    template is still frozen in the trace, so an experiment can reproduce and
    compare both candidates without silently mixing their instructions.
    """
    plan = prompt_v3_render_plan(context=context, stage=stage)

    return "\n".join((
        f"任务：{plan['objective']}。",
        f"必须：{'；'.join(plan['hard_constraints'])}；主体完整不裁切。",
        f"可调整：{plan['allowed_change']}；{plan['platter_rule']}。",
        f"画面：{plan['angle_rule']}；{plan['background_rule']}。",
        f"输出：{' / '.join(plan['output_bits'])}；{plan['finish_rule']}。",
        f"执行方式：{plan['adapter_profile']['directive']}。",
    ))


def compile_prompt_version(
    template_prompt: str,
    *,
    prompt_version: str,
    context: Mapping[str, Any] | None = None,
    stage: str = "primary",
) -> str:
    """Compile a versioned prompt while keeping v1 byte-for-byte unchanged."""
    version = str(prompt_version or PROMPT_V1).strip().lower()
    if version == PROMPT_V1:
        return str(template_prompt)
    if version == PROMPT_V3:
        return _compile_prompt_v3(context=context, stage=stage)
    if version != PROMPT_V2:
        raise ValueError(f"unsupported prompt version: {version}")

    values = dict(context or {})
    product_name = str(values.get("product_name") or "参考图中的产品").strip()
    output_kind = str(values.get("output_kind") or "ecommerce-main").strip()
    angle = str(values.get("angle") or "auto").strip()
    platter = str(values.get("platter") or "auto").strip()
    fidelity = max(0, min(int(values.get("fidelity", 40)), 100))
    stage_key = str(stage or "primary").lower()
    if "adjust" in stage_key:
        objective = f"只对{product_name}执行用户指定的局部调整，正确区域保持不变"
    elif "refine" in stage_key or stage_key.startswith("2-"):
        objective = f"精修{product_name}并提高商业交付完成度，不重新设计主体"
    else:
        objective = f"把参考图中的{product_name}转化为可直接交付的商业电商主图"

    platter_rule = {
        "keep": "保留并优化原有器皿，不替换器皿类型",
        "remove": "移除器皿和托盘，主体直接置于纯白背景",
    }.get(platter, "按产品类型处理器皿，但不得凭空增加无关容器")
    angle_rule = {
        "keep": "严格保持参考图的角度与透视",
        "front": "采用正面平视，包装正面和文字清晰",
        "45top": "采用约 45 度俯视的三分之四视角",
        "30side": "采用约 30 度斜侧视角",
        "90top": "采用正俯视平铺视角",
    }.get(angle, "选择适合产品的商业角度，并保持主体结构可信")
    fidelity_rule = (
        "只允许改变背景和光线" if fidelity <= 25
        else "允许轻度优化光影和构图" if fidelity <= 50
        else "允许增强材质和商业表现" if fidelity <= 75
        else "允许较明显的商业美化，但产品仍须可识别"
    )
    return "\n".join((
        f"任务目标：{objective}。",
        "不可破坏项：保持主体结构、产品数量、包装轮廓、可见文字、品牌色和关键材质特征；不要增加未请求的产品。",
        f"允许修改项：{fidelity_rule}；{platter_rule}。",
        f"场景、光线与构图：{angle_rule}；使用干净纯白影棚背景、柔和均匀灯光和完整不裁切的主体构图。",
        f"输出约束：输出类型为 {output_kind}；边缘自然清晰，透明与反光材质符合真实光学关系，结果应可用于商业交付。",
        f"基线细节要求：{str(template_prompt).strip()}",
    ))


def prompt_snapshot(
    *,
    template_prompt: str | None = None,
    base_prompt: str,
    compiled_prompt: str,
    negative_prompt: str,
    knowledge_evidence: Iterable[Any] | None = None,
    prompt_version: str = PROMPT_COMPILER_VERSION,
) -> dict[str, Any]:
    """Freeze both sides of knowledge enrichment without changing the prompt."""
    evidence = list(knowledge_evidence or [])
    return {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "prompt_version": str(prompt_version),
        "template_prompt_sha256": hashlib.sha256(
            str(template_prompt if template_prompt is not None else base_prompt).encode("utf-8")
        ).hexdigest(),
        "base_prompt_sha256": hashlib.sha256(str(base_prompt).encode("utf-8")).hexdigest(),
        "compiled_prompt_sha256": hashlib.sha256(
            str(compiled_prompt).encode("utf-8")
        ).hexdigest(),
        "negative_prompt_sha256": hashlib.sha256(
            str(negative_prompt).encode("utf-8")
        ).hexdigest(),
        "knowledge_snapshot_sha256": canonical_sha256(evidence),
        "knowledge_evidence_count": len(evidence),
    }


def capability_contract(model: str, provider_family: str = "") -> dict[str, Any]:
    model_key = str(model or "").strip()
    family = str(provider_family or "").strip()
    if not family:
        if model_key.startswith("gpt-image-2") or model_key == "tt-image-2":
            family = "gpt-image-2"
        elif model_key.startswith("gemini-") and "image" in model_key:
            family = "gemini-image"
        else:
            family = "generic-image"
    contract = dict(_CAPABILITY_CONTRACTS.get(family, _CAPABILITY_CONTRACTS["generic-image"]))
    return {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "adapter_version": PROVIDER_ADAPTER_VERSION,
        "model": model_key,
        **contract,
        "billing_telemetry": "not-exposed-by-current-adapter",
    }


def unavailable_billing_evidence() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "amount": None,
        "currency": None,
        "source": "provider-response-has-no-billing-field",
    }


def summarize_trace_timings(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build an offline baseline summary from immutable trace payloads."""
    stages: list[dict[str, Any]] = []
    billable_calls = 0
    priced_calls = 0
    total_elapsed_ms = 0.0
    for trace in traces:
        output = trace.get("output") if isinstance(trace.get("output"), Mapping) else {}
        elapsed = output.get("elapsed_ms")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            elapsed_value = max(0.0, float(elapsed))
            total_elapsed_ms += elapsed_value
            stages.append({
                "stage": str(trace.get("stage") or ""),
                "status": str(trace.get("status") or ""),
                "elapsed_ms": round(elapsed_value, 3),
            })
        if str(trace.get("stage") or "").startswith(("provider.image.", "vlm.")):
            if str(trace.get("status") or "") != "skipped":
                billable_calls += 1
                billing = output.get("billing") if isinstance(output, Mapping) else None
                if isinstance(billing, Mapping) and isinstance(billing.get("amount"), (int, float)):
                    priced_calls += 1
    return {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "stage_count": len(stages),
        "stages": stages,
        "summed_stage_elapsed_ms": round(total_elapsed_ms, 3),
        "billable_call_count": billable_calls,
        "priced_call_count": priced_calls,
        "cost_baseline_status": "available" if billable_calls and priced_calls == billable_calls else "incomplete",
    }
