"""_fetch_turnover 深交所直连降级（akshare 库内 float.replace bug 规避）。

背景：akshare stock_szse_summary 库内 L41 `map(lambda x: x.replace(",", ""))`
假定「第 2 列后全为字符串」；上游 xlsx 数值列被 pandas 推断为 float 后崩溃
（'float' object has no attribute 'replace'，2026-08-31 实证）。降级路径
直连 szse.cn 官方 API 并以容错解析替代：列级 astype(str) 剥逗号 +
pd.to_numeric(coerce)——数值/字符串形态均容错。

测试清单（合成 fixture，无网络）：
① float 形态：数值列 float → 不崩、产出同构（数值正确）
② 字符串带逗号形态：'1,234,567,890' → 剥逗号数值化正确
③ 混合形态（字符串逗号 + 数值混合列）→ 均可数值化（错误 coerce 为 NaN 不崩）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from market_microstructure import _finalize_szse_df  # noqa: E402


def test_float_shape_survives():
    """① 现网漂移形态：数值列已是 float（akshare 崩溃根因）→ 容错不崩且值正确。"""
    df = pd.DataFrame({
        "证券类别": ["股票", "基金"],
        "数量": [3, 2000],
        "成交金额": [2_566_074_972.0, 8_765_211.0],
        "总市值": [58_348_265_000.0, 5_812_193_000.0],
        "流通市值": [47_583_912_000.0, 5_801_209_000.0],
    })
    out = _finalize_szse_df(df)
    assert list(out.columns) == ["证券类别", "数量", "成交金额", "总市值", "流通市值"]
    assert out["成交金额"].iloc[0] == 2_566_074_972.0
    assert out["流通市值"].iloc[0] == 47_583_912_000.0


def test_string_comma_shape():
    """② 传统形态：带千分位逗号字符串 → 剥逗号数值化正确（akshare 假设场景）。"""
    df = pd.DataFrame({
        "证券类别": ["股票"],
        "数量": ["1,234"],
        "成交金额": ["56,074,972"],
        "总市值": ["58,348,265,000"],
        "流通市值": ["47,583,912,000"],
    })
    out = _finalize_szse_df(df)
    assert out["数量"].iloc[0] == 1234
    assert out["成交金额"].iloc[0] == 56_074_972
    assert out["流通市值"].iloc[0] == 47_583_912_000


def test_mixed_shape_and_category_strip():
    """③ 混合形态（字符串逗号 + 数值混杂）+ 证券类别剥空格 → 容错数值化。"""
    df = pd.DataFrame({
        "证券类别": ["股票", "债券 "],
        "数量": [5, "2,000"],
        "成交金额": ["12,345.6", 987_654.0],
        "总市值": [1.5e10, "2,000"],
        "流通市值": [1.2e10, 2_000],
    })
    out = _finalize_szse_df(df)
    assert out["证券类别"].tolist() == ["股票", "债券"]
    assert out["成交金额"].iloc[0] == 12_345.6
    assert out["成交金额"].iloc[1] == 987_654.0
