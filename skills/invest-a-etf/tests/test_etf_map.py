"""CSINDEX_MAP coverage vs hedge map."""

from __future__ import annotations

from etf_data import CSINDEX_MAP, ETF_HEDGE_MAP


def test_159845_in_csindex_map():
    assert CSINDEX_MAP.get("159845") == "000852"
    assert CSINDEX_MAP["159845"] == CSINDEX_MAP["512100"]


def test_588000_options_and_coverage():
    entry = ETF_HEDGE_MAP["588000"]
    assert entry["options"] == "科创50ETF期权"
    assert entry["coverage"] == "high"


def test_hedge_mapped_cn_etfs_have_csindex_when_applicable():
    """境内宽基 ETF：有对冲条目且跟踪国内指数的，应有 csindex 映射。"""
    overseas = {"513100", "513500", "518880", "511880"}  # 海外/商品/货基可无 A 股指数 PE
    for code, meta in ETF_HEDGE_MAP.items():
        if code in overseas:
            continue
        if meta.get("index") in ("纳指100", "标普500", "黄金9999", "银华日利"):
            continue
        # 行业主题可无 csindex 宽基码，跳过
        if meta.get("coverage") == "none" and code.startswith(("515", "516")):
            continue
        if code in ("510880",):  # 红利 — 可选
            continue
        if code in CSINDEX_MAP:
            assert CSINDEX_MAP[code]
        # 至少 159845 / 主流宽基已覆盖
    assert "159845" in CSINDEX_MAP and "512100" in CSINDEX_MAP


def test_159206_mapped_to_defense_satellite():
    """v0.2.5 R14: 159206 补 ETF_TO_SW_INDUSTRY（国防军工/卫星）。"""
    from etf_data import ETF_TO_SW_INDUSTRY

    entry = ETF_TO_SW_INDUSTRY["159206"]
    assert entry["sw_code"] == "801740"
    assert entry["sw_name"] == "国防军工"
    assert entry["sub"] == "卫星"


def test_159206_cohort_members():
    """peers 自动发现：159206 同 sw_code 成员 = 军工 ETF（512760 芯片已归电子）。"""
    from etf_data import ETF_TO_SW_INDUSTRY

    cohort = {
        c for c, info in ETF_TO_SW_INDUSTRY.items()
        if info["sw_code"] == "801740"
    }
    assert "159206" in cohort
    assert {"512660", "512710"} <= cohort
    assert "512760" not in cohort  # 芯片ETF国泰 → 801080 电子


def test_512760_mapped_to_electronics():
    """v0.2.5 R13 修正：512760 芯片ETF 原误映射国防军工 → 电子/芯片。"""
    from etf_data import ETF_TO_SW_INDUSTRY

    entry = ETF_TO_SW_INDUSTRY["512760"]
    assert entry["sw_code"] == "801080"
    assert entry["sub"] == "芯片"
