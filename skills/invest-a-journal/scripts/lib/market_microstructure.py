"""市场微观结构 — Tier 1-3 指标采集 + 历史序列 + 环境护栏。

v0.2.2: 自建历史序列（market_snapshots 表）、历史分位标签、多指标交叉验证。
所有 akshare 调用走直连会话。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import statistics
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from dates import shanghai_today  # noqa: E402
from lib import env  # noqa: E402
from lib.nums import safe_float  # noqa: E402
from lib.proxy import akshare_direct_session  # noqa: E402
from lib.stats import percentile_rank_inclusive  # noqa: E402
from market_pulse import fetch_margin_account_info  # noqa: E402

logger = logging.getLogger(__name__)

# 涨跌停比极端阈值：>5:1 亢奋，<1:5（即 ratio < 0.2）恐慌
_LU_LD_EXTREME_UP = 5.0
_LU_LD_EXTREME_DOWN = 0.2  # 1:5

# 数据维度键（全失败判定用；date/labels/_errors 等元数据键不计入）
# 任一维度有值即视为采集成功；全部为 None → 信封标记 all_failed，
# 使 data_bridge 的失败不缓存语义（_FAILURE_STATUSES）对 microstructure 生效
_SNAPSHOT_DATA_KEYS = (
    "margin_balance", "margin_buy_amount", "ad_ratio",
    "lu_ld_ratio", "limit_up_count", "limit_down_count",
    "total_turnover", "sse_float_mcap", "szse_float_mcap",
    "erp", "pcr", "below_book_pct",
    "northbound_net_inflow", "northbound_market_value",
    "futures_basis_pct",
)


# ---------------------------------------------------------------------------
# 数据库连接（委托 db.py，避免 _conn/_safe_close 重复定义）
# ---------------------------------------------------------------------------

from db import _conn, _safe_close  # noqa: E402
from db_util import hist_ex_today, load_recent_rows, upsert_daily_rows  # noqa: E402
from lib.store import init_db  # noqa: E402  # 确保 market_snapshots 表存在

# market_snapshots 表列（与 store.init_db schema 对齐；写入统一走 upsert_daily_rows）
_MARKET_SNAPSHOT_COLUMNS = (
    "date", "margin_balance", "margin_buy_amount", "ad_ratio",
    "limit_up_count", "limit_down_count", "lu_ld_ratio", "total_turnover",
    "sse_float_mcap", "szse_float_mcap",
    "margin_to_mcap", "margin_buy_to_turnover", "margin_20d_change",
    "ad_ratio_5d_ma", "limit_down_20d_pct",
    "erp", "pcr", "below_book_pct",
    "northbound_net_inflow", "northbound_direction", "northbound_source",
    "futures_basis_pct",
    "env_label",
)


# ---------------------------------------------------------------------------
# 当日快照（v0.2.1 兼容接口，内部采集不变）
# ---------------------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    """采集 Tier 1-3 当日快照：两融、涨跌比、涨跌停比、成交额、ERP、PCR、破净率。

    每个指标独立采集，失败不阻塞其他维度。
    """
    result: dict[str, Any] = {
        "date": shanghai_today(),
        # Tier 1
        "margin_balance": None,          # 融资余额（亿元）
        "margin_buy_amount": None,        # 融资买入额（亿元）
        "ad_ratio": None,                 # 涨跌比
        "lu_ld_ratio": None,              # 涨跌停比（跌停=0 时为 None）
        "lu_ld_note": None,
        "limit_up_count": None,           # 涨停家数
        "limit_down_count": None,         # 跌停家数
        "total_turnover": None,           # 全市场成交额（亿）
        "sse_float_mcap": None,           # 上证流通市值（亿）
        "szse_float_mcap": None,          # 深证流通市值（亿）
        # Tier 2（save_snapshot 计算后写入）
        "margin_to_mcap": None,
        "margin_buy_to_turnover": None,
        "margin_20d_change": None,
        "ad_ratio_5d_ma": None,
        "limit_down_20d_pct": None,
        # Tier 3
        "erp": None,
        "pcr": None,
        "below_book_pct": None,
        # 资金面 — 北向
        "northbound_net_inflow": None,      # 北向净流入（亿元）
        "northbound_market_value": None,     # 北向持股市值（亿元）
        "northbound_direction": None,        # "流入" | "流出" | None
        "northbound_source": None,           # "direct" | "derived" | None
        # 资金面 — 股指期货（v0.2.6 F 系列：机构对冲行为，状态度量非预测）
        "futures_basis_pct": None,           # IC 当月基差%（负 = 贴水）
        # 标签
        "label_leverage": None,
        "label_breadth": None,
        "label_sentiment": None,
        "label_capital_flow": None,
        "_errors": [],
    }

    _fetch_margin(result)
    _fetch_ad_ratio(result)
    _fetch_limit_pools(result)
    _fetch_turnover(result)
    _fetch_erp(result)
    _fetch_pcr(result)
    _fetch_below_book_pct(result)
    _fetch_northbound(result)
    _fetch_futures(result)
    _compute_labels(result)
    _auto_persist(result)

    # 状态信封：全部数据维度失败 → "all_failed"，使 data_bridge 的失败
    # 不缓存语义（_FAILURE_STATUSES）生效。否则全 None 快照会被 5min 缓存
    # 服务给后续每次评估与 market-status --save（v0.2.3 回归修复）。
    if all(result.get(k) is None for k in _SNAPSHOT_DATA_KEYS):
        result["status"] = "all_failed"
    else:
        result["status"] = "ok"
    return result


# ---------------------------------------------------------------------------
# 自动持久化：每次 snapshot() 调用即积累一条历史记录
# ---------------------------------------------------------------------------

def _auto_persist(snap: dict) -> None:
    """snapshot() 调用后自动将 Tier 1 + Tier 3 写入 market_snapshots。

    非交易日（成交额 + 涨跌比均缺失）跳过写入。
    写入为 merge=True 逐列合并：Tier-1 字段刷新（新值非 NULL 覆盖）、
    Tier-2 字段（轻量路径恒 None）保留表中已有值——当日 save_snapshot()
    先写入的 margin_20d_change / margin_to_mcap / limit_down_20d_pct
    不会被轻量快照抹成 NULL。env_label 冲突时保留表中原值（save_snapshot
    的 v2 标签不被本函数的 v1 JSON 覆盖）；全新行仍写入 v1。
    save_snapshot() 自身仍以 INSERT OR REPLACE 补全 Tier 2 + v2 标签。

    此函数静默失败：持久化异常不阻塞 snapshot() 正常返回。
    """
    try:
        # 非交易日检测
        if snap.get("total_turnover") is None and snap.get("ad_ratio") is None:
            return

        # 构造简易 env_label JSON（v1 标签，无历史分位）
        env = {
            "leverage": snap.get("label_leverage"),
            "breadth": snap.get("label_breadth"),
            "sentiment": snap.get("label_sentiment"),
            "capital_flow": snap.get("label_capital_flow"),
        }
        warnings = []
        lev = str(env.get("leverage") or "")
        brd = str(env.get("breadth") or "")
        sen = str(env.get("sentiment") or "")
        if "去杠杆" in lev:
            warnings.append("去杠杆")
        if "极冷" in brd:
            warnings.append("广度极冷")
        if "极热" in brd:
            warnings.append("广度极热")
        if "极端亢奋" in sen:
            warnings.append("情绪极端亢奋")
        if "恐慌" in sen:
            warnings.append("恐慌")
        if "背离" in sen:
            warnings.append("情绪背离")
        env["summary"] = (
            "偏谨慎" if len(warnings) >= 2
            else ("⚠️ " + " + ".join(warnings) if len(warnings) == 1 else "正常")
        )
        snap["env_label"] = json.dumps(env, ensure_ascii=False)

        init_db()
        c = _conn()
        try:
            upsert_daily_rows(
                c, "market_snapshots",
                [{col: snap.get(col) for col in _MARKET_SNAPSHOT_COLUMNS}],
                pk=("date",), merge=True, exclude_cols=("env_label",),
            )
            c.commit()
        except Exception:
            c.rollback()
            logger.warning("_auto_persist(%s): DB write failed", snap.get("date"), exc_info=True)
        finally:
            _safe_close(c)
    except Exception:
        logger.warning("_auto_persist: persist failed", exc_info=True)


# ---------------------------------------------------------------------------
# 持久化（v0.2.2 新增）
# ---------------------------------------------------------------------------

def save_snapshot() -> dict[str, Any] | None:
    """采集当日快照 → 计算 Tier 2 衍生指标 → 写入 market_snapshots 表。

    v0.2.3：优先复用 data_bridge 5min 缓存快照（get_microstructure），
    避免与 query_data 评估路径重复重采 8 个数据源（含 Tushare 昂贵接口）；
    data_bridge 不可用时降级直接 snapshot()（与旧行为一致）。
    缓存命中时 _auto_persist 不重复执行——INSERT OR REPLACE 保证无重复行。

    Returns
    -------
    dict or None
        写入的快照字典；非交易日（涨跌家数/成交额缺失）返回 None 以跳过写入。
    """
    try:
        from data_bridge import get_microstructure  # noqa: PLC0415

        snap = get_microstructure()
    except Exception:
        # 缓存层任何异常（ImportError/OSError/UnicodeDecodeError，如磁盘满、
        # 坏缓存文件）一律降级直连 snapshot()——旧行为即直连，不能因缓存层
        # 新增失败面崩掉 market-status --save（v0.2.3 补丁 #6）
        snap = snapshot()
    if snap is None:
        return None

    # 非交易日检测：成交额缺失 → 大概率非交易日
    if snap.get("total_turnover") is None and snap.get("ad_ratio") is None:
        logger.info("疑似非交易日（成交额/涨跌比缺失），跳过 market_snapshot 写入")
        return None

    # 计算 Tier 2 衍生指标（需要历史序列）
    history = load_history(60)
    _compute_tier2(snap, history)

    # 计算 Tier 1-2 历史分位标签（v0.2.2 升级）。
    # 注（C14）：缓存快照（get_microstructure / snapshot()）携带 v1 标签
    # （_compute_labels，绝对值启发式，供 apply_env_guardrail 词表消费），
    # 此处 v2 分位标签原地 REPLACE 四个 label_* 字段 + env_label，最终
    # 落库行 = v2 标签——v1 与 v2 是兼容层 + 演进关系，非冗余。
    _compute_labels_v2(snap, history)

    # 确保 market_snapshots 表存在（独立调用时 init_db 可能未执行）
    init_db()

    # 写入
    c = _conn()
    try:
        upsert_daily_rows(
            c, "market_snapshots",
            [{col: snap.get(col) for col in _MARKET_SNAPSHOT_COLUMNS}],
            pk=("date",), merge=False,
        )
        c.commit()
        logger.info("market_snapshot %s saved", snap["date"])
        return snap
    except Exception as exc:
        logger.warning("market_snapshot save failed: %s", exc)
        c.rollback()
        return None
    finally:
        _safe_close(c)


def load_history(days: int = 60) -> list[dict]:
    """从 market_snapshots 表读取近 N 日记录。

    Returns
    -------
    list[dict]
        按 date ASC 排序的历史快照列表。
    """
    c = _conn()
    try:
        return load_recent_rows(c, "market_snapshots", limit=int(days))
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        _safe_close(c)


def latest_snapshot() -> dict | None:
    """获取最近一条 market_snapshot。"""
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM market_snapshots ORDER BY date DESC LIMIT 1",
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    finally:
        _safe_close(c)


# ---------------------------------------------------------------------------
# Tier 2 衍生计算
# ---------------------------------------------------------------------------

def _compute_tier2(snap: dict, history: list[dict]) -> None:
    """基于当日快照 + 历史序列计算 Tier 2 衍生指标（原地修改 snap）。"""

    # 7. 两融/流通市值
    margin = snap.get("margin_balance")
    sse_mcap = snap.get("sse_float_mcap")
    szse_mcap = snap.get("szse_float_mcap")
    if margin is not None and sse_mcap is not None and szse_mcap is not None:
        total_mcap = sse_mcap + szse_mcap
        if total_mcap > 0:
            snap["margin_to_mcap"] = round(margin / total_mcap * 100, 2)

    # 8. 融资买入/成交额（优先使用估算全市场成交额，避免仅用深交所而被夸大 ~2x）
    buy = snap.get("margin_buy_amount")
    turnover = snap.get("total_turnover_est") or snap.get("total_turnover")
    if buy is not None and turnover is not None and turnover > 0:
        snap["margin_buy_to_turnover"] = round(buy / turnover * 100, 2)

    # 9. 融资余额20日变化率
    # 注意：与步骤 10/11 同型——history 已含今日刚持久化的行（snapshot→_auto_persist→load_history），
    # 必须剔除今日，否则 20 日窗口实际只跨 19 个间隔、lookback[-20] 锚定 T-19 而非 T-20
    # （与 cd5e7a4 修复的双计 bug 同类，此前遗漏本步骤）
    if margin is not None and len(history) >= 20:
        snap_date_9 = snap.get("date")
        hist_ex_today_9 = hist_ex_today(history, snap_date_9)
        lookback = [h for h in hist_ex_today_9 if h.get("margin_balance") is not None]
        if len(lookback) >= 20:
            prev_margin = lookback[-20].get("margin_balance")
            if prev_margin and prev_margin > 0:
                snap["margin_20d_change"] = round((margin - prev_margin) / prev_margin * 100, 2)

    # 10. 涨跌比N日均值（首次运行可能不足5日）
    # 注意：history 已含今日刚持久化的行（snapshot→_auto_persist→load_history），
    # 拼接前必须剔除今日，否则今日被双计（"5 日 MA"实际跨 4 个不同日、今日权重 40%）
    ad = snap.get("ad_ratio")
    snap_date = snap.get("date")
    hist_ex_today_v = hist_ex_today(history, snap_date)
    recent_ad = [h.get("ad_ratio") for h in hist_ex_today_v[-4:]
                 if h.get("ad_ratio") is not None]
    if ad is not None:
        recent_ad.append(ad)
        snap["ad_ratio_5d_ma"] = round(sum(recent_ad) / len(recent_ad), 4)
        snap["_ad_ma_window"] = len(recent_ad)  # 实际窗口大小，用于标签

    # 11. 跌停家数20日分位（同上，剔除今日避免窗口偏移）
    ld = snap.get("limit_down_count")
    if ld is not None and len(hist_ex_today_v) >= 19:
        ld_history = [h.get("limit_down_count") for h in hist_ex_today_v[-19:]
                      if h.get("limit_down_count") is not None]
        ld_history.append(ld)
        if ld_history:
            # 含边界分位（<=、1 位小数），委托 skills/lib/stats
            snap["limit_down_20d_pct"] = percentile_rank_inclusive(
                ld_history, ld, round_to=1)


# ---------------------------------------------------------------------------
# 环境标签 v2（历史分位 + 趋势 + 交叉验证）
# ---------------------------------------------------------------------------

def _compute_labels_v2(snap: dict, history: list[dict]) -> None:
    """基于历史分位 + 趋势 + 交叉验证计算环境标签。"""

    # --- 杠杆标签 ---
    mtm = snap.get("margin_to_mcap")
    m20 = snap.get("margin_20d_change")
    if mtm is not None:
        # 60日分位（剔除 history 中今日已持久化行，与 _compute_tier2 同型防双计）
        snap_date = snap.get("date")
        hist_ex_today_v = hist_ex_today(history, snap_date)
        mtm_hist = [h.get("margin_to_mcap") for h in hist_ex_today_v
                    if h.get("margin_to_mcap") is not None]
        if len(mtm_hist) >= 20:
            mtm_hist.append(mtm)
            sorted_mtm = sorted(mtm_hist)
            pct = sum(1 for x in sorted_mtm if x <= mtm) / len(sorted_mtm) * 100
            if pct < 10:
                pos = "冰点"
            elif pct < 30:
                pos = "偏低"
            elif pct <= 70:
                pos = "正常"
            elif pct <= 90:
                pos = "偏高"
            else:
                pos = "危险"
        else:
            pos = "中性" if 2 <= mtm <= 5 else ("偏低" if mtm < 2 else "偏高")

        # 趋势
        if m20 is not None:
            if m20 > 5:
                trend = " 🟢 加杠杆中"
            elif m20 < -5:
                trend = " 🧊 去杠杆中"
            else:
                trend = ""
        else:
            trend = ""

        snap["label_leverage"] = (
            f"两融/市值 {mtm:.2f}%，{pos}{trend}"
        )

    # --- 广度标签 ---
    ad = snap.get("ad_ratio")
    ad5 = snap.get("ad_ratio_5d_ma")
    if ad is not None:
        # 60日分位（剔除 history 中今日已持久化行——snapshot→_auto_persist→
        # load_history 已含今日，双计会抬高约 2/(N+1) 个分位点、在 10/30/70/90
        # 边界翻转冷暖标签；与杠杆分位/_compute_tier2 同型防双计，review #6）
        snap_date = snap.get("date")
        hist_ex_today_v = hist_ex_today(history, snap_date)
        ad_hist = [h.get("ad_ratio") for h in hist_ex_today_v
                   if h.get("ad_ratio") is not None]
        if len(ad_hist) >= 20:
            ad_hist.append(ad)
            sorted_ad = sorted(ad_hist)
            pct = sum(1 for x in sorted_ad if x <= ad) / len(sorted_ad) * 100
            if pct < 10:
                pos = "极冷"
            elif pct < 30:
                pos = "偏冷"
            elif pct <= 70:
                pos = "正常"
            elif pct <= 90:
                pos = "偏暖"
            else:
                pos = "极热"
        else:
            if ad < 0.6:
                pos = "极冷"
            elif ad < 0.8:
                pos = "偏冷"
            elif ad <= 1.5:
                pos = "正常"
            elif ad <= 2.0:
                pos = "偏暖"
            else:
                pos = "极热"

        n_days = snap.get("_ad_ma_window", 5)
        ad5_str = f"，{n_days}日均值 {ad5:.2f}" if ad5 is not None else ""
        snap["label_breadth"] = f"涨跌比 {ad:.2f}，{pos}{ad5_str}"

    # --- 情绪标签 ---
    lr = snap.get("lu_ld_ratio")
    ld = snap.get("limit_down_count")
    ld_pct = snap.get("limit_down_20d_pct")
    no_dn = snap.get("lu_ld_note") == "no_limit_down"

    if lr is not None or no_dn:
        # 交叉验证：涨跌比正常但涨跌停比极端 → 背离
        ad_normal = ad is not None and 0.8 <= ad <= 1.5

        if no_dn:
            if ad_normal:
                snap["label_sentiment"] = "⚠️ 极端看多背离（无跌停，但涨跌比正常→权重股拉偏）"
            else:
                snap["label_sentiment"] = "🔥 极端亢奋（无跌停）"
        elif ld is not None and ld > 50:
            snap["label_sentiment"] = f"🚨 局部恐慌（跌停{ld}家" + (
                f"，20日分位{ld_pct:.0f}%）" if ld_pct is not None else "）")
        elif lr is not None and lr > _LU_LD_EXTREME_UP:
            if ad_normal:
                snap["label_sentiment"] = f"⚠️ 极端看多背离（涨跌停比{lr:.1f}:1，涨跌比{ad:.2f}正常→指数失真）"
            else:
                snap["label_sentiment"] = f"🔥 极端亢奋（涨跌停比{lr:.1f}:1" + (
                    f"，跌停20日分位{ld_pct:.0f}%" if ld_pct is not None else "") + "）"
        elif lr is not None and lr < 0.25:
            snap["label_sentiment"] = f"😱 恐慌（涨跌停比{lr:.2f}:1）"
        elif lr is not None and lr < 0.6:
            snap["label_sentiment"] = f"偏冷（涨跌停比{lr:.2f}:1）"
        elif lr is not None and lr <= 3.0:
            snap["label_sentiment"] = f"正常（涨跌停比{lr:.1f}:1）"
        elif lr is not None:
            snap["label_sentiment"] = f"偏热（涨跌停比{lr:.1f}:1）"

    # --- 资金面标签（北向 + 股指期货双视角；v0.2.6 F 系列扩展） ---
    nb_net = snap.get("northbound_net_inflow")
    nb_dir = snap.get("northbound_direction")
    nb_mv = snap.get("northbound_market_value")
    parts: list[str] = []
    if nb_mv is not None:
        mv_str = f"持股市值 {nb_mv:.0f}亿"
        if nb_net is not None and nb_dir is not None:
            parts.append(f"北向 {nb_dir} {abs(nb_net):.0f}亿（季度环比，{mv_str}）")
        else:
            parts.append(f"北向 {mv_str}（季度快照，日频不可得）")
    else:
        parts.append("北向数据暂不可用")
    basis = snap.get("futures_basis_pct")
    if basis is not None:
        parts.append(f"IC 基差 {basis:+.2f}%")
    snap["label_capital_flow"] = "；".join(parts)

    # --- 综合环境标签（JSON，供 journal 注入） ---
    env = {
        "leverage": snap.get("label_leverage"),
        "breadth": snap.get("label_breadth"),
        "sentiment": snap.get("label_sentiment"),
        "capital_flow": snap.get("label_capital_flow"),
    }
    # 综合判断
    warnings = []
    if "去杠杆" in str(env["leverage"] or ""):
        warnings.append("去杠杆")
    if "背离" in str(env["sentiment"] or ""):
        warnings.append("情绪背离")
    if "恐慌" in str(env["sentiment"] or ""):
        warnings.append("恐慌")
    if "极冷" in str(env["breadth"] or ""):
        warnings.append("广度极冷")
    if nb_dir == "流出" and nb_net is not None and nb_net < -20:
        warnings.append("外资大幅流出")

    env["summary"] = "偏谨慎" if len(warnings) >= 2 else (
        "⚠️ " + " + ".join(warnings) if len(warnings) == 1 else "正常"
    )
    snap["env_label"] = json.dumps(env, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 兼容层：v0.2.1 旧接口（使用当日绝对值标签）
# ---------------------------------------------------------------------------

def _compute_labels(result: dict) -> None:
    """v0.2.1 兼容：基于当日绝对值 + 固定阈值的启发式标签。

    由 snapshot() 调用以保持向后兼容；save_snapshot() 路径走 _compute_labels_v2。
    """
    # 杠杆标签
    margin = result.get("margin_balance")
    if margin is not None:
        buy = result.get("margin_buy_amount")
        if margin > 30000:
            base = "高杠杆"
        elif margin < 12000:
            base = "冰点"
        else:
            base = "中性"

        if buy is not None and margin > 0:
            pct = buy / margin
            if pct > 0.08:
                result["label_leverage"] = f"{base} 偏热"
            elif pct < 0.03:
                if base == "中性":
                    result["label_leverage"] = "中性 去杠杆"
                else:
                    result["label_leverage"] = f"{base} 偏冷"
            else:
                result["label_leverage"] = base
        else:
            result["label_leverage"] = base

    # 广度标签
    ad = result.get("ad_ratio")
    if ad is not None:
        if ad < 0.6:
            result["label_breadth"] = "极冷"
        elif ad < 0.8:
            result["label_breadth"] = "偏冷"
        elif ad <= 1.5:
            result["label_breadth"] = "正常"
        elif ad <= 2.0:
            result["label_breadth"] = "偏暖"
        else:
            result["label_breadth"] = "极热"

    # 情绪标签
    lr = result.get("lu_ld_ratio")
    ld = result.get("limit_down_count")
    if lr is not None or result.get("lu_ld_note") == "no_limit_down":
        if ld is not None and ld > 50:
            result["label_sentiment"] = "局部恐慌"
        elif _is_extreme_sentiment_up(result):
            result["label_sentiment"] = "极端亢奋"
        elif lr is not None and lr < 0.25:
            result["label_sentiment"] = "恐慌"
        elif lr is not None and lr < 0.6:
            result["label_sentiment"] = "偏冷"
        elif lr is not None and lr <= 3.0:
            result["label_sentiment"] = "正常"
        elif lr is not None:
            result["label_sentiment"] = "偏热"

    # 资金面标签（v0.2.2；v0.2.6 F 系列扩展：北向 + 股指期货双视角）
    nb_net = result.get("northbound_net_inflow")
    nb_dir = result.get("northbound_direction")
    nb_mv = result.get("northbound_market_value")
    parts: list[str] = []
    if nb_mv is not None:
        mv_str = f"持股市值 {nb_mv:.0f}亿"
        if nb_net is not None and nb_dir is not None:
            parts.append(f"北向 {nb_dir} {abs(nb_net):.0f}亿（季度环比，{mv_str}）")
        else:
            parts.append(f"北向 {mv_str}（季度快照，日频不可得）")
    basis = result.get("futures_basis_pct")
    if basis is not None:
        parts.append(f"IC 基差 {basis:+.2f}%")
    if parts:
        result["label_capital_flow"] = "；".join(parts)


# ---------------------------------------------------------------------------
# 采集函数
# ---------------------------------------------------------------------------

def _fetch_margin(result: dict) -> None:
    """两融余额 + 融资买入额（akshare stock_margin_account_info）。

    单位假设：akshare EastMoney RPTA_WEB_MARGIN_DAILYTRADE
    的 FIN_BALANCE / FIN_BUY_AMT 已是亿元（实测约 2.6e4）。
    """
    try:
        df = fetch_margin_account_info()
        if df is None or df.empty:
            result["_errors"].append("margin: empty response")
            return
        latest = df.iloc[-1]
        result["margin_balance"] = safe_float(latest.get("融资余额"))
        result["margin_buy_amount"] = safe_float(latest.get("融资买入额"))
    except Exception as exc:
        logger.warning("margin fetch failed: %s", exc)
        result["_errors"].append(f"margin: {exc}")


def _fetch_ad_ratio(result: dict) -> None:
    """涨跌比（akshare stock_market_activity_legu 快照）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_market_activity_legu()
        if df is None or df.empty:
            result["_errors"].append("ad_ratio: empty response")
            return
        kv = dict(zip(df["item"], df["value"]))
        up = safe_float(kv.get("上涨"))
        down = safe_float(kv.get("下跌"))
        if up is not None and down is not None and down > 0:
            result["ad_ratio"] = round(up / down, 4)
    except Exception as exc:
        logger.warning("ad_ratio fetch failed: %s", exc)
        result["_errors"].append(f"ad_ratio: {exc}")


