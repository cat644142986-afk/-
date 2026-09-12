# -*- coding: utf-8 -*-
"""Read-only adapters for context-only design methods.

The adapter deliberately exposes one fixed, reviewed subset of a local SKILL.md.
It never imports or executes files from the skill directory and never delegates
provider selection to the skill.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any


CONTEXT_SKILL_ADAPTER_VERSION = "context-skill-adapter-v1"
CONTEXT_SKILL_SNAPSHOT_VERSION = "context-skill-snapshot-v1"
DEFAULT_CONTEXT_SKILL_ROOT = Path.home() / ".codex" / "skills"


class ContextSkillError(ValueError):
    """Raised when a selected context-only design method cannot be frozen."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_ALLOWLIST: dict[str, dict[str, Any]] = {
    "comfyui-food-product-main-image": {
        "directory": "comfyui-food-product-main-image",
        "title": "食品饮料白底主图",
        "supported_modes": ("single", "multi-file"),
        "required_markers": (
            "name: comfyui-food-product-main-image",
            "# ComfyUI 电商食品饮料主图生成技能",
            "极简纯白",
            "柔和柔光箱影棚光线",
            "自然真实色彩",
            "无文字水印",
        ),
        # These are reviewed, provider-neutral design directives.  They are not
        # parsed from executable examples and intentionally exclude the skill's
        # generic `text` / “无文字” negative prompt.
        "rules": (
            (
                "composition",
                "采用极简纯白商业棚拍，产品居中，画面保持干净且无杂物",
            ),
            (
                "lighting",
                "采用柔和漫射棚拍光，并保留自然、克制的软阴影",
            ),
            (
                "material",
                "保持自然真实色彩，材质细节清晰可信",
            ),
            (
                "restraint",
                "不添加无关道具、装饰或水印",
            ),
        ),
    },
}


