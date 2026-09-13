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
    assert recs[0]["rules_version"] == "0.2.0"


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


# ── R4 评审修复回归 ───────────────────────────────────────────────────────

def test_cli_forecast_is_actually_used(monkeypatch, tmp_path):
    """预告数据须真进 L3 增速子项——此前只在 fina 为空时取、且该分支必 continue 丢弃，
    使增速子项恒 0、降级档永不生效（R4 评审实跑复现）。"""
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(sources, "fetch_forecast",
                        lambda code: [{"p_change_max": 9999.0}])
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    rec = json.loads(snap.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["hits"], "应有命中"
    # g_implied（≈16%）< 9999 → 第二项应记 1
    assert all(h["gap_flags"][1] == 1 for h in rec["hits"]), "预告增速未被用于 L3"


def test_cli_rf_none_not_rendered_as_number(monkeypatch, tmp_path):
    """rf 不可得时不得渲染成「中国 10Y None%」（把 Python None 当收益率）。"""
    cli = load_scan_cli()
    _stub(monkeypatch, rf=None)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "None%" not in body
    assert "不可得" in body


def test_cli_snapshot_records_rule_params(monkeypatch, tmp_path):
    """per_industry / with_bj 也是规则参数——缺了快照无法归因（回填裁决锚点失效）。"""
    cli = load_scan_cli()
    _stub(monkeypatch)
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path, per_industry=5))
    rec = json.loads(snap.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["params"]["per_industry"] == 5
    assert rec["params"]["with_bj"] is False


def test_cli_l4_context_renders_short_label(monkeypatch, tmp_path):
    """L4 行不得把 market_form 的整个 dict 插进报告。"""
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(sources, "market_form_context", lambda: {
        "available": True, "market_form": "宽幅震荡轮动（缩量电风扇）",
        "note": "事后标注，不蕴含收益可预测性（Kirby 2023）"})
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "宽幅震荡轮动（缩量电风扇）" in body
    assert "{'form'" not in body and "'kirby_note'" not in body


def test_client_is_singleton(monkeypatch):
    """客户端须单例：每次新建会重置实例级限流器 → 全速突发（空返回根因）。"""
    import sources as src
    monkeypatch.setattr(src, "_CLIENT", None)
    assert src.client() is src.client()


def test_drilldown_command_symbol_is_executable():
    """下钻命令里的 symbol 必须是 invest.py 能接受的形态。

    首版发出的是 ts_code（`000612.SZ`）→ `exchange_code()` 的 `isdigit()` 失败 →
    整个 report 崩。仅断言「字符串里有 invest.py report」抓不到这个——
    必须把 symbol 抽出来喂给真正的校验函数。
    """
    cli = load_scan_cli()
    cmd = cli._drill_cmd("000612.SZ")
    sym = cmd.split()[-1]
    import sys as _sys
    _sys.path.insert(0, "skills/lib")
    from codes import exchange_code
    assert sym.isdigit(), f"下钻命令的 symbol 非纯数字：{sym!r}"
    assert exchange_code(sym)["tushare"] == "000612.SZ"   # 能被解析回来


def test_drilldown_command_rendered_in_report(monkeypatch, tmp_path):
    cli = load_scan_cli()
    _stub(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path))
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "invest.py report 600000" in body, "报告内的下钻命令须用纯数字形态"
    assert "invest.py report 600000.SH" not in body, "不得出现带后缀的形态"


# ── T11-5 港股池（--pool hk）────────────────────────────────────────────

_HK_UNIVERSE = [
    {"ts_code": "00700.HK", "symbol": "00700", "name": "腾讯控股", "market": "主板",
     "currency": "HKD", "industry": None},
    {"ts_code": "00005.HK", "symbol": "00005", "name": "汇丰控股", "market": "主板",
     "currency": "HKD", "industry": None},
] + [{"ts_code": f"{i:05d}.HK", "symbol": f"{i:05d}", "name": f"标的{i}",
      "market": "主板", "currency": "HKD", "industry": None} for i in range(100, 114)]


