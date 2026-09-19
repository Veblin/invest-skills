"""A/H 比价透镜测试（T11-4 / HK-2）——纯离线：注入汇率与价格，不联网。

三条纪律（requirements §3.2 + P0）：
1. 溢价率**只能**由 Python 计算，输出须带 `[来源: Python calc: formula]`
2. 币种/汇率时点/复权口径**显式**（东财 CURRENCY 字段对 A+H 公司不可靠）
3. 不可得 → 三态标注，不得出「0% 溢价」这类伪造的中性值

汇率口径陷阱（2026-09-12 实测）：中行牌价按**每 100 港元**计价
（`央行中间价` 86.384 → 0.86384 CNY/HKD），漏除 100 会把溢价率放大近百倍。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

import hk_ah  # noqa: E402


# ── 溢价率（纯函数，P0）───────────────────────────────────────────────────

def test_premium_positive_and_zero_and_discount():
    # A 价 12 CNY，H 价 10 HKD，汇率 0.8 → H 折合 8 CNY → 溢价 +50%
    assert hk_ah.premium_pct(12.0, 10.0, 0.8) == 50.0
    assert hk_ah.premium_pct(8.0, 10.0, 0.8) == 0.0
    assert hk_ah.premium_pct(6.0, 10.0, 0.8) == -25.0


def test_premium_invalid_inputs_return_none():
    """缺失/非正价格或汇率 → None（调用方出三态，而非伪造 0%）。

    注：数值字符串（如 `"12"`）按仓库 `safe_float` 惯例**接受**（宽松解析），
    不在此列——只有缺值与非正数才是不可得。
    """
    for a, h, fx in [(None, 10.0, 0.8), (12.0, None, 0.8), (12.0, 10.0, None),
                     (12.0, 0.0, 0.8), (12.0, 10.0, 0.0), (12.0, 10.0, -0.8),
                     (12.0, 10.0, "abc")]:
        assert hk_ah.premium_pct(a, h, fx) is None, f"({a}, {h}, {fx}) 应判不可得"
    assert hk_ah.premium_pct("12", 10.0, 0.8) == 50.0, "数值字符串按 safe_float 惯例接受"


# ── 汇率取数：首选与降级 ─────────────────────────────────────────────────

def _days_ago_iso(n: int) -> str:
    """北京今天往前 n 天的 ISO 日期（注入日期一律**相对今天**，防测试自身成时间炸弹）。"""
    import datetime as _dt

    from dates import shanghai_today

    t = shanghai_today()
    d = _dt.date(int(t[:4]), int(t[4:6]), int(t[6:8])) - _dt.timedelta(days=n)
    return d.isoformat()


def _boc_frame(monkeypatch, *, mid=86.384, fallback=None, days_ago=1):
    """注入中行牌价帧（列名同 akshare currency_boc_sina 实测；日期相对今天）。"""
    import pandas as pd

    row = {"日期": _days_ago_iso(days_ago), "中行汇买价": 85.36, "中行钞买价": 85.36,
           "中行钞卖价/汇卖价": 85.70, "央行中间价": mid, "中行折算价": fallback}
    monkeypatch.setattr(hk_ah, "_fetch_boc_raw", lambda: pd.DataFrame([row]))


def test_fx_converts_per_100_hkd(monkeypatch):
    """**每 100 港元** → 必须 /100：86.384 → 0.86384 CNY/HKD。"""
    _boc_frame(monkeypatch)
    fx = hk_ah.fetch_fx_hkd_cny()
    assert abs(fx["rate"] - 0.86384) < 1e-9, f"未做 /100 换算：{fx['rate']}"
    assert fx["date"] == _days_ago_iso(1)
    assert "央行中间价" in fx["source"]


def test_fx_stale_frame_rejected_in_favour_of_fallback(monkeypatch):
    """**真机踩过的坑**：接口默认窗口曾返回 2023 年数据（滞后近 3 年）。

    若照单采用，会用一个过期汇率算出「看起来合理」的溢价率（-13.4% vs 真实 -7.8%，
    量级对、方向反）。故滞后超阈值 → 不采用，走降级源。
    """
    _boc_frame(monkeypatch, days_ago=1000)
    monkeypatch.setattr(hk_ah, "_fetch_fred_cross",
                        lambda: {"rate": 0.8637, "date": _days_ago_iso(8),
                                 "source": "FRED DEXCHUS/DEXHKUS（交叉换算）"})
    fx = hk_ah.fetch_fx_hkd_cny()
    assert abs(fx["rate"] - 0.8637) < 1e-9, "陈旧的中行牌价被采用了"
    assert "滞后" in fx["note"]


def test_fx_stale_frame_without_fallback_is_unavailable(monkeypatch):
    """陈旧 + 无降级源 → 判不可得（宁可不出溢价率，也不出错的）。"""
    _boc_frame(monkeypatch, days_ago=1000)
    monkeypatch.setattr(hk_ah, "_fetch_fred_cross", lambda: None)
    fx = hk_ah.fetch_fx_hkd_cny()
    assert fx["rate"] is None and "不可得" in fx["note"]


def test_fx_slightly_stale_frame_used_with_warning(monkeypatch):
    """轻度滞后（阈值内）仍可用，但须标注「陈旧」——不得静默。"""
    _boc_frame(monkeypatch, days_ago=hk_ah._FX_STALE_WARN_DAYS + 1)
    fx = hk_ah.fetch_fx_hkd_cny()
    assert fx["rate"] and "陈旧" in fx["note"]


def test_boc_raw_passes_explicit_date_range(monkeypatch):
    """`_fetch_boc_raw` 必须显式传日期区间（不传时该接口返回的默认窗口不是最新数据）。"""
    import contextlib
    import sys
    import types

    import pandas as pd

    seen: dict = {}
    fake_ak = types.SimpleNamespace(
        currency_boc_sina=lambda **kw: (seen.update(kw),
                                        pd.DataFrame([{"日期": _days_ago_iso(0),
                                                       "央行中间价": 86.384}]))[1])
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    import lib.proxy as proxy

    monkeypatch.setattr(proxy, "akshare_direct_session", lambda: contextlib.nullcontext())

    df = hk_ah._fetch_boc_raw()
    assert seen.get("start_date") and seen.get("end_date"), \
        f"漏传日期区间（会取到过期窗口）：{seen}"
    assert len(df) == 1


def test_fx_uses_converted_price_when_mid_missing(monkeypatch):
    """中间价缺失 → 退到 `中行折算价`（同为每 100 港元口径），并在 source 里写明用了哪列。"""
    _boc_frame(monkeypatch, mid=None, fallback=86.38)
    fx = hk_ah.fetch_fx_hkd_cny()
    assert abs(fx["rate"] - 0.8638) < 1e-9
    assert "折算价" in fx["source"]


def test_fx_falls_back_to_fred_cross_with_lag_note(monkeypatch):
    """中行牌价不可得 → FRED 交叉（CNY/HKD = DEXCHUS / DEXHKUS），须标滞后。"""
    monkeypatch.setattr(hk_ah, "_fetch_boc_raw", lambda: None)
    monkeypatch.setattr(hk_ah, "_fetch_fred_cross",
                        lambda: {"rate": 0.8637, "date": "2026-09-04",
                                 "source": "FRED DEXCHUS/DEXHKUS"})
    fx = hk_ah.fetch_fx_hkd_cny()
    assert abs(fx["rate"] - 0.8637) < 1e-9
    assert "滞后" in fx["note"]


def test_fx_fred_uses_actual_lag_and_marks_staleness(monkeypatch):
    """FRED 降级源不能硬编码“约一周”，须按实际观测日判陈旧。"""
    monkeypatch.setattr(hk_ah, "_fetch_boc_raw", lambda: None)
    days = hk_ah._FX_STALE_WARN_DAYS + 2
    monkeypatch.setattr(hk_ah, "_fetch_fred_cross",
                        lambda: {"rate": 0.8637, "date": _days_ago_iso(days),
                                 "source": "FRED DEXCHUS/DEXHKUS"})
    fx = hk_ah.fetch_fx_hkd_cny()
    assert fx["rate"] == 0.8637
    assert f"滞后 {days} 天" in fx["note"]
    assert "陈旧" in fx["note"]


def test_fx_stale_fred_is_rejected(monkeypatch):
    """FRED 超过同一失效阈值时不可继续用于 A/H 溢价计算。"""
    monkeypatch.setattr(hk_ah, "_fetch_boc_raw", lambda: None)
    days = hk_ah._FX_STALE_FAIL_DAYS + 1
    monkeypatch.setattr(hk_ah, "_fetch_fred_cross",
                        lambda: {"rate": 0.8637, "date": _days_ago_iso(days),
                                 "source": "FRED DEXCHUS/DEXHKUS"})
    fx = hk_ah.fetch_fx_hkd_cny()
    assert fx["rate"] is None
    assert "不可得" in fx["note"]


def test_fx_all_sources_down_is_explicit(monkeypatch):
    monkeypatch.setattr(hk_ah, "_fetch_boc_raw", lambda: None)
    monkeypatch.setattr(hk_ah, "_fetch_fred_cross", lambda: None)
    fx = hk_ah.fetch_fx_hkd_cny()
    assert fx["rate"] is None and fx["note"], "全失败须显式不可得（不得静默给 1.0）"


# ── CLI：ah 子命令（离线，注入三处取数）──────────────────────────────────

_FX_OK = {"rate": 0.86384, "date": "2026-09-11",
          "source": "中行牌价 央行中间价（每 100 港元 ÷100）",
          "note": "中国银行外汇牌价口径；央行中间价为当日 9:15 发布"}


def _args(tmp_path, a="600036", hk="03968"):
    import argparse

    return argparse.Namespace(a_symbol=a, hk_symbol=hk, outdir=str(tmp_path))


_CLI = None


def _cli():
    """按**文件路径**加载 CLI 模块。

    不能 `import hk`：conftest 只注入 lib 目录，且刻意不插 `scripts/` 根
    （避免 `import lib` 抢先命中本 skill 而非共享层）。CLI 自身在 import 时
    完成路径引导（_LIB + ensure_*），故用 importlib 按路径加载最干净。
    """
    global _CLI
    if _CLI is None:
        import importlib.util

        p = Path(__file__).resolve().parent.parent / "scripts" / "hk.py"
        spec = importlib.util.spec_from_file_location("hk_cli_under_test", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CLI = mod
    return _CLI


def _stub_all(monkeypatch, *, a_price=36.42, h_price=42.15, fx=None,
              a_ts="20260912150000", h_ts="2026/09/12 16:08:06"):
    """两侧报价 stub。

    时间戳**必须给且同日**（2026-09-12 双侧已收盘）——真实持仓快照恒带下标 30
    时间戳，缺 ts 会让交易日对齐判定判不可比（D1 修复后的新契约）。原 stub 只给
    `"20260912"`（8 位日期、非实测格式），属 fixture 不真实，已按 2026-09-17
    实测格式修正。
    """
    hk_mod = _cli()

    monkeypatch.setattr(hk_mod, "_a_quote_row", lambda s: {"price": a_price, "ts": a_ts})
    monkeypatch.setattr(hk_mod, "_snapshot_row",
                        lambda s: {"price": h_price, "name": "招商银行", "ts": h_ts})
    monkeypatch.setattr(hk_mod.hk_ah, "fetch_fx_hkd_cny",
                        lambda: dict(fx if fx is not None else _FX_OK))
    return hk_mod


def test_cli_ah_prints_premium_with_formula(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_all(monkeypatch)
    assert hk_mod.cmd_ah(_args(tmp_path)) == 0
    out = capsys.readouterr().out
    # 36.42 / (42.15 × 0.86384) − 1 = +0.03%
    assert "+0.03%" in out, f"溢价率未按公式算出: {out[:300]}"
    assert "Python calc" in out, "P0：加工数字须带公式标签"
    for banned in ("建议买入", "建议卖出", "建议持有", "应当加仓", "应当减仓"):
        assert banned not in out
    assert list(tmp_path.glob("600036-03968-AH比价/*.md")), "比价须落盘"
    for caliber in ("币种", "汇率时点", "复权"):
        assert caliber in out, f"口径三件套缺 {caliber}"


def test_cli_ah_missing_h_price_is_explicit(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_all(monkeypatch, h_price=None)
    assert hk_mod.cmd_ah(_args(tmp_path)) == 1, "输入不可得须返回 1（不得出中性值）"
    out = capsys.readouterr().out
    assert "不可得" in out
    assert "不得读作「两地平价」" in out, "缺该项提示会被读成「两地平价」这一事实断言"


def test_cli_ah_missing_fx_is_explicit(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_all(monkeypatch, fx={"rate": None, "date": None, "source": None,
                                        "note": "汇率不可得：中行牌价与 FRED 交叉均失败"})
    assert hk_mod.cmd_ah(_args(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "汇率不可得" in out and "—（不可得）" in out


def test_cli_ah_does_not_print_fallback_from_a_auction(monkeypatch, tmp_path, capsys):
    """回退口径中的 A 价必须是已收盘价，不能把竞价指示价标作「A 的收盘」。"""
    hk_mod = _stub_all(monkeypatch, a_ts="20260917092000",
                       h_ts="2026/09/17 09:20:00")
    assert hk_mod.cmd_ah(_args(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "—（不可比）" in out
    assert "参考口径（**前提待验证，非本表结论**）" not in out


def test_cli_ah_rejects_a_share_code_in_hk_slot(monkeypatch, tmp_path, capsys):
    """H 槽位收到 A 股 6 位码 → exit 2（v1 既有纪律：防 zfill 错路由）。"""
    hk_mod = _stub_all(monkeypatch)
    assert hk_mod.cmd_ah(_args(tmp_path, hk="600036")) == 2
    assert "❌" in capsys.readouterr().err


def test_cli_ah_rejects_bad_a_symbol(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_all(monkeypatch)
    assert hk_mod.cmd_ah(_args(tmp_path, a="0700")) == 2
    assert "A 股代码" in capsys.readouterr().err
