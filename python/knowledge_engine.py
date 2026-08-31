# -*- coding: utf-8 -*-
"""Deterministic, read-only knowledge compiler for Product Atelier.

The compiler deliberately starts with structured Markdown routing instead of a
vector database.  It keeps every injected rule traceable to a source page and
never writes back to the user's vault.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PREFERRED_WINDOWS_VAULT = Path("D:/知识库")


def canonicalize_vault_path(path: str | Path) -> Path:
    """Return one stable path, resolving Windows junction compatibility aliases."""
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return candidate


def default_vault_path() -> Path:
    override = str(os.environ.get("PRODUCT_ATELIER_KNOWLEDGE_BASE", "")).strip()
    if override:
        return canonicalize_vault_path(override)
    if os.name == "nt" and PREFERRED_WINDOWS_VAULT.exists():
        return canonicalize_vault_path(PREFERRED_WINDOWS_VAULT)
    return canonicalize_vault_path(Path.home() / "Documents" / "知识库")


DEFAULT_VAULT = default_vault_path()
DESIGN_RELATIVE = Path("20 知识库") / "设计知识"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("\"'") for part in inner.split(",")]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value.strip("\"'")


def parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Parse the small frontmatter subset used by this vault without PyYAML."""
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", text, re.DOTALL)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for raw in match.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        meta[key.strip()] = _scalar(value)
    return meta, text[match.end():]


def clean_markdown(value: str, limit: int = 180) -> str:
    value = re.sub(r"!\[.*?\]\(.*?\)", "", value)
    value = re.sub(r"\[\[(?:[^]|]+\|)?([^]]+)\]\]", r"\1", value)
    value = re.sub(r"[`*_>#]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -|：:")
    return value[:limit].rstrip()


def extract_rule_lines(body: str) -> list[str]:
    """Extract concise bullets and table cells while ignoring prose/code blocks."""
    rules: list[str] = []
    in_code = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        candidate = ""
        if re.match(r"^(?:[-*+]\s+|\d+[.)、]\s*)", stripped):
            candidate = re.sub(r"^(?:[-*+]\s+|\d+[.)、]\s*)", "", stripped)
        elif stripped.startswith("|") and stripped.endswith("|") and "---" not in stripped:
            cells = [clean_markdown(cell, 90) for cell in stripped.strip("|").split("|")]
            cells = [cell for cell in cells if cell]
            if len(cells) >= 2:
                candidate = "；".join(cells)
        candidate = clean_markdown(candidate)
        if 8 <= len(candidate) <= 180 and candidate not in rules:
            rules.append(candidate)
    return rules


