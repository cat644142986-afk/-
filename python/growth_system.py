# -*- coding: utf-8 -*-
"""Read-only Growth System projection for Product Atelier.

This module deliberately does not introduce a second memory or task store. It
projects two existing sources into the current Governor input:

* reviewed Obsidian Markdown pages, indexed read-only; and
* explicit result feedback already stored in the Atelier ledger.

Only compact, relevant directives enter the prompt. Full Markdown bodies and
binary assets remain at their original sources and are referenced by stable
identities in the frozen task snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from knowledge_engine import clean_markdown, extract_rule_lines
except ImportError:  # Allows importing as python.growth_system during tests.
    from python.knowledge_engine import clean_markdown, extract_rule_lines


GROWTH_CONTEXT_VERSION = "growth-context-v1"
OBSIDIAN_INDEX_VERSION = "obsidian-readonly-index-v1"
CASE_PROJECTION_VERSION = "ledger-case-projection-v1"

_EXCLUDED_KNOWLEDGE_DIRECTORIES = frozenset({
    "待整理",
    "兼容入口",
    "来源页",
    "体检报告",
})
_ACTIVE_PAGE_TYPES = frozenset({
    "analysis",
    "checklist",
    "concept",
    "decision",
    "lesson",
    "personal_knowledge",
    "project",
})
_GENERIC_TOKENS = frozenset({
    "设计", "图片", "生成", "当前", "结果", "任务", "画面", "产品",
    "image", "design", "result", "task", "current", "general",
})
_REASON_CODE_LABELS = {
    "subject_accurate": "主体准确",
    "packaging_clean": "包装文字清楚",
    "composition_ready": "构图可直接使用",
    "lighting_natural": "光影自然",
    "color_material_right": "色彩与材质准确",
    "subject_scale": "主体比例或数量需要调整",
    "packaging_text": "包装文字需要调整",
    "composition_crop": "构图或裁切需要调整",
    "perspective_shape": "透视或形体需要调整",
    "lighting_shadow": "光影或阴影需要调整",
    "color_material": "色彩或材质需要调整",
    "background_scene": "背景或场景需要调整",
    "detail_artifact": "存在细节瑕疵",
    "product_identity_wrong": "商品特征不准确",
    "quantity_wrong": "商品数量不准确",
    "packaging_unusable": "包装文字不可用",
    "composition_direction_wrong": "构图方向不符合要求",
    "style_mismatch": "风格不符合要求",
    "severe_distortion": "存在严重变形",
    "background_wrong": "背景方向不符合要求",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _compact(value: Any, maximum: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t-—，。；;:")[:maximum]


def _frontmatter_scalar(value: str) -> Any:
    candidate = value.strip()
    if not candidate:
        return ""
    if candidate.startswith("[") and candidate.endswith("]"):
        body = candidate[1:-1].strip()
        if not body:
            return []
        return [item.strip().strip("\"'") for item in body.split(",") if item.strip()]
    if candidate.casefold() in {"true", "false"}:
        return candidate.casefold() == "true"
    return candidate.strip("\"'")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the vault's stable YAML subset, including block string lists."""

    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", text, re.DOTALL)
    if not match:
        return {}, text
    metadata: dict[str, Any] = {}
    active_list = ""
    for raw_line in match.group(1).splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if active_list and re.match(r"^\s+-\s+", line):
            metadata.setdefault(active_list, []).append(
                _frontmatter_scalar(re.sub(r"^\s+-\s+", "", line))
            )
            continue
        active_list = ""
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if not value:
            metadata[key] = []
            active_list = key
        else:
            metadata[key] = _frontmatter_scalar(value)
    return metadata, text[match.end():]


def _wikilinks(text: str) -> list[str]:
    links: list[str] = []
    for target in re.findall(r"\[\[([^\]]+)\]\]", text):
        normalized = _compact(target.split("|", 1)[0].split("#", 1)[0], 160)
        if normalized and normalized not in links:
            links.append(normalized)
    return links


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _tokens(value: Any) -> set[str]:
    text = _flatten_text(value).casefold()
    result = {
        match.group(0)
        for match in re.finditer(r"[a-z0-9][a-z0-9._-]{1,}|[\u3400-\u9fff]{2,}", text)
    }
    for segment in re.findall(r"[\u3400-\u9fff]{3,}", text):
        result.update(segment[index:index + 2] for index in range(len(segment) - 1))
        if len(segment) <= 12:
            result.add(segment)
    return {item for item in result if item not in _GENERIC_TOKENS}


