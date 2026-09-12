#!/usr/bin/env python3
"""数据接口冒烟检查（v0.1，2026-09-08）。

分层：
- L1（默认）：akshare 接口存在性检查（hasattr，零网络，秒级）——清单 70 项——检测版本漂移。
  接口清单与 skills/lib/references/data-interface-map.md 同步维护（数量以运行期打印的
  `len(AK_INTERFACES)` 为准，不手写）；**每个清单项都必须在地图内有登记行**，
  由 `skills/lib/tests/test_v030_doc_checks.py::test_data_interface_map_covers_smoke_l1` 守卫。
- L2（--live）：精选接口真实调用（网络）——检测环境/反爬/权限问题。

用法：
    uv run python scripts/smoke_interfaces.py            # L1
    uv run python scripts/smoke_interfaces.py --live     # L1 + L2

输出头部含 akshare/tushare 版本，留存输出即可对照「版本 vs 可用性」。
退出码：L1 存在性缺失 > 0；L2 失败仅报告不阻断（环境敏感项属预期）。
"""

from __future__ import annotations

import argparse
import sys
import time

# --- L1 清单：与 data-interface-map.md §A 同步（akshare 1.18.64 实测基线） ---
AK_INTERFACES: list[str] = [
    # A1 行情/K线/日历
    "stock_zh_a_hist", "stock_zh_a_spot_em", "stock_zh_index_daily",
    "stock_zh_index_daily_em", "stock_zh_index_value_csindex",
    "tool_trade_date_hist_sina", "index_stock_cons", "index_stock_cons_sina",
    "stock_individual_info_em", "stock_zh_a_daily",
    # A2 板块/行业
    "index_hist_sw", "index_analysis_weekly_sw",
    "sw_index_first_info", "sw_index_second_info", "sw_index_third_info",
    "stock_board_industry_name_em", "stock_board_industry_cons_em",
    "stock_board_industry_hist_em", "stock_industry_pe_ratio_cninfo",  # 1.18.64 改名（旧名已移除，见 data-interface-map E 节）
    # A3 涨停/情绪/概况
    "stock_zt_pool_em", "stock_zt_pool_dtgc_em", "stock_market_activity_legu",
    "stock_sse_summary", "stock_szse_summary", "stock_margin_account_info",
    # A4 杠杆/资金
    "stock_margin_sse", "stock_hsgt_hist_em", "stock_hsgt_individual_em",
    "stock_fund_flow_industry",
    "stock_hsgt_fund_flow_summary_em",  # 南向当日汇总（invest-hk-stock）；见 map A4 语义未核注记
    # A5 龙虎榜
    "stock_lhb_detail_em", "stock_lhb_detail_daily_sina", "stock_lhb_stock_detail_em",
    # A6 股东/高管/解禁
    "stock_shareholder_change_ths", "stock_gdfx_top_10_em",
    "stock_hold_management_detail_cninfo", "stock_restricted_release_queue_em",
    "stock_restricted_release_summary_em", "stock_info_a_code_name",
    # A7 分红
    "stock_dividend_cninfo", "stock_history_dividend_detail",
    # A8 财务
    "stock_financial_abstract_ths",
    # A9 宏观
    "macro_china_pmi", "macro_china_cpi", "macro_china_ppi", "macro_china_lpr",
    "macro_china_money_supply", "macro_rmb_loan", "bond_china_yield", "bond_zh_us_rate",
    "news_economic_baidu",  # v3 宏观日程（invest-a-event-calendar；能返回未来日程）
    "currency_boc_sina",  # 中行外汇牌价（A/H 比价汇率；⚠️ 每 100 港元计价，须显式传日期区间）
    # A10 新闻/公告/研报
    "stock_notice_report", "stock_individual_notice_report", "stock_news_em",
    "stock_research_report_em",
    # A11 ETF/基金
    "fund_etf_spot_em", "fund_etf_category_sina", "fund_etf_fund_info_em",
    "fund_open_fund_info_em", "fund_portfolio_industry_allocation_em",
    # A12 期货
    "futures_main_sina", "futures_spot_price",
    # A13 港股
    "stock_financial_hk_analysis_indicator_em", "stock_hk_valuation_baidu",
    # D 技能内联（进 L1 防漂移）
    "stock_index_pe_lg", "stock_market_pb_lg", "stock_market_pe_lg", "stock_index_pb_lg",
    "stock_margin_szse",
]

# --- L2 精选实探：调用形式须经过实测/代码确认，新增项必须跑通一次再入库 ---
# (接口名, 调用函数, 期望非空的关键判断) —— 失败即记为环境/反爬/权限异常
def _live_probes() -> list[tuple[str, object]]:
    import datetime as _dt

    today = _dt.date.today()
    start = (today - _dt.timedelta(days=35)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    import akshare as ak

    return [
        ("futures_main_sina(SR0 郑糖主力)", lambda: ak.futures_main_sina(symbol="SR0")),
        ("stock_margin_sse(近35日)", lambda: ak.stock_margin_sse(start_date=start, end_date=end)),
        ("tool_trade_date_hist_sina(交易日历)", lambda: ak.tool_trade_date_hist_sina()),
        ("macro_china_pmi(最新期)", lambda: ak.macro_china_pmi()),
        ("stock_index_pe_lg(沪深300)", lambda: ak.stock_index_pe_lg(symbol="沪深300")),
    ]


def run_l1() -> int:
    import akshare as ak

    print(f"akshare {ak.__version__}")
    try:
        import tushare as ts

        print(f"tushare {ts.__version__}")
    except Exception:  # noqa: BLE001 — 版本打印失败不阻断 L1
        print("tushare (版本不可得)")
    missing: list[str] = []
    for name in AK_INTERFACES:
        if hasattr(ak, name):
            print(f"  ✓ {name}")
        else:
            missing.append(name)
            print(f"  ✗ {name}  ← 缺失（版本漂移？）")
    print(f"\nL1 总计 {len(AK_INTERFACES)}，缺失 {len(missing)}")
    if missing:
        print("处置：对比 akshare 版本（上行）与 data-interface-map.md 基线；")
        print("      已移除接口 → 改用替代源并更新地图文档 E 节。")
    return len(missing)


def run_l2() -> int:
    print("\n--- L2 精选实探（网络，环境/反爬/权限检测）---")
    fails = 0
    for label, fn in _live_probes():
        t0 = time.time()
        try:
            df = fn()
            ok = hasattr(df, "__len__") and len(df) > 0
            tag = f"{len(df)} 行" if ok else f"空结果({type(df).__name__})"
            if ok:
                print(f"  ✓ {label} — {tag} ({time.time()-t0:.1f}s)")
            else:
                # 空结果 = 环境/反爬/权限失败的常见形态，不得记 ✓（假绿防护）
                fails += 1
                print(f"  ✗ {label} — {tag}（空结果视为失败：环境/反爬/权限）")
        except Exception as e:  # noqa: BLE001 — 冒烟脚本有意捕获全部
            fails += 1
            print(f"  ✗ {label} — {type(e).__name__}: {str(e)[:100]} ({time.time()-t0:.1f}s)")
    print(f"\nL2 失败 {fails} 项（东财类在 Clash 环境失败属预期，见地图 E 节）")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="同时跑 L2 精选实探（网络）")
    args = ap.parse_args()

    missing = run_l1()
    if args.live:
        run_l2()  # L2 环境敏感项：仅报告（打印失败计数），不参与退出码——对齐模块 docstring
    return 1 if missing > 0 else 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
