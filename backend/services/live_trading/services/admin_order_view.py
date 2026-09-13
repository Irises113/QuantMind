"""Helpers for the admin order-management page (history + planned)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "")


def isoformat_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(sep=" ", timespec="seconds")


def classify_auto_source(strategy_id: Any, remarks: str | None) -> str | None:
    """Return hosted/risk if this looks like an automatic order, else None."""
    text = str(remarks or "")
    if "risk_rule" in text:
        return "risk"
    if strategy_id not in (None, "", 0, "0"):
        return "hosted"
    return None


def planned_dedup_key(tenant_id: str, user_id: str, strategy_id: str, trade_date: str) -> str:
    return f"{tenant_id}|{user_id}|{strategy_id}|{trade_date}"