def _frontmatter_version(text: str, content_sha256: str) -> str:
    frontmatter = ""
    if text.startswith("---"):
        match = re.match(r"^---\s*\r?\n(.*?)\r?\n---", text, flags=re.DOTALL)
        if match:
            frontmatter = match.group(1)
    version_match = re.search(
        r"^version\s*:\s*['\"]?([^'\"\r\n]+)",
        frontmatter,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if version_match:
        return re.sub(r"\s+", "-", version_match.group(1).strip())[:80]
    return f"content-{content_sha256[:12]}"


def _definition(skill_id: str) -> dict[str, Any]:
    skill_id = str(skill_id or "").strip()
    if skill_id not in _ALLOWLIST:
        raise ContextSkillError(
            "SKILL_CONTEXT_NOT_ALLOWED",
            "所选设计方法不在当前只读允许列表中",
        )
    return _ALLOWLIST[skill_id]


def resolve_context_skill(
    skill_id: str,
    *,
    mode: str,
    skill_root: Path | str | None = None,
) -> dict[str, Any]:
    """Freeze one allowlisted SKILL.md into safe rules and provenance.

    Only SKILL.md bytes are read.  No referenced file, script, model or provider
    is opened, imported or executed.
    """

    definition = _definition(skill_id)
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in definition["supported_modes"]:
        raise ContextSkillError(
            "SKILL_CONTEXT_UNSUPPORTED_MODE",
            "该设计方法当前只适用于单产品和多文件工作流",
        )

    root = Path(skill_root or DEFAULT_CONTEXT_SKILL_ROOT).expanduser().resolve(strict=False)
    path = (root / str(definition["directory"]) / "SKILL.md").resolve(strict=False)
    if not path.is_relative_to(root):
        raise ContextSkillError(
            "SKILL_CONTEXT_PATH_INVALID",
            "设计方法来源超出允许的只读目录",
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ContextSkillError(
            "SKILL_CONTEXT_UNAVAILABLE",
            "所选设计方法的 SKILL.md 当前不可读取",
        ) from exc
    if len(payload) > 256 * 1024:
        raise ContextSkillError(
            "SKILL_CONTEXT_INCOMPATIBLE",
            "所选设计方法的 SKILL.md 超出只读适配范围",
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContextSkillError(
            "SKILL_CONTEXT_INCOMPATIBLE",
            "所选设计方法的 SKILL.md 不是可读取的 UTF-8 文本",
        ) from exc
    if any(marker not in text for marker in definition["required_markers"]):
        raise ContextSkillError(
            "SKILL_CONTEXT_INCOMPATIBLE",
            "所选设计方法内容已变化，需要重新确认只读规则适配",
        )

    content_sha256 = hashlib.sha256(payload).hexdigest()
    version = _frontmatter_version(text, content_sha256)
    source = {
        "id": (
            f"skill:{skill_id}:{CONTEXT_SKILL_ADAPTER_VERSION}:"
            f"{content_sha256}"
        ),
        "title": f"设计方法 · {definition['title']} · {version}",
        "relative_path": f"设计方法/{definition['title']}/SKILL.md",
    }
    rules = [
        {
            "id": f"{skill_id}:{rule_id}",
            "text": rule_text,
            "source": source,
        }
        for rule_id, rule_text in definition["rules"]
    ]
    snapshot = {
        "snapshot_version": CONTEXT_SKILL_SNAPSHOT_VERSION,
        "skill_id": skill_id,
        "title": str(definition["title"]),
        "version": version,
        "content_sha256": content_sha256,
        "adapter_version": CONTEXT_SKILL_ADAPTER_VERSION,
        "mode": "context-only",
        "source": copy.deepcopy(source),
        "selected_rule_ids": [str(rule["id"]) for rule in rules],
        "applied_rule_ids": [],
        "ignored_rule_ids": [],
        "status": "selected",
    }
    return {"snapshot": snapshot, "rules": rules, "source": source}


def context_skill_status(
    *,
    skill_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return the single fixed design-method capability without local paths."""

    skill_id = "comfyui-food-product-main-image"
    definition = _ALLOWLIST[skill_id]
    try:
        resolved = resolve_context_skill(
            skill_id,
            mode=str(definition["supported_modes"][0]),
            skill_root=skill_root,
        )
        snapshot = resolved["snapshot"]
        return {
            "id": skill_id,
            "title": snapshot["title"],
            "available": True,
            "version": snapshot["version"],
            "content_sha256": snapshot["content_sha256"],
            "adapter_version": snapshot["adapter_version"],
            "mode": snapshot["mode"],
            "supported_modes": list(definition["supported_modes"]),
            "read_only": True,
        }
    except ContextSkillError as exc:
        return {
            "id": skill_id,
            "title": str(definition["title"]),
            "available": False,
            "adapter_version": CONTEXT_SKILL_ADAPTER_VERSION,
            "mode": "context-only",
            "supported_modes": list(definition["supported_modes"]),
            "read_only": True,
            "error": {"code": exc.code, "message": exc.message},
        }


def attach_context_skill(
    bundle: dict[str, Any],
    resolved: dict[str, Any],
) -> dict[str, Any]:
    """Append lower-priority design rules to an existing knowledge bundle."""

    result = copy.deepcopy(dict(bundle or {}))
    result["positive_rules"] = [
        *list(result.get("positive_rules") or []),
        *copy.deepcopy(list(resolved.get("rules") or [])),
    ]
    result["sources"] = [
        *list(result.get("sources") or []),
        copy.deepcopy(dict(resolved["source"])),
    ]
    return result


def finalize_context_skill(
    governed_bundle: dict[str, Any],
    resolved: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep identity bound even when the existing prompt budget omits all rules."""

    bundle = copy.deepcopy(dict(governed_bundle or {}))
    snapshot = copy.deepcopy(dict(resolved["snapshot"]))
    selected_ids = set(snapshot["selected_rule_ids"])
    applied_ids = [
        str(rule.get("id") or "")
        for rule in bundle.get("positive_rules") or []
        if isinstance(rule, dict) and str(rule.get("id") or "") in selected_ids
    ]
    snapshot["applied_rule_ids"] = applied_ids
    snapshot["ignored_rule_ids"] = [
        rule_id for rule_id in snapshot["selected_rule_ids"] if rule_id not in applied_ids
    ]
    snapshot["status"] = "applied" if applied_ids else "selected-not-applied"

    source = copy.deepcopy(dict(resolved["source"]))
    source_id = str(source["id"])
    sources = [
        item for item in bundle.get("sources") or []
        if not isinstance(item, dict) or str(item.get("id") or "") != source_id
    ]
    # The source identity carries both content hash and adapter version. Keeping
    # it in the governed bundle binds stale detection even when rule budgets omit
    # every optional design rule.
    sources.append(source)
    bundle["sources"] = sources
    return bundle, snapshot
