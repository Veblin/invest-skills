"""Unit tests for ETF holdings fetch/parse/query (v0.2.5 R12, no network).

D13: mocks 一律打在定义模块（etf_data）命名空间。
"""

from __future__ import annotations

import pytest

from etf_data import (
    _decode_em_page,
    _holdings_missing,
    _parse_holdings_blocks,
    fetch_etf_holdings,
    query_etf_holdings,
    HOLDINGS_CLUSTER_MAP,
)

# 实测 2026-06-30 前十大权重（天天基金 jjcc 页）
_W10 = [
    ("688002", "睿创微纳", "9.07", "1,036.01", "160,260.70"),
    ("600879", "航天电子", "8.44", "7,004.94", "149,135.26"),
    ("600118", "中国卫星", "7.60", "1,635.54", "134,195.82"),
    ("688385", "复旦微电", "3.81", "943.49", "67,261.61"),
    ("603678", "火炬电子", "3.80", "809.09", "67,154.47"),
    ("300762", "上海瀚讯", "3.79", "1,466.12", "66,928.53"),
    ("688387", "信科移动", "3.62", "3,223.07", "63,945.72"),
    ("300136", "信维通信", "3.12", "462.95", "55,207.15"),
    ("688375", "国博电子", "3.00", "507.76", "52,958.95"),
    ("688102", "斯瑞新材", "2.90", "1,085.32", "51,205.39"),
]

_Q2_ROW = (
    "<tr><td>{i}</td><td><a href='//quote.eastmoney.com/unify/r/1.{code}'>{code}</a></td>"
    "<td class='tol'><a href='//quote.eastmoney.com/unify/r/1.{code}'>{name}</a></td>"
    "<td class='tor'><span data-id='dq{code}'></span></td>"
    "<td class='tor'><span data-id='zd{code}'></span></td>"
    "<td class='xglj'><a href='ccbdxq_159206_{code}.html' class='red'>变动详情</a></td>"
    "<td class='tor'>{pct}%</td><td class='tor'>{shares}</td><td class='tor'>{amount}</td></tr>"
)

# Q1 旧列结构：7 个 td（无最新价/涨跌幅 span 占位）→ 验证负索引抗列漂移
_Q1_ROW = (
    "<tr><td>{i}</td><td><a href='//quote.eastmoney.com/unify/r/1.{code}'>{code}</a></td>"
    "<td class='tol'><a href='//quote.eastmoney.com/unify/r/1.{code}'>{name}</a></td>"
    "<td class='tor'>{pct}%</td><td class='tor'>{shares}</td><td class='tor'>{amount}</td>"
    "<td class='xglj'><a href='ccbdxq_159206_{code}.html'>变动详情</a></td></tr>"
)