def _stub_hk(monkeypatch, *, pe=5.0, fin_roe=15.0, fin_profit=1.0e9):
    cli = load_scan_cli()
    import sources_hk
    monkeypatch.setattr(sources_hk, "reset_warnings", lambda: None)
    monkeypatch.setattr(sources_hk, "fetch_hk_universe", lambda: list(_HK_UNIVERSE))
    # ⚠️ PE 必须有**梯度**：全部同 PE 时 bisect_right 给出分位 1.0 → L1 一只也命中不了
    # （与 A 侧夹具同型的坑：过窄/过于均匀的夹具会让测试在空清单上空转）
    def _quotes(syms):
        return {s: {"price": 10.0, "pe_ttm": (pe if i < 2 else 30.0 + i),
                    "mcap_hkd_yi": 1000.0} for i, s in enumerate(syms)}

    monkeypatch.setattr(sources_hk, "fetch_hk_quote_batch", _quotes)
    monkeypatch.setattr(sources_hk, "fetch_hk_financials",
                        lambda sym: [{"report_date": "2026-06-30", "roe": fin_roe,
                                      "net_profit": fin_profit}])
    monkeypatch.setattr(sources_hk, "warnings", [])
    monkeypatch.setattr(sources, "rf_10y_usd", lambda: (4.95, "FRED.DGS10"), raising=False)
    monkeypatch.setattr(sources, "latest_trade_date", lambda: "20260911")
    # ⚠️ 必须 stub has_token —— HK 夹具漏了它（A 股夹具 stub 了）会让测试**依赖环境**：
    # `sources.has_token()` 走 `from lib.env import get_config`，而跨目录跑测时
    # `lib` 的解析可能被其它技能影响 → 返回 False → CLI 退 4 → 用例以「无 token」失败
    monkeypatch.setattr(sources, "has_token", lambda: True)
    return cli


def test_hk_pool_report_has_lens_availability_table(monkeypatch, tmp_path):
    """HK-4 验收核心：**每透镜标可用性、空透镜不冒充**。"""
    cli = _stub_hk(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path) + ["--pool", "hk"]) == 0
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "透镜可用性" in body and "空透镜不冒充" in body
    for lens in ("L1 pe_grank", "L1 ind_rk", "L3 利差", "L3 预告增速", "L2 自身历史分位"):
        assert lens in body, f"可用性表缺 {lens}"
    assert "不可得" in body, "不可得透镜须显式标注"


