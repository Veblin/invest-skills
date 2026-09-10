"""invest-a-forecast-scan 纯函数测试（离线，不触网）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from forecast_scan import _cell, analyze  # noqa: E402


def _rec(ts, typ, pmin=None, pmax=None, npmin=None, npmax=None, ann="20260715", end="20260630"):
    return {
        "ts_code": ts, "ann_date": ann, "end_date": end, "type": typ,
        "p_change_min": pmin, "p_change_max": pmax,
        "net_profit_min": npmin, "net_profit_max": npmax,
        "summary": "预计:净利润…",
    }


BASIC = {"600001.SH": {"name": "甲股", "industry": "化学制品"},
         "600002.SH": {"name": "乙股", "industry": "食品加工"},
         "600003.SH": {"name": "丙股", "industry": "机械设备"},
         "600004.SH": {"name": "丁股", "industry": "证券"}}

RECORDS = [
    _rec("600001.SH", "预增", 50.0, 80.0, 10000, 15000),
    _rec("600001.SH", "预增", 10.0, 20.0, 3000, 4000),   # 同股同日重复披露应各自保留？取最新——见 test 仅覆盖过滤
    _rec("600002.SH", "预增", -5.0, 12.0, 500, 900),      # p_max<30 → 不入 gainers
    _rec("600003.SH", "扭亏", 0, 0, 2000, 5000),
    _rec("600004.SH", "首亏", -200.0, -150.0, -8000, -6000),
]


def test_analyze_gainers_threshold_and_order():
    out = analyze(RECORDS, BASIC, min_gain=30.0)
    names = [(r["ts_code"], r["p_max"]) for r in out["gainers"]]
    assert names[0] == ("600001.SH", 80.0), names          # 降序且带名称行业
    assert out["gainers"][0]["name"] == "甲股"
    assert out["gainers"][0]["industry"] == "化学制品"
    assert len(out["gainers"]) == 1                        # 乙股 12% < 30 被过滤


def test_analyze_turnarounds_sorted():
    out = analyze(RECORDS, BASIC, min_gain=30.0)
    assert len(out["turnarounds"]) == 1
    assert out["turnarounds"][0]["ts_code"] == "600003.SH"
    assert out["turnarounds"][0]["np_max"] == 5000.0


def test_analyze_negatives_capped():
    out = analyze(RECORDS, BASIC, min_gain=30.0)
    assert len(out["negatives"]) == 1
    assert out["negatives"][0]["ts_code"] == "600004.SH"


def test_analyze_counts_and_window():
    out = analyze(RECORDS, BASIC, min_gain=30.0)
    assert out["counts"] == {"预增": 3, "扭亏": 1, "首亏": 1}
    assert out["window_days"] == 1


def test_analyze_empty():
    out = analyze([], {}, min_gain=30.0)
    assert out == {"counts": {}, "gainers": [], "turnarounds": [], "negatives": [],
                   "window_days": 0, "all": []}


def test_analyze_all_detail_rows():
    """窗口明细段（all）自包含：含未入清单的略增/略减条目，按 type 分组序排列。"""
    rows = [_rec("600001.SH", "略增", 5.0, 8.0), _rec("600002.SH", "预增", 50.0, 80.0)]
    out = analyze(rows, BASIC, min_gain=30.0)
    assert len(out["all"]) == 2
    assert [r["type"] for r in out["all"]] == ["预增", "略增"]  # 预增在前（分组序）
    assert out["all"][0]["name"] == "乙股"  # all[0] = 预增的乙股


def test_analyze_missing_numeric_fields():
    # 摘要/区间缺失不崩溃（fillna/NaN 路径）
    rows = [_rec("600001.SH", "预增"), _rec("600002.SH", "略增", 0, 0)]
    out = analyze(rows, BASIC, min_gain=30.0)
    assert out["gainers"] == []       # 增幅全缺 → 不算达标预增
    assert out["counts"]["预增"] == 1


def test_cell_escape_pipe_and_newline():
    """Markdown 单元格转义（review F8）：远端 summary 含 |/换行不拆列。"""
    assert _cell("预计:净利 1.2|1.8 亿元") == "预计:净利 1.2\\|1.8 亿元"
    assert _cell("第一行\n第二行\r回车") == "第一行 第二行 回车"
    assert _cell(None) == "None"   # str(None) 语义；norm() 保证非 None，此处仅兜底防错
    assert _cell("") == ""
