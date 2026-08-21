"""E1 板块同步性引擎（sector_sync）单测 — 无网络。

覆盖验收 #5：正常路径 / 数据不足降级 / 缓存命中 / 行业分类缺失。
统计量用 numpy 独立复算比对（验收 #1 的单元层：与引擎实现不同的计算路径）。
网络层全部经 mock 打 `sector_sync._ak_fetch`（D13：patch 目标 = 定义模块
命名空间；mock 数据含独特标记，patch 失效时断言必然失败，不会碰巧通过）。
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from cache import DataCache  # noqa: E402
import sector_sync as ss  # noqa: E402


# ---------------------------------------------------------------------------
# 确定性合成市场（无随机数，结果可精确复算）
# ---------------------------------------------------------------------------

def _make_weekdays(n: int, start: str = "20250102") -> list[str]:
    d = date(2025, 1, 2)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _index_closes(dates: list[str], base: float = 1000.0) -> list[float]:
    """板块指数收盘：r_t = 0.0005 + 0.01·sin(t/17)。"""
    closes = [base]
    for t in range(1, len(dates)):
        r = 0.0005 + 0.01 * math.sin(t / 17.0)
        closes.append(closes[-1] * (1.0 + r))
    return closes


def _stock_closes(dates: list[str], idx: list[float], beta: float = 1.2) -> list[float]:
    """个股收盘：r_i,t = beta·r_idx,t + 0.0008·cos(t/5)。"""
    closes = [idx[0]]
    for t in range(1, len(dates)):
        r_idx = idx[t] / idx[t - 1] - 1.0
        r = beta * r_idx + 0.0008 * math.cos(t / 5.0)
        closes.append(closes[-1] * (1.0 + r))
    return closes


def _cons_closes(dates: list[str], idx: list[float], i: int, n: int) -> list[float]:
    """成分股 i 收盘：r_i,t = r_idx,t + 0.004·sin(t/9 + i·0.7)。"""
    closes = [idx[0]]
    for t in range(1, len(dates)):
        r_idx = idx[t] / idx[t - 1] - 1.0
        r = r_idx + 0.004 * math.sin(t / 9.0 + i * 0.7)
        closes.append(closes[-1] * (1.0 + r))
    return closes


def _rows(dates: list[str], closes: list[float]) -> list[dict]:
    return [{"trade_date": d, "close": c} for d, c in zip(dates, closes)]


def _make_cache(tmp_path) -> DataCache:
    return DataCache(cache_dir=tmp_path / "cache")


# ---------------------------------------------------------------------------
# β / R² / 特质方差
# ---------------------------------------------------------------------------

class TestBeta:
    def test_beta_recovers_known_beta(self):
        dates = _make_weekdays(80)
        idx = _index_closes(dates)
        stk = _stock_closes(dates, idx, beta=1.2)
        i_rets = [idx[t] / idx[t - 1] - 1 for t in range(1, len(dates))][-60:]
        s_rets = [stk[t] / stk[t - 1] - 1 for t in range(1, len(dates))][-60:]
        res = ss.sector_beta_stats(s_rets, i_rets)
        assert res["available"]
        assert abs(res["beta"] - 1.2) < 0.15  # 噪声 cos(t/5) 与 sin(t/17) 近似正交
        assert 0.5 < res["r_squared"] <= 1.0
        assert res["observations"] == 60

    def test_beta_zero_is_valid_not_none(self):
        """D1：β 可合法为 0.0，不得被 `or` 吞掉。"""
        dates = _make_weekdays(40)
        i_rets = [0.01 * math.sin(t) for t in range(60)]
        s_rets = [0.0005 * math.cos(t * 3) for t in range(60)]  # 与指数无关
        res = ss.sector_beta_stats(s_rets, i_rets)
        assert res["available"]
        assert abs(res["beta"]) < 0.05

    def test_beta_insufficient_fails_loud(self):
        res = ss.sector_beta_stats([0.01] * 5, [0.01] * 5)
        assert not res["available"]
        assert "数据点不足" in res["reason"]

    def test_beta_zero_market_variance(self):
        res = ss.sector_beta_stats([0.01] * 30, [0.0] * 30)
        assert not res["available"]
        assert "方差为零" in res["reason"]


# ---------------------------------------------------------------------------
# 板块内离散度
# ---------------------------------------------------------------------------

class TestDispersion:
    def test_dispersion_known_std(self):
        # pstdev([0.01, 0.02, 0.03]×10) = 0.008165 → 0.82%（末日 ≥ 20 只成分股）
        last_day = [0.01, 0.02, 0.03] * 10
        res = ss.cross_sectional_dispersion_pct(
            {"20250102": [0.01] * 25, "20250103": last_day})  # F3：交易日键
        assert res["available"]
        assert res["n_constituents"] == 30
        assert res["dispersion_pct"] == round(float(np.std(last_day)) * 100, 2)

    def test_dispersion_insufficient_constituents(self):
        rets = {"20250102": [0.01] * 19}
        res = ss.cross_sectional_dispersion_pct(rets)
        assert not res["available"]
        assert "成分股不足" in res["reason"]

    def test_dispersion_empty(self):
        res = ss.cross_sectional_dispersion_pct({})
        assert not res["available"]


# ---------------------------------------------------------------------------
# 收益序列（F2：零收盘守卫）
# ---------------------------------------------------------------------------

class TestReturns:
    def test_zero_close_current_injected_as_none(self):
        """F2：cur==0 不得注入 -100% 收益（0/prev − 1 污染 OLS/CSAD/下行相关）。"""
        dates = ["20250101", "20250102", "20250103"]
        by_date = {"20250101": 10.0, "20250102": 0.0, "20250103": 12.0}
        rets = ss._returns(dates, by_date)
        assert rets == [None, None]  # cur==0 与 prev==0 均拒绝

    def test_zero_close_prev_injected_as_none(self):
        dates = ["20250101", "20250102", "20250103"]
        by_date = {"20250101": 0.0, "20250102": 10.0, "20250103": 11.0}
        rets = ss._returns(dates, by_date)
        assert rets[0] is None
        assert rets[1] == pytest.approx(0.1)

    def test_normal_returns_unchanged(self):
        dates = ["20250101", "20250102", "20250103"]
        by_date = {"20250101": 10.0, "20250102": 10.5, "20250103": 11.0}
        rets = ss._returns(dates, by_date)
        assert rets[0] == pytest.approx(0.05)
        assert rets[1] == pytest.approx(11.0 / 10.5 - 1.0)


# ---------------------------------------------------------------------------
# CSAD 回归（CCK 2000）
# ---------------------------------------------------------------------------

class TestCsad:
    def _herding_dataset(self, n_days: int = 120, gamma2: float = -0.5):
        """构造 CSAD_t = 0.02 − 0.005·|Rm| + gamma2·Rm² 的精确样本（交易日键）。"""
        dates = _make_weekdays(n_days + 1)
        idx_ret_by_date: dict[str, float] = {}
        returns_by_day: dict[str, list[float]] = {}
        for t in range(n_days):
            rm = 0.0005 + 0.01 * math.sin(t / 7.0)
            d = dates[t + 1]  # 结束交易日（F3：CSAD 以交易日对齐）
            idx_ret_by_date[d] = rm
            csad_t = 0.02 - 0.005 * abs(rm) + gamma2 * rm * rm
            # 30 只成分股：20 只 = Rm + CSAD_t，10 只 = Rm − CSAD_t → mean|dev| = CSAD_t
            returns_by_day[d] = [rm + csad_t] * 20 + [rm - csad_t] * 10
        return idx_ret_by_date, returns_by_day

    def test_herding_gamma2_recovered(self):
        idx_ret_by_date, by_day = self._herding_dataset()
        res = ss.csad_regression(idx_ret_by_date, by_day)
        assert res["available"]
        assert abs(res["gamma2"] - (-0.5)) < 1e-9
        assert res["n_days"] == len(idx_ret_by_date)

    def test_gamma2_matches_numpy_independent(self):
        """验收 #1 单元层：与 numpy lstsq 独立复算一致。"""
        idx_ret_by_date, by_day = self._herding_dataset(n_days=150, gamma2=-0.8)
        res = ss.csad_regression(idx_ret_by_date, by_day)
        dts = sorted(idx_ret_by_date)
        rm = np.array([idx_ret_by_date[d] for d in dts])
        csad = np.array([sum(abs(r - idx_ret_by_date[d]) for r in by_day[d]) / len(by_day[d])
                         for d in dts])
        A = np.vstack([np.ones_like(rm), np.abs(rm), rm ** 2]).T
        coef = np.linalg.lstsq(A, csad, rcond=None)[0]
        assert abs(res["gamma2"] - float(coef[2])) < 1e-9

    def test_herding_positive_gamma2_no_herding(self):
        """γ2 > 0（发散型）也能正确恢复——引擎不自带方向假设。"""
        idx_ret_by_date, by_day = self._herding_dataset(gamma2=0.7)
        res = ss.csad_regression(idx_ret_by_date, by_day)
        assert abs(res["gamma2"] - 0.7) < 1e-9

    def test_csad_insufficient_fails_loud(self):
        dates = _make_weekdays(11)
        idx_ret_by_date = {d: 0.01 for d in dates[1:]}
        by_day = {d: [0.01] * 30 for d in dates[1:]}
        res = ss.csad_regression(idx_ret_by_date, by_day)
        assert not res["available"]
        assert "样本不足" in res["reason"]

    def test_t_stat_matches_numpy_ill_conditioned(self):
        """SE 路径回归：|Rm| 与 Rm² 高度共线（实盘形态，未缩放 X'X 条件数 ~1e10）
        时 t 统计量必须与 numpy 独立复算一致（曾因 RHS 构造错误塌成 None）。"""
        dates = _make_weekdays(251)
        idx_ret_by_date: dict[str, float] = {}
        returns_by_day: dict[str, list[float]] = {}
        for t in range(250):
            rm = 0.0005 + 0.01 * math.sin(t / 7.0)
            d = dates[t + 1]
            idx_ret_by_date[d] = rm
            csad_t = (0.02 - 0.005 * abs(rm) - 0.5 * rm * rm
                      + 1e-4 * math.sin(3 * t))  # 加入噪声使 RSS > 0
            returns_by_day[d] = [rm + csad_t] * 20 + [rm - csad_t] * 10
        res = ss.csad_regression(idx_ret_by_date, returns_by_day)
        assert res["available"]
        assert res["t_stat"] is not None, "t 统计量不得为 None"

        dts = sorted(idx_ret_by_date)
        rm = np.array([idx_ret_by_date[d] for d in dts])
        csad = np.array([sum(abs(r - idx_ret_by_date[d]) for r in returns_by_day[d]) / 30
                         for d in dts])
        A = np.vstack([np.ones_like(rm), np.abs(rm), rm ** 2]).T
        coef = np.linalg.lstsq(A, csad, rcond=None)[0]
        resid = csad - A @ coef
        s2 = float(resid @ resid) / (250 - 3)
        xtx_inv = np.linalg.inv(A.T @ A)
        se_np = float(np.sqrt(s2 * xtx_inv[2, 2]))
        t_np = float(coef[2] / se_np)
        assert abs(res["gamma2"] - float(coef[2])) < 1e-6
        assert abs(res["t_stat"] - t_np) < 1e-6

    def test_thin_days_excluded(self):
        """D4：每日 < 20 只成分股的交易日不参与 CSAD（覆盖范围标注）。"""
        idx_ret_by_date, by_day = self._herding_dataset(n_days=60)
        drop_day = sorted(idx_ret_by_date)[10]
        by_day[drop_day] = [0.01] * 5  # 当日仅 5 只 → 剔除
        res = ss.csad_regression(idx_ret_by_date, by_day)
        assert res["available"]
        assert res["n_days"] == 59

    def test_dropped_day_no_misalignment(self):
        """F3：任一侧缺日不得错位——删除一日后其余天仍按交易日与当日指数收益配对。"""
        idx_ret_by_date, by_day = self._herding_dataset(n_days=60, gamma2=-0.5)
        drop = sorted(idx_ret_by_date)[15]
        del by_day[drop]  # 当日无成分股数据 → 仅该日 CSAD 观察剔除
        res = ss.csad_regression(idx_ret_by_date, by_day)
        assert res["available"]
        assert res["n_days"] == 59
        keep = [d for d in sorted(idx_ret_by_date) if d != drop]
        rm = np.array([idx_ret_by_date[d] for d in keep])
        csad = np.array([sum(abs(r - idx_ret_by_date[d]) for r in by_day[d]) / len(by_day[d])
                         for d in keep])
        A = np.vstack([np.ones_like(rm), np.abs(rm), rm ** 2]).T
        coef = np.linalg.lstsq(A, csad, rcond=None)[0]
        assert abs(res["gamma2"] - float(coef[2])) < 1e-9


