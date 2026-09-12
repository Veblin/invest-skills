"""discover_scan.py CLI 契约 — 离线单测（设计 §3 退出码 + §12 验收 + §10 合规）。

退出码（设计 §3）：0 正常 / 3 数据不可得（**不产空清单**）/ 4 缺 token 或权限 /
2 参数错或功能未实现（fillback 桩）。
"""
from __future__ import annotations

import json

import pytest
from _scan_cli import load_scan_cli

import sources

# 夹具宽度受两个门槛共同约束，两处都容易踩空：
# ① 全 A 分位 ≤15% —— N 只中最低者分位 1/N，故 N ≥ 7 才可能达标；
# ② 行业内排名 rk/n ≤25% —— **行业只有 1-3 只时 rk=1 也过不了**（1/3=33%）。
#    两门槛都要满足才算命中，故每只目标标的需要 ≥4 只同行业同伴。
def _mk(ts_code, name, industry, pe, *, market="主板", roe=None, dv=0.5):
    basic = {"ts_code": ts_code, "name": name, "industry": industry, "market": market}
    daily = {"ts_code": ts_code, "pe_ttm": pe, "dv_ratio": dv,
             "close": 10.0, "total_mv": 1.6e6}
    return basic, daily, roe


_TARGETS = []      # (ts_code, industry, pe, roe_yearly) —— 预期通过质量闸门者
for _ind, _base, _pe, _roe in (("银行", 600000, 5.0, 15.0), ("白酒", 1, 6.0, 12.0)):
    _TARGETS.append((f"{_base:06d}.{'SH' if _ind == '银行' else 'SZ'}", _ind, _pe, _roe))

_BASIC, _DAILY, _FINA = [], [], {}
for _code, _ind, _pe, _roe in _TARGETS:
    b, d, _ = _mk(_code, f"目标{_ind}", _ind, _pe)
    _BASIC.append(b), _DAILY.append(d)
    _FINA[_code] = [{"end_date": "20260630", "ann_date": "20260815",
                     "roe_yearly": _roe, "profit_dedt": 1.0e9}]
# 每个目标行业补 3 只高 PE 同伴（使 n=4，rk=1 → 1/4 = 25%，含边界恰好达标）
# ⚠️ 代码用显式计数器生成——`hash()` 在同进程外随机化，会让夹具随运行变化
_peer_n = 0
for _ind in ("银行", "白酒"):
    for _i in range(3):
        _peer_n += 1
        b, d, _ = _mk(f"6011{_peer_n:02d}.SH", f"同伴{_ind}{_i}", _ind, 50.0 + _i)
        _BASIC.append(b), _DAILY.append(d)
# 抬高全市场 N（8 只机械股），使最低分位 = 1/16 ≈ 6.3%、次低 2/16 = 12.5%
for _i in range(8):
    b, d, _ = _mk(f"6001{_i:02d}.SH", f"机械{_i}", "机械", 30.0 + _i)
    _BASIC.append(b), _DAILY.append(d)
# 排除项：ST（创业板）+ 北交所
_BASIC.append({"ts_code": "300001.SZ", "name": "*ST 某某", "industry": "白酒", "market": "创业板"})
_DAILY.append({"ts_code": "300001.SZ", "pe_ttm": 4.0, "dv_ratio": 0.0,
               "close": 9.0, "total_mv": 3.0e5})
_BASIC.append({"ts_code": "830001.BJ", "name": "北交所某某", "industry": "机械", "market": "北交所"})
_DAILY.append({"ts_code": "830001.BJ", "pe_ttm": 3.0, "dv_ratio": 0.0,
               "close": 9.0, "total_mv": 3.0e5})


def _stub(monkeypatch, *, basic=None, daily=None, fina=None, rf=1.73, token=True,
          daily_boom=False):
    """注入 sources 叶子取数（D13：patch 打在同一模块对象上）。"""
    if token:
        monkeypatch.setenv("TUSHARE_TOKEN", "test-token-not-used")
    else:
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(sources, "has_token", lambda: token)
    monkeypatch.setattr(sources, "latest_trade_date", lambda: "20260911")
    monkeypatch.setattr(sources, "fetch_stock_basic", lambda: list(basic or _BASIC))
    if daily_boom:
        def _boom(*a, **kw):
            raise RuntimeError("全市场源不可得")
        monkeypatch.setattr(sources, "fetch_daily_basic", _boom)
    else:
        monkeypatch.setattr(sources, "fetch_daily_basic", lambda d: list(daily or _DAILY))
    monkeypatch.setattr(sources, "fetch_fina_indicator",
                        lambda code: list((fina or _FINA).get(code, [])))
    monkeypatch.setattr(sources, "fetch_forecast", lambda code: [])
    monkeypatch.setattr(sources, "rf_10y_pct", lambda: (rf, "test"))


