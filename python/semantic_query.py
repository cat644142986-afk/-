from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


LEXICON_PATH = Path(__file__).with_name("semantic_query_lexicon.json")
LEXICON_SOURCE = "offline-commerce-lexicon-v1"
_CJK_RANGES = (("\u3400", "\u4dbf"), ("\u4e00", "\u9fff"), ("\uf900", "\ufaff"))
_LEADING_NOISE = ("请帮我", "帮我", "请", "只保留", "保留", "选择", "找出", "找到", "要")
_COUNT_PREFIX = re.compile(r"^(?:[一二两三四五六七八]|[1-8])+(?:个|只|件|张|瓶|盒|袋|双)?")


def contains_cjk(value: str) -> bool:
    return any(start <= char <= end for char in value for start, end in _CJK_RANGES)


def _text(value: Any, *, limit: int = 80) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _compact_chinese(value: str) -> str:
    compact = re.sub(r"[\s，,。.!！?？、的]", "", value)
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_NOISE:
            if compact.startswith(prefix):
                compact = compact[len(prefix):]
                changed = True
                break
    return _COUNT_PREFIX.sub("", compact, count=1)


@lru_cache(maxsize=1)
def load_semantic_query_lexicon(path: str | Path = LEXICON_PATH) -> dict[str, Any]:
    lexicon_path = Path(path)
    payload = json.loads(lexicon_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("semantic query lexicon schema_version must be 1.0")
    exact = payload.get("exact")
    modifiers = payload.get("modifiers")
    if not isinstance(exact, dict) or not exact or not isinstance(modifiers, dict):
        raise ValueError("semantic query lexicon requires exact and modifier mappings")
    for group_name, group in (("exact", exact), ("modifiers", modifiers)):
        for source, target in group.items():
            if not str(source).strip() or not str(target).strip() or contains_cjk(str(target)):
                raise ValueError(f"semantic query lexicon has an invalid {group_name} entry")
    return payload


def _result(
    original_query: str,
    model_query: str,
    status: str,
    message: str,
    *,
    source_terms: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "original_query": original_query,
        "model_query": model_query,
        "status": status,
        "mapped": bool(model_query),
        "editable": True,
        "source": LEXICON_SOURCE,
        "source_terms": list(source_terms or []),
        "message": message,
    }


def resolve_semantic_query(
    query: Any,
    model_query_override: Any = "",
    *,
    lexicon: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    original = _text(query)
    override = _text(model_query_override).lower().rstrip(".")
    if override:
        if contains_cjk(override) or not re.search(r"[a-z]", override):
            return _result(
                original,
                "",
                "invalid_override",
                "英文识别词必须包含英文字母；可清空后使用离线映射",
            )
        return _result(
            original,
            override,
            "user_override",
            f"将使用你填写的英文识别词“{override}”自动定位",
            source_terms=[override],
        )
    if not original:
        return _result(original, "", "empty", "请先填写要保留的物体名称")
    if not contains_cjk(original):
        direct = original.lower().rstrip(".")
        if re.search(r"[a-z]", direct):
            return _result(
                original,
                direct,
                "direct_english",
                f"将使用英文名称“{direct}”自动定位",
                source_terms=[direct],
            )
        return _result(original, "", "unmapped", "当前名称无法转换为本地模型识别词")

    data = dict(lexicon or load_semantic_query_lexicon())
    exact = data.get("exact") or {}
    modifiers = data.get("modifiers") or {}
    compact = _compact_chinese(original)
    if compact in exact:
        mapped = str(exact[compact]).strip().lower()
        return _result(
            original,
            mapped,
            "mapped_exact",
            f"已将“{original}”映射为“{mapped}”进行本地定位",
            source_terms=[compact],
        )

    remainder = compact
    translated_modifiers: list[str] = []
    source_terms: list[str] = []
    for source in sorted(modifiers, key=len, reverse=True):
        if remainder.startswith(source):
            translated_modifiers.append(str(modifiers[source]).strip().lower())
            source_terms.append(source)
            remainder = remainder[len(source):]
            break
    object_term = str(exact.get(remainder) or "").strip().lower()
    if not object_term and remainder and not contains_cjk(remainder) and re.search(r"[a-z]", remainder):
        object_term = remainder.lower()
    if object_term:
        mapped = " ".join([*translated_modifiers, object_term]).strip()
        return _result(
            original,
            mapped,
            "mapped_composed",
            f"已将“{original}”组合为“{mapped}”进行本地定位",
            source_terms=[*source_terms, remainder],
        )
    return _result(
        original,
        "",
        "unmapped",
        f"离线词表暂未收录“{original}”；可填写英文识别词，或直接手动框选",
    )
