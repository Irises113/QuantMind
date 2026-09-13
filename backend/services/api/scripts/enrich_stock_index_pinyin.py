#!/usr/bin/env python3
"""为已有 stocks_index.json 补充 pinyin 拼音首字母字段。

本地维护命令（pypinyin 不入项目依赖，需本机自行安装）：
  pip install pypinyin
  python backend/services/api/scripts/enrich_stock_index_pinyin.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stock_pinyin_util import name_to_pinyin_abbr  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    output_path = os.path.abspath(
        os.getenv("STOCK_INDEX_JSON_PATH", "data/stocks/stocks_index.json")
    )
    if not os.path.exists(output_path):
        raise FileNotFoundError(output_path)

    with open(output_path, encoding="utf-8") as f:
        payload = json.load(f)

    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("stocks_index.json 缺少 items 数组")

    updated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        pinyin = name_to_pinyin_abbr(name)
        if item.get("pinyin") != pinyin:
            item["pinyin"] = pinyin
            updated += 1

    payload["generated_at"] = _now_iso()
    payload["count"] = len(items)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "ok": True,
                "output": output_path,
                "count": len(items),
                "updated": updated,
                "generated_at": payload["generated_at"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
