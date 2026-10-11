"""ETF 专属数据查询 — 指数 PE、折溢价、规模、跟踪误差、对冲覆盖。

Canonical owner: invest-a-etf（自 journal v0.2.1 迁出）。
invest-a-journal 经 scripts/lib/etf_data.py shim 复用本模块。
硬编码对冲映射表 + akshare 直调。
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime
from typing import Any

from _invest_path import (  # noqa: E402
    ensure_invest_a_scripts_on_path,
    ensure_skills_lib_on_path,
)

ensure_invest_a_scripts_on_path()
ensure_skills_lib_on_path()

from codes import etf_symbol_to_ts_code  # noqa: E402
from dates import shanghai_days_ago, shanghai_today  # noqa: E402
from lib.nums import (  # noqa: E402
    ONE_PER_YI,
    QIAN_PER_YI,
    WAN_PER_YI,
    safe_float,
)
from lib.db_util import hist_ex_today  # noqa: E402
from lib.proxy import (  # noqa: E402
    akshare_direct_session,
    no_proxy_session,
    throttle_eastmoney,
)
from quote_tencent import fetch_tencent_quote  # noqa: E402 — skills/lib 共享库（v0.2.7 腾讯行情唯一实现）
from lib.technical import (  # noqa: E402
    annualized_volatility_from_returns,
    boll_latest,
    rsi_series,
    sma,
)

logger = logging.getLogger(__name__)

# fund_etf_spot_em 全表缓存（短 TTL，去重同请求内多次查询）
_SPOT_CACHE_LOCK = threading.Lock()
_SPOT_CACHE_DF: Any = None
_SPOT_CACHE_TS: float = 0.0
_SPOT_CACHE_TTL_SEC = 30.0
# 全表源失败负缓存（秒）：同进程内失败后短时间内不重复回源（详见 _get_etf_spot_df）
_SPOT_FAIL_TTL_SEC = 60.0
_SPOT_FAIL_TS: float = 0.0
_SPOT_FAIL_REASON: str = ""

# fetch_etf_nav 固定取数窗口（自然日）：与 fetch_etf_index_daily 的 700 对齐。
# 覆盖 days ≤ ~470 交易日的 query_etf_kline 请求（MA60 需 ~75 交易日缓冲，
# 裕量约 6 倍）；超窗请求在 query_etf_kline 显式告警而非静默截断
_NAV_FETCH_NATURAL_DAYS = 700

# fetch_etf_share_history 固定取数窗口（自然日）：覆盖 days ≤ ~170 交易日的
# query_etf_share_history 请求；超窗在查询侧显式告警而非静默少返回
_SHARE_FETCH_NATURAL_DAYS = 250

# query_etf_kline 输出 note 文案（原内联字符串上提，逐字节不变）
_RSI_NOTE = ("Wilder RSI on NAV closes，默认周期 24（ETF NAV 波动低于个股价格，"
             "较标准 14 周期更平滑；数据不足时降级为 14），非交易信号")
_ADJ_NOTE = ("NAV 序列已通过 Tushare fund_adj 前复权（消除分红/拆分造成的断点），"
             "MA/波动率/RSI/BOLL 基于复权后序列计算；"
             "nav_history.change_pct 基于复权 NAV 重新计算，"
             "原始 日增长率 不再适用")

# 单位换算比率上提至 skills/lib/nums.py（两 skill 共用）：ONE_PER_YI / WAN_PER_YI /
# QIAN_PER_YI / ONE_PER_WAN。此处经 import 使用（spot AUM / 份额快照 / share_flow /
# share_history 共用），不再保留本文件私有常量（code-review #10）

# 份额历史早退 note 文案（fetch / query 两层共用，逐字节一致）
_NOTE_SHARE_HIST_UNAVAILABLE = "份额历史不可用"
_NOTE_SHARE_NO_DATA = "fund_share 无数据（需 ≥2000 Tushare 积分）"
_NOTE_DAILY_NO_DATA = "fund_daily 无数据"
_NOTE_NO_DATE_INTERSECT = "份额-价格日期无交集"

# 近端流向窗口（行）：整体合计定性会掩盖近端转向（batch-test P1-4）
RECENT_FLOW_DAYS = 5


# ---------------------------------------------------------------------------
# 对冲工具覆盖映射表
# ---------------------------------------------------------------------------

# 运行时 canonical 源；人类可读副本见 references/etf-hedge-map.md（改映射请先改此处再同步文档）
# 各类型对应对冲覆盖率说明：
#   high    — 有期货 + 期权
#   partial — 仅有期权或期货
#   low     — 跨市场/商品类，有相关衍生品但非直接挂钩
#   none    — 行业/主题 ETF，无直接对冲工具
ETF_HEDGE_MAP: dict[str, dict[str, str | None]] = {
    # —— 宽基（高对冲覆盖） ——
    "510050": {"index": "上证50",       "futures": "上证50股指期货(IH)",  "options": "上证50ETF期权",       "coverage": "high"},
    "510300": {"index": "沪深300",      "futures": "沪深300股指期货(IF)", "options": "沪深300ETF期权",      "coverage": "high"},
    "510500": {"index": "中证500",      "futures": "中证500股指期货(IC)", "options": "中证500ETF期权",      "coverage": "high"},
    "512100": {"index": "中证1000",     "futures": "中证1000股指期货(IM)", "options": "中证1000ETF期权(部分)", "coverage": "partial"},
    "159845": {"index": "中证1000",     "futures": "中证1000股指期货(IM)", "options": "中证1000ETF期权(部分)", "coverage": "partial"},
    "588000": {"index": "科创50",       "futures": "科创50期货(2025上线)",  "options": "科创50ETF期权",       "coverage": "high"},
    "159915": {"index": "创业板指",     "futures": None,                   "options": "创业板ETF期权",       "coverage": "partial"},
    "159949": {"index": "创业板50",     "futures": None,                   "options": "创业板50ETF期权",     "coverage": "partial"},
    "563300": {"index": "中证2000",     "futures": None, "options": None, "coverage": "none"},
    # —— 策略/红利/货币 ——
    "510880": {"index": "红利指数",     "futures": None, "options": None, "coverage": "none"},
    "511880": {"index": "银华日利",     "futures": None, "options": None, "coverage": "none"},
    # —— 跨境 ——
    "513100": {"index": "纳指100",      "futures": None, "options": None, "coverage": "low"},
    "513500": {"index": "标普500",      "futures": None, "options": None, "coverage": "low"},
    "159920": {"index": "恒生ETF",      "futures": None, "options": None, "coverage": "low"},
    "513050": {"index": "中概互联",     "futures": None, "options": None, "coverage": "low"},
    "159941": {"index": "纳指ETF",      "futures": None, "options": None, "coverage": "low"},
    # —— 商品 ——
    "518880": {"index": "黄金9999",     "futures": "黄金期货(AU)", "options": None, "coverage": "low"},
    # —— 行业/主题（无直接对冲工具） ——
    "512480": {"index": "半导体",       "futures": None, "options": None, "coverage": "none"},
    "159995": {"index": "国证芯片",     "futures": None, "options": None, "coverage": "none"},
    "512760": {"index": "CES半导体芯片", "futures": None, "options": None, "coverage": "none"},
    "512660": {"index": "军工ETF",      "futures": None, "options": None, "coverage": "none"},
    "512690": {"index": "中证酒",       "futures": None, "options": None, "coverage": "none"},
    "512010": {"index": "300医药",      "futures": None, "options": None, "coverage": "none"},
    "512170": {"index": "中证医疗",     "futures": None, "options": None, "coverage": "none"},
    "516160": {"index": "新能源",       "futures": None, "options": None, "coverage": "none"},
    "159806": {"index": "新能源车",     "futures": None, "options": None, "coverage": "none"},
    "515790": {"index": "光伏产业",     "futures": None, "options": None, "coverage": "none"},
    "512880": {"index": "证券公司",     "futures": None, "options": None, "coverage": "none"},
    "512800": {"index": "中证银行",     "futures": None, "options": None, "coverage": "none"},
    "512200": {"index": "中证地产",     "futures": None, "options": None, "coverage": "none"},
    "516970": {"index": "基建工程",     "futures": None, "options": None, "coverage": "none"},
    "159865": {"index": "中证畜牧",     "futures": None, "options": None, "coverage": "none"},
    "159766": {"index": "旅游",         "futures": None, "options": None, "coverage": "none"},
    "159611": {"index": "电力",         "futures": None, "options": None, "coverage": "none"},
    "512980": {"index": "中证传媒",     "futures": None, "options": None, "coverage": "none"},
    "159869": {"index": "动漫游戏",     "futures": None, "options": None, "coverage": "none"},
    "516510": {"index": "云计算",       "futures": None, "options": None, "coverage": "none"},
    "515050": {"index": "5G通信",       "futures": None, "options": None, "coverage": "none"},
    "515880": {"index": "通信设备",     "futures": None, "options": None, "coverage": "none"},
}

# csindex 符号映射（ETF 代码 → csindex 指数代码）
CSINDEX_MAP: dict[str, str] = {
    "510050": "000016",   # 上证50
    "510300": "000300",   # 沪深300
    "510500": "000905",   # 中证500
    "512100": "000852",   # 中证1000
    "159845": "000852",   # 中证1000 ETF（深市，同 512100）
    "563300": "932000",   # 中证2000
    "588000": "000688",   # 科创50
    "159915": "399006",   # 创业板指
    "159949": "399673",   # 创业板50
    "515050": "931079",   # 中证5G通信主题指数（已核实 2026-08-11）
    "516160": "399808",   # 中证新能源指数（已核实 2026-09-07：csindex 市盈率1/2 单窗 20 行可得）
    # R12：510880 身份登记（verified_original）标的=上证红利指数 000015，但 PE 取数
    # 映射曾缺此条 → 报告写「尚无 csindex 指数代码映射」而 tracking 腿又用 000015，
    # 自相矛盾。2026-10-09 实测 csindex PE 文件可得（市盈率1=9.22，单窗 20 行，
    # 日期 2026-10-09）→ 登记同一代码，PE 腿与跟踪身份腿统一。
    "510880": "000015",   # 上证红利指数（实测 2026-10-09）
}


# ---------------------------------------------------------------------------
# ETF 类型分类映射（G1）
# ---------------------------------------------------------------------------

# ETF 代码 → 类型标签。已覆盖 HEDGE_MAP 所有条目 + 主流行业/主题 ETF。
# 未列出的 ETF 通过 fund_etf_category_sina 动态查询或名称关键词推断。
#
# v0.3.2 B01-1：本表保留为**兼容单轴标签**（category 字段消费方不变），
# 新增消费请用 identity.axes（资产/通道/策略三轴）。两处修正均来自官方原文核验：
# - 510880 原误标 `bond`（"红利ETF归类为bond策略"）→ `factor`（上证红利指数：
#   股息率前 50 只选样，股票型；原文=华泰柏瑞 2026-06-13 更新招募说明书）；
# - 515050 原 `sector` → `thematic`（基金全称含「中证 5G 通信主题指数」，
#   原文=华夏基金 2025 年年度报告，披露 2026-03-31）。
_ETF_CATEGORY_MAP: dict[str, str] = {
    # 宽基
    "510050": "broad_market", "510300": "broad_market", "510500": "broad_market",
    "512100": "broad_market", "159845": "broad_market", "563300": "broad_market",
    "588000": "broad_market", "159915": "broad_market", "159949": "broad_market",
    # 行业
    "515790": "sector", "516970": "sector", "512480": "sector", "512690": "sector",
    "512010": "sector", "512760": "sector", "516160": "sector", "159995": "sector",
    "512800": "sector", "512200": "sector", "515880": "sector",
    "512660": "sector", "512710": "sector",
    # 主题（原文核验：中证 5G 通信主题指数）
    "515050": "thematic",
    # 因子/策略（原文核验：上证红利指数股息率选样，股票型）
    "510880": "factor",
    # 跨境
    "513100": "cross_border", "513500": "cross_border",
    # 商品
    "518880": "commodity",
    # 货币
    "511880": "money_market",
}

_CATEGORY_LABELS: dict[str, str] = {
    "broad_market": "宽基ETF", "sector": "行业ETF", "thematic": "主题ETF",
    "cross_border": "跨境ETF", "bond": "债券ETF", "commodity": "商品ETF",
    "money_market": "货币ETF", "factor": "因子/策略ETF",
    # R03：推断兜底返回 "unknown" 时 label 必须同为中文「未知」——旧表缺此键使
    # sina/name 推断路径渲染英文 "unknown"，与 source=none 路径的中文「未知」不一致。
    "unknown": "未知",
}


# ---------------------------------------------------------------------------
# 产品身份（B01-1，v0.3.2）：三轴 + 原文证据 + 适用性
# ---------------------------------------------------------------------------

# 身份登记：先核原文再登记（evidence 带 URL/日期/摘录/核验日/sha256）。
# 未登记代码按 category 推导为 `mapped_unverified`（静态映射待核），
# 名称关键词推断为 `inferred_unverified`——两者都不得当作已核事实使用。
#
# 三轴词表：
#   asset_type: stock / bond / commodity / money_market / unknown
#   channel:    domestic / cross_border / unknown
#   strategy:   broad_market / sector / thematic / factor / unknown
# （管理方式 management: passive / enhanced / active / unknown 为附轴）
ETF_IDENTITY: dict[str, dict] = {
    "510300": {
        "fund_name": "华泰柏瑞沪深300交易型开放式指数证券投资基金",
        "asset_type": "stock", "channel": "domestic", "strategy": "broad_market",
        "management": "passive", "currency": "CNY",
        "tracked_index": {"name": "沪深300", "code": "000300"},
        "verification": {"status": "verified_original", "verified_at": "2026-10-08",
                         "note": "交易所公告 + 管理人产品资料概要（2024-06-20 编制）原文核验"},
        "evidence": [
            {"claim": "基金名称/代码/上市地",
             "source_type": "上交所基金公告（做市服务，2026-06-11）",
             "url": "https://www.sse.com.cn/disclosure/announcement/general/jjzssgg/c/c_20260611_10821513.shtml",
             "as_of": "2026-06-11",
             "quote": "华泰柏瑞沪深300交易型开放式指数证券投资基金（基金代码：510300）提供主做市服务。",
             "read_at": "2026-10-08", "verified_at": "2026-10-08",
             "locator": "公告正文（本机 HTTP 200，去除脚本后正文提取）",
             "sha256": "2be1b8bc1f713ed4381607e00a1c31ae517a4dceb1fc73cd471099000d555dd2"},
            {"claim": "资产类型/复制方式/基准",
             "source_type": "基金产品资料概要更新（管理人，编制 2024-06-20）",
             "url": "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-06-20/510300_20240620_7SNV.pdf",
             "as_of": "2024-06-20",
             "quote": "基金类型 股票型；业绩比较基准 沪深300指数；本基金主要采取完全复制法",
             "read_at": "2026-10-08", "verified_at": "2026-10-08",
             "locator": "§一 产品概况 / §二(一) 投资目标与投资策略（pdftotext 前 2 页）",
             "sha256": "320ddbf8a84c126a13227862445c507d0a9828b015d989972ed19fa248fda9e4",
             "note": "费率 0.5%/0.1% 为 2024 历史披露值（见 ETF_FEE_SCHEDULE.current_rate_verified=False）"},
        ],
    },
    "515050": {
        "fund_name": "华夏中证 5G 通信主题交易型开放式指数证券投资基金",
        "asset_type": "stock", "channel": "domestic", "strategy": "thematic",
        "management": "passive", "currency": "CNY",
        "tracked_index": {"name": "中证 5G 通信主题指数", "code": "931079"},
        "verification": {"status": "verified_original", "verified_at": "2026-10-08",
                         "note": "管理人 2025 年年度报告原文核验（披露 2026-03-31）"},
        "evidence": [
            {"claim": "基金名称/主代码/上市地/生效日",
             "source_type": "基金 2025 年年度报告（管理人华夏基金，披露 2026-03-31）",
             "url": "https://www.chinaamc.com/upload/resources/file/2026/03/31/cc00948747a74f66984d8ecff8214cac.pdf",
             "as_of": "2025-12-31（报告期）", "quote": "基金名称 华夏中证 5G 通信主题交易型开放式指数证券投资基金；基金主代码 515050",
             "read_at": "2026-10-08", "verified_at": "2026-10-08",
             "locator": "§2.1 基金基本情况（物理 PDF 第 4 页；文内页码 3）",
             "sha256": "382123a4277fe99dc7271a05003ce9382eb85aae96a496572d69ee505e7a8545"},
            {"claim": "资产类型/基准指数",
             "source_type": "同上（§2.2 基金产品说明）", "url": "同上", "as_of": "2025-12-31（报告期）",
             "quote": "本基金为股票基金；业绩比较基准 中证 5G 通信主题指数收益率",
             "read_at": "2026-10-08", "verified_at": "2026-10-08",
             "locator": "§2.2（物理 PDF 第 4 页；文内页码 3）",
             "sha256": "382123a4277fe99dc7271a05003ce9382eb85aae96a496572d69ee505e7a8545"},
        ],
    },
    "510880": {
        "fund_name": "上证红利交易型开放式指数证券投资基金",
        "asset_type": "stock", "channel": "domestic", "strategy": "factor",
        "management": "passive", "currency": "CNY",
        "tracked_index": {"name": "上证红利指数", "code": "000015"},
        "verification": {"status": "verified_original", "verified_at": "2026-10-08",
                         "note": "交易所公告 + 管理人 2026 年第 1 号更新招募说明书原文核验"},
        "evidence": [
            {"claim": "基金名称/代码/上市地",
             "source_type": "上交所基金公告（做市服务，2026-05-28）",
             "url": "https://www.sse.com.cn/disclosure/announcement/general/jjzssgg/c/c_20260528_10820053.shtml",
             "as_of": "2026-05-28", "quote": "上证红利交易型开放式指数证券投资基金（基金代码：510880）提供主做市服务。",
             "read_at": "2026-10-08", "verified_at": "2026-10-08",
             "locator": "公告正文（本机 HTTP 200；标题行含「上证红利交易型开放式指数证券投资基金」）",
             "sha256": "d77e828d1b27ec3ca6cce96f532d5c54afe153e728d59042379f3de031a47cd1"},
            {"claim": "资产类型/策略（因子）/标的指数",
             "source_type": "更新招募说明书（管理人华泰柏瑞，2026 年第 1 号，2026-06-13）",
             "url": "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-06-13/510880_20260613_RW6R.pdf",
             "as_of": "2026-06-13",
             "quote": "本基金的标的指数为上证红利指数；按照过去三年平均现金股息率由高到低排名，选取排名靠前的 50 只证券作为指数样本",
             "read_at": "2026-10-08", "verified_at": "2026-10-08",
             "locator": "重要提示「标的指数」段（PDF 第 2 页）",
             "sha256": "43d294020119e07333c7f600902cad313ebfbd2668d234003a50648f4627b197",
             "note": "费率 0.5%/0.1%（contract_disclosure，现行招募说明书面）；资产类型=股票（非债券），策略=股息率选样因子"},
        ],
    },
    # 已收录但未经本轮原文核验的静态映射（mapped_unverified；使用须注明待核）
    "518880": {"asset_type": "commodity", "channel": "domestic", "strategy": "unknown",
               "tracked_index": {"name": "黄金9999", "code": None}},
    "511880": {"asset_type": "money_market", "channel": "domestic", "strategy": "unknown",
               "tracked_index": {"name": "银华日利", "code": None}},
    "513100": {"asset_type": "stock", "channel": "cross_border", "strategy": "unknown",
               "tracked_index": {"name": "纳指100", "code": None}},
    "513500": {"asset_type": "stock", "channel": "cross_border", "strategy": "unknown",
               "tracked_index": {"name": "标普500", "code": None}},
    "513050": {"asset_type": "stock", "channel": "cross_border", "strategy": "unknown",
               "tracked_index": {"name": "中概互联", "code": None}},
    "159941": {"asset_type": "stock", "channel": "cross_border", "strategy": "unknown",
               "tracked_index": {"name": "纳指ETF", "code": None}},
    # A5（2026-10-10 原文核验）：基金 2026 年中期报告（56 页 PDF）+ 管理人官网产品页。
    "159920": {
        "fund_name": "恒生交易型开放式指数证券投资基金",
        "asset_type": "stock", "channel": "cross_border", "strategy": "broad_market",
        "management": "passive", "currency": "CNY",
        "tracked_index": {
            "name": "恒生指数", "code": "HSI", "currency": "HKD",
            "note": ("业绩比较基准=**经人民币汇率调整**的恒生指数收益率（原文 §2.2）——"
                     "基准含汇率调整；恒指为 HKD 价格指数口径，与 CNY 复权净值两腿"
                     "币种/收益类型均不同 → 不确认 TD/TE（见 tracking 块）"),
        },
        "verification": {"status": "verified_original", "verified_at": "2026-10-10",
                         "note": "基金 2026 年中期报告原文核验（原件托管渠道，物理 PDF 56 页）"
                                 "+ 管理人官网产品页（读取 2026-10-10）；费率见 ETF_FEE_SCHEDULE"},
        "evidence": [
            {"claim": "基金名称/主代码/管理人/托管人/生效日/上市地/运作方式",
             "source_type": "基金 2026 年中期报告（管理人华夏基金；报告期末 2026-06-30）",
             "url": "https://static.howbuy.com/gkxx/ggfile//2026/2026-8/2026-08-31/5514889.pdf",
             "as_of": "2026-06-30（报告期末；渠道披露 2026-08-31）",
             "quote": "基金名称 恒生交易型开放式指数证券投资基金；基金主代码 159920；"
                      "基金管理人 华夏基金管理有限公司；基金托管人 中国银行股份有限公司；"
                      "基金合同生效日 2012 年 8 月 9 日；基金份额上市的证券交易所 深圳证券交易所",
             "read_at": "2026-10-10", "verified_at": "2026-10-10",
             "locator": "§2.1 基金基本情况（物理 PDF 第 5 页 / 共 56 页）",
             "sha256": "c207dd9f5331be91269c5bfb23005064b45d43cea9e10b3c1e6aaf2925458210"},
            {"claim": "资产类型/复制方式/业绩比较基准（含汇率调整）",
             "source_type": "同上（§2.2 基金产品说明 / §4.4.1 投资策略和运作分析）",
             "url": "https://static.howbuy.com/gkxx/ggfile//2026/2026-8/2026-08-31/5514889.pdf",
             "as_of": "2026-06-30（报告期末）",
             "quote": "业绩比较基准 本基金的业绩比较基准为经人民币汇率调整的恒生指数收益率；"
                      "本基金属于股票基金……基金主要投资于标的指数成份股及备选成份股；"
                      "本基金主要采用组合复制策略及适当的替代性策略以更好地跟踪标的指数",
             "read_at": "2026-10-10", "verified_at": "2026-10-10",
             "locator": "§2.2（物理 PDF 第 5 页）；§4.4.1（物理 PDF 第 14 页）",
             "sha256": "c207dd9f5331be91269c5bfb23005064b45d43cea9e10b3c1e6aaf2925458210"},
            {"claim": "业绩比较基准细化公式 + 估值/开放日/汇率规则（跨境时点条款）",
             "source_type": "招募说明书（更新）2026-07-20（管理人华夏基金）",
             "url": "https://www.chinaamc.com/upload/resources/file/2026/07/20/"
                    "794ad3cf4e4941b0a65e89ecddbbf5b8.pdf",
             "as_of": "2026-07-20",
             "quote": "本基金业绩比较基准：经人民币汇率调整的标的指数收益率。其中，"
                      "经人民币汇率调整的标的指数为人民币/港币汇率×恒生指数；"
                      "本基金的开放日为深圳证券交易所和香港交易所的共同交易日；"
                      "人民币对主要外汇的汇率应当以中国人民银行或其授权机构最新公布的"
                      "人民币汇率中间价",
             "read_at": "2026-10-10", "verified_at": "2026-10-10",
             "locator": "基准公式：物理 PDF 第 18–19 页（共 115 页）；开放日：第 39 页；"
                        "估值日：第 51 页；汇率（中间价）：第 52 页；暂停估值情形：第 53 页",
             "sha256": "a2e2b4855aaf118b35e4edeb26c1cccc72d4fa9e5c75894a16a57cb152337235",
             "note": "跨境时点条款：开放日=两地共同交易日（港市休市日暂停申赎）；"
                     "汇率=人民银行中间价——追踪/复算跨币种口径须按该规则，不得以即期价代替"},
            {"claim": "官网产品页身份字段（基金全称/代码/币种）",
             "source_type": "管理人官网产品页（华夏基金；页面无版本号，读取日 2026-10-10）",
             "url": "https://www.chinaamc.com/fund/159920/index.shtml",
             "as_of": "2026-10-10（读取日）",
             "quote": "基金全称：恒生交易型开放式指数证券投资基金；基金代码：159920；"
                      "基金合同生效日：2012-08-09；基金管理人：华夏基金管理有限公司；"
                      "基金托管人：中国银行股份有限公司；交易币种：人民币",
             "read_at": "2026-10-10", "verified_at": "2026-10-10",
             "locator": "官网产品页「基金概况」区（HTML 渲染文本，非 PDF 页码）",
             "sha256": "0855b4c1a5a43bf485ef16fda6defc53eb977a4d6561ad75fcc3f82f31d006c3"},
        ],
    },
}

# 费率登记（原文）：逐条带 rate_as_of / evidence_type / current_rate_verified。
# **`current_rate_verified` 只有一个含义（R3 恢复原义）：报告日现行费率已核**——
# 材料版本已读 ≠ 报告日现行已核；无「截至报告日的更新核查」一律 False。
# 版本信息用 source 元信息披露（source.version / as_of / locator / verified_at），
# 不另设 currency/版本布尔（不做第二套并行现行性合同）。区分三种未核形态
# （由 evidence_type + source 表达）：历史披露值（2024 材料，非现行证据）、
# 合同文件版本已读（版本之后调整未核）、年报实际计费口径（非合同费率核验）。
# 未登记的代码 → query_etf_fees 返回 unverified（不给 0，不猜测）。每份材料
# 记录实际读取内容的 sha256 / 页或章节定位 / 读取时间（URL 存在 ≠ 已读）。
ETF_FEE_SCHEDULE: dict[str, dict] = {
    "510300": {
        "items": [
            {"name": "管理费", "annual_rate_pct": 0.5, "basis": "基金资产净值",
             "rate_as_of": "2024-06-20（产品资料概要）"},
            {"name": "托管费", "annual_rate_pct": 0.1, "basis": "基金资产净值",
             "rate_as_of": "2024-06-20（产品资料概要）"},
        ],
        "evidence_type": "historical_disclosure_only",
        "current_rate_verified": False,   # 报告日现行费率未核（材料为 2024 版）
        "source": {
            "source_type": "基金产品资料概要更新（管理人，编制 2024-06-20）",
            "version": "产品资料概要更新（2024-06-20 编制）",
            "url": "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-06-20/510300_20240620_7SNV.pdf",
            "as_of": "2024-06-20",
            "read_at": "2026-10-08",
            "sha256": "320ddbf8a84c126a13227862445c507d0a9828b015d989972ed19fa248fda9e4",
            "locator": "费用表（管理费 0.5% / 托管费 0.1%，物理 PDF 第 3 页）",
            "note": "2024 年材料：**只作历史披露值**，报告日现行费率未核（须以最新招募说明书复核心）",
        },
        "verified_at": "2026-10-08",
    },
    "515050": {
        "items": [
            {"name": "管理费", "annual_rate_pct": 0.5, "basis": "前一日基金资产净值",
             "rate_as_of": "2025-12-31（2025 年年度报告计费费率）"},
            {"name": "托管费", "annual_rate_pct": 0.1, "basis": "前一日基金资产净值",
             "rate_as_of": "2025-12-31（2025 年年度报告计费费率）"},
        ],
        "evidence_type": "annual_report_actual",
        "current_rate_verified": False,   # 报告日现行合同费率未核（年报为实际计费口径）
        "source": {
            "source_type": "基金 2025 年年度报告（报表附注 7.4.10.2）",
            "version": "2025 年年度报告（披露日 2026-03-31）",
            "url": "https://www.chinaamc.com/upload/resources/file/2026/03/31/cc00948747a74f66984d8ecff8214cac.pdf",
            "as_of": "2025-12-31（报告期）",
            "read_at": "2026-10-08",
            "sha256": "382123a4277fe99dc7271a05003ce9382eb85aae96a496572d69ee505e7a8545",
            "locator": "报表附注 7.4.10.2.1 管理费（物理 PDF 第 36 页；文内页码 35）；"
                       "附注 7.4.10.2.2 托管费（物理 PDF 第 37 页；文内页码 36）；"
                       "费用附录另见物理第 58 页",
            "note": "当期实际计费年费率；报告日现行合同费率未核（须以最新招募说明书核验；原文注明销售服务费：无）",
        },
        "verified_at": "2026-10-08",
    },
    "510880": {
        "items": [
            {"name": "管理费", "annual_rate_pct": 0.5, "basis": "前一日基金资产净值",
             "rate_as_of": "2026-06-13（更新招募说明书 2026 年第 1 号）"},
            {"name": "托管费", "annual_rate_pct": 0.1, "basis": "前一日基金资产净值",
             "rate_as_of": "2026-06-13（更新招募说明书 2026 年第 1 号）"},
        ],
        "evidence_type": "contract_disclosure",
        # **报告日现行费率未核**：只读到 2026-06-13 版本（更新招募说明书
        # 2026 年第 1 号），该版本之后是否有费率调整无核查材料。版本已读由
        # source.version/as_of/locator 披露；current_rate_verified 不换义
        # （它只表示「报告日现行已核」，此处为 False）。
        "current_rate_verified": False,
        "source": {
            "source_type": "更新招募说明书 2026 年第 1 号（管理人，合同文件版本）",
            "version": "更新招募说明书 2026 年第 1 号（2026-06-13）",
            "url": "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-06-13/510880_20260613_RW6R.pdf",
            "as_of": "2026-06-13",
            "read_at": "2026-10-08",
            "sha256": "43d294020119e07333c7f600902cad313ebfbd2668d234003a50648f4627b197",
            "locator": "基金费用章节（管理费 0.50% / 托管费 0.10% 年费率，物理 PDF 第 58 页；"
                       "附录同费率见物理第 84 页；原 pdftotext 文本行号 2435/2443 非 PDF 页码）",
            "note": "2026-06-13 版本（更新招募说明书 2026 年第 1 号）费率已读；"
                    "该版本之后的调整未核 → 报告日现行费率未核",
        },
        "verified_at": "2026-10-08",
    },
    # A5（2026-10-10 原文核验）：以**报告日前最新法律文件**（招募说明书更新
    # 2026-07-20）为费率主源，产品资料概要 2026-05-29（自述「最新合同费率」）、
    # 官网费率页（读取 2026-10-10）与中期报告实际计费口径（§6.4.10.2）四方一致。
    # `current_rate_verified=False` 的**剩余缺口**：2026-09-01 之后的费率调整公告
    # 未全量核对（公告列表只读第 1 页）——不签「报告日现行费率全核」。
    "159920": {
        "items": [
            {"name": "管理费", "annual_rate_pct": 0.6, "basis": "前一日基金资产净值",
             "rate_as_of": "2026-07-20（招募说明书更新）"},
            {"name": "托管费", "annual_rate_pct": 0.15, "basis": "前一日基金资产净值",
             "rate_as_of": "2026-07-20（招募说明书更新）"},
            {"name": "基金运作综合费率（年化）", "annual_rate_pct": 0.76,
             "basis": "管理费+托管费+销售服务费（若有）+其他运作费用（概要口径）",
             "rate_as_of": "2026-05-29（产品资料概要）"},
        ],
        "evidence_type": "contract_disclosure",
        "current_rate_verified": False,
        "source": {
            "source_type": "招募说明书（更新）2026-07-20（主源）+ 产品资料概要 2026-05-29 "
                           "+ 管理人官网费率页 + 2026 年中期报告实际计费附注",
            "version": "招募说明书（更新）2026-07-20；产品资料概要 2026-05-29；"
                       "官网费率页（无版本号，读取日 2026-10-10）；中期报告 2026-06-30",
            "url": "https://www.chinaamc.com/upload/resources/file/2026/07/20/"
                   "794ad3cf4e4941b0a65e89ecddbbf5b8.pdf",
            "as_of": "2026-07-20（最新法律文件版本）",
            "read_at": "2026-10-10",
            "sha256": "a2e2b4855aaf118b35e4edeb26c1cccc72d4fa9e5c75894a16a57cb152337235",
            "locator": "招募更新 2026-07-20 基金费用章节：管理费 0.60%、托管费 0.15% 年费率"
                       "（物理 PDF 第 49 页）；产品资料概要 2026-05-29「管理费固定费率 0.60%、"
                       "托管费固定费率 0.15%、基金运作综合费率（年化）0.76%」"
                       "（物理 PDF 第 3 页；自述『管理费、托管费为最新合同费率』）；"
                       "官网费率页同值；中期报告 §6.4.10.2.1/2 实际计费同值（物理第 30 页）",
            "note": "四方同值（0.60%/0.15%）；剩余缺口=2026-09-01 之后的费率调整公告未全量"
                    "核对（公告列表只读第 1 页）——按「报告日现行费率已核」的严格语义不签 True；"
                    "引用须带 rate_as_of",
        },
        "verified_at": "2026-10-10",
    },
}

# ---------------------------------------------------------------------------
# 原文披露的跟踪/结构补充（A5/A6，2026-10-10）
# ---------------------------------------------------------------------------
# 只登记**实际读取的官方材料**（URL+sha256+物理页码定位）；未登记 → None（不推断）。
# 角色：① 跟踪质量优先用原文「基金 vs 业绩比较基准」收益对比（A6）——
# 定期报告 §3.2.1 表为官方口径（基准已含汇率调整），优先于引擎两腿自算；
# ② 资产/行业/持仓结构（A5）——期末静态披露，不等于报告日现行。

ETF_BENCHMARK_PERFORMANCE: dict[str, dict] = {
    "159920": {
        "as_of": "2026-06-30（2026 年中期报告）",
        "benchmark_text": "经人民币汇率调整的恒生指数收益率",
        "periods": [
            {"period": "过去三个月", "fund_pct": -8.39, "benchmark_pct": -9.20,
             "diff_pct": 0.81, "fund_std_pct": 1.14, "benchmark_std_pct": 1.14},
            {"period": "过去六个月", "fund_pct": -13.29, "benchmark_pct": -14.15,
             "diff_pct": 0.86, "fund_std_pct": 1.22, "benchmark_std_pct": 1.21},
            {"period": "过去一年", "fund_pct": -8.00, "benchmark_pct": -9.47,
             "diff_pct": 1.47, "fund_std_pct": 1.14, "benchmark_std_pct": 1.13},
            {"period": "过去三年", "fund_pct": 21.93, "benchmark_pct": 13.95,
             "diff_pct": 7.98, "fund_std_pct": 1.41, "benchmark_std_pct": 1.41},
            {"period": "过去五年", "fund_pct": -9.80, "benchmark_pct": -17.15,
             "diff_pct": 7.35, "fund_std_pct": 1.52, "benchmark_std_pct": 1.52},
            {"period": "过去七年", "fund_pct": -12.01, "benchmark_pct": -20.85,
             "diff_pct": 8.84, "fund_std_pct": 1.47, "benchmark_std_pct": 1.46},
            {"period": "过去十年", "fund_pct": 30.44, "benchmark_pct": 11.82,
             "diff_pct": 18.62, "fund_std_pct": 1.35, "benchmark_std_pct": 1.34},
            {"period": "自基金合同生效起至今", "fund_pct": 43.01, "benchmark_pct": 21.19,
             "diff_pct": 21.82, "fund_std_pct": 1.28, "benchmark_std_pct": 1.28},
        ],
        "latest_nav_cny": 1.3596,
        "manager_attribution": (
            "本报告期（2026H1）份额净值增长率 -13.29%，同期经估值汇率调整的业绩比较基准"
            "增长率 -14.15%，跟踪偏离度 +0.86%；偏离主要原因：日常运作费用、指数结构调整、"
            "申购赎回、成份股分红等（§4.4.2）"),
        "source": {
            "source_type": "基金 2026 年中期报告（§3.2.1 / §4.4.2）",
            "url": "https://static.howbuy.com/gkxx/ggfile//2026/2026-8/2026-08-31/5514889.pdf",
            "as_of": "2026-06-30（报告期末）",
            "read_at": "2026-10-10", "verified_at": "2026-10-10",
            "sha256": "c207dd9f5331be91269c5bfb23005064b45d43cea9e10b3c1e6aaf2925458210",
            "locator": "§3.2.1 基金份额净值增长率及其与同期业绩比较基准收益率的比较"
                       "（物理 PDF 第 7 页）；§4.4.2 业绩表现（物理 PDF 第 14 页）",
        },
        "note": ("官方披露口径（基准含汇率调整），跟踪质量**优先引用**；表中 ②－④ 列为"
                 "「份额净值增长率标准差 − 基准收益率标准差」，是收益波动之差，"
                 "**不是跟踪误差（TE）**——不得把两个标准差的差当 TE 引用"),
    },
}

ETF_DISCLOSED_STRUCTURE: dict[str, dict] = {
    "159920": {
        "as_of": "2026-06-30",
        "shares_total": 6407559219,
        "unit_nav_cny": 1.3596,
        "total_assets_cny": 8829472169.97,
        # 资产负债表（§6.1/§6.4.12）：资产总计 / 负债合计 / 净资产合计 / 实收基金
        "balance_sheet": {
            "total_assets_cny": 8829472169.97,
            "total_liabilities_cny": 117703114.36,
            "net_assets_cny": 8711769055.61,
            "paid_in_capital_cny": 6407559219.00,
            "locator": "§6.1 资产负债表 / §6.4.12 期末基金资产净值（物理 PDF 第 16–17 页）",
        },
        # §7.1 资产组合：分母 = 基金**总资产**
        "asset_mix_pct_of_total_assets": {
            "equity": 91.81, "bank_deposits_and_settlement": 6.24, "other_assets": 1.95},
        "balance_sheet_note": ("§6.1 资产负债表口径：货币资金 447,576,103.42 元"
                               "（分母=总资产，非全部净资产；银行存款≠可分配现金）"),
        # §7.2/7.3/7.4：分母 = 基金**资产净值**
        "equity_market_value_cny": 8106049244.23,
        "equity_pct_of_nav": 93.05,
        "hk_stock_connect_cny": 7609596561.66,
        "hk_stock_connect_pct_of_nav": 87.35,
        "equity_by_country": [{"country": "中国香港", "fair_value_cny": 8106049244.23,
                               "pct_of_nav": 93.05}],
        "industry_gics_pct_of_nav": [
            {"industry": "金融", "pct": 31.67},
            {"industry": "非必需消费品", "pct": 16.13},
            {"industry": "通信服务", "pct": 14.91},
            {"industry": "信息技术", "pct": 6.18},
            {"industry": "工业", "pct": 5.05},
            {"industry": "保健", "pct": 4.46},
            {"industry": "能源", "pct": 4.45},
            {"industry": "房地产", "pct": 3.79},
            {"industry": "公用事业", "pct": 2.34},
            {"industry": "必需消费品", "pct": 2.09},
            {"industry": "材料", "pct": 1.98},
        ],
        "industry_total_pct_of_nav": 93.05,
        # §7.4.1 完整权益投资明细：93 只（原文全量；此处只登记口径与定位，不复制全表）
        "equity_holdings_detail": {
            "n_holdings": 93,
            "locator": "§7.4.1 期末按公允价值占基金资产净值比例大小排序的**所有**权益投资明细"
                       "（物理 PDF 第 39–48 页）",
            "top1": {"name": "HSBC HOLDINGS PLC / 汇丰控股有限公司", "code": "00005",
                     "pct_of_nav": 8.51},
            "note": "期末（2026-06-30）静态披露的完整持仓，不等于报告日现行持仓；"
                    "全表见原文定位（或本次采集件）",
        },
        "derivatives": {
            "index_futures_notional_cny": 629586500.71,
            "instruments": [{"contract": "HIN6 HANG SENG IDX FUT Jul26",
                             "position_lots": 615,
                             "contract_market_value_cny": 610905249.61,
                             "fair_value_change_cny": -18681251.10}],
            "locator": "§6.4.7.3.1/§6.4.7.3.2（物理 PDF 第 24 页）",
            "note": "股指期货名义金额；衍生净额为 0（当日无负债结算、暂收款抵销）",
        },
        # §8/§9 官方份额变动与持有人结构（报告期 2026H1；静态披露）
        "official_share_flow_2026H1": {
            "period": "2026-01-01 ~ 2026-06-30",
            "shares_begin": 8793559219,
            "subscriptions": 4000000,
            "redemptions": 2390000000,
            "split_change": 0,
            "shares_end": 6407559219,
            "locator": "§9 开放式基金份额变动（物理 PDF 第 52–53 页）",
            "note": ("官方口径（份额，非金额）：报告期净赎回 -2,386,000,000 份；"
                     "拆分变动 0（本基金报告期无拆分）"),
        },
        "holders": {
            "households": 98408,
            "institutional_pct": 29.86, "retail_pct": 31.62,
            "linked_feeder_pct": 38.52,  # 华夏恒生 ETF 联接基金持有占比
            "locator": "§8.1 期末基金份额持有人户数及持有人结构（物理 PDF 第 52 页）",
            "note": "联接基金持有 38.52%：申赎节奏受场外联接申赎影响（流量解读须注明）",
        },
        "fx_exposure": {
            "foreign_assets_cny": 8400913926.89,
            "hkd_assets_cny": 8376211118.84,
            "sensitivity_5pct_cny": 420045696.34,
            "locator": "§6.4.13.4.2 外汇风险（物理 PDF 第 34–36 页）",
            "note": "外币资产折人民币合计与 ±5% 汇率敏感性（原文披露）",
        },
        "denominators_note": ("资产配置（§7.1）分母=总资产；国家/行业/持仓（§7.2–7.4）"
                              "分母=基金资产净值；两组不得混用——行业合计余额（93.05%）"
                              "不是现金/银行存款"),
        "source": {
            "source_type": "基金 2026 年中期报告（§6/§7）",
            "url": "https://static.howbuy.com/gkxx/ggfile//2026/2026-8/2026-08-31/5514889.pdf",
            "as_of": "2026-06-30（报告期末）", "read_at": "2026-10-10",
            "verified_at": "2026-10-10",
            "sha256": "c207dd9f5331be91269c5bfb23005064b45d43cea9e10b3c1e6aaf2925458210",
            "locator": "§7.1（物理 PDF 第 38 页）；§7.2/§7.3（第 39 页）；§7.4.1（第 39–48 页）；"
                       "§6.4.7.3（第 24 页）",
        },
        "note": "期末静态披露（非报告日现行全核）；引用须带 as_of 与原文定位",
    },
}


def query_etf_disclosed_structure(symbol: str) -> dict | None:
    """原文披露的结构补充（A5）：只回登记过的标的；未登记 → None（不推断）。"""
    entry = ETF_DISCLOSED_STRUCTURE.get(symbol)
    if not entry:
        return None
    return {"symbol": symbol, **entry}


# ---------------------------------------------------------------------------
# ETF 份额拆分登记与调整（R4，2026-10-10）
# ---------------------------------------------------------------------------
# 份额拆分是**机械份额变化，不是申赎流**：跨拆分的长份额序列若不调整，会把
# 「1:2 拆分」算成 +100% 巨额净流入（513660 现场）。登记项只来自实际读取的
# 官方公告（URL+sha256+页码定位）。
#
# 调整口径（前复权=最新基准，与 NAV 前复权同向）：对日期 t 的份额
#   adj_t = raw_t × Π{ratio : effective_from ≤ t}⁻¹ × Π{ratio : effective_from ≤ latest}
# 最新行不变；拆分前的行按比例放大 → 拆分日 Δadj=0（纯机械跳变被消除，
# 真实申赎仍保留）。`effective_from` 以**数据口径**（份额序列实际跳变的首行）
# 校准，公告口径的权益登记日/除权日另行记录。
ETF_SHARE_SPLITS: dict[str, list[dict]] = {
    "513660": [{
        "effective_from": "20260420", "ratio": 2.0, "type": "split",
        "record_date": "2026-04-20", "ex_date": "2026-04-21",
        "source": {
            "source_type": "基金份额拆分公告（管理人华夏基金，2026-04-15）",
            "url": "https://www.chinaamc.com/upload/resources/file/2026/04/15/"
                   "9c8e5a50d59348229bfa529be6bdadc4.pdf",
            "as_of": "2026-04-15", "read_at": "2026-10-10", "verified_at": "2026-10-10",
            "sha256": "bbf9645e949ea3100d59b1063d3fe645d509b13c2c53850bc6352e85efc90027",
            "locator": "正文「一、基金份额的拆分」：权益登记日 2026-04-20、除权日 "
                       "2026-04-21、按 1:2 比例拆分（每 1 份拆成 2 份）；"
                       "物理 PDF 第 1–2 页（共 4 页）",
        },
        "note": ("数据口径（Tushare fund_share）首个拆分后行=20260420"
                 "（权益登记日日终变更登记）——与公告除权日 04-21 差一个交易日，"
                 "按实际序列校准；拆分本身不是申赎流"),
    }],
}

#: 未登记跳变安全网：单段 |Δadj 份额| / 前值 ≥ 该阈值且无登记拆分覆盖时，
#: 该段流量不可确认（疑似拆分/份额调整但无原文证据；不猜比例、不把机械
#: 份额变化当申赎流）。
SPLIT_SUSPECT_RATIO = 0.5


def _split_factor_map(symbol: str, dates: list[str], *,
                      price: bool = False) -> tuple[list[float], list[dict]]:
    """逐行拆分系数与窗口内事件，统一到最新行的份额单位。

    ``factor_t = Π(ratio : effective_from ≤ latest) / Π(ratio : effective_from ≤ t)``；
    ``adj_t = raw_t × factor_t``。价格用 ``price=True``：分母改按除权日
    ``ex_date`` 生效，原价除以系数。分子仍按最新份额生效日决定单位，
    因而最新行在登记日与除权日之间时，原价也须换算到拆分后份额单位。
    无登记事件 → 全 1（零影响）；日期兼容 YYYYMMDD / YYYY-MM-DD。
    返回 ``(factors, events_in_window)``；events 带 source/note 供 payload 披露。
    """
    events = ETF_SHARE_SPLITS.get(symbol) or []
    if not events:
        return [1.0] * len(dates), []
    dates = [d.replace("-", "") for d in dates]
    latest = dates[-1] if dates else ""

    def _effective(e: dict) -> str:
        return str(e.get("effective_from") or "").replace("-", "")

    def _date(e: dict) -> str:
        if price:
            return str(e.get("ex_date") or e.get("effective_from") or "").replace("-", "")
        return _effective(e)

    cum_latest = math.prod(e["ratio"] for e in events
                           if _effective(e) <= latest)
    factors: list[float] = []
    for d in dates:
        cum_t = math.prod(e["ratio"] for e in events
                          if _date(e) <= d)
        factors.append(cum_latest / cum_t if cum_t else 1.0)
    in_window = [e for e in events
                 if dates and (dates[0] <= _effective(e) <= latest
                               or dates[0] <= _date(e) <= latest)]
    return factors, in_window


# 源可读性记录：接口/字段/时点/是否原文披露（接口渠道不冒充原披露）。
SOURCE_PROVENANCE: dict[str, dict] = {
    "identity_original": {
        "channel": "交易所公告 / 管理人基金合同类材料（PDF）",
        "fields": "基金名称/代码/基准/费率",
        "as_of": "各材料自身日期（逐条记录）",
        "original_disclosure": True,
        "note": "原文渠道；搜索摘要不作为已读原文",
    },
    "etf_spot": {
        "channel": "akshare fund_etf_spot_em（东方财富基金行情页）",
        "fields": "最新价/涨跌幅/成交额/最新份额/基金折价率",
        "as_of": "查询时点（字段无交易日）",
        "original_disclosure": False,
        "note": "接口渠道；折溢价净值时点未知，不得确认实时错价",
    },
    "etf_nav": {
        "channel": "akshare fund_etf_fund_info_em（天天基金净值页）",
        "fields": "净值日期/单位净值/日增长率",
        "as_of": "数据末端日期（latest_nav_date）",
        "original_disclosure": False,
        "note": "接口渠道；单位净值为基金披露口径",
    },
    "etf_adj_factor": {
        "channel": "Tushare fund_adj",
        "fields": "trade_date/adj_factor",
        "as_of": "因子末端日期",
        "original_disclosure": False,
        "note": "复权因子渠道",
    },
    "etf_index_pe": {
        "channel": "akshare stock_zh_index_value_csindex（中证指数公司 PE 文件）",
        "fields": "日期/市盈率1（官方字段：（总股本）P/E1）/市盈率2（官方字段：（计算用股本）P/E2）",
        "as_of": "文件日期列（取值日期随信封记录）",
        "original_disclosure": True,
        "note": "指数公司为指数口径原发布方",
    },
    "etf_index_cons": {
        "channel": "akshare index_stock_cons_csindex（中证指数公司成分文件）",
        "fields": "日期/指数代码/成分券代码/名称/交易所",
        "as_of": "文件日期列",
        "original_disclosure": True,
        "url_template": "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{code}cons.xls",
    },
    "etf_index_weight": {
        "channel": "akshare index_stock_cons_weight_csindex（中证指数公司权重文件）",
        "fields": "日期/成分券代码/权重(%)",
        "as_of": "文件日期列（权重文件通常为上月月末）",
        "original_disclosure": True,
        "url_template": "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{code}closeweight.xls",
    },
    "etf_holdings": {
        "channel": "天天基金 FundArchivesDatas jjcc 页（裸 HTTP）",
        "fields": "报告期/股票代码/名称/占净值比例/持股数/持仓市值",
        "as_of": "报告期（季度，report_date）",
        "original_disclosure": False,
        "note": "渠道页面；基金定期报告为原披露。仅披露前十大，非当期完整持仓",
    },
    "etf_industry_alloc": {
        "channel": "akshare fund_portfolio_industry_allocation_em",
        "fields": "截止时间/行业类别/占净值比例",
        "as_of": "报告期（季度）",
        "original_disclosure": False,
    },
    "etf_index_daily": {
        "channel": "akshare stock_zh_index_daily（指数日线收盘）",
        "fields": "date/close",
        "as_of": "序列末端日期",
        "original_disclosure": False,
        "note": "价格指数收盘；无总收益口径",
    },
    "etf_share_history": {
        "channel": "Tushare fund_share + fund_daily",
        "fields": "trade_date/fd_share；OHLCV",
        "as_of": "序列末端交易日（份额 T+1）",
        "original_disclosure": False,
    },
    "etf_category_sina": {
        "channel": "akshare fund_etf_category_sina",
        "fields": "代码/名称",
        "as_of": "查询时点",
        "original_disclosure": False,
        "note": "分类辅助；不构成身份核验",
    },
    "industry_weekly": {
        "channel": "本地 industry_weekly 表（申万行业 PE/PB 周度快照）",
        "fields": "pe/pb/date",
        "as_of": "快照日期",
        "original_disclosure": False,
        "note": "行业代理值；非 ETF 精确 PE",
    },
}


def _identity_by_category(category: str, symbol: str) -> dict:
    """由兼容单轴 category 推导三轴（未原文核验 → mapped_unverified）。"""
    axis = {
        "broad_market": ("stock", "domestic", "broad_market"),
        "sector": ("stock", "domestic", "sector"),
        "thematic": ("stock", "domestic", "thematic"),
        "factor": ("stock", "domestic", "factor"),
        "cross_border": ("stock", "cross_border", "unknown"),
        "commodity": ("commodity", "domestic", "unknown"),
        "bond": ("bond", "domestic", "unknown"),
        "money_market": ("money_market", "domestic", "unknown"),
    }.get(category, ("unknown", "unknown", "unknown"))
    return {"asset_type": axis[0], "channel": axis[1], "strategy": axis[2]}


_ASSET_LABELS = {"stock": "股票", "bond": "债券", "commodity": "商品",
                 "money_market": "货币", "unknown": "未知"}
_CHANNEL_LABELS = {"domestic": "境内", "cross_border": "跨境", "unknown": "未知"}
_STRATEGY_LABELS = {"broad_market": "宽基", "sector": "行业", "thematic": "主题",
                    "factor": "因子/策略", "unknown": "未知"}


def _applicability_for(axes: dict, *, asset_unknown: bool) -> dict:
    """按资产/通道推导适用维度（未知 → 全不适用 + 待核；不套股票 PE）。"""
    asset = axes.get("asset_type", "unknown")
    is_stock = asset == "stock"
    notes: list[str] = []
    if asset_unknown or asset == "unknown":
        notes.append("身份未核：适用性未知，不得按名称强填 PE/持仓等必填分析")
    if axes.get("channel") == "cross_border":
        notes.append("跨境：估值/净值时点与时区、汇率、额度限制须单独核验")
    if asset == "bond":
        notes.append("债券：适用久期/信用/利率敏感性，不套股票 PE")
    if asset == "commodity":
        notes.append("商品：适用合约/展期/抵押品口径，不套盈利倍数")
    if asset == "money_market":
        notes.append("货币：适用收益披露/流动性口径，不套股票涨跌与 PE")
    return {
        "index_pe": is_stock,
        "stock_pe_drilldown": is_stock,
        "stock_holdings_penetration": is_stock,
        "duration_credit": asset == "bond",
        "futures_roll": asset == "commodity",
        "pe_method": "index_official_only",
        "no_stock_pe_weighting": True,
        "notes": notes,
    }


def query_etf_identity(symbol: str, *, category: str | None = None) -> dict:
    """产品身份三轴 + 证据 + 适用性（B01-1）。

    优先级：原文核验登记（ETF_IDENTITY，verified_original）> 静态映射
    （category 推导，mapped_unverified）> 名称/动态推断（inferred_unverified）
    > unknown。**不以代码/名称猜测替代核验**；未核身份不得触发资产不适用的
    分析必填项（`applicability` 全 False + 待核）。
    """
    entry = ETF_IDENTITY.get(symbol)
    # R14：category=None 时只查一次分类（旧实现在 legacy 推导与 cat_info 各查一次，
    # sina 全表/网络重复；两次结果在缓存竞态下还可能不一致，axes 与 cat_info 分叉）
    if category is None:
        cat_info = query_etf_category(symbol)
        legacy = cat_info.get("category", "unknown")
    else:
        cat_info = {"category": category,
                    "label": _CATEGORY_LABELS.get(category, category),
                    "source": "caller"}
        legacy = category
    if entry and entry.get("verification", {}).get("status") == "verified_original":
        axes = {k: entry[k] for k in ("asset_type", "channel", "strategy")}
        ver = dict(entry["verification"])
        evidence = list(entry.get("evidence") or [])
    elif entry:
        axes = {k: entry[k] for k in ("asset_type", "channel", "strategy")}
        ver = {"status": "mapped_unverified", "verified_at": None,
               "note": "静态映射登记，未做本轮原文核验（使用须注明待核）"}
        evidence = []
    else:
        axes = _identity_by_category(legacy, symbol)
        status = "mapped_unverified" if legacy != "unknown" else "unknown"
        ver = {"status": status, "verified_at": None,
               "note": "由分类映射/名称推断推导，未核验" if status != "unknown"
               else "无身份证据（分类未知）"}
        evidence = []
    return {
        "symbol": symbol,
        "axes": axes,
        "labels": {
            "asset_type": _ASSET_LABELS.get(axes["asset_type"], axes["asset_type"]),
            "channel": _CHANNEL_LABELS.get(axes["channel"], axes["channel"]),
            "strategy": _STRATEGY_LABELS.get(axes["strategy"], axes["strategy"]),
        },
        "management": entry.get("management", "unknown") if entry else "unknown",
        "currency": entry.get("currency") if entry else (
            "CNY" if axes["channel"] == "domestic" else "unknown"),
        "tracked_index": entry.get("tracked_index") if entry else None,
        "fund_name": entry.get("fund_name") if entry else None,
        "applicability": _applicability_for(axes, asset_unknown=(ver["status"] == "unknown")),
        "verification": ver,
        "evidence": evidence,
        "category": cat_info,          # 兼容单轴标签（旧消费方）
    }


def query_etf_fees(symbol: str) -> dict:
    """基金费率（原文登记）。未登记者 → unverified，不给 0、不猜测。

    **`current_rate_verified` 只有一个含义（R3 恢复原义）：报告日现行费率已核**。
    材料版本已读 ≠ 报告日现行已核——没有「截至报告日的更新核查」时必须 False，
    不得换义成「某版本内已核」（也不另设 currency 类并行现行性布尔；currency
    在金融通常表示币种）。版本与读取范围用 source 元信息披露：
    `source.version` / `source.as_of` / `source.locator` / `verified_at`。

    未核的三种形态（由 `evidence_type` + `note` 如实区分，不得互相顶替）：
    - `historical_disclosure_only`：历史披露值（如 2024 材料），非现行费率证据；
    - `contract_disclosure`：合同文件版本已读，但版本之后的调整未核；
    - `annual_report_actual`：年报实际计费口径，非合同费率核验。
    """
    entry = ETF_FEE_SCHEDULE.get(symbol)
    if not entry:
        return {"symbol": symbol, "status": "unverified", "items": [],
                "evidence_type": None, "current_rate_verified": False,
                "source": None, "verified_at": None,
                "note": "未收录官方费率材料；不得用接口渠道冒充原披露或填写估计值"}
    verified_at = str(entry.get("verified_at") or "").strip() or None
    # True 必须绑定实际核查时点（无 verified_at 不允许置 True，防内置常量永远 True）
    current_verified = bool(entry.get("current_rate_verified")) and bool(verified_at)
    src = dict(entry["source"])
    version = src.get("version")
    as_of = src.get("as_of")
    etype = entry.get("evidence_type")
    if current_verified:
        note = f"报告日现行费率已核（核查时点 {verified_at}）"
    elif etype == "contract_disclosure":
        ver_txt = version or "合同文件版本"
        if as_of and str(as_of) in ver_txt:
            ver_txt = ver_txt.replace(f"（{as_of}）", "").strip()
        note = (f"{as_of} 版本（{ver_txt}）费率已读；"
                "报告日现行费率未核（该版本之后的调整材料未核）；引用须带 rate_as_of")
    elif etype == "annual_report_actual":
        per = str(as_of or "").replace("（报告期）", "").strip() or "未标注"
        note = (f"年报实际计费口径（材料期间 {per}）；非现行合同费率核验——"
                "报告日现行费率未核；引用须带 rate_as_of")
    else:
        note = ("历史披露值（非现行费率核验）：报告日现行费率未核；"
                "引用须带 rate_as_of，不得称现行费率")
    return {
        "symbol": symbol,
        "status": "ok",
        "items": [dict(i) for i in entry["items"]],
        "evidence_type": etype,
        "current_rate_verified": current_verified,
        "source": src,
        "verified_at": verified_at,
        "note": note + "；报告费率不等于未来总成本",
    }


def resolve_tracked_index(symbol: str, idx_code: str | None = None, *,
                          identity: dict | None = None) -> dict:
    """跟踪指数代码**统一解析**（R12）：显式参数 > CSINDEX_MAP > 已核身份 tracked_index。

    两个概念必须分开称呼、不得互相推断：

    - ``CSINDEX_MAP`` = **指数文件/PE 取数映射**（数据接线表，逐条经人工核实）；
    - ``ETF_IDENTITY[symbol]["tracked_index"]`` = **产品跟踪身份**（原文核验记录）。

    「CSINDEX_MAP 未登记」≠「产品没有跟踪指数」——此前 query_etf_kline 只查
    CSINDEX_MAP（拿不到指数日线/MA），而 compute_etf_tracking /
    query_etf_index_exposure 又用身份兜底（拿到 000015），同一符号两套解析、
    报告与跟踪腿互相矛盾（510880 现场）。

    返回 ``{"code", "source", "pe_mapped", "identity_status", "currency"}``；
    ``code=None`` 表示两条来源都无（未知保持未知）。``currency`` 为**基准指数**
    币种（仅身份登记显式给出时；csindex 路径为 None——境内指数按调用方默认）——
    A6：两腿币种不一致时禁止确认 TD/TE，故币种须可解析。``identity`` 缺省时用
    纯登记表（``ETF_IDENTITY``，无网络）；调用方已持 identity 块时应透传。
    """
    if idx_code:
        return {"code": str(idx_code), "source": "caller",
                "pe_mapped": CSINDEX_MAP.get(symbol) == str(idx_code),
                "identity_status": None, "currency": None}
    mapped = CSINDEX_MAP.get(symbol)
    ident = identity if identity is not None else (ETF_IDENTITY.get(symbol) or {})
    ident_status = (ident.get("verification") or {}).get("status")
    tracked_info = ident.get("tracked_index") or {}
    if mapped:
        return {"code": mapped, "source": "CSINDEX_MAP", "pe_mapped": True,
                "identity_status": ident_status,
                "currency": tracked_info.get("currency") if str(
                    tracked_info.get("code") or "") == str(mapped) else None}
    tracked = tracked_info.get("code")
    if tracked:
        return {"code": str(tracked),
                "source": f"identity.tracked_index（{ident_status or 'unknown'}）",
                "pe_mapped": False, "identity_status": ident_status,
                "currency": tracked_info.get("currency")}
    return {"code": None, "source": None, "pe_mapped": False,
            "identity_status": ident_status, "currency": None}


def _identity_for_index_resolution(symbol: str) -> dict | None:
    """统一解析的惰性身份输入：只有「CSINDEX_MAP 未登记且身份登记表有条目」才查。

    两道短路都是必要的：CSINDEX_MAP 命中时身份不参与解析；而不在
    ``ETF_IDENTITY`` 登记表的标的其 ``tracked_index`` 恒为 None（类别推断补不出
    指数代码），查一次 `query_etf_identity` 只会白白触发类别查询/网络
    （etf_peers 对每只 peer 调 query_etf_kline，属热路径）。
    """
    if symbol in CSINDEX_MAP or symbol not in ETF_IDENTITY:
        return None
    return query_etf_identity(symbol)


# ---------------------------------------------------------------------------
# ETF → 申万一级行业映射（G4）
# ---------------------------------------------------------------------------

ETF_TO_SW_INDUSTRY: dict[str, dict[str, str]] = {
    # 科技/TMT
    "515050": {"sw_code": "801770", "sw_name": "通信",       "sub": "5G"},
    "515880": {"sw_code": "801770", "sw_name": "通信",       "sub": "通信设备"},
    "512480": {"sw_code": "801080", "sw_name": "电子",       "sub": "半导体"},
    "512760": {"sw_code": "801080", "sw_name": "电子",       "sub": "芯片"},  # 芯片ETF国泰（原误映射军工，v0.2.5 R13 修正）
    "159995": {"sw_code": "801080", "sw_name": "电子",       "sub": "芯片"},
    "562590": {"sw_code": "801080", "sw_name": "电子",       "sub": "半导体设备"},  # R15 补全：半导体设备ETF华夏
    "512330": {"sw_code": "801750", "sw_name": "计算机",     "sub": "信息技术"},
    "515230": {"sw_code": "801750", "sw_name": "计算机",     "sub": "软件"},
    # 新能源/高端制造
    "515790": {"sw_code": "801730", "sw_name": "电力设备",   "sub": "光伏"},
    "516160": {"sw_code": "801730", "sw_name": "电力设备",   "sub": "新能源"},
    "512660": {"sw_code": "801740", "sw_name": "国防军工",   "sub": "军工"},
    "159206": {"sw_code": "801740", "sw_name": "国防军工",   "sub": "卫星"},
    "516970": {"sw_code": "801720", "sw_name": "建筑装饰",   "sub": "基建"},
    # 消费/医药
    "512690": {"sw_code": "801120", "sw_name": "食品饮料",   "sub": "白酒"},
    "512010": {"sw_code": "801150", "sw_name": "医药生物",   "sub": "医药"},
    "159992": {"sw_code": "801150", "sw_name": "医药生物",   "sub": "创新药"},  # R15 补全：创新药ETF银华
    "512200": {"sw_code": "801180", "sw_name": "房地产",     "sub": "地产"},
    # 金融
    "512800": {"sw_code": "801780", "sw_name": "银行",       "sub": "银行"},
    "512070": {"sw_code": "801790", "sw_name": "非银金融",   "sub": "券商"},
    # 资源/周期
    "512400": {"sw_code": "801050", "sw_name": "有色金属",   "sub": "有色"},
    "512710": {"sw_code": "801740", "sw_name": "国防军工",   "sub": "军工龙头"},
}


# ---------------------------------------------------------------------------
# 股票 → 子环节聚类映射（R12 holdings.clusters）
# ---------------------------------------------------------------------------
# 子环节聚类本质是股票属性（新易盛=光模块对任何 ETF 成立），故用股票代码静态映射。
# 仅覆盖已研究 ETF 前十大；未覆盖股票由引擎归入「未归类」，报告层可 AI 补充并标注。
# 修改映射先改此处（聚合口径见 _build_holdings_clusters）。

HOLDINGS_CLUSTER_MAP: dict[str, str] = {
    # —— 515050 通信ETF华夏 前十大（2026-06-30）——
    "300502": "光模块/光器件",  # 新易盛
    "300394": "光模块/光器件",  # 天孚通信
    "300308": "光模块/光器件",  # 中际旭创
    "002384": "PCB/覆铜板",     # 东山精密
    "600183": "PCB/覆铜板",     # 生益科技
    "002463": "PCB/覆铜板",     # 沪电股份
    "002475": "终端/服务器代工",  # 立讯精密
    "601138": "终端/服务器代工",  # 工业富联
    "603986": "存储/光缆",      # 兆易创新
    "600487": "存储/光缆",      # 亨通光电
    # —— 512660 军工ETF 前十大（2026-06-30；F1-2 修复）——
    "600150": "船舶总装",        # 中国船舶
    "002179": "军工连接器",      # 中航光电
    "688002": "红外/光电",       # 睿创微纳
    "600879": "航天电子/无人系统",  # 航天电子
    "300395": "军工新材料",      # 菲利华
    "002625": "军工新材料",      # 光启技术（超材料）
    "600893": "航空发动机",      # 航发动力
    "600118": "卫星/航天",       # 中国卫星
    "600760": "航空整机",        # 中航沈飞
    "601698": "卫星/航天",       # 中国卫通
    # —— 588000 科创50ETF 前十大（2026-06-30；F1-2 修复）——
    "688256": "AI 芯片",        # 寒武纪
    "688041": "AI 芯片",        # 海光信息
    "688981": "晶圆代工",       # 中芯国际
    "688347": "晶圆代工",       # 华虹宏力
    "688012": "半导体设备",     # 中微公司
    "688072": "半导体设备",     # 拓荆科技
    "688008": "互连/接口芯片",  # 澜起科技
    "688498": "光芯片",         # 源杰科技
    "688525": "存储",           # 佰维存储
    "688521": "IP/设计服务",    # 芯原股份
}


# ---------------------------------------------------------------------------
# 申万行业 → 推荐估值指标（G9）
# ---------------------------------------------------------------------------

SECTOR_VALUATION_MAP: dict[str, dict] = {
    # 金融地产 — PB 为主要指标（利润受拨备/周期扭曲）
    "银行":       {"primary": "PB",  "secondary": "ROE",        "pe_timing": False, "reason": "利润受拨备周期扭曲，PB+ROE 更可靠"},
    "非银金融":    {"primary": "PB",  "secondary": "ROE",        "pe_timing": False, "reason": "强周期，保险看 EV"},
    "房地产":      {"primary": "PB",  "secondary": "净负债率",    "pe_timing": False, "reason": "政策驱动 > 估值驱动"},
    # 周期行业 — EV/EBITDA 或 PB
    "石油石化":    {"primary": "EV/EBITDA", "secondary": "PB",  "pe_timing": False, "reason": "油价驱动，PE 失真"},
    "煤炭":       {"primary": "PE",  "secondary": "股息率",      "pe_timing": True,  "reason": "盈利高位时注意 PE 虚低"},
    "有色金属":    {"primary": "EV/EBITDA", "secondary": "PB",  "pe_timing": False, "reason": "商品价格驱动"},
    "基础化工":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "部分子行业均值回归"},
    "钢铁":       {"primary": "PB",  "secondary": "股息率",      "pe_timing": False, "reason": "强周期，PE 高位=买点"},
    "建筑材料":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "均值回归特征强"},
    # 制造业
    "电力设备":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "新能源子行业不适用传统 PE（需结合 PEG/PS）"},
    "机械设备":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "部分子行业均值回归"},
    "国防军工":    {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "盈利不稳定，主题/政策驱动"},
    "汽车":       {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "强周期，需结合月度销量"},
    "家用电器":    {"primary": "PE",  "secondary": "股息率",      "pe_timing": True,  "reason": "消费属性强，均值回归"},
    # 消费行业 — 盈利增长消化高 PE
    "食品饮料":    {"primary": "PE",  "secondary": "ROE",        "pe_timing": False, "reason": "品牌壁垒使高盈利持续，高 PE 不意味必然回调"},
    "纺织服装":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "品牌/渠道分化大"},
    "轻工制造":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "子行业差异大"},
    "商贸零售":    {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "转型期，盈利不稳定"},
    "社会服务":    {"primary": "PE",  "secondary": "PS",         "pe_timing": False, "reason": "恢复期"},
    # TMT — 部分适用 PE，部分需 PS
    "电子":       {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "PE 时机选择在 A 股电子行业有效；半导体早期看 PS"},
    "计算机":      {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "亏损占比高，主题/政策驱动"},
    "通信":       {"primary": "PE",  "secondary": "EV/EBITDA",  "pe_timing": False, "reason": "高增长消化高 PE"},
    "传媒":       {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "主题/爆款驱动"},
    # 公用事业与基础设施
    "公用事业":    {"primary": "PE",  "secondary": "股息率",      "pe_timing": True,  "reason": "A 股 PE 时机选择最有效的行业之一，盈利稳定，均值回归"},
    "交通运输":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "部分子行业有效"},
    "建筑装饰":    {"primary": "PB",  "secondary": "PE",         "pe_timing": False, "reason": "政策驱动"},
    "环保":       {"primary": "PB",  "secondary": "PE",         "pe_timing": False, "reason": "政策驱动"},
    # 其他
    "农林牧渔":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "猪周期驱动，PE 波动极大"},
    "医药生物":    {"primary": "PE",  "secondary": "研发费用率",   "pe_timing": False, "reason": "创新药 vs 仿制药差异极大"},
    "美容护理":    {"primary": "PE",  "secondary": "PS",         "pe_timing": False, "reason": "高增长"},
    "综合":       {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "多元化企业，逐案分析"},
}


# ---------------------------------------------------------------------------
# 主查询函数
# ---------------------------------------------------------------------------

def query_etf_data(
    symbol: str,
    fund_code: str = "",
    *,
    spot_row: Any = None,
) -> dict[str, Any]:
    """查询 ETF 专属数据。

    Parameters
    ----------
    symbol : str
        ETF 代码（如 "563300"）。
    fund_code : str
        对应指数代码（csindex 格式，如 "932000"）。为空时从 CSINDEX_MAP 查找。
    spot_row : optional
        已解析的 fund_etf_spot_em 行（Series），避免重复拉全表。

    Returns
    -------
    dict
        {index_pe, index_pe_status, premium_discount, aum, tracking_error,
         hedge_coverage, flags, data_quality}
    """
    category = query_etf_category(symbol)
    identity = query_etf_identity(symbol, category=category.get("category", "unknown"))
    result: dict[str, Any] = {
        "symbol": symbol,
        "category": category,
        "identity": identity,
        "applicability": identity["applicability"],
        "valuation_applicability": {
            "pe_applicable": identity["applicability"]["index_pe"],
            "method": "index_official_only",
            "no_stock_pe_weighting": True,
            "reason": (
                "股票类：使用指数公司口径 PE（如映射可得）；禁止个股 PE 加权冒充指数 PE"
                if identity["applicability"]["index_pe"]
                else "非股票类/身份未核：不适用股票 PE 分析"
            ),
        },
        "index_pe": None,
        "index_pe_caliber": None,       # R2/T9-3：取值口径（总股本口径/计算用股本口径）
        # 兼容 alias：字段名沿用旧名（消费方按名读取），值为市盈率2（计算用股本口径）；
        # 不改数值、不重算历史（R3：只修口径显示与说明，不换字段名）
        "index_pe_circulating": None,
        "index_pe_pct": None,
        # 分位伴随中位数/样本数（report-conventions §9.2）：同窗口同口径
        "index_pe_history_median": None,
        "index_pe_history_n": None,
        "index_pe_history_column": None,
        "index_pe_status": "unknown_etf",
        "index_code": None,             # R12：统一解析的跟踪指数代码（PE/指数腿同源）
        "index_code_source": None,      # R12：CSINDEX_MAP / identity.tracked_index（状态）
        "industry_pe": None,
        "industry_pe_note": None,
        "industry_pe_status": None,     # R09：available/skipped_index_pe_present/…
        "valuation_guide": None,
        "industry_allocation": None,
        "premium_discount": None,
        "premium_discount_caliber": None,
        "share_price_mv_est": None,     # B02：份额×交易价代理市值（亿元）
        "aum": None,                    # 兼容别名（= share_price_mv_est.value_yi，下版本退出）
        "aum_deprecated_note": (
            "旧键 aum=share_price_mv_est；口径为份额×交易价估算，非官方资产净值"
        ),
        "tracking_error": None,
        "tracking_error_note": (
            "跟踪误差需 ETF 净值与指数点位序列对比，当前引擎未实现；"
            "请勿填写估算数字"
        ),
        "hedge_coverage": _lookup_hedge(symbol),
        "flags": [],
        "_errors": [],
        "data_quality": {},
    }

    # R12：指数代码统一解析（显式参数 > CSINDEX_MAP > 已核身份）——PE 取数不再
    # 只看 CSINDEX_MAP，避免「报告无映射、tracking 腿却用该指数」的自相矛盾
    idx_res = resolve_tracked_index(symbol, fund_code or None, identity=identity)
    idx_code = idx_res["code"] or ""
    result["index_code"] = idx_code or None
    result["index_code_source"] = idx_res["source"]
    _set_index_pe_status(result, symbol, idx_code, pe_mapped=idx_res["pe_mapped"],
                         source=idx_res["source"])
    # A5 修正（2026-10-10）：csindex PE 文件按 6 位数字代码索引——身份登记的
    # 非 csindex 指数（如恒指 HSI）不在该源覆盖范围：不发起无谓取数，也不把
    # 「源不覆盖」标成「已映射但取数失败（missing）」。状态改记 not_mapped +
    # 措辞说明（该指数估值须按其指数公司原文另核）。
    if idx_code.isdigit() and len(idx_code) == 6:
        _fetch_csindex_pe(result, idx_code)
    elif idx_code:
        result["index_pe_status"] = "not_mapped"
        result["index_pe_note"] = (
            f"跟踪指数代码 {idx_code} 来源={idx_res['source'] or '未知'}：不属于 csindex "
            "指数 PE 文件覆盖范围（该源按 6 位数字代码索引）——指数 PE 未接线"
            "（非源失败）；该指数估值口径须按其指数公司原文另核（本次未获则如实标注）")

    # G4: 行业 ETF — 从 industry_weekly 查行业 PE 作为估值参考
    _attach_industry_pe(result, symbol)

    # G6: ETF 行业配置比例（akshare fund_portfolio_industry_allocation_em）
    _attach_industry_allocation(result, symbol)

    # G9: 附加行业特定估值指标指引
    _attach_valuation_guide(result)

    if spot_row is not None:
        _apply_spot_row_to_profile(result, spot_row, symbol)
    else:
        row, err = _lookup_etf_spot_row(symbol)
        if err:
            result["_errors"].append(err)
        elif row is not None:
            _apply_spot_row_to_profile(result, row, symbol)

    _auto_flags(result, result["category"].get("category", ""))
    result["data_quality"] = _summarize_etf_data_quality(result)
    return result


# ---------------------------------------------------------------------------
# ETF spot 缓存与查询
# ---------------------------------------------------------------------------

def prefetch_etf_spot() -> bool:
    """预热 spot 全表。优先走 data_bridge L2（热时零网络）；L2 不可用则强制回源写 L1。

    L2 取回（含命中）后回填 L1（30s 进程内缓存），使后续 _lookup_etf_spot_row
    走内存而非每次全表磁盘读 + 线性扫描（v0.2.3 补丁 #5）。

    空表/失败一律返回 False（与旧语义一致）：L2 路径下 `[] is not None`
    曾把空表误报为成功（v0.2.3 修复），隐藏失败的预热。
    """
    try:
        import data_bridge  # noqa: PLC0415
    except ImportError:
        return _get_etf_spot_df(force=True) is not None
    rows = data_bridge.get_etf_spot_rows()
    if rows:
        _seed_etf_spot_l1(rows)
        return True
    return False


def clear_etf_spot_cache() -> None:
    """清空**进程内**（L1）spot 缓存。

    测试隔离公共 API（test_etf_spot_cache/test_etf_cache_fixes 的 fixture
    依赖它跨测试清 L1，保证 canonical 实例的缓存不跨用例泄漏）。
    L2（data_bridge 磁盘缓存）由 data_bridge.invalidate_symbol("market")
    或测试的 tmp cache_dir 隔离负责，此处不触碰。
    """
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS, _SPOT_FAIL_TS, _SPOT_FAIL_REASON
    with _SPOT_CACHE_LOCK:
        _SPOT_CACHE_DF = None
        _SPOT_CACHE_TS = 0.0
        _SPOT_FAIL_TS = 0.0
        _SPOT_FAIL_REASON = ""


def _peek_etf_spot_df() -> Any:
    """纯缓存检查：L1 新鲜则返回 df，否则 None；绝不触发网络。"""
    with _SPOT_CACHE_LOCK:
        if (
            _SPOT_CACHE_DF is not None
            and (time.monotonic() - _SPOT_CACHE_TS) < _SPOT_CACHE_TTL_SEC
        ):
            return _SPOT_CACHE_DF.copy()
    return None


def _get_etf_spot_df(*, force: bool = False) -> Any:
    """带锁 + TTL 的 fund_etf_spot_em 全表缓存（L1，进程内 30s）。

    失败负缓存（A7 补强）：EM 断连时该**全市场端点**每次重试约 5s，一次 report
    会为每只标的重复回源（6+ 次无谓全表请求）——失败后 ``_SPOT_FAIL_TTL_SEC``
    秒内不再发起（成功即清；到期自动允许再试，源恢复不被永久屏蔽）。
    `force=True` 显式绕过负缓存（仅桥不可用的显式预热回退使用）。
    """
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS, _SPOT_FAIL_TS, _SPOT_FAIL_REASON
    if not force:
        cached = _peek_etf_spot_df()
        if cached is not None:
            return cached
        if _SPOT_FAIL_TS and (time.monotonic() - _SPOT_FAIL_TS) < _SPOT_FAIL_TTL_SEC:
            logger.debug("etf_spot negative cache: skip refetch（%.0fs 前失败: %s）",
                         time.monotonic() - _SPOT_FAIL_TS, _SPOT_FAIL_REASON)
            return None
    # 释放锁后拉取数据（网络 I/O 不占锁，避免串行化）
    try:
        import akshare as ak

        with akshare_direct_session():
            df = ak.fund_etf_spot_em()
    except Exception as exc:
        logger.warning("fund_etf_spot_em failed: %s", exc)
        _SPOT_FAIL_TS = time.monotonic()
        _SPOT_FAIL_REASON = str(exc)
        return None
    if df is None or df.empty:
        _SPOT_FAIL_TS = time.monotonic()
        _SPOT_FAIL_REASON = "empty response"
        return None
    # 重新获取锁，更新缓存
    with _SPOT_CACHE_LOCK:
        _SPOT_CACHE_DF = df
        _SPOT_CACHE_TS = time.monotonic()
        _SPOT_FAIL_TS = 0.0
        _SPOT_FAIL_REASON = ""
        return df


def _seed_etf_spot_l1(records: list[dict]) -> None:
    """用 L2/网络取回的 records 回填 L1 进程内缓存（prefetch 预热用）。

    保证 prefetch 后进程内后续 _lookup_etf_spot_row 走内存而非每次
    全表磁盘读 + 线性扫描（v0.2.3 补丁 #5：L2 命中时 L1 曾永远不热）。
    """
    if not records:
        return
    import pandas as pd
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS
    with _SPOT_CACHE_LOCK:
        _SPOT_CACHE_DF = pd.DataFrame(records)
        _SPOT_CACHE_TS = time.monotonic()


def _lookup_etf_spot_row(symbol: str) -> tuple[Any | None, str | None]:
    """从 L1（进程内 30s）→ L2（data_bridge 60s）→ 网络 查找单只 ETF spot 行。

    返回 (row, error)：row 为 pandas Series（L1 路径）或 dict（L2 路径），
    两者均支持 .get()，下游 _spot_row_to_quote / _apply_spot_row_to_profile 不变。
    """
    df = _peek_etf_spot_df()
    l1_fresh = df is not None
    if l1_fresh:
        row_df = df[df["代码"] == symbol]
        if not row_df.empty:
            return row_df.iloc[0], None
        # L1 新鲜但未命中该符号（如新上市/盘中异动）：继续查 L2，
        # 避免 30s L1 窗口内屏蔽掉更新更完整的 L2 数据（v0.2.3 修复）
    rows = _bridge_get("get_etf_spot_rows")
    if rows is None:
        if l1_fresh:
            # L2/网络不可用：保留 L1 判定结果（与旧行为一致）
            return None, f"etf_spot: {symbol} not found"
        # L1 冷且 L2/网络失败：无法获取任何数据（保持旧错误语义）
        return None, "etf_spot: empty response"
    for r in rows:
        if str(r.get("代码")) == symbol:
            return r, None
    return None, f"etf_spot: {symbol} not found"


def _read_via(env: Any) -> str:
    """本次读取路径标签：L2 缓存命中 vs 本次网络（B-F01）。

    data_bridge 经 ``DataCache.get`` 返回的 dict 信封带 ``_from_cache=True``
    （缓存层实现，只读标记）；据此区分「缓存读取」与「本次网络取数」，
    不冒充来源、不猜。

    R05：``env=None`` 不得推断为「本次网络」——None 的两种含义（桥不可用 =
    未发起；桥内失败/无信封 = 已发起但结果不可得）由 :func:`_bridge_availability`
    与 :func:`_leg_call_state` 分辨，本函数对 None 只报「路径未留痕」。
    """
    if isinstance(env, dict) and env.get("_from_cache"):
        return "L2 缓存读取"
    if env is None:
        return "路径未留痕（无信封）"
    return "本次网络"


def _bridge_availability(getter: str) -> dict:
    """桥可用性探针（R05）：不取数，只回答「调用被判为已发起还是未发起」。

    ``_bridge_get`` 返回 None 时无法从 None 本身区分两种含义：

    - data_bridge 不可导入 / getter 不存在 → **未发起**（未接线，谈不上网络）；
    - 桥可用但返回 None / 抛异常 → **已发起但失败或路径未留痕**。

    探针只做 import 与属性检查（零取数、零网络），供 ``_read_via`` 的调用方
    与 ``_leg_call_state`` 使用。
    """
    try:
        import data_bridge  # noqa: PLC0415
    except ImportError:
        return {"available": False, "reason": "data_bridge 模块不可导入"}
    if getattr(data_bridge, getter, None) is None:
        return {"available": False, "reason": f"data_bridge.{getter} 不存在（未接线）"}
    return {"available": True, "reason": None}


def _leg_call_state(env: Any, *, getter: str) -> dict:
    """数据腿调用状态（R05/R06/R07 统一留痕，不猜）：

    ``{"attempted": bool, "skip_reason": str|None, "via": str}``

    - ``env is None`` → 探针判桥可用性：桥/getter 不可用 = 未发起（带原因）；
      桥可用 = 已发起但失败/缺追溯；
    - 信封带 ``attempted=False``（producer 结构化跳过，如无 token）→ 未发起，
      原因取信封 note/``skip_reason``；
    - 其余信封 → 已发起，路径标签沿用 ``_read_via``（L2 缓存/本次网络）。
    """
    if env is None:
        probe = _bridge_availability(getter)
        if probe["available"]:
            return {"attempted": True, "skip_reason": None,
                    "via": "已发起但失败/缺追溯（桥内无信封）"}
        return {"attempted": False,
                "skip_reason": f"未发起取数：{probe['reason']}",
                "via": "未发起（桥不可用）"}
    if env.get("attempted") is False:
        return {"attempted": False,
                "skip_reason": env.get("note") or env.get("skip_reason")
                or "producer 标记未发起取数",
                "via": "未发起（producer 留痕）"}
    return {"attempted": True, "skip_reason": None, "via": _read_via(env)}


def _bridge_get(getter: str, *args: Any) -> Any:
    """函数体内惰性调 skills/lib data_bridge；不可用时返回 None（查询侧视为 missing）。

    调用本身也包异常：缓存层（_fetch_dimension→collector）抛出的 OSError/
    UnicodeDecodeError 等不应让报告流程崩溃，按查询侧 missing 降级
    （v0.2.3 补丁 #6）。

    返回 None 的语义**不唯一**（未发起 vs 已发起但失败）：需要如实留痕的调用方
    用 :func:`_leg_call_state` / :func:`_bridge_availability` 分辨，不得用 None 猜。
    """
    try:
        import data_bridge  # noqa: PLC0415
    except ImportError:
        logger.debug("data_bridge unavailable; %s degraded", getter)
        return None
    fn = getattr(data_bridge, getter, None)
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception as exc:
        logger.warning("data_bridge %s(%s) raised %r; degraded to missing", getter, args, exc)
        return None


# ---------------------------------------------------------------------------
# data_bridge L2 取数层（fetch_*，canonical 公开原始取数）
# 信封统一带 status（"ok" / "missing"），使 data_bridge 的失败不缓存语义
# （_FAILURE_STATUSES）直接生效；参数全部在 fetch 内部固定，不透出 kwargs。
# ---------------------------------------------------------------------------

def fetch_etf_spot_rows() -> list[dict] | None:
    """原始取数：fund_etf_spot_em 全表 → records（data_bridge etf_spot 维度）。

    经 _get_etf_spot_df（L1 30s 进程内缓存）去重；失败/空表返回 None（不缓存）。
    """
    df = _get_etf_spot_df()
    if df is None:
        return None
    return df.to_dict("records")


# csindex 两个 PE 口径的展示名（R2/T9-3：口径须显式，不得混用后仍标 PE(1)；
# R3 按 AKShare 官方接口文档的字段定义命名，不自行改称）：
# `stock_zh_index_value_csindex` 输出「市盈率1：注意：（总股本）P/E1」、
# 「市盈率2：注意：（计算用股本）P/E2」（来源见 SOURCE_PROVENANCE.index_pe_csindex）。
# 「市盈率2」不得直接命名「流通加权」（官方字段未如此定义）。
_PE_CALIBER_SHARE = "总股本口径（市盈率1）"          # 官方字段：市盈率1（总股本）
_PE_CALIBER_CIRCULATING = "计算用股本口径（市盈率2）"  # 官方字段：市盈率2（计算用股本）


def fetch_etf_index_pe(idx_code: str) -> dict:
    """原始取数：csindex 指数 PE（data_bridge etf_index_pe 维度）。

    PE 取不到时返回 status="missing"（而非 ok+None），避免"无 PE"状态被
    1d TTL 缓存住。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_zh_index_value_csindex(symbol=idx_code)
        if df is None or df.empty:
            return {"status": "missing", "index_pe": None, "index_pe_caliber": None,
                    "index_pe_circulating": None, "index_pe_note": None,
                    "rows": [], "error": "csindex empty response"}
        if "日期" in df.columns:
            # csindex 返回新日期在前；显式按日期升序后取末行，不依赖返回顺序
            # （2026-08-22 实测 iloc[-1] 取到最早行 → index_pe 滞后约 3.5 周）
            # NaN 日期行先剔除（pandas 升序 NaN 置末，会污染 iloc[-1] 取行）
            df = df.dropna(subset=["日期"])
            # 双 PE 列均 NaN 的行同样剔除（csindex 当日 PE 未生成的瞬时缺口，
            # 否则 iloc[-1] 取到 NaN 行 → 假 missing / 分位跳日；对齐 journal
            # market_microstructure.py:800 已剔 PE 列的孪生逻辑）。任一列有效
            # 即保留（市盈率2（计算用股本）在部分指数缺列不应连坐）。
            pe_cols = [c for c in ("市盈率1", "市盈率2") if c in df.columns]
            if pe_cols:
                df = df.dropna(subset=pe_cols, how="all")
            df = df.sort_values("日期")
            if df.empty:
                return {"status": "missing", "index_pe": None, "index_pe_caliber": None,
                        "index_pe_circulating": None, "index_pe_note": None,
                        "rows": [], "error": "csindex rows empty after date dropna"}
        else:
            # 列名漂移守卫（review #3）：无「日期」列时无法排序，沿用原始行序取末行，
            # 记录警告使取行偏移 fail-loud，不复现静默滞后
            logger.warning(
                "csindex_pe(%s): 无「日期」列（列名漂移?），沿用原始行序取末行，"
                "可能存在取行偏移", idx_code)
        latest = df.iloc[-1]
        pe1 = safe_float(latest.get("市盈率1"))
        pe2 = safe_float(latest.get("市盈率2"))
        pe = pe1 if pe1 is not None else pe2
        # 口径必须**显式**（R2/T9-3）：上方回落是静默的，若消费者按「PE(1) 总股本」
        # 标注就会误标——累积表 index_pe_history.pe 只存市盈率1，两处由此表现不一致
        # （实测 58.49 vs 54.92，即为两种口径之差）。这里同时暴露两个口径（R3 按
        # AKShare 官方字段说明命名；字段名 index_pe_circulating 保留为兼容 alias）。
        caliber = (_PE_CALIBER_SHARE if pe1 is not None
                   else (_PE_CALIBER_CIRCULATING if pe2 is not None else None))
        return {
            "status": "ok" if pe is not None else "missing",
            "index_pe": pe,
            "index_pe_caliber": caliber,
            "index_pe_circulating": pe2,
            "index_pe_note": (
                f"来源: csindex {idx_code}，单窗 {len(df)} 条历史，"
                # 2026-09-19：注明取值日期。此前只给口径不给日期，读者无法知道该 PE
                # 是不是最新交易日（600519/588000 实跑暴露：源已含 09-18 行而报告取到
                # 09-17 值，且输出无任何日期线索）。日期缺失时留「日期不可得」不编造。
                f"取值日期 {latest.get('日期') or '不可得'}；"
                f"本次取值口径＝{caliber or '不可得'}"
                "（口径按 AKShare `stock_zh_index_value_csindex` 官方字段："
                "市盈率1=（总股本）P/E1；市盈率2=（计算用股本）P/E2；"
                "历史分位见 index_pe_pct）"
            ),
            "rows": df.to_dict("records"),
            "error": None,
        }
    except Exception as exc:
        # 404 属确定性资源缺失（指数代码无 csindex PE 文件），且 missing 信封
        # 不缓存 → 每次报告重复打印；debug 级静默降级（对齐同文件 silent degrade
        # 惯例），调用方仍凭 status="missing" 判断
        logger.debug("csindex_pe(%s) failed, silent degrade (fallback probe): %s",
                     idx_code, exc)
        return {"status": "missing", "index_pe": None, "index_pe_caliber": None,
                "index_pe_circulating": None, "index_pe_note": None,
                "rows": [], "error": str(exc)}