def _fetch_limit_pools(result: dict) -> None:
    """涨跌停池：涨停数 + 跌停数。"""
    today = shanghai_today()
    # 涨停
    try:
        import akshare as ak
        with akshare_direct_session():
            df_up = ak.stock_zt_pool_em(date=today)
        if df_up is not None and not df_up.empty:
            result["limit_up_count"] = len(df_up)
    except Exception as exc:
        logger.warning("limit_up fetch failed: %s", exc)
        result["_errors"].append(f"limit_up: {exc}")

    # 跌停
    try:
        import akshare as ak
        with akshare_direct_session():
            df_dn = ak.stock_zt_pool_dtgc_em(date=today)
        if df_dn is not None and not df_dn.empty:
            result["limit_down_count"] = len(df_dn)
    except Exception as exc:
        logger.warning("limit_down fetch failed: %s", exc)
        result["_errors"].append(f"limit_down: {exc}")

    # 计算涨跌停比
    up = result.get("limit_up_count")
    dn = result.get("limit_down_count")
    if up is not None and dn is not None and dn > 0:
        result["lu_ld_ratio"] = round(up / dn, 4)
        result["lu_ld_note"] = None
    elif up is not None and dn is not None and dn == 0 and up == 0:
        # 涨跌停均为 0：非交易日或跌停池无数据，与"无数据"状态区分
        result["lu_ld_ratio"] = None
        result["lu_ld_note"] = "zero_both"
    elif up is not None and dn is not None and dn == 0 and up > 0:
        # 跌停数为 0（已成功获取），涨停 > 0 → 极端看多
        result["lu_ld_ratio"] = None
        result["lu_ld_note"] = "no_limit_down"
    # dn is None（API 失败）且 up 有值时：不设置 lu_ld_note，
    # 避免 API 失败被误判为"无跌停"极端看多信号


