from backend.services.live_trading.services.admin_order_view import (
    classify_auto_source,
    planned_dedup_key,
)


def test_classify_risk_remarks():
    assert classify_auto_source(None, "risk_rule:position_stop_loss 触及") == "risk"
    assert classify_auto_source(12, "risk_rule:x") == "risk"


def test_classify_hosted_by_strategy_id():
    assert classify_auto_source(8, None) == "hosted"
    assert classify_auto_source("lgbm-t1", "") == "hosted"


def test_classify_manual_orders_excluded():
    assert classify_auto_source(None, "用户手动下单") is None
    assert classify_auto_source(0, None) is None
    assert classify_auto_source("", None) is None


def test_planned_dedup_key():
    assert planned_dedup_key("default", "0", "s1", "2026-09-14") == "default|0|s1|2026-09-14"
