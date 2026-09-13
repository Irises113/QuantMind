#!/usr/bin/env python3
"""因子工厂: QuantDB 富字段 × 算子 × 窗口 → 组合因子 → IC 筛选 → 训练 parquet

与 alpha_library_factors.py 的区别:
  - alpha_library 只用 OHLCV 生成 429 个经典因子；
  - 因子工厂用 QuantDB 的几百个数值字段（l1_factors / features_daily 等）
    组合生成成千上万个「表达式因子」，再用 IC/ICIR 筛选、相关性去重，
    产出可训练 parquet（默认写用户自定义市场 quantcustom）。

产物:
  <out>/dt=YYYYMMDD/data.parquet   (列: symbol(suffix) + date + 因子列 float32)
  <out>/MANIFEST.csv                (factor_name, expression, ic, icir, coverage, kept)
  <out>/PROPOSALS.json              (表达式清单，供量化研究/特征目录导入)

用法（容器内）:
  python /app/backend/scripts/factor_factory.py --smoke
  python /app/backend/scripts/factor_factory.py --top-n 2000 --windows 5,10,20,60
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import alpha_library_factors as alf  # noqa: E402  复用算子/写盘

log = logging.getLogger("factor_factory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

EPS = 1e-12


# ---------------------------------------------------------------------------
# 1. 路径解析（容器 /data 优先，回退项目 data/）
# ---------------------------------------------------------------------------


def _resolve_dir(env_key: str, container: str, project_sub: str) -> Path:
    candidates = [os.getenv(env_key), container, str(PROJECT_ROOT / "data" / project_sub)]
    for c in candidates:
        if c and Path(c).is_dir():
            return Path(c)
    # 输出目录可能尚不存在 → 取 env / 容器 / 项目
    fallback = os.getenv(env_key) or container or str(PROJECT_ROOT / "data" / project_sub)
    return Path(fallback)


QUANTDB_ROOT = _resolve_dir("QM_QUANTDB_DATA_DIR", "/data/quantdb", "quantdb")
CUSTOM_ROOT = _resolve_dir("QM_QUANTCUSTOM_DATA_DIR", "/data/quantcustom", "quantcustom")

ML_DIR = QUANTDB_ROOT / "6_ml_datasets"


# ---------------------------------------------------------------------------
# 2. 基字段白名单（剔除 OHLCV/ID，取 QuantDB 预计算数值字段）
# ---------------------------------------------------------------------------

# l1_factors: 15 大类里挑高价值、非共线的字段
L1_FIELDS = [
    # 换手 / 流动性
    "turn_5", "turn_20", "turn_std_20", "turn_z_20", "turn_ratio_1_5",
    "turn_ratio_1_20", "turn_trend_5_20", "turn_acc_5",
    # 资金流
    "amt_net_flow_5", "amt_net_flow_20", "amt_z_20", "amt_ratio_1_5",
    "amt_ratio_5_20", "amt_skew_20", "amt_up_ratio_20", "amt_vol_ratio_20",
    "mfi_14", "obv_slope_20",
    # 动量
    "mom_ret_1d", "mom_ret_3d", "mom_ret_5d", "mom_ret_10d", "mom_ret_20d",
    "mom_ret_60d", "mom_ma_gap_5", "mom_ma_gap_20", "mom_macd_hist",
    "mom_rsi_14", "mom_kdj_k",
    # 波动率
    "vol_std_5", "vol_std_20", "vol_atr_14", "vol_parkinson_20", "vol_gk_20",
    "vol_amp_20",
    # 技术指标
    "tech_bb_width", "tech_bb_pos", "tech_cci_20", "tech_adx_14",
    "tech_close_to_high_20", "tech_max_drawdown_20",
    # 基本面 / 估值
    "fun_float_mv", "fun_total_mv", "fun_mv_rank", "fun_pe", "fun_pb",
    "fun_bp", "fun_ep", "fun_value_zscore", "fun_roe", "fun_peg", "fun_np_growth",
    # 筹码
    "chip_profit_ratio_20", "chip_profit_ratio_60", "chip_concentration_20",
    "chip_floating_ratio", "chip_cost_90_width", "chip_profit_delta_5",
    # 风格
    "style_beta_20", "style_beta_60", "style_idio_vol_20", "style_idio_vol_60",
    "style_residual_ret_20",
    # 行业
    "ind_ret_5", "ind_ret_20", "ind_rotation_speed_20", "ind_strength_20",
    "ind_dispersion_20", "ind_breadth_up_20", "ind_crowding_20",
    "ind_relative_momentum_20", "ind_relative_pe",
    # 概念
    "concept_hot_score", "concept_momentum_top3", "concept_rotation_score",
    "concept_crowding_max", "concept_flow_rank", "concept_leader_score",
]

# features_daily: 技术 + 估值补充（与 l1 去重后使用）
FEATURES_FIELDS = [
    "rsi_14", "kdj_k", "kdj_d", "kdj_j", "macd_hist", "vol_atr_14", "beta_20",
    "pe_ttm", "pb", "ps_ttm", "dividend_rate", "total_mv", "float_mv",
    "net_profit_ttm", "revenue_ttm",
]

DATASET_SOURCES = {
    "l1_factors": L1_FIELDS,
    "features_daily": FEATURES_FIELDS,
}

SMOKE_L1_FIELDS = [
    "turn_20", "turn_z_20", "amt_net_flow_20", "mfi_14", "mom_ret_20d",
    "mom_rsi_14", "vol_std_20", "vol_parkinson_20", "tech_bb_pos", "tech_adx_14",
    "fun_bp", "fun_roe", "chip_profit_ratio_20", "style_idio_vol_20",
    "ind_strength_20", "concept_hot_score",
]


# ---------------------------------------------------------------------------
# 3. 算子定义（(op_name, value_fn, expr_fn)；窗口算子 value_fn(x, w)，截面算子 value_fn(x)）
# ---------------------------------------------------------------------------

WINDOW_OPS = [
    ("tsrank", lambda x, w: alf.TSRANK(x, w), lambda f, w: f"TSRANK(${f},{w})"),
    ("tsstd", lambda x, w: alf.STD(x, w), lambda f, w: f"STD(${f},{w})"),
    ("delta", lambda x, w: x - x.shift(w), lambda f, w: f"(${f} - Ref(${f},{w}))"),
    ("roc", lambda x, w: x / x.shift(w) - 1.0, lambda f, w: f"(${f} / Ref(${f},{w}) - 1)"),
    (
        "zscore",
        lambda x, w: (x - alf.MEAN(x, w)) / (alf.STD(x, w) + EPS),
        lambda f, w: f"((${f} - MEAN(${f},{w})) / (STD(${f},{w}) + 1e-12))",
    ),
    ("decay", lambda x, w: alf.DECAY(x, w), lambda f, w: f"DECAYLINEAR(${f},{w})"),
    (
        "slope",
        lambda x, w: alf.REG(x, w)[0]
        / (x.abs().rolling(w, min_periods=w).mean() + EPS),
        lambda f, w: f"(SLOPE(${f},{w}) / (MEAN(ABS(${f}),{w}) + 1e-12))",
    ),
]

CS_OPS = [
    ("csrank", lambda x: alf.R(x), lambda f: f"RANK(${f})"),
    (
        "cszscore",
        lambda x: x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1) + EPS, axis=0),
        lambda f: f"ZSCORE(${f})",
    ),
]

OP_BY_NAME = {name: (fn, expr) for name, fn, expr in WINDOW_OPS}
CS_OP_BY_NAME = {name: (fn, expr) for name, fn, expr in CS_OPS}

_SANITIZE = re.compile(r"[^a-zA-Z0-9_]+")


def _feature_name(op: str, field: str, window: int | None) -> str:
    base = f"ff_{op}{window}_{field}" if window else f"ff_{op}_{field}"
    return _SANITIZE.sub("_", base).lower()[:64]


# ---------------------------------------------------------------------------
# 4. 数据加载
# ---------------------------------------------------------------------------


def _parse_dt(series: pd.Series) -> pd.Series:
    """dt 可能是 '20160104' / 20160104 / Timestamp，统一转 datetime。"""
    s = series.astype(str)
    out = pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    bad = out.isna()
    if bad.any():
        out.loc[bad] = pd.to_datetime(s[bad], errors="coerce")
    return out


def load_fields(
    dataset: str,
    fields: list[str],
    *,
    start_year: int | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    max_symbols: int | None = None,
) -> dict[str, pd.DataFrame]:
    """读取 6_ml_datasets/<dataset> 指定列 → {field: 宽表(index=time, cols=symbol)}。"""
    glob = str(ML_DIR / dataset / "dt=*" / "data.parquet")
    if not list(ML_DIR.glob(f"{dataset}/dt=*")):
        log.warning("数据集无分区，跳过: %s", glob)
        return {}
    cols = ", ".join(["symbol", "dt"] + [f'"{f}"' for f in fields])
    q = f"SELECT {cols} FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"
    conds = []
    if start_year:
        conds.append(f"CAST(dt AS VARCHAR) >= '{start_year}0101'")
    if start is not None:
        conds.append(f"CAST(dt AS VARCHAR) >= '{start.strftime('%Y%m%d')}'")
    if end is not None:
        conds.append(f"CAST(dt AS VARCHAR) <= '{end.strftime('%Y%m%d')}'")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    con = duckdb.connect()
    try:
        df = con.execute(q).fetchdf()
    finally:
        con.close()
    if df.empty:
        return {}
    df["_dt"] = _parse_dt(df["dt"])
    df["symbol"] = df["symbol"].astype(str)
    df = df.dropna(subset=["_dt"]).drop_duplicates(subset=["symbol", "_dt"])
    if max_symbols:
        syms = sorted(df["symbol"].unique())[:max_symbols]
        df = df[df["symbol"].isin(syms)]

    out: dict[str, pd.DataFrame] = {}
    for f in fields:
        if f not in df.columns:
            continue
        wide = df.pivot_table(index="_dt", columns="symbol", values=f, aggfunc="last")
        wide = wide.sort_index()
        if wide.notna().to_numpy().any():
            out[f] = wide.astype("float64")
    return out


def load_close(
    *,
    start_year: int | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    max_symbols: int | None = None,
) -> pd.DataFrame:
    """daily_forward 收盘价宽表（用于前瞻收益 / IC）。"""
    return load_ohlcv(
        ["close"], start_year=start_year, start=start, end=end, max_symbols=max_symbols
    )["close"]


def load_ohlcv(
    cols: list[str],
    *,
    start_year: int | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    max_symbols: int | None = None,
) -> dict[str, pd.DataFrame]:
    """daily_forward 行情宽表 {col: index=time, cols=symbol}。

    CUSTOM 市场的训练读取器需要因子源自带 OHLCV（其 daily_backward 未部署时
    会用同目录 l1_factors 作为行情补给），故工厂产物需内联这些列。
    """
    glob = str(QUANTDB_ROOT / "1_kline_data" / "daily_forward" / "dt=*" / "data.parquet")
    sel = ", ".join(["symbol", "time"] + [f'"{c}"' for c in cols])
    q = f"SELECT {sel} FROM read_parquet('{glob}', hive_partitioning=true)"
    conds = []
    if start_year:
        conds.append(f"year(time) >= {start_year}")
    if start is not None:
        conds.append(f"time >= TIMESTAMP '{start.strftime('%Y-%m-%d')}'")
    if end is not None:
        conds.append(f"time <= TIMESTAMP '{end.strftime('%Y-%m-%d')}'")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    con = duckdb.connect()
    try:
        df = con.execute(q).fetchdf()
    finally:
        con.close()
    df["time"] = pd.to_datetime(df["time"])
    df["symbol"] = df["symbol"].astype(str)
    df = df.drop_duplicates(subset=["symbol", "time"])
    if max_symbols:
        syms = sorted(df["symbol"].unique())[:max_symbols]
        df = df[df["symbol"].isin(syms)]
    out: dict[str, pd.DataFrame] = {}
    for c in cols:
        if c in df.columns:
            out[c] = df.pivot(index="time", columns="symbol", values=c).sort_index()
    return out


# ---------------------------------------------------------------------------
# 5. 因子生成
# ---------------------------------------------------------------------------


def generate_factors(
    base: dict[str, pd.DataFrame],
    fields: list[str],
    *,
    windows: list[int],
    ops: list[str],
    cs_ops: list[str],
    max_factors: int | None = None,
) -> dict[str, tuple[pd.DataFrame, str, str]]:
    """生成 {feature_name: (values, expression, field)}。"""
    out: dict[str, tuple[pd.DataFrame, str, str]] = {}
    for field in fields:
        x = base.get(field)
        if x is None or x.empty:
            continue
        # 截面算子（无窗口）
        for op in cs_ops:
            if op not in CS_OP_BY_NAME:
                continue
            fn, expr = CS_OP_BY_NAME[op]
            name = _feature_name(op, field, None)
            try:
                out[name] = (fn(x).astype(np.float32), expr(field), field)
            except Exception as exc:  # noqa: BLE001
                log.debug("op %s on %s failed: %s", op, field, exc)
        # 窗口算子
        for op in ops:
            if op not in OP_BY_NAME:
                continue
            fn, expr = OP_BY_NAME[op]
            for w in windows:
                name = _feature_name(op, field, w)
                try:
                    out[name] = (fn(x, w).astype(np.float32), expr(field, w), field)
                except Exception as exc:  # noqa: BLE001
                    log.debug("op %s(w=%d) on %s failed: %s", op, w, field, exc)
        if max_factors and len(out) >= max_factors:
            break
    log.info("generated %d candidate factors from %d fields", len(out), len(fields))
    return out


# ---------------------------------------------------------------------------
# 6. IC / ICIR / 覆盖率
# ---------------------------------------------------------------------------


def _daily_rank_ic(fac: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """逐日横截面 Spearman IC（秩相关 = 秩的 Pearson）。"""
    rf = fac.rank(axis=1)
    rr = fwd.rank(axis=1)
    rf_c = rf.sub(rf.mean(axis=1), axis=0)
    rr_c = rr.sub(rr.mean(axis=1), axis=0)
    num = (rf_c * rr_c).sum(axis=1)
    den = np.sqrt((rf_c**2).sum(axis=1) * (rr_c**2).sum(axis=1))
    ic = num / den.replace(0, np.nan)
    return ic.replace([np.inf, -np.inf], np.nan).dropna()


def screen_factors(
    factors: dict[str, tuple[pd.DataFrame, str, str]],
    close: pd.DataFrame,
    *,
    horizon: int = 1,
    screen_days: int = 500,
    min_coverage: float = 0.5,
) -> pd.DataFrame:
    """按最近 screen_days 计算每个因子的 IC/ICIR/覆盖率，返回带指标的清单。"""
    fwd = close.shift(-horizon) / close - 1.0
    screen_dates = close.index[-screen_days:]
    fwd_win = fwd.reindex(index=screen_dates)

    rows = []
    for name, (fac, expr, field) in factors.items():
        sub = fac.reindex(index=screen_dates, columns=close.columns)
        coverage = float(sub.notna().to_numpy().mean())
        if coverage < min_coverage:
            rows.append({
                "factor_name": name, "expression": expr, "field": field,
                "ic": np.nan, "icir": np.nan, "coverage": coverage, "n_ic_days": 0,
            })
            continue
        ic = _daily_rank_ic(sub, fwd_win)
        ic_mean = float(ic.mean()) if len(ic) else np.nan
        ic_std = float(ic.std(ddof=1)) if len(ic) > 1 else np.nan
        icir = float(ic_mean / ic_std) if ic_std and ic_std > 0 else 0.0
        rows.append({
            "factor_name": name, "expression": expr, "field": field,
            "ic": ic_mean, "icir": icir, "coverage": coverage, "n_ic_days": int(len(ic)),
        })
    df = pd.DataFrame(rows)
    df["abs_ic"] = df["ic"].abs()
    log.info("screened %d factors (mean |IC| = %.4f)", len(df), df["abs_ic"].mean())
    return df


# ---------------------------------------------------------------------------
# 7. 相关性去重
# ---------------------------------------------------------------------------


def dedup_by_correlation(
    factors: dict[str, tuple[pd.DataFrame, str, str]],
    screened: pd.DataFrame,
    close: pd.DataFrame,
    *,
    top_n: int,
    corr_threshold: float = 0.85,
    screen_days: int = 500,
    max_samples: int = 20000,
) -> pd.DataFrame:
    """按 |IC| 排序后贪心去重（|corr|>阈值则丢弃），返回保留的 top_n。"""
    cand = screened.dropna(subset=["ic"]).sort_values("abs_ic", ascending=False).head(top_n * 3)
    if cand.empty:
        return screened.head(0)

    screen_dates = close.index[-screen_days:]
    # 先按日期采样再堆叠：避免构造 (n_days×n_syms) × n_candidates 的超大中间矩阵
    n_syms = max(1, close.shape[1])
    max_dates = max(5, min(len(screen_dates), max_samples // n_syms))
    step = max(1, len(screen_dates) // max_dates)
    dates = screen_dates[::step][:max_dates]

    mat = {}
    for name in cand["factor_name"]:
        s = factors[name][0].reindex(index=dates, columns=close.columns)
        mat[name] = s.to_numpy(dtype=np.float32).ravel()
    stack = pd.DataFrame(mat)
    corr = stack.corr(min_periods=200)

    kept: list[str] = []
    for name in cand["factor_name"]:
        if len(kept) >= top_n:
            break
        redundant = False
        for k in kept:
            c = corr.at[name, k] if name in corr.index and k in corr.columns else np.nan
            if np.isfinite(c) and abs(c) > corr_threshold:
                redundant = True
                break
        if not redundant:
            kept.append(name)
    log.info("dedup: %d candidates → %d kept (threshold=%.2f)", len(cand), len(kept), corr_threshold)
    out = screened.set_index("factor_name").loc[kept].reset_index()
    out["kept"] = True
    return out


# ---------------------------------------------------------------------------
# 8. 写盘
# ---------------------------------------------------------------------------


def write_factor_partitions(
    factors: dict[str, tuple[pd.DataFrame, str, str]],
    names: list[str],
    *,
    out_root: Path,
    start_dt: str = "20160101",
    rebuild: bool = False,
    ohlcv: dict[str, pd.DataFrame] | None = None,
) -> int:
    """按日写 <out_root>/dt=YYYYMMDD/data.parquet。

    列: symbol(suffix) + date + OHLCV + 因子列（float32）。
    内联 OHLCV 使 CUSTOM 因子源可被训练读取器直接消费（标签需要行情列）。
    """
    frames = [factors[n][0] for n in names]
    dates = frames[0].index
    syms = list(frames[0].columns)
    out_root.mkdir(parents=True, exist_ok=True)

    ohlcv_cols = list((ohlcv or {}).keys())
    ohlcv_arrs = [
        (ohlcv[c].reindex(index=dates, columns=syms).values, c) for c in ohlcv_cols
    ]

    written = 0
    chunk = 40
    n = len(dates)
    arrs = [f.values for f in frames]
    for b in range(0, n, chunk):
        b_end = min(b + chunk, n)
        block = np.stack([a[b:b_end] for a in arrs], axis=2)  # (nb, n_sym, n_fac)
        for k in range(b, b_end):
            dt_str = pd.Timestamp(dates[k]).strftime("%Y%m%d")
            if dt_str < start_dt:
                continue
            target = out_root / f"dt={dt_str}" / "data.parquet"
            if target.exists() and not rebuild:
                continue
            day = pd.DataFrame(block[k - b], index=syms, columns=names)
            day = day.replace([np.inf, -np.inf], np.nan)
            day = day.reset_index().rename(columns={"index": "symbol"})
            day.insert(1, "date", pd.Timestamp(dates[k]))
            for arr, c in ohlcv_arrs:
                day[c] = arr[k].astype("float32")
            for c in names:
                day[c] = day[c].astype("float32")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.parent / ".tmp-data.parquet"
            try:
                day.to_parquet(tmp, index=False)
                tmp.replace(target)
                written += 1
            finally:
                tmp.unlink(missing_ok=True)
        del block
    log.info("wrote %d partitions → %s", written, out_root)
    return written


# ---------------------------------------------------------------------------
# 9. 主流程
# ---------------------------------------------------------------------------


def _select_fields(smoke: bool, limit: int | None) -> list[tuple[str, str]]:
    """返回 [(dataset, field)]，l1 优先，features_daily 补充不重名者。"""
    if smoke:
        l1 = SMOKE_L1_FIELDS
        fd: list[str] = ["rsi_14", "macd_hist", "pe_ttm", "pb"]
    else:
        l1 = L1_FIELDS
        fd = FEATURES_FIELDS
    pairs: list[tuple[str, str]] = [("l1_factors", f) for f in l1]
    seen = set(l1)
    pairs += [("features_daily", f) for f in fd if f not in seen]
    if limit:
        pairs = pairs[:limit]
    return pairs


def _slice(df: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.DataFrame:
    if start is None and end is None:
        return df
    idx = df.index
    mask = np.ones(len(idx), dtype=bool)
    if start is not None:
        mask &= idx >= start
    if end is not None:
        mask &= idx <= end
    return df.loc[mask]


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    windows = [int(w) for w in str(args.windows).split(",") if w.strip()]
    ops = [o.strip() for o in str(args.ops).split(",") if o.strip()]
    cs_ops = [o.strip() for o in str(args.cs_ops).split(",") if o.strip()]
    pairs = _select_fields(args.smoke, args.limit_fields)

    start_ts = pd.Timestamp(args.start_date) if args.start_date else None
    end_ts = pd.Timestamp(args.end_date) if args.end_date else None
    warmup = (max(windows) + 10) if windows else 70
    load_start = (start_ts - pd.Timedelta(days=warmup)) if start_ts is not None else None
    log.info("mode=%s fields=%d windows=%s ops=%s cs_ops=%s range=%s~%s warmup=%dd",
             "SMOKE" if args.smoke else "FULL", len(pairs), windows, ops, cs_ops,
             start_ts.date() if start_ts is not None else "-",
             end_ts.date() if end_ts is not None else "-", warmup)

    # 1) 加载基字段（含 warmup，保证窗口算子前段有效）
    by_dataset: dict[str, list[str]] = {}
    for ds, f in pairs:
        by_dataset.setdefault(ds, []).append(f)
    base: dict[str, pd.DataFrame] = {}
    for ds, fs in by_dataset.items():
        base.update(load_fields(
            ds, fs, start_year=args.start_year, start=load_start, end=end_ts,
            max_symbols=args.max_symbols,
        ))
    log.info("loaded %d base fields (%.0fs)", len(base), time.time() - t0)

    close = load_close(
        start_year=args.start_year, start=load_start, end=end_ts, max_symbols=args.max_symbols
    )
    # 对齐：仅保留基字段与 close 共有的日期
    base = {k: v.reindex(index=close.index).reindex(columns=close.columns) for k, v in base.items()}

    # 2) 生成候选因子（含 warmup 后切片到目标窗口）
    fields = [f for _, f in pairs if f in base]
    factors = generate_factors(
        base, fields, windows=windows, ops=ops, cs_ops=cs_ops, max_factors=args.max_candidates
    )
    if not factors:
        log.warning("no factors generated; abort")
        return
    factors = {n: (_slice(v, start_ts, end_ts), e, f) for n, (v, e, f) in factors.items()}
    close_win = _slice(close, start_ts, end_ts)

    # 3) 筛选（screen_days 超过窗口时自然用满窗口）
    screened = screen_factors(
        factors, close_win, horizon=args.horizon,
        screen_days=args.screen_days, min_coverage=args.min_coverage,
    )

    # 4) 去重
    kept = dedup_by_correlation(
        factors, screened, close_win,
        top_n=args.top_n, corr_threshold=args.corr_threshold, screen_days=args.screen_days,
    )
    kept = kept.sort_values("abs_ic", ascending=False)
    kept_names = kept["factor_name"].tolist()
    log.info("final kept: %d", len(kept_names))

    out_root = Path(args.out) if args.out else (CUSTOM_ROOT / "6_ml_datasets" / "l1_factors")
    start_dt = args.start_dt or (start_ts.strftime("%Y%m%d") if start_ts is not None else "20160101")

    if args.dry_run:
        log.info("dry-run: skip partition write")
    else:
        out_root.mkdir(parents=True, exist_ok=True)
        ohlcv = load_ohlcv(
            ["open", "high", "low", "close", "volume", "amount"],
            start_year=args.start_year, start=load_start, end=end_ts, max_symbols=args.max_symbols,
        )
        n = write_factor_partitions(
            factors, kept_names, out_root=out_root,
            start_dt=start_dt, rebuild=args.rebuild, ohlcv=ohlcv,
        )
        log.info("partitions: %d", n)
        kept.to_csv(out_root / "MANIFEST.csv", index=False, encoding="utf-8")
        (out_root / "PROPOSALS.json").write_text(
            json.dumps(
                [{"name": r.factor_name, "expression": r.expression,
                  "ic": None if pd.isna(r.ic) else round(float(r.ic), 5),
                  "icir": None if pd.isna(r.icir) else round(float(r.icir), 5),
                  "coverage": round(float(r.coverage), 4)}
                 for r in kept.itertuples()],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    log.info("DONE in %.0fs (kept=%d, out=%s)", time.time() - t0, len(kept_names), out_root)


def main() -> None:
    ap = argparse.ArgumentParser(description="QuantDB 因子工厂")
    ap.add_argument("--smoke", action="store_true", help="冒烟: 小字段集 × 小窗口")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--start-year", type=int, default=None)
    ap.add_argument("--start-date", default=None, help="目标窗口起始 YYYY-MM-DD")
    ap.add_argument("--end-date", default=None, help="目标窗口结束 YYYY-MM-DD")
    ap.add_argument("--limit-fields", type=int, default=None)
    ap.add_argument("--windows", default="5,10,20,60")
    ap.add_argument("--ops", default="tsrank,tsstd,roc,zscore,delta,decay,slope")
    ap.add_argument("--cs-ops", default="csrank,cszscore")
    ap.add_argument("--max-candidates", type=int, default=None, help="生成上限（调试用）")
    ap.add_argument("--top-n", type=int, default=200, help="IC 去重后保留因子数")
    ap.add_argument("--horizon", type=int, default=1, help="前瞻收益天数")
    ap.add_argument("--screen-days", type=int, default=500)
    ap.add_argument("--min-coverage", type=float, default=0.5)
    ap.add_argument("--corr-threshold", type=float, default=0.85)
    ap.add_argument("--start-dt", default="20160101")
    ap.add_argument("--out", default=None, help="输出根目录（默认 quantcustom/6_ml_datasets/l1_factors）")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.smoke:
        args.max_symbols = args.max_symbols or 50
        args.start_year = args.start_year or 2024
        args.windows = "20"
        args.ops = "tsrank,roc,zscore,delta"
    run(args)


if __name__ == "__main__":
    main()