def fetch_etf_nav(symbol: str) -> dict:
    """原始取数：ETF 历史单位净值（data_bridge etf_nav 维度）。

    固定 _NAV_FETCH_NATURAL_DAYS 自然日窗口；主源 fund_etf_fund_info_em，
    akshare 列数变更（Length mismatch）时降级 fund_open_fund_info_em
    （fallback 无日期参数，须按同一窗口过滤全量历史，防止旧 ETF 全量
    入 L2 缓存——v0.2.3 修复）。
    rows 归一化为 [{date, nav, change_pct}]。
    """
    import akshare as ak

    end_date = shanghai_today()
    start_date = shanghai_days_ago(_NAV_FETCH_NATURAL_DAYS)
    start_key = start_date.replace("-", "")

    df = None
    source = "fund_etf_fund_info_em"
    try:
        with akshare_direct_session():
            df = ak.fund_etf_fund_info_em(fund=symbol, start_date=start_date, end_date=end_date)
    except Exception as exc:
        msg = str(exc)
        if "Length mismatch" in msg or "Expected axis has" in msg:
            logger.info(
                "fund_etf_fund_info_em column mismatch, falling back to fund_open_fund_info_em: %s",
                exc,
            )
            try:
                with akshare_direct_session():
                    df = ak.fund_open_fund_info_em(symbol=symbol, indicator="单位净值走势")
                source = "fund_open_fund_info_em"
            except Exception as fb_exc:
                logger.warning("fund_open_fund_info_em fallback also failed: %s", fb_exc)
                return {"status": "missing", "source": source, "rows": [],
                        "error": f"fund_etf_fund_info_em: {exc}; fallback: {fb_exc}"}
        else:
            raise

    if df is None or df.empty:
        return {"status": "missing", "source": source, "rows": [], "error": f"{source}: empty response"}

    rows = []
    for _, r in df.iterrows():
        d = str(r.get("净值日期", "")).replace(" 00:00:00", "")[:10]
        nav = safe_float(r.get("单位净值"))
        # 日期窗口过滤（YYYYMMDD 字典序比较）：fallback 全量历史在此截断
        if d and nav is not None and d.replace("-", "")[:8] >= start_key:
            rows.append({"date": d, "nav": nav, "change_pct": safe_float(r.get("日增长率"))})
    if not rows:
        # df 非空但窗口过滤/解析后无有效行（数据停更或列格式漂移）→ missing，
        # 避免 ok+[] 空信封被 L2 缓存 1 天（v0.2.3 补丁 #3）
        return {"status": "missing", "source": source, "rows": [],
                "error": f"{source}: 无有效净值行（窗口过滤后为空）"}
    return {"status": "ok", "source": source, "rows": rows, "error": None}