def _fetch_turnover(result: dict) -> None:
    """全市场成交额 + 流通市值（上交所 + 深交所）。

    数据源结构差异：
    - stock_sse_summary: key-value 格式（「项目」列含「流通市值」行），无成交金额列
    - stock_szse_summary: 列式格式（含 成交金额/流通市值 列），第一行为「股票」类别
    - 单位差异: SSE 流通市值已是亿元，SZSE 为元须 /1e8
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            sse = ak.stock_sse_summary()
            # stock_szse_summary 不传日期时 akshare 硬编码默认 '20240830'，
            # 会把流通市值/成交额冻结在 2 年前；必须显式传上海时区当日
            szse = ak.stock_szse_summary(date=shanghai_today())

        # --- 流通市值 ---
        # SSE: 按「项目」列查找「流通市值」行，「股票」列为数值（亿元）
        if sse is not None and not sse.empty and "项目" in sse.columns:
            sse_mcap_row = sse[sse["项目"] == "流通市值"]
            if not sse_mcap_row.empty:
                sse_mcap_raw = safe_float(sse_mcap_row.iloc[0].get("股票"))
                if sse_mcap_raw is not None:
                    result["sse_float_mcap"] = round(sse_mcap_raw, 2)  # 已是亿元

        # SZSE: 列式结构，「证券类别」=「股票」行的「流通市值」列（元）
        if szse is not None and not szse.empty:
            szse_stock = szse[szse["证券类别"] == "股票"]
            if not szse_stock.empty:
                szse_mcap_raw = safe_float(szse_stock.iloc[0].get("流通市值"))
                if szse_mcap_raw is not None:
                    result["szse_float_mcap"] = round(szse_mcap_raw / 1e8, 2)  # 元→亿

        # --- 成交额 ---
        # SSE 无成交额列；SZSE 有成交金额列（元）
        szse_amount = 0.0
        if szse is not None and not szse.empty:
            szse_stock = szse[szse["证券类别"] == "股票"]
            if not szse_stock.empty:
                szse_amount = safe_float(szse_stock.iloc[0].get("成交金额", 0)) or 0.0
        if szse_amount > 0:
            result["total_turnover"] = round(szse_amount / 1e8, 2)  # 元→亿
            # 注：仅含深交所成交额；上交所 stock_sse_summary 不提供成交金额，
            # 实际全市场成交额约为该值的 1.8-2.2 倍。
            # 融资买入/成交额 比率已除以估算全市场值（×1.9），避免被夸大 ~2x。
            result["total_turnover_est"] = round(szse_amount / 1e8 * 1.9, 2)
            result["total_turnover_note"] = "仅深交所；全市场估算 = 深交所 × 1.9"
    except Exception as exc:
        logger.warning("turnover fetch failed: %s", exc)
        result["_errors"].append(f"turnover: {exc}")


# ---------------------------------------------------------------------------
# Tier 3 — ERP / PCR / 破净率
# ---------------------------------------------------------------------------

def _fetch_erp(result: dict) -> None:
    """ERP = 1/HS300 PE − 10Y 国债收益率。

    HS300 PE 优先 Tushare index_dailybasic，降级 akshare stock_zh_index_value_csindex。
    10Y 收益率优先 FRED DGS10，降级 akshare bond_zh_us_rate。
    """
    try:
        import akshare as ak
        from lib import env as _env

        # HS300 PE
        pe_hs300 = None
        config = _env.get_config()
        if _env.is_tushare_available(config):
            try:
                from lib.tushare_client import TushareClient
                tc = TushareClient(token=config.get("TUSHARE_TOKEN"))
                # 查询当日 trade_date；非上海时区主机在上海 00:00-08:00 窗口可能为空
                # （naive→上海时区统一的有意变更）
                today_str = shanghai_today()
                df = tc.query("index_dailybasic", ts_code="000300.SH",
                              trade_date=today_str)
                if df is not None and not df.empty and "pe_ttm" in df.columns:
                    pe_hs300 = safe_float(df.iloc[-1].get("pe_ttm"))
            except Exception as exc:
                logger.warning("erp: Tushare HS300 PE unavailable, falling back to akshare: %s", exc)

        if pe_hs300 is None:
            with akshare_direct_session():
                df = ak.stock_zh_index_value_csindex(symbol="000300")
            if df is not None and not df.empty:
                pe_hs300 = safe_float(df.iloc[-1].get("市盈率1"))

        if pe_hs300 is None or pe_hs300 <= 0:
            result["_errors"].append("erp: HS300 PE unavailable")
            return

        # 10Y 收益率
        y10 = None
        if _env.is_fred_available(config):
            try:
                from lib.macro import _fetch_fred_series as _fred
                dgs10 = _fred("DGS10", config)
                # _fetch_fred_series returns (latest_value, [(date, value), ...])
                if dgs10 and dgs10[0] is not None:
                    y10 = dgs10[0]  # latest_value
            except Exception as exc:
                logger.warning("erp: FRED DGS10 unavailable, falling back to akshare: %s", exc)

        if y10 is None:
            with akshare_direct_session():
                df = ak.bond_zh_us_rate()
            if df is not None and not df.empty:
                # 列名即为收益率名称，如「中国国债收益率10年」
                col_10y = "中国国债收益率10年"
                if col_10y in df.columns:
                    y10 = safe_float(df.iloc[-1].get(col_10y))

        if y10 is None:
            result["_errors"].append("erp: 10Y yield unavailable")
            return

        ey = (1.0 / pe_hs300) * 100
        result["erp"] = round(ey - y10, 2)
    except Exception as exc:
        logger.warning("erp fetch failed: %s", exc)
        result["_errors"].append(f"erp: {exc}")


def _fetch_pcr(result: dict) -> None:
    """50ETF PCR（Put/Call Ratio）= 认沽成交量 ÷ 认购成交量。

    优先 Tushare opt_basic + opt_daily（5000 分），降级为 None。
    先查 opt_basic 获取 50ETF 期权合约代码，再按 exchange 查 opt_daily。
    """
    try:
        from lib import env as _env
        config = _env.get_config()
        if not _env.is_tushare_available(config):
            result["_errors"].append("pcr: Tushare unavailable (5000 pts required)")
            return

        from lib.tushare_client import TushareClient
        tc = TushareClient(token=config.get("TUSHARE_TOKEN"))
        # 查询当日 trade_date；非上海时区主机在上海 00:00-08:00 窗口可能为空
        # （naive→上海时区统一的有意变更）
        today_str = shanghai_today()

        # 先获取 50ETF 期权合约代码（opt_daily 的 ts_code 需为合约代码而非 ETF 代码）
        df_basic = tc.query("opt_basic", exchange="SSE", fields="ts_code,call_put,name")
        if df_basic is None or df_basic.empty:
            result["_errors"].append("pcr: opt_basic empty")
            return
        etf50 = df_basic[df_basic["name"].str.contains("50ETF", na=False)]
        if etf50.empty:
            result["_errors"].append("pcr: no 50ETF option contracts in opt_basic")
            return
        put_codes = set(etf50[etf50["call_put"] == "P"]["ts_code"])
        call_codes = set(etf50[etf50["call_put"] == "C"]["ts_code"])

        # 按 exchange 查询当日所有 SSE 期权数据，再按合约代码过滤
        df = tc.query("opt_daily", trade_date=today_str, exchange="SSE")
        if df is None or df.empty:
            result["_errors"].append("pcr: opt_daily empty")
            return

        puts = df[df["ts_code"].isin(put_codes)]
        calls = df[df["ts_code"].isin(call_codes)]
        if puts.empty or calls.empty:
            result["_errors"].append("pcr: no C/P data for 50ETF")
            return

        put_vol = puts["vol"].sum()
        call_vol = calls["vol"].sum()
        if call_vol > 0:
            result["pcr"] = round(put_vol / call_vol, 4)
    except Exception as exc:
        logger.warning("pcr fetch failed: %s", exc)
        result["_errors"].append(f"pcr: {exc}")


def _fetch_below_book_pct(result: dict) -> None:
    """破净率：PB < 1 的个股占比。

    采样全部 A 股（Tushare daily_basic 全量 pb 字段，需 ≥2000 Tushare 积分）。
    """
    try:
        from lib import env as _env
        config = _env.get_config()
        if not _env.is_tushare_available(config):
            result["_errors"].append("below_book: Tushare unavailable")
            return

        from lib.tushare_client import TushareClient
        tc = TushareClient(token=config.get("TUSHARE_TOKEN"))
        # 查询当日 trade_date；非上海时区主机在上海 00:00-08:00 窗口可能为空
        # （naive→上海时区统一的有意变更）
        today_str = shanghai_today()
        df = tc.query("daily_basic", trade_date=today_str)
        if df is None or df.empty or "pb" not in df.columns:
            result["_errors"].append("below_book: daily_basic empty")
            return

        pb = df["pb"].dropna()
        if len(pb) == 0:
            return
        below = (pb < 1.0).sum()
        result["below_book_pct"] = round(below / len(pb) * 100, 2)
    except Exception as exc:
        msg = str(exc)
        if "权限" in msg or "permission" in msg.lower() or "点" in msg or "积分" in msg:
            hint = "（daily_basic 全量查询需 ≥2000 Tushare 积分）"
        else:
            hint = ""
        logger.warning("below_book fetch failed: %s%s", exc, f" {hint}" if hint else "")
        result["_errors"].append(f"below_book: {exc}{hint}")


def _fetch_futures(result: dict) -> None:
    """股指期货基差（v0.2.6 F 系列）——机构对冲成本状态度量，非预测。

    读 futures_daily 最新一行（IC 为主品种，代表中小盘对冲成本）。
    注：持仓量 20 日变化已由 F3 实证裁定为展期节奏主导（不可刻画持仓
    状态），本修订起不再输出到用户标签；字段与 compound_oi_change
    helper 保留于数据层供后续研究。
    """
    try:
        from lib import store as _store  # noqa: E402 — 惰性导入
        _store.init_db()
        rows = _store.load_futures_daily(symbol="IC", limit=21)
        if not rows:
            return
        latest = rows[-1]
        if latest.get("basis_pct") is not None:
            result["futures_basis_pct"] = latest["basis_pct"]
    except Exception:  # noqa: BLE001 — 单维度失败不阻塞
        result["_errors"].append("futures 读取失败（降级：资金面缺期货维度）")


def _fetch_northbound(result: dict) -> None:
    """北向资金市场级（沪股通 + 深股通合计）。

    数据源：akshare ``stock_hsgt_hist_em``。

    注意：
    - ``当日成交净买额`` 自 2024-08-19 起恒为 NaN（交易所停止实时披露）
    - ``持股市值`` 仅在季度末有非零值，日频数据为 0
    - 因此方向判断依赖最近两次季度快照的持股市值变动

    单位：akshare 返回元，统一转换为亿元。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_hsgt_hist_em()
        if df is None or df.empty:
            result["_errors"].append("northbound: empty response")
            return

        # 过滤持股市值 >0 的行（仅季度末有数据）
        mv_col = "持股市值"
        valid = df[df[mv_col].notna() & (df[mv_col] > 0)]
        if valid.empty:
            result["_errors"].append("northbound: no valid 持股市值 records")
            return

        # 最近一次季度快照
        latest_q = valid.iloc[-1]
        mv_current = safe_float(latest_q.get(mv_col))
        if mv_current is not None:
            result["northbound_market_value"] = round(mv_current / 1e8, 2)

        # 与上一次季度快照比较方向
        if len(valid) >= 2:
            prev_q = valid.iloc[-2]
            mv_prev = safe_float(prev_q.get(mv_col))
            if mv_current is not None and mv_prev is not None and mv_prev > 0:
                change = (mv_current - mv_prev) / 1e8
                result["northbound_net_inflow"] = round(change, 2)
                result["northbound_source"] = "quarterly"
                if change > 100:        # 阈值避免噪音
                    result["northbound_direction"] = "流入"
                elif change < -100:
                    result["northbound_direction"] = "流出"
                else:
                    result["northbound_direction"] = None
            else:
                result["northbound_source"] = "unavailable"
        else:
            result["northbound_source"] = "unavailable"
    except Exception as exc:
        logger.warning("northbound fetch failed: %s", exc)
        result["_errors"].append(f"northbound: {exc}")