def _argv(tmp_path, **over):
    a = ["--out-dir", str(tmp_path)]
    if "top" in over:
        a += ["--top", str(over["top"])]
    if over.get("with_bj"):
        a.append("--with-bj")
    if over.get("no_out"):
        a.append("--no-out")
    if "per_industry" in over:
        a += ["--per-industry", str(over["per_industry"])]
    return a


def test_cli_runs_and_writes_snapshot(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch)
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path)) == 0
    recs = [json.loads(ln) for ln in snap.read_text(encoding="utf-8").strip().splitlines()]
    assert len(recs) == 1
    assert recs[0]["rules_version"] == "0.1.0"


def test_cli_shortlist_within_cap_and_has_reason_fields(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path, top=1))
    mds = list(tmp_path.glob("*.md"))
    assert mds, "默认须落 md"
    body = mds[0].read_text(encoding="utf-8")
    assert "研究信号，非决策" in body, "首行须有定位声明（设计 §1/§10）"
    assert "仅供研究" in body or "不构成投资建议" in body
    for token in ("规则版本", "数据日", "池口径"):
        assert token in body, f"报告头缺 {token}（设计 §7）"


def test_cli_md_includes_drilldown_command(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "invest.py report" in body, "每只须附下钻命令（验收 §12-2）"


def test_cli_no_out_skips_md_but_still_writes_snapshot(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch)
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path, no_out=True)) == 0
    assert not list(tmp_path.glob("*.md")), "--no-out 不应落 md"
    assert snap.exists(), "快照必须照写（回填依赖它）"


def test_cli_market_failure_exits_3_without_empty_shortlist(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch, daily_boom=True)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path)) == 3
    assert not list(tmp_path.glob("*.md")), "数据不可得时**不得**产出空清单"


def test_cli_missing_token_exits_4(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch, token=False)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    assert cli.main(_argv(tmp_path)) == 4


def test_cli_top_is_capped(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch)
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path, top=1))
    rec = json.loads(snap.read_text(encoding="utf-8").strip().splitlines()[0])
    assert len(rec["hits"]) <= 1


def test_cli_rf_unavailable_degrades_l3_with_warning(monkeypatch, tmp_path):
    """rf 不可得 → L3 利差透镜降级，**须明示**（设计 §5）。"""
    cli = load_scan_cli()
    _stub(monkeypatch, rf=None)
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path)) == 0
    rec = json.loads(snap.read_text(encoding="utf-8").strip().splitlines()[0])
    assert any("rf" in w.lower() or "10Y" in w for w in rec["warnings"]), \
        "利差降级须记 warning"


def test_cli_quality_degradation_is_recorded(monkeypatch, tmp_path):
    """无一候选通过质量闸门时须记 warning，**不得**静默产出空清单冒充「今日无机会」。"""
    cli = load_scan_cli()
    _stub(monkeypatch, fina={"600000.SH": [{"end_date": "20260630", "ann_date": "20260815",
                                            "roe_yearly": 1.0, "profit_dedt": 1.0}]})
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path)) == 0
    rec = json.loads(snap.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["warnings"], "过滤后无候选须可归因"


def test_fillback_stub_exits_2(capsys):
    cli = load_scan_cli("discover_fillback.py")
    assert cli.main(["--horizon", "90"]) == 2
    out = capsys.readouterr().out
    assert "未实现" in out


def _strip_disclaimer(body: str) -> str:
    """剔除固定的否定式声明行后再做禁用词扫描。

    免责句（「不含买卖/仓位建议」「不构成投资建议」）本身就是**否定式**合规表述，
    把它们算作违规会让检查无法通过；真正要拦的是**陈述式**建议措辞。
    """
    keep = [ln for ln in body.splitlines()
            if "不构成投资建议" not in ln and "不含买卖" not in ln and "不构成任何交易建议" not in ln]
    return "\n".join(keep)


@pytest.mark.parametrize("banned", ["建议买入", "建议卖出", "目标价", "应当加仓", "仓位建议"])
def test_cli_output_has_no_advice_language(monkeypatch, tmp_path, banned):
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert banned not in _strip_disclaimer(body), f"LAW 6：清单不得含「{banned}」"
    assert "不构成投资建议" in body, "免责声明须在位"


def test_cli_snapshot_not_written_under_repo(monkeypatch, tmp_path):
    """个股产出红线：快照须落私有 store，不在仓库内。"""
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    import snapshot as snap_mod
    assert "code/skills" not in str(snap_mod.discovery_dir())
