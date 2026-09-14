"""将 strategy_templates 目录中的内置策略同步到用户 PG 策略表。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def existing_template_markers(
    items: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """已落库策略里的模板身份：parameters.strategy_type、tags 里的 template:<id>、同名。"""
    types: set[str] = set()
    names: set[str] = set()
    for item in items:
        params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        strategy_type = str(params.get("strategy_type") or "").strip()
        if strategy_type:
            types.add(strategy_type)
        for tag in item.get("tags") or []:
            text = str(tag)
            if text.startswith("template:"):
                marker = text.split(":", 1)[1].strip()
                if marker:
                    types.add(marker)
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    return types, names


def _load_templates():
    from backend.services.engine.qlib_app.services.strategy_templates import (
        get_all_templates,
    )

    return get_all_templates()


def _storage_service():
    from backend.shared.strategy_storage import get_strategy_storage_service

    return get_strategy_storage_service()


async def sync_builtin_templates(user_id: str) -> int:
    """把缺失的内置模板克隆到用户策略库。去重键为 strategy_type / template:<id> / 同名。"""
    svc = _storage_service()
    templates = _load_templates()
    existing = await asyncio.to_thread(svc.list, user_id=user_id)
    existing_types, existing_names = existing_template_markers(existing)
    synced_count = 0
    for t in templates:
        if t.id in existing_types or t.name in existing_names:
            continue

        await svc.save(
            user_id=user_id,
            name=t.name,
            code=t.code,
            metadata={
                "description": t.description,
                "tags": [t.category, t.difficulty, "SystemSync", f"template:{t.id}"],
                "status": "ACTIVE",
                "is_verified": True,
                "parameters": {"strategy_type": t.id, "topk": 50, "signal": "<PRED>"},
            },
        )
        synced_count += 1
        existing_types.add(t.id)
        existing_names.add(t.name)
    if synced_count:
        logger.info(
            "synced %s builtin strategy templates for user %s", synced_count, user_id
        )
    return synced_count