# ---------------------------------------------------------------------------
# 环境护栏 v1（确定���规则，只追加 blind_spots，不改写评级，不输出仓位数字）
# ---------------------------------------------------------------------------

def _is_extreme_sentiment_up(snap: dict) -> bool:
    """涨跌停比极端亢奋：ratio > 5 或无跌停。"""
    if snap.get("lu_ld_note") == "no_limit_down":
        return True
    lr = snap.get("lu_ld_ratio")
    if lr is None:
        return False
    try:
        return float(lr) > _LU_LD_EXTREME_UP
    except (ValueError, TypeError):
        return False


def apply_env_guardrail(evaluation_json: dict, snap: dict | None = None) -> dict:
    """在评估结果上追加环境盲点提示。

    3 条 v1 规则：
    1. 去杠杆趋势 → 追加"流动性收紧"盲点
    2. 涨跌停比 >5:1 或 <1:5 → 追加"情绪回归"盲点
    3. 涨跌比 <0.6 → 追加"指数失真"盲点

    不改写 dimensions.*.level，不输出仓位/买卖建议数字。
    """
    if snap is None:
        return evaluation_json

    blind_spots: list[dict] = evaluation_json.get("blind_spots", [])
    if not isinstance(blind_spots, list):
        blind_spots = []

    # 规则 1：去杠杆趋势
    label_lev = snap.get("label_leverage", "")
    if "偏冷" in str(label_lev) or "去杠杆" in str(label_lev):
        blind_spots.append({
            "rule": "deleveraging",
            "note": (
                "融资余额处于偏低水平或呈下降趋势"
                "——你的假设是否纳入了去杠杆环境下流动性收紧的可能？"
            ),
        })

    # 规则 2：涨跌停比极端
    if _is_extreme_sentiment_up(snap):
        lr = snap.get("lu_ld_ratio")
        if snap.get("lu_ld_note") == "no_limit_down":
            ratio_desc = "无跌停（涨停>0）"
        else:
            try:
                ratio_desc = f"{float(lr):.1f}:1"
            except (TypeError, ValueError):
                ratio_desc = "极端"
        blind_spots.append({
            "rule": "extreme_sentiment_up",
            "note": (
                f"涨跌停比 {ratio_desc}，处于极端亢奋区间"
                "——情绪回归均值时，你的入场价可能包含情绪溢价。"
            ),
        })
    else:
        lr = snap.get("lu_ld_ratio")
        if lr is not None:
            try:
                lr_val = float(lr)
                if lr_val < _LU_LD_EXTREME_DOWN:
                    blind_spots.append({
                        "rule": "extreme_sentiment_down",
                        "note": (
                            f"涨跌停比 {lr_val:.2f}:1，处于恐慌区间"
                            "——跌停潮下部分标的可能无法成交，名义仓位 ≠ 可退出仓位。"
                        ),
                    })
            except (ValueError, TypeError):
                pass

    # 规则 3：涨跌比 <0.6
    ad = snap.get("ad_ratio")
    if ad is not None and ad < 0.6:
        blind_spots.append({
            "rule": "market_breadth",
            "note": (
                f"涨跌比 {ad:.2f}，大多数股票在跌"
                "——指数可能被权重股拉偏，你的标的真实跌幅可能更大。"
            ),
        })

    evaluation_json["blind_spots"] = blind_spots
    return evaluation_json