def _q2_block() -> str:
    rows = "".join(
        _Q2_ROW.format(i=i + 1, code=c, name=n, pct=p, shares=s, amount=a)
        for i, (c, n, p, s, a) in enumerate(_W10)
    )
    return (
        "<h4 class='t'><label class='left'><a title='卫星ETF永赢' "
        "href='http://fund.eastmoney.com/159206.html'>卫星ETF永赢</a>"
        "&nbsp;&nbsp;2026年2季度股票投资明细</label>"
        "<label class='right lab2 xq505'>&nbsp;&nbsp;来源：天天基金&nbsp;&nbsp;"
        "截止至：<font class='px12'>2026-06-30</font></label></h4>"
        "<div class='space0'></div><table class='w782 comm tzxq'>"
        "<thead><tr><th class='first'>序号</th><th>股票代码</th><th>股票名称</th>"
        "<th>最新价</th><th>涨跌幅</th><th class='xglj'>相关资讯</th>"
        "<th>占净值<br />比例</th><th class='cgs'>持股数<br />（万股）</th>"
        "<th class='last ccs'>持仓市值<br />（万元）</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _q1_block() -> str:
    rows = "".join(
        _Q1_ROW.format(i=i + 1, code=c, name=n, pct=p, shares=s, amount=a)
        for i, (c, n, p, s, a) in enumerate(_W10[:2])
    )
    return (
        "<h4 class='t'><label class='left'>卫星ETF永赢 2026年1季度股票投资明细</label>"
        "<label class='right lab2 xq505'>&nbsp;&nbsp;来源：天天基金&nbsp;&nbsp;"
        "截止至：<font class='px12'>2026-03-31</font></label></h4>"
        "<div class='space0'></div><table><thead><tr><th>序号</th><th>股票代码</th>"
        "<th>股票名称</th><th>占净值比例</th><th>持股数（万股）</th>"
        "<th>持仓市值（万元）</th><th>相关资讯</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _apidata(*blocks: str) -> str:
    return 'var apidata={content:"' + blocks[0] + '",arryear:[2026,2025],curyear:2026};'


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------


def test_parse_picks_latest_report_date_ignoring_order():
    """乱序（Q1 在前 Q2 在后）→ 取「截止至」日期最大的块。"""
    content = _apidata(_q1_block() + _q2_block())
    blocks = _parse_holdings_blocks(content)
    assert len(blocks) == 2
    latest = max(blocks, key=lambda b: b["report_date"])
    assert latest["report_date"] == "2026-06-30"
    assert latest["quarter"] == "2026年2季度"
    assert len(latest["rows"]) == 10
    assert [r["pct"] for r in latest["rows"]] == pytest.approx(
        [9.07, 8.44, 7.60, 3.81, 3.80, 3.79, 3.62, 3.12, 3.00, 2.90]
    )


def test_parse_handles_variable_td_counts():
    """9-td（Q2）与 7-td（Q1）身体行都能解析 — 负索引抗列漂移。"""
    blocks = _parse_holdings_blocks(_apidata(_q1_block()))
    row = blocks[0]["rows"][0]
    assert row["code"] == "688002"
    assert row["name"] == "睿创微纳"
    assert row["pct"] == 9.07
    assert row["shares"] == 1036.01
    assert row["amount"] == 160260.70


def test_parse_skips_rows_with_fewer_than_6_tds():
    content = (
        "<h4>2026年2季度截止至：<font>2026-06-30</font></h4><table>"
        "<thead><tr><th>序号</th><th>股票代码</th><th>股票名称</th>"
        "<th>占净值比例</th><th>持股数（万股）</th><th>持仓市值（万元）</th></tr></thead>"
        "<tbody>"
        '<tr><td>1</td><td>a</td></tr>'
        '<tr><td>1</td><td><a>600118</a></td><td>中国卫星</td><td class="tor">7.60%</td>'
        '<td class="tor">1,635.54</td><td class="tor">134,195.82</td></tr>'
        "</tbody></table>"
    )
    blocks = _parse_holdings_blocks(content)
    assert len(blocks) == 1
    assert len(blocks[0]["rows"]) == 1  # 2-td 行被跳过


def test_parse_skips_block_without_report_date():
    content = "<h4>无日期块</h4><table><tbody><tr><td>1</td></tr></tbody></table>"
    assert _parse_holdings_blocks(content) == []


def test_parse_skips_block_when_header_unparseable():
    """表头列名不可解析 → 跳过该块（fail loud，不猜测列位置）。"""
    content = (
        "<h4>2026年2季度 截止至：<font>2026-06-30</font></h4><table>"
        "<thead><tr><th>未知列A</th><th>未知列B</th><th>未知列C</th></tr></thead>"
        "<tbody><tr><td>1</td><td>2</td><td>3</td></tr></tbody></table>"
    )
    assert _parse_holdings_blocks(content) == []


# ---------------------------------------------------------------------------
# 解码层
# ---------------------------------------------------------------------------


def test_decode_utf8_preferred():
    class FakeResp:
        content = "卫星ETF 9.07%".encode("utf-8")

    assert _decode_em_page(FakeResp()) == "卫星ETF 9.07%"


def test_decode_falls_back_to_gbk():
    class FakeResp:
        content = "卫星ETF 9.07%".encode("gbk")

    assert _decode_em_page(FakeResp()) == "卫星ETF 9.07%"


# ---------------------------------------------------------------------------
# fetch 层（D13: mock 打在 etf_data 命名空间）
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _FakeSession:
    headers: dict = {}

    def __init__(self, resp: bytes):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, timeout=None):
        return _FakeResp(self._resp)


def _patch_network(monkeypatch, resp: bytes):
    monkeypatch.setattr(
        "etf_data.no_proxy_session", lambda: _FakeSession(resp)
    )
    monkeypatch.setattr("etf_data.throttle_eastmoney", lambda: None)


def test_fetch_ok_parses_ten_rows(monkeypatch):
    _patch_network(monkeypatch, _apidata(_q2_block()).encode("utf-8"))
    out = fetch_etf_holdings("159206")
    assert out["status"] == "ok"
    assert out["report_date"] == "2026-06-30"
    assert len(out["rows"]) == 10


def test_fetch_missing_when_no_apidata(monkeypatch):
    _patch_network(monkeypatch, b"var apidata={};")
    out = fetch_etf_holdings("159206")
    assert out["status"] == "missing"
    assert out["rows"] == []
    assert "content" in (out["error"] or "")


def test_fetch_missing_on_network_error(monkeypatch):
    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr("etf_data.no_proxy_session", boom)
    monkeypatch.setattr("etf_data.throttle_eastmoney", lambda: None)
    out = fetch_etf_holdings("159206")
    assert out["status"] == "missing"
    assert "connection refused" in (out["error"] or "")


def test_holdings_missing_envelope():
    out = _holdings_missing("boom")
    assert out["status"] == "missing"
    assert out["rows"] == []
    assert out["error"] == "boom"


# ---------------------------------------------------------------------------
# query 层（集中度引擎计算，P0）
# ---------------------------------------------------------------------------


def _ok_env() -> dict:
    rows = [
        {"code": c, "name": n, "pct": float(p), "shares": float(s.replace(",", "")),
         "amount": float(a.replace(",", ""))}
        for c, n, p, s, a in _W10
    ]
    return {"status": "ok", "report_date": "2026-06-30", "quarter": "2026年2季度",
            "rows": rows, "error": None}


def test_query_concentration_engine_calculated(monkeypatch):
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: _ok_env())
    out = query_etf_holdings("159206")
    assert out["status"] == "ok"
    assert out["top1_pct"] == 9.07
    assert out["top5_sum_pct"] == pytest.approx(32.72)
    assert out["top10_sum_pct"] == pytest.approx(49.15)
    assert "未归类" in out["note"]