# ---------------------------------------------------------------------------
# 下行相关差（Forbes-Rigobon 校正）
# ---------------------------------------------------------------------------

class TestDownsideCorr:
    def _regime_dataset(self, n_per: int = 40):
        """下行档：R_m = −0.04±0.002；上行档：R_m = +0.04±0.002；中段 ±0.01。

        中段压低整体 σ（≈0.033）→ 下行档全部 ≤ −σ、上行档全部 ≥ +σ，
        n_down = n_up = 40 稳定满足 ≥ 20 门槛。个股 = 1.1×R_m + 噪声。"""
        idx = []
        stk = []
        for _ in range(n_per):
            rm = -0.04 + 0.003 * math.sin(_)  # 下行档（高波：噪声 0.003）
            idx.append(rm)
            stk.append(1.1 * rm + 0.001 * math.cos(_ * 2))
        for _ in range(n_per):
            rm = 0.04 + 0.001 * math.sin(_ * 3)  # 上行档（低波：噪声 0.001）
            idx.append(rm)
            stk.append(1.1 * rm + 0.001 * math.cos(_ * 5))
        for _ in range(n_per):  # 中段（|R_m| < σ，不进入任何档）
            rm = 0.01 * math.sin(_ * 7)
            idx.append(rm)
            stk.append(1.1 * rm + 0.001 * math.cos(_ * 11))
        return stk, idx

    def test_fr_correction_formula_and_direction(self):
        stk, idx = self._regime_dataset()
        res = ss.downside_correlation_gap(stk, idx)
        assert res["available"]
        assert res["n_down"] == 40 and res["n_up"] == 40
        # 下行档波动更高 → δ > 0 → 校正值 < 原始值（高波市态机械性高相关被剔除）
        assert res["delta"] > 0
        assert res["rho_minus_corr"] < res["rho_minus_raw"]
        # 公式核对：ρ_corr = ρ*/sqrt(1 + δ(1 − ρ*²))
        expected = (res["rho_minus_raw"] / math.sqrt(
            1.0 + res["delta"] * (1.0 - res["rho_minus_raw"] ** 2)))
        assert abs(res["rho_minus_corr"] - expected) < 1e-12
        assert abs(res["gap"] - (res["rho_minus_corr"] - res["rho_plus"])) < 1e-12
        assert res["gap"] > 0  # 下行联动真实存在（校正后仍为正）

    def test_gap_matches_numpy_independent(self):
        stk, idx = self._regime_dataset()
        res = ss.downside_correlation_gap(stk, idx)
        s = np.array(stk)
        m = np.array(idx)
        sigma = float(np.std(m, ddof=1))
        down = m <= -sigma
        up = m >= sigma
        rho_m = float(np.corrcoef(s[down], m[down])[0, 1])
        rho_p = float(np.corrcoef(s[up], m[up])[0, 1])
        delta = float(np.var(s[down], ddof=1) / np.var(s[up], ddof=1) - 1.0)
        corr = rho_m / math.sqrt(1.0 + delta * (1.0 - rho_m ** 2))
        assert abs(res["rho_minus_raw"] - rho_m) < 1e-12
        assert abs(res["rho_plus"] - rho_p) < 1e-12
        assert abs(res["delta"] - delta) < 1e-12
        assert abs(res["rho_minus_corr"] - corr) < 1e-12
        assert abs(res["gap"] - (corr - rho_p)) < 1e-12

    def test_regime_insufficient_fails_loud(self):
        # 100 个零收益日 + 每侧 10 个 ±0.05 极值日 → σ≈0.0204，各档仅 10 日 < 20
        idx = [0.0] * 100 + [-0.05] * 10 + [0.05] * 10
        stk = [0.0] * 100 + [-0.04] * 10 + [0.04] * 10
        res = ss.downside_correlation_gap(stk, idx)
        assert not res["available"]
        assert "1σ 分组样本不足" in res["reason"]

    def test_constant_index_fails_loud(self):
        res = ss.downside_correlation_gap([0.01] * 50, [0.0] * 50)
        assert not res["available"]
        assert "无波动" in res["reason"]

    def test_short_window_fails_loud(self):
        res = ss.downside_correlation_gap([0.01] * 10, [0.01] * 10)
        assert not res["available"]