def zt_industry_flow(days: int = 10, return_daily: bool = False) -> dict[str, Any]:
    """涨停行业轮动（近 N 交易日东财涨停池按行业聚合）。

    分析时按需调用的补充材料（invest-a-pulse 极端情绪维度），**不进入
    snapshot() 常规输出**。东财不可用时 available=False 降级，不阻断。

    return_daily=True 时额外返回 daily（{日期: {行业: 涨停数}}），
    供板块簇相关性/跷跷板检验等二次分析复用。

    返回：
    - covered_dates: 实际覆盖交易日（YYYYMMDD，非交易日/失败日跳过）
    - latest_date / latest_total: 最新交易日及涨停家数
    - top5: 最新日涨停家数 Top5（并列按 N 日累计），含 n_day_first_half/
      n_day_second_half 前后半段拆分（轮动方向判断用）
    - top5_share_pct: Top5 合计占当日涨停总数 %（Python 计算）
    - industry_trend: 全行业 N 日聚合 {行业: {count, days_active, ...}}
    - daily: return_daily=True 时返回 {日期: {行业: 涨停数}}
    """
    result: dict[str, Any] = {
        "available": False,
        "window_days": days,
        "covered_dates": [],
        "latest_date": None,
        "latest_total": None,
        "top5": [],
        "top5_share_pct": None,
        "industry_trend": {},
        "_errors": [],
    }
    try:
        import akshare as ak
        today = shanghai_today()
        with akshare_direct_session():
            cal = ak.tool_trade_date_hist_sina()
        cal_dates = [str(d).replace("-", "") for d in cal["trade_date"]]
        dates = [d for d in cal_dates if d <= today][-days:]

        daily: dict[str, dict[str, int]] = {}
        for d in dates:
            try:
                with akshare_direct_session():
                    df = ak.stock_zt_pool_em(date=d)
                if df is None or df.empty or "所属行业" not in df.columns:
                    result["_errors"].append(f"{d}: empty or no industry col")
                    continue
                daily[d] = df.groupby("所属行业").size().to_dict()
            except Exception as exc:
                logger.warning("zt_industry_flow %s fetch failed: %s", d, exc)
                result["_errors"].append(f"{d}: {type(exc).__name__}")

        if not daily:
            return result

        covered = sorted(daily)
        result["available"] = True
        result["covered_dates"] = covered
        latest = covered[-1]
        result["latest_date"] = latest
        latest_counts = daily[latest]
        result["latest_total"] = sum(latest_counts.values())

        # 全行业聚合 + 前后半段拆分（轮动方向）
        n = len(covered)
        mid = n // 2
        first_half = covered[:mid] or [covered[0]]
        second_half = covered[mid:]
        all_inds = {ind for dd in daily.values() for ind in dd}
        trend: dict[str, Any] = {}
        for ind in all_inds:
            cnt = [daily[dd].get(ind, 0) for dd in covered]
            trend[ind] = {
                "count": sum(cnt),
                "days_active": sum(1 for c in cnt if c > 0),
                "first_half": sum(daily[dd].get(ind, 0) for dd in first_half),
                "second_half": sum(daily[dd].get(ind, 0) for dd in second_half),
                "latest": latest_counts.get(ind, 0),
            }
        result["industry_trend"] = trend

        # Top5：最新日涨停数降序，并列按 N 日累计
        top = sorted(latest_counts.items(),
                     key=lambda kv: (-kv[1], -trend[kv[0]]["count"]))[:5]
        result["top5"] = [
            {"industry": ind, "count": c,
             "n_day_total": trend[ind]["count"],
             "n_day_days_active": trend[ind]["days_active"],
             "n_day_first_half": trend[ind]["first_half"],
             "n_day_second_half": trend[ind]["second_half"]}
            for ind, c in top
        ]
        if result["latest_total"]:
            result["top5_share_pct"] = round(
                sum(x["count"] for x in result["top5"]) / result["latest_total"] * 100, 1)

        if return_daily:
            result["daily"] = daily
    except Exception as exc:
        logger.warning("zt_industry_flow failed: %s", exc)
        result["_errors"].append(f"fatal: {type(exc).__name__}: {exc}")
    return result