def fetch_etf_index_daily(idx_code: str) -> dict:
    """原始取数：指数日 K 收盘（data_bridge etf_index_daily 维度）。

    sh/sz 前缀路由在 fetch 内（不参与缓存键）；只保留最近 700 自然日
    （≈470 交易日，MA60 需 ~75 交易日缓冲，裕量约 6 倍；若未来加长
    周期 MA 需同步上调此窗口）。
    """
    try:
        import akshare as ak

        # csindex 代码 → akshare 行情代码前缀路由
        # 0xxxxx / 9xxxxx → 上交所发布（sh）；1xxxxx / 3xxxxx → 深交所发布（sz）
        if idx_code.startswith(("0", "9")):
            ticker = f"sh{idx_code}"
        elif idx_code.startswith(("1", "3")):
            ticker = f"sz{idx_code}"
        else:
            logger.debug("fetch_etf_index_daily(%s): unrecognized csindex prefix '%s', defaulting to sz",
                         idx_code, idx_code[0] if idx_code else "")
            ticker = f"sz{idx_code}"
        with akshare_direct_session():
            df = ak.stock_zh_index_daily(symbol=ticker)
        if df is None or df.empty:
            return {"status": "missing", "ticker": ticker, "rows": [], "error": "index daily empty"}
        df = df.tail(700)
        rows = []
        for _, r in df.iterrows():
            close = safe_float(r.get("close"))
            if close is None:
                continue
            rows.append({"date": str(r.get("date", ""))[:10], "close": close})
        if not rows:
            # 全行 close 解析失败（列格式漂移）→ missing，避免 ok+[] 被 L2 缓存
            return {"status": "missing", "ticker": ticker, "rows": [],
                    "error": "index daily 无有效 close"}
        return {"status": "ok", "ticker": ticker, "rows": rows, "error": None}
    except Exception as exc:
        logger.debug("fetch_etf_index_daily(%s): failed, silent degrade: %s", idx_code, exc)
        return {"status": "missing", "ticker": None, "rows": [], "error": str(exc)}


def fetch_etf_adj_factor(symbol: str) -> dict:
    """原始取数：Tushare fund_adj 复权因子（data_bridge etf_adj_factor 维度）。

    B-F01：区分「无 token 跳过（未发起网络）」与「已发起但失败/空」——
    前者不构成一次源尝试，消费方不得登记为 attempted。
    R07：跳过/尝试由**结构化字段** ``attempted``/``skip_reason`` 表达，
    消费方不再对 ``note`` 做子串匹配（文案可变，结构化标记不变）。
    """
    from lib.env import get_config

    if not (get_config() or {}).get("TUSHARE_TOKEN"):
        return {"status": "missing", "adj_map": None,
                "attempted": False, "skip_reason": "no_token",
                "note": "无 TUSHARE_TOKEN：未发起网络（跳过）"}
    if not etf_symbol_to_ts_code(symbol):
        # F5（独立复检）：代码无效时 ts_code 判空即返回，Tushare client 从未创建
        # （实测 client_calls=0）——不是一次尝试，不得记 attempted=True
        return {"status": "missing", "adj_map": None,
                "attempted": False, "skip_reason": "invalid_symbol",
                "note": "无效的 ETF 代码：未发起网络（跳过）"}
    adj_map = _fetch_fund_adj_factor(symbol)
    if not adj_map:
        return {"status": "missing", "adj_map": None,
                "attempted": True, "skip_reason": None,
                "note": "fund_adj 查询失败/空返回"}
    return {"status": "ok", "adj_map": adj_map}


def fetch_etf_share_history(symbol: str) -> dict:
    """原始取数：Tushare fund_share + fund_daily（data_bridge etf_share_history 维度）。

    固定 _SHARE_FETCH_NATURAL_DAYS 自然日窗口；token/ts_code/积分检查失败 → status="missing"。
    R06/R07：逐分支带结构化 ``attempted``/``skip_reason``（no_token / invalid_symbol
    未发起；no_data / query_failed 已发起），消费方沿用而不硬编码源名与原因。
    """
    from lib import env
    from lib.tushare_client import TushareClient

    config = env.get_config()
    token = config.get("TUSHARE_TOKEN")
    if not token:
        return {"status": "missing", "fund_share": [], "fund_daily": [],
                "attempted": False, "skip_reason": "no_token", "note": "Tushare 不可用"}

    ts_code = etf_symbol_to_ts_code(symbol)
    if not ts_code:
        return {"status": "missing", "fund_share": [], "fund_daily": [],
                "attempted": False, "skip_reason": "invalid_symbol", "note": "无效的 ETF 代码"}

    end_date = shanghai_today()
    start_date = shanghai_days_ago(_SHARE_FETCH_NATURAL_DAYS)

    try:
        client = TushareClient(token=token, timeout=15)
        shares_df = client.query("fund_share", ts_code=ts_code,
                                 start_date=start_date, end_date=end_date)
        if shares_df is None or shares_df.empty:
            return {"status": "missing", "fund_share": [], "fund_daily": [],
                    "attempted": True, "skip_reason": "no_data",
                    "note": _NOTE_SHARE_NO_DATA}
        daily_df = client.query("fund_daily", ts_code=ts_code,
                                start_date=start_date, end_date=end_date)
        if daily_df is None or daily_df.empty:
            return {"status": "missing", "fund_share": [], "fund_daily": [],
                    "attempted": True, "skip_reason": "no_data",
                    "note": _NOTE_DAILY_NO_DATA}
    except Exception as exc:
        return {"status": "missing", "fund_share": [], "fund_daily": [],
                "attempted": True, "skip_reason": "query_failed",
                "note": f"Tushare 查询失败: {exc}"}

    return {
        "status": "ok",
        "fund_share": shares_df.to_dict("records"),
        "fund_daily": daily_df.to_dict("records"),
        "note": None,
    }


def fetch_etf_industry_alloc(symbol: str) -> dict:
    """原始取数：ETF 行业配置（data_bridge etf_industry_alloc 维度，季度报告期）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.fund_portfolio_industry_allocation_em(symbol=symbol, date=str(datetime.now().year))
        if df is None or df.empty:
            return {"status": "missing", "allocation": [], "latest_date": None, "error": "empty"}
        latest_date = str(sorted(df["截止时间"].unique())[-1])
        latest = df[df["截止时间"] == latest_date]
        alloc: list[dict] = []
        for _, row in latest.iterrows():
            industry = str(row.get("行业类别", ""))
            pct = safe_float(row.get("占净值比例"))
            if industry and pct is not None and pct > 0:
                alloc.append({"industry": industry, "pct": round(pct, 2)})
        alloc.sort(key=lambda x: x["pct"], reverse=True)
        return {"status": "ok" if alloc else "missing", "allocation": alloc,
                "latest_date": latest_date, "error": None}
    except Exception as exc:
        logger.debug("fetch_etf_industry_alloc(%s): failed, silent degrade: %s", symbol, exc)
        return {"status": "missing", "allocation": [], "latest_date": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# 指数样本 / 权重（B01-2，v0.3.2）：csindex 官方公开文件（公开源，非付费）
# ---------------------------------------------------------------------------
# 三列分列铁律：指数样本/权重（指数公司）≠ 基金披露持仓（季报）≠ PCF（申赎篮子）。
# 成分文件与权重文件**发布节奏不同**（cons 更新至最新月末/季末，closeweight 通常
# 为上月月末）——只有两份文件日期相同（同日）才允许拼成组合，否则只能分别陈述。


def _csindex_date_iso(v: Any) -> str | None:
    """csindex 文件日期 → ISO（YYYY-MM-DD）；解析不了返回 None（不猜测）。"""
    if v is None:
        return None
    s = str(v)[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    s8 = str(v).replace("-", "")[:8]
    if len(s8) == 8 and s8.isdigit():
        return f"{s8[:4]}-{s8[4:6]}-{s8[6:8]}"
    return None


def fetch_etf_index_cons(idx_code: str) -> dict:
    """原始取数：csindex 指数成分文件（data_bridge 维度建议 etf_index_cons）。

    来源=中证指数公司公开 cons 文件（经 akshare index_stock_cons_csindex）；
    失败返回 status="missing"（不缓存语义由调用侧遵守）。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.index_stock_cons_csindex(symbol=idx_code)
        if df is None or df.empty:
            return {"status": "missing", "index_code": idx_code, "as_of": None,
                    "rows": [], "error": "csindex cons empty response"}
        as_of = _csindex_date_iso(df["日期"].iloc[0]) if "日期" in df.columns else None
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("成分券代码", "")).strip()
            if not code:
                continue
            rows.append({"code": code, "name": str(r.get("成分券名称", "")).strip(),
                         "exchange": str(r.get("交易所", "")).strip()})
        if not rows:
            return {"status": "missing", "index_code": idx_code, "as_of": as_of,
                    "rows": [], "error": "csindex cons 无有效行（列名漂移?）"}
        return {"status": "ok", "index_code": idx_code, "as_of": as_of,
                "rows": rows, "row_count": len(rows), "error": None}
    except Exception as exc:
        logger.debug("fetch_etf_index_cons(%s): failed, silent degrade: %s", idx_code, exc)
        return {"status": "missing", "index_code": idx_code, "as_of": None,
                "rows": [], "error": str(exc)}


