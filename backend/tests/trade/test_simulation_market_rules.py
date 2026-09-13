"""模拟盘多市场规则与行情读取测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backend.services.simulation.services.market_rules import (
    Market,
    infer_market,
    infer_market_from_symbols,
    rules_for,
)

_PROJECT = Path(__file__).resolve().parents[2]


def test_infer_market_from_symbols():
    assert infer_market("0001.HK") is Market.HK
    assert infer_market("600036.SH") is Market.CN
    assert infer_market("600036") is Market.CN
    assert infer_market("AAPL") is Market.US
    assert infer_market("RB0.CN") is Market.FUTURES
    assert infer_market("CL.FUT") is Market.FUTURES
    assert infer_market("Au99.99") is Market.FUTURES
    assert infer_market("AG(T+D)") is Market.FUTURES
    assert infer_market("BTCUSDT") is Market.CRYPTO
    assert infer_market("ETHUSDT") is Market.CRYPTO
    # 众数：同一策略的信号来自同一市场
    assert infer_market_from_symbols(["0001.HK", "0700.HK", "0002.HK"]) is Market.HK
    assert infer_market_from_symbols([]) is Market.CN


def test_lot_size_for_symbol_star_board_200():
    from backend.services.simulation.services.market_rules import lot_size_for_symbol

    assert lot_size_for_symbol("688001.SH") == 200
    assert lot_size_for_symbol("SH688217") == 200
    assert lot_size_for_symbol("689009.SH") == 200
    assert lot_size_for_symbol("600036.SH") == 100
    assert lot_size_for_symbol("300001.SZ") == 100
    assert lot_size_for_symbol("AAPL") == 1


def test_trading_rules_per_market():
    cn = rules_for("CN")
    assert cn.t_plus_1 and cn.lot_size == 100 and cn.has_price_limit

    hk = rules_for("HK")
    assert not hk.t_plus_1 and hk.currency == "HKD"
    # 港股印花税：卖出 1 万港元 → 10 港元印花税
    assert hk.compute_commission(100, 100, "sell") == pytest.approx(10.0 + max(100 * 0.0003, 3.0), abs=0.01)
    # 买入无印花税
    assert hk.compute_commission(100, 100, "buy") == pytest.approx(max(100 * 0.0003, 3.0), abs=0.01)

    us = rules_for("US")
    assert not us.t_plus_1 and us.currency == "USD" and us.commission_rate == 0.0


def test_local_market_data_multi_market():
    """按市场读取行情：HK/US 无涨跌停，CN 保留涨跌停计算。"""
    from backend.services.simulation.services.local_market_data import (
        get_local_market_data,
    )

    data_root = _PROJECT / "data"
    if not (data_root / "quanthk" / "1_kline_data").is_dir():
        pytest.skip("本地市场数据未部署")

    hk = get_local_market_data("HK")
    bars = hk.load_date(date(2026, 8, 26), symbols=["0001.HK", "0700.HK"])
    assert "0001.HK" in bars
    bar = bars["0001.HK"]
    assert bar.close > 0
    assert bar.limit_up == float("inf") and bar.limit_down == 0.0
    assert not bar.is_st

    us = get_local_market_data("US")
    us_bars = us.load_date(date(2026, 8, 7), symbols=["AAPL"])
    assert "AAPL" in us_bars and us_bars["AAPL"].close > 0

    cn = get_local_market_data("CN")
    cn_bars = cn.load_date(date(2026, 8, 26), symbols=["600036.SH"])
    if cn_bars:
        cn_bar = cn_bars["600036.SH"]
        assert cn_bar.limit_down > 0  # CN 保留涨跌停计算