# 板块簇映射（东财涨停池「所属行业」归并，研究假设非官方分类）
_ZT_CLUSTERS: dict[str, tuple[str, ...]] = {
    "电子/AI算力": ("元件", "半导体", "通信设备", "消费电子", "光学光电", "其他电子", "电子化学", "计算机设", "军工电子"),
    "医药": ("医疗服务", "化学制药", "生物制品", "中药Ⅱ", "医疗器械", "医药商业"),
    "TMT软/传媒": ("软件开发", "IT服务Ⅱ", "数字媒体", "互联网电", "游戏Ⅱ", "出版", "广告营销", "影视院线", "通信服务", "教育"),
    "电力设备/新能源": ("电网设备", "电池", "光伏设备", "风电设备", "电机Ⅱ", "其他电源"),
    "资源/周期": ("贵金属", "小金属", "工业金属", "煤炭开采", "普钢", "特钢Ⅱ", "化学原料", "化学制品", "化学纤维",
                  "炼化及贸", "水泥", "玻璃玻纤", "非金属材", "农化制品", "能源金属"),
    "地产链/建筑": ("房地产开", "房地产服", "装修装饰", "装修建材", "家居用品", "家电零部", "厨卫电器", "小家电",
                   "专业工程", "基础建设", "工程咨询", "房屋建设"),
    "消费": ("饮料乳品", "食品加工", "休闲食品", "白酒Ⅱ", "非白酒", "调味发酵", "农产品加", "养殖业", "种植业",
             "化妆品", "个护用品", "服装家纺", "纺织制造", "饰品", "酒店餐饮", "旅游及景", "一般零售",
             "文娱用品", "造纸", "包装印刷"),
    "机械/交运": ("通用设备", "专用设备", "自动化设", "工程机械", "环保设备", "汽车零部", "商用车", "乘用车",
                  "铁路公路", "物流", "航空装备", "航天装备", "地面兵装", "航海装备"),
    "公用事业": ("电力", "燃气Ⅱ"),
    "金融": ("证券Ⅱ", "多元金融"),
}