def fetch_etf_index_weight(idx_code: str) -> dict:
    """原始取数：csindex 指数权重文件（data_bridge 维度建议 etf_index_weight）。

    来源=中证指数公司公开 closeweight 文件（经 akshare
    index_stock_cons_weight_csindex）；`weight_sum_pct` 原样披露（不得因
    不足/超过 100% 而重归一化）。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.index_stock_cons_weight_csindex(symbol=idx_code)
        if df is None or df.empty:
            return {"status": "missing", "index_code": idx_code, "as_of": None,
                    "rows": [], "weight_sum_pct": None,
                    "error": "csindex closeweight empty response"}
        as_of = _csindex_date_iso(df["日期"].iloc[0]) if "日期" in df.columns else None
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("成分券代码", "")).strip()
            w = safe_float(r.get("权重"))
            if not code or w is None:
                continue
            rows.append({"code": code, "name": str(r.get("成分券名称", "")).strip(),
                         "weight_pct": w})
        if not rows:
            return {"status": "missing", "index_code": idx_code, "as_of": as_of,
                    "rows": [], "weight_sum_pct": None,
                    "error": "csindex closeweight 无有效行（列名漂移?）"}
        return {"status": "ok", "index_code": idx_code, "as_of": as_of,
                "rows": rows, "row_count": len(rows),
                "weight_sum_pct": round(sum(r["weight_pct"] for r in rows), 3),
                "error": None}
    except Exception as exc:
        logger.debug("fetch_etf_index_weight(%s): failed, silent degrade: %s", idx_code, exc)
        return {"status": "missing", "index_code": idx_code, "as_of": None,
                "rows": [], "weight_sum_pct": None, "error": str(exc)}


def query_etf_index_exposure(symbol: str, idx_code: str | None = None) -> dict:
    """指数样本/权重按需查询 + 同期性裁决（B01-2；默认不接入报告主链）。

    - `cons`/`weight` 分列，各自带 `as_of`/行数/来源；`weight` 原样带
      `weight_sum_pct`（缺失权重不得按 0、不得重归一化）；
    - `period_alignment.joinable=True` **仅当两份文件日期完全相同**；错月
      → False + note（只可分别陈述，禁止拼「当期组合」）；
    - PCF 不在本函数（见 holdings.pcf）：篮子 ≠ 权重，且未接入。
    """
    idx = idx_code or CSINDEX_MAP.get(symbol, "")
    if not idx:
        ident = query_etf_identity(symbol)
        tracked = (ident.get("tracked_index") or {}).get("code")
        idx = tracked or ""
    if not idx:
        return {"symbol": symbol, "index_code": None, "status": "not_mapped",
                "cons": None, "weight": None,
                "period_alignment": None,
                "scope_note": INDEX_SCOPE_NOTE,
                "note": "无指数代码映射（CSINDEX_MAP/身份登记均无）；按需人工核实跟踪指数"}
    # data_bridge 维度接线属 A 市场 owner（本包不动 data_bridge）：
    # getter 不存在时 _bridge_get 返回 None → 直接回源（见交接「接口请求」）。
    cons = _bridge_get("get_etf_index_cons", idx)
    if cons is None:
        cons = fetch_etf_index_cons(idx)
    weight = _bridge_get("get_etf_index_weight", idx)
    if weight is None:
        weight = fetch_etf_index_weight(idx)
    cons_ok = cons.get("status") == "ok"
    weight_ok = weight.get("status") == "ok"
    if not cons_ok and not weight_ok:
        status = "missing"
    elif cons_ok and weight_ok:
        status = "ok"
    else:
        status = "partial"
    same_period: bool | None = None
    joinable = False
    if cons_ok and weight_ok:
        same_period = bool(cons.get("as_of") and cons["as_of"] == weight["as_of"])
        joinable = same_period
    pa = {
        "same_period": same_period,
        "joinable": joinable,
        "cons_as_of": cons.get("as_of") if cons_ok else None,
        "weight_as_of": weight.get("as_of") if weight_ok else None,
        "note": (
            "两份文件日期相同，可在声明来源日期后拼为组合"
            if joinable else
            "成分与权重文件日期不同（或有一侧缺失）：只可分别陈述，"
            "禁止拼成「当期完整组合」"
        ),
    }
    return {"symbol": symbol, "index_code": idx, "status": status,
            "cons": cons if cons_ok else None,
            "weight": weight if weight_ok else None,
            "cons_status": cons.get("status"),
            "weight_status": weight.get("status"),
            "period_alignment": pa,
            "scope_note": INDEX_SCOPE_NOTE,
            "source_records": {
                "cons": SOURCE_PROVENANCE["etf_index_cons"],
                "weight": SOURCE_PROVENANCE["etf_index_weight"],
            },
            "note": None if status == "ok" else "指数样本/权重源缺失（见 cons_status/weight_status）"}


#: 指数 vs 基金持仓 vs PCF 的职责分列声明（措辞铁律的单一出处）
INDEX_SCOPE_NOTE = (
    "指数样本/权重描述规则暴露（指数公司文件）；基金披露持仓描述历史基金资产"
    "（季报，仅前十大）；PCF 描述指定申赎单位的篮子（未接入）——三者不得互相冒充"
)


# ---------------------------------------------------------------------------
# 前十大持仓（裸 HTTP：akshare fund_portfolio_hold_em 已坏 — EM 页面结构变更
# → demjson JSONDecodeError，v0.2.5 R12 起自写 fetch）
# ---------------------------------------------------------------------------

_EM_F10_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
_EM_F10_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://fundf10.eastmoney.com/ccmx_{symbol}.html",
}


def _decode_em_page(resp) -> str:
    """utf-8 优先（实测 HTTPS 端点 charset=utf-8），失败回退 gbk（旧端点）。"""
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode("gbk", errors="replace")


def _thead_column_indexes(table_block: str):
    """表头列定位：归一化 th 文本 → {code/name/pct/shares/amount: 列索引}。

    表头列历史上多次漂移（最新价/涨跌幅列曾增删、列序变化）→ 优先按表头名
    定位；表头不可解析时返回 None（调用方回退负索引兜底）。
    """
    import html as _html
    import re as _re

    head_m = _re.search(r"<thead>(.*?)</thead>", table_block, _re.S)
    if not head_m:
        return None
    ths = []
    for t in _re.findall(r"<th[^>]*>(.*?)</th>", head_m.group(1), _re.S):
        text = _re.sub(r"<[^>]+>", "", t)  # 去标签（含 <br />）
        text = _html.unescape(text).replace(" ", " ").strip()
        ths.append(text)
    if not ths:
        return None
    idx: dict[str, int] = {}

    def find(key: str) -> int | None:
        for i, t in enumerate(ths):
            if key in t:
                return i
        return None

    for field, key in (
        ("code", "股票代码"),
        ("name", "股票名称"),
        ("pct", "占净值"),
        ("shares", "持股"),
        ("amount", "持仓市值"),
    ):
        i = find(key)
        if i is None:
            return None
        idx[field] = i
    return idx


def _parse_holdings_blocks(content: str) -> list[dict]:
    """按 h4 块切分季度报告期，每块 = {report_date, quarter, rows:[...]}。

    列定位仅依赖表头（_thead_column_indexes）——历史列序多次漂移，负索引在
    「相关资讯列在末位」等旧布局下会静默错位（pct←持股、shares←市值），
    因此表头不可解析时**跳过该块**（fail loud → 上层 missing 信封），
    绝不猜测列位置；<td> 数 <6 的行跳过（thead 行、空行防御）。
    """
    import html as _html
    import re as _re

    blocks: list[dict] = []
    for m in _re.finditer(
        r"<h4[^>]*>(.*?)</h4>.*?<table.*?</table>", content, _re.S
    ):
        h4 = m.group(1)
        date_m = _re.search(r"截止至：\s*<font[^>]*>([\d\-]+)</font>", h4)
        if not date_m:
            continue
        q_m = _re.search(r"(\d{4}年\d季度)", h4)
        col = _thead_column_indexes(m.group(0))
        if col is None:
            continue  # 表头不可解析 → 跳过该块（不猜测列位置）
        rows: list[dict] = []
        # 先在 tbody 内提取（<tbody><tr> 只出现一次；后续行是裸 <tr>，
        # 直接 <tr> 匹配会误收 thead 行），再逐行解析
        tbody_m = _re.search(r"<tbody>(.*?)</tbody>", m.group(0), _re.S)
        for tr in _re.findall(r"<tr>(.*?)</tr>", tbody_m.group(1) if tbody_m else "", _re.S):
            cells = [
                _html.unescape(c).strip()
                for c in _re.findall(r"<td[^>]*>(.*?)</td>", tr, _re.S)
            ]
            cells = [_re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if len(cells) < 6:
                continue
            rows.append(
                {
                    "code": cells[col["code"]],
                    "name": cells[col["name"]],
                    "pct": safe_float(cells[col["pct"]].rstrip("%").replace(",", "")),
                    "shares": safe_float(cells[col["shares"]].replace(",", "")),
                    "amount": safe_float(cells[col["amount"]].replace(",", "")),
                }
            )
        if rows:
            blocks.append(
                {
                    "report_date": date_m.group(1),
                    "quarter": q_m.group(1) if q_m else "",
                    "rows": rows,
                }
            )
    return blocks


def _holdings_missing(error: str) -> dict:
    return {
        "status": "missing",
        "report_date": None,
        "quarter": None,
        "rows": [],
        "error": error,
    }


def fetch_etf_holdings(symbol: str) -> dict:
    """原始取数：天天基金前十大持仓（data_bridge etf_holdings 维度，季度报告期）。

    裸 HTTP（akshare fund_portfolio_hold_em 已坏）。响应为
    ``var apidata={content:"...html...",arryear:[...]}``（JS 字面量非严格 JSON），
    content 内含多个季度块（h4+table），取「截止至」日期最大的一块。
    失败返回 status="missing" 信封（data_bridge _FAILURE_STATUSES 不缓存语义生效）。
    """
    import html as _html
    import random as _random
    import re as _re

    year = int(shanghai_today()[:4])
    for attempt in range(2):  # year → year-1 回退（年初 Q4 报告未披露）
        params = {
            "type": "jjcc",
            "code": symbol,
            "topline": "10",
            "year": str(year),
            "month": "",
            "rt": f"{_random.random():.13f}",
        }
        try:
            throttle_eastmoney()  # 全局东财限流 ≥0.5s（与 akshare 调用共享槽）
            with no_proxy_session() as sess:
                sess.headers.update(
                    {k.format(symbol=symbol): v.format(symbol=symbol)
                     for k, v in _EM_F10_HEADERS.items()}
                )
                r = sess.get(_EM_F10_URL, params=params, timeout=15)
                r.raise_for_status()
            text = _decode_em_page(r)
            m = _re.search(r'content:"(.*?)",arryear:', text, _re.S)
            if not m:
                return _holdings_missing("EM 响应无 apidata.content")
            content = _html.unescape(m.group(1))
            blocks = _parse_holdings_blocks(content)
            if blocks:
                latest = max(blocks, key=lambda b: b["report_date"])
                return {
                    "status": "ok",
                    "report_date": latest["report_date"],
                    "quarter": latest["quarter"],
                    "rows": latest["rows"],
                    "error": None,
                }
            if attempt == 0:
                year -= 1
                continue
            return _holdings_missing("该年份无持仓数据（可能尚未披露）")
        except Exception as exc:
            logger.warning("fetch_etf_holdings(%s) failed: %s", symbol, exc)
            # 异常也回退 year-1 重试一次（年初 Q4 未披露场景；限流/TLS
            # 瞬态故障同样受益，避免 attempt 0 直接 missing）
            if attempt == 0:
                year -= 1
                continue
            return _holdings_missing(str(exc)[:200])
    return _holdings_missing("取数失败")


def fetch_etf_category_sina() -> dict:
    """原始取数：sina ETF 分类表（data_bridge etf_category_sina 维度，市场级）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.fund_etf_category_sina()
        if df is None or df.empty:
            return {"status": "missing", "rows": [], "error": "empty"}
        return {"status": "ok", "rows": df.to_dict("records"), "error": None}
    except Exception as exc:
        logger.debug("fetch_etf_category_sina: failed, silent degrade: %s", exc)
        return {"status": "missing", "rows": [], "error": str(exc)}


def _apply_spot_row_to_profile(result: dict, row: Any, symbol: str) -> None:
    """将 spot 行写入 profile（折溢价口径 / 份额市值代理）。

    B02-1（v0.3.2）：原 `aum = 份额×最新价` 直接命名 AUM 会误导为基金资产
    净值。现：
    - 主字段 `share_price_mv_est`：显式单位/口径/时点（spot 行无交易日字段，
      as_of 只能记 None + 说明）；
    - 旧键 `aum` 保留为**同值兼容别名**一版（消费呈现不得再按「官方 AUM」用），
      下版本退出；
    - 官方资产净值口径（份额×单位净值，同日匹配）见 `query_etf_est_net_assets`，
      不得在本函数用交易价冒充。
    """
    result["premium_discount"] = _em_to_premium_discount(row.get("基金折价率"))
    result["premium_discount_caliber"] = _premium_discount_caliber()
    result.setdefault("aum_deprecated_note",
                      "旧键 aum=share_price_mv_est；口径为份额×交易价估算，非官方资产净值")
    shares = safe_float(row.get("最新份额"))
    price = safe_float(row.get("最新价"))
    if (shares is not None and price is not None
            and math.isfinite(shares) and math.isfinite(price)):
        value_yi = round(shares * price / ONE_PER_YI, 2)
        result["share_price_mv_est"] = {
            "value_yi": value_yi,
            "unit": "亿元",
            "caliber": "最新份额 × 最新价（交易价代理）",
            "as_of": None,
            "as_of_note": "spot 行无交易日字段；为查询时点最新披露值（份额披露存在 T+1 延迟）",
            "is_official_net_asset": False,
            "source": "fund_etf_spot_em 最新份额×最新价",
        }
        result["aum"] = value_yi  # 兼容别名（一版后退出）；消费呈现须标代理语义
    else:
        # 份额/价格缺失或非有限值（NaN/Inf）→ 无代理值（不给估计、不崩）
        result["share_price_mv_est"] = None
        result["aum"] = None


def _spot_row_to_quote(symbol: str, row: Any) -> dict[str, Any]:
    """将 spot 行转为 quote 结构。

    R07/R08：``source`` 为结构化来源标记（消费方不得对 ``_error`` 做文本匹配）；
    ``premium_discount`` 为 None 时其原因是「spot 行本身缺该字段」，
    与「回退腾讯导致不可得」是两回事。
    """
    return {
        "symbol": symbol,
        "price": safe_float(row.get("最新价")),
        "change_pct": safe_float(row.get("涨跌幅")),
        "volume": safe_float(row.get("成交量")),
        "amount": safe_float(row.get("成交额")),
        "premium_discount": _em_to_premium_discount(row.get("基金折价率")),
        "premium_discount_caliber": _premium_discount_caliber(),
        "source": "fund_etf_spot_em",
        "bid_ask_spread": {
            "status": "not_available",
            "note": "本次未取得/未接入可验证盘口数据（项目未接线，非「无该数据源」）；"
                    "日成交额不得推导价差/冲击成本",
        },
        "status": "available",
        "_error": None,
    }


# ---------------------------------------------------------------------------
# 子查询
# ---------------------------------------------------------------------------

def _set_index_pe_status(result: dict, symbol: str, idx_code: str, *,
                         pe_mapped: bool = True, source: str | None = None) -> None:
    """指数 PE 状态（R12：措辞只陈述「指数代码是否可得」，不推断产品身份）。

    ``pe_mapped=False + 有 code``（代码来自身份登记而非 CSINDEX_MAP）时，PE 已按
    该代码尝试（csindex 文件可得性由取数结果说话），状态仍为 mapped 但注明来源；
    无 code 时才是「未接线」——不得写成「无映射」让读者以为产品没有跟踪指数。
    """
    if idx_code:
        result["index_pe_status"] = "mapped"
        if not pe_mapped or (source and source != "CSINDEX_MAP"):
            result["index_pe_note"] = (
                f"指数代码 {idx_code} 来源={source or '未知'}（CSINDEX_MAP 未登记）："
                "PE 按该代码取数，可得性以取数结果为准（不按名称猜指数代码）")
        return
    if symbol in ETF_HEDGE_MAP:
        result["index_pe_status"] = "not_mapped"
        result["index_pe_note"] = (
            "未登记 csindex 指数代码（CSINDEX_MAP 与已核身份 tracked_index 均无）："
            "指数 PE 未接线——这是数据接线状态，不等于该产品没有跟踪指数"
        )
        return
    result["index_pe_status"] = "unknown_etf"
    result["index_pe_note"] = "不在已知映射表中，请手动核实跟踪指数"


def _fetch_csindex_pe(result: dict, idx_code: str) -> None:
    """指数 PE（csindex，单窗仅约 20 条历史）。

    v0.2.3：原始取数迁至 fetch_etf_index_pe，经 data_bridge L2 缓存（1d）。
    v0.2.4：index_pe_history 表累积 ≥20 条时附加历史分位（best-effort）。
    R13：分位/中位数/样本数由**一次窗口读取**产出（旧实现分位与统计各读一次，
    两次读取之间序列可变化 → 同一报告里 pct 与 median/n 来自不同数据集）。
    """
    env = _bridge_get("get_etf_index_pe", idx_code)
    if env is None:
        # R05 同类：记桥调用状态——桥/getter 不可用 = 未发起，data_status 不得
        # 把它写成「已尝试 csindex 但未取到」
        state = _leg_call_state(env, getter="get_etf_index_pe")
        result["index_pe_fetch_status"] = ("attempted" if state["attempted"]
                                           else "not_initiated")
        result["index_pe_fetch_skip_reason"] = state["skip_reason"]
        result["_errors"].append("csindex_pe: empty response")
        return
    if env.get("status") != "ok":
        result["_errors"].append(env.get("error") or "csindex_pe: empty response")
        return
    result["index_pe"] = env.get("index_pe")
    # 口径须**随值一起**透传（R2/T9-3）：只传 index_pe 会让徽章拿不到口径，
    # 只能回退到「未标注」——静默替换就仍然不可见
    result["index_pe_caliber"] = env.get("index_pe_caliber")
    result["index_pe_circulating"] = env.get("index_pe_circulating")
    result["index_pe_note"] = env.get("index_pe_note")
    # 当前 PE 值所属日期 = 信封最新行日期（query-before-persist 仅单次 cmd_report
    # 内成立；collect-weekly/早间 report 已持久化今日行时，分位须剔除今日自身，
    # 同 journal market_microstructure.hist_ex_today 型防双计）
    rows = env.get("rows") or []
    # 最新行日期取 max 而非 rows[-1]（review #4）：修复前的 L2 缓存信封为降序
    # rows，rows[-1] 是最早日期 → 分位防双计剔除错行；max 对任意行序稳健
    current_date = str(max((r.get("日期") for r in rows), key=lambda d: str(d))) if rows else None
    # 分位必须用**与当前值同口径**的历史列（R0~R2 review）：表内 pe=市盈率1（总
    # 股本）、pe_circulating=市盈率2（计算用股本）。否则「PE(2) 计算用股本口径」的
    # 徽章旁边会是一个拿总股本历史排出来的分位。
    _pct_col = ("pe_circulating"
                if result.get("index_pe_caliber") == _PE_CALIBER_CIRCULATING else "pe")
    # R13：一次读取 → pct/median/n 同窗口同口径（report-conventions §9.2：
    # 分位不得单独使用，必须伴随中位数与样本数）
    stats = _index_pe_window_stats(idx_code, env.get("index_pe"),
                                   current_date, column=_pct_col)
    if stats:
        result["index_pe_pct"] = stats["pct"]
        result["index_pe_history_median"] = stats["median"]
        result["index_pe_history_n"] = stats["n"]
        result["index_pe_history_column"] = stats["column"]


def _index_pe_window_stats(idx_code: str, current_pe: Any,
                           current_date: str | None = None, *,
                           column: str = "pe") -> dict | None:
    """一次读取同一窗口 → ``{pct, median, n, column}``（分位/中位数/样本数同源）。

    窗口/口径/防双计合同：``get_index_pe_history`` 取全窗 → 剔今日
    （``hist_ex_today``）→ 同 column 算分位与中位数。分位不可算（样本不足/
    非有限当前值/DB 不可用）返回 None，不阻断报告。
    """
    if current_pe is None:
        return None
    current = safe_float(current_pe)
    if current is None:  # NaN/±inf/非数字 → 无分位（防 nan<=v 恒 False 的假 0%）
        return None
    try:
        import statistics

        from index_pe_snapshot import get_index_pe_history, index_pe_percentile
        rows = get_index_pe_history(idx_code)
        if current_date is not None:
            rows = hist_ex_today(rows, current_date)
        pct = index_pe_percentile(rows, current, column=column)
        if pct is None:
            return None
        seq = [v for v in (safe_float(r.get(column)) for r in rows) if v is not None]
        if not seq:
            return None
        return {"pct": pct, "median": round(statistics.median(seq), 2),
                "n": len(seq), "column": column}
    except Exception as exc:  # DB 不可用等：统计缺失不阻断
        logger.debug("index_pe_window_stats(%s) failed: %s", idx_code, exc)
        return None


def _index_pe_history_stats(idx_code: str, current_pe: Any,
                            current_date: str | None = None, *,
                            column: str = "pe") -> dict | None:
    """历史分位 + 同窗口中位数/有效样本数（兼容接口；内部走单次读取核）。"""
    return _index_pe_window_stats(idx_code, current_pe, current_date, column=column)


def _index_pe_percentile_from_db(idx_code: str, current_pe: Any,
                                 current_date: str | None = None, *,
                                 column: str = "pe") -> float | None:
    """index_pe_history 累积 ≥20 条时计算当前 PE 历史分位；否则 None（数据不足）。

    current_date 为当前 PE 值所属日期（csindex 信封最新行日期）：剔除
    index_pe_history 中同日的已持久化行，防今日自投——collect-weekly/早间
    report 已把今日行入库时，cmd_report 的 query-before-persist 保证失效，
    今日值进入分位序列会把分位推向 100%/0% 假象；剔除后与 journal
    market_microstructure.hist_ex_today（commit cd5e7a4）同型防双计。
    历史中无该日行（当日尚未入库）时剔除为 no-op，行为与旧路径一致。

    R13：实现委托 :func:`_index_pe_window_stats`（单次读取）；本包装保留为
    兼容接口（外部复现脚本/测试按此名断言）。

    best-effort：任何异常返回 None，不阻断报告流程。
    """
    stats = _index_pe_window_stats(idx_code, current_pe, current_date, column=column)
    return stats["pct"] if stats else None


def _summarize_etf_data_quality(result: dict) -> dict[str, str]:
    """8 态简版 data_quality（与 journal 语义对齐）。"""
    dq: dict[str, str] = {}

    status = result.get("index_pe_status")
    if result.get("index_pe") is not None:
        dq["index_pe"] = "available"
    elif status == "not_mapped":
        dq["index_pe"] = "not_applicable"
    elif status == "mapped":
        dq["index_pe"] = "missing"
    else:
        dq["index_pe"] = "not_applicable"

    if result.get("premium_discount") is not None or result.get("aum") is not None:
        dq["spot"] = "available"
    elif any(e.startswith("etf_spot") for e in result.get("_errors", [])):
        dq["spot"] = "missing"
    else:
        dq["spot"] = "missing"

    hc = result.get("hedge_coverage") or {}
    cov = hc.get("coverage", "unknown")
    dq["hedge"] = "available" if cov != "unknown" else "unknown"

    return dq


# ---------------------------------------------------------------------------
# 自动标记
# ---------------------------------------------------------------------------

# ETF 类型 → 自动标记阈值（默认值适用于 broad_market / sector / thematic）
_TYPE_THRESHOLDS: dict[str, dict[str, float]] = {
    "cross_border": {"premium_warn": 5.0, "discount_warn": -5.0, "aum_min": 1.0},
    "bond":          {"premium_warn": 2.0, "discount_warn": -2.0, "aum_min": 2.0},
    "commodity":     {"premium_warn": 3.0, "discount_warn": -3.0, "aum_min": 1.0},
}
_DEFAULT_THRESHOLDS: dict[str, float] = {"premium_warn": 2.0, "discount_warn": -2.0, "aum_min": 2.0}


def _auto_flags(result: dict, etf_category: str = "") -> None:
    """基于阈值自动生成 flags。etf_category 为空时使用默认阈值。

    B02-1：规模阈值读 `share_price_mv_est`（代理）；文案不得据代理断言清盘
    事实——「筛查位 + 代理口径 + 须核定期报告」。旧 payload 只有 `aum` 时
    回退读之（兼容一版）。
    """
    thresholds = _TYPE_THRESHOLDS.get(etf_category, _DEFAULT_THRESHOLDS)
    flags: list[str] = []

    est = result.get("share_price_mv_est") or {}
    aum = est.get("value_yi") if isinstance(est, dict) else None
    if aum is None:  # 兼容旧 payload / 手工构造的 result
        aum = result.get("aum")
    if aum is not None and aum < thresholds["aum_min"]:
        flags.append(
            f"⚠️ 规模代理 < {thresholds['aum_min']} 亿（{aum:.2f} 亿；份额×交易价估算，"
            "非官方资产净值）：清盘/流动性风险筛查位，须以定期报告核实"
        )

    pd_val = result.get("premium_discount")
    if pd_val is not None:
        if not math.isfinite(pd_val):
            flags.append("⚠️ 折溢价数据异常")
        elif pd_val > thresholds["premium_warn"]:
            flags.append(f"⚠️ 溢价 {pd_val:.1f}%，买入成本偏高")
        elif pd_val < thresholds["discount_warn"]:
            flags.append(f"⚠️ 折价 {abs(pd_val):.1f}%，可能存在流动性或结构问题")

    hc = result.get("hedge_coverage", {})
    cov = hc.get("coverage", "unknown")
    if cov == "none":
        # R2：静态表未收录 ≠ 品种不存在（不得无原文断言「无可用工具」）
        flags.append("⚠️ 静态表未收录该 ETF 的期货/期权对冲工具（未核；不表示不存在）")
    elif cov == "low":
        flags.append("⚠️ 对冲工具覆盖有限")

    result["flags"] = flags


# ---------------------------------------------------------------------------
# 对冲覆盖查询
# ---------------------------------------------------------------------------

# 对冲静态映射的核验状态（B03）：默认 static_map_only（待核线索）；
# 带期货/期权具体品种主张的条目逐条登记核验尝试与结果。
# 588000「科创50期货(2025上线)」：2026-10-08 尝试中金所官网（cffex.com.cn），
# 本机 HTTP 301→HTTPS 后连接失败（通道异常），未能原文核验 → unverified（待核）。
ETF_HEDGE_VERIFICATION: dict[str, dict] = {
    "588000": {
        "status": "unverified",
        "attempted_sources": ["cffex.com.cn（中金所官网）"],
        "checked_at": "2026-10-08",
        "note": "期货上线主张缺官方原文核验（通道失败）；覆盖度按待核线索使用，不得称已核事实",
    },
}


def _lookup_hedge(symbol: str) -> dict:
    """查找 ETF 对冲工具覆盖。未知 ETF 返回 unknown。

    B03（v0.3.2）：所有条目附 `verification`——静态映射（含期货/期权品种
    主张）为**待核线索**，不是已核事实；带具体品种主张的条目按
    ETF_HEDGE_VERIFICATION 登记核验状态（源失败如实降级）。
    """
    entry = ETF_HEDGE_MAP.get(symbol)
    if entry:
        out = dict(entry)
        ver = ETF_HEDGE_VERIFICATION.get(symbol, {"status": "static_map_only",
                                                  "note": "静态映射，未逐条原文核验"})
        out["verification"] = dict(ver)
        return out
    return {"index": "未知", "futures": None, "options": None, "coverage": "unknown",
            "verification": {"status": "static_map_only",
                             "note": "不在映射表；未核验"},
            "note": "未在已知对冲工具映射表中，请手动核实"}


# ---------------------------------------------------------------------------
# ETF 行情 + K 线（净值序列）
# ---------------------------------------------------------------------------

def build_holdings_clusters(rows: list[dict]) -> list[dict]:
    """按 HOLDINGS_CLUSTER_MAP 聚合持仓为子环节聚类合计（引擎计算，AI 不心算）。

    公开 API（A04，v0.3.2）：领域聚类归 ETF 模块所有——data_bridge 等访问层
    不再复制聚类实现，经本公开名调用；``_build_holdings_clusters`` 为兼容
    别名（历史私有名，同实现）。

    口径：pct 为 None 的行不计（与 topN 集中度口径一致）；未映射股票归入「未归类」；
    sum_pct 先累加后 round(2)；排序：未归类恒排最后，其余按 sum_pct 降序；
    members 按 pct 降序。rows 为空 → 返回 []。
    """
    groups: dict[str, dict] = {}
    for r in rows:
        pct = r.get("pct")
        if pct is None:
            continue
        label = HOLDINGS_CLUSTER_MAP.get(str(r.get("code")), "未归类")
        g = groups.setdefault(label, {"cluster": label, "sum_pct": 0.0, "members": []})
        g["sum_pct"] += pct
        g["members"].append({"code": r["code"], "name": r.get("name"), "pct": pct})
    out = [
        {
            **g,
            "sum_pct": round(g["sum_pct"], 2),
            "members": sorted(g["members"], key=lambda m: -(m["pct"] or 0)),
        }
        for g in groups.values()
    ]
    out.sort(key=lambda c: (c["cluster"] == "未归类", -c["sum_pct"]))
    return out


# 兼容别名（历史私有名，同实现——A04 公开名迁移）
_build_holdings_clusters = build_holdings_clusters


def query_etf_holdings(symbol: str) -> dict[str, Any]:
    """前十大持仓 + 集中度统计 + 子环节聚类合计（v0.2.5 R12；引擎计算，AI 不心算）。

    Returns
    -------
    dict
        {symbol, report_date, quarter, status: ok/missing,
         rows: [{code, name, pct, shares, amount}],
         top1_pct / top5_sum_pct / top10_sum_pct,
         clusters: [{cluster, sum_pct, members: [{code, name, pct}]}],
         note, source}。

    口径：pct=占净值比例(%)；shares=持股数(万股)；amount=持仓市值(万元)；
    top5/top10 合计 = 可用行占比之和（不足 N 行按可用行求和，pct None 不计）；
    前十大合计可能 <100%（非前十大持仓未列），note 显式声明；
    clusters = HOLDINGS_CLUSTER_MAP 聚合合计，未映射股票归入「未归类」。
    """
    env = _bridge_get("get_etf_holdings", symbol)
    ok = env is not None and env.get("status") == "ok" and env.get("rows")
    if not ok:
        return {
            "symbol": symbol,
            "report_date": None,
            "quarter": None,
            "status": "missing",
            "rows": [],
            "top1_pct": None,
            "top5_sum_pct": None,
            "top10_sum_pct": None,
            "clusters": [],
            "holding_scope": HOLDING_SCOPE_TOP10,
            "coverage": None,
            "pcf": _pcf_block(),
            "note": (env or {}).get("error") or "持仓数据不可用",
            "source": "天天基金(东财 FundArchivesDatas jjcc)",
        }
    rows = env["rows"]
    pcts = [r["pct"] for r in rows if r.get("pct") is not None]
    # 统一口径：topN 均按占比降序取（不假设页面行序），与 top1 的 max 语义一致
    ranked = sorted(pcts, reverse=True)
    top10 = round(sum(ranked[:10]), 2) if ranked else None
    return {
        "symbol": symbol,
        "report_date": env.get("report_date"),
        "quarter": env.get("quarter"),
        "status": "ok",
        "rows": rows,
        "top1_pct": round(ranked[0], 2) if ranked else None,
        "top5_sum_pct": round(sum(ranked[:5]), 2) if ranked else None,
        "top10_sum_pct": top10,
        "clusters": build_holdings_clusters(rows),
        # B01-2：披露范围显式（前十 ≠ 完整持仓）；覆盖分母不得缺权重按 0 或重归一化
        "holding_scope": HOLDING_SCOPE_TOP10,
        "coverage": {
            "disclosed_sum_pct": top10,
            "denominator": "基金资产净值（占净值比例，仅披露范围内的前十大）",
            "row_count": len(rows),
            "pct_missing_rows": sum(1 for r in rows if r.get("pct") is None),
            "uncovered_note": "披露范围外持仓未知；缺失权重不得按 0 处理、不得把已披露样本"
                              "重归一化为 100%",
        },
        "pcf": _pcf_block(),
        "note": (
            "前十大持仓合计可能 <100%（非前十大未列）；集中度仅覆盖前十大披露范围；"
            "子环节聚类由引擎按 HOLDINGS_CLUSTER_MAP 聚合，未映射股票归入「未归类」"
        ),
        "source": "天天基金(东财 FundArchivesDatas jjcc)",
    }


#: 基金持仓披露范围（措辞铁律：不得称「完整持仓/全部持仓」）
HOLDING_SCOPE_TOP10 = "基金披露前十大（季度报告期，非完整持仓）"


def _pcf_block() -> dict:
    """PCF 状态：未接入 → 显式 not_connected（不新增强制付费源、不伪造篮子）。"""
    return {
        "status": "not_connected",
        "note": "PCF（申赎篮子/现金替代）未接入：交易所/管理人为原披露渠道，"
                "Tushare etf_sh_cons/etf_sz_cons 需 8000 积分（本机未验）——"
                "不新增强制付费源；PCF ≠ 基金净值权重",
    }


