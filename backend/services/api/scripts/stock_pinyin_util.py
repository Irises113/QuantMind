"""股票名称 → 拼音首字母（供 stocks_index.json 搜索索引使用）。

依赖 pypinyin 仅在本机维护索引时使用（不入 requirements.txt）：
  pip install pypinyin
"""

from __future__ import annotations

import re


def name_to_pinyin_abbr(name: str) -> str:
    """将股票简称转为拼音首字母串，用于代码/名称/拼音搜索。

    规则：
    - 连续汉字块：按词组取拼音首字母（避免「行→x」等多音字误判）
    - ASCII 字母数字：原样保留（小写），覆盖 ST、N、XD 等前缀
    - 空格、标点、全角符号：忽略
    """
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError as exc:
        raise ImportError(
            "生成拼音索引需要本机安装 pypinyin（不入项目依赖）：pip install pypinyin"
        ) from exc

    text = str(name or "").strip()
    if not text:
        return ""

    parts: list[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text):
        chunk = match.group()
        if "\u4e00" <= chunk[0] <= "\u9fff":
            for initial in lazy_pinyin(chunk, style=Style.FIRST_LETTER):
                if initial:
                    parts.append(initial.lower())
        else:
            parts.append(chunk.lower())
    return "".join(parts)
