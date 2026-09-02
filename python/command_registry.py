# -*- coding: utf-8 -*-
"""Canonical command metadata shared by quick workflows and the canvas."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


COMMAND_REGISTRY_VERSION = "canvas-command-v1"

_COMMANDS: tuple[dict[str, Any], ...] = (
    {
        "id": "command:existing-generate-single",
        "label": "生成单产品图",
        "mode": "single",
        "engine_key": "cloud-workflow",
        "min_sources": 1,
        "max_sources": 1,
        "cost_policy": "provider-confirmed",
        "execution_kind": "durable-job",
        "existing_quick_mode": True,
        "supports_canvas": True,
    },
    {
        "id": "command:existing-generate-multi-file",
        "label": "批量生成产品图",
        "mode": "multi-file",
        "engine_key": "cloud-workflow",
        "min_sources": 1,
        "max_sources": 20,
        "cost_policy": "provider-confirmed",
        "execution_kind": "durable-job",
        "existing_quick_mode": True,
        "supports_canvas": True,
    },
    {
        "id": "command:existing-group-split",
        "label": "合照拆分",
        "mode": "group-split",
        "engine_key": "group-workflow",
        "min_sources": 1,
        "max_sources": 1,
        "cost_policy": "provider-confirmed",
        "execution_kind": "durable-job",
        "existing_quick_mode": True,
        "supports_canvas": True,
    },
    {
        "id": "command:existing-remove-background",
        "label": "快速去背景",
        "mode": "cutout-batch",
        "engine_key": "local-cutout",
        "min_sources": 1,
        "max_sources": 24,
        "cost_policy": "free-local",
        "execution_kind": "durable-job",
        "existing_quick_mode": True,
        "supports_canvas": True,
    },
    {
        "id": "command:local-edit-generate",
        "label": "生成局部编辑候选",
        "mode": "single",
        "engine_key": "cloud-local-edit",
        "min_sources": 1,
        "max_sources": 1,
        "cost_policy": "provider-confirmed",
        "execution_kind": "durable-job",
        "existing_quick_mode": False,
        "supports_canvas": True,
    },
    {
        "id": "command:transform-layer",
        "label": "变换图层",
        "mode": None,
        "engine_key": "canvas-local",
        "min_sources": 0,
        "max_sources": 0,
        "cost_policy": "free-local",
        "execution_kind": "canvas-mutation",
        "existing_quick_mode": False,
        "supports_canvas": True,
    },
    {
        "id": "command:toggle-layer",
        "label": "显示或隐藏图层",
        "mode": None,
        "engine_key": "canvas-local",
        "min_sources": 0,
        "max_sources": 0,
        "cost_policy": "free-local",
        "execution_kind": "canvas-mutation",
        "existing_quick_mode": False,
        "supports_canvas": True,
    },
    {
        "id": "command:toggle-layer-lock",
        "label": "锁定或解锁图层",
        "mode": None,
        "engine_key": "canvas-local",
        "min_sources": 0,
        "max_sources": 0,
        "cost_policy": "free-local",
        "execution_kind": "canvas-mutation",
        "existing_quick_mode": False,
        "supports_canvas": True,
    },
    {
        "id": "command:local-edit-compose",
        "label": "应用局部编辑",
        "mode": None,
        "engine_key": "canvas-local",
        "min_sources": 0,
        "max_sources": 0,
        "cost_policy": "contract-confirmed",
        "execution_kind": "canvas-mutation",
        "existing_quick_mode": False,
        "supports_canvas": True,
    },
)

_BY_ID = {command["id"]: command for command in _COMMANDS}
_BY_MODE = {
    command["mode"]: command
    for command in _COMMANDS
    if command["execution_kind"] == "durable-job"
    and command["existing_quick_mode"]
}


def list_commands() -> list[dict[str, Any]]:
    return deepcopy(list(_COMMANDS))


def get_command(command_id: str) -> dict[str, Any]:
    key = str(command_id or "").strip()
    try:
        return deepcopy(_BY_ID[key])
    except KeyError as exc:
        raise KeyError(f"unknown command: {key}") from exc


def command_for_mode(mode: str) -> dict[str, Any]:
    key = str(mode or "").strip()
    try:
        return deepcopy(_BY_MODE[key])
    except KeyError as exc:
        raise KeyError(f"no command registered for workflow mode: {key}") from exc


def validate_command_sources(command: dict[str, Any], source_asset_ids: list[str]) -> None:
    count = len(source_asset_ids)
    minimum = int(command["min_sources"])
    maximum = int(command["max_sources"])
    if count < minimum or count > maximum:
        raise ValueError(
            f"{command['id']} requires between {minimum} and {maximum} source assets"
        )
