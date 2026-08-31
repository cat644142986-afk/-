# -*- coding: utf-8 -*-
"""Evidence-driven Design DNA suggestion compiler.

This module never writes the user's formal knowledge base. It converts repeated,
explicit feedback into reviewable candidates stored in the local ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any


def _compact(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:\-—_]+", "", str(text or "").strip().lower())


def _value_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


MEMORY_SCOPE_PRIORITY = {
    "designer": 100,
    "category": 200,
    "brand": 300,
    "project": 400,
}


def resolve_approved_memory_rules(
    suggestions: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Choose one approved value per rule using the documented scope priority."""
    scope = dict(context or {})
    category = str(scope.get("category") or "general").strip()
    brand = str(scope.get("brand_profile") or "").strip()
    project = str(scope.get("project_name") or "").strip()
    designer = str(scope.get("designer_profile") or "default").strip()
    winners: dict[str, dict[str, Any]] = {}
    for item in suggestions:
        if not isinstance(item, dict) or str(item.get("status") or "") != "approved":
            continue
        scope_type = str(item.get("scope_type") or "designer").strip()
        scope_id = str(item.get("scope_id") or "").strip()
        item_category = str(item.get("category") or "general").strip()
        matches = {
            "designer": scope_id in {"", "default", designer},
            "category": bool(category and category != "general")
            and (scope_id == category or item_category == category),
            "brand": bool(brand) and scope_id == brand,
            "project": bool(project) and scope_id == project,
        }.get(scope_type, False)
        if not matches:
            continue
        proposed = item.get("proposed_value")
        proposed = proposed if isinstance(proposed, dict) else {}
        directive = str(proposed.get("directive") or "").strip()
        if not directive:
            continue
        rule_key = str(item.get("rule_key") or item.get("id") or "").strip()
        candidate = {
            "id": str(item.get("id") or rule_key),
            "rule_key": rule_key,
            "label": str(proposed.get("label") or rule_key or "已批准记忆反馈"),
            "text": directive,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "category": item_category,
            "priority": MEMORY_SCOPE_PRIORITY.get(scope_type, 0),
            "created_at": str(item.get("created_at") or ""),
        }
        existing = winners.get(rule_key)
        if existing is None or (
            candidate["priority"], candidate["created_at"], candidate["id"]
        ) > (
            existing["priority"], existing["created_at"], existing["id"]
        ):
            winners[rule_key] = candidate
    return sorted(
        winners.values(),
        key=lambda item: (-int(item["priority"]), item["rule_key"], item["id"]),
    )


