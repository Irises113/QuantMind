from datetime import datetime
from zoneinfo import ZoneInfo

from backend.services.live_trading.services.admin_inference_view import (
    next_weekday_midnight,
    reason_label,
)

_SH = ZoneInfo("Asia/Shanghai")


def test_next_weekday_midnight_skips_weekend():
    friday_afternoon = datetime(2026, 9, 11, 15, 0, tzinfo=_SH)
    nxt = next_weekday_midnight(friday_afternoon)
    assert nxt.weekday() == 0
    assert nxt.date().isoformat() == "2026-09-14"
    assert nxt.hour == 0


def test_next_weekday_midnight_same_day_before_midnight():
    monday_early = datetime(2026, 9, 14, 0, 0, tzinfo=_SH)
    nxt = next_weekday_midnight(monday_early)
    assert nxt.date().isoformat() == "2026-09-15"


def test_reason_label():
    assert reason_label("ALREADY_DONE") == "当日已完成"
    assert reason_label("LOCK_HELD") == "任务锁冲突"
    assert reason_label(None) is None
    assert reason_label("CUSTOM") == "CUSTOM"
