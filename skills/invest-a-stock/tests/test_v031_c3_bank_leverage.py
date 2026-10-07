"""R4 同链最小修复（2026-10-04 复读 600036）：金融行业杠杆判断豁免。

反例（同一报告跨节冲突）：600036@172 新报告底稿「核心判断摘要」给出
`[结论] 资产负债率较高（>70%），财务杠杆偏大；权益乘数偏高，扩产依赖外部融资`
与 `[分析] 资产负债率偏高，需关注偿债风险…`，而同报告口径说明与风险豁免
（F0-8 / DCF 金融业豁免）明确银行高负债率不构成工商企业同口径杠杆信号。
修复：核心判断摘要与结论helper 对银行/非银金融（`_extract_industry` 判据，
与 `_check_fast_veto` 同一词表）改用负债经营模式口径；非金融输出不变。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _bank_dims() -> dict:
    return {
        "basic_info": {"data": {"name": "测试银行", "industry": "银行"}},
        "financials": {"data": [
            {"end_date": "20231231", "roe": 15.3, "debt_ratio": 91.0,
             "equity_multiplier": 10.0, "revenue": 3.3e11, "net_profit": 1.48e11},
            {"end_date": "20241231", "roe": 14.5, "debt_ratio": 90.8,
             "equity_multiplier": 10.1, "revenue": 3.35e11, "net_profit": 1.5e11},
            {"end_date": "20251231", "roe": 12.02, "debt_ratio": 90.183,
             "equity_multiplier": 10.24, "revenue": 3.375e11, "net_profit": 1.5e11},
        ]},
    }


def test_bank_asset_liability_conclusion_not_industrial_thresholds():
    from lib.render_markdown._v3 import _conclude_asset_liability

    text = _conclude_asset_liability(90.18, 10.24, None, None, None,
                                     financial_industry=True)
    assert "金融行业负债经营特征" in text
    assert "财务杠杆偏大" not in text
    assert "扩产依赖外部融资" not in text


def test_nonfinancial_conclusion_unchanged():
    from lib.render_markdown._v3 import _conclude_asset_liability

    text = _conclude_asset_liability(75.0, 4.0, None, None, None)
    assert "资产负债率较高（>70%）" in text
    assert "权益乘数偏高，扩产依赖外部融资" in text


def test_bank_core_judgment_summary_uses_business_model_wording():
    from lib.render_markdown._v3 import _FundamentalsContext, _core_judgment_summary

    ctx = _FundamentalsContext(_bank_dims(), {})
    assert ctx.financial_industry is True
    text = "\n".join(_core_judgment_summary(ctx))
    assert "资产负债率为金融行业负债经营特征" in text
    assert "需关注偿债风险" not in text
    assert "财务风险较大" not in text
    assert "扩产依赖外部融资" not in text


def test_nonfinancial_core_judgment_keeps_warning():
    from lib.render_markdown._v3 import _FundamentalsContext, _core_judgment_summary

    dims = _bank_dims()
    dims["basic_info"] = {"data": {"name": "测试制造", "industry": "电气设备"}}
    ctx = _FundamentalsContext(dims, {})
    assert ctx.financial_industry is False
    text = "\n".join(_core_judgment_summary(ctx))
    assert "需关注偿债风险" in text
    assert "金融行业负债经营特征" not in text


def _bank_4c_dims():
    return {
        "basic_info": {"data": {"industry": "银行"}},
        "financials": {"data": [
            {"end_date": "20231231", "roe": 15.3, "revenue": 3.3e11, "net_profit": 1.4e11,
             "netprofit_margin": 43.0, "asset_turnover": 0.013, "equity_multiplier": 10.0},
            {"end_date": "20241231", "roe": 12.02, "revenue": 3.35e11, "net_profit": 1.5e11,
             "netprofit_margin": 43.2, "asset_turnover": 0.0131, "equity_multiplier": 10.24},
        ]},
    }


def test_bank_dupont_hint_uses_business_model_wording():
    """R9 同链（二轮）：银行 C-② 杜邦提示不得赋予工商企业风险含义。"""
    from lib.render_markdown._v3 import (
        _FundamentalsContext, _section_4c_financial_quality,
    )

    ctx = _FundamentalsContext(_bank_4c_dims(), {})
    text = "\n".join(_section_4c_financial_quality(ctx, []))
    assert "金融行业不适用工商企业的高杠杆风险口径" in text
    assert "高杠杆在加息周期更脆弱" not in text
    assert "金融行业负债经营模式" in text           # 权益乘数标签附口径


def test_nonfinancial_dupont_hint_unchanged():
    from lib.render_markdown._v3 import (
        _FundamentalsContext, _section_4c_financial_quality,
    )

    dims = _bank_4c_dims()
    dims["basic_info"] = {"data": {"industry": "电气设备"}}
    ctx = _FundamentalsContext(dims, {})
    text = "\n".join(_section_4c_financial_quality(ctx, []))
    assert "高杠杆在加息周期更脆弱" in text
    assert "金融行业不适用" not in text