class KnowledgeCompiler:
    CATEGORY_FILES = {
        "food": "食品饮料类目规范.md",
        "beverage": "食品饮料类目规范.md",
        "beauty": "美妆护肤类目规范.md",
        "fashion": "服装配饰类目规范.md",
        "3c": "3C数码类目规范.md",
        "electronics": "3C数码类目规范.md",
    }
    BASE_FILES = (
        "CON-0009-设计知识体系宪法.md",
        "CHK-0009-Prompt自动组装流程.md",
        "通用产品摄影构图原则.md",
        "通用产品摄影灯光原则.md",
        "通用产品摄影色彩原则.md",
        "拍摄角度与透视原则.md",
        "背景与道具原则.md",
        "L1通用平面设计基础原则.md",
        "通用避坑清单.md",
    )
    SOURCE_ONLY_FILES = {"CON-0009-设计知识体系宪法.md", "CHK-0009-Prompt自动组装流程.md"}

    def __init__(self, vault_path: str | Path = DEFAULT_VAULT):
        self._lock = threading.RLock()
        self.vault_path = canonicalize_vault_path(vault_path)
        self.design_path = self._resolve_design_path(self.vault_path)
        self.documents: list[dict[str, Any]] = []
        self.by_name: dict[str, dict[str, Any]] = {}
        self.loaded_at = ""
        self.errors: list[dict[str, str]] = []
        self.reload()

    @staticmethod
    def _resolve_design_path(path: Path) -> Path:
        if path.name == "设计知识":
            return path
        direct = path / DESIGN_RELATIVE
        return direct if direct.exists() else path

    def set_path(self, path: str | Path) -> dict[str, Any]:
        with self._lock:
            self.vault_path = canonicalize_vault_path(path)
            self.design_path = self._resolve_design_path(self.vault_path)
        return self.reload()

    def reload(self) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        if self.design_path.exists():
            for path in sorted(self.design_path.rglob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8-sig")
                    meta, body = parse_markdown(text)
                    relative = str(path.relative_to(self.design_path)).replace("\\", "/")
                    documents.append({
                        "id": str(meta.get("id", "")),
                        "title": next((clean_markdown(line[2:], 120) for line in body.splitlines() if line.startswith("# ")), path.stem),
                        "name": path.name,
                        "path": str(path),
                        "relative_path": relative,
                        "section": relative.split("/", 1)[0] if "/" in relative else "root",
                        "summary": str(meta.get("summary", "")),
                        "status": str(meta.get("status", "")),
                        "review": str(meta.get("review", "")),
                        "updated": str(meta.get("updated", "")),
                        "mtime": path.stat().st_mtime,
                        "rules": extract_rule_lines(body),
                    })
                except Exception as exc:
                    errors.append({"path": str(path), "error": str(exc)})
        with self._lock:
            self.documents = documents
            self.by_name = {doc["name"]: doc for doc in documents}
            self.loaded_at = utc_now()
            self.errors = errors
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self.design_path.exists(),
                "vault_path": str(self.vault_path),
                "design_path": str(self.design_path),
                "loaded_at": self.loaded_at,
                "document_count": len(self.documents),
                "rule_count": sum(len(doc["rules"]) for doc in self.documents),
                "errors": list(self.errors[:20]),
                "read_only": True,
            }

    def _select_documents(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        wanted: list[str] = list(self.BASE_FILES[:2])
        category = str(context.get("category", "general")).lower()
        if category in self.CATEGORY_FILES:
            wanted.append(self.CATEGORY_FILES[category])
        output_kind = str(context.get("output_kind", "ecommerce-main"))
        if output_kind in {"ecommerce-main", "main", "single", "group-split"}:
            wanted.append("电商主图设计规范.md")
        wanted.extend(self.BASE_FILES[2:])
        if context.get("premium") or str(context.get("style", "")).lower() in {"premium", "luxury", "高级感"}:
            wanted.append("高级感的设计规律.md")
        if context.get("brand_profile"):
            wanted.extend(("品牌调性维度.md", "VI系统一致性原则.md"))
        return [self.by_name[name] for name in wanted if name in self.by_name]

    @staticmethod
    def build_creative_brief(context: dict[str, Any]) -> dict[str, Any]:
        """Normalize UI/task input into a stable, inspectable creative contract."""
        intent_locks = context.get("intent_locks") if isinstance(context.get("intent_locks"), dict) else {}
        output_spec = context.get("output_spec") if isinstance(context.get("output_spec"), dict) else {}
        return {
            "objective": str(context.get("objective", "将产品原图转化为可交付的商业图片")),
            "mode": str(context.get("mode", "single")),
            "category": str(context.get("category", "general")),
            "product_name": str(context.get("product_name", "")),
            "audience": str(context.get("audience", "")),
            "platform": str(context.get("platform", "ecommerce")),
            "output_kind": str(context.get("output_kind", "ecommerce-main")),
            "brand_profile": str(context.get("brand_profile", "")),
            "style": str(context.get("style", "clean-commercial")),
            "composition": {
                "angle": str(context.get("angle", "auto")),
                "platter": str(context.get("platter", "auto")),
                "background": str(context.get("background", "white-studio")),
            },
            "fidelity": int(context.get("fidelity", 40) or 40),
            "output_spec": {
                "ratio": str(output_spec.get("ratio", "1:1")),
                "format": str(output_spec.get("format", "JPG+transparent PNG")),
                "size": str(output_spec.get("size", "2048x2048")),
            },
            "user_request": str(context.get("user_request", "")),
            "intent_locks": intent_locks,
        }

    @staticmethod
    def _intent_lock_rules(brief: dict[str, Any]) -> list[str]:
        locks = brief.get("intent_locks") or {}
        rules: list[str] = []
        mapping = {
            "subject_shape": "严格保持主体外形、比例、结构与关键识别特征，不得重绘或变形",
            "packaging_text": "严格保留包装上的文字、数字、标签位置与可读性，不得改字、漏字或生成伪文字",
            "brand_color": "严格保持品牌主色及产品固有色，不得产生偏色或擅自换色",
            "logo": "严格保持品牌标志的形状、位置、比例与清晰度，不得重构标志",
            "angle": "严格保持原图拍摄角度、透视与产品朝向",
            "product_count": "严格保持产品数量，不得新增、删减或合并主体",
        }
        for key, rule in mapping.items():
            value = locks.get(key)
            if value is True or str(value).lower() in {"true", "strict", "locked", "keep"}:
                rules.append(rule)
            elif value not in (None, False, "", "false", "off"):
                rules.append(f"{rule}；指定值：{value}")
        return rules

    @staticmethod
    def _prompt_rule(text: str) -> str:
        """Keep executable rules; omit explanatory/frontmatter-like list items."""
        blocked = ("层级：", "适用前提：", "适用：", "不适用", "原理：", "为什么：")
        if text.startswith(blocked):
            return ""
        if text.startswith("内容："):
            text = text[3:].strip()
        if "；" in text and text.split("；", 1)[0] in {"层级", "平台", "维度", "适用前提", "规则层"}:
            return ""
        if re.search(r"\d+×\d+px|≤\d+MB", text, re.IGNORECASE):
            return ""
        return text

    @staticmethod
    def _dedupe(rules: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rule in rules:
            key = re.sub(r"\W+", "", rule["text"]).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(rule)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _v3_rule_conflicts(context: dict[str, Any], text: str) -> str:
        """Return the task field that makes one soft rule unsafe to inject."""
        compact = re.sub(r"\s+", "", str(text)).lower()
        platter = str(context.get("platter", "auto")).lower()
        if platter == "remove" and any(token in compact for token in (
            "保留器皿", "保留盘", "保留托盘", "保持器皿",
        )):
            return "platter"
        if platter == "keep" and any(token in compact for token in (
            "移除器皿", "去除器皿", "无任何器皿", "不要器皿",
        )):
            return "platter"
        angle = str(context.get("angle", "auto")).lower()
        if angle == "keep" and any(token in compact for token in (
            "45度", "俯拍", "俯视", "正面平视", "正俯视",
        )):
            return "angle"
        background = str(context.get("background", "white-studio")).lower()
        if background in {"white", "pure-white", "white-studio"} and any(
            token in compact for token in ("深色背景", "暗色背景", "彩色背景")
        ):
            return "background"
        locks = context.get("intent_locks")
        locks = locks if isinstance(locks, dict) else {}
        if locks.get("packaging_text") and any(token in compact for token in (
            "去除文字", "无文字", "删除文字", "抹除文字",
        )):
            return "packaging_text"
        if locks.get("logo") and any(token in compact for token in (
            "去除logo", "无logo", "删除logo", "移除标志",
        )):
            return "logo"
        return ""

    @classmethod
    def _select_v3_rules(
        cls,
        rules: list[dict[str, Any]],
        context: dict[str, Any],
        *,
        count_limit: int,
        character_budget: int,
        kind: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        used = 0
        seen: set[str] = set()
        for rule in rules:
            text = str(rule.get("text", "")).strip()
            key = re.sub(r"\W+", "", text).lower()
            if not key or key in seen:
                ignored.append({"kind": kind, "reason": "duplicate", "text": text})
                continue
            seen.add(key)
            # Negative rules describe conditions to avoid, so a phrase such as
            # "不要使用深色背景" supports a white-background task rather than
            # conflicting with it. Task-conflict pruning applies to directives.
            conflict_field = (
                "" if kind == "negative_rule"
                else cls._v3_rule_conflicts(context, text)
            )
            if conflict_field:
                ignored.append({
                    "kind": kind,
                    "reason": "conflicts-with-task",
                    "field": conflict_field,
                    "text": text,
                })
                continue
            if len(selected) >= count_limit:
                ignored.append({"kind": kind, "reason": "count-budget", "text": text})
                continue
            if used + len(text) > character_budget:
                ignored.append({"kind": kind, "reason": "character-budget", "text": text})
                continue
            selected.append(rule)
            used += len(text)
        return selected, ignored

    @classmethod
    def _compact_v3_bundle(
        cls,
        bundle: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        positives, ignored_positive = cls._select_v3_rules(
            list(bundle.get("positive_rules") or []),
            context,
            count_limit=3,
            character_budget=180,
            kind="positive_rule",
        )
        negatives, ignored_negative = cls._select_v3_rules(
            list(bundle.get("negative_rules") or []),
            context,
            count_limit=1,
            character_budget=80,
            kind="negative_rule",
        )
        locks: list[str] = []
        ignored_locks: list[dict[str, Any]] = []
        used_lock_chars = 0
        for raw in bundle.get("intent_lock_rules") or []:
            text = str(raw).strip()
            if len(locks) >= 5:
                ignored_locks.append({"kind": "intent_lock", "reason": "count-budget", "text": text})
                continue
            if used_lock_chars + len(text) > 180:
                ignored_locks.append({"kind": "intent_lock", "reason": "character-budget", "text": text})
                continue
            locks.append(text)
            used_lock_chars += len(text)

        sources: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for rule in positives + negatives:
            source = rule.get("source") if isinstance(rule, dict) else None
            if not isinstance(source, dict):
                continue
            source_key = str(source.get("id") or source.get("path") or source.get("title") or "")
            if not source_key or source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            sources.append(source)

        ignored = ignored_positive + ignored_negative + ignored_locks
        conflicts = list(bundle.get("conflicts") or [])
        for item in ignored:
            if item.get("reason") == "conflicts-with-task":
                conflicts.append({
                    "field": item.get("field", "task"),
                    "winner": "task",
                    "message": "任务硬约束高于被裁剪的知识规则。",
                })
        return {
            **bundle,
            "intent_lock_rules": locks,
            "positive_rules": positives,
            "negative_rules": negatives,
            "sources": sources,
            "conflicts": conflicts,
            "ignored_rules": ignored,
            "rule_budget": {
                "intent_locks": 5,
                "positive_rules": 3,
                "negative_rules": 1,
                "lock_characters": 180,
                "positive_characters": 180,
                "negative_characters": 80,
            },
        }

    @staticmethod
    def _v3_negative_prompt(
        context: dict[str, Any],
        negative_rules: list[dict[str, Any]],
    ) -> str:
        locks = context.get("intent_locks")
        locks = locks if isinstance(locks, dict) else {}
        category = str(context.get("category", "general")).lower()
        terms = ["主体结构或数量改变", "主体裁切或边缘残缺"]
        if category == "packaging" or locks.get("packaging_text") or locks.get("logo"):
            terms.append("包装文字数字或品牌标志错误")
        else:
            terms.append("伪文字或无关水印")
        for rule in negative_rules:
            text = str(rule.get("text", "")).strip().lstrip("❌").strip()
            if text and text not in terms:
                terms.append(text)
            if len(terms) >= 4:
                break
        if len(terms) < 4:
            terms.append("背景脏污或明显偏色")
        return "，".join(terms[:4])

    @staticmethod
    def _detect_conflicts(context: dict[str, Any], rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        corpus = "\n".join(rule["text"] for rule in rules)
        conflicts: list[dict[str, Any]] = []
        platter = str(context.get("platter", "auto"))
        if platter == "remove" and any(token in corpus for token in ("保留器皿", "保留盘", "器皿保留")):
            conflicts.append({"field": "platter", "winner": "task", "message": "本次任务要求移除器皿，高于知识库中的保留建议。"})
        angle = str(context.get("angle", "auto"))
        if angle == "keep" and any(token in corpus for token in ("45度", "俯拍", "正面平视")):
            conflicts.append({"field": "angle", "winner": "intent-lock", "message": "原始角度锁定，高于知识库的推荐角度。"})
        background = str(context.get("background", ""))
        if background in {"white", "pure-white"} and any(token in corpus for token in ("深色背景", "暗色背景")):
            conflicts.append({"field": "background", "winner": "task", "message": "纯白输出规格高于知识库的风格背景建议。"})
        return conflicts

    def compile(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {})
        brief = self.build_creative_brief(context)
        approved_memory = self._approved_memory_rules(context)
        with self._lock:
            selected = self._select_documents(context)
        positives: list[dict[str, Any]] = []
        negatives: list[dict[str, Any]] = []
        for doc in selected:
            if doc["name"] in self.SOURCE_ONLY_FILES:
                continue
            source = {"id": doc["id"], "title": doc["title"], "path": doc["path"], "relative_path": doc["relative_path"]}
            positive_count = 0
            negative_count = 0
            for raw_text in doc["rules"]:
                text = self._prompt_rule(raw_text)
                if not text:
                    continue
                item = {"text": text, "source": source}
                is_negative = doc["section"] == "负面偏好" or text.startswith("❌")
                if is_negative and negative_count < 3:
                    negatives.append(item)
                    negative_count += 1
                elif not is_negative and positive_count < 2:
                    positives.append(item)
                    positive_count += 1
        positives = self._dedupe(positives, 10)
        negatives = self._dedupe(negatives, 14)
        # Scope-resolved memory rules are prepended so they survive the enrichment
        # slice. Explicit task intent locks are still compiled separately as the
        # highest-priority, non-overridable constraints.
        memory_sources: list[dict[str, Any]] = []
        memory_rules: list[dict[str, Any]] = []
        for item in approved_memory:
            source = {
                "id": item["id"],
                "title": item["label"],
                "path": "",
                "relative_path": "记忆反馈/已批准",
            }
            memory_rules.append({
                "text": f"已批准记忆反馈：{item['text']}",
                "source": source,
            })
            memory_sources.append(source)
        positives = (memory_rules + positives)[:10]
        all_rules = positives + negatives
        sources = []
        seen_paths: set[str] = set()
        for doc in selected:
            if doc["path"] in seen_paths:
                continue
            seen_paths.add(doc["path"])
            sources.append({
                "id": doc["id"],
                "title": doc["title"],
                "path": doc["path"],
                "relative_path": doc["relative_path"],
            })
        for source in memory_sources:
            if not any(existing.get("id") == source["id"] for existing in sources):
                sources.append(source)
        return {
            "compiled_at": utc_now(),
            "context": context,
            "creative_brief": brief,
            "intent_lock_rules": self._intent_lock_rules(brief),
            "positive_rules": positives,
            "negative_rules": negatives,
            "sources": sources,
            "conflicts": self._detect_conflicts(context, all_rules),
            "fallback": not bool(sources),
        }

    @staticmethod
    def _approved_memory_rules(context: dict[str, Any]) -> list[dict[str, str]]:
        """Normalize approved memory rules passed in by the generation/preview layer.

        The knowledge engine stays ledger-agnostic: callers resolve approved feedback
        from the runbook/ledger and hand over a small, validated list here. Each item
        must carry ``text`` (the executable directive), ``label`` and ``id`` so the
        prompt enrichment and the UI can trace it back to a real, user-approved rule.
        """
        raw = context.get("approved_memory_rules") or []
        if not isinstance(raw, list):
            return []
        rules: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            rules.append({
                "id": str(item.get("id", "")).strip() or f"memory:{hash(text)}",
                "label": str(item.get("label", "")).strip() or "已批准记忆反馈",
                "text": text,
                "scope_type": str(item.get("scope_type", "designer")),
                "priority": str(item.get("priority", "")),
            })
        return rules[:5]

    def enrich_prompt(self, prompt: str, negative_prompt: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {})
        bundle = self.compile(context)
        compact_v3 = str(context.get("prompt_version") or "").lower() == "prompt_v3"
        if compact_v3:
            bundle = self._compact_v3_bundle(bundle, context)
        positive_addition = "；".join(rule["text"] for rule in bundle["positive_rules"][:10])
        negative_addition = "，".join(rule["text"] for rule in bundle["negative_rules"][:10])
        lock_addition = "；".join(bundle["intent_lock_rules"])
        enriched_prompt = prompt
        if lock_addition:
            label = "最高优先" if compact_v3 else "不可破坏约束（最高优先级）"
            enriched_prompt += f"。{label}：{lock_addition}"
        if positive_addition:
            label = "相关知识" if compact_v3 else "知识库设计约束"
            enriched_prompt += f"。{label}：{positive_addition}"
        if compact_v3:
            enriched_negative = self._v3_negative_prompt(
                context, list(bundle.get("negative_rules") or [])
            )
        else:
            enriched_negative = "，".join(part for part in (negative_prompt, negative_addition) if part)
        return {
            **bundle,
            "prompt": enriched_prompt,
            "negative_prompt": enriched_negative,
        }

    @staticmethod
    def execution_evidence(bundle: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Freeze the exact rules and sources used by one prompt execution.

        Generations keep ``knowledge_refs`` for backwards compatibility. Execution
        traces additionally store rule text so a completed job can later explain the
        exact constraints it used without recompiling the live knowledge vault.
        """
        payload = dict(bundle or {})
        evidence: list[dict[str, Any]] = []
        for text in payload.get("intent_lock_rules") or []:
            value = str(text).strip()
            if value:
                evidence.append({"kind": "intent_lock", "text": value})
        for kind, key in (("positive_rule", "positive_rules"), ("negative_rule", "negative_rules")):
            for item in payload.get(key) or []:
                if isinstance(item, dict):
                    value = str(item.get("text", "")).strip()
                    source = item.get("source")
                else:
                    value = str(item).strip()
                    source = None
                if not value:
                    continue
                row: dict[str, Any] = {"kind": kind, "text": value}
                if isinstance(source, dict):
                    row["source"] = source
                evidence.append(row)
        for source in payload.get("sources") or []:
            if isinstance(source, dict):
                evidence.append({"kind": "source", "source": source})
        return evidence