def test_hk_pool_declares_universe_deviation(monkeypatch, tmp_path):
    """口径偏离（港股通/恒指 → 全部上市港股）须在报告头显式说明。"""
    cli = _stub_hk(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "口径偏离声明" in body
    assert "超集" in body


def test_hk_pool_caliber_notes_present(monkeypatch, tmp_path):
    """港股口径注记：无扣非 / ROE 非年化 / 无季报无预告。"""
    cli = _stub_hk(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    for token in ("无扣非概念", "期间 ROE", "年报+中报"):
        assert token in body, f"缺口径注记：{token}"


def test_hk_drilldown_points_to_hk_cli(monkeypatch, tmp_path):
    cli = _stub_hk(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "invest-hk-stock/scripts/hk.py report 00700" in body
    assert "invest-a-stock/scripts/invest.py report 00700" not in body


def test_hk_industry_gate_skipped_not_silent(monkeypatch, tmp_path):
    """港股无行业字段 → 行业条件整体跳过，且**在报告中明示**（不静默）。"""
    cli = _stub_hk(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    body = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "行业条件已整体跳过" in body or "行业条件：**港股无行业字段，该条件已整体跳过**" in body


def test_hk_pool_snapshot_records_pool_kind(monkeypatch, tmp_path):
    cli = _stub_hk(monkeypatch)
    snap = tmp_path / "2026.jsonl"
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: snap)
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    rec = json.loads(snap.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["params"]["pool"] == "hk"
    assert rec["pool"]["market"].startswith("港股")


def test_hk_and_a_reports_do_not_collide(monkeypatch, tmp_path):
    """两个池同日落盘**不得互相覆盖**（真机实测：港股池曾冲掉当日 A 股报告）。"""
    cli = _stub_hk(monkeypatch)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    hk_files = {p.name for p in tmp_path.glob("*.md")}
    assert any("-hk" in n for n in hk_files), f"港股报告名须含池标识：{hk_files}"


def test_hk_report_renders_pe_anomaly(monkeypatch, tmp_path):
    """真机实测：港股池前三名全是 PE<1 的困境房企 —— 异常必须**显式呈现**，
    不得静默让它们以「低估」面貌排在榜首。"""
    cli = _stub_hk(monkeypatch, pe=0.01)
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    cli.main(_argv(tmp_path) + ["--pool", "hk"])
    body = list(tmp_path.glob("*-hk.md"))[0].read_text(encoding="utf-8")
    assert "极低 PE" in body, "PE<1 的标的须带异常标注"
    assert "一次性损益" in body
    assert "不得直接读作「极度低估」" in body


# ── 轮末评审修复的回归（2026-09-13）──────────────────────────────────────────

class _FakeHkCalendar:
    """假港股日历模块（替 `sources_hk._load_hk_module('hk_calendar')` 的产物）。"""

    def __init__(self, date, *, degraded=False, exc=None):
        self._date, self._degraded, self._exc = date, degraded, exc

    def hk_session_date(self):
        if self._exc:
            raise self._exc
        return self._date

    def hk_calendar_degraded(self):
        return self._degraded


def _widen_hk(monkeypatch, sources_hk, n_low, n=45):
    """放宽港股池夹具：`n_low` 只低 PE + `n - n_low` 只高 PE。

    ⚠️ 必须同时拓宽 universe——L1 门槛是**全池分位 ≤15%**，`_HK_UNIVERSE` 只有 16 只，
    低 PE 达 3 只时 3/16 = 18.8% 即已不达标。
    `n=45` 使低 PE 者分位 6/45 = 13.3% 命中、紧邻的高 PE 者 7/45 = 15.6% 落榜
    → L1 **恰好 6 只**（夹具边界刻意留出余量，不贴 15.0% 走钢丝）。
    """
    uni = [{"ts_code": f"{i:05d}.HK", "symbol": f"{i:05d}", "name": f"标的{i}",
            "market": "主板", "currency": "HKD", "industry": None} for i in range(n)]
    monkeypatch.setattr(sources_hk, "fetch_hk_universe", lambda: list(uni))
    monkeypatch.setattr(sources_hk, "fetch_hk_quote_batch", lambda syms: {
        s: {"price": 10.0, "pe_ttm": (5.0 if i < n_low else 30.0 + i),
            "mcap_hkd_yi": 1000.0} for i, s in enumerate(syms)})


def _run_hk(cli, monkeypatch, tmp_path, extra=()):
    monkeypatch.setattr(cli.snapshot, "snapshot_path", lambda year=None: tmp_path / "2026.jsonl")
    monkeypatch.setattr(cli.snapshot, "discovery_dir", lambda: tmp_path)
    assert cli.main(_argv(tmp_path) + ["--pool", "hk", *extra]) == 0
    body = list(tmp_path.glob("*-hk.md"))[0].read_text(encoding="utf-8")
    rec = json.loads((tmp_path / "2026.jsonl").read_text(
        encoding="utf-8").strip().splitlines()[0])
    return body, rec


def test_hk_calendar_degraded_is_annotated(monkeypatch, tmp_path):
    """日历**内部降级**（不抛异常、退回周末近似）必须显式标注。

    `hk_calendar.hk_session_date` 降级时**不抛异常**，只置 `hk_calendar_degraded()`；
    只 catch 异常的实现会静默接受一个周末近似日当数据日，报告仍称数据日来自港股日历。
    """
    cli = _stub_hk(monkeypatch)
    import sources_hk
    monkeypatch.setattr(sources_hk, "_load_hk_module",
                        lambda name: _FakeHkCalendar("2026-09-12", degraded=True))
    body, rec = _run_hk(cli, monkeypatch, tmp_path)
    assert rec["trade_date"] == "20260912"
    assert any("降级" in w for w in rec["warnings"]), rec["warnings"]
    assert "周末近似" in body, "报告须披露数据日来自降级近似"


def test_hk_calendar_failure_warning_reaches_report(monkeypatch, tmp_path):
    """日历模块不可得 → 退回 A 股口径**且警告必须进报告/快照**。

    ⚠️ 原实现把 `_hk_trade_date()` 放在 `warnings` 键**之后**求值（dict 字面量按序），
    它追加的警告永远不会进聚合结果——警告「写了但走不到」，正是静默降级。
    """
    cli = _stub_hk(monkeypatch)
    import sources_hk

    def _boom(name):
        raise ImportError("invest-hk-stock lib/hk_calendar.py 缺失")

    monkeypatch.setattr(sources_hk, "_load_hk_module", _boom)
    body, rec = _run_hk(cli, monkeypatch, tmp_path)
    assert rec["trade_date"] == "20260911", "须退回 A 股口径桩值"
    assert any("港股日历" in w for w in rec["warnings"]), rec["warnings"]
    assert "港股日历" in body, "降级须出现在报告的降级清单里"


def test_hk_shortlist_not_capped_by_single_industry_bucket(monkeypatch, tmp_path):
    """港股全部行同属一个「无行业」桶 → 不得按 `per_industry=3` 封顶。

    实测缺陷：`--pool hk --top 15` 恒只出 3 只（第 4 名起静默丢弃）。
    """
    cli = _stub_hk(monkeypatch)
    import sources_hk
    _widen_hk(monkeypatch, sources_hk, n_low=6)
    body, rec = _run_hk(cli, monkeypatch, tmp_path, extra=["--top", "15"])
    assert len(rec["hits"]) == 6, f"6 只通过闸门却只出 {len(rec['hits'])} 只"
    assert "短清单（6 只" in body


def test_hk_snapshot_params_record_actual_industry_cap(monkeypatch, tmp_path):
    """快照 `params` 是回填裁决的锚点——须记录**实际行为**（不做行业分散），
    而非 CLI 默认值 3（原实现记录 3、行为却是 None）。"""
    cli = _stub_hk(monkeypatch)
    import sources_hk
    _widen_hk(monkeypatch, sources_hk, n_low=6)
    _, rec = _run_hk(cli, monkeypatch, tmp_path, extra=["--top", "15"])
    assert rec["params"]["per_industry"] is None


def test_hk_pool_reports_unassessable_and_rejected_counts(monkeypatch, tmp_path):
    """「无标的通过」须能区分「不可评估 / 被闸门剔除」——计数须入快照**并渲染**。"""
    cli = _stub_hk(monkeypatch)
    import sources_hk
    _widen_hk(monkeypatch, sources_hk, n_low=6)

    def _fin(sym):
        if sym == "00000":
            return []                                   # 财务不可得 → 不可评估
        if sym == "00001":
            return [{"report_date": "2026-06-30", "roe": 1.0,   # 年化 2% < 8%
                     "net_profit": 1.0e9}]                  # → 被质量闸门剔除
        return [{"report_date": "2026-06-30", "roe": 15.0, "net_profit": 1.0e9}]

    monkeypatch.setattr(sources_hk, "fetch_hk_financials", _fin)
    body, rec = _run_hk(cli, monkeypatch, tmp_path, extra=["--top", "15"])
    assert rec["pool"]["n_unassessable"] == 1, rec["pool"]
    assert rec["pool"]["n_quality_rejected"] == 1, rec["pool"]
    assert len(rec["hits"]) == 4
    assert "不可评估" in body and "质量闸门" in body, "计数须渲染，否则是死数据"


def test_hk_no_pass_warning_distinguishes_data_from_market(monkeypatch, tmp_path):
    """L1 命中但财务全不可得 → 「无标的通过」是**数据不可得**，不得读作市场事实。"""
    cli = _stub_hk(monkeypatch)
    import sources_hk
    _widen_hk(monkeypatch, sources_hk, n_low=6)
    monkeypatch.setattr(sources_hk, "fetch_hk_financials", lambda sym: [])
    body, rec = _run_hk(cli, monkeypatch, tmp_path, extra=["--top", "15"])
    assert rec["hits"] == []
    assert any("不可评估" in w and "市场" in w for w in rec["warnings"]), rec["warnings"]
    assert "不可得" in body


def test_hk_report_footer_roe_caliber_matches_lens_table(monkeypatch, tmp_path):
    """页脚与透镜表口径说明不得互相矛盾（年化已实际生效）。"""
    cli = _stub_hk(monkeypatch)
    body, _ = _run_hk(cli, monkeypatch, tmp_path)
    assert "中报非年化" not in body, "页脚仍称「非年化」，与透镜表「已年化」矛盾"
    assert "年化" in body