# ---------------------------------------------------------------------------
# compute_sector_sync 全流程（_ak_fetch mock，无网络）
# ---------------------------------------------------------------------------

def _em_board_rows():
    return pd.DataFrame([{"板块名称": "玻璃玻纤", "板块代码": "BK9999"}])


def _sw_tables():
    """申万三级行业表：L3 空；L2 玻璃玻纤（16 只，实测 2026-08-21 为 16）；
    L1 建筑材料。祖先链 玻璃玻纤 → 建筑材料。"""
    return {
        "sw_index_third_info": pd.DataFrame(),
        "sw_index_second_info": pd.DataFrame(
            [{"行业名称": "玻璃玻纤", "行业代码": "801712.SI", "上级行业": "建筑材料"}]),
        "sw_index_first_info": pd.DataFrame(
            [{"行业名称": "建筑材料", "行业代码": "801060.SI", "上级行业": ""}]),
    }


def _make_ak_fetch(dates: list[str], idx_closes: list[float],
                   n_cons: int = 30, *, em_fails: bool = True,
                   n_l2_cons: int = 16) -> Any:
    """构造 _ak_fetch mock 的按函数分派表。

    - em_fails=True：东财 BK 列表抛 ConnectionError → 走申万降级路径。
    - L2 玻璃玻纤成分默认 16 只（< 20）→ 候选链自动落到 L1 建筑材料（30 只）。
    数据带独特标记（指数日期序列、代码 600001..），patch 失效时断言必然失败。
    """
    cons_codes = [f"6000{i:02d}" for i in range(1, n_cons + 1)]
    l2_cons_codes = [f"6001{i:02d}" for i in range(1, n_l2_cons + 1)]
    hist_df = pd.DataFrame({
        "日期": [d for d in dates],
        "收盘": idx_closes,
    })
    sw_tables = _sw_tables()

    def _fn(name: str, *args, **kwargs):
        if name == "stock_board_industry_name_em":
            if em_fails:
                raise ConnectionError("Remote end closed connection without response")
            return _em_board_rows()
        if name in sw_tables:
            return sw_tables[name]
        if name == "stock_board_industry_hist_em":
            assert kwargs.get("symbol") == "玻璃玻纤"
            return hist_df
        if name == "index_hist_sw":
            assert kwargs.get("symbol") in ("801712", "801060"), kwargs
            return hist_df
        if name == "stock_board_industry_cons_em":
            assert kwargs.get("symbol") == "玻璃玻纤"
            return pd.DataFrame({"代码": cons_codes, "名称": [f"股{i}" for i in range(n_cons)]})
        if name == "index_component_sw":
            sym = str(kwargs.get("symbol"))
            if sym == "801712":
                return pd.DataFrame({"证券代码": l2_cons_codes,
                                     "证券名称": [f"股{i}" for i in range(n_l2_cons)]})
            assert sym == "801060", sym
            return pd.DataFrame({"证券代码": cons_codes, "证券名称": [f"股{i}" for i in range(n_cons)]})
        if name == "stock_zh_a_hist":
            code = str(kwargs.get("symbol"))
            if code.startswith("6001"):
                i = l2_cons_codes.index(code)
                closes = _cons_closes(dates, idx_closes, i, n_l2_cons)
            else:
                i = cons_codes.index(code)
                closes = _cons_closes(dates, idx_closes, i, n_cons)
            return pd.DataFrame({"日期": dates, "收盘": closes})
        if name == "stock_zh_a_daily":
            raise AssertionError(f"不应走到 sina 降级: {name}({kwargs})")
        raise AssertionError(f"未预期网络调用: {name}({args}, {kwargs})")

    return _fn