class MemoryEngine:
    """Aggregate repeated feedback while preserving evidence and contradictions."""

    REJECT_PATTERNS = (
        (r"阴影.{0,4}(太重|过重|太黑|过黑|太硬)", "shadow.intensity", "lighter", "减轻阴影", "后续同类任务使用更轻、更柔的阴影"),
        (r"阴影.{0,4}(太浅|太淡|不明显|没有)", "shadow.intensity", "stronger", "加强阴影", "后续同类任务适度加强阴影层次"),
        (r"(光线|光影|高光).{0,4}(太硬|过硬|刺眼)", "lighting.softness", "softer", "使用更柔的光", "降低硬光和刺眼高光，优先更柔和的商业光线"),
        (r"(光线|光影).{0,4}(太平|太软|没层次)", "lighting.softness", "firmer", "增加光影层次", "保持商业清晰度并增加适度明暗层次"),
        (r"(饱和|颜色).{0,4}(太高|过艳|太艳|太鲜)", "color.saturation", "lower", "降低饱和度", "降低过艳和不自然的饱和度"),
        (r"(饱和|颜色).{0,4}(太低|太灰|发灰|没精神)", "color.saturation", "higher", "提高饱和度", "适度提高饱和度并保持产品固有色"),
        (r"(偏黄|太黄|太暖|过暖)", "color.temperature", "cooler", "减少暖黄偏色", "减少暖黄偏色，保持中性产品色"),
        (r"(偏蓝|太蓝|太冷|过冷)", "color.temperature", "warmer", "减少冷蓝偏色", "减少冷蓝偏色，适度回暖"),
        (r"(构图|产品|主体).{0,5}(太满|太大|顶边|太挤)", "composition.product_scale", "smaller", "增加画面留白", "缩小主体占比并增加安全留白"),
        (r"(构图|产品|主体).{0,5}(太小|太空|留白太多)", "composition.product_scale", "larger", "提高主体占比", "提高主体占比，减少无效留白"),
        (r"(背景).{0,5}(不白|发灰|不纯|脏)", "background.cleanliness", "pure", "保持背景干净", "优先纯净、无杂色的交付背景"),
        (r"(包装字|包装文字|文字|标签).{0,7}(错|变形|乱码|不清|糊|改了|少了)", "intent_lock.packaging_text", True, "锁定包装文字", "严格锁定包装文字、数字和标签可读性"),
        (r"(logo|标志|商标).{0,7}(错|变形|不清|糊|改了|少了)", "intent_lock.logo", True, "锁定 Logo", "严格锁定 Logo 的形状、比例、位置和清晰度"),
        (r"(主体|产品|瓶型|外形).{0,7}(变形|不像|改了|走样)", "intent_lock.subject_shape", True, "锁定主体外形", "严格保持产品外形、比例和关键识别特征"),
        (r"(角度|透视|朝向).{0,7}(变了|不对|改了|跑了)", "intent_lock.angle", True, "锁定拍摄角度", "严格保持原图角度、透视和产品朝向"),
        (r"(少了|多了|数量不对|产品数量)", "intent_lock.product_count", True, "锁定产品数量", "严格保持产品数量，不新增、删减或合并主体"),
        (r"(品牌色|产品颜色|固有色).{0,7}(不对|变了|偏色|改了)", "intent_lock.brand_color", True, "锁定品牌色", "严格保持品牌主色和产品固有色"),
    )

    POSITIVE_PATTERNS = (
        (r"(喜欢|满意|采用).{0,8}(柔光|柔和|自然光)", "lighting.softness", "soft", "偏好柔和光线", "同类任务优先延续柔和、自然的商业光线"),
        (r"(喜欢|满意|采用).{0,8}(留白|呼吸感|不拥挤)", "composition.spacing", "generous", "偏好克制留白", "同类任务优先保留克制且有呼吸感的构图"),
        (r"(喜欢|满意|采用).{0,8}(自然阴影|柔和阴影)", "shadow.style", "natural", "偏好自然阴影", "同类任务优先延续自然、不过度的接触阴影"),
        (r"(喜欢|满意|采用).{0,8}(暖调|暖色|温暖)", "color.temperature", "warm", "偏好暖调", "同类任务可优先尝试克制暖调"),
        (r"(喜欢|满意|采用).{0,8}(冷调|冷色|清冷)", "color.temperature", "cool", "偏好冷调", "同类任务可优先尝试克制冷调"),
    )

    def __init__(self, ledger: Any):
        self.ledger = ledger

    @staticmethod
    def _scope(feedback: dict[str, Any]) -> tuple[str, str, str]:
        category = str(feedback.get("category") or "general")
        brand = str(feedback.get("brand_profile") or "").strip()
        if brand:
            return "brand", brand, category
        if category and category != "general":
            return "category", category, category
        designer = str(feedback.get("designer_profile") or "default")
        return "designer", designer, "general"

    def _claims(self, feedback: dict[str, Any]) -> list[dict[str, Any]]:
        reason = str(feedback.get("reason") or "").strip()
        if len(_compact(reason)) < 2:
            return []
        signal = str(feedback.get("signal") or "note")
        patterns = self.POSITIVE_PATTERNS if signal == "adopted" else self.REJECT_PATTERNS
        claims: list[dict[str, Any]] = []
        for pattern, rule_key, value, label, directive in patterns:
            if re.search(pattern, reason, re.IGNORECASE):
                claims.append({
                    "rule_key": rule_key,
                    "value": value,
                    "label": label,
                    "directive": directive,
                    "min_support": 2,
                })
        if claims:
            return claims

        # Unknown language is not discarded, but it needs three independent
        # sessions with the same normalized judgment before becoming a card.
        normalized = _compact(reason)
        key_hash = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
        direction = "prefer" if signal == "adopted" else "avoid"
        return [{
            "rule_key": f"feedback.recurring.{key_hash}",
            "value": {"direction": direction, "text": reason},
            "label": "重复设计判断",
            "directive": reason,
            "min_support": 3,
        }]

    def synthesize(self, *, limit: int = 500) -> dict[str, Any]:
        feedback_rows = self.ledger.list_feedback(limit=limit)
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        claim_count = 0

        for feedback in feedback_rows:
            scope_type, scope_id, category = self._scope(feedback)
            for claim in self._claims(feedback):
                claim_count += 1
                grouped[(scope_type, scope_id, category, claim["rule_key"])].append({
                    "claim": claim,
                    "feedback": feedback,
                })

        suggestions: list[dict[str, Any]] = []
        for (scope_type, scope_id, category, rule_key), entries in grouped.items():
            by_value: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for entry in entries:
                by_value[_value_key(entry["claim"]["value"])].append(entry)
            dominant_key, support_entries = max(by_value.items(), key=lambda item: len(item[1]))
            distinct_sessions = {entry["feedback"]["session_id"] for entry in support_entries}
            min_support = max(entry["claim"].get("min_support", 2) for entry in support_entries)
            if len(distinct_sessions) < min_support:
                continue

            contradiction_entries = [
                entry for value_key, value_entries in by_value.items()
                if value_key != dominant_key for entry in value_entries
            ]
            support_count = len(support_entries)
            contradiction_count = len(contradiction_entries)
            total = support_count + contradiction_count
            support_ratio = support_count / max(total, 1)
            if support_ratio < (2 / 3):
                self.ledger.dismiss_pending_memory_rule(
                    scope_type,
                    rule_key,
                    scope_id=scope_id,
                    category=category,
                )
                continue

            claim = support_entries[0]["claim"]
            confidence = min(
                0.95,
                max(0.0, 0.48 + min(support_count, 5) * 0.09 + support_ratio * 0.12 - min(contradiction_count, 3) * 0.04),
            )
            evidence = []
            for entry in support_entries[:8]:
                feedback = entry["feedback"]
                structured = (
                    feedback.get("structured")
                    if isinstance(feedback.get("structured"), dict)
                    else {}
                )
                evidence.append({
                    "feedback_id": feedback["id"],
                    "session_id": feedback["session_id"],
                    "generation_id": feedback.get("generation_id"),
                    "job_id": structured.get("job_id"),
                    "review_id": structured.get("review_id"),
                    "result_asset_id": (
                        structured.get("result_asset_id") or feedback.get("asset_id")
                    ),
                    "signal": feedback.get("signal"),
                    "reason": feedback.get("reason"),
                    "mode": feedback.get("mode"),
                    "category": feedback.get("category"),
                    "created_at": feedback.get("created_at"),
                })
            proposed = {
                "value": claim["value"],
                "label": claim["label"],
                "directive": claim["directive"],
                "support_count": support_count,
                "distinct_sessions": len(distinct_sessions),
                "min_support": min_support,
                "contradiction_count": contradiction_count,
                "contradiction_examples": [
                    entry["feedback"].get("reason") for entry in contradiction_entries[:3]
                ],
            }
            suggestion = self.ledger.upsert_memory_suggestion(
                scope_type,
                rule_key,
                proposed,
                scope_id=scope_id,
                category=category,
                evidence=evidence,
                confidence=confidence,
            )
            if suggestion.get("status") == "pending":
                suggestions.append(suggestion)

        return {
            "feedback_considered": len(feedback_rows),
            "claims_extracted": claim_count,
            "groups_considered": len(grouped),
            "pending_suggestions": len(suggestions),
            "suggestions": suggestions,
        }

    def learning_receipt(
        self,
        feedback: dict[str, Any] | None,
        *,
        feedback_rows: list[dict[str, Any]] | None = None,
        suggestions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Describe the durable learning outcome for one piece of feedback."""
        if not feedback:
            return {
                "status": "reviewed",
                "extracted_rule": False,
                "independent_sessions": 0,
                "threshold": 0,
                "suggestion_id": "",
                "suggestion_status": "",
                "next_action": "none",
            }
        claims = self._claims(feedback)
        if not claims:
            return {
                "status": "no_rule_extracted",
                "extracted_rule": False,
                "independent_sessions": 1,
                "threshold": 0,
                "suggestion_id": "",
                "suggestion_status": "",
                "next_action": "add_specific_reason",
            }

        rows = list(feedback_rows) if feedback_rows is not None else self.ledger.list_feedback(limit=2000)
        if suggestions is None:
            suggestions = []
            for status in ("pending", "approved", "rejected", "dismissed", "disabled"):
                suggestions.extend(self.ledger.list_memory_suggestions(status=status, limit=200))
        target_scope = self._scope(feedback)
        feedback_id = str(feedback.get("id") or "")
        candidates: list[dict[str, Any]] = []
        for claim in claims:
            matching_sessions: set[str] = set()
            contradiction_count = 0
            for row in rows:
                if self._scope(row) != target_scope:
                    continue
                row_claims = self._claims(row)
                same_rule = [entry for entry in row_claims if entry["rule_key"] == claim["rule_key"]]
                if any(_value_key(entry["value"]) == _value_key(claim["value"]) for entry in same_rule):
                    matching_sessions.add(str(row.get("session_id") or ""))
                elif same_rule:
                    contradiction_count += 1
            threshold = int(claim.get("min_support", 2))
            related_suggestion = next((
                suggestion for suggestion in suggestions
                if str(suggestion.get("scope_type") or "") == target_scope[0]
                and str(suggestion.get("scope_id") or "") == target_scope[1]
                and str(suggestion.get("category") or "") == target_scope[2]
                and str(suggestion.get("rule_key") or "") == claim["rule_key"]
                and any(
                    str(item.get("feedback_id") or "") == feedback_id
                    for item in suggestion.get("evidence") or []
                    if isinstance(item, dict)
                )
            ), None)
            candidates.append({
                "claim": claim,
                "independent_sessions": len(matching_sessions),
                "threshold": threshold,
                "contradiction_count": contradiction_count,
                "suggestion": related_suggestion,
            })
        best = max(
            candidates,
            key=lambda entry: (
                bool(entry["suggestion"]),
                min(entry["independent_sessions"] / max(entry["threshold"], 1), 1),
                entry["independent_sessions"],
            ),
        )
        suggestion = best["suggestion"]
        if suggestion:
            status = str(suggestion.get("status") or "pending")
            next_action = "review_suggestion" if status == "pending" else "none"
        elif best["independent_sessions"] < best["threshold"]:
            status = "accumulating"
            next_action = "collect_independent_evidence"
        elif best["contradiction_count"]:
            status = "conflicting_evidence"
            next_action = "review_conflict"
        else:
            status = "ready_to_suggest"
            next_action = "form_suggestion"
        return {
            "status": status,
            "extracted_rule": True,
            "rule_key": best["claim"]["rule_key"],
            "independent_sessions": best["independent_sessions"],
            "threshold": best["threshold"],
            "suggestion_id": str(suggestion.get("id") or "") if suggestion else "",
            "suggestion_status": str(suggestion.get("status") or "") if suggestion else "",
            "next_action": next_action,
        }
