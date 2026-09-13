#!/usr/bin/env python3
"""
从 QuantDB instrument_list 构建股票搜索索引 JSON。

数据源已从 PG（stocks/symbols 表）迁移到 QuantDB：
  2_base_sector/instrument_detail/instrument_list.parquet

默认输出：
  data/stocks/stocks_index.json
可通过环境变量覆盖：
  STOCK_INDEX_JSON_PATH=/abs/path/stocks_index.json
  QM_QUANTDB_DATA_DIR=/data/quantdb

拼音字段需本机安装 pypinyin（不入 requirements.txt）：
  pip install pypinyin
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from stock_pinyin_util import name_to_pinyin_abbr  # noqa: E402


def _pinyin_or_empty(name: str) -> str:
    try:
        return name_to_pinyin_abbr(name)
    except ImportError:
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# 候选数据目录：容器内挂载点 / 环境变量 / 本地项目根
def _candidate_instrument_paths() -> List[Path]:
    qdb_dir = os.getenv("QM_QUANTDB_DATA_DIR", "").strip()
    candidates: List[Path] = []
    if qdb_dir:
        candidates.append(Path(qdb_dir) / "2_base_sector" / "instrument_detail")
    candidates.append(Path("/data/quantdb") / "2_base_sector" / "instrument_detail")
    candidates.append(Path("/app/data/quantdb") / "2_base_sector" / "instrument_detail")
    project_root = Path(__file__).resolve().parents[4]
    candidates.append(project_root / "data" / "quantdb" / "2_base_sector" / "instrument_detail")
    for fname in ("instrument_detail.parquet", "instrument_list.parquet"):
        for base in candidates:
            p = base / fname
            if p.exists():
                return [p]
    return []


def _load_instrument_df() -> pd.DataFrame:
    paths = _candidate_instrument_paths()
    if not paths:
        raise FileNotFoundError(
            "未找到 QuantDB instrument_list/instrument_detail.parquet（候选目录见脚本注释）"
        )
    df = pd.read_parquet(paths[0])
    # 统一列名：Symbol -> symbol
    if "Symbol" in df.columns and "symbol" not in df.columns:
        df = df.rename(columns={"Symbol": "symbol"})
    if "Name" in df.columns and "name" not in df.columns:
        df = df.rename(columns={"Name": "name"})
    return df


def _is_a_share(symbol: str) -> bool:
    """按 A 股市场规则识别：SH 6/9 开头、SZ 0/3/2 开头、BJ 4/8 开头。"""
    s = str(symbol or "").strip().upper()
    if "." not in s:
        return False
    code, ex = s.split(".", 1)
    if ex == "SH":
        return code.startswith(("6", "9"))
    if ex == "SZ":
        return code.startswith(("0", "3", "2"))
    if ex == "BJ":
        return code.startswith(("4", "8"))
    return False


def build_items(rows: List[Any]) -> List[dict[str, Any]]:
    items: List[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or not _is_a_share(symbol):
            continue
        # 排除退市股（IsQuitGP=1）
        quit_flag = row.get("IsQuitGP")
        if pd.notna(quit_flag):
            try:
                if int(float(quit_flag)) == 1:
                    continue
            except (TypeError, ValueError):
                pass
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        code, exchange = symbol.split(".", 1)
        pinyin = _pinyin_or_empty(name)
        items.append(
            {
                "symbol": symbol,
                "code": code,
                "exchange": exchange,
                "name": name,
                "abbr": code.lower(),
                "pinyin": pinyin,
            }
        )
    return items


def main() -> None:
    output_path = os.path.abspath(os.getenv("STOCK_INDEX_JSON_PATH", "data/stocks/stocks_index.json"))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    df = _load_instrument_df()
    rows = [dict(r) for r in df.to_dict("records")]
    items = build_items(rows)
    payload = {
        "generated_at": _now_iso(),
        "count": len(items),
        "items": items,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "ok": True,
                "output": output_path,
                "count": len(items),
                "generated_at": payload["generated_at"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