def _stock_kline(dates: list[str], idx_closes: list[float]) -> list[dict]:
    return _rows(dates, _stock_closes(dates, idx_closes))


class TestComputeSectorSync:
    def test_normal_path_all_fields(self, tmp_path, monkeypatch):
        """正常路径（申万降级）：6 字段全部非 None，且与独立复算一致。"""
        dates = _make_weekdays(340)
        idx_closes = _index_closes(dates)
        n_cons = 30
        monkeypatch.setattr(ss, "_ak_fetch", _make_ak_fetch(dates, idx_closes, n_cons))
        cache = _make_cache(tmp_path)

        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=cache)

        assert out["available"], out["reasons"]
        assert out["provider"] == "sw"          # EM 拒连 → 申万
        # 玻璃玻纤 L2 成分 16 只 < 20 → 候选链落到上级行业 建筑材料（L1）
        assert out["industry"] == "建筑材料"
        assert out["index_code"] == "801060"
        assert out["n_constituents"] == 30
        assert out["window_days"] == 250
        assert out["window_end"] == dates[-1]
        for f in ss.SECTOR_SYNC_FIELDS:
            assert out["fields"][f] is not None, f"{f} 不可得: {out['reasons']}"

        # 独立复算（numpy）：β / 离散度 / CSAD γ2 / 下行相关
        idx_arr = np.array(idx_closes)
        i_rets = idx_arr[1:] / idx_arr[:-1] - 1.0
        stk_arr = np.array(_stock_closes(dates, idx_closes))
        s_rets = stk_arr[1:] / stk_arr[:-1] - 1.0

        beta_exp = np.cov(s_rets[-60:], i_rets[-60:])[0, 1] / np.var(i_rets[-60:], ddof=1)
        r2_exp = np.corrcoef(s_rets[-60:], i_rets[-60:])[0, 1] ** 2
        assert abs(out["fields"]["sector_beta_60d"] - round(beta_exp, 2)) < 1e-9
        # r2 / 特质方差 / γ2 用 4 位精度（2 位会把微弱信号归零丢符号）
        assert abs(out["fields"]["sector_r2_60d"] - round(r2_exp, 4)) < 1e-9
        assert abs(out["fields"]["idio_var_share"] - round(1.0 - r2_exp, 4)) < 1e-9

        # 离散度：末日横截面 std（成分收益 = r_idx + e_it，r_idx 为常数 → std 仅来自 e_it）
        last_r = i_rets[-1]
        e_last = [0.004 * math.sin((len(dates) - 1) / 9.0 + i * 0.7) for i in range(n_cons)]
        disp_exp = round(np.std(e_last) * 100, 2)
        assert abs(out["fields"]["sector_dispersion"] - disp_exp) < 1e-9

        # CSAD：mean_i |e_it|；窗口收益 w（0..249）= 全序列第 90+w 步
        # （long_dates = dates[89:340] → 收益步 90..339；成分股 e 的相位用全序列步索引）
        rm_win = i_rets[-250:]
        csad = np.array([
            np.mean([abs(0.004 * math.sin((90 + t) / 9.0 + i * 0.7))
                     for i in range(n_cons)])
            for t in range(250)])
        A = np.vstack([np.ones(250), np.abs(rm_win), rm_win ** 2]).T
        coef = np.linalg.lstsq(A, csad, rcond=None)[0]
        assert abs(out["fields"]["csad_gamma2"] - round(float(coef[2]), 4)) < 1e-9
        # 正常路径（成分股与指数同日）横截面日期 = window_end
        assert out["meta"]["sector_dispersion"]["date"] == dates[-1]

        # 下行相关（独立复算，含 FR 校正）
        sigma = float(np.std(rm_win, ddof=1))
        down = rm_win <= -sigma
        up = rm_win >= sigma
        assert out["meta"]["downside_corr_gap"]["n_down"] == int(down.sum())
        assert out["meta"]["downside_corr_gap"]["n_up"] == int(up.sum())

        # 缓存已写入（缓存命中测试依赖）
        assert cache.get("sector_index_hist", "801060") is not None
        assert cache.get("sector_cons", "801060") is not None
        assert cache.get("sector_cons_kline", "600001") is not None

    def test_em_primary_path(self, tmp_path, monkeypatch):
        """东财 BK 可用时走主口径（provider=em_bk）。"""
        dates = _make_weekdays(300)
        idx_closes = _index_closes(dates)
        monkeypatch.setattr(ss, "_ak_fetch",
                            _make_ak_fetch(dates, idx_closes, em_fails=False))
        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=_make_cache(tmp_path))
        assert out["available"]
        assert out["provider"] == "em_bk"
        assert out["index_code"] == "BK9999"

    def test_cache_hit_no_expensive_network(self, tmp_path, monkeypatch):
        """缓存命中：板块指数/成分股/成分股日线不再走网络（D6/D10）。"""
        dates = _make_weekdays(340)
        idx_closes = _index_closes(dates)
        n_cons = 30
        cache = _make_cache(tmp_path)
        # 首跑：真实 mock 全量
        monkeypatch.setattr(ss, "_ak_fetch", _make_ak_fetch(dates, idx_closes, n_cons))
        first = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=cache)
        assert first["available"]

        # 二跑：任何板块数据抓取都必须命中缓存（raise 防走网络）；
        # 行业解析（EM 探测 + 申万三级列表）是廉价调用，允许但记数。
        calls: list[str] = []
        sw_tables = _sw_tables()

        def _offline(name, *args, **kwargs):
            calls.append(name)
            if name in ("index_hist_sw", "index_component_sw",
                        "stock_zh_a_hist", "stock_zh_a_daily",
                        "stock_board_industry_hist_em",
                        "stock_board_industry_cons_em"):
                raise AssertionError(f"缓存命中后不应再抓取: {name}")
            if name == "stock_board_industry_name_em":
                raise ConnectionError("mock EM 拒连")
            if name in sw_tables:
                return sw_tables[name]
            raise AssertionError(f"未预期调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _offline)
        second = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=cache)
        assert second["available"]
        assert second["fields"] == first["fields"]
        assert second["meta"] == first["meta"]

    def test_no_industry_hint_no_network(self, tmp_path, monkeypatch):
        """行业分类缺失：零网络调用 + 明确原因（验收 #4）。"""
        def _forbid(name, *args, **kwargs):
            raise AssertionError(f"行业分类缺失时不应有网络调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _forbid)
        out = ss.compute_sector_sync("600176", industry_hint="",
                                     stock_kline=_stock_kline(_make_weekdays(80),
                                                              _index_closes(_make_weekdays(80))),
                                     cache=_make_cache(tmp_path))
        assert not out["available"]
        assert "行业分类缺失" in out["reasons"]["_all"]
        assert all(v is None for v in out["fields"].values())  # 无默认值

    def test_industry_no_match(self, tmp_path, monkeypatch):
        sw_tables = _sw_tables()

        def _fn(name, *args, **kwargs):
            if name == "stock_board_industry_name_em":
                raise ConnectionError("mock EM 拒连")
            if name in sw_tables:
                return sw_tables[name]
            raise AssertionError(f"未预期调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        out = ss.compute_sector_sync("600176", industry_hint="不存在行业",
                                     stock_kline=_stock_kline(_make_weekdays(80),
                                                              _index_closes(_make_weekdays(80))),
                                     cache=_make_cache(tmp_path))
        assert not out["available"]
        assert "未匹配到" in out["reasons"]["_all"]

    def test_constituents_insufficient_fails_loud(self, tmp_path, monkeypatch):
        """全部候选成分股 < 20 → 不可得（验收 #2），绝不给默认值。"""
        dates = _make_weekdays(300)
        idx_closes = _index_closes(dates)
        monkeypatch.setattr(ss, "_ak_fetch", _make_ak_fetch(dates, idx_closes, n_cons=15))
        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=_make_cache(tmp_path))
        assert not out["available"]
        assert "全部候选成分股 < 20" in out["reasons"]["_all"]
        assert all(v is None for v in out["fields"].values())

    def test_sw_l2_anchor_preferred(self, tmp_path, monkeypatch):
        """L2 成分充足（≥ 20）时锚定最细粒度行业，不落到 L1。"""
        dates = _make_weekdays(300)
        idx_closes = _index_closes(dates)
        fetch = _make_ak_fetch(dates, idx_closes, n_l2_cons=30)
        hist_symbols: list[str] = []

        def _tracking(name, *args, **kwargs):
            if name == "index_hist_sw":
                hist_symbols.append(str(kwargs.get("symbol")))
            return fetch(name, *args, **kwargs)

        monkeypatch.setattr(ss, "_ak_fetch", _tracking)
        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=_make_cache(tmp_path))
        assert out["available"]
        assert out["industry"] == "玻璃玻纤"
        assert out["index_code"] == "801712"
        assert hist_symbols == ["801712"]  # L2 已足额，L1 指数历史不被拉取

    def test_index_unavailable_all_providers(self, tmp_path, monkeypatch):
        """板块指数缺失（EM + 申万全挂）→ 不可得。"""
        def _fn(name, *args, **kwargs):
            if name == "stock_board_industry_name_em":
                raise ConnectionError("mock EM 拒连")
            if name == "sw_index_first_info":
                raise ConnectionError("mock SW 拒连")
            raise AssertionError(f"未预期调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        out = ss.compute_sector_sync("600176", industry_hint="玻璃玻纤",
                                     stock_kline=_stock_kline(_make_weekdays(80),
                                                              _index_closes(_make_weekdays(80))),
                                     cache=_make_cache(tmp_path))
        assert not out["available"]
        assert "不可得" in out["reasons"]["_all"]

    def test_stock_kline_insufficient(self, tmp_path, monkeypatch):
        """个股 K 线 < 61 点 → 不可得（无需网络）。"""
        def _forbid(name, *args, **kwargs):
            raise AssertionError(f"K 线不足时不应有网络调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _forbid)
        short = _rows(_make_weekdays(10), [100.0 + i for i in range(10)])
        out = ss.compute_sector_sync("600176", industry_hint="玻璃玻纤",
                                     stock_kline=short, cache=_make_cache(tmp_path))
        assert not out["available"]
        assert "K 线不足" in out["reasons"]["_all"]

    def test_stock_kline_missing(self, tmp_path, monkeypatch):
        def _forbid(name, *args, **kwargs):
            raise AssertionError(f"K 线缺失时不应有网络调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _forbid)
        out = ss.compute_sector_sync("600176", industry_hint="玻璃玻纤",
                                     stock_kline=None, cache=_make_cache(tmp_path))
        assert not out["available"]


# ---------------------------------------------------------------------------
# compute anchor_override（probe → compute 锚定共享）
# ---------------------------------------------------------------------------

class TestComputeAnchor:
    def test_anchor_override_skips_resolution_network(self, tmp_path, monkeypatch):
        """anchor_override：跳过候选解析（板块表零网络），直接抓取锚定板块。"""
        dates = _make_weekdays(340)
        idx_closes = _index_closes(dates)
        n_cons = 30
        calls: list[str] = []
        cons_codes = [f"6000{i:02d}" for i in range(1, n_cons + 1)]
        hist_df = pd.DataFrame({"日期": dates, "收盘": idx_closes})

        def _fn(name, *a, **k):
            calls.append(name)
            if name in ("stock_board_industry_name_em", "sw_index_third_info",
                        "sw_index_second_info", "sw_index_first_info"):
                raise AssertionError(f"anchor_override 下不应解析板块表: {name}")
            if name == "index_hist_sw":
                return hist_df
            if name == "index_component_sw":
                return pd.DataFrame({"证券代码": cons_codes})
            if name == "stock_zh_a_hist":
                i = cons_codes.index(str(k.get("symbol")))
                return pd.DataFrame(
                    {"日期": dates, "收盘": _cons_closes(dates, idx_closes, i, n_cons)})
            raise AssertionError(f"未预期调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=_make_cache(tmp_path),
            anchor_override={"provider": "sw", "index_name": "建筑材料",
                             "index_code": "801060"})
        assert out["available"], out["reasons"]
        assert out["provider"] == "sw"
        assert out["index_code"] == "801060"
        for name in ("stock_board_industry_name_em", "sw_index_third_info",
                     "sw_index_second_info", "sw_index_first_info"):
            assert name not in calls

    def test_anchor_override_insufficient_cons_fails_loud(self, tmp_path, monkeypatch):
        """anchor_override 锚定板块成分 < 20 → 不可得（验收 #2 仍生效）。"""
        dates = _make_weekdays(340)
        idx_closes = _index_closes(dates)
        hist_df = pd.DataFrame({"日期": dates, "收盘": idx_closes})

        def _fn(name, *a, **k):
            if name == "index_hist_sw":
                return hist_df
            if name == "index_component_sw":
                return pd.DataFrame({"证券代码": [f"6000{i:02d}" for i in range(1, 6)]})
            raise AssertionError(f"未预期调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=_make_cache(tmp_path),
            anchor_override={"provider": "sw", "index_name": "建筑材料",
                             "index_code": "801060"})
        assert not out["available"]
        assert "不可得" in out["reasons"]["_all"]
        assert all(v is None for v in out["fields"].values())


# ---------------------------------------------------------------------------
# 零尺度守卫 / 逐字段 fail loud / 离散度日期
# ---------------------------------------------------------------------------

class TestOlsZeroScale:
    def test_zero_scale_raises_value_error(self):
        """零范数列（窗口内板块收益恒 0 → |R_m|、R_m² 全零）→ ValueError，
        而非 ZeroDivisionError 穿透 csad_regression 的捕获。"""
        xs = [[1.0, 0.0, 0.0]] * 30
        ys = [0.02] * 30
        with pytest.raises(ValueError, match="零范数"):
            ss._ols_with_se(xs, ys)

    def test_csad_zero_market_fails_loud(self):
        """板块指数窗口内收益恒 0：CSAD 回归不可辨识 → available=False，
        绝不抛 ZeroDivisionError 击穿整个 sector_sync。"""
        dates = _make_weekdays(41)
        idx_ret_by_date = {d: 0.0 for d in dates[1:]}
        by_day = {d: [0.0] * 30 for d in dates[1:]}
        res = ss.csad_regression(idx_ret_by_date, by_day)
        assert not res["available"]
        assert "奇异" in res["reason"]

    def test_guarded_metric_value_error_becomes_unavailable(self):
        """_guarded_metric：ValueError → 该字段 available=False + reason。"""
        def _boom():
            raise ValueError("boom")

        res = ss._guarded_metric("CSAD 回归", _boom)
        assert res == {"available": False, "reason": "CSAD 回归 计算异常: boom"}


class TestDispersionMetaDate:
    def test_meta_date_is_last_cross_section_day(self, tmp_path, monkeypatch):
        """盘中形态（成分股日线滞后指数一日）：离散度按昨日横截面计算，
        meta.date 标注昨日而非 window_end（不把昨日数据标成当日）。"""
        dates = _make_weekdays(340)
        idx_closes = _index_closes(dates)
        n_cons = 30
        fetch = _make_ak_fetch(dates, idx_closes, n_cons)

        def _lagged(name, *a, **k):
            df = fetch(name, *a, **k)
            if name == "stock_zh_a_hist":
                return df.iloc[:-1]  # 成分股日线止于昨日
            return df

        monkeypatch.setattr(ss, "_ak_fetch", _lagged)
        out = ss.compute_sector_sync(
            "600176", industry_hint="玻璃玻纤",
            stock_kline=_stock_kline(dates, idx_closes), cache=_make_cache(tmp_path))
        assert out["available"], out["reasons"]
        assert out["window_end"] == dates[-1]
        assert out["meta"]["sector_dispersion"]["date"] == dates[-2]


# ---------------------------------------------------------------------------
# C7 措辞约束
# ---------------------------------------------------------------------------

class TestC7Wording:
    def test_interpretation_note_conditional(self):
        """C7：板块同步性结论必须带条件限定，不得出现「基本面不重要」。"""
        note = ss.SECTOR_SYNC_INTERPRETATION_NOTE
        assert "高同步性板块" in note and "短窗口" in note and "高波动市态" in note
        assert "基本面" in note  # 恢复意义的主语仍在
        assert "不重要" not in note

    def test_module_docstring_has_no_unconditional_claim(self):
        import inspect
        doc = inspect.getdoc(ss) or ""
        # 「基本面不重要」只能以禁令形式出现（「禁止写成…」），不得作为主张
        if "基本面不重要" in doc:
            assert "禁止写成「基本面不重要」" in doc
        assert "基本面在" in doc  # 条件限定句（C7 修正表述）存在


# ---------------------------------------------------------------------------
# F1 冷缓存门控探测 / F6 申万指数先排序再切片
# ---------------------------------------------------------------------------

def _probe_ak_fetch(func_name, *a, **k):
    """F1 探测用 akshare 桩：EM 板块名 → 指数历史 → 成分股（D13 唯一标记）。"""
    if func_name == "stock_board_industry_name_em":
        return pd.DataFrame([{"板块名称": "玻璃玻纤", "板块代码": "BK1071"}])
    if func_name == "stock_board_industry_hist_em":
        dates = _make_weekdays(30)
        return pd.DataFrame({"日期": dates, "收盘": _index_closes(dates)})
    if func_name == "stock_board_industry_cons_em":
        return pd.DataFrame({"代码": [f"6000{i:02d}" for i in range(25)]})  # 6 位补零
    raise AssertionError(f"unexpected akshare fetch: {func_name}")


class TestProbeCacheWarmth:
    def test_cold_cache_reports_cold(self, monkeypatch, tmp_path):
        """F1：成分股 kline 全冷（缺口 > 预算）→ warm=False，默认采集跳过。"""
        cache = _make_cache(tmp_path)
        monkeypatch.setattr(ss, "_ak_fetch", _probe_ak_fetch)
        res = ss.probe_sector_cache_warmth("玻璃玻纤", cache=cache)
        assert res["warm"] is False
        assert res["miss"] == 25
        assert res["valid"] == 0
        assert "未预热" in res["reason"]
        assert "--force-sector-sync" in res["reason"]
        assert res["anchor"]["index_code"] == "BK1071"  # 锚定随探测结果返回

    def test_warm_cache_within_budget(self, monkeypatch, tmp_path):
        """F1：缺口 ≤ 预算（24/25 已缓存）→ warm=True，现场补抓有界。"""
        cache = _make_cache(tmp_path)
        monkeypatch.setattr(ss, "_ak_fetch", _probe_ak_fetch)
        dates = _make_weekdays(30)
        closes = _index_closes(dates)
        for code in [f"6000{i:02d}" for i in range(24)]:
            cache.set("sector_cons_kline", code, list(zip(dates, closes)),
                      ttl_seconds=86400, source="test")
        res = ss.probe_sector_cache_warmth("玻璃玻纤", cache=cache)
        assert res["warm"] is True
        assert res["miss"] == 1
        assert res["valid"] == 24
        assert res["anchor"]["index_code"] == "BK1071"

    def test_miss_counts_invalid_and_tombstone(self, monkeypatch, tmp_path):
        """miss 判定与 compute 读取校验同口径：结构损坏条目计 miss、
        墓碑既非 miss 也非 valid（compute 侧不会为其发网络）。"""
        cache = _make_cache(tmp_path)
        monkeypatch.setattr(ss, "_ak_fetch", _probe_ak_fetch)
        dates = _make_weekdays(30)
        closes = _index_closes(dates)
        for code in [f"6000{i:02d}" for i in range(20)]:  # 20 只有效缓存
            cache.set("sector_cons_kline", code, list(zip(dates, closes)),
                      ttl_seconds=86400, source="test")
        cache.set("sector_cons_kline", "600020", {"failed": True},  # 2 只墓碑
                  ttl_seconds=ss._TOMBSTONE_TTL_SECONDS, source="failed")
        cache.set("sector_cons_kline", "600021", {"failed": True},
                  ttl_seconds=ss._TOMBSTONE_TTL_SECONDS, source="failed")
        cache.set("sector_cons_kline", "600022", [["垃圾条目"]],  # 1 只损坏
                  ttl_seconds=86400, source="test")
        # 600023/600024 未缓存
        res = ss.probe_sector_cache_warmth("玻璃玻纤", cache=cache)
        assert res["warm"] is True
        assert res["miss"] == 3          # 损坏 1 + 未缓存 2（墓碑不计）
        assert res["valid"] == 20        # 只计通过校验的条目
        assert res["total"] == 25

    def test_no_anchor_does_not_block(self, monkeypatch, tmp_path):
        """F1：行业无锚定板块 → anchor=None + 真实 reason（调用方据此跳过
        compute，不再二次解析；下一次采集自然重试）。"""
        cache = _make_cache(tmp_path)
        monkeypatch.setattr(ss, "_ak_fetch",
                            lambda *a, **k: pd.DataFrame())  # 全部空表
        res = ss.probe_sector_cache_warmth("不存在的行业", cache=cache)
        assert res["warm"] is True
        assert res["anchor"] is None
        assert "不可得" in res["reason"]

    def test_empty_hint_reason_and_no_anchor(self, monkeypatch, tmp_path):
        """行业分类缺失：anchor=None + 明确原因（验收 #4 措辞）。"""
        cache = _make_cache(tmp_path)

        def _forbid(*a, **k):
            raise AssertionError("空行业提示不应有网络调用")

        monkeypatch.setattr(ss, "_ak_fetch", _forbid)
        res = ss.probe_sector_cache_warmth("", cache=cache)
        assert res["warm"] is True
        assert res["anchor"] is None
        assert "行业分类缺失" in res["reason"]

    def test_parse_failure_reason_and_no_anchor(self, monkeypatch, tmp_path):
        """全部板块表解析失败：anchor=None + 真实 reason（fail-fast 契约）。"""
        cache = _make_cache(tmp_path)

        def _fn(name, *a, **k):
            raise ConnectionError("mock 全挂")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        res = ss.probe_sector_cache_warmth("玻璃玻纤", cache=cache)
        assert res["warm"] is True
        assert res["anchor"] is None
        assert "不可得" in res["reason"]


class TestFetchIndexHistory:
    def test_sw_sorts_before_slice(self, monkeypatch, tmp_path):
        """F6：申万全历史先排序再取尾部（防御接口乱序返回）。"""
        cache = _make_cache(tmp_path)
        rows = [
            {"日期": "2020-01-03", "收盘": 100.0},
            {"日期": "2020-01-02", "收盘": 99.0},   # 乱序：较新日期在前
            {"日期": "1999-12-31", "收盘": 1.0},    # 全历史起点
        ]
        monkeypatch.setattr(ss, "_ak_fetch",
                            lambda *a, **k: pd.DataFrame(rows))
        out = ss._fetch_index_history(
            {"provider": "sw", "index_name": "建筑材料", "index_code": "801010"},
            cache=cache, days=2)
        assert out == [("20200102", 99.0), ("20200103", 100.0)]  # 排序后取最近 2 日

    def test_cached_read_sorts_and_clips(self, monkeypatch, tmp_path):
        """缓存读同现场路径：排序 + 尾部裁剪（幂等修复修复前写入的乱序/超窗条目）。"""
        cache = _make_cache(tmp_path)
        cache.set("sector_index_hist", "801010",
                  [("2020-01-03", 100.0), ("2020-01-02", 99.0), ("1999-12-31", 1.0)],
                  ttl_seconds=86400, source="test")

        def _forbid(*a, **k):
            raise AssertionError("缓存命中后不应走网络")

        monkeypatch.setattr(ss, "_ak_fetch", _forbid)
        out = ss._fetch_index_history(
            {"provider": "sw", "index_name": "建筑材料", "index_code": "801010"},
            cache=cache, days=2)
        assert out == [("20200102", 99.0), ("20200103", 100.0)]
        # sorted() 返回新列表：缓存对象本身不被污染
        raw = cache.get("sector_index_hist", "801010")
        assert raw[0] == ["2020-01-03", 100.0]


class TestSectorTableCache:
    def test_table_cached_second_call_no_network(self, monkeypatch, tmp_path):
        """板块表解析走缓存：二次解析零网络（probe/compute 共享同一份表）。"""
        cache = _make_cache(tmp_path)
        calls: list[str] = []

        def _fn(name, *a, **k):
            calls.append(name)
            return _em_board_rows()

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        expected = {"provider": "em_bk", "index_name": "玻璃玻纤", "index_code": "BK9999"}
        assert ss._resolve_em_board("玻璃玻纤", cache=cache) == expected
        assert ss._resolve_em_board("玻璃玻纤", cache=cache) == expected
        assert calls == ["stock_board_industry_name_em"]

    def test_empty_table_not_cached(self, monkeypatch, tmp_path):
        """空表不缓存（D6）：每次解析都重新抓取，不把空结果钉死在缓存里。"""
        cache = _make_cache(tmp_path)
        calls: list[str] = []

        def _fn(name, *a, **k):
            calls.append(name)
            return pd.DataFrame()

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        assert ss._resolve_em_board("玻璃玻纤", cache=cache) is None
        assert ss._resolve_em_board("玻璃玻纤", cache=cache) is None
        assert len(calls) == 2
        assert cache.get("sector_tables", "stock_board_industry_name_em") is None


class TestSinaFallback:
    def _kline_mock(self, em_rows: dict, sina_rows: dict | None = None,
                    sina_raises: bool = False):
        def _fn(name, *a, **k):
            if name == "stock_zh_a_hist":
                return pd.DataFrame(em_rows)
            if name == "stock_zh_a_daily":
                if sina_raises:
                    raise ConnectionError("mock sina 拒连")
                return pd.DataFrame(sina_rows or {})
            raise AssertionError(f"未预期调用: {name}")

        return _fn

    def test_em_short_series_triggers_sina_pick_longer(self, monkeypatch, tmp_path):
        """截断 EM 响应（3 行，限流形态）→ 触发 sina 兜底，取较长者。"""
        dates = _make_weekdays(60)
        closes = _index_closes(dates)
        em_rows = {"日期": dates[:3], "收盘": [10.0, 10.1, 10.2]}
        sina_rows = {"date": dates, "close": closes}
        monkeypatch.setattr(ss, "_ak_fetch",
                            self._kline_mock(em_rows, sina_rows))
        cache = _make_cache(tmp_path)
        out = ss._fetch_constituent_kline("600001", cache=cache)
        assert len(out) == 60
        assert out[0] == (dates[0], 1000.0)
        raw = cache.get("sector_cons_kline", "600001")
        assert raw == [list(p) for p in zip(dates, closes)]

    def test_em_short_sina_missing_uses_em(self, monkeypatch, tmp_path):
        """EM 截断且 sina 也挂：仍缓存较长的 EM 序列（最佳可用信息）。"""
        dates = _make_weekdays(60)
        em_rows = {"日期": dates[:3], "收盘": [10.0, 10.1, 10.2]}
        monkeypatch.setattr(ss, "_ak_fetch",
                            self._kline_mock(em_rows, sina_raises=True))
        cache = _make_cache(tmp_path)
        out = ss._fetch_constituent_kline("600001", cache=cache)
        assert out == [("20250102", 10.0), ("20250103", 10.1), ("20250106", 10.2)]

    def test_fetch_uses_shanghai_dates(self, monkeypatch, tmp_path):
        """抓取窗口用上海时区（非本机 date.today()）——UTC 机器上滞后一天的根因。"""
        dates = _make_weekdays(60)
        monkeypatch.setattr(ss, "_shanghai_today", lambda: "20260821")
        monkeypatch.setattr(ss, "_shanghai_days_ago", lambda n: "20250701")
        seen: dict = {}

        def _fn(name, *a, **k):
            if name == "stock_zh_a_hist":
                seen.update(k)
                return pd.DataFrame({"日期": dates, "收盘": _index_closes(dates)})
            raise AssertionError(f"未预期调用: {name}")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        cache = _make_cache(tmp_path)
        assert len(ss._fetch_constituent_kline("600001", cache=cache)) == 60
        assert seen["start_date"] == "20250701"
        assert seen["end_date"] == "20260821"


class TestTombstone:
    def test_double_fail_writes_tombstone(self, monkeypatch, tmp_path):
        """EM + sina 双挂 → 写失败墓碑（短 TTL），近期 collect 不再重试。"""
        def _fn(name, *a, **k):
            raise ConnectionError("mock 全挂")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        cache = _make_cache(tmp_path)
        assert ss._fetch_constituent_kline("600001", cache=cache) is None
        raw = cache.get("sector_cons_kline", "600001")
        assert isinstance(raw, dict) and raw.get("failed") is True

    def test_fresh_tombstone_skips_network(self, monkeypatch, tmp_path):
        """墓碑未过期：读取直接返回 None，零网络（不再重试同一批失败代码）。"""
        cache = _make_cache(tmp_path)
        cache.set("sector_cons_kline", "600001", {"failed": True},
                  ttl_seconds=ss._TOMBSTONE_TTL_SECONDS, source="failed")

        def _forbid(*a, **k):
            raise AssertionError("墓碑未过期时不应有网络调用")

        monkeypatch.setattr(ss, "_ak_fetch", _forbid)
        assert ss._fetch_constituent_kline("600001", cache=cache) is None

    def test_bj_code_double_fail_writes_tombstone(self, monkeypatch, tmp_path):
        """北交所代码（无 sina 日线）：EM 失败也写墓碑，不再每次白试。"""
        calls: list[str] = []

        def _fn(name, *a, **k):
            calls.append(name)
            raise ConnectionError("mock EM 拒连")

        monkeypatch.setattr(ss, "_ak_fetch", _fn)
        cache = _make_cache(tmp_path)
        assert ss._fetch_constituent_kline("830001", cache=cache) is None
        assert calls == ["stock_zh_a_hist"]  # 北交所无 sina：只试 EM
        raw = cache.get("sector_cons_kline", "830001")
        assert isinstance(raw, dict) and raw.get("failed") is True