def test_query_missing_envelope(monkeypatch):
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: None)
    out = query_etf_holdings("159206")
    assert out["status"] == "missing"
    assert out["top1_pct"] is None
    assert out["top5_sum_pct"] is None
    assert out["rows"] == []
    assert out["clusters"] == []


# ---------------------------------------------------------------------------
# query 层（R12 holdings.clusters 聚类合计，引擎聚合 AI 不心算）
# ---------------------------------------------------------------------------


# 515050 通信ETF华夏 前十大（2026-06-30，全部在 HOLDINGS_CLUSTER_MAP 内）
_W10_515050 = [
    ("300502", "新易盛", "9.91"),
    ("603986", "兆易创新", "9.91"),
    ("300308", "中际旭创", "9.29"),
    ("002475", "立讯精密", "6.55"),
    ("002384", "东山精密", "6.13"),
    ("601138", "工业富联", "5.22"),
    ("600183", "生益科技", "4.60"),
    ("600487", "亨通光电", "3.93"),
    ("002463", "沪电股份", "3.75"),
    ("300394", "天孚通信", "3.61"),
]


def _ok_env_515050() -> dict:
    rows = [
        {"code": c, "name": n, "pct": float(p), "shares": 0.0, "amount": 0.0}
        for c, n, p in _W10_515050
    ]
    return {"status": "ok", "report_date": "2026-06-30", "quarter": "2026年2季度",
            "rows": rows, "error": None}


def test_query_clusters_all_mapped(monkeypatch):
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: _ok_env_515050())
    out = query_etf_holdings("515050")
    clusters = out["clusters"]
    assert len(clusters) == 4  # 全部映射，无「未归类」
    by_name = {c["cluster"]: c for c in clusters}
    assert by_name["光模块/光器件"]["sum_pct"] == pytest.approx(22.81)
    assert by_name["PCB/覆铜板"]["sum_pct"] == pytest.approx(14.48)
    assert by_name["终端/服务器代工"]["sum_pct"] == pytest.approx(11.77)
    assert by_name["存储/光缆"]["sum_pct"] == pytest.approx(13.84)
    # 排序：未归类最后，其余按 sum_pct 降序
    sums = [c["sum_pct"] for c in clusters]
    assert sums == sorted(sums, reverse=True)
    # members 内 pct 降序
    members = by_name["光模块/光器件"]["members"]
    m_pcts = [m["pct"] for m in members]
    assert m_pcts == sorted(m_pcts, reverse=True)
    assert [m["code"] for m in members] == ["300502", "300308", "300394"]


def test_query_clusters_sum_cross_check(monkeypatch):
    """四组合计与 top10_sum_pct 交叉验证（62.90）。"""
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: _ok_env_515050())
    out = query_etf_holdings("515050")
    assert sum(c["sum_pct"] for c in out["clusters"]) == pytest.approx(
        out["top10_sum_pct"]
    )
    assert out["top10_sum_pct"] == pytest.approx(62.90)


def test_query_clusters_unmapped_to_uncategorized(monkeypatch):
    """未映射股票归入「未归类」单组（159206 前十全未映射）。"""
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: _ok_env())
    out = query_etf_holdings("159206")
    clusters = out["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["cluster"] == "未归类"
    assert clusters[0]["sum_pct"] == pytest.approx(49.15)
    assert len(clusters[0]["members"]) == 10


def test_query_clusters_empty_rows_missing(monkeypatch):
    """空 rows 走 missing 分支（ok 判定含 rows truthy），clusters 为空。"""
    env = {"status": "ok", "report_date": None, "quarter": None,
           "rows": [], "error": None}
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: env)
    out = query_etf_holdings("515050")
    assert out["status"] == "missing"
    assert out["clusters"] == []


def test_query_clusters_pct_none_skipped(monkeypatch):
    env = {"status": "ok", "report_date": None, "quarter": None,
           "rows": [{"code": "300502", "name": "新易盛", "pct": None}],
           "error": None}
    monkeypatch.setattr("etf_data._bridge_get", lambda *a: env)
    out = query_etf_holdings("515050")
    assert out["clusters"] == []  # pct None 不计入任何聚类（与 topN 口径一致）


def test_holdings_cluster_map_integrity():
    """映射表完整性：键 6 位数字、值非空、无重复键。"""
    assert HOLDINGS_CLUSTER_MAP
    for code, label in HOLDINGS_CLUSTER_MAP.items():
        assert len(code) == 6 and code.isdigit()
        assert isinstance(label, str) and label.strip()
    assert len(HOLDINGS_CLUSTER_MAP) == len(set(HOLDINGS_CLUSTER_MAP))