def _q_tencent_etf_quote(symbol: str) -> dict[str, Any] | None:
    """腾讯行情回退（F1-1）：东财 spot 不可达（代理/限流）时提供
    价/涨跌幅/成交量/成交额；折溢价腾讯无字段，标注不可得。

    v0.2.7 起委托 skills/lib/quote_tencent 唯一实现（统一路由/解析/单位换算），
    本层只保留返回契约：price/change_pct/volume/amount（amount 已转元）。
    """
    try:
        # 惰性导入：测试 patch lib.proxy.no_proxy_session 时须在调用时重新解析
        from lib.proxy import no_proxy_session
        with no_proxy_session() as sess:
            q = fetch_tencent_quote(symbol, session=sess)
        if q is None:
            return None
        return {
            "price": q["price"],
            "change_pct": q["change_pct"],
            "volume": q["volume"],
            "amount": q["amount"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("tencent etf quote fallback failed: %s", exc)
        return None


def query_etf_quote(symbol: str, *, spot_row: Any = None) -> dict[str, Any]:
    """ETF 当前行情：价格、涨跌幅、折溢价（从 fund_etf_spot_em；失败回退腾讯）。"""
    result: dict[str, Any] = {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "volume": None,
        "amount": None,
        "premium_discount": None,
        "premium_discount_caliber": _premium_discount_caliber(),
        # R07/R08：结构化来源（spot / tencent / None=两源均未取到），
        # 消费方（build_data_status）据此判断是否发生腾讯回退，不匹配文案
        "source": None,
        "spot_row_found": None,
        "bid_ask_spread": {
            "status": "not_available",
            "note": "本次未取得/未接入可验证盘口数据（项目未接线，非「无该数据源」）；"
                    "日成交额不得推导价差/冲击成本",
        },
        "status": "missing",
        "_error": None,
    }
    try:
        if spot_row is not None:
            return _spot_row_to_quote(symbol, spot_row)
        row, err = _lookup_etf_spot_row(symbol)
        if err or row is None:
            # F1-1: spot 源失败 → 腾讯行情回退（折溢价不可得，不整列缺失）
            result["spot_row_found"] = False
            result["spot_error"] = err or "empty response"
            fallback = _q_tencent_etf_quote(symbol)
            if fallback:
                result.update(fallback)
                result["status"] = "available"
                result["source"] = "tencent"
                result["premium_discount"] = None
                result["premium_discount_caliber"] = {
                    **_premium_discount_caliber(),
                    "note": "spot 源不可用（回退腾讯行情）；折溢价不可得，"
                            "不得用隔夜 NAV 确认当日错价",
                }
                result["_error"] = (
                    f"spot 源不可用（{err or 'empty response'}），"
                    "已回退腾讯行情；折溢价不可得"
                )
                return result
            # 两源均未取到：保留各源实际结果（not found 与网络失败可区分），
            # 不再 strip 前缀后丢失「spot 未命中」这一事实
            result["_error"] = (
                f"spot 未取到（{result['spot_error']}）；"
                "腾讯行情回退未取得数据"
            )
            return result
        result["spot_row_found"] = True
        return _spot_row_to_quote(symbol, row)
    except Exception as exc:
        logger.warning("etf_quote(%s) failed: %s", symbol, exc)
        result["_error"] = str(exc)
    return result


def _fill_boll_band(result: dict, navs: list[float], symbol: str) -> None:
    """D8: BOLL 布林带（基于 NAV 序列，SMA(20) ± 2×std，用于波动率区间判断）。

    引擎统一计算（lib.technical.boll_latest，总体方差，Bollinger 定义）；
    数据不足（<20 行）或计算失败静默跳过（形态对齐 _fetch_index_ma）。
    """
    if len(navs) >= 20:
        try:
            boll = boll_latest(navs)
            if boll["mid"] is not None:
                result["boll_mid"] = round(boll["mid"], 4)
            if boll["upper"] is not None:
                result["boll_upper"] = round(boll["upper"], 4)
            if boll["lower"] is not None:
                result["boll_lower"] = round(boll["lower"], 4)
        except Exception as exc:
            logger.info("boll(%s) skipped: %s", symbol, exc)


def _nav_derived_metrics(latest_nav: float | None, *, ma20: float | None = None,
                         ma60: float | None = None, boll_upper: float | None = None,
                         boll_mid: float | None = None, boll_lower: float | None = None,
                         volatility_annualized: float | None = None) -> dict[str, float]:
    """D14/A1：NAV 相对均线/BOLL 的派生指标（纯计算；查询与测试共用同一实现）。

    口径与防护（A1，2026-10-10）：

    - ``boll_bandwidth_pct`` = **(上轨 − 下轨) / 中轨 × 100**（标准 BOLL 带宽）。
      旧实现 ``上轨/下轨 − 1`` 以下轨作分母（159920 现场 7.45% vs 正确 7.19%）。
    - 所有以某值为分母的键都要求该分母**存在且 > 0**；不满足 → 不输出该键
      （不除零、不用 0/None 顶替、不猜）。
    - ``boll_position_pct`` 还需 上轨 > 下轨（上下轨重合 = 分母为零）。
    - 年化波动 → 日均波动的换算系数走模块常量 ``TRADING_DAYS_PER_YEAR``。
    """
    derived: dict[str, float] = {}
    if latest_nav is None:
        return derived
    if ma20 is not None and ma20 > 0:
        derived["nav_vs_ma20_pct"] = round((latest_nav / ma20 - 1) * 100, 2)
    if ma60 is not None and ma60 > 0:
        derived["nav_vs_ma60_pct"] = round((latest_nav / ma60 - 1) * 100, 2)
    if boll_mid is not None and boll_mid > 0:
        derived["nav_vs_boll_mid_pct"] = round((latest_nav / boll_mid - 1) * 100, 2)
    if (boll_upper is not None and boll_lower is not None
            and boll_mid is not None and boll_mid > 0):
        # 带宽只用中轨作分母：upper==lower（零波动窗）时带宽=0 仍合法
        derived["boll_bandwidth_pct"] = round((boll_upper - boll_lower) / boll_mid * 100, 2)
        if boll_upper > boll_lower:
            derived["boll_position_pct"] = round(
                (latest_nav - boll_lower) / (boll_upper - boll_lower) * 100, 2
            )
    if boll_lower is not None and boll_lower > 0:
        derived["nav_to_boll_lower_pct"] = round((latest_nav / boll_lower - 1) * 100, 2)
    if boll_upper is not None and boll_upper > 0:
        derived["nav_to_boll_upper_pct"] = round((latest_nav / boll_upper - 1) * 100, 2)
    if volatility_annualized is not None:
        # R16（D11 同类）：年化系数走模块常量，不另写裸 252
        derived["daily_volatility_pct"] = round(
            volatility_annualized / math.sqrt(TRADING_DAYS_PER_YEAR), 2)
    return derived


def query_etf_kline(symbol: str, days: int = 60) -> dict[str, Any]:
    """ETF 净值序列 + 年化波动率计算。

    通过 fund_etf_fund_info_em 获取历史单位净值，计算日收益率
    的年化标准差。同时返回 MA20/MA60 基于净值。

    Args:
        days: Number of **trading bars** needed (not calendar days).
            Calendar lookback uses ``int(days * 365 / 250) + 15`` so MA60
            has enough history after weekends/holidays.
    """
    result: dict[str, Any] = {
        "symbol": symbol,
        "nav_rows": 0,
        "latest_nav": None,
        "latest_nav_date": None,  # 数据末端日期（L2 缓存命中时可能滞后，供识别陈旧）
        "volatility_annualized": None,
        "adj_applied": False,
        "adj_note": None,
        "rsi": None,
        "rsi_period": None,
        "rsi_note": _RSI_NOTE,
        "ma20": None,
        "ma60": None,
        "index_ma20": None,
        "index_ma60": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "derived": None,  # D14: 衍生指标（NAV 偏离度/BOLL 位置，避免 AI 手工计算）
        "nav_history": [],
        "status": "missing",
        "_error": None,
        # B-F01（真实调用留痕）：attempted_sources=本次**实际发起**的取数（按顺序）；
        # source_trace=逐源 {source, via, outcome, reason}（via 区分 L2 缓存读取
        # 与本次网络）；skipped_sources=**未发起**的分支及原因（含无 token 跳过）。
        # 消费方（tracking/data_status）直接沿用，不得由静态表猜源。
        "attempted_sources": [],
        "source_trace": [],
        "skipped_sources": [],
    }

    def _trace(source: str, env: Any, *, outcome: str, reason: str | None = None) -> None:
        result["source_trace"].append({
            "source": source, "via": _read_via(env), "outcome": outcome, "reason": reason})

    try:
        # v0.2.3：原始取数经 data_bridge L2 缓存（fetch_etf_nav 固定 _NAV_FETCH_NATURAL_DAYS 自然日窗口）
        nav_env = _bridge_get("get_etf_nav", symbol)
        nav_src = str((nav_env or {}).get("source") or "fund_etf_fund_info_em")
        nav_name = f"{nav_src}（基金净值）"
        # R05：None 不得猜「本次网络」——未发起（桥不可用）时不登记 attempted
        nav_state = _leg_call_state(nav_env, getter="get_etf_nav")
        if nav_state["attempted"]:
            result["attempted_sources"].append(nav_name)
        else:
            result["skipped_sources"].append(
                {"source": nav_name, "reason": nav_state["skip_reason"]})
        if nav_env is None or nav_env.get("status") != "ok":
            err = (nav_env or {}).get("error") or nav_state["skip_reason"] \
                or "etf_nav: empty response"
            _trace(nav_name, nav_env, outcome="missing", reason=err)
            result["skipped_sources"].append(
                {"source": "Tushare fund_adj（复权因子）", "reason": "基金净值不可用，未发起（提前返回）"})
            result["_error"] = err
            return result

        rows = nav_env.get("rows") or []
        if not rows:
            err = nav_env.get("error") or "etf_nav: empty response"
            _trace(nav_name, nav_env, outcome="missing", reason=err)
            result["skipped_sources"].append(
                {"source": "Tushare fund_adj（复权因子）", "reason": "基金净值不可用，未发起（提前返回）"})
            result["_error"] = err
            return result
        _trace(nav_name, nav_env, outcome="ok")

        # 按调用方窗口切片：锚定数据末端日期（缓存跨日/跨节假日时窗口长度稳定）
        calendar_days = int(days * 365 / 250) + 15
        if calendar_days > _NAV_FETCH_NATURAL_DAYS:
            # 超窗请求：显式告警 + 结果 note，不再静默截断（v0.2.3 修复）
            logger.warning(
                "query_etf_kline(%s, days=%d): 请求窗口 %d 自然日超过取数上限 %d"
                "（约 %d 个交易日），已按上限截断",
                symbol, days, calendar_days, _NAV_FETCH_NATURAL_DAYS,
                int(_NAV_FETCH_NATURAL_DAYS * 250 / 365),
            )
            result["note"] = (
                f"请求 {days} 个交易日，超过取数上限 {_NAV_FETCH_NATURAL_DAYS} 自然日"
                f"（约 {int(_NAV_FETCH_NATURAL_DAYS * 250 / 365)} 个交易日），已按上限截断"
            )
        start = shanghai_days_ago(calendar_days).replace("-", "")
        # 切片多取 2 自然日上下文：复权切换行若恰为窗口首行，prev_nav 需要
        # 前一行做连续性校验（v0.2.3 补丁 #2：无上下文时假跳变残留）；
        # _aligned_nav_returns 计算完成后按 start 裁剪回请求窗口。
        used_ctx = False
        ctx_start = shanghai_days_ago(calendar_days + 2).replace("-", "")
        sliced = [r for r in rows if str(r["date"]).replace("-", "")[:8] >= ctx_start]
        if len(sliced) < 5:
            sliced = rows  # 兜底：数据跨度不足窗口时退回全部（超窗时已附 note 告警）
        else:
            used_ctx = True
        rows = sliced

        import pandas as pd

        df = pd.DataFrame(rows).rename(
            columns={"date": "净值日期", "nav": "单位净值", "change_pct": "日增长率"}
        )
        source = nav_env.get("source", "fund_etf_fund_info_em")
        # B-F01：暴露实际净值来源（列格式错位时降级 fund_open_fund_info_em），
        # 供 tracking/data_status 记录真实尝试源，不由静态表猜测。
        result["source"] = source

        result["nav_rows"] = len(df)

        # 尝试 Tushare fund_adj 复权（消除分红/拆分断点；data_bridge 6h 缓存，
        # 除息日开盘前必过期重拉，防 stale 因子跨断点算收益率）
        adj_env = _bridge_get("get_etf_adj_factor", symbol)
        adj_map = adj_env.get("adj_map") if adj_env and adj_env.get("status") == "ok" else None
        adj_name = "Tushare fund_adj（复权因子）"
        # R05/R07：尝试/跳过由结构化留痕判定（旧实现靠 note 子串「未发起网络」，
        # 文案一变就误判；None 也不再猜「本次网络」）
        adj_state = _leg_call_state(adj_env, getter="get_etf_adj_factor")
        if adj_map:
            result["attempted_sources"].append(adj_name)
            _trace(adj_name, adj_env, outcome="ok")
            result["adj_applied"] = True
            result["adj_note"] = _ADJ_NOTE
        elif not adj_state["attempted"]:
            # 无 token 等 producer 结构化跳过：未发起取数，不登记 attempted（B-F01）
            result["skipped_sources"].append(
                {"source": adj_name, "reason": adj_state["skip_reason"]})
        else:
            result["attempted_sources"].append(adj_name)
            _trace(adj_name, adj_env, outcome="missing",
                   reason=(adj_env or {}).get("note") or "fund_adj 不可得（查询失败/空返回）")

        navs, returns, aligned_rows = _aligned_nav_returns(df, source=source, adj_map=adj_map)
        # 裁剪上下文行（date < start）：仅当上下文切片实际生效（非兜底）时执行，
        # 保持 navs/returns/rows 三者对齐。returns 与行的对应关系取决于数据形态：
        # 复权路径首行无收益 → returns[j] 对应 rows[j+1]；未复权路径每行带日增长率
        # → returns[j] 对应 rows[j]（错位量见下方 first_ret_row 推导）。
        if used_ctx:
            keep = [i for i, r in enumerate(aligned_rows)
                    if str(r["date"]).replace("-", "")[:8] >= start]
            if keep and len(keep) < len(navs):
                # 错位量 = 序列首部无收益的行数（len(navs) - len(returns)）。
                # 旧实现硬编码 rows[j+1]，在未复权路径（每行一个日增长率，
                # len(returns) == len(navs)）下访问 aligned_rows[len(navs)] 越界
                # （list index out of range，2026-08-12 窗口起点越过数据首行后
                # 由回归测试复现；首部多行无收益时还会静默错位）。
                first_ret_row = len(navs) - len(returns)
                ret_keep = [j for j in range(len(returns))
                            if str(aligned_rows[first_ret_row + j]["date"]).replace("-", "")[:8] >= start]
                navs = [navs[i] for i in keep]
                returns = [returns[j] for j in ret_keep]
                aligned_rows = [aligned_rows[i] for i in keep]
        if navs:
            result["latest_nav"] = navs[-1]

        if len(returns) < 5:
            result["status"] = "insufficient"
            result["_error"] = f"only {len(returns)} daily returns"
            result["skipped_sources"].append(
                {"source": "stock_zh_index_daily（指数日线）", "reason": "净值序列不足，未发起（提前返回）"})
            return result

        # 固定 60 日窗口，不同 ETF 间可比；不足 60 日则用全部可用数据
        # 引擎统一计算（lib.technical），避免手写公式与共享库分歧
        vol_ann = annualized_volatility_from_returns(returns, window=60)
        if vol_ann is None:
            result["status"] = "insufficient"
            result["_error"] = f"only {len(returns)} daily returns"
            result["skipped_sources"].append(
                {"source": "stock_zh_index_daily（指数日线）", "reason": "净值序列不足，未发起（提前返回）"})
            return result
        result["volatility_annualized"] = vol_ann
        result["volatility_window"] = min(len(returns), 60)

        ma20_series = sma(navs, 20)
        ma60_series = sma(navs, 60)
        if ma20_series and ma20_series[-1] is not None:
            result["ma20"] = round(ma20_series[-1], 4)
        if ma60_series and ma60_series[-1] is not None:
            result["ma60"] = round(ma60_series[-1], 4)

        # D4: 指数价格 MA（底层指数的趋势结构，与 NAV MA 互补）
        # R12：指数代码经统一解析（CSINDEX_MAP → 已核身份），与 tracking 腿同源
        _ident = _identity_for_index_resolution(symbol)
        _idx_res = resolve_tracked_index(symbol, identity=_ident)
        idx_code = _idx_res["code"] or ""
        _bench_ccy = _idx_res.get("currency") or (
            "CNY" if _idx_res["source"] == "CSINDEX_MAP" else None)
        _fund_ccy = ((_ident or {}).get("currency") or "CNY")
        if idx_code and _bench_ccy and _bench_ccy != _fund_ccy:
            # A6：跨境币种不一致 → 指数 MA（如恒指 HKD 点位）与 CNY 复权 NAV
            # 不可直接同图比较；不发起点无意义的取数，如实记录跳过原因。
            result["index_ma_skipped_reason"] = (
                f"指数（{idx_code}，{_bench_ccy}）与净值（{_fund_ccy}）币种不一致："
                "指数 MA 未取数（跨币种不可直接对照，须经汇率调整口径）")
        elif idx_code and len(navs) >= 60:
            try:
                _fetch_index_ma(result, idx_code)
            except Exception as exc:
                logger.info("index_ma(%s/%s) skipped: %s", symbol, idx_code, exc)

        # D8: BOLL 布林带（基于 NAV 序列）
        _fill_boll_band(result, navs, symbol)

        period = 24 if len(navs) >= 25 else (14 if len(navs) >= 15 else None)
        if period is not None:
            rsi_val = _latest_rsi(navs, period)
            result["rsi"] = rsi_val
            result["rsi_period"] = period

        # D14: 衍生指标 — 所有 NAV vs 均线/BOLL 的百分比偏离与位置
        # 由引擎统一计算，避免 AI 手工心算引入误差（纯函数见 _nav_derived_metrics，
        # A1：BOLL 带宽 = (上轨 − 下轨) / 中轨 × 100；缺值/零防护）
        derived = _nav_derived_metrics(
            result["latest_nav"],
            ma20=result.get("ma20"), ma60=result.get("ma60"),
            boll_upper=result.get("boll_upper"), boll_mid=result.get("boll_mid"),
            boll_lower=result.get("boll_lower"),
            volatility_annualized=result.get("volatility_annualized"),
        )
        if derived:
            result["derived"] = derived

        # 使用与指标计算相同的对齐数据构建 nav_history（含复权调整）
        result["nav_history"] = [
            {"date": r["date"], "nav": navs[i], "change_pct": r["change_pct"]}
            for i, r in enumerate(aligned_rows)
        ]
        # 暴露数据末端日期：L2 缓存命中时序列可能滞后 1-2 交易日（净值晚间公布、
        # 缓存 TTL 内不重拉），供消费方识别陈旧（v0.2.3 补丁 #4）
        if aligned_rows:
            result["latest_nav_date"] = aligned_rows[-1]["date"]
        result["status"] = "available"

    except Exception as exc:
        logger.warning("etf_kline(%s) failed: %s", symbol, exc)
        result["_error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _aligned_nav_returns(df: Any, *, source: str = "", adj_map: dict[str, float] | None = None) -> tuple[list[float], list[float], list[dict]]:
    """从净值表构建对齐的 navs / returns（同一行样本）。

    Parameters
    ----------
    source : str
        数据源标识（仅用于日志/调试，不改变字段名解析逻辑）。
    adj_map : dict[str, float] | None
        Tushare fund_adj 复权因子映射 {date_str: adj_factor}。
        不为 None 时，对历史 NAV 做前复权（forward-adjust），
        使分红/拆分前后的 NAV 连续可比。
    """
    latest_adj = 1.0
    switch_factors: dict[str, float] = {}
    if adj_map:
        sorted_dates = sorted(adj_map.keys())
        latest_adj = adj_map[sorted_dates[-1]] if sorted_dates else 1.0
        # 因子切换点：Tushare fund_adj 因子在除权日次日才更新，而净值源
        # （akshare）在除权日当天已是拆后口径（如 515050 2026-05-13 因子变
        # 3.0，但净值 05-12 已从 3.29 → 1.1095）→ 切换日**前一行**须按新
        # 因子重算，否则前复权在该行产生假跳变（×3 假低点），污染
        # 波动率/MA/RSI/BOLL。是否采用由循环内的连续性校验决定。
        for i in range(1, len(sorted_dates)):
            prev_d, cur_d = sorted_dates[i - 1], sorted_dates[i]
            if adj_map[cur_d] != adj_map[prev_d]:
                switch_factors[prev_d] = adj_map[cur_d]

    navs: list[float] = []
    returns: list[float] = []
    rows: list[dict] = []
    prev_nav: float | None = None
    for _, row_data in df.iterrows():
        nav = safe_float(row_data.get("单位净值"))
        if nav is None:
            continue
        date_str = str(row_data.get("净值日期", ""))
        chg_pct = safe_float(row_data.get("日增长率"))
        # 前复权：adjusted = raw * adj(d) / adj(latest)
        if adj_map:
            # Tushare fund_adj 日期格式为 "20260724"，akshare 为 "2026-07-24"
            # 统一为无分隔符格式做匹配
            date_key = date_str.replace("-", "")
            adj_d = adj_map.get(date_key)
            if adj_d is None:
                adj_d = adj_map.get(date_str)
            aligned = switch_factors.get(date_key)
            if (aligned is not None and adj_d and latest_adj > 0
                    and prev_nav is not None and prev_nav > 0):
                # 错位对齐 + 连续性校验：切换日前一行若已是新口径（净值源提前
                # 一天除权），按新因子重算应与前值连续（市场变动 <15%，A 股
                # 涨跌停保证），而按旧因子则出现 ≈除权幅度 的跳变 → 采用新因子。
                # 阈值不再硬编码 15%：改为感知除权幅度先验 expected_drop
                # （= 1 - 旧因子/新因子），小分红（除权 1-5%，cur 跳变不足 15%）
                # 同样能命中，避免假跳变残留；同时保留"新因子下必须连续"
                # （cand_dev < 15%）防误采。
                cand_nav = nav * aligned / latest_adj
                cur_nav = nav * adj_d / latest_adj
                cand_dev = abs(cand_nav / prev_nav - 1)
                cur_dev = abs(cur_nav / prev_nav - 1)
                expected_drop = 1.0 - adj_d / aligned
                if (cand_dev < 0.15
                        and cur_dev >= expected_drop * 0.5
                        and abs(cur_dev - expected_drop) <= max(0.05, expected_drop * 0.5)):
                    adj_d = aligned
            if adj_d and latest_adj > 0:
                nav = nav * adj_d / latest_adj
        # 复权状态下：原始 日增长率 基于未复权净值，与调整后的 nav 不匹配
        # → 从调整后的 NAV 重新计算收益率并回写 change_pct（batch-test P1-5：
        # 旧实现重算 ret 只进 returns，rows.change_pct 恒 None，字段语义与注释
        # 承诺不符；下游 compute_history_stats/detect_big_move_days 虽防御性
        # 自行重算，但统一回写使 nav 路径与 baostock 路径口径一致）
        if adj_map:
            if prev_nav is not None and prev_nav > 0:
                ret = (nav / prev_nav) - 1.0
                navs.append(nav)
                returns.append(ret)
                rows.append({"date": date_str, "change_pct": round(ret * 100, 2)})
                prev_nav = nav  # D15 fix: 更新 prev_nav 使下期收益率为单期而非累积
            else:
                # 复权首行，无法计算收益率；保留 NAV 用于 MA/RSI 连续性
                prev_nav = nav
                navs.append(nav)
                rows.append({"date": date_str, "change_pct": None})
        else:
            # 未复权：使用原始 日增长率
            chg = chg_pct
            if chg is not None:
                ret = chg / 100.0
                navs.append(nav)
                returns.append(ret)
                rows.append({"date": date_str, "change_pct": chg_pct})
                prev_nav = nav
            elif prev_nav is not None and prev_nav > 0:
                ret = (nav / prev_nav) - 1.0
                navs.append(nav)
                returns.append(ret)
                rows.append({"date": date_str, "change_pct": chg_pct})
                prev_nav = nav
            else:
                # 首行有效 NAV 但无日增长率 — 保留为锚点，不丢失数据
                prev_nav = nav
                navs.append(nav)
                rows.append({"date": date_str, "change_pct": chg_pct})
    return navs, returns, rows


def _latest_rsi(navs: list[float], period: int) -> float | None:
    """Latest Wilder RSI from NAV close series (aligned with lib.technical)."""
    if len(navs) < period + 1:
        return None
    series = rsi_series(navs, period)
    for val in reversed(series):
        if val is not None:
            return round(val, 2)
    return None


def rollup_etf_quality_status(etf: dict) -> str:
    """Roll up etf_data bundle to available / partial / missing (journal query_data)."""
    errors = list(etf.get("_errors") or [])
    if etf.get("_error") and not errors:
        errors = [str(etf["_error"])]
    hc = etf.get("hedge_coverage") or {}
    has_hedge_data = hc.get("coverage") not in (None, "unknown")
    has_data = any(
        etf.get(k) is not None
        for k in ("index_pe", "premium_discount", "aum", "tracking_error")
    ) or has_hedge_data
    if errors and has_data:
        return "partial"
    if errors or not has_data:
        return "missing"
    return "available"


def _fetch_fund_adj_factor(symbol: str) -> dict[str, float] | None:
    """从 Tushare ``fund_adj`` 拉取 ETF 复权因子。

    用于前复权 NAV 序列，消除分红/拆分造成的断点。

    Returns
    -------
    dict or None
        {date_str: adj_factor}，Tushare 不可用时返回 None。
    """
    try:
        from lib.env import get_config

        config = get_config()
        token = config.get("TUSHARE_TOKEN")
        if not token:
            logger.info("fund_adj(%s): no TUSHARE_TOKEN, skipping", symbol)
            return None

        # ETF 代码 → Tushare ts_code（共享 lib.codes，ETF 规则与股票不同：5→SH）
        ts_code = etf_symbol_to_ts_code(symbol)
        if not ts_code:
            return None

        # v0.2.3：统一走 TushareClient（原裸 ts.pro_api，缺限流/权限降级）
        from lib.tushare_client import TushareClient

        client = TushareClient(token=token, timeout=15)
        df = client.query("fund_adj", ts_code=ts_code, fields="trade_date,adj_factor")
        if df is None or df.empty:
            return None

        adj_map: dict[str, float] = {}
        for _, row in df.iterrows():
            d = str(row.get("trade_date", ""))
            f = safe_float(row.get("adj_factor"))
            if d and f is not None:
                adj_map[d] = f
        logger.info("fund_adj(%s): %d rows loaded, latest adj=%.4f", symbol, len(adj_map),
                     adj_map.get(sorted(adj_map.keys())[-1], 1.0) if adj_map else 1.0)
        return adj_map if adj_map else None
    except Exception as exc:
        logger.info("fund_adj(%s) unavailable: %s", symbol, exc)
        return None


def _fetch_index_ma(result: dict, idx_code: str) -> None:
    """指数日 K → index_ma20/index_ma60。

    v0.2.3：原始取数迁至 fetch_etf_index_daily（sh/sz 路由在内），
    经 data_bridge L2 缓存（1d）。降级策略：数据不足时静默跳过。
    """
    try:
        env = _bridge_get("get_etf_index_daily", idx_code)
        if env is None or env.get("status") != "ok":
            return
        closes = [safe_float(r.get("close")) for r in env.get("rows", [])]
        closes = [c for c in closes if c is not None]
        idx_ma20 = sma(closes, 20)
        idx_ma60 = sma(closes, 60)
        if idx_ma20 and idx_ma20[-1] is not None:
            result["index_ma20"] = round(idx_ma20[-1], 2)
        if idx_ma60 and idx_ma60[-1] is not None:
            result["index_ma60"] = round(idx_ma60[-1], 2)
    except Exception:
        logger.debug("_fetch_index_ma(%s): index daily fetch failed, silent degrade", idx_code)


def _em_to_premium_discount(em_raw: object) -> float | None:
    """EM 基金折价率（+ = 折价）→ premium_discount（+ = 溢价）。"""
    em = safe_float(em_raw)
    return None if em is None else -em


def _premium_discount_caliber() -> dict:
    """折溢价口径/时点（B03）：净值时点不可得时不得确认实时错价。"""
    return {
        "formula": "收盘/最新价相对对应每份 NAV 的偏离（EM 基金折价率，已规范化为 +溢价/-折价）",
        "nav_time_point": "未知（spot 字段无对应净值日期）",
        "quote_time_point": "查询时点（spot 最新报价）",
        "quote_snapshot": "单次快照，非连续盘口",
        "note": "NAV 可能为隔夜/盘中估值口径未知；不得用隔夜 NAV 确认当日错价，"
                "也不得由日成交额推导盘口价差",
    }


# ---------------------------------------------------------------------------
# ETF 类型分类（G1）
# ---------------------------------------------------------------------------

def query_etf_category(symbol: str) -> dict[str, str]:
    """查询 ETF 类型标签。

    来源优先级：硬编码映射 > fund_etf_category_sina > 名称关键词推断。

    Returns
    -------
    dict
        {"category": "sector", "label": "行业ETF", "source": "builtin_map"}
    """
    cat = _ETF_CATEGORY_MAP.get(symbol)
    if cat:
        return {"category": cat, "label": _CATEGORY_LABELS.get(cat, cat), "source": "builtin_map"}

    # 动态查询 akshare 分类（兜底；v0.2.3 经 data_bridge L2 缓存，7d TTL）
    env = _bridge_get("get_etf_category_sina")
    if env is not None and env.get("status") == "ok":
        for r in env.get("rows", []):
            if str(r.get("代码")) == symbol:
                name = str(r.get("名称", ""))
                cat = _infer_category_from_name(name)
                return {"category": cat, "label": _CATEGORY_LABELS.get(cat, cat), "source": "sina_dynamic"}

    # 名称关键词回退
    try:
        row2, _ = _lookup_etf_spot_row(symbol)  # type: ignore[arg-type]
        if row2 is not None:
            name = str(row2.get("名称", ""))
            cat = _infer_category_from_name(name)
            return {"category": cat, "label": _CATEGORY_LABELS.get(cat, cat), "source": "name_fallback"}
    except Exception:
        pass

    return {"category": "unknown", "label": "未知", "source": "none"}


def _infer_category_from_name(name: str) -> str:
    """从 ETF 名称关键词推断类型。

    B01-1：关键词命中仍是**推断**（调用方标 inferred_unverified）；兜底由旧
    `sector` 改 `unknown`——名称不含任何类别的 ETF 不得被猜成行业 ETF，
    否则「名称猜测触发行业分析必填项」（设计 §3.1 禁止）。资产类关键词
    （债/黄金/货币/纳指）保留：命中的是资产类型而非策略断言。
    """
    n = name.lower()
    if any(k in n for k in ["qdii", "纳指", "标普", "恒生", "日经", "德国"]):
        return "cross_border"
    if any(k in n for k in ["黄金", "豆粕", "原油", "商品期货", "期货"]):
        return "commodity"
    if any(k in n for k in ["货币", "日利", "保证金"]):
        return "money_market"
    if any(k in n for k in ["债", "国债", "信用债", "可转债"]):
        return "bond"
    if any(k in n for k in ["沪深300", "中证500", "中证1000", "中证2000",
                              "上证50", "科创50", "创业板", "深证"]):
        return "broad_market"
    return "unknown"


# ---------------------------------------------------------------------------
# 行业 PE 附加 + 估值指引（G4 + G9）
# ---------------------------------------------------------------------------

def _attach_industry_pe(result: dict, symbol: str) -> None:
    """对行业/主题 ETF，从 industry_weekly SQLite 表查行业 PE 注入 result。

    R09：逐条留痕 ``industry_pe_status``（available / skipped_index_pe_present /
    no_industry_mapping / source_null / query_error）。515050/516160 同时有
    csindex 指数 PE 与申万行业映射：指数 PE 可得时行业代理 PE **政策上不取**
    （避免双口径混用），旧实现静默 return，data_status 只能写「无行业映射或
    未请求」——与事实不符。
    """
    sw_info = ETF_TO_SW_INDUSTRY.get(symbol)
    if not sw_info:
        result["industry_pe_status"] = "no_industry_mapping"
        return
    # 仅当无 csindex PE 时才附加行业 PE（行业 ETF 通常 not_mapped 或 unknown_etf）
    if result.get("index_pe") is not None:
        result["industry_pe_status"] = "skipped_index_pe_present"
        result["industry_pe_note"] = (
            f"已有 csindex 指数 PE（{result.get('index_code') or '—'}，"
            "指数公司口径）；申万行业代理 PE 本次未取（避免两种口径混用）"
        )
        return
    try:
        import sqlite3
        from lib.store import close_connection, open_connection
        c = open_connection()
        try:
            row = c.execute(
                "SELECT pe, pb, date FROM industry_weekly "
                "WHERE index_code = ? ORDER BY date DESC LIMIT 1",
                (sw_info["sw_code"],),
            ).fetchone()
        except sqlite3.OperationalError:
            result["industry_pe_status"] = "query_error"
            return
        finally:
            close_connection(c)
        if row:
            pe_val = row["pe"]
            if pe_val is None:
                # pe 字段为 NULL（如行业整体亏损）——已查得该行但无值：missing 而非未执行
                result["industry_pe_status"] = "source_null"
                result["industry_pe_note"] = (
                    f"申万{sw_info['sw_name']}({sw_info['sw_code']})行业 PE 为空（源侧 NULL，"
                    "常为行业整体亏损）")
                return
            result["industry_pe"] = pe_val
            result["industry_pe_status"] = "available"
            result["industry_pe_note"] = (
                f"申万{sw_info['sw_name']}({sw_info['sw_code']})行业 PE={pe_val:.2f}，"
                f"数据日期 {row['date']}；"
                f"此为行业层面估值参考，非 ETF 精确 PE"
            )
        else:
            result["industry_pe_status"] = "source_null"
            result["industry_pe_note"] = (
                f"申万{sw_info['sw_name']}({sw_info['sw_code']})行业快照无数据行")
    except Exception:
        result["industry_pe_status"] = "query_error"  # 非关键附加数据，降级不阻塞主流程


def _attach_industry_allocation(result: dict, symbol: str) -> None:
    """ETF 行业配置比例（G6）。

    v0.2.3：原始取数迁至 fetch_etf_industry_alloc（季度报告期数据，
    data_bridge L2 缓存 1d，保证新季度报告 1d 内可见）。降级策略：
    接口不可用或数据为空时静默跳过。
    """
    env = _bridge_get("get_etf_industry_alloc", symbol)
    if env is None or env.get("status") != "ok" or not env.get("allocation"):
        # R05 同类：桥/getter 不可用 = 未发起（data_status 不得静态记已尝试源）
        state = _leg_call_state(env, getter="get_etf_industry_alloc")
        result["industry_allocation_fetch_status"] = (
            "attempted" if state["attempted"] else "not_initiated")
        result["industry_allocation_skip_reason"] = state["skip_reason"]
        return
    result["industry_allocation"] = env["allocation"]
    result["industry_allocation_date"] = env.get("latest_date")


def _attach_valuation_guide(result: dict) -> None:
    """附加该 ETF 对应行业的推荐估值指标指引。"""
    symbol = result.get("symbol", "")
    sw_info = ETF_TO_SW_INDUSTRY.get(symbol)
    if not sw_info:
        return
    guide = query_sector_valuation_guide(sw_info["sw_name"])
    if guide:
        result["valuation_guide"] = {
            "industry": sw_info["sw_name"],
            "sub_sector": sw_info.get("sub", ""),
            "primary": guide["primary"],
            "secondary": guide["secondary"],
            "pe_timing": guide["pe_timing"],
            "reason": guide["reason"],
        }


def query_sector_valuation_guide(sw_name: str) -> dict | None:
    """查询申万行业的推荐估值指标。

    Parameters
    ----------
    sw_name : str
        申万一级行业名称（如 "电子"、"银行"）。

    Returns
    -------
    dict or None
        {primary, secondary, pe_timing, reason}
    """
    return SECTOR_VALUATION_MAP.get(sw_name)


# ---------------------------------------------------------------------------
# ETF 份额历史序列（v0.2.2 P0）
# ---------------------------------------------------------------------------

def save_etf_share_snapshot(symbol: str) -> dict | None:
    """采集当日 ETF 份额快照并写入 etf_share_snapshots 表。

    数据源：akshare ``fund_etf_spot_em`` 的 ``最新份额`` 列。

    注意：date 为采集日墙钟日期；akshare fund_etf_spot_em spot 行
    无 trade_date 字段，最新份额披露存在 T+1 延迟，shares 实际对应
    交易日未知。该语义由读取侧 etf_share_flow 的 lag_note 标注，
    写入侧不推算实际交易日（已否决 trade_cal 推算方案）。

    Parameters
    ----------
    symbol : str
        6 位 ETF 代码（如 588000）。

    Returns
    -------
    dict or None
        写入的快照字典；非交易日（价格/份额为 NaN）返回 None。
    """
    import sqlite3

    from lib.store import close_connection, init_db, open_connection

    # v0.2.3：经 _lookup_etf_spot_row（L1 30s → L2 60s）取 spot 行，修直原直连绕过缓存问题
    row, err = _lookup_etf_spot_row(symbol)
    if err or row is None:
        logger.warning("etf share snapshot: %s（%s）", symbol, err or "no spot row")
        return None

    shares = safe_float(row.get("最新份额"))
    price = safe_float(row.get("最新价"))

    # 非交易日检测
    if shares is None or price is None:
        logger.info("etf share snapshot: %s 疑似非交易日（份额/价格缺失），跳过", symbol)
        return None

    aum = round(shares * price / ONE_PER_YI, 2)
    today = shanghai_today()  # 采集日墙钟；spot 行无 trade_date，份额披露 T+1（实际对应交易日未知）

    snap = {
        "date": today,
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "aum": aum,
    }

    init_db()
    c = open_connection()
    try:
        c.execute(
            "INSERT OR REPLACE INTO etf_share_snapshots (date, symbol, shares, price, aum) "
            "VALUES (?, ?, ?, ?, ?)",
            (today, symbol, shares, price, aum),
        )
        c.commit()
        logger.info("etf share snapshot %s/%s saved: %.0f 份, AUM %.2f 亿", today, symbol, shares, aum)
        return snap
    except Exception as exc:
        logger.warning("etf share snapshot save failed: %s", exc)
        c.rollback()
        return None
    finally:
        close_connection(c)


def etf_share_flow(symbol: str, days: int = 60) -> dict:
    """读取 ETF 份额历史序列，计算份额变动和估算资金流。

    Parameters
    ----------
    symbol : str
        6 位 ETF 代码。
    days : int
        回溯行数（默认 60 行，对应约 60 个交易日；注意非自然日语义，依赖每日 snapshot 采集频率）。

    Returns
    -------
    dict
        {symbol, date, shares_current, aum_current,
         share_change_5d/20d/60d (份),
         flow_est_5d/20d/60d (亿元),
         history_count, lag_note, window_status}

    date 为采集日墙钟日期；shares/price/aum 为采集时 akshare
    fund_etf_spot_em 最新披露值（份额披露 T+1 延迟，实际对应交易日
    未知），该语义由 lag_note 在返回结构中如实标注。采集日不得用于
    拆分复权；跨登记/除权日或存在疑似机械跳变的窗口返回 None，
    window_status 逐窗披露不可确认原因。
    """
    import sqlite3

    from lib.store import close_connection, open_connection

    c = open_connection()
    try:
        rows = c.execute(
            "SELECT date, shares, price, aum FROM etf_share_snapshots "
            "WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol, days + 1),  # +1 确保 _change(window) 所需的 window+1 行
        ).fetchall()
        rows = list(reversed(rows))  # 恢复为 ASC 顺序
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {"symbol": symbol, "history_count": 0, "note": "无历史数据（etf_share_snapshots 表不存在）"}
        raise
    finally:
        close_connection(c)

    if not rows:
        return {"symbol": symbol, "history_count": 0, "note": "无历史数据"}

    history = [dict(r) for r in rows]
    latest = history[-1]

    def _split_events_between(start: str, end: str) -> list[dict]:
        start, end = start.replace("-", ""), end.replace("-", "")
        # 这里只用公告日期识别风险窗口，不据采集日期认定份额已生效。
        return [e for e in ETF_SHARE_SPLITS.get(symbol, [])
                if any(start <= str(e.get(key) or "").replace("-", "") <= end
                       for key in ("effective_from", "ex_date"))]

    split_events = _split_events_between(str(history[0]["date"]), str(latest["date"]))

    def _change(window: int) -> dict:
        status = {"confirmable": False, "note": "历史样本不足"}
        if len(history) < window + 1:
            return {"share_change": None, "flow_est": None, "status": status}
        prev = history[-(window + 1)]
        status.update(start=prev["date"], end=latest["date"])
        if _split_events_between(str(prev["date"]), str(latest["date"])):
            status["note"] = ("采集窗口跨份额拆分登记/除权日，份额披露日期未知；"
                              "无法确认两端份额口径，本窗口变化及流量不可确认")
            return {"share_change": None, "flow_est": None, "status": status}
        window_rows = history[-(window + 1):]
        # 披露可能迟于公告日：不把窗口内大跳变猜成按期完成的拆分或申赎。
        if any(a["shares"] > 0 and abs(b["shares"] - a["shares"]) / a["shares"]
               >= SPLIT_SUSPECT_RATIO for a, b in zip(window_rows, window_rows[1:])):
            status["note"] = ("份额存在疑似机械跳变，份额披露日期未知；"
                              "无法确认是否份额调整，本窗口变化及流量不可确认")
            return {"share_change": None, "flow_est": None, "status": status}
        d_shares = latest["shares"] - prev["shares"]
        avg_price = (latest["price"] + prev["price"]) / 2
        flow_est = round(d_shares * avg_price / ONE_PER_YI, 2) if avg_price > 0 else None
        return {
            "share_change": d_shares,
            "flow_est": flow_est,
            "status": {**status, "confirmable": True,
                       "note": "按采集快照估算；份额实际对应交易日仍未知"},
        }

    changes = {window: _change(window) for window in (5, 20, 60)}
    out = {
        "symbol": symbol,
        "date": latest["date"],
        "shares_current": latest["shares"],
        "aum_current": latest["aum"],                       # 兼容别名（=份额×交易价估算）
        "share_price_mv_est_current": latest["aum"],        # B02-1 正名：代理口径
        "share_change_5d": changes[5]["share_change"],
        "flow_est_5d": changes[5]["flow_est"],
        "share_change_20d": changes[20]["share_change"],
        "flow_est_20d": changes[20]["flow_est"],
        "share_change_60d": changes[60]["share_change"],
        "flow_est_60d": changes[60]["flow_est"],
        "window_status": {f"{w}d": change["status"] for w, change in changes.items()},
        "history_count": len(history),
        "lag_note": (
            "date 为采集日墙钟日期；shares/price/aum 为采集时 akshare 最新披露值，"
            "份额披露存在 T+1 延迟，实际对应交易日未知"
        ),
        "caliber_note": (
            "aum_current/share_price_mv_est_current=份额×交易价估算市值（非官方资产净值）；"
            "flow_est=份额变化×均价估算（亿元），为估算方向标签而非官方申赎数据；"
            "价格涨跌 ≠ 资金净流入"
        ),
    }
    if split_events:
        out["split_events"] = split_events
    blocked = [f"{w}日：{c['status']['note']}" for w, c in changes.items()
               if not c["status"]["confirmable"] and c["status"].get("start")]
    if blocked:
        out["note"] = "；".join(blocked)
    return out


# ---------------------------------------------------------------------------
# ETF 份额资金流历史序列（Tushare fund_share + fund_daily，v0.2.3）
# ---------------------------------------------------------------------------

def query_etf_share_history(symbol: str, days: int = 20) -> dict:
    """查询 ETF 份额历史序列 + 每日资金流估算 + OHLCV 趋势数据。

    数据源：Tushare ``fund_share``（份额，万份）+ ``fund_daily``（OHLCV）。

    Parameters
    ----------
    symbol : str
        6 位 ETF 代码（如 515050）。
    days : int
        回溯行数（默认 20 日）。

    Returns
    -------
    dict
        {symbol, date_range, rows: [{date, open, high, low, close, pre_close,
         pct_chg, vol, amount, turnover_rate, shares, share_change, flow_est,
         direction}], summary: {...}}

    ``summary``（A3 起）含 **共同起止** 的窗口披露：``flow_window``
    ``{start, end, segments, trading_days, missing_share_days, note}`` 与
    同窗的 ``share_total_change``/``share_change_pct``——flow 合计与份额变化
    起止点一致（含首日变化段）；缺值跨间隔只计一段；尾部 T+1 无份额日不在
    窗内（``date_range`` 对比即实际覆盖）。``rows`` 仍为 r0（无 prev）之后的
    明细行，逐行 ``share_change``/``flow_est`` 语义不变。
    """
    # v0.2.3：原始取数迁至 fetch_etf_share_history（固定 _SHARE_FETCH_NATURAL_DAYS 自然日窗口），
    # 经 data_bridge L2 缓存（1d）
    env = _bridge_get("get_etf_share_history", symbol)
    if env is None or env.get("status") != "ok":
        # R06 同类（D11 扫描）：无 token/桥不可用时源**未发起**，不得让消费者
        # 静态登记为 attempted——把结构化留痕透传给 data_status
        state = _leg_call_state(env, getter="get_etf_share_history")
        out = {"symbol": symbol, "available": False,
               "note": (env or {}).get("note") or _NOTE_SHARE_HIST_UNAVAILABLE}
        if state["attempted"]:
            out["attempted_sources"] = ["Tushare fund_share", "Tushare fund_daily"]
            out["skipped_sources"] = []
        else:
            out["attempted_sources"] = []
            out["skipped_sources"] = [
                {"source": src, "reason": state["skip_reason"]}
                for src in ("Tushare fund_share", "Tushare fund_daily")]
        return out

    import pandas as pd

    shares_df = pd.DataFrame(env.get("fund_share") or [])
    daily_df = pd.DataFrame(env.get("fund_daily") or [])
    if shares_df.empty:
        return {"symbol": symbol, "available": False,
                "note": _NOTE_SHARE_NO_DATA}
    if daily_df.empty:
        return {"symbol": symbol, "available": False,
                "note": _NOTE_DAILY_NO_DATA}

    try:
        # 合并：按 trade_date 对齐。left join → 确保 fund_daily 最新日期的 OHLCV
        # 不被 fund_share 的 T+1 延迟丢掉（份额字段为 null 但价格/成交仍可用）
        shares_df = shares_df.sort_values("trade_date")
        daily_df = daily_df.sort_values("trade_date")
        daily_cols = ["trade_date", "open", "high", "low", "close",
                      "pre_close", "pct_chg", "vol", "amount"]
        merged = daily_df[[c for c in daily_cols if c in daily_df.columns]].merge(
            shares_df, on="trade_date", how="left"
        )
    except Exception as exc:
        return {"symbol": symbol, "available": False,
                "note": f"份额-价格合并失败: {exc}"}
    if merged.empty:
        return {"symbol": symbol, "available": False, "note": _NOTE_NO_DATE_INTERSECT}

    # 取最近 days 行（+1 用于计算第一行的变化）
    # 超窗检测：取数窗口（_SHARE_FETCH_NATURAL_DAYS 自然日）行数不足 days+1 时
    # 显式告警 + 结果 note，不再静默少返回（v0.2.3 修复）
    clipped = len(merged) < days + 1
    if clipped:
        logger.warning(
            "query_etf_share_history(%s, days=%d): 取数窗口仅覆盖 %d 行，少于请求的 %d，"
            "已按可用数据返回",
            symbol, days, len(merged), days,
        )
    merged = merged.tail(days + 1)

    rows = []
    prev_share = None          # 拆分调整后的份额（变化段计算基准）
    prev_share_raw = None      # 原始披露份额（base_row 留痕）
    prev_price = None          # 拆分调整后的收盘价（flow 金额计算用）
    prev_price_raw = None      # 原始披露收盘价（base_row 留痕）
    prev_date = None
    prev_factor = 1.0
    prev_share_pos = None      # prev_share 所在 rows 下标（窗口起止计算用）
    # A3：flow 合计与份额变化的**共同起止**（首个可算变化段的基期 ~ 末个可算段的期末）。
    # 缺值跨间隔只计一段（segments < trading_days-1 时按实际披露）。
    flow_base: dict[str, Any] | None = None
    flow_end: dict[str, Any] | None = None
    flow_segments = 0
    unconfirmable_segments = 0

    # R4：份额拆分调整（已登记公告；前复权=最新基准）——拆分是机械份额变化，
    # 不是申赎流；未登记的大跳变按 SPLIT_SUSPECT_RATIO 记「流量不可确认」。
    merged_dates = [str(x) for x in merged.get("trade_date", [])]
    split_factors, split_events = _split_factor_map(symbol, merged_dates)
    price_factors, price_events = _split_factor_map(symbol, merged_dates, price=True)
    factor_by_date = dict(zip(merged_dates, split_factors))
    price_factor_by_date = dict(zip(merged_dates, price_factors))
    split_events = price_events or split_events
    split_applied = any(f != 1.0 for f in split_factors + price_factors)

    import math as _math

    for _, row in merged.iterrows():
        date_str = str(row.get("trade_date", ""))
        shares_raw = row.get("fd_share")
        shares_val = safe_float(shares_raw) if (shares_raw is not None
            and not (isinstance(shares_raw, (int, float)) and _math.isnan(float(shares_raw)))) else None
        close_val = safe_float(row.get("close"))
        if close_val is None:
            continue
        open_val = safe_float(row.get("open"))
        high_val = safe_float(row.get("high"))
        low_val = safe_float(row.get("low"))
        pre_close_val = safe_float(row.get("pre_close"))
        pct_chg_val = safe_float(row.get("pct_chg"))
        vol_val = safe_float(row.get("vol"))       # 手
        amount_val = safe_float(row.get("amount"))  # 千元
        row_pos = len(rows)                        # 当前行在 rows 中的下标（append 前）

        # 估算换手率(%)。Tushare vol=手(1手=100份), shares=万份
        # turnover = (vol×100) / (shares×10000) × 100 = vol / shares (直接得到 %)
        turnover = None
        if vol_val is not None and shares_val is not None and shares_val > 0:
            turnover = round(vol_val / shares_val, 2)

        factor = factor_by_date.get(date_str, 1.0)
        shares_adj = round(shares_val * factor, 2) if shares_val is not None else None
        # 份额按 effective_from 调整，价格按 ex_date 调整到同一份额单位。
        # 登记日到除权日之间仍是除权前价格，不能复用份额系数。
        price_factor = price_factor_by_date.get(date_str, 1.0)
        close_adj = (close_val / price_factor) if price_factor else close_val

        share_change = None
        flow_est = None
        direction = None
        suspected = False

        if prev_share is not None and shares_adj is not None:
            share_change = round(shares_adj - prev_share, 2)  # 万份（拆分调整后）
            avg_price = (close_adj + prev_price) / 2          # 两端均为拆分调整价
            # 段内发生**已登记拆分**（factor 变化）→ 机械跳变已被调整消除；
            # 未登记的大跳变（|Δ|/前值 ≥ 阈值）→ 本段流量不可确认（不猜比例）
            split_in_segment = (factor != prev_factor and factor > 0 and prev_factor > 0)
            if (not split_in_segment and prev_share > 0
                    and abs(share_change) / prev_share >= SPLIT_SUSPECT_RATIO):
                suspected = True
                unconfirmable_segments += 1
                direction = "⚠️ 疑似份额调整（未登记），本段流量不可确认"
            else:
                flow_est = round(share_change * avg_price / WAN_PER_YI, 2)  # 亿元（万份×元=万元）
                if abs(flow_est) < 0.3:
                    direction = "→ 持平"
                elif flow_est > 0:
                    direction = "🟢 净流入" if flow_est >= 3 else "🟢→ 小幅流入"
                else:
                    direction = "🔴 净流出" if abs(flow_est) >= 3 else "🔴→ 小幅流出"
            if flow_base is None:
                flow_base = {"date": prev_date, "shares": prev_share_raw,
                             "shares_split_adj": prev_share,
                             "close": prev_price_raw, "close_split_adj": prev_price,
                             "pos": prev_share_pos}
            flow_end = {"date": date_str, "shares": shares_val,
                        "shares_split_adj": shares_adj,
                        "close": close_val, "close_split_adj": close_adj,
                        "pos": row_pos}
            flow_segments += 1

        # 成交额格式化（亿元）。Tushare fund_daily.amount 单位为千元
        amount_e = round(amount_val / QIAN_PER_YI, 2) if amount_val is not None else None

        row_out = {
            "date": date_str,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "pre_close": pre_close_val,
            "pct_chg": pct_chg_val,
            "vol": int(vol_val) if vol_val is not None else None,     # 手
            "amount": amount_e,                                        # 亿元
            "turnover_rate": turnover,                                 # %
            "shares": round(shares_val, 2) if shares_val is not None else None,  # 万份（原始披露）
            "share_change": share_change,                              # 万份（拆分调整后差分）
            "flow_est": flow_est,                                      # 亿元
            "direction": direction,
        }
        if split_applied:
            row_out["shares_split_adj"] = shares_adj                   # 拆分调整后份额（复算用）
            row_out["close_split_adj"] = round(close_adj, 4)           # 拆分调整后收盘价（flow 金额口径）
        if suspected:
            row_out["suspected_adjustment"] = True
        rows.append(row_out)

        if shares_val is not None:
            prev_share = shares_adj
            prev_share_raw = shares_val
            prev_price = close_adj      # 调整价（flow 金额），仅当份额有效时更新
            prev_price_raw = close_val  # 原始价（base_row 留痕）
            prev_date = date_str
            prev_share_pos = row_pos
            prev_factor = factor

    # 去掉第一行（无变化数据）
    detail_rows = rows[1:]

    # 汇总
    flows = [r["flow_est"] for r in detail_rows if r["flow_est"] is not None]
    total_flow = round(sum(flows), 2) if flows else None
    avg_daily = round(total_flow / len(flows), 2) if flows and total_flow is not None else None

    if total_flow is not None and total_flow > 5:
        trend = "🟢 持续净流入"
    elif total_flow is not None and total_flow < -5:
        trend = "🔴 持续净流出"
    elif total_flow is not None:
        trend = "→ 资金面平稳"
    else:
        trend = "数据不足"

    # 近端（最后 5 日）方向：整体合计定性会掩盖近端转向（batch-test P1-4，
    # 实例：588000 20 日 +326.92 亿但近 5 日连续净流出 -39.88 亿）。
    # detail_rows 升序，最后 RECENT_FLOW_DAYS 行即最近窗口。
    recent_n = RECENT_FLOW_DAYS
    recent_flows = flows[-recent_n:]
    recent_flow = round(sum(recent_flows), 2) if recent_flows else None
    # 「近 N 日」实际跨度：flows 只含份额可算行（fund_share T+1 延迟使尾端
    # 1-2 日无份额），最近 N 个可算行可能覆盖 >N 个交易日（batch-test P1-4
    # 二次修复：trend 标注实际跨度，避免「近 5 日」实际跨 7 个交易日失真）。
    recent_span = recent_n
    if recent_flows:
        target = max(len(flows) - recent_n + 1, 1)  # flows 中 recent 段首行序号（1-based）
        seen = 0
        for i, r in enumerate(detail_rows):
            if r.get("flow_est") is not None:
                seen += 1
                if seen == target:
                    recent_span = len(detail_rows) - i
                    break
    if recent_flow is not None and total_flow is not None:
        if total_flow > 5 and recent_flow < 0:
            trend = f"{trend}（近 {recent_span} 日转净流出 {recent_flow:+.2f} 亿）"
        elif total_flow < -5 and recent_flow > 0:
            trend = f"{trend}（近 {recent_span} 日转净流入 {recent_flow:+.2f} 亿）"

    # 交易量趋势
    amounts = [r["amount"] for r in detail_rows if r["amount"] is not None]
    avg_amount = round(sum(amounts) / len(amounts), 2) if amounts else None
    max_amount = max(amounts) if amounts else None

    # A3（2026-10-10 根治）：份额变化与 flow 合计**共同起止**。
    # 旧实现从 detail_rows 首尾非 None shares 自算——detail_rows = rows[1:]（r0 被
    # 丢弃），而 flow 合计含 r0→r1 的首个变化段 → 同一行里「份额变化」与「资金流
    # 合计」起点不同（peers._flow_row 复算同病）。现由引擎统一给出：
    #   flow_window = {start: 首个可算段的基期, end: 末个可算段的期末, segments,
    #                  trading_days, missing_share_days}
    #   share_total_change / share_change_pct 都取该同一窗口（含首日变化段）。
    # 尾部份额缺失（T+1 延迟）不在窗内：flow_window.end 可能早于 rows 末端，
    # 由 date_range 对比即「实际覆盖」披露，不把无份额日当无变化。
    flow_window: dict[str, Any] | None = None
    share_total_change = None
    share_change_pct = None
    if flow_base is not None and flow_end is not None:
        base_pos = int(flow_base.get("pos") or 0)
        end_pos = int(flow_end.get("pos") or 0)
        trading_days = end_pos - base_pos + 1
        missing_share_days = max(trading_days - 1 - flow_segments, 0)
        confirmable = unconfirmable_segments == 0
        flow_window = {
            "start": flow_base["date"], "end": flow_end["date"],
            "segments": flow_segments, "trading_days": trading_days,
            "missing_share_days": missing_share_days,
            "unconfirmable_segments": unconfirmable_segments,
            "confirmable": confirmable,
            # R5：起始基期行（复算所需的原始/调整份额与价格）随封存冻结
            "base_row": {"date": flow_base["date"], "shares": flow_base["shares"],
                         "shares_split_adj": flow_base["shares_split_adj"],
                         "close": flow_base["close"],
                         "close_split_adj": flow_base.get("close_split_adj")},
            "end_row": {"date": flow_end["date"], "shares": flow_end["shares"],
                        "shares_split_adj": flow_end["shares_split_adj"],
                        "close": flow_end["close"],
                        "close_split_adj": flow_end.get("close_split_adj")},
            "note": ("共同起止：首个可算变化段基期 ~ 末个可算段期末；缺值跨间隔只计"
                     "一段（segments ≤ trading_days−1）；尾部无份额日不在窗内"
                     "（与 date_range 对比即实际覆盖）；份额变化按已登记拆分调整"
                     "（前复权=最新基准）"),
        }
        if not confirmable:
            flow_window["note"] += ("；**含未登记份额跳变段**：该段流量不可确认，"
                                    "份额变化含机械成分，flow 合计只含可确认段")
        base_shares = safe_float(flow_base.get("shares_split_adj"))
        if base_shares:
            delta = flow_end["shares_split_adj"] - base_shares
            share_total_change = round(delta, 2)
            share_change_pct = round(delta / base_shares * 100, 2)

    date_range = f"{detail_rows[0]['date']} ~ {detail_rows[-1]['date']}" if detail_rows else ""

    result = {
        "symbol": symbol,
        "available": True,
        "date_range": date_range,
        "rows": detail_rows,
        "summary": {
            "total_flow_est": total_flow,
            "avg_daily_flow_est": avg_daily,
            "trend": trend,
            "row_count": len(detail_rows),
            "window_days": days,
            "flow_window": flow_window,
            "recent_flow_est": recent_flow,
            "recent_flow_days": recent_span,
            "avg_amount_e": avg_amount,
            "max_amount_e": max_amount,
            "share_total_change": share_total_change,
            "share_change_pct": share_change_pct,
            # 正/负/平流日计数与合计（P0：报告层引用引擎字段，禁止对 rows 目视计数）
            "inflow_days": len([f for f in flows if f > 0]),
            "outflow_days": len([f for f in flows if f < 0]),
            "flat_days": len([f for f in flows if f == 0]),
            "inflow_sum_est": round(sum(f for f in flows if f > 0), 2) if flows else None,
            "outflow_sum_est": round(sum(f for f in flows if f < 0), 2) if flows else None,
        },
    }
    if split_applied:
        # R4：拆分调整留痕（原始份额仍在 rows[].shares；调整值见 rows[].shares_split_adj）
        result["summary"]["split_adjusted"] = True
        result["summary"]["split_events"] = [
            {"effective_from": e.get("effective_from"), "ratio": e.get("ratio"),
             "type": e.get("type"), "record_date": e.get("record_date"),
             "ex_date": e.get("ex_date"), "source": e.get("source"),
             "note": e.get("note")} for e in split_events]
    if clipped:
        result["note"] = (
            f"请求 {days} 日窗口，取数上限（{_SHARE_FETCH_NATURAL_DAYS} 自然日）"
            f"仅覆盖 {len(detail_rows)} 行，已按可用数据返回"
        )
    # B02-1：估算标签与「价格涨≠净流入」措辞的单一出处
    # A3：逐段 flow_est 按各自区间均价估算——合计与「净份额变化×单一价格」**不必
    # 相等**（份额净变化为零时，先申后赎因两段价格不同，flow 合计仍可为非零）；
    # 两者不是一致性约束，不得据此报错。
    result["caliber_note"] = (
        "flow_est=份额变化×区间均价估算（亿元），为估算方向标签（Tushare fund_share "
        "T+1 延迟），非官方申赎数据；价格涨跌幅 ≠ 资金净流入；各段均按本段区间均价"
        "估算，合计与「净份额变化×单一价格」不必相等（价格差异，非错误）"
        + ("；份额序列已按已登记拆分调整（前复权=最新基准）——拆分本身不是申赎流"
           if split_applied else "")
    )
    return result


# ---------------------------------------------------------------------------
# 估算资产净值（B02-1）：份额×单位净值，仅同日匹配
# ---------------------------------------------------------------------------

def query_etf_est_net_assets(symbol: str) -> dict:
    """份额×单位净值的**估算**资产净值（仅同日匹配；非官方披露规模）。

    份额来自 Tushare fund_share（`trade_date` + `fd_share`，万份），净值来自
    `fetch_etf_nav`（`date` + `nav`）。只有当「最新可用份额日」与「同一日期
    的净值」同时存在时才给出估算值；份额日无同日净值 → `status="mismatch"`
    并列出两个日期、**不给数字**（不得用隔日 NAV 冒充同期）。

    R06：attempted/skipped 由**producer 结构化留痕**推导（份额源无 token 时
    未发起网络 → 不得硬编码登记为 attempted，也不得丢弃真实原因）。
    """
    sh_env = _bridge_get("get_etf_share_history", symbol)
    sh_state = _leg_call_state(sh_env, getter="get_etf_share_history")
    if sh_env is None or sh_env.get("status") != "ok" or not sh_env.get("fund_share"):
        share_attempted = ["Tushare fund_share"] if sh_state["attempted"] else []
        skipped: list[dict] = []
        if not sh_state["attempted"]:
            skipped.append({"source": "Tushare fund_share（份额）",
                            "reason": sh_state["skip_reason"]})
        elif sh_env is not None:
            share_attempted_note = (sh_env.get("note")
                                    or "Tushare fund_share 未取到数据")
        else:
            share_attempted_note = "Tushare fund_share 未取到数据（桥内失败/缺溯源）"
        skipped.append({"source": "fund_etf_fund_info_em（净值）",
                        "reason": "份额不可用，净值腿未发起取数（提前返回）"})
        return {"symbol": symbol, "status": "missing", "value_yi": None,
                "attempted_sources": share_attempted,
                "skipped_sources": skipped,
                "note": f"份额序列不可用（{sh_state['skip_reason'] if not sh_state['attempted'] else share_attempted_note}）"}
    nav_env = _bridge_get("get_etf_nav", symbol)
    nav_state = _leg_call_state(nav_env, getter="get_etf_nav")
    if nav_env is None or nav_env.get("status") != "ok" or not nav_env.get("rows"):
        nav_attempted = ["Tushare fund_share",
                         "fund_etf_fund_info_em（净值）"] if nav_state["attempted"] else \
            ["Tushare fund_share"]
        nav_skipped = (None if nav_state["attempted"] else
                       [{"source": "fund_etf_fund_info_em（净值）",
                         "reason": nav_state["skip_reason"]}])
        note = (f"净值序列不可用（{(nav_env or {}).get('note') or (nav_env or {}).get('error') or '未取到'}）"
                if nav_state["attempted"] else
                f"净值腿未发起取数（{nav_state['skip_reason']}）")
        out = {"symbol": symbol, "status": "missing", "value_yi": None,
               "attempted_sources": nav_attempted, "note": note}
        if nav_skipped:
            out["skipped_sources"] = nav_skipped
        return out
    nav_by_date: dict[str, float] = {}
    for r in nav_env.get("rows") or []:
        d = str(r.get("date", ""))
        v = safe_float(r.get("nav"))
        if d and v is not None:
            nav_by_date[d] = v
    shares_by_date: dict[str, float] = {}
    for r in sh_env.get("fund_share") or []:
        d = str(r.get("trade_date", ""))
        # fund_share.trade_date 为 YYYYMMDD；净值源为 YYYY-MM-DD → ISO 归一后匹配
        iso = (f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d)
        v = safe_float(r.get("fd_share"))
        if iso and v is not None and math.isfinite(v):
            shares_by_date[iso] = v
    if not shares_by_date or not nav_by_date:
        return {"symbol": symbol, "status": "missing", "value_yi": None,
                "attempted_sources": ["Tushare fund_share", "fund_etf_fund_info_em（净值）"],
                "note": "份额或净值为空（无有效行）"}
    shares_date = max(shares_by_date)
    nav_date = max(nav_by_date)
    if shares_date not in nav_by_date:
        return {
            "symbol": symbol, "status": "mismatch", "value_yi": None,
            "attempted_sources": ["Tushare fund_share", "fund_etf_fund_info_em（净值）"],
            "shares_date": shares_date, "shares_wan": shares_by_date[shares_date],
            "nav_date": nav_date, "nav": nav_by_date[nav_date],
            "caliber": "份额×单位净值（估算资产净值）",
            "note": "最新份额日无同日单位净值：不给估算值（不得用隔日 NAV 当同期）",
        }
    nav = nav_by_date[shares_date]
    return {
        "symbol": symbol, "status": "ok",
        "value_yi": round(shares_by_date[shares_date] * nav / WAN_PER_YI, 2),
        "as_of": shares_date, "shares_date": shares_date, "nav_date": shares_date,
        "shares_wan": shares_by_date[shares_date], "nav": nav,
        "caliber": "同日份额×单位净值（估算资产净值，非官方披露规模）",
        "not_official_disclosure": True,
        "attempted_sources": ["Tushare fund_share", "fund_etf_fund_info_em（净值）"],
        "note": "估算值；官方规模口径以基金定期报告/公告披露为准",
    }


# ---------------------------------------------------------------------------
# 跟踪质量（B02-2）：同收益类型才可确认的 TD/TE
# ---------------------------------------------------------------------------
# 契约（负责人 2026-10-08 纠偏）：确认性 TD/TE 仅当两腿收益类型/币种/频率
# 一致时成立。现有基准只有价格指数 → 默认 pipeline 输出 limited +
# td_pct/te_pct_annualized=None，价格基准偏离只作研究数值（research_only）。
# 严禁以一句 caliber 免责声明放行类型不一致的「确认指标」。
# R3 口径分离：TD=窗口首末有效端点累计差（缺值区间不因剔除消失）；TE=采样
# 区间日偏离波动（覆盖度显式披露）。纯样本收益列表不足以定义窗口 TD——
# 两个函数的 TD 都必须显式传 window_returns，缺端点即报错。

#: 允许的收益类型词表（确认性比较必须两腿相等）
RETURN_TYPES = ("total_return", "price_return")
#: 允许的频率词表
RETURN_FREQUENCIES = ("daily",)
#: 日频年化系数（A 股交易日；显式声明，不允许 √365 混用）
TRADING_DAYS_PER_YEAR = 252
#: 确认性 TD/TE 的最小共同样本（试验下限，非行业标准）
TRACKING_MIN_COMMON = 60


class TrackingCaliberError(ValueError):
    """跟踪指标两腿不可比（收益类型/币种/频率不一致或样本不足）——拒绝计算。"""


def _skip_once(skips: list[dict], entry: dict) -> None:
    """同一次跳过只记一条（同 source 去重，保留既有更贴近真实原因的一条）。

    R10：compute_etf_tracking 沿用 kline.skipped_sources 后又追加同一指数腿
    （原因不同）→ 同一次未发生的事被记两条，消费方计数/呈现重复。
    """
    if any(s.get("source") == entry.get("source") for s in skips):
        return
    skips.append(entry)


def _deviation_core(returns_fund: list[float], returns_bench: list[float], *,
                    window_returns: tuple[float, float] | None,
                    annualization_factor: int) -> dict:
    """TD/TE 公共计算核（确认指标与研究数值两条路径共用同一公式与校验）。

    R16/R17 统一：年化系数由调用方传入（不得各自硬编码 √252）；
    空样本/不等长/非有限值统一抛 :class:`TrackingCaliberError`（不再
    ZeroDivisionError 或静默 NaN）。返回 ``{td_pct, te_pct_annualized,
    sample_n, window_returns}``。
    """
    if len(returns_fund) != len(returns_bench):
        raise TrackingCaliberError(
            f"两腿样本长度不等（{len(returns_fund)} vs {len(returns_bench)}）——"
            "拒绝截短配对；须先按相邻有效区间对齐两腿（_align_legs_by_date）")
    n = len(returns_fund)
    if n == 0:
        raise TrackingCaliberError("空样本（0 个收益区间）——无 TD/TE 可算")
    if n < 2:
        raise TrackingCaliberError(
            f"样本不足以估计日偏离波动（n={n}，ddof=1 需 ≥2）——拒绝输出 TE")
    win_f, win_b = _validate_window_returns(window_returns)
    diffs: list[float] = []
    for a, b in zip(returns_fund, returns_bench):
        if not (math.isfinite(a) and math.isfinite(b)):
            raise TrackingCaliberError("收益序列含非有限值（NaN/Inf）")
        diffs.append(a - b)
    mean = sum(diffs) / n
    var = sum((x - mean) ** 2 for x in diffs) / (n - 1)
    te_pct = math.sqrt(var) * math.sqrt(annualization_factor) * 100
    return {
        "td_pct": round((win_f - win_b) * 100, 2),
        "te_pct_annualized": round(te_pct, 2),
        "sample_n": n,
        "annualization_factor": annualization_factor,
        "window_returns": {"fund": win_f, "bench": win_b},
    }


def compute_tracking_metrics(returns_fund: list[float], returns_bench: list[float], *,
                             fund_return_type: str, bench_return_type: str,
                             fund_currency: str, bench_currency: str,
                             window_returns: tuple[float, float] | None = None,
                             frequency: str = "daily",
                             annualization_factor: int = TRADING_DAYS_PER_YEAR,
                             min_common: int = TRACKING_MIN_COMMON) -> dict:
    """两腿收益序列 + 窗口端点收益 → 确认性 TD/TE（**先校验口径**，不可比即拒绝）。

    **两种口径分开定义（R3，不得混用）**：

    - TD（**窗口起止累计偏离**）=（基金窗口端点收益 − 基准窗口端点收益）×100，
      端点收益由调用方给出（``window_returns``，= 窗口首末**有效端点**取值比 − 1）。
      纯样本收益列表不足以定义窗口累计：有效区间不连续时，区间样本复利的
      ∏(1+r)−1 会漏掉被剔除区间的价格变动，既不是窗口首末累计、也不是任何
      单一可解释量（R3 负责人反例：端点差 25% vs 样本复利 12.79%）——因此
      本函数**不接受**由样本复利推导 TD，缺端点即报错，不静默出数。
    - TE（**日偏离波动**）= std(e_t, ddof=1) × √annualization × 100，
      e_t = r_fund,t − r_bench,t，只覆盖传入的样本区间（覆盖度由调用方
      在底稿披露，见 ``compute_etf_tracking.input_evidence.sampled_coverage``）。

    样本 < min_common → 拒绝（由调用方按 insufficient 披露，不返回数字）。

    **两腿必须等长**（B-F03）：按日期配对由调用方完成（``_align_legs_by_date``）；
    本函数拒绝不等长输入——历史上 ``min(len)`` 静默截尾会把 61/60 个收益
    强行配对成「同窗口」，属未证同期的错配。
    """
    if fund_return_type not in RETURN_TYPES or bench_return_type not in RETURN_TYPES:
        raise TrackingCaliberError(f"收益类型词表外: {fund_return_type!r}/{bench_return_type!r}")
    if fund_return_type != bench_return_type:
        raise TrackingCaliberError(
            f"两腿收益类型不一致（{fund_return_type} vs {bench_return_type}）——"
            "不可输出确认性 TD/TE")
    if fund_currency != bench_currency:
        raise TrackingCaliberError(
            f"两腿币种不一致（{fund_currency} vs {bench_currency}）——不可比")
    if frequency not in RETURN_FREQUENCIES:
        raise TrackingCaliberError(f"频率词表外: {frequency!r}")
    n = len(returns_fund)
    if n < min_common:
        raise TrackingCaliberError(f"共同样本不足（{n} < {min_common}）——停止确认性计算")
    core = _deviation_core(returns_fund, returns_bench,
                           window_returns=window_returns,
                           annualization_factor=annualization_factor)
    return {
        **core,
        "frequency": frequency,
        "fund_return_type": fund_return_type,
        "bench_return_type": bench_return_type,
        "currency": fund_currency,
        # 口径留痕（R3）：TD 来自窗口端点、TE 来自样本区间——消费方/复算可据此
        # 区分，不得把 TE 样本数当 TD 的窗口覆盖。
        "td_scope": "window_endpoint",
        "te_scope": "sampled_intervals",
    }


def _validate_window_returns(window_returns: tuple[float, float] | None
                             ) -> tuple[float, float]:
    """窗口端点收益校验：两个有限且 > −100%（1+r 须为正）的累计收益。

    缺端点（None）→ TrackingCaliberError（**拒绝**用样本复利代替窗口累计）；
    保持报错类型为模块统一的 TrackingCaliberError（外部复现脚本按此断言）。
    """
    if window_returns is None:
        raise TrackingCaliberError(
            "缺窗口端点收益（window_returns）：TD 必须由窗口首末有效端点定义，"
            "不得用样本区间复利充当窗口累计偏离")
    try:
        win_f, win_b = float(window_returns[0]), float(window_returns[1])
    except (TypeError, IndexError, ValueError) as exc:
        raise TrackingCaliberError(
            f"窗口端点收益不可用（{window_returns!r}）——TD 必须由窗口首末有效端点定义，"
            "不得用样本区间复利代替") from exc
    for v, leg in ((win_f, "基金"), (win_b, "基准")):
        if not math.isfinite(v) or v <= -1.0:
            raise TrackingCaliberError(f"{leg}窗口端点收益非法（{v}）：须为 >−100% 的有限值")
    return win_f, win_b


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _norm_trade_date(value: Any) -> str | None:
    """交易日归一：ISO（``YYYY-MM-DD``，容忍尾部时间）或 compact（``YYYYMMDD``）
    → ``YYYY-MM-DD``；不可解析/非法日历日 → None（B-F03 统一日期合同）。"""
    s = str(value).strip() if value is not None else ""
    if not s:
        return None
    m = _ISO_DATE_RE.match(s[:10])
    if not m:
        m = _COMPACT_DATE_RE.match(s)
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        datetime(int(y), int(mo), int(d))
    except ValueError:
        return None
    return f"{y}-{mo}-{d}"


def _dated_values(rows: list[dict], value_key: str) -> tuple[dict[str, float], dict]:
    """行序列 → ``{归一日: 值}``（仅有限正数参与；同一日期重复值去重、冲突值弃用）。

    B-F03 去重合同：同一归一日期的**不同值**无法判定哪个正确 → 该日期两腿
    一起弃用并计入 ``conflicts``；零/负/None/NaN/±Inf/不可解析日期均为无效
    值（日收益在零值处不可定义）→ 计入 ``dropped_values``。返回
    ``(values, stats)``，stats 供底稿如实披露丢弃情况，不静默。
    """
    out: dict[str, float] = {}
    conflicts: set[str] = set()
    dropped_values = 0
    for r in rows or []:
        d = _norm_trade_date(r.get("date"))
        v = safe_float(r.get(value_key))
        if d is None or v is None or not math.isfinite(v) or v <= 0:
            dropped_values += 1
            continue
        if d in out and out[d] != v:
            conflicts.add(d)
            continue
        out[d] = v
    for d in conflicts:
        out.pop(d, None)
    stats = {"dropped_values": dropped_values,
             "conflict_dates": sorted(conflicts)}
    return out, stats


def _returns_over_intervals(intervals: list[tuple[str, str]],
                            values: dict[str, float]) -> list[float]:
    """按给定**收益区间**计算收益（两腿共用同一区间表 → 长度恒等）。"""
    return [values[b] / values[a] - 1.0 for a, b in intervals]


def _align_legs_by_date(nav_rows: list[dict], bench_rows: list[dict]) -> dict:
    """两腿按**已知日期并集脊**取相邻有效区间对齐（B-F03 合同；R2 修正）。

    1. 两腿各自归一日期 + 排序 + 去重（冲突值弃该日）；
    2. 已知日期脊 = 两腿日期**并集**（任一侧的日期都算已知交易日候选——
       不新增日历数据源，也不把某一腿的缺失当成「非交易日」）；
    3. 沿脊只取**相邻日对**，且两端日期在两腿均有有效值时才保留为收益
       区间；任一端缺值 → 该相邻对整对剔除（**不得**把跨两个交易日的
       收益当作日频样本再乘 √252——R2 负责人反例：09-01→09-03 桥接）；
    4. 保留区间可以**不连续**；逐区间计算两腿收益 → 长度恒等。

    返回 ``{dates, intervals, fund_values, bench_values, returns_fund,
    returns_bench, dropped, window, contiguous}``。``dates`` 仅含实际参与
    区间端点的日期（不参与任何区间的孤立日期不出现）；复算必须用
    ``intervals`` 逐对取值，不得对 ``dates`` 再做相邻配对。
    """
    fund_values, fund_stats = _dated_values(nav_rows, "nav")
    bench_values, bench_stats = _dated_values(bench_rows, "close")
    # 无效/冲突值仍有已知交易日期，不能从脊中消失后桥接两侧日收益。
    spine = sorted({d for rows in (nav_rows, bench_rows) for r in rows or []
                    if (d := _norm_trade_date(r.get("date"))) is not None})
    intervals: list[tuple[str, str]] = []
    skipped_intervals = 0
    for a, b in zip(spine, spine[1:]):
        if (a in fund_values and b in fund_values
                and a in bench_values and b in bench_values):
            intervals.append((a, b))
        else:
            skipped_intervals += 1
    dates = sorted({d for pair in intervals for d in pair})
    window = f"{dates[0]} ~ {dates[-1]}" if dates else None
    # 公共区间（两腿都有值的首末日期）内的相邻日对总数 vs 保留数 = 样本内缺口；
    # 窗口外（两腿序列覆盖范围不同）的剔除另计，不冒充样本内缺口
    common = sorted(set(fund_values) & set(bench_values))
    span = (common[0], common[-1]) if common else None
    interior_pairs = [(a, b) for a, b in zip(spine, spine[1:])
                      if span and span[0] <= a and b <= span[1]]
    interior_gaps = len(interior_pairs) - len(intervals)
    return {
        "dates": dates,
        "intervals": [[a, b] for a, b in intervals],
        "fund_values": fund_values,
        "bench_values": bench_values,
        "returns_fund": _returns_over_intervals(intervals, fund_values),
        "returns_bench": _returns_over_intervals(intervals, bench_values),
        "dropped": {
            "fund": fund_stats,
            "benchmark": bench_stats,
            "joint_days_dropped": len(set(fund_values) ^ set(bench_values)),
            "skipped_intervals": skipped_intervals,
            "interior_gaps": interior_gaps,
        },
        "window": window,
        "contiguous": interior_gaps == 0 and bool(intervals),
    }


def compute_etf_tracking(symbol: str, *, days: int = 250,
                         min_common: int = TRACKING_MIN_COMMON) -> dict:
    """ETF 跟踪质量块（TD/TE 合同见段首注释）。

    基金腿=复权单位净值（`query_etf_kline` nav_history，需 `adj_applied=True`）；
    基准腿=指数收盘价（`fetch_etf_index_daily`，**价格指数**）。两腿类型不同 →
    `status="limited"`：`td_pct`/`te_pct_annualized` 为 None，价格基准偏离只入
    `price_benchmark_deviation`（research_only）；仅当两腿同口径时才 available。

    基准指数代码解析（B-F05）：``CSINDEX_MAP``（指数 PE 映射）→ 已核身份
    ``identity.tracked_index.code`` 兜底——PE 未映射 ≠ 产品没有收益基准；
    两者都无 → not_mapped（未知保持未知）。取数走既有指数日线接口，失败
    透明记 missing。

    对齐（B-F03，R2 修正）：两腿先各自归一日期并排序去重（冲突值弃用），
    再在**已知日期并集脊**上只取相邻日对且两端两腿均有有效值的区间——
    缺值处相邻区间整段剔除，**不桥接跨交易日收益充日频样本**（缺值不再让
    两腿收益跨不同区间；保留区间可不连续）。两腿收益由同一区间表构造 →
    长度恒等；不等长（含生产异常）由 ``compute_tracking_metrics`` 拒绝。

    偏离口径（R3）：TD=**窗口首末有效端点的两腿累计收益差**（缺值区间的
    价格变动不会因剔除而消失，端点差才是窗口起止累计偏离；有效区间样本
    复利只作区间级留痕，不作为窗口 TD 输出）；TE=采样区间的日偏离波动
    （只覆盖 intervals 样本，覆盖度见底稿 ``sampled_coverage``）。

    A6（2026-10-10）：① 原文披露的「基金 vs 业绩比较基准」收益对比（定期报告
    §3.2.1，基准含汇率调整）**优先**随块返回（``official_benchmark_comparison``）；
    ② 两腿币种不一致（如基金 CNY vs 恒指 HKD）→ 停止 TD/TE 与跨币种价格偏离
    计算（不因价格指数可取就等同复权 NAV 总收益；汇率时点/收益类型须全对齐）。
    """
    base = {
        "status": None, "td_pct": None, "te_pct_annualized": None,
        "sample_n": 0, "annualization_factor": TRADING_DAYS_PER_YEAR,
        "window": None,
        # B-F01：attempted_sources=本次实际发起过的取数（由 producer 分支留痕
        # 逐条沿用，不由静态源表/状态猜测合成）；source_trace 区分 L2 缓存读取
        # /本次网络与失败理由；skipped_sources=未发起的腿及原因（含无 token）。
        "attempted_sources": [],
        "source_trace": [],
        "skipped_sources": [],
        "index_code": None,
        "index_code_source": None,
        "caliber": {
            "fund_return": "复权单位净值（前复权，分红/拆分断点已消除）≈总收益",
            "benchmark_return": "指数收盘价（价格指数，非总收益）",
            "currency": "CNY", "frequency": "daily",
            "fund_return_type": "total_return",
            "bench_return_type": "price_return",
            "annualization": f"√{TRADING_DAYS_PER_YEAR}",
            "source": {
                "nav": "fund_etf_fund_info_em 单位净值 + Tushare fund_adj 前复权",
                "benchmark": "stock_zh_index_daily 指数收盘价（价格口径）",
            },
        },
        "not_double_count_fees": "基金净值收益已包含管理费/托管费；不得再单独扣减",
        "note": None,
        "price_benchmark_deviation": None,
        # A6：原文披露的「基金 vs 业绩比较基准」收益对比（定期报告口径，基准含
        # 汇率调整）——跟踪质量**优先引用**；未登记 = None（不推断）。
        "official_benchmark_comparison": None,
        # B05 负责人补充：同代冻结完整性——真正使用的两腿底稿（日期+取值）
        # 随 payload 冻结，使 seal 对原始序列/输入变化敏感（可独立复算）。
        "input_evidence": None,
    }
    # A6：官方披露对比优先（有则随块返回；不依赖两腿能否自算）
    disclosed = ETF_BENCHMARK_PERFORMANCE.get(symbol)
    if disclosed:
        base["official_benchmark_comparison"] = {
            **{k: v for k, v in disclosed.items()},
            "role": "官方披露口径（优先于引擎两腿自算）；基准=经人民币汇率调整的恒生指数收益率",
        }
    # B-F05/R12：基准指数 = 统一解析（CSINDEX_MAP → 身份登记 tracked_index）；
    # PE 未映射 ≠ 产品没有收益基准，反之亦然——两处解析必须同源
    ident = _identity_for_index_resolution(symbol)
    idx_res = resolve_tracked_index(symbol, identity=ident)
    idx = idx_res["code"] or ""
    fund_ccy = ((ident or {}).get("currency") or "CNY")
    # csindex 映射路径（境内指数）按 CNY；身份登记可显式给基准币种（如 HKD）
    bench_ccy = idx_res.get("currency") or ("CNY" if idx_res["source"] == "CSINDEX_MAP" else None)
    base["caliber"]["currency"] = fund_ccy
    base["caliber"]["benchmark_currency"] = bench_ccy
    if idx:
        base["index_code"] = idx
        base["index_code_source"] = idx_res["source"]
    if idx and bench_ccy and bench_ccy != fund_ccy:
        # A6：币种不一致 → 不确认 TD/TE，也不输出跨币种「价格偏离」（须经汇率
        # 调整的基准口径；QDII/跨境产品的原文披露见 official_benchmark_comparison）。
        base.update(
            status="limited",
            note=(f"基准腿币种（{bench_ccy}）≠ 基金腿（{fund_ccy}）：跨币种累计偏离"
                  "不得由两腿价格序列直接相减（须经汇率调整的基准口径与汇率时点对齐）；"
                  "停止确认性 TD/TE 与价格偏离计算"
                  + ("；原文披露对比见 official_benchmark_comparison"
                     if base["official_benchmark_comparison"] else "")))
        base["skipped_sources"] = [
            {"source": "基金净值（tracking 腿）",
             "reason": f"币种不一致（{fund_ccy} vs {bench_ccy}），两腿不可比，未发起"},
            {"source": "指数日线（tracking 腿）",
             "reason": f"币种不一致（{fund_ccy} vs {bench_ccy}），两腿不可比，未发起"},
        ]
        return base
    if not idx:
        base.update(status="not_mapped",
                    note="无跟踪指数代码（CSINDEX_MAP 与身份登记均无）：无法构建基准腿")
        base["skipped_sources"] = [
            {"source": "基金净值", "reason": "无跟踪指数代码，未发起取数（基准腿不可建）"},
            {"source": "指数日线", "reason": "无跟踪指数代码，未发起取数"},
        ]
        return base
    # 基金腿：query_etf_kline 内部依次尝试 EM ETF 净值（列格式错位时降级
    # fund_open_fund_info_em）与 Tushare fund_adj 复权因子；逐条沿用其真实
    # 调用留痕（B-F01），不再在此追加惯例清单。
    kline = query_etf_kline(symbol, days=days)
    nav_src = str(kline.get("source") or "fund_etf_fund_info_em")
    base["attempted_sources"] = [str(s) for s in kline.get("attempted_sources") or []]
    base["source_trace"] = [dict(s) for s in kline.get("source_trace") or []]
    base["skipped_sources"] = [dict(s) for s in kline.get("skipped_sources") or []]
    if kline.get("status") != "available" or not kline.get("nav_history"):
        _skip_once(base["skipped_sources"],
                   {"source": "stock_zh_index_daily（指数日线）",
                    "reason": "基金净值不可用，基准腿未发起取数（提前返回）"})
        base.update(status="missing_nav",
                    note=f"基金净值序列不可用（{kline.get('_error') or 'no nav rows'}）")
        return base
    if not kline.get("adj_applied"):
        _skip_once(base["skipped_sources"],
                   {"source": "stock_zh_index_daily（指数日线）",
                    "reason": "基金腿未复权，基准腿无意义（提前返回）"})
        base.update(status="limited",
                    note="基金腿未复权（分红/拆分断点未消除）：无法证明与基准可比，"
                         "停止确认性 TD/TE")
        return base
    bench_env = _bridge_get("get_etf_index_daily", idx)
    bench_src = "stock_zh_index_daily（指数日线）"
    # R05：None 不猜「本次网络」——桥不可用（未发起）记 skipped；已发起但失败
    # 记 attempted + trace 理由（两类由结构化探针区分）
    bench_state = _leg_call_state(bench_env, getter="get_etf_index_daily")
    if bench_state["attempted"]:
        base["attempted_sources"].append(bench_src)  # 已真实发起（B-F01）
    else:
        _skip_once(base["skipped_sources"],
                   {"source": bench_src, "reason": bench_state["skip_reason"]})
    if bench_env is None or bench_env.get("status") != "ok" or not bench_env.get("rows"):
        base["source_trace"].append({
            "source": bench_src, "via": bench_state["via"], "outcome": "missing",
            "reason": f"{'已发起取数但不可得' if bench_state['attempted'] else '未发起取数'}"
                      f"（index={idx}；{bench_state['skip_reason'] or (bench_env or {}).get('error') or '信封 missing/空'}）"})
        base.update(status="missing_benchmark",
                    note=f"缺基准序列（指数 {idx} 日线"
                         f"{'不可得' if bench_state['attempted'] else '未发起取数'}）；"
                         "停止确认性 TD/TE")
        return base
    base["source_trace"].append({
        "source": bench_src, "via": _read_via(bench_env), "outcome": "ok",
        "reason": f"index={idx}"})
    nav_rows = kline.get("nav_history") or []
    bench_rows = bench_env.get("rows") or []
    # B-F03（R2）：日期归一/排序/去重 → 已知日期并集脊 → 只取相邻且两端两腿
    # 有效的区间；缺值处区间整段剔除，不桥接跨交易日收益充日频样本。
    aligned = _align_legs_by_date(nav_rows, bench_rows)
    dates = aligned["dates"]
    intervals = aligned["intervals"]
    rf, rb = aligned["returns_fund"], aligned["returns_bench"]
    n = len(rf)  # 由同一区间表构造 → 两腿恒等长（= 保留区间数）
    window = aligned["window"]
    base["window"] = window
    base["sample_n"] = n
    skipped = int((aligned["dropped"] or {}).get("skipped_intervals") or 0)
    interior_gaps = int((aligned["dropped"] or {}).get("interior_gaps") or 0)
    outside_skipped = max(skipped - interior_gaps, 0)
    # R3：窗口起止累计偏离只取**窗口首末有效端点**（首末区间端点两腿均有效）。
    # 有效区间样本复利 ≠ 窗口累计偏离：缺值剔除掉的区间价格变动不在样本里，
    # 复利会漏掉它们（负责人反例：端点差 25% vs 样本复利 12.79%）。
    if intervals:
        first_d, last_d = intervals[0][0], intervals[-1][1]
        window_returns = (
            aligned["fund_values"][last_d] / aligned["fund_values"][first_d] - 1.0,
            aligned["bench_values"][last_d] / aligned["bench_values"][first_d] - 1.0)
    else:
        window_returns = None  # 无有效区间 → 无窗口端点 → 停止累计（不猜）
    if interior_gaps:
        intervals_note = (
            f"非连续区间（日频存疑）：窗口内 {interior_gaps} 个相邻日对因单腿缺值"
            "整对剔除——不得桥接跨交易日收益，也不得按日期列表再相邻配对复算；"
            f"累计偏离只取窗口首末有效端点（{window}），日偏离波动仅覆盖 {n} 个"
            f"有效区间（{n}/{n + interior_gaps}）")
    else:
        intervals_note = "窗口内连续：全部相邻日对两端两腿有效（端点累计=区间复利一致）"
        if outside_skipped:
            intervals_note += (f"（窗口外另有 {outside_skipped} 个相邻日对因单腿"
                               "缺值未计入，系两腿序列覆盖范围不同）")
    # 同代冻结底稿：真正参与计算的**收益区间**（相邻日对）+ 端点上两腿取值，
    # 随 payload 封存；序列/来源/输入变化 → payload 哈希变化 → 旧签认失效。
    base["input_evidence"] = {
        "legs": {
            "fund": {
                "series": ("单位净值（Tushare fund_adj 前复权）"
                           if kline.get("adj_applied") else "单位净值（未复权）"),
                "source": nav_src,
                "return_type": "total_return",
                "rows": [[d, aligned["fund_values"][d]] for d in dates],
            },
            "benchmark": {
                "series": "指数收盘价（价格指数）",
                "source": bench_src,
                "return_type": "price_return",
                "rows": [[d, aligned["bench_values"][d]] for d in dates],
            },
        },
        "currency": "CNY", "frequency": "daily",
        "weights": "等权（逐日收益差，无加权）",
        "alignment": "日期归一（ISO/compact→YYYY-MM-DD）+ 已知日期并集脊上"
                     "只取相邻日对且两端两腿有效值（缺值处区间整段剔除，"
                     "不桥接跨交易日收益）；保留区间可不连续，见 intervals",
        "intervals": intervals,
        "intervals_note": intervals_note,
        "dropped": aligned["dropped"],
        "common_window": window, "sample_n": n,
        # R3：窗口端点收益与取样覆盖随底稿冻结（TD 与 TE 两种口径分开可复算）
        "window_endpoints": (
            {"fund": [[first_d, aligned["fund_values"][first_d]],
                      [last_d, aligned["fund_values"][last_d]]],
             "benchmark": [[first_d, aligned["bench_values"][first_d]],
                           [last_d, aligned["bench_values"][last_d]]]}
            if intervals else None),
        "window_returns_pct": (
            {"fund": round(window_returns[0] * 100, 4),
             "bench": round(window_returns[1] * 100, 4)} if window_returns else None),
        "sampled_coverage": {
            "sampled_intervals": n,
            "interior_adjacent_pairs": n + interior_gaps,
            "coverage_pct": (round(n / (n + interior_gaps) * 100, 1)
                             if (n + interior_gaps) else None),
            "note": "TE（日偏离波动）只覆盖 sampled_intervals；窗口累计偏离（TD）"
                    "取窗口首末有效端点，与样本覆盖无关",
        },
        "deviation_formula": "TD=(基金窗口端点收益−基准窗口端点收益)×100"
                             "（窗口首末有效端点，非区间样本复利）；"
                             "TE=std(rf−rb, ddof=1)×√252（rf/rb 按 intervals 逐区间计算，"
                             "只覆盖 sampled_intervals）",
        "note": "报告/QC/HTML 只应使用本底稿复算（TD 用 window_endpoints、"
                "TE 按 intervals 逐对取值）；原始序列或输入变化须重新冻结（新代）",
    }
    if n < min_common:
        base.update(status="insufficient",
                    note=f"可用相邻有效区间不足（{n} < {min_common}）；停止确认性 "
                         f"TD/TE，样本 {n} 个日收益、窗口 {window}"
                         + (f"（{skipped} 个区间因缺值剔除）" if skipped else ""))
        return base
    try:
        dev = compute_tracking_metrics(
            rf, rb, fund_return_type="total_return", bench_return_type="price_return",
            fund_currency="CNY", bench_currency="CNY", window_returns=window_returns,
            frequency="daily",
            annualization_factor=TRADING_DAYS_PER_YEAR, min_common=min_common)
    except TrackingCaliberError as exc:
        if "长度不等" in str(exc):
            # 对齐后仍不等长 = 实现缺陷：不得静默截短出数字（B-F03）
            base.update(status="caliber_error", note=f"两腿样本不等长（{exc}）：停止计算")
            return base
        # 类型不一致（当前唯一真实形态）：确认指标置 None，偏离只作研究数值
        # R17：研究数值同样拒绝空/NaN/不等长（统一校验），拒绝时按 caliber_error
        # 披露——不得让异常穿透或被静默吞掉
        try:
            raw = _tracking_deviation_raw(rf, rb, window_returns=window_returns)
        except TrackingCaliberError as raw_exc:
            base.update(status="caliber_error",
                        note=f"价格基准偏离不可算（{raw_exc}）：停止计算")
            return base
        base["price_benchmark_deviation"] = {
            **raw,
            "window_endpoints": base["input_evidence"]["window_endpoints"],
            "sampled_coverage": base["input_evidence"]["sampled_coverage"],
            "research_only": True,
            "not_confirmatory": True,
            "caliber": "基金总收益（复权净值）− 价格指数收益：收益类型不同，"
                       "仅作价格基准偏离研究数值，不构成跟踪差异/跟踪误差；"
                       "TD=窗口首末有效端点累计差，TE=采样区间日偏离波动",
        }
        base.update(
            status="limited",
            note="基准为价格指数（缺总收益基准）：停止确认性 TD/TE；"
                 "价格基准偏离见 price_benchmark_deviation（research_only）")
        return base
    base.update(status="available", **dev,
                note="两腿同收益类型（total_return）——确认性 TD/TE")
    return base


def _tracking_deviation_raw(returns_fund: list[float], returns_bench: list[float], *,
                            window_returns: tuple[float, float] | None = None,
                            annualization_factor: int = TRADING_DAYS_PER_YEAR) -> dict:
    """类型不一致时的研究数值（不作确认指标；仅内部使用）。

    B-F03：与 ``compute_tracking_metrics`` 同合同——拒绝不等长（不得截短配对）。
    R3：TD 同样只由**窗口端点收益**定义（缺端点报错，不得用样本区间复利代替）；
    TE 只覆盖传入样本区间（调用方在底稿披露覆盖度）。
    R16/R17：公式与校验统一走 :func:`_deviation_core`——年化系数不再硬编码
    √252（与确认路径同源），空样本/不等长/非有限值统一抛
    :class:`TrackingCaliberError`（旧实现空样本 ZeroDivisionError、NaN 静默出数）。
    """
    core = _deviation_core(returns_fund, returns_bench,
                           window_returns=window_returns,
                           annualization_factor=annualization_factor)
    return {**core, "td_scope": "window_endpoint", "te_scope": "sampled_intervals"}


# ---------------------------------------------------------------------------
# Fit 数据侧（B04）：集中风险 / 取证预算 / 外部材料清单
# ---------------------------------------------------------------------------

#: 持仓定向取证证据表字段（限定字段；由主流程按真实研究问题触发下钻后回填）
HOLDINGS_EVIDENCE_FIELDS = (
    "证券", "权重来源日期", "业务证据", "期间", "事件", "关联命题", "来源", "缺口",
)

#: 外部材料清单（按需取证的使用条件；不强制全部获取）
EXTERNAL_MATERIALS_CHECKLIST: list[dict] = [
    {"material": "基金合同/招募说明书（更新）", "use_for": "身份/费率/申赎规则/基准",
     "source_type": "管理人/交易所披露原文", "use_when": "费率或身份结论进入报告时"},
    {"material": "基金定期报告（季报/年报）", "use_for": "披露持仓/实际计费/规模",
     "source_type": "管理人披露原文", "use_when": "持仓或规模结论进入报告时"},
    {"material": "指数编制方案（含调样公告）", "use_for": "选样/加权/调样规则",
     "source_type": "指数公司原文", "use_when": "指数暴露/因子规则结论进入报告时"},
    {"material": "交易所公告", "use_for": "上市/做市/申赎状态等事实",
     "source_type": "交易所原文", "use_when": "交易机制结论进入报告时"},
    {"material": "PCF/申赎清单", "use_for": "篮子与现金替代", "source_type": "交易所/管理人",
     "use_when": "申赎机制问题必要且源可得时（未接入，不得伪造）"},
    {"material": "公司公告与财报", "use_for": "持仓个股业务/事件证据",
     "source_type": "公司/交易所原文", "use_when": "个别持仓为关键假设载体时（按问题定向）"},
]


def build_evidence_budget(symbol: str, *, target_cov_pct: float, max_names: int,
                          evidence: dict[str, dict] | None = None,
                          research_question: str | None = None,
                          holdings: dict | None = None) -> dict:
    """持仓定向取证预算 + 证据表（B04 数据侧）。

    **两个参数必填、无默认**：50%/最多 10 只只是待标定**试验参数**，不进默认
    报告轮询、不成为硬阈值；由真实研究问题决定取值。选择仅在**已披露范围**内
    按权重降序累计；覆盖不足目标 → `shortfall_note` 如实披露，不扩采、不把缺
    权重当 0、不重归一化。

    ``holdings``（可选）：已封存的持仓块（如 payload.holdings）——重放/冻结路径
    用它避免二次联网取持仓；缺省才走 ``query_etf_holdings``。

    ``evidence``（可选）：本次**实际读取**的定向证据，按证券代码/名称索引
    ``{code: {业务证据, 期间, 事件, 关联命题, 来源: {url, sha256, locator,
    read_at, source_type}, 缺口}}``。给证据的行必须有来源三件套
    （url+sha256+locator），否则整行标 `invalid_evidence`（URL 存在 ≠ 已读，
    不得用文件名/摘要冒充原文）。未给证据的行保持 `not_collected`（未执行 ≠
    完成，不得静默标完成）。证据行只回填预算内选中的证券——预算外不扩采。
    """
    if not (0 < target_cov_pct <= 100):
        raise ValueError("target_cov_pct 须在 (0, 100]")
    if max_names < 1:
        raise ValueError("max_names 须 ≥ 1")
    h = holdings if holdings is not None else query_etf_holdings(symbol)
    out = {
        "symbol": symbol,
        "holding_scope": h.get("holding_scope"),
        "status": h.get("status"),
        "research_question": research_question,
        "budget": {"target_cov_pct": target_cov_pct, "max_names": max_names,
                   "experimental_parameters": True,
                   "note": "试验参数（非行业标准/非本版硬阈值）；按真实研究问题调整"},
        "names": [], "evidence_fields": list(HOLDINGS_EVIDENCE_FIELDS),
        "evidence_rows": [], "evidence_summary": None,
        "shortfall_note": None, "gaps": [],
    }
    if h.get("status") != "ok":
        out["gaps"].append(h.get("note") or "持仓数据不可用")
        return out
    rows = [r for r in h.get("rows") or [] if r.get("pct") is not None]
    rows.sort(key=lambda r: r["pct"], reverse=True)
    selected: list[dict] = []
    cov = 0.0
    stop_reason = None
    for i, r in enumerate(rows):
        if len(selected) >= max_names:
            stop_reason = "max_names"
            break
        if cov >= target_cov_pct:
            stop_reason = "target_reached"
            break
        selected.append({"rank": i + 1, "code": r.get("code"), "name": r.get("name"),
                         "pct": r.get("pct")})
        cov += float(r["pct"])
    else:
        stop_reason = "target_reached" if cov >= target_cov_pct else "disclosed_exhausted"
    out["names"] = selected
    out["budget"]["selected_n"] = len(selected)
    out["budget"]["selected_cov_pct"] = round(cov, 2)
    out["budget"]["stop_reason"] = stop_reason
    out["budget"]["disclosed_sum_pct"] = h.get("top10_sum_pct")
    evidence = evidence or {}
    ev_rows: list[dict] = []
    row_has_evidence: list[bool] = []  # R18：证据有效性按行索引记录，不靠列表等值匹配
    for n in selected:
        code = str(n.get("code") or "")
        ev = evidence.get(code) or {}
        row = {**{k: None for k in HOLDINGS_EVIDENCE_FIELDS},
               "证券": f"{n['name']}（{n['code']}）", "权重来源日期": h.get("report_date")}
        valid = False
        if not ev:
            row["缺口"] = "未取证（预算内待查）"
        else:
            for k in ("业务证据", "期间", "事件", "关联命题", "缺口"):
                if ev.get(k) is not None:
                    row[k] = ev[k]
            src = ev.get("来源") or {}
            missing = [k for k in ("url", "sha256", "locator") if not src.get(k)]
            if missing:
                row["来源"] = src or None
                row["缺口"] = (f"证据来源不完整（缺 {'/'.join(missing)}）："
                               "URL 存在 ≠ 已读，不按已取证处理")
            else:
                row["来源"] = dict(src)
                valid = True
        ev_rows.append(row)
        row_has_evidence.append(valid)
    out["evidence_rows"] = ev_rows
    n_with_ev = sum(1 for v in row_has_evidence if v)
    # 覆盖加权按**同一索引**取选中权重（旧实现逐行做 dict 等值匹配，同权重不同
    # 证据行的两行会被同事匹配，属 O(n²) 且语义偶然）
    cov_with_ev = round(sum(float(n["pct"]) for n, ok in zip(selected, row_has_evidence)
                            if ok), 2)
    out["evidence_summary"] = {
        "selected_n": len(selected),
        "evidence_rows": n_with_ev,
        "not_collected_rows": len(selected) - n_with_ev,
        "evidence_weight_cov_pct": cov_with_ev,
        "note": "覆盖加权 = 已有原文证据的选中证券权重合计（仅已披露范围内）；"
                "未取证行如实保留 not_collected（未执行 ≠ 完成）",
    }
    if cov < target_cov_pct:
        # R04：缺口原因按**实际停止原因**分派——旧实现只要 top10_sum_pct 非空
        # 就说「已披露范围不足以覆盖」，max_names 截断（披露 55% > 目标 50%、
        # 实际只选到 27%）也被这句话误述；max_names 分支因此不可达。
        disclosed = h.get("top10_sum_pct")
        if stop_reason == "max_names":
            why = (f"预算内选择上限（max_names={max_names}）先到："
                   f"已选 {len(selected)} 只累计 {round(cov, 2)}%"
                   + (f"；已披露范围合计 {disclosed}%（是否足够覆盖目标需按需扩选，"
                      "本次不扩采）" if disclosed is not None else ""))
        elif stop_reason == "disclosed_exhausted" and disclosed is not None:
            why = (f"已披露范围（前十大合计 {disclosed}%）不足以覆盖目标 "
                   f"{target_cov_pct}%：实际覆盖 {round(cov, 2)}%，缺口如实披露，"
                   "不扩大采集")
        else:
            why = (f"已披露范围累计不足（{round(cov, 2)}% < 目标 {target_cov_pct}%；"
                   "权重缺失不按 0、不重归一化）")
        out["shortfall_note"] = why
    missing_rows = [r for r in h.get("rows") or [] if r.get("pct") is None]
    for r in missing_rows:
        out["gaps"].append(f"{r.get('name') or r.get('code')} 权重缺失（不按 0 计、不重归一化）")
    return out


# ---------------------------------------------------------------------------
# 数据侧状态合同（B06 基础）：五态 + 实际尝试源
# ---------------------------------------------------------------------------

#: 维度状态词表（未执行/失败/不适用/未知/可得 五态分开）
DIMENSION_STATES = ("available", "missing", "not_applicable", "not_executed", "unknown")


def _dim(status: str, *, sources: list[str] | None = None, as_of: str | None = None,
         note: str | None = None, source_record: dict | None = None,
         applicable: bool | None = None, skipped: list[dict] | None = None,
         producer_status: str | None = None) -> dict:
    assert status in DIMENSION_STATES, status
    d = {"status": status, "attempted_sources": list(sources or []),
         "as_of": as_of, "note": note}
    if applicable is not None:
        d["applicable"] = applicable
    if source_record is not None:
        d["source_record"] = dict(source_record)
    if skipped:
        # 未发起的腿/源及原因（B-F01）：提前返回不得表现为「已尝试」，
        # 也不得静默——消费方与 QC 可据此区分「试了失败」与「没试」。
        d["skipped_sources"] = [dict(s) for s in skipped]
    if producer_status is not None:
        # 生产者原始状态（如 tracking=limited）：五态之外的口径细节不丢失
        d["producer_status"] = producer_status
    return d


def _range_end_iso(value: Any) -> str | None:
    """区间字符串（``A ~ B``）→ 末端日期 ISO（R02）。

    旧实现取 ``[-10:]``：ISO 区间侥幸可用，compact 区间（如 Tushare
    trade_date "20260904 ~ 20261009"）会得到 "~ 20261009" 脏 as_of。
    解析不了返回 None（不猜）。
    """
    s = str(value or "").strip()
    if not s:
        return None
    tail = s.split("~")[-1].strip() if "~" in s else s
    return _norm_trade_date(tail)


def _holdings_dim_from_evidence(hd: dict) -> dict:
    """holdings 实采证据 → 维度五态（R10：证据照实登记，不看前置适用性）。

    - ``status="ok"``（实采到，含 report_date）→ available；
    - 其余提交记录（missing/失败占位）→ missing：attempted/skipped 按
      producer 留痕透传（有 attempts 不得清空；无留痕才用静态渠道标注）。
    本函数只映射证据；适用性（applicable/note 口径）由调用方叠加。
    """
    h_attempted = hd.get("attempted_sources")
    sources = (list(h_attempted) if h_attempted is not None
               else ["天天基金 jjcc 页（渠道页面，非原披露）"])
    if hd.get("status") == "ok":
        return _dim("available", sources=sources,
                    as_of=hd.get("report_date"),
                    note=(hd.get("holding_scope") or "基金披露前十大（非完整持仓）"))
    return _dim("missing", sources=sources,
                note=hd.get("note") or "持仓不可得",
                skipped=(hd.get("skipped_sources") or None))


def _index_exposure_dim_from_evidence(ie: dict) -> dict:
    """index_exposure 实采证据 → 维度五态（R10 同款：提交即按实况如实映射）。

    与 holdings 同属「按需接口」：未提交不在此函数（由调用方记未执行/
    未知/不适用），提交了就不能被适用性前置遮蔽。
    """
    if ie.get("status") == "ok":
        pa = ie.get("period_alignment") or {}
        note = (f"cons {pa.get('cons_as_of')} / weight {pa.get('weight_as_of')}；"
                + ("同日可拼组合" if pa.get("joinable") else
                   "错月只可分别陈述，不得拼成「当期完整组合」"))
        return _dim("available",
                    sources=["csindex 官方成分/权重文件（经 akshare）"],
                    as_of=pa.get("weight_as_of") or pa.get("cons_as_of"),
                    note=note)
    return _dim("missing", sources=["csindex 官方成分/权重文件（经 akshare）"],
                note=(ie.get("note")
                      or f"指数样本/权重不可得（cons={ie.get('cons_status')}，"
                         f"weight={ie.get('weight_status')}）"))


def build_data_status(*, identity: dict | None, profile: dict, quote: dict,
                      kline: dict, share_history: dict,
                      history_block: dict | None = None,
                      events_block: dict | None = None,
                      tracking: dict | None = None,
                      fees: dict | None = None,
                      est_net_assets: dict | None = None,
                      extra_dims: dict | None = None,
                      index_exposure: dict | None = None) -> dict:
    """各维度状态（B06 基础）：五态 + **实际尝试源** + 源可读性记录。

    attempted_sources 只从本次实际取数证据推导（信封 source/_error/status，
    或上游 producer 记录的 attempted_sources——见 B-F01），不得由静态
    SOURCE_PROVENANCE 合成尝试记录；未执行的维度记
    `not_executed` 且 attempted_sources=[]。flags 不代表检查执行，
    `note` 中显式声明。

    ``extra_dims``：payload-out 流程实采的 holdings/peers/sector_flow
    （实采到就必须按实况记 available/missing，不得固定 not_executed；
    R10：身份未核时 ``stock_holdings_penetration=False`` 只是默认值——
    holdings/index_exposure 的实采记录同样不得被吞掉）；
    ``index_exposure``：显式按需查询的指数样本/权重块。
    """
    identity = identity or {}
    app = identity.get("applicability") or {}
    ver_status = (identity.get("verification") or {}).get("status")
    # 身份未核：适用性未知（≠ 确定不适用）——相关维度记 unknown 而非 not_applicable
    identity_unknown = ver_status in (None, "unknown") or \
        (identity.get("axes") or {}).get("asset_type") in (None, "unknown")
    na_state = "unknown" if identity_unknown else "not_applicable"
    dims: dict[str, dict] = {}

    # identity
    if ver_status == "verified_original":
        dims["identity"] = _dim(
            "available", sources=["交易所/管理人原文（见 evidence URL）"],
            as_of=(identity.get("verification") or {}).get("verified_at"),
            note=f"原文核验 {len(identity.get('evidence') or [])} 条")
    elif ver_status == "mapped_unverified":
        dims["identity"] = _dim("available", sources=[], as_of=None,
                                note="静态映射（待核）：使用须注明未核验")
    else:
        dims["identity"] = _dim("unknown", sources=[], as_of=None,
                                note="无身份证据；不适用维度不可按名称强填")

    # index_pe
    pe_status = profile.get("index_pe_status")
    if app.get("index_pe") is False:
        dims["index_pe"] = _dim(
            na_state, applicable=False,
            note="身份未核：适用性未知（不套股票 PE）" if identity_unknown
            else "非股票类：不套股票 PE")
    elif profile.get("index_pe") is not None:
        dims["index_pe"] = _dim("available", sources=["csindex 指数 PE 文件"],
                                as_of=None,
                                note=f"口径: {profile.get('index_pe_caliber')}；"
                                     f"{profile.get('index_pe_note') or ''}",
                                source_record=SOURCE_PROVENANCE["etf_index_pe"])
    elif pe_status == "mapped":
        # R05 同类：桥/getter 不可用时 csindex 源未发起 → 不得记「已尝试」
        if profile.get("index_pe_fetch_status") == "not_initiated":
            dims["index_pe"] = _dim(
                "unknown", sources=[], producer_status="not_initiated",
                note="指数 PE 未发起取数（桥/getter 不可用）",
                skipped=[{"source": "csindex 指数 PE 文件",
                          "reason": profile.get("index_pe_fetch_skip_reason")
                          or "未发起取数"}])
        else:
            dims["index_pe"] = _dim("missing", sources=["csindex 指数 PE 文件"],
                                    note="已映射但源未取到（信封 missing）")
    else:
        # R12：措辞只陈述接线状态；profile 侧注记（区分 PE 文件映射与跟踪身份）
        # 优先，兼容旧 profile 无 index_pe_note 时用通用措辞
        dims["index_pe"] = _dim("unknown", sources=[],
                                note=(profile.get("index_pe_note")
                                      or "无 csindex 映射：指数 PE 未接线（不猜测）"))

    # quote / premium
    # R07/R08 + F3（独立复检）：来源只认 producer 结构化标记 `quote.source`。
    # 两种"无来源信息"形态同判未知：①该键缺失（旧 payload，无追溯）；
    # ②显式 `source=None`（新 payload 两源均失败/未留痕）。二者都 **不** 按
    # `_error` 文本推断腾讯回退、也不制造已尝试源（"回退"二字可能是别的意思
    # 或别的源）——note 的措辞按"source 为空或缺失"统一陈述，不假定 payload 新旧。
    q_err = quote.get("_error") or ""
    q_source = quote.get("source")
    q_trace_known = "source" in quote
    if not q_trace_known:
        q_source = None
    if quote.get("status") == "available":
        if q_source == "tencent":
            dims["quote"] = _dim("available", sources=["fund_etf_spot_em", "腾讯行情"],
                                 note=q_err)
        elif q_source == "fund_etf_spot_em":
            dims["quote"] = _dim("available", sources=["fund_etf_spot_em"],
                                 note=q_err or None)
        else:
            dims["quote"] = _dim(
                "available", sources=[],
                note=(q_err or "") + "；来源未留痕（source 为空或缺失）——"
                                     "已尝试源未知，不得按文案推断")
    else:
        dims["quote"] = _dim("missing", sources=["fund_etf_spot_em", "腾讯行情"],
                             note=q_err or "spot 与回退源均不可用")
    if profile.get("premium_discount") is not None:
        dims["premium_discount"] = _dim(
            "available", sources=["fund_etf_spot_em 基金折价率"],
            note="符号已规范化（+溢价/-折价）；净值时点未知（口径见 premium_discount_caliber）")
    elif quote.get("status") == "available":
        if q_source == "tencent":
            dims["premium_discount"] = _dim(
                "missing", sources=["fund_etf_spot_em"],
                note="spot 源不可用（回退腾讯行情）：折溢价字段不可得")
        elif q_source == "fund_etf_spot_em":
            # R08：spot 正常但折价率字段缺失 ≠ 发生过腾讯回退——旧文案写死回退
            dims["premium_discount"] = _dim(
                "missing", sources=["fund_etf_spot_em"],
                note="spot 行无基金折价率字段（本次未发生腾讯回退）：折溢价不可得")
        else:
            dims["premium_discount"] = _dim(
                "missing", sources=[],
                note="行情来源未留痕（source 为空或缺失）：折溢价不可得，"
                     "已尝试源未知，不得断言来自 spot 或腾讯回退")
    else:
        dims["premium_discount"] = _dim("missing", sources=["fund_etf_spot_em"],
                                        note="spot 不可用")

    # nav / history
    if kline.get("status") == "available":
        dims["nav"] = _dim("available",
                           sources=[str(kline.get("source") or "fund_etf_fund_info_em")],
                           as_of=kline.get("latest_nav_date"),
                           note="复权" if kline.get("adj_applied") else "未复权",
                           source_record=SOURCE_PROVENANCE["etf_nav"])
    else:
        # R05/R06 同类：未发起取数时 attempted 记空 + skipped 带原因；
        # 生产者无留痕字段（旧 payload）才回退静态源名
        k_src = kline.get("attempted_sources")
        dims["nav"] = _dim(
            "missing",
            sources=(list(k_src) if k_src is not None else ["fund_etf_fund_info_em"]),
            note=kline.get("_error"),
            skipped=(kline.get("skipped_sources") or None),
            producer_status=kline.get("status"))
    h = history_block or {}
    hist = h.get("history") or {}
    if hist.get("status") == "available":
        src = str(hist.get("source") or "nav")
        h_src = hist.get("attempted_sources")
        dims["history"] = _dim("available",
                               sources=(list(h_src) if h_src is not None else
                                        (["nav:fund_etf_fund_info_em"] if src == "nav"
                                         else [src])),
                               as_of=(hist.get("rows") or [{}])[-1].get("date"),
                               note=hist.get("note"),
                               skipped=(hist.get("skipped_sources") or None))
    elif hist:
        h_src = hist.get("attempted_sources")
        dims["history"] = _dim(
            "missing",
            sources=(list(h_src) if h_src is not None
                     else ["nav:fund_etf_fund_info_em", "baostock"]),
            note=hist.get("error"),
            skipped=(hist.get("skipped_sources") or None),
            producer_status=hist.get("status"))
    else:
        dims["history"] = _dim("not_executed", sources=[],
                               note="本次未请求历史深度（需 --history/--playbook）")

    # share_history
    if share_history.get("available"):
        dims["share_history"] = _dim("available", sources=["Tushare fund_share", "Tushare fund_daily"],
                                     # R02：date_range 为 compact 日期（"20260904 ~ 20261009"），
                                     # 旧实现 [-10:] 产出 "~ 20261009" 脏 as_of
                                     as_of=_range_end_iso(share_history.get("date_range")),
                                     note=share_history.get("caliber_note"),
                                     source_record=SOURCE_PROVENANCE["etf_share_history"])
    else:
        # R06 同类：producer 已给结构化尝试/跳过时按实况记（无 token = 未发起）
        sh_src = share_history.get("attempted_sources")
        dims["share_history"] = _dim(
            "missing",
            sources=(list(sh_src) if sh_src is not None
                     else ["Tushare fund_share", "Tushare fund_daily"]),
            note=share_history.get("note"),
            skipped=(share_history.get("skipped_sources") or None))

    # est net assets（份额×NAV 同日匹配）
    if est_net_assets is not None:
        # R06：空列表 = producer 明确说「未发起」——不得被 `or 默认源表` 顶替
        # （falsy 空集合与「字段缺失」必须分开处理）
        ena_src_raw = est_net_assets.get("attempted_sources")
        ena_src = (["Tushare fund_share", "fund_etf_fund_info_em（净值）"]
                   if ena_src_raw is None else list(ena_src_raw))
        ena_skip = est_net_assets.get("skipped_sources") or None
        if est_net_assets.get("status") == "ok":
            dims["est_net_assets"] = _dim("available", sources=ena_src,
                                          as_of=est_net_assets.get("as_of"),
                                          note=est_net_assets.get("caliber"))
        else:
            dims["est_net_assets"] = _dim("missing", sources=ena_src,
                                          note=est_net_assets.get("note"),
                                          skipped=ena_skip)

    # fees（R3：current_rate_verified=报告日现行已核；版本已读用 source 元信息，
    # 状态注记复用 query_etf_fees 的 note，不再有第二套现行性布尔）
    if fees and fees.get("status") == "ok":
        dims["fees"] = _dim(
            "available",
            sources=[(fees.get("source") or {}).get("source_type") or "管理人原文"],
            as_of=(fees.get("source") or {}).get("as_of"),
            note=fees.get("note"))
    else:
        dims["fees"] = _dim("unknown", sources=[],
                            note=(fees or {}).get("note") or "未收录费率材料")

    # tracking（B-F01：attempted_sources 只取自 producer 实际调用留痕；
    # source_trace 保留缓存/网络与失败理由，逐条沿革不猜）
    if tracking is not None:
        tstatus = tracking.get("status")
        t_src = list(tracking.get("attempted_sources") or [])
        t_skip = tracking.get("skipped_sources") or None
        t_trace = list(tracking.get("source_trace") or [])
        # R01/F2（独立复检）：五态按 producer **实际出口**分派，不用状态名一刀切。
        #   - available：两腿同口径，确认性 TD/TE 已算；
        #   - limited + price_benchmark_deviation：两腿已采集、口径受限，
        #     研究偏离已算出 → available（已采集，producer_status=limited）；
        #   - limited 但无偏离（基金腿未复权提前返回，基准没取）→ unknown：
        #     已采集基金腿但**不可验证**，不得声称 tracking 可得；
        #   - insufficient/caliber_error：序列已取但样本/口径不可算 → unknown；
        #   - not_mapped：未知保持未知；missing_nav/missing_benchmark → missing（源失败）。
        deviation = tracking.get("price_benchmark_deviation")
        disclosed_cmp = tracking.get("official_benchmark_comparison")
        if tstatus == "available" or (tstatus == "limited" and deviation):
            dims["tracking"] = _dim("available", sources=t_src,
                                    as_of=_range_end_iso(tracking.get("window")),
                                    note=tracking.get("note"), skipped=t_skip,
                                    producer_status=tstatus)
        elif tstatus == "limited" and disclosed_cmp:
            # A6：原文披露的基金 vs 业绩比较基准对比已登记（官方口径，基准含汇率
            # 调整）——该维度有可用证据（available）；两腿自算受限（producer_status
            # 保留 limited），不得因「跨币种不确认 TD/TE」而声称 tracking 未采集。
            dims["tracking"] = _dim(
                "available",
                sources=[(disclosed_cmp.get("source") or {}).get("source_type")
                         or "基金定期报告（§3.2.1）"],
                as_of=(disclosed_cmp.get("as_of") or "")[:10] or None,
                note=("原文披露对比（官方口径）："
                      + (disclosed_cmp.get("note") or tracking.get("note") or "")),
                skipped=t_skip, producer_status=tstatus)
        elif tstatus in ("limited", "insufficient", "caliber_error"):
            dims["tracking"] = _dim(
                "unknown", sources=t_src,
                note=tracking.get("note"), skipped=t_skip,
                producer_status=tstatus)
        elif tstatus == "not_mapped":
            dims["tracking"] = _dim(
                "unknown", sources=t_src,
                note=(f"无跟踪指数代码（{tracking.get('index_code_source') or '映射缺失'}）；"
                      "无法构建基准腿"), skipped=t_skip)
        else:
            dims["tracking"] = _dim("missing", sources=t_src,
                                    note=tracking.get("note"), skipped=t_skip,
                                    producer_status=tstatus)
        if t_trace:
            dims["tracking"]["source_trace"] = t_trace

    # holdings / pcf / index_exposure（默认不执行；按需命令/实采触发）
    hd = (extra_dims or {}).get("holdings") or {}
    ie = index_exposure or {}
    if app.get("stock_holdings_penetration") is False:
        # R10（2026-10-11 复查）：该分支的 False 含两种情况——已知非股票
        # （确定不适用）与**身份未核的默认值**（适用性未知）。身份未核时提交了
        # holdings/index_exposure 实采证据，必须照实登记（available/missing +
        # 实际尝试源 + 日期），不得因适用性未确认而写成「未请求/未采集」；
        # 适用性仍不确认（applicable 保持 False，note 显式声明不据此定身份）。
        # 已知非股票不因单次提交放宽资产闸门（absent request 仍是 not_applicable；
        # index_pe 等维度各有自身分支，不在此联动）。
        na_note = ("身份未核：适用性未知" if identity_unknown
                   else "非股票类：不适用股票持仓穿透")
        if identity_unknown and hd:
            dims["holdings"] = _holdings_dim_from_evidence(hd)
            dims["holdings"]["applicable"] = False
            caveat = "身份未核：适用性未知（实采记录照实登记，不据此确认身份或适用性）"
            base_note = dims["holdings"].get("note")
            dims["holdings"]["note"] = f"{base_note}；{caveat}" if base_note else caveat
        else:
            dims["holdings"] = _dim(na_state, applicable=False, note=na_note)
        if identity_unknown and ie:
            dims["index_exposure"] = _index_exposure_dim_from_evidence(ie)
            dims["index_exposure"]["applicable"] = False
            caveat = "身份未核：适用性未知（实采记录照实登记，不据此确认身份或适用性）"
            base_note = dims["index_exposure"].get("note")
            dims["index_exposure"]["note"] = f"{base_note}；{caveat}" if base_note else caveat
        else:
            dims["index_exposure"] = _dim(na_state, applicable=False,
                                          note="身份未核：适用性未知" if identity_unknown
                                          else "非股票类：指数暴露按该资产口径另核")
    else:
        if hd:
            # 实采到就按实况记录（不得固定 not_executed 冒充未采）
            dims["holdings"] = _holdings_dim_from_evidence(hd)
        else:
            dims["holdings"] = _dim("not_executed", sources=[],
                                    note="本次未请求持仓（按需 etf.py holdings / B04 触发）")
        if ie:
            dims["index_exposure"] = _index_exposure_dim_from_evidence(ie)
        else:
            dims["index_exposure"] = _dim(
                "not_executed", sources=[],
                note="指数样本/权重为按需接口（query_etf_index_exposure），默认不接入报告主链")
    # B04/B02（F4 独立复检）：这两个维度**独立于股票持仓适用性分支**，
    # 维度面在资产类型间稳定，且实采结果不被非股票前置覆盖：
    # - same_index_comparison 的生产者（etf_peers.same_index_quality_comparison）
    #   无股票前置——可比性由「同指数身份证据 + 币种 + 复权净值」决定，商品 ETF
    #   同样可比较 → 未请求=not_executed，提交即按实况映射（不看 asset_type）；
    # - holdings_evidence 的取证对象是「持仓个股的公司公告/财报」，非股票资产
    #   无此口径 → 未提交时 not_applicable（依据见 note），提交了则按实况。
    he = (extra_dims or {}).get("holdings_evidence") or {}
    if he.get("evidence_summary"):
        es = he["evidence_summary"]
        dims["holdings_evidence"] = _dim(
            "available" if es.get("evidence_rows") else "missing",
            sources=[f"公司/交易所原文（{es.get('evidence_rows')} 行已读；"
                     f"{es.get('not_collected_rows')} 行未取证）"],
            as_of=(he.get("evidence_rows") or [{}])[0].get("权重来源日期"),
            note=(he.get("research_question") or
                  "按研究问题定向取证（B04；预算内、不轮询全成分）"))
    elif he:
        dims["holdings_evidence"] = _dim("missing", sources=[],
                                         note=he.get("note") or "定向证据不可得")
    elif app.get("stock_holdings_penetration") is False:
        dims["holdings_evidence"] = _dim(
            na_state, applicable=False,
            note=("身份未核：适用性未知" if identity_unknown
                  else "非股票类：定向取证对象为持仓个股的公司公告/财报（股票业务证据），"
                       "该资产口径下不适用"))
    else:
        dims["holdings_evidence"] = _dim(
            "not_executed", sources=[],
            note="本次未执行定向持仓取证（按研究问题触发；未执行 ≠ 完成）")
    sic = (extra_dims or {}).get("same_index_comparison") or {}
    if sic:
        wins = (sic.get("common_window") or {})
        dims["same_index_comparison"] = _dim(
            "available" if sic.get("status") == "ok" else "missing",
            sources=["官方产品材料（同指数身份/费率）+ 复权净值（既有接口）"],
            as_of=wins.get("end"),
            note=(f"同指数 {sic.get('index_code')} 有限比较（不排名）；"
                  f"{(sic.get('gaps') or ['无明确缺口'])[0]}"))
    else:
        dims["same_index_comparison"] = _dim(
            "not_executed", sources=[],
            note="本次未执行同指数产品比较（按需 --same-index + 官方材料证据；"
                 "未执行 ≠ 完成）")
    dims["pcf"] = _dim("not_executed", sources=[],
                       note="PCF 通道未接入（不新增强制付费源；不得伪造篮子）")

    # industry allocation / industry pe / events / hedge
    if profile.get("industry_allocation"):
        dims["industry_allocation"] = _dim(
            "available", sources=["fund_portfolio_industry_allocation_em"],
            as_of=profile.get("industry_allocation_date"),
            source_record=SOURCE_PROVENANCE["etf_industry_alloc"])
    elif app.get("index_pe") is True or identity.get("axes", {}).get("asset_type") == "stock":
        if profile.get("industry_allocation_fetch_status") == "not_initiated":
            # R05 同类：桥/getter 不可用 = 未发起（不得静态记已尝试源）
            dims["industry_allocation"] = _dim(
                "unknown", sources=[], producer_status="not_initiated",
                note="行业配置未发起取数（桥/getter 不可用）",
                skipped=[{"source": "fund_portfolio_industry_allocation_em",
                          "reason": profile.get("industry_allocation_skip_reason")
                          or "未发起取数"}])
        else:
            dims["industry_allocation"] = _dim(
                "missing", sources=["fund_portfolio_industry_allocation_em"],
                note="行业配置不可得（接口空/失败）")
    else:
        dims["industry_allocation"] = _dim(na_state, applicable=False,
                                           note="身份未核：适用性未知" if identity_unknown
                                           else "非股票类：行业配置不适用")
    # R09：行业 PE 状态按 producer 留痕（available/skipped_index_pe_present/
    # source_null/query_error/no_industry_mapping），不再把「已有指数 PE 而未取」
    # 写成「无行业映射或未请求」
    ip_status = profile.get("industry_pe_status")
    if profile.get("industry_pe") is not None:
        dims["industry_pe"] = _dim("available", sources=["industry_weekly（申万行业 PE 快照）"],
                                   note=profile.get("industry_pe_note"),
                                   source_record=SOURCE_PROVENANCE["industry_weekly"])
    elif ip_status == "skipped_index_pe_present":
        dims["industry_pe"] = _dim(
            "not_executed", sources=[],
            note=profile.get("industry_pe_note")
            or "已有 csindex 指数 PE：行业代理 PE 本次未取（避免口径混用）")
    elif ip_status == "source_null":
        dims["industry_pe"] = _dim(
            "missing", sources=["industry_weekly（申万行业 PE 快照）"],
            note=profile.get("industry_pe_note") or "行业 PE 源侧为空")
    elif ip_status == "query_error":
        dims["industry_pe"] = _dim(
            "missing", sources=["industry_weekly（申万行业 PE 快照）"],
            note="行业 PE 查询失败（本地 industry_weekly 不可用）")
    else:
        dims["industry_pe"] = _dim(
            "not_applicable" if identity.get("axes", {}).get("strategy") == "broad_market"
            else "not_executed", sources=[],
            note="无行业映射或未请求（行业 PE 为代理值，仅按需）")
    ev = events_block or {}
    if ev.get("available"):
        dims["events"] = _dim("available", sources=["events/{symbol}.json（本地事件文件）"],
                              note="事件-价格对齐依赖历史深度；"
                                   "本地事件文件当前**无自动写入方**（生产者缺口）——"
                                   "时效/过期未自动核验，引用前须人工确认")
    else:
        dims["events"] = _dim("not_executed", sources=[],
                              note=(ev.get("note") or "无事件文件（不阻断）")
                                   + "；事件写入方未接入（生产者缺口），"
                                     "不得据此称事件时效已核")
    hc = profile.get("hedge_coverage") or {}
    cov = hc.get("coverage")
    dims["hedge"] = _dim(
        "available" if cov not in (None, "unknown") else "unknown",
        sources=["ETF_HEDGE_MAP（静态映射）"],
        note=(hc.get("verification") or {}).get("note", "静态映射待核"))

    return {
        "dimensions": dims,
        "states_vocab": list(DIMENSION_STATES),
        "note": "flags 为提示标签，不代表对应检查已执行；以本块状态为准。"
                "attempted_sources 记录本次实际尝试的源。",
    }

# baostock 取数窗口自然日缓冲（节假日/周末），与 query_etf_kline 的
# int(days * 365 / 250) + 15 口径对齐
_BAOSTOCK_LOOKBACK_BUFFER = 15


def baostock_code(symbol: str) -> str:
    """ETF 代码 → baostock 证券代码（sh./sz. 前缀路由）。

    交易所解析规则：51/56/58 开头 → 上交所（sh.{symbol}）；
    15/16/18 开头 → 深交所（sz.{symbol}）；其余抛 ValueError。
    """
    if symbol.startswith(("51", "56", "58")):
        return f"sh.{symbol}"
    if symbol.startswith(("15", "16", "18")):
        return f"sz.{symbol}"
    raise ValueError(
        f"无法映射交易所: {symbol!r}（ETF 代码须 51/56/58=沪 或 15/16/18=深）"
    )


def fetch_etf_kline_baostock(symbol: str, days: int = 250) -> dict:
    """原始取数：baostock 日线（sh.588000 格式，R11a 备源）。

    baostock 为一次性全局登录：bs.login() 后同一进程后续调用直接返回成功；
    本函数异常安全（try/finally 内 logout）。失败返回 status="missing"
    （不抛异常，与其它 fetch_* 信封一致），供查询侧降级。

    rows 归一化为 [{date, open, close, high, low, volume, change_pct}]，
    与现有 kline 结构对齐（nav 链路的 nav_history 为 {date, nav, change_pct}；
    消费方 compute_history_stats 同时兼容 nav/close 键）。
    """
    code: str | None = None
    try:
        code = baostock_code(symbol)
        import baostock as bs

        login = bs.login()
        try:
            if login is None or login.error_code != "0":
                return {"status": "missing", "source": "baostock", "code": code,
                        "rows": [], "error": "baostock login failed"
                        + (f": {login.error_msg}" if login is not None else "（无返回）")}
            # baostock 要求 YYYY-MM-DD；shanghai_days_ago/shanghai_today 返回 YYYYMMDD
            def _iso(yyyymmdd: str) -> str:
                return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
            end_date = _iso(shanghai_today())
            start_date = _iso(shanghai_days_ago(int(days * 365 / 250) + _BAOSTOCK_LOOKBACK_BUFFER))
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",  # 前复权：与全仓统一复权语义（_sources.py / gap-scan 同款）
                # 不复权（3）会使除息日出现假 ≥5% 大波动/假最大回撤（review #7）
            )
            if rs.error_code != "0":
                return {"status": "missing", "source": "baostock", "code": code,
                        "rows": [], "error": f"baostock query failed: {rs.error_msg}"}
            raw = []
            while rs.next():
                raw.append(rs.get_row_data())
        finally:
            bs.logout()

        rows = []
        prev_close: float | None = None
        for r in raw:
            close = safe_float(r[4]) if len(r) > 4 else None
            if close is None:
                continue
            chg = None
            if prev_close is not None and prev_close > 0:
                chg = round((close / prev_close - 1) * 100, 2)
            prev_close = close
            rows.append({
                "date": str(r[0])[:10] if len(r) > 0 else "",
                "open": safe_float(r[1]) if len(r) > 1 else None,
                "close": close,
                "high": safe_float(r[2]) if len(r) > 2 else None,
                "low": safe_float(r[3]) if len(r) > 3 else None,
                "volume": safe_float(r[5]) if len(r) > 5 else None,
                "change_pct": chg,
            })
        if not rows:
            return {"status": "missing", "source": "baostock", "code": code,
                    "rows": [], "error": "baostock: 无有效 K 线行"}
        return {"status": "ok", "source": "baostock", "code": code,
                "rows": rows, "error": None}
    except Exception as exc:
        logger.warning("fetch_etf_kline_baostock(%s) failed: %s", symbol, exc)
        return {"status": "missing", "source": "baostock", "code": code,
                "rows": [], "error": str(exc)}


def query_etf_kline_history(symbol: str, days: int = 250) -> dict[str, Any]:
    """ETF 历史行情深度（R11a）：nav 链路优先，失败/异常自动回退 baostock。

    返回 dict 附 source 字段标注数据链路（"nav" / "baostock"）；
    两条链路均不可用时 status="missing"（不抛异常）。

    Returns
    -------
    dict
        {symbol, source, status, rows, requested_days, note, error?}
        rows 为 nav_history（{date, nav, change_pct}）或 baostock
        K 线行（{date, open, close, high, low, volume, change_pct}）。
    """
    nav_kline = None
    try:
        nav_kline = query_etf_kline(symbol, days=days)
    except Exception as exc:
        logger.warning(
            "etf_kline_history(%s): nav chain raised %r, falling back to baostock",
            symbol, exc,
        )
    nav_rows = (nav_kline or {}).get("nav_history") or []
    # R05/R06 同类（D11）：真实尝试/跳过留痕随块透传，data_status 不再静态补源
    nav_attempted = [str(s) for s in (nav_kline or {}).get("attempted_sources") or []]
    nav_skipped = [dict(s) for s in (nav_kline or {}).get("skipped_sources") or []]
    if nav_kline is not None and nav_kline.get("status") == "available" and nav_rows:
        return {
            "symbol": symbol,
            "source": "nav",
            "status": "available",
            "rows": nav_rows,
            "requested_days": days,
            "note": "来源: 基金单位净值（fund_etf_fund_info_em，Tushare 复权）",
            "attempted_sources": nav_attempted,
            "skipped_sources": nav_skipped,
        }

    env = fetch_etf_kline_baostock(symbol, days=days)
    if env.get("status") == "ok" and env.get("rows"):
        return {
            "symbol": symbol,
            "source": "baostock",
            "status": "available",
            "rows": env["rows"],
            "requested_days": days,
            "note": "来源: baostock 日线（前复权 adjustflag=2）",
            # nav 腿已发起但失败 → 留在 attempted；baostock 为本次真实回退
            "attempted_sources": nav_attempted + ["baostock 日线（前复权）"],
            "skipped_sources": nav_skipped,
        }
    return {
        "symbol": symbol,
        "source": "none",
        "status": "missing",
        "rows": [],
        "requested_days": days,
        "error": (env.get("error") or (nav_kline or {}).get("_error")
                  or "nav 与 baostock 均不可用"),
        "attempted_sources": nav_attempted + ["baostock 日线（前复权）"],
        "skipped_sources": nav_skipped,
    }


def compute_history_stats(nav_history: list[dict]) -> dict[str, Any]:
    """历史统计（引擎计算，AI 不得心算；R11a）。

    输入 nav_history（nav 链路 {date, nav, change_pct}）或 baostock K 线行
    （{date, open, close, high, low, volume, change_pct}），两种键均可。

    统计项（全部由引擎计算）：
    - annual_high / annual_low：年度最高/最低收盘价 + 各自日期
    - max_drawdown：最大回撤峰值/谷底 + 日期 + 幅度%（负数）
    - big_move_days：|change_pct| >= 5% 的交易日清单（基于收盘价逐日计算）
    - big_move_days_count / big_move_up_days / big_move_down_days：±5% 日计数
      （聚合由引擎完成——报告层引用 count 字段，禁止对清单目视计数）
    - ma20 / ma60 / ma120：收盘价均线尾部值
    - current_vs_high_pct / current_vs_low_pct：当前价 vs 历史高低点偏离%
      （窗口 = 250 日时为 52 周距离等价量，v0.2.6 不重复造同名字段）
    - dist_to_ytd_low_pct / ytd_low：年内低点偏离%（v0.2.6 D 类字段，四不原则 ①-b）
    - atr14 / atr14_pct：ATR14 及其占价格%（v0.2.6 D 类字段；仅 OHLC 路径，
      纯 NAV 链路无 high/low → None + atr14_note）
    """
    dates: list[str] = []
    closes: list[float] = []
    highs: list[float | None] = []
    lows: list[float | None] = []
    for r in nav_history:
        close = safe_float(r.get("nav", r.get("close")))
        d = str(r.get("date", ""))[:10]
        if close is not None and d:
            dates.append(d)
            closes.append(close)
            highs.append(safe_float(r.get("high")))
            lows.append(safe_float(r.get("low")))

    stats: dict[str, Any] = {
        "rows": len(closes),
        "date_range": (
            f"{dates[0]} ~ {dates[-1]}" if len(dates) >= 2 else (dates[0] if dates else None)
        ),
        "annual_high": None,
        "annual_low": None,
        "max_drawdown": None,
        "big_move_days": [],
        "big_move_days_count": 0,
        "big_move_up_days": 0,
        "big_move_down_days": 0,
        "ma20": None,
        "ma60": None,
        "ma120": None,
        "current_vs_high_pct": None,
        "current_vs_low_pct": None,
        "dist_to_ytd_low_pct": None,
        "ytd_low": None,
        "atr14": None,
        "atr14_pct": None,
        "atr14_note": None,
        "status": "available" if len(closes) >= 2 else "insufficient",
    }
    if len(closes) < 2:
        return stats

    # 年度高低点（首次出现优先，序列首行持平不翻转）
    high_i = low_i = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[high_i]:
            high_i = i
        if closes[i] < closes[low_i]:
            low_i = i
    stats["annual_high"] = {"date": dates[high_i], "close": closes[high_i]}
    stats["annual_low"] = {"date": dates[low_i], "close": closes[low_i]}

    # 最大回撤：运行峰值 → 谷底（峰值之后的最低收盘）。
    # peak/trough 必须配对记录：trough 更新时快照当时的运行峰值，
    # 否则循环结束后 peak_i 是"最后"一个运行峰值（可能晚于 trough），
    # V 型反转窗口会输出自相矛盾的峰谷对（{peak:110, trough:90, dd:-10%}
    # 实际是 110→90 = -18.18%）。
    peak_i = 0
    trough_i = 0
    dd_peak_i = 0  # 与最大回撤配对的峰值索引
    max_dd = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[peak_i]:
            peak_i = i
        dd = (closes[i] / closes[peak_i] - 1) * 100
        if dd < max_dd:
            max_dd = dd
            trough_i = i
            dd_peak_i = peak_i
    stats["max_drawdown"] = {
        "peak_date": dates[dd_peak_i],
        "peak_close": closes[dd_peak_i],
        "trough_date": dates[trough_i],
        "trough_close": closes[trough_i],
        "drawdown_pct": round(max_dd, 2),
    }

    # |change_pct| >= 5% 交易日清单（由收盘价重算，与 nav 源的日增长率口径解耦）
    big: list[dict[str, Any]] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            chg = (closes[i] / closes[i - 1] - 1) * 100
            if abs(chg) >= 5.0:
                big.append({"date": dates[i], "change_pct": round(chg, 2)})
    stats["big_move_days"] = big
    # 计数聚合（P0：清单目视计数违规——报告层必须引用引擎 count 字段）
    stats["big_move_days_count"] = len(big)
    stats["big_move_up_days"] = sum(1 for r in big if r["change_pct"] > 0)
    stats["big_move_down_days"] = sum(1 for r in big if r["change_pct"] < 0)

    for n, key in ((20, "ma20"), (60, "ma60"), (120, "ma120")):
        series = sma(closes, n)
        if series and series[-1] is not None:
            stats[key] = round(series[-1], 4)

    latest = closes[-1]
    high = stats["annual_high"]["close"]
    low = stats["annual_low"]["close"]
    if high > 0:
        stats["current_vs_high_pct"] = round((latest / high - 1) * 100, 2)
    if low > 0:
        stats["current_vs_low_pct"] = round((latest / low - 1) * 100, 2)

    # v0.2.6 D 类字段：年内低点偏离%（有符号，正 = 高于年内低点）
    year = dates[-1][:4]
    ytd_idx = min(
        (i for i in range(len(closes)) if dates[i][:4] == year),
        key=lambda i: closes[i],
        default=None,
    )
    if ytd_idx is not None and closes[ytd_idx] > 0:
        stats["ytd_low"] = {"date": dates[ytd_idx], "close": closes[ytd_idx]}
        stats["dist_to_ytd_low_pct"] = round((latest / closes[ytd_idx] - 1) * 100, 2)

    # v0.2.6 D 类字段：ATR14（仅 OHLC 路径）
    ohlc_available = bool(highs) and all(h is not None and l is not None for h, l in zip(highs, lows))
    if ohlc_available and len(closes) >= 15:
        tr_sum = sum(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, 15)
        )
        atr = tr_sum / 14
        for i in range(15, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            atr = (atr * 13 + tr) / 14
        stats["atr14"] = round(atr, 4)
        stats["atr14_pct"] = round(atr / latest * 100, 2) if latest else None
    elif not ohlc_available:
        stats["atr14_note"] = "纯 NAV 链路无 OHLC，ATR14 不可得"
    return stats