def _task_conflict(context: Mapping[str, Any], text: str) -> str:
    compact = re.sub(r"\s+", "", str(text)).casefold()
    locks = context.get("intent_locks")
    locks = locks if isinstance(locks, Mapping) else {}
    background = str(context.get("background") or "").casefold()
    if background in {"white", "pure-white", "white-studio"} and any(
        token in compact for token in ("深色背景", "暗色背景", "彩色背景")
    ):
        return "background"
    if locks.get("packaging_text") and any(
        token in compact for token in ("去除文字", "删除文字", "抹除文字", "无文字")
    ):
        return "packaging_text"
    if locks.get("logo") and any(
        token in compact for token in ("去除logo", "删除logo", "移除标志", "无logo")
    ):
        return "logo"
    if locks.get("subject_shape") and any(
        token in compact for token in ("改变外形", "重构主体", "替换主体")
    ):
        return "subject_shape"
    if locks.get("product_count") and any(
        token in compact for token in ("增加产品", "删除产品", "改变数量")
    ):
        return "product_count"
    return ""


class ObsidianKnowledgeIndex:
    """Small local inverted index over reviewed Markdown knowledge pages."""

    def __init__(self, vault_path: str | Path):
        self._lock = threading.RLock()
        self.vault_path = Path(vault_path).expanduser().resolve(strict=False)
        self.knowledge_path = self._resolve_knowledge_path(self.vault_path)
        self.documents: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.loaded_at = ""
        self.snapshot_sha256 = _sha256([])
        self.reload()

    @staticmethod
    def _resolve_knowledge_path(path: Path) -> Path:
        candidate = path / "20 知识库"
        return candidate if candidate.exists() else path

    def set_path(self, vault_path: str | Path) -> dict[str, Any]:
        with self._lock:
            self.vault_path = Path(vault_path).expanduser().resolve(strict=False)
            self.knowledge_path = self._resolve_knowledge_path(self.vault_path)
        return self.reload()

    def reload(self) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        if self.knowledge_path.exists():
            for path in sorted(self.knowledge_path.rglob("*.md")):
                try:
                    relative = path.relative_to(self.knowledge_path)
                    if relative.parts and relative.parts[0] in _EXCLUDED_KNOWLEDGE_DIRECTORIES:
                        continue
                    if path.stat().st_size > 512 * 1024:
                        errors.append({"path": str(relative).replace("\\", "/"), "error": "file-too-large"})
                        continue
                    payload = path.read_bytes()
                    text = payload.decode("utf-8-sig")
                    metadata, body = parse_frontmatter(text)
                    title = next(
                        (clean_markdown(line[2:], 120) for line in body.splitlines() if line.startswith("# ")),
                        path.stem,
                    )
                    relative_path = str(relative).replace("\\", "/")
                    searchable = {
                        "title": title,
                        "summary": metadata.get("summary", ""),
                        "project_scope": metadata.get("project_scope", []),
                        "tags": metadata.get("tags", []),
                        "category": metadata.get("category", ""),
                        "wikilinks": _wikilinks(body),
                        "rules": extract_rule_lines(body),
                    }
                    documents.append({
                        "id": str(metadata.get("id") or f"obsidian:{_sha256(relative_path)[:16]}"),
                        "title": title,
                        "relative_path": relative_path,
                        "review": str(metadata.get("review") or "pending").casefold(),
                        "page_type": str(metadata.get("page_type") or ""),
                        "summary": _compact(metadata.get("summary"), 180),
                        "project_scope": list(metadata.get("project_scope") or [])
                        if isinstance(metadata.get("project_scope"), list) else [],
                        "wikilinks": searchable["wikilinks"],
                        "rules": searchable["rules"],
                        "tokens": sorted(_tokens(searchable)),
                        "content_sha256": _sha256(payload),
                        "mtime": path.stat().st_mtime,
                    })
                except Exception as exc:
                    errors.append({"path": str(path), "error": str(exc)})
        snapshot = [
            {
                "id": item["id"],
                "relative_path": item["relative_path"],
                "review": item["review"],
                "content_sha256": item["content_sha256"],
            }
            for item in documents
        ]
        with self._lock:
            self.documents = documents
            self.errors = errors
            self.loaded_at = _utc_now()
            self.snapshot_sha256 = _sha256(snapshot)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            approved = sum(1 for item in self.documents if item["review"] == "approved")
            pending = sum(1 for item in self.documents if item["review"] != "approved")
            executable = sum(
                1 for item in self.documents
                if item["review"] == "approved"
                and item["page_type"] in _ACTIVE_PAGE_TYPES
                and item["rules"]
            )
            return {
                "contract_version": OBSIDIAN_INDEX_VERSION,
                "available": self.knowledge_path.exists(),
                "read_only": True,
                "document_count": len(self.documents),
                "approved_count": approved,
                "pending_count": pending,
                "executable_count": executable,
                "snapshot_sha256": self.snapshot_sha256,
                "loaded_at": self.loaded_at,
                "errors": copy.deepcopy(self.errors[:20]),
            }

    def retrieve(self, context: Mapping[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
        query_tokens = _tokens({
            key: context.get(key)
            for key in (
                "category", "brand_profile", "project_name", "product_name",
                "objective", "user_request", "style", "output_kind", "platform",
            )
        })
        if not query_tokens:
            return []
        project = str(context.get("project_name") or "").strip().casefold()
        category = str(context.get("category") or "").strip().casefold()
        ranked: list[tuple[int, dict[str, Any], list[str]]] = []
        with self._lock:
            documents = copy.deepcopy(self.documents)
        for document in documents:
            if (
                document["review"] != "approved"
                or document["page_type"] not in _ACTIVE_PAGE_TYPES
                or not document["rules"]
            ):
                continue
            document_tokens = set(document["tokens"])
            overlap = query_tokens & document_tokens
            score = min(len(overlap) * 5, 30)
            reasons = [f"关键词 {token}" for token in sorted(overlap)[:4]]
            project_scope = {
                str(item).strip().casefold() for item in document["project_scope"] if str(item).strip()
            }
            if project and project in project_scope:
                score += 45
                reasons.insert(0, "同一项目范围")
            if category and category != "general" and category in document_tokens:
                score += 20
                reasons.insert(0, "同一设计类目")
            if score >= 15:
                ranked.append((score, document, reasons))
        ranked.sort(key=lambda item: (-item[0], item[1]["relative_path"]))
        return [
            {**document, "relevance_score": score, "relevance_reasons": reasons}
            for score, document, reasons in ranked[:max(0, int(limit))]
        ]


class GrowthSystem:
    """Compile relevant Obsidian knowledge and ledger evidence into PA rules."""

    def __init__(self, ledger: Any, vault_path: str | Path):
        self.ledger = ledger
        self.index = ObsidianKnowledgeIndex(vault_path)

    @property
    def vault_path(self) -> Path:
        return self.index.vault_path

    def set_path(self, vault_path: str | Path) -> dict[str, Any]:
        return self.index.set_path(vault_path)

    def reload(self) -> dict[str, Any]:
        return self.index.reload()

    def status(self) -> dict[str, Any]:
        feedback = []
        try:
            feedback = self.ledger.list_feedback(limit=2000)
        except Exception:
            pass
        usable = sum(1 for item in feedback if self._feedback_directive(item))
        return {
            "contract_version": GROWTH_CONTEXT_VERSION,
            "knowledge": self.index.status(),
            "cases": {
                "contract_version": CASE_PROJECTION_VERSION,
                "evidence_count": len(feedback),
                "reusable_count": usable,
                "projection_only": True,
                "copies_assets": False,
            },
        }

    @staticmethod
    def _feedback_directive(feedback: Mapping[str, Any]) -> tuple[str, str] | None:
        signal = str(feedback.get("signal") or "").casefold()
        if signal not in {"adopted", "rejected", "adjusted", "final_artwork"}:
            return None
        reason = _compact(feedback.get("reason"), 140)
        structured = feedback.get("structured")
        structured = structured if isinstance(structured, Mapping) else {}
        reason_codes = [
            _REASON_CODE_LABELS.get(str(code), str(code))
            for code in structured.get("reason_codes") or []
            if str(code).strip()
        ]
        if not reason:
            reason = "、".join(reason_codes[:4])
        if not reason:
            return None
        if signal in {"adopted", "final_artwork"}:
            return "positive", f"相关历史采用经验：{reason}"
        return "negative", f"避免重现相关历史方案的问题：{reason}"

    def _case_evidence(self, feedback: Mapping[str, Any]) -> dict[str, Any] | None:
        directive = self._feedback_directive(feedback)
        if directive is None:
            return None
        session_id = str(feedback.get("session_id") or "").strip()
        if not session_id:
            return None
        try:
            session = self.ledger.get_session(session_id, include_timeline=False)
        except Exception:
            return None
        structured = feedback.get("structured")
        structured = dict(structured) if isinstance(structured, Mapping) else {}
        generation_id = str(feedback.get("generation_id") or "").strip()
        result_asset_id = str(
            structured.get("result_asset_id") or feedback.get("asset_id") or ""
        ).strip()
        job_id = str(structured.get("job_id") or "").strip()
        generation = None
        asset = None
        job = None
        try:
            generation = self.ledger.get_generation(generation_id) if generation_id else None
            asset = self.ledger.get_asset(result_asset_id) if result_asset_id else None
            job = self.ledger.get_job(job_id, include_attempts=False) if job_id else None
        except Exception:
            return None
        if generation is None and asset is None and job is None:
            return None
        if generation is not None and str(generation.get("session_id") or "") != session_id:
            return None
        if asset is not None and str(asset.get("session_id") or "") != session_id:
            return None
        if job is not None and str(job.get("session_id") or "") != session_id:
            return None
        job_snapshot = (
            job.get("snapshot")
            if isinstance((job or {}).get("snapshot"), Mapping)
            else {}
        )
        snapshot_parameters = (
            job_snapshot.get("parameters")
            if isinstance(job_snapshot.get("parameters"), Mapping)
            else {}
        )
        execution_context = (
            snapshot_parameters.get("execution_context")
            if isinstance(snapshot_parameters.get("execution_context"), Mapping)
            else {}
        )
        canvas_context = (
            execution_context.get("canvas_context")
            if isinstance(execution_context.get("canvas_context"), Mapping)
            else {}
        )
        kind, text = directive
        case_id = f"case:{feedback.get('id')}"
        evidence_identity = {
            "feedback_id": str(feedback.get("id") or ""),
            "session_id": session_id,
            "generation_id": generation_id,
            "job_id": job_id,
            "result_asset_id": result_asset_id,
            "signal": str(feedback.get("signal") or ""),
            "reason": str(feedback.get("reason") or ""),
        }
        source = {
            "id": f"{case_id}:{_sha256(evidence_identity)[:16]}",
            "title": (
                f"经验案例 · {'已采用' if kind == 'positive' else '已否决'} · "
                f"{_compact(session.get('title') or session.get('project_name') or session_id, 80)}"
            ),
            "relative_path": f"经验案例/{feedback.get('id')}",
        }
        brief = session.get("brief") if isinstance(session.get("brief"), Mapping) else {}
        parameters = generation.get("parameters") if isinstance((generation or {}).get("parameters"), Mapping) else {}
        return {
            "case_id": case_id,
            "feedback_id": str(feedback.get("id") or ""),
            "session_id": session_id,
            "project_name": str(session.get("project_name") or ""),
            "category": str(session.get("category") or "general"),
            "brand_profile": str(session.get("brand_profile") or ""),
            "designer_profile": str(session.get("designer_profile") or "default"),
            "mode": str(session.get("mode") or feedback.get("mode") or ""),
            "generation_id": generation_id,
            "job_id": job_id,
            "result_asset_id": result_asset_id,
            "parent_generation_id": str((generation or {}).get("parent_generation_id") or ""),
            "parent_asset_id": str((asset or {}).get("parent_asset_id") or ""),
            "canvas_document_id": str(canvas_context.get("document_id") or ""),
            "canvas_document_version_id": str(job_snapshot.get("canvas_document_version_id") or ""),
            "canvas_operation_id": str(job_snapshot.get("canvas_operation_id") or canvas_context.get("operation_id") or ""),
            "signal": str(feedback.get("signal") or ""),
            "reason": str(feedback.get("reason") or ""),
            "reason_codes": list(structured.get("reason_codes") or []),
            "created_at": str(feedback.get("created_at") or ""),
            "kind": kind,
            "directive": text,
            "source": source,
            "tokens": sorted(_tokens({
                "session_title": session.get("title"),
                "project_name": session.get("project_name"),
                "category": session.get("category"),
                "brand_profile": session.get("brand_profile"),
                "brief": brief,
                "parameters": parameters,
                "reason": feedback.get("reason"),
                "reason_codes": structured.get("reason_codes"),
            })),
        }

    def retrieve_cases(self, context: Mapping[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
        query_tokens = _tokens({
            key: context.get(key)
            for key in (
                "category", "brand_profile", "project_name", "designer_profile",
                "product_name", "objective", "user_request", "style", "output_kind", "mode",
            )
        })
        current_project = str(context.get("project_name") or "").strip().casefold()
        current_brand = str(context.get("brand_profile") or "").strip().casefold()
        current_category = str(context.get("category") or "general").strip().casefold()
        current_designer = str(context.get("designer_profile") or "default").strip().casefold()
        try:
            feedback_rows = self.ledger.list_feedback(limit=2000)
        except Exception:
            return []
        ranked: list[tuple[int, dict[str, Any], list[str]]] = []
        for feedback in feedback_rows:
            case = self._case_evidence(feedback)
            if case is None:
                continue
            score = 0
            reasons: list[str] = []
            if current_project and current_project == case["project_name"].casefold():
                score += 45
                reasons.append("同一项目")
            if current_brand and current_brand == case["brand_profile"].casefold():
                score += 35
                reasons.append("同一品牌")
            if current_category != "general" and current_category == case["category"].casefold():
                score += 25
                reasons.append("同一类目")
            if current_designer and current_designer == case["designer_profile"].casefold():
                score += 5
            overlap = query_tokens & set(case["tokens"])
            if overlap:
                score += min(len(overlap) * 4, 24)
                reasons.extend(f"语义 {token}" for token in sorted(overlap)[:3])
            if score < 20:
                continue
            case["relevance_score"] = score
            case["relevance_reasons"] = reasons
            ranked.append((score, case, reasons))
        ranked.sort(
            key=lambda item: (item[0], item[1]["created_at"], item[1]["case_id"]),
            reverse=True,
        )
        return [item[1] for item in ranked[:max(0, int(limit))]]

    def compile(self, context: Mapping[str, Any]) -> dict[str, Any]:
        knowledge_documents = self.index.retrieve(context, limit=2)
        cases = self.retrieve_cases(context, limit=2)
        knowledge_rules: list[dict[str, Any]] = []
        case_rules: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        for document in knowledge_documents:
            source = {
                "id": (
                    f"obsidian:{document['id']}:{document['content_sha256'][:16]}"
                ),
                "title": f"Obsidian · {document['title']}",
                "relative_path": f"Obsidian/{document['relative_path']}",
            }
            for index, raw_rule in enumerate(document["rules"][:2]):
                text = _compact(raw_rule, 160)
                if not text:
                    continue
                conflict_field = _task_conflict(context, text)
                if conflict_field:
                    ignored.append({
                        "id": f"obsidian:{document['id']}:{index}",
                        "reason": "conflicts-with-task",
                        "text": text,
                    })
                    conflicts.append({
                        "field": conflict_field,
                        "winner": "task",
                        "message": "本次用户意图或商品硬约束高于相关 Obsidian 知识。",
                    })
                    continue
                knowledge_rules.append({
                    "id": f"obsidian:{document['id']}:{index}",
                    "text": f"相关 Obsidian 知识：{text}",
                    "source": source,
                    "context_layer": "approved-knowledge",
                })
                break
        for case in cases:
            conflict_field = (
                _task_conflict(context, case["directive"])
                if case["kind"] == "positive" else ""
            )
            if conflict_field:
                ignored.append({
                    "id": case["case_id"],
                    "reason": "conflicts-with-task",
                    "text": case["directive"],
                })
                conflicts.append({
                    "field": conflict_field,
                    "winner": "task",
                    "message": "本次用户意图或商品硬约束高于历史 Case。",
                })
                continue
            case_rules.append({
                "id": case["case_id"],
                "kind": case["kind"],
                "text": case["directive"],
                "source": copy.deepcopy(case["source"]),
                "context_layer": "case",
            })
        snapshot_payload = {
            "knowledge_index_sha256": self.index.snapshot_sha256,
            "knowledge_sources": [rule["source"]["id"] for rule in knowledge_rules],
            "cases": [
                {
                    key: case.get(key)
                    for key in (
                        "case_id", "feedback_id", "session_id", "generation_id", "job_id",
                        "result_asset_id", "parent_generation_id", "parent_asset_id",
                        "canvas_document_id", "canvas_document_version_id",
                        "canvas_operation_id", "signal",
                        "relevance_score", "relevance_reasons",
                    )
                }
                for case in cases
            ],
        }
        return {
            "contract_version": GROWTH_CONTEXT_VERSION,
            "compiled_at": _utc_now(),
            "knowledge_rules": knowledge_rules,
            "case_rules": case_rules,
            "cases": cases,
            "conflicts": conflicts,
            "ignored_rules": ignored,
            "snapshot_sha256": _sha256(snapshot_payload),
            "knowledge_index_sha256": self.index.snapshot_sha256,
        }

    @staticmethod
    def attach_knowledge(bundle: Mapping[str, Any], growth: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(bundle or {}))
        rules = copy.deepcopy(list(growth.get("knowledge_rules") or []))
        result["positive_rules"] = [*list(result.get("positive_rules") or []), *rules]
        growth_sources = [
            copy.deepcopy(dict(rule["source"]))
            for rule in rules
            if isinstance(rule, Mapping) and isinstance(rule.get("source"), Mapping)
        ]
        result["sources"] = [*list(result.get("sources") or []), *growth_sources]
        result["conflicts"] = [
            *list(result.get("conflicts") or []),
            *copy.deepcopy(list(growth.get("conflicts") or [])),
        ]
        result["ignored_rules"] = [
            *list(result.get("ignored_rules") or []),
            *copy.deepcopy(list(growth.get("ignored_rules") or [])),
        ]
        return result

    @staticmethod
    def attach_cases(bundle: Mapping[str, Any], growth: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(bundle or {}))
        positive = []
        negative = []
        for rule in growth.get("case_rules") or []:
            target = positive if str(rule.get("kind") or "") == "positive" else negative
            row = copy.deepcopy(dict(rule))
            row.pop("kind", None)
            target.append(row)
        result["positive_rules"] = [*list(result.get("positive_rules") or []), *positive]
        result["negative_rules"] = [*list(result.get("negative_rules") or []), *negative]
        case_sources = [
            copy.deepcopy(dict(rule["source"]))
            for rule in [*positive, *negative]
            if isinstance(rule, Mapping) and isinstance(rule.get("source"), Mapping)
        ]
        result["sources"] = [*list(result.get("sources") or []), *case_sources]
        return result

    @staticmethod
    def reserve_relevant_context(bundle: Mapping[str, Any], *, prompt_version: str) -> dict[str, Any]:
        """Reserve prompt slots for relevant dynamic context, trimming generic rules only.

        Existing behavior is byte-for-byte unchanged when no Growth rule is present.
        """

        result = copy.deepcopy(dict(bundle or {}))
        positives = list(result.get("positive_rules") or [])
        negatives = list(result.get("negative_rules") or [])
        growth_rules = [
            item for item in positives
            if isinstance(item, Mapping)
            and str(item.get("context_layer") or "") in {"approved-knowledge", "case"}
        ]
        case_negatives = [
            item for item in negatives
            if isinstance(item, Mapping)
            and str(item.get("context_layer") or "") == "case"
        ]
        if not growth_rules and not case_negatives:
            return result
        growth_knowledge = [
            item for item in growth_rules
            if str(item.get("context_layer") or "") == "approved-knowledge"
        ]
        case_positives = [
            item for item in growth_rules
            if str(item.get("context_layer") or "") == "case"
        ]
        skill_rules = [
            item for item in positives
            if isinstance(item, Mapping)
            and str(item.get("id") or "").startswith("comfyui-food-product-main-image:")
        ]
        dynamic = [*growth_knowledge[:1], *skill_rules[:1], *case_positives[:1]]
        dynamic_ids = {str(item.get("id") or "") for item in dynamic}
        identity = [
            item for item in positives
            if isinstance(item, Mapping)
            and str((item.get("source") or {}).get("relative_path") or "").startswith(
                ("商品档案/", "记忆反馈/")
            )
        ]
        identity_ids = {str(item.get("id") or _sha256(item)) for item in identity}
        generic = []
        for item in positives:
            item_id = str(item.get("id") or "") if isinstance(item, Mapping) else ""
            identity_key = (
                str(item.get("id") or _sha256(item))
                if isinstance(item, Mapping) else _sha256(item)
            )
            if item_id not in dynamic_ids and identity_key not in identity_ids:
                generic.append(item)
        if case_negatives:
            case_negative_ids = {str(item.get("id") or "") for item in case_negatives}
            generic_negatives = [
                item for item in negatives
                if not isinstance(item, Mapping)
                or str(item.get("id") or "") not in case_negative_ids
            ]
            negative_limit = 1 if str(prompt_version or "").casefold() == "prompt_v3" else 10
            result["negative_rules"] = [
                *generic_negatives[:max(0, negative_limit - len(case_negatives))],
                *case_negatives[:negative_limit],
            ]
        if str(prompt_version or "").casefold() == "prompt_v3":
            # v3 has three positive slots. One product/memory fact, one relevant
            # knowledge or Skill rule, and one Case make the history observable
            # without expanding the frozen prompt budget.
            preferred_dynamic = dynamic
            selected = [*identity[:1], *preferred_dynamic[:2]]
            if not any(str(item.get("context_layer") or "") == "case" for item in selected):
                case = next((item for item in dynamic if str(item.get("context_layer") or "") == "case"), None)
                if case is not None:
                    selected = [*selected[:2], case]
            result["positive_rules"] = [*selected, *generic, *positives]
            return result
        reserved = dynamic[:3]
        kept_identity = identity[:max(0, 10 - len(reserved))]
        remaining = max(0, 10 - len(kept_identity) - len(reserved))
        result["positive_rules"] = [*kept_identity, *generic[:remaining], *reserved]
        return result

    @staticmethod
    def finalize_snapshot(
        governed_bundle: Mapping[str, Any],
        growth: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        bundle = copy.deepcopy(dict(governed_bundle or {}))
        applied_rule_ids = {
            str(item.get("id") or "")
            for key in ("positive_rules", "negative_rules")
            for item in bundle.get(key) or []
            if isinstance(item, Mapping)
        }
        cases = []
        for case in growth.get("cases") or []:
            row = {
                key: copy.deepcopy(case.get(key))
                for key in (
                    "case_id", "feedback_id", "session_id", "generation_id", "job_id",
                    "result_asset_id", "parent_generation_id", "parent_asset_id",
                    "canvas_document_id", "canvas_document_version_id",
                    "canvas_operation_id", "signal",
                    "relevance_score", "relevance_reasons",
                )
            }
            row["applied"] = str(case.get("case_id") or "") in applied_rule_ids
            cases.append(row)
        knowledge_rule_ids = [
            str(item.get("id") or "") for item in growth.get("knowledge_rules") or []
        ]
        snapshot = {
            "contract_version": GROWTH_CONTEXT_VERSION,
            "snapshot_sha256": str(growth.get("snapshot_sha256") or ""),
            "knowledge_index_sha256": str(growth.get("knowledge_index_sha256") or ""),
            "knowledge_rule_ids": knowledge_rule_ids,
            "applied_knowledge_rule_ids": [
                rule_id for rule_id in knowledge_rule_ids if rule_id in applied_rule_ids
            ],
            "cases": cases,
            "status": "applied" if any(case["applied"] for case in cases)
            or any(rule_id in applied_rule_ids for rule_id in knowledge_rule_ids)
            else "no-relevant-context",
        }
        return bundle, snapshot