# Pearson 双尾 p<0.05 临界值查表（n → r_crit，保守取 ≤n 的最大临界）
_PEARSON_CRIT = ((10, 0.632), (12, 0.576), (15, 0.514), (18, 0.468), (20, 0.444),
                 (25, 0.396), (30, 0.361), (40, 0.316), (50, 0.279), (60, 0.254))


def _pearson_crit(n: int) -> float:
    for nn, c in _PEARSON_CRIT:
        if n <= nn:
            return c
    return _PEARSON_CRIT[-1][1]


def zt_seesaw(days: int = 30, min_days: int = 10) -> dict[str, Any]:
    """涨停热度板块簇跷跷板检验（占比 Pearson 相关 + 前后半段对比）。

    **参考内容，不构成投资决策**：描述资金在板块簇间的腾挪结构，
    帮助分析盘面强弱分化的来源，不输出任何方向性预测。

    基于 zt_industry_flow(days, return_daily=True) 的 daily 矩阵：
    - seesaw_pairs: 显著负相关对（|r| > 临界值，p<0.05 双尾），按 |r| 降序
    - sync_pairs: 显著正相关对（同步资金池）
    - half_split: 前后半段占比变化（Δpp），验证轮动方向
    - 占比口径（簇涨停家数/当日涨停总数），控制总量波动
    - 东财不可用或样本 <min_days 时 available=False 并说明原因
    """
    result: dict[str, Any] = {
        "available": False,
        "n_days": 0,
        "dates": [],
        "significance": None,
        "seesaw_pairs": [],
        "sync_pairs": [],
        "half_split": [],
        "_errors": [],
    }
    flow = zt_industry_flow(days=days, return_daily=True)
    if not flow.get("available") or not flow.get("daily"):
        result["_errors"] = flow.get("_errors", ["zt_industry_flow unavailable"])
        result["_errors"].append("东财涨停池不可用，跷跷板检验跳过")
        return result
    daily: dict[str, dict[str, int]] = flow["daily"]
    dates = sorted(daily)
    n = len(dates)
    result["n_days"] = n
    result["dates"] = dates
    if n < min_days:
        result["_errors"].append(f"样本不足: {n} 日 < {min_days}，跳过")
        return result

    total_by_date = {d: sum(daily[d].values()) for d in dates}
    cluster_names = list(_ZT_CLUSTERS)
    share = {
        c: [sum(daily[d].get(i, 0) for i in _ZT_CLUSTERS[c]) / total_by_date[d] * 100
            for d in dates]
        for c in cluster_names
    }

    def _pearson(x: list[float], y: list[float]) -> float:
        mx, my = sum(x) / n, sum(y) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
        sx = math.sqrt(sum((a - mx) ** 2 for a in x))
        sy = math.sqrt(sum((b - my) ** 2 for b in y))
        return cov / (sx * sy) if sx and sy else 0.0

    crit = _pearson_crit(n)
    result["significance"] = {"n": n, "r_crit": crit, "note": f"双尾 p<0.05，|r|>{crit} 为显著"}
    for i in range(len(cluster_names)):
        for j in range(i + 1, len(cluster_names)):
            a, b = cluster_names[i], cluster_names[j]
            r = _pearson(share[a], share[b])
            if r <= -crit:
                result["seesaw_pairs"].append({"a": a, "b": b, "r": round(r, 2)})
            elif r >= crit:
                result["sync_pairs"].append({"a": a, "b": b, "r": round(r, 2)})
    result["seesaw_pairs"].sort(key=lambda p: p["r"])
    result["sync_pairs"].sort(key=lambda p: -p["r"])

    # 前后半段占比均值差（Δpp）；share[c] 为按 dates 顺序的列表
    mid = n // 2
    h1, h2 = dates[:mid], dates[mid:]
    if h1 and h2:
        half = []
        for c in cluster_names:
            vals = share[c]
            m1 = sum(vals[:mid]) / len(h1)
            m2 = sum(vals[mid:]) / len(h2)
            half.append({"cluster": c, "first_half_share": round(m1, 1),
                         "second_half_share": round(m2, 1), "delta_pp": round(m2 - m1, 1)})
        half.sort(key=lambda x: -x["delta_pp"])
        result["half_split"] = {"first_half": [h1[0], h1[-1]], "second_half": [h2[0], h2[-1]],
                                "rows": half}

    result["available"] = True
    return result


# ---------------------------------------------------------------------------
# v0.2.5 D3 — 筹码出清度四信号 + 阶段判定（状态描述，非择时信号；不落库）
# ---------------------------------------------------------------------------

