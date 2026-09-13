from pathlib import Path

import pandas as pd
import pytest

from backend.services.engine.qlib_app.utils.simple_signal import SimpleSignal


def test_simple_signal_loads_first_column_from_qlib_instrument_file(tmp_path: Path):
    instrument_file = tmp_path / "margin.txt"
    instrument_file.write_text(
        "SH600000\t2005-01-01\t2099-12-31\nSZ000001\t2005-01-01\t2099-12-31\n",
        encoding="utf-8",
    )

    signal = SimpleSignal(universe=str(instrument_file))

    assert signal._load_instruments_from_file(instrument_file) == ["SH600000", "SZ000001"]


def test_simple_signal_lags_pred_series_to_next_available_date(tmp_path: Path):
    pred_path = tmp_path / "pred.pkl"
    idx = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "SH600000"),
            (pd.Timestamp("2025-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    pd.DataFrame({"score": [0.1, 0.2]}, index=idx).to_pickle(pred_path)

    # qlib_signal_shift_days=0 表示由本适配器独自从实现完整滞后（非 step 引擎场景）
    signal = SimpleSignal(
        universe="all",
        pred_path=str(pred_path),
        signal_lag_days=1,
        qlib_signal_shift_days=0,
    )
    series = signal._load_pred_series(pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-03"))

    assert series is not None
    assert series.loc[(pd.Timestamp("2025-01-03"), "SH600000")] == 0.1


def test_simple_signal_does_not_double_apply_qlib_step_shift(tmp_path: Path):
    """回归：step 引擎 qlib 策略已用 shift=1 实现一天滞后，适配器不应再滞后一天。

    默认 signal_lag_days=1 + qlib_signal_shift_days=1 → 有效滞后 0：
    交易日 2025-01-03 应拿到 pred(2025-01-03)=0.2，而不是 pred(2025-01-02)=0.1。
    """
    pred_path = tmp_path / "pred.pkl"
    idx = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "SH600000"),
            (pd.Timestamp("2025-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    pd.DataFrame({"score": [0.1, 0.2]}, index=idx).to_pickle(pred_path)

    signal = SimpleSignal(universe="all", pred_path=str(pred_path), signal_lag_days=1)
    assert signal._effective_lag_days == 0
    series = signal._load_pred_series(pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-03"))

    assert series is not None
    assert series.loc[(pd.Timestamp("2025-01-03"), "SH600000")] == 0.2


def test_simple_signal_applies_residual_lag_when_signal_lag_days_gt_one(tmp_path: Path):
    """signal_lag_days=2 时，扣除 qlib 自带 shift=1 后仍应补 1 天。"""
    pred_path = tmp_path / "pred.pkl"
    idx = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "SH600000"),
            (pd.Timestamp("2025-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    pd.DataFrame({"score": [0.1, 0.2]}, index=idx).to_pickle(pred_path)

    signal = SimpleSignal(universe="all", pred_path=str(pred_path), signal_lag_days=2)
    assert signal._effective_lag_days == 1
    series = signal._load_pred_series(pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-03"))

    assert series is not None
    assert series.loc[(pd.Timestamp("2025-01-03"), "SH600000")] == 0.1


def test_simple_signal_with_pred_path_returns_empty_when_slice_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pred_path = tmp_path / "pred.pkl"
    idx = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "SH600000"),
            (pd.Timestamp("2025-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    pd.DataFrame({"score": [0.1, 0.2]}, index=idx).to_pickle(pred_path)

    signal = SimpleSignal(universe="all", pred_path=str(pred_path), signal_lag_days=1)
    monkeypatch.setattr(
        signal,
        "_get_universe_instruments",
        lambda: (_ for _ in ()).throw(AssertionError("pred-backed signal should not fallback to feature loading")),
    )
    result = signal.get_signal(pd.Timestamp("2025-01-06"), pd.Timestamp("2025-01-06"))

    assert result.empty