def compute_chip_clearance(
    snap: dict[str, Any] | None = None,
    history_days: int = 120,
    confirmation_window: int = 5,
) -> dict[str, Any]:
    """筹码出清度四信号 + 阶段判定（v0.2.5 D3）。状态描述，非择时信号。
    返回: date, available, stage, signals{deleveraging_pct, turnover_60d_pct,
    down_volume_days_30d, limit_down_20d_pct, days_since_margin_peak,
    confirmation, margin_20d_change}, calc_notes: list[str], _errors: list[str]
    """
    # Minor 5：confirmation_window 必须 > 0（传 0/负数时 hist_rows[-0:] 会扩至全历史）
    assert confirmation_window > 0, "confirmation_window 必须 > 0"
    result: dict[str, Any] = {
        "date": None,
        "available": False,
        "stage": None,
        "signals": {
            "deleveraging_pct": None,
            "turnover_60d_pct": None,
            "down_volume_days_30d": None,
            "limit_down_20d_pct": None,
            "days_since_margin_peak": None,
            "confirmation": None,
        },
        "calc_notes": [],
        "_errors": [],
    }
    signals = result["signals"]
    calc_notes = result["calc_notes"]

    # 当日快照：未显式传入时先走 data_bridge 缓存，缓存不可用降级直采
    # snapshot()（同 save_snapshot 的 L213-221 降级模式）
    if snap is None:
        try:
            from data_bridge import get_microstructure  # noqa: PLC0415
            snap = get_microstructure()
        except Exception:
            snap = None
        if snap is None:
            snap = snapshot()
    if not isinstance(snap, dict):
        snap = {}

    date = snap.get("date") or shanghai_today()
    result["date"] = date
    result["_errors"].extend(snap.get("_errors") or [])

    # 历史序列：剔除与当日同日的行防双计（snapshot→_auto_persist→
    # load_history 已含今日，同 _compute_tier2 模式）
    history = load_history(history_days)
    hist_rows = hist_ex_today(history, date)

    # --- 信号① 去杠杆幅度 + 信号④ 磨底时长（共用 margin 序列） ---
    # margin_peak = 剔今日 history 中 margin_balance 最大值
    margin_now = snap.get("margin_balance")
    margin_series = [h.get("margin_balance") for h in hist_rows
                     if h.get("margin_balance") is not None]

    def _days_since_peak(vals: list[float]) -> int:
        """距最近一次 margin 峰值的天数（峰值在最新一行 = 0，取末次峰值）。"""
        # 调用方已过滤 None/""/NaN（主路径 is not None、降级路径 notna+strip），
        # 不会出现 nan==nan 为 False 导致 next() StopIteration 的情况
        peak = max(vals)
        return next(i for i, v in enumerate(reversed(vals)) if v == peak)

    if len(margin_series) >= 20:
        margin_peak = max(margin_series)
        if margin_now is not None and margin_peak and margin_peak > 0:
            signals["deleveraging_pct"] = round(
                (margin_peak - margin_now) / margin_peak * 100, 2)
        # F7 口径统一：距峰值天数含今日（0 = 峰值在今天，主/SSE 两路径一致；
        # SSE 序列天然含今日，此处仅主路径 append）。deleveraging_pct 的
        # margin_peak 保持剔今日口径——新高日保留负值（高于旧峰值的扩张幅度）；
        # SSE 路径新高 → 0.0（序列含今日），跨路径差异为既有行为
        if margin_now is not None:
            signals["days_since_margin_peak"] = _days_since_peak(
                margin_series + [margin_now])
        else:
            signals["days_since_margin_peak"] = _days_since_peak(margin_series)
        if len(margin_series) < history_days:
            calc_notes.append(
                f"信号①：历史窗口不足 {history_days} 日（实际 {len(margin_series)} 行），"
                "峰值口径为近 N 日")
    else:
        # 历史 <20 行 → akshare stock_margin_sse 降级（SSE 口径，calc_notes 注明）
        try:
            import datetime as _dt
            import akshare as ak
            from dates import shanghai_now  # noqa: PLC0415
            # Minor 2：降级路径日期必须用上海时区（_dt.date.today() 为本地时区，
            # 日期边界可能与 A 股交易日不一致）
            _today = shanghai_now().date()
            _start = (_today - _dt.timedelta(days=history_days)).strftime("%Y%m%d")
            _end = _today.strftime("%Y%m%d")
            with akshare_direct_session():
                df = ak.stock_margin_sse(start_date=_start, end_date=_end)
            df = df.sort_values("信用交易日期")
            # I-1：SSE 原始数据可能出现 "" / NaN 空值行（akshare 侧已用
            # pd.to_numeric(errors="coerce") 防御，但字符串残留仍存在），转换前
            # 过滤，否则 float("") 抛 ValueError 使整个降级作废
            df = df[df["融资余额"].notna()
                    & (df["融资余额"].astype(str).str.strip() != "")]
            if df.empty:
                raise ValueError("stock_margin_sse 过滤后无有效融资余额行")
            margin_series = [float(v) / 1e8 for v in df["融资余额"]]  # 元 → 亿
            margin_peak = max(margin_series)
            margin_now = margin_series[-1]
            if margin_peak and margin_peak > 0:
                signals["deleveraging_pct"] = round(
                    (margin_peak - margin_now) / margin_peak * 100, 2)
            signals["days_since_margin_peak"] = _days_since_peak(margin_series)
            calc_notes.append(
                "信号①：历史 margin 序列 <20 行，降级 akshare stock_margin_sse"
                "（SSE 口径，与全市场口径存在差异）")
        except Exception as exc:
            result["_errors"].append(f"margin_fallback: {exc}")
            calc_notes.append(
                "信号①：历史 margin 序列 <20 行且 akshare stock_margin_sse"
                "降级失败，去杠杆信号不可用")
            margin_peak = None
            margin_series = []

    # --- 信号② 换手温度（60 日窗口含边界分位） ---
    # ⚠️ 口径硬约束（决策 D3-1）：用 total_turnover（深交所口径），
    # 不得用 total_turnover_est（×1.9 估算，不入库）
    turnover_now = snap.get("total_turnover")
    if turnover_now is not None:
        turnover_seq = [h.get("total_turnover") for h in hist_rows[-59:]
                        if h.get("total_turnover") is not None]
        turnover_seq.append(turnover_now)
        if len(turnover_seq) >= 20:
            signals["turnover_60d_pct"] = percentile_rank_inclusive(
                turnover_seq, turnover_now, round_to=1)
            if len(turnover_seq) < 60:
                calc_notes.append(
                    f"信号②：实际窗口不足 60 日（{len(turnover_seq)} 行），"
                    "分位口径为近 N 日")
        else:
            calc_notes.append(
                f"信号②：近 60 日换手序列不足 20 日（{len(turnover_seq)}），"
                "分位不可计算")
    else:
        calc_notes.append(
            "信号②：当日成交额缺失（深交所口径 total_turnover），换手分位不可计算")

    # --- 信号③ 割肉盘代理（近 30 日 ad_ratio<1.0 且放量 ≥ 同窗口中位数） ---
    # F3：窗口含今日（hist_ex_today 已剔 DB 今日行，snap 今日行补入，对称防双计）；
    # 仅今日有数据（len<2）时保持"数据不足"，防单样本中位数退化
    today_row = {"ad_ratio": snap.get("ad_ratio"),
                 "total_turnover": snap.get("total_turnover")}
    window30 = hist_rows[-29:] + [today_row]
    turn30 = [h.get("total_turnover") for h in window30
              if h.get("total_turnover") is not None]
    if len(turn30) < 2:
        signals["down_volume_days_30d"] = None
        calc_notes.append("信号③：近 30 日窗口无有效成交额数据，割肉盘计数不可计算")
    else:
        med30 = statistics.median(turn30)
        signals["down_volume_days_30d"] = sum(
            1 for h in window30
            if h.get("ad_ratio") is not None and h.get("ad_ratio") < 1.0
            and h.get("total_turnover") is not None
            and h.get("total_turnover") >= med30)
        if len(window30) < 30:
            calc_notes.append(f"信号③：近 30 日窗口实际 {len(window30)} 行")

    # limit_down_20d_pct：复用 snap 已有值，否则按 _compute_tier2 步骤 11 逻辑
    if snap.get("limit_down_20d_pct") is not None:
        signals["limit_down_20d_pct"] = snap.get("limit_down_20d_pct")
    elif snap.get("limit_down_count") is not None and len(hist_rows) >= 19:
        ld_seq = [h.get("limit_down_count") for h in hist_rows[-19:]
                  if h.get("limit_down_count") is not None]
        ld_seq.append(snap.get("limit_down_count"))
        if ld_seq:
            signals["limit_down_20d_pct"] = percentile_rank_inclusive(
                ld_seq, snap.get("limit_down_count"), round_to=1)

    # --- 信号④ 企稳确认（最近 confirmation_window 日内放量上涨日，含今日） ---
    # 窗口 = 前 (confirmation_window-1) 个历史交易日 + 今日（F3：反转日当天
    # 的放量上涨必须能置位 confirmation；第 confirmation_window+1 日不算）
    win = ([today_row] if confirmation_window <= 1
           else hist_rows[-(confirmation_window - 1):] + [today_row])
    evaluable = any(
        h.get("ad_ratio") is not None and h.get("total_turnover") is not None
        for h in win)
    if len(turn30) < 2 or not evaluable:
        signals["confirmation"] = None
        calc_notes.append("信号④：企稳确认窗口数据不足，无法判定")
    else:
        signals["confirmation"] = any(
            h.get("ad_ratio") is not None and h.get("ad_ratio") >= 2.0
            and h.get("total_turnover") is not None
            and h.get("total_turnover") >= med30
            for h in win)
        calc_notes.append(
            "信号④：企稳确认 = 从业者惯例代理（ad_ratio≥2.0 且放量≥30日中位数），"
            "非严格 90% Upside Day")

    # --- 阶段判定（状态描述，无动作词） ---
    # margin_20d_change 复用 snap 已有值，否则按 _compute_tier2 步骤 9 逻辑计算
    m20 = snap.get("margin_20d_change")
    if m20 is None and margin_now is not None and len(margin_series) >= 20:
        prev_margin = margin_series[-20]
        if prev_margin and prev_margin > 0:
            m20 = round((margin_now - prev_margin) / prev_margin * 100, 2)
    # F13：m20 已计算 → 输出到 signals，供主线资金流确认引用
    if m20 is not None:
        signals["margin_20d_change"] = m20

    # I-2：margin 维度可用 = 必须含今日 margin（deleveraging_pct 可计算）。
    # days_since_margin_peak 仅由剔今日历史得出，单独存在不足以支撑阶段判定 —
    # 今日快照全缺失 + 足量历史时，不得凭历史峰值位置断言"磨底/去杠杆"
    margin_dim = signals["deleveraging_pct"] is not None
    if not margin_dim and signals["turnover_60d_pct"] is None:
        result["stage"] = "数据不足"
    elif signals["confirmation"] is True:
        if not margin_dim:
            calc_notes.append(
                "信号④：margin 维度缺失，企稳确认仅基于换手/涨跌比证据")
        result["stage"] = "企稳确认"
    elif m20 is not None and m20 < -1:
        result["stage"] = "去杠杆中"
    elif (margin_now is not None and margin_peak is not None
          and margin_now < margin_peak
          and margin_now < max(margin_series[-5:] or [0])):
        # F1：今日 margin 低于历史峰值且低于自身近 5 日水平 = 下行延续
        # （含峰值刚落急跌：峰值在近 5 日内 + 今日大幅低于峰值 → 去杠杆中；
        # 长期平底低于旧峰值时 max(近5日)==now，落入磨底中）
        result["stage"] = "去杠杆中"
    elif not margin_dim:
        # I-2 补全：margin 维度缺失但换手在场时，不得凭换手断言磨底
        # （零 margin 证据的正面对底断言）
        result["stage"] = "数据不足"
    else:
        result["stage"] = "磨底中"

    result["available"] = result["stage"] != "数据不足"
    return result
