#!/usr/bin/env python3
"""
investment-learning CLI。

用法:
  uv run python skills/invest-a-stock/scripts/invest.py collect 600176              # 采集数据
  uv run python skills/invest-a-stock/scripts/invest.py report 600176               # Markdown 报告（默认 stdout）
  uv run python skills/invest-a-stock/scripts/invest.py report 600176 --outdir ./out # Markdown 写入目录
  uv run python skills/invest-a-stock/scripts/invest.py report 600176 --emit=html    # HTML 报告（v0.1.2 旧版，须显式指定）
  uv run python skills/invest-a-stock/scripts/invest.py report 600176 --emit=json   # JSON 报告（stdout）
  uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858        # 对比
  uv run python skills/invest-a-stock/scripts/invest.py diagnose                     # 检查数据源
  uv run python skills/invest-a-stock/scripts/invest.py store list                   # 查看存储
  uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --store       # 采集并存储
  uv run python skills/invest-a-stock/scripts/invest.py watchlist 000001,600519 --outdir ./out  # 批量标的摘要
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 确保从本项目的 lib/ 导入，排除旧归档路径
_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))
# 跨 skill 导入 invest-a-journal 的 market_microstructure
_JOURNAL_LIB = _SCRIPT_DIR.parent.parent / "invest-a-journal" / "scripts" / "lib"
if str(_JOURNAL_LIB) not in sys.path:
    sys.path.insert(0, str(_JOURNAL_LIB))

# 查找项目根目录（向上遍历直到找到 pyproject.toml）
_project_root = _SCRIPT_DIR
while _project_root != _project_root.parent:
    if (_project_root / "pyproject.toml").exists():
        break
    _project_root = _project_root.parent

from lib import collector, env, render
from lib.collector import _DEFAULT_DIMS
from lib.proxy import warn_if_proxy_detected

_CLI_DEFAULT_DIMS = ",".join(_DEFAULT_DIMS)

try:
    from lib import store as store_mod
    _HAS_STORE = True
except ImportError as e:
    store_mod = None
    _HAS_STORE = False
    import logging
    logging.getLogger(__name__).warning("store 模块导入失败（功能降级）: %s", e)

try:
    from lib import planner as planner_mod
    _HAS_PLANNER = True
except ImportError:
    planner_mod = None
    _HAS_PLANNER = False

try:
    from lib import evidence as evidence_mod
    _HAS_EVIDENCE = True
except ImportError:
    evidence_mod = None
    _HAS_EVIDENCE = False

try:
    from lib import archiver as archiver_mod
    _HAS_ARCHIVER = True
except ImportError:
    archiver_mod = None
    _HAS_ARCHIVER = False

try:
    from lib import lint as lint_mod
    _HAS_LINT = True
except ImportError:
    lint_mod = None
    _HAS_LINT = False


def _plan_sort_key(module: dict) -> int:
    """计划模块 priority；null/非法值视为最低优先级。"""
    p = module.get("priority")
    if isinstance(p, bool):
        return 99
    if isinstance(p, int):
        return p
    if isinstance(p, float) and p == int(p):
        return int(p)
    return 99


def _collection_dimensions(cached: dict) -> list[dict]:
    dims = cached.get("dimensions")
    return dims if isinstance(dims, list) else []


def _dims_from_args(args: argparse.Namespace) -> list[str]:
    """从 --plan 文件或 --dims 解析维度列表。"""
    plan_path = getattr(args, "plan", "") or ""
    if plan_path:
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            modules = pdata.get("modules", [])
            if modules:
                return [
                    m["module_id"]
                    for m in sorted(modules, key=_plan_sort_key)
                ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"⚠️ 无法读取计划文件 {plan_path}: {exc}", file=sys.stderr)
    return [d.strip() for d in args.dims.split(",") if d.strip()]


def _collect_kwargs(args: argparse.Namespace) -> dict:
    deep = getattr(args, "deep", False)
    with_macro = getattr(args, "with_macro", False)
    return {
        "deep": deep,
        "with_macro": with_macro,
        "with_chain": with_macro or deep,
        "with_news_pack": getattr(args, "with_news_pack", False),
    }


def _try_resume_collection(symbol: str) -> dict | None:
    """--resume 时从 store 加载最近一次采集结果。"""
    if not _HAS_STORE:
        return None
    progress = store_mod.get_pipeline_progress(symbol)
    if not progress.get("collect"):
        return None
    rows = store_mod.list_collections(limit=1, symbol=symbol)
    if not rows:
        return None
    rec = store_mod.get_collection(rows[0]["id"])
    if rec and rec.get("raw_json"):
        return rec["raw_json"]
    return None


def _apply_deep_dims(dims: list[str], deep: bool) -> list[str]:
    out = list(dims)
    if deep:
        if "kline" not in out:
            out.append("kline")
        if "industry" not in out:
            out.append("industry")
        # Add research dim for Template C (architecture decision #4)
        if "research" not in out:
            out.append("research")
    return out


def _normalize_collection_for_render(payload: dict) -> dict:
    """统一 credibility / credibility_scores 别名，供 render 消费。"""
    out = dict(payload)
    cred_a = out.get("credibility")
    cred_b = out.get("credibility_scores")
    if not isinstance(cred_a, dict):
        cred_a = {}
    if not isinstance(cred_b, dict):
        cred_b = {}
    cred = {**cred_b, **cred_a}
    out["credibility"] = cred
    out["credibility_scores"] = cred
    return out


def _ensure_render_ready(collection: dict, symbol: str) -> None:
    """补齐报告渲染所需字段（market_structure / phase2），写入 collection。"""
    if not collection.get("market_structure"):
        collector.attach_market_structure(collection, symbol)
    collector.attach_phase2_extras(collection, symbol)


def _resume_cache_compatible(
    args: argparse.Namespace,
    dims: list[str],
    cached: dict,
) -> bool:
    """检查 store 快照是否与当前 CLI 标志兼容；不兼容时打印警告并返回 False。"""
    issues: list[str] = []
    symbol = getattr(args, "symbol", cached.get("symbol", ""))

    if getattr(args, "with_macro", False):
        macro = cached.get("macro_context") or {}
        indicators = macro.get("indicators") or {}
        if not any(indicators.values()):
            issues.append("--with-macro 已启用但快照无宏观数据")

    if getattr(args, "deep", False):
        dim_names = {
            d.get("dimension")
            for d in _collection_dimensions(cached)
            if d and d.get("dimension")
        }
        if "industry" not in dim_names:
            issues.append("--deep 已启用但快照无 industry 维度")
        if "research" not in dim_names:
            issues.append("--deep 已启用但快照无 research 维度")

    if _HAS_STORE:
        step = store_mod.load_pipeline_step(symbol, "collect")
        if step:
            st = step.get("state") or {}
            stored_dims = st.get("dims")
            if stored_dims and set(stored_dims) != set(dims):
                issues.append(
                    f"维度与上次 collect 不一致（快照: {stored_dims}，当前: {dims}）"
                )
            if not st.get("with_macro") and getattr(args, "with_macro", False):
                issues.append("--with-macro 已启用但上次 collect 未开启宏观")
            if not st.get("deep") and getattr(args, "deep", False):
                issues.append("--deep 已启用但上次 collect 未开启深度模式")

    for msg in issues:
        print(f"⚠️ --resume: {msg}，将重新采集", file=sys.stderr)
    return not issues


def _collect_pipeline_state(args: argparse.Namespace, dims: list[str]) -> dict:
    return {
        "dims": dims,
        "with_macro": bool(getattr(args, "with_macro", False)),
        "deep": bool(getattr(args, "deep", False)),
    }


def _warn_degraded_collection(result: dict) -> None:
    """partial 维度有数据时提示降级，避免静默使用不可靠结果。"""
    sm = result.get("summary") or {}
    degraded = sm.get("degraded", 0)
    total = sm.get("total", 0)
    if degraded > 0:
        print(
            f"⚠️ {degraded}/{total} 个维度为降级（partial）状态，部分数据源失败",
            file=sys.stderr,
        )
    if sm.get("all_partial"):
        print("⚠️ 全部有数据维度均为 partial，交叉验证与融合可靠性受限", file=sys.stderr)


def _no_sources_responded(summary: dict | None) -> bool:
    """中止条件：无任一维度有数据源响应（status 均非 available/partial）。

    与 summary.available 区分：数据源响应但返回空数据（非交易日 quote、
    节假日）不算失败——报告照常渲染为无数据区块。旧存档缺
    sources_responded 时回退 available 保持旧行为。
    """
    s = summary or {}
    return (s.get("sources_responded", s.get("available", 0)) or 0) == 0


def _add_collect_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--with-macro", action="store_true",
        help="采集宏观指标（中国: PMI/CPI/PPI/LPR + 全球: VIX/SOX）",
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="深度模式：K线窗口从默认 400 天（~1.1年）扩展至 730 天（2年），增加行业/产业链分析 + 自动采集机构研报",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股个股调研数据采集与分析")
    p.add_argument("--plan", default="", help="JSON 采集计划文件路径")
    p.add_argument("--mode", default="full", choices=["brief", "full", "concise"],
                   help="报告模式: brief(简报) / full(完整九模块) / concise(对话精简)")
    p.add_argument("--resume", action="store_true", help="从上次中断的步骤继续")
    p.add_argument("--save-raw", action="store_true",
                   help="保存原始采集 JSON 到 ~/.local/share/investment/raw/")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("collect", help="采集多维度数据")
    pc.add_argument("symbol")
    pc.add_argument("--dims", default=_CLI_DEFAULT_DIMS)
    pc.add_argument("--store", action="store_true", help="存入持久化存储")
    pc.add_argument("--with-macro", action="store_true", help="采集宏观指标（中国: PMI/CPI/PPI/LPR + 全球: VIX/SOX）")
    pc.add_argument("--deep", action="store_true", help="深度模式：K线窗口从默认 400 天（~1.1年）扩展至 730 天（2年），增加行业/产业链分析 + 自动采集机构研报")
    pc.add_argument(
        "--with-news-pack",
        action="store_true",
        help="采集新闻包（公告 + 声明式查询包 + 可选 Tavily；无 Key 时 Layer3 静默跳过）",
    )

    pr = sub.add_parser("report", help="生成分析报告")
    pr.add_argument("symbol")
    pr.add_argument("--plan", default="", help="JSON 采集计划文件路径")
    pr.add_argument("--mode", default="full", choices=["brief", "full", "concise"],
                   help="报告模式: brief(简报) / full(完整九模块) / concise(对话精简)")
    pr.add_argument("--emit", default="md", choices=["compact", "json", "md", "html"])
    pr.add_argument("--dims", default=_CLI_DEFAULT_DIMS)
    pr.add_argument("--with-macro", action="store_true", help="采集宏观指标（中国: PMI/CPI/PPI/LPR + 全球: VIX/SOX）")
    pr.add_argument("--deep", action="store_true", help="深度模式：K线窗口从默认 400 天（~1.1年）扩展至 730 天（2年），增加行业/产业链分析 + 自动采集机构研报")
    pr.add_argument(
        "--with-news-pack",
        action="store_true",
        help="采集新闻包（公告 + 声明式查询包 + 可选 Tavily；无 Key 时 Layer3 静默跳过）",
    )
    pr.add_argument(
        "--strict-rigor",
        action="store_true",
        help="严格验算：跨源差异 >5%% 时在报告中硬标注阻断提示",
    )
    pr.add_argument(
        "--material-gap",
        action="store_true",
        help="R12c：报告生成前输出 12 题数据缺口清单（先回填再出报告）",
    )
    pr.add_argument("--outdir", default="", help="报告输出目录（指定则写 .md 或 .html 文件；默认仅 stdout）")

    pcomp = sub.add_parser("compare", help="双标对比")
    pcomp.add_argument("symbol_a")
    pcomp.add_argument("symbol_b")
    pcomp.add_argument("--emit", default="compact", choices=["compact", "json"])

    pdiff = sub.add_parser("diff", help="对比两次快照变化")
    pdiff.add_argument("symbol")
    pdiff.add_argument("--from", dest="from_id", type=int, help="指定旧快照 ID")
    pdiff.add_argument("--to", dest="to_id", type=int, help="指定新快照 ID")
    pdiff.add_argument("--emit", default="compact", choices=["compact", "json", "md"])

    pw = sub.add_parser(
        "watchlist",
        help="批量标的摘要（优先 store 快照；无快照时现场采集，较慢）",
    )
    pw.add_argument("symbols", help="逗号分隔股票代码（≥2）")
    pw.add_argument("--outdir", default="", help="输出目录（指定则写 watchlist_YYYY-MM-DD.md；默认 stdout）")

    pd = sub.add_parser("diagnose", help="检查数据源")
    pd.add_argument("--json", action="store_true")

    pl = sub.add_parser(
        "lint",
        help="合规扫描：检查研究报告是否符合措辞、结构和证据规范",
    )
    pl.add_argument("target", help="报告文件路径或 reports/ 目录", nargs="?", default="reports")
    pl.add_argument("--profile", choices=["claude", "precommit", "engine"], default="claude",
                    help="扫描规则集（claude=全部规则，precommit=钩子阻断项，engine=仅措辞+文件名）")
    pl.add_argument("--fail-on", choices=["error", "warning", "info"], default="error",
                    help="达到该级别及以上时返回非零退出码")

    ps = sub.add_parser("store", help="管理存储")
    ps.add_argument("action", nargs="?", default="list", choices=["list", "stats", "clear", "valuations"])
    ps.add_argument("--symbol", default="", help="过滤股票代码（valuations 模式）")

    ppl = sub.add_parser("plan", help="生成采集计划")
    ppl.add_argument("symbol")
    ppl.add_argument("--intent", default="deep_analysis",
                     choices=[
                         "deep_analysis", "quick_check", "catalyst_monitor", "compare",
                         "sentiment_deep", "financials_deep", "game_theory",
                     ])
    ppl.add_argument("--emit", default="json", choices=["json"])

    pe = sub.add_parser("evidence", help="生成结构化证据表")
    pe.add_argument("symbol")
    pe.add_argument("--emit", default="md", choices=["md", "json"])
    pe.add_argument("--dims", default=_CLI_DEFAULT_DIMS)
    _add_collect_flags(pe)

    pa = sub.add_parser("analyze", help="分析采集结果（输出中间分析 JSON）")
    pa.add_argument("symbol")
    pa.add_argument("--input", default="", help="采集结果 JSON 文件路径（留空则现场采集）")
    pa.add_argument("--emit", default="json", choices=["json", "md"])
    _add_collect_flags(pa)

    psyn = sub.add_parser("synthesize", help="合成最终研究报告")
    psyn.add_argument("symbol")
    psyn.add_argument("--input", default="", help="分析结果 JSON 文件路径")
    psyn.add_argument("--emit", default="md", choices=["md", "json"])
    psyn.add_argument("--mode", default="full", choices=["brief", "full", "concise"])
    psyn.add_argument("--outdir", default="", help="报告输出目录")
    psyn.add_argument("--dims", default=_CLI_DEFAULT_DIMS)
    _add_collect_flags(psyn)

    pp = sub.add_parser(
        "peer",
        help="行业横向对比：输出同行业公司估值与财务对比表",
    )
    pp.add_argument("symbol", help="股票代码，如 600176")
    pp.add_argument(
        "--top", type=int, default=10,
        help="对比公司数量（默认10）",
    )
    pp.add_argument(
        "--sort-by", choices=["market_cap", "revenue", "roe"],
        default="market_cap", help="排序依据（默认市值下降）",
    )

    prigor = sub.add_parser("rigor", help="财务验算：市值/估值/跨源交叉验证")
    prigor.add_argument("symbol")
    prigor.add_argument("--verify-all", action="store_true", help="运行全部验算命令")
    prigor.add_argument("--strict", action="store_true", help="严格模式：>5%% 差异视为阻断")
    prigor.add_argument("--calc", default="", help="Decimal 精确计算表达式")

    paudit = sub.add_parser("audit", help="报告审计：抽取数据点 / 准出判决")
    paudit.add_argument("report")
    paudit.add_argument("--extract", action="store_true", help="抽取 15%% 数据点到 audit_checklist.json")
    paudit.add_argument("--verdict", action="store_true", help="读取核验结果并输出 PASS/FAIL")

    pcheck = sub.add_parser(
        "check",
        help="单标的质地检查（非全市场筛选；全市场扫描 → v0.2.0）",
    )
    pcheck.add_argument("symbol")

    pport = sub.add_parser("portfolio", help="组合风险特征（行业集中度/相关性/压力测试）")
    pport.add_argument("holdings", help="holdings.json 路径")
    pport.add_argument("--stress", action="store_true", help="指数 -10%%/-20%%/-30%% 压力测试")

    pthesis = sub.add_parser("thesis", help="投资假设追踪")
    pthesis.add_argument("symbol")
    pthesis.add_argument("--init", action="store_true", help="初始化假设模板")
    pthesis.add_argument("--update", action="store_true", help="更新假设状态")
    pthesis.add_argument("--status", action="store_true", help="查看当前状态")
    pthesis.add_argument(
        "--invalidate", action="append", default=[], metavar="ID",
        help="将指定 assumption id 标为 invalid（可重复，配合 --update）",
    )
    pthesis.add_argument(
        "--trigger-redline", action="append", default=[], metavar="ID",
        help="将指定 red_line id 标为 triggered（可重复，配合 --update）",
    )

    pshock = sub.add_parser("shock", help="价格冲击插值比例（非风险中性概率）")
    pshock.add_argument("symbol", nargs="?", default="", help="标的代码（仅标注用）")
    pshock.add_argument("--pre-price", type=float, required=True)
    pshock.add_argument("--post-price", type=float, required=True)
    pshock.add_argument("--eps-base", type=float, required=True)
    pshock.add_argument("--eps-hit", type=float, required=True)
    pshock.add_argument("--pe-normal", type=float, required=True)
    pshock.add_argument("--pe-stressed", type=float, required=True)

    prr = sub.add_parser("risk-reward", help="DCF 三情景盈亏比分析")
    prr.add_argument("symbol", help="股票代码，如 600176")
    prr.add_argument("--rf", type=float, help="无风险利率（小数），默认 2.5%%")
    prr.add_argument("--erp", type=float, help="股权风险溢价（默认 0.06）")
    prr.add_argument("--terminal-g", type=float, default=0.025, help="终端增长率（默认 0.025）")
    prr.add_argument("--store", action="store_true", help="从 store 读取最近采集结果")

    pic = sub.add_parser("ic", help="投资委员会决策框架")
    pic.add_argument("symbol", help="股票代码，如 600176")
    pic.add_argument("--rf", type=float, help="无风险利率（小数），默认 2.5%%")
    pic.add_argument("--erp", type=float, help="股权风险溢价（默认 0.06）")

    pcls = sub.add_parser("classify", help="R1: 收益驱动假设分类（研究路径分流）")
    pcls.add_argument("symbol", help="股票代码，如 002466")
    pcls.add_argument("--div-years", type=int, default=None, help="连续分红年数（未提供则标注证据缺失）")
    pcls.add_argument("--div-yield", type=float, default=None, help="股息率（小数，如 0.03）")
    pcls.add_argument("--refi-times", type=int, default=None, help="近 N 年再融资次数（未提供则标注证据缺失）")
    pcls.add_argument("--emit", default="text", choices=["text", "json"])

    pval = sub.add_parser("value", help="科学估值：多方法交叉（PE/PB/盈利收益/隐含增长/ROE-PB匹配）")
    pval.add_argument("symbol", help="股票代码，如 002466")
    pval.add_argument("--rf", type=float, help="无风险利率（小数），默认自动获取中国10Y国债")
    pval.add_argument("--erp", type=float, default=0.06, help="股权风险溢价（默认 0.06）")
    pval.add_argument("--store", action="store_true", help="结果存入数据库便于回溯")
    pval.add_argument("--emit", default="text", choices=["text", "json"])
    pval.add_argument("--steady", action="store_true",
                      help="R2: 追加稳态盈利估值（穿越周期视角，识别周期高点低PE陷阱）")
    pval.add_argument("--cycle-start", default=None, help="周期区间起点（YYYY1231）")
    pval.add_argument("--cycle-end", default=None, help="周期区间终点（YYYY1231）")
    pval.add_argument("--cycle-method", default="median", choices=["median", "trimmed", "range"],
                      help="稳态盈利算法（默认 median）")
    pval.add_argument("--cycle-pe", type=float, default=None, help="周期中枢 PE（默认 12）")
    pval.add_argument("--ev-ebitda", action="store_true",
                      help="R3: 追加 EV/EBITDA 企业价值桥接表（可审计逐项）+ 私有化检验研究问题")
    pval.add_argument("--industry", default=None, help="行业名（用于 R3 金融业豁免判定）")

    pms = sub.add_parser("market-status", help="市场微观结构快照：杠杆/广度/情绪/估值温度；或 R5 行业景气状态卡")
    pms.add_argument("--days", type=int, default=5, help="趋势表周期（默认 5 天）")
    pms.add_argument("--json", action="store_true", help="输出原始 JSON")
    pms.add_argument("--save", action="store_true", help="采集并保存当日快照（非交易时段跳过）")
    pms.add_argument("--industry", type=str, default="", metavar="SW_NAME",
                     help="R5 行业景气状态卡：指定申万一级行业名（如 半导体/消费/钢铁），输出五维状态卡")

    pef = sub.add_parser("etf-flow", help="ETF 份额变化趋势（需先 --save 积累历史）")
    pef.add_argument("symbol", help="6 位 ETF 代码（如 588000）")
    pef.add_argument("--days", type=int, default=60, help="回溯天数（默认 60）")
    pef.add_argument("--save", action="store_true", help="采集当日份额并存入 DB")
    pef.add_argument("--json", action="store_true", help="输出原始 JSON")

    pcat = sub.add_parser("catalyst", help="催化剂日历：分红/解禁/公告前瞻事件")
    pcat.add_argument("symbol", help="股票代码，如 600176")
    pcat.add_argument("--days", type=int, default=90, help="前瞻天数（默认 90）")

    return p


def cmd_collect(args: argparse.Namespace) -> int:
    dims = _apply_deep_dims(_dims_from_args(args), args.deep)
    if args.resume and _HAS_STORE:
        progress = store_mod.get_pipeline_progress(args.symbol)
        completed_steps = [s for s, done in progress.items() if done]
        if completed_steps:
            print(f"📋 已完成步骤: {', '.join(completed_steps)}")
        cached = _try_resume_collection(args.symbol)
        if cached and _resume_cache_compatible(args, dims, cached):
            print("♻️ 从 store 恢复上次采集结果（--resume）")
            result = cached
            _warn_degraded_collection(result)
            print(render.render(result, args.symbol, "compact"))
            if getattr(args, "save_raw", False):
                try:
                    from lib.archiver import archive_collection
                    filepath = archive_collection(args.symbol, result)
                    if filepath:
                        print(f"📦 原始数据已存档: {filepath}")
                except Exception as exc:
                    print(f"⚠️ 存档失败: {exc}", file=sys.stderr)
            return 0
        if progress.get("collect"):
            print(
                "⚠️ --resume: 无 store 快照可恢复（需先 `collect SYMBOL --store`）",
                file=sys.stderr,
            )
    if args.with_macro and "kline" not in dims:
        dims.append("kline")
    if args.deep:
        print("🔬 深度模式已启用（扩大K线范围至730日 + 行业/舆情分析）")
    if args.with_macro:
        print("🌐 宏观数据模式已启用（中国 PMI/CPI/PPI/LPR + 全球 VIX/SOX）")
    if getattr(args, "with_news_pack", False):
        print("📰 新闻包模式已启用（公告 + 查询包 + 可选 Tavily）")
    env.print_missing_token_warnings()
    warn_if_proxy_detected(probe=True)
    if "kline" in dims:
        try:
            from lib.collector import _kline_cache
            _kline_cache.cleanup_old()
        except Exception:
            pass
    result = collector.collect_all(args.symbol, dims, **_collect_kwargs(args))
    _warn_degraded_collection(result)
    if _no_sources_responded(result["summary"]):
        print(render.render(result, args.symbol, "compact"))
        print("⚠️ 所有维度均不可用。请运行 diagnose。")
        return 1
    print(render.render(result, args.symbol, "compact"))
    if args.store and _HAS_STORE:
        store_mod.save_collection(result)
        print("💾 已存入持久化存储")
    if _HAS_STORE:
        store_mod.save_pipeline_step(
            args.symbol, "collect", _collect_pipeline_state(args, dims),
        )
    if getattr(args, 'save_raw', False):
        try:
            from lib.archiver import archive_collection
            filepath = archive_collection(args.symbol, result)
            if filepath:
                print(f"📦 原始数据已存档: {filepath}")
        except Exception as exc:
            print(f"⚠️ 存档失败: {exc}", file=sys.stderr)
    return 0


def _report_basename(result: dict, symbol: str, ts: str) -> str:
    """生成报告子目录名：{symbol}-{name}（文件名用日期，如 2026-07-05.md）。"""
    name = ""
    for dim in result.get("dimensions", []):
        if dim.get("dimension") == "basic_info":
            data = dim.get("data", {})
            if isinstance(data, dict):
                name = data.get("name", "") or data.get("股票简称", "")
            break
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", name) if name else ""
    return f"{symbol}-{safe_name}" if safe_name else symbol


def _report_filepath(outdir: Path, subdir: str, ts: str) -> Path:
    """生成报告完整路径：{outdir}/{subdir}/{YYYY-MM-DD-HH-MM-SS}.md。"""
    report_dir = outdir / subdir
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir / f"{ts}.md"


def cmd_report(args: argparse.Namespace) -> int:
    dims = _apply_deep_dims(_dims_from_args(args), args.deep)
    result = None
    if args.resume and _HAS_STORE:
        progress = store_mod.get_pipeline_progress(args.symbol)
        completed_steps = [s for s, done in progress.items() if done]
        if completed_steps:
            print(f"📋 已完成步骤: {', '.join(completed_steps)}")
        result = _try_resume_collection(args.symbol)
        if result and _resume_cache_compatible(args, dims, result):
            print("♻️ 从 store 恢复上次采集结果（--resume）", file=sys.stderr)
        elif result:
            result = None
        elif progress.get("collect"):
            print(
                "⚠️ --resume: 无 store 快照可恢复（需先 `collect SYMBOL --store`）",
                file=sys.stderr,
            )
    if args.with_macro and "kline" not in dims:
        dims.append("kline")
    if args.deep:
        print("🔬 深度模式已启用（扩大K线范围至730日 + 行业/舆情分析）")
    if args.with_macro:
        print("🌐 宏观数据模式已启用（中国 PMI/CPI/PPI/LPR + 全球 VIX/SOX）")
    if result is None:
        env.print_missing_token_warnings()
        warn_if_proxy_detected(probe=True)
        result = collector.collect_all(args.symbol, dims, **_collect_kwargs(args))
    # R4: 行业成功关键因素装配（未覆盖行业 → covered=False，渲染层标注「无行业成功因素定义」）
    try:
        from lib.render_utils import _get_dim_data, _index_dims
        from lib.industry.base import get_success_factors
        basic = _get_dim_data(_index_dims(result), "basic_info") or {}
        industry = ""
        if isinstance(basic, dict):
            industry = str(basic.get("industry") or basic.get("行业") or "")
        factors = get_success_factors(industry)
        result["success_factors"] = {
            "industry": industry,
            "covered": bool(factors),
            "factors": factors,
        }
    except Exception:  # 装配失败不阻断报告
        result["success_factors"] = {"industry": "", "covered": False, "factors": []}
    # R12g-A: 连板触发 → 龙虎榜/涨停池采集（仅触发时执行，未触发零额外网络调用）
    try:
        from lib.lhb import attach_limit_streak_dims
        if attach_limit_streak_dims(result, args.symbol):
            print("⚡ 近 5 日 ≥2 涨停，已附加连板结构数据（龙虎榜/涨停池）", file=sys.stderr)
    except Exception:  # 采集失败不阻断报告
        pass
    # R10/R12g-B: 风格-标的匹配三态（风格档案 + 同标的 journal Q1 代理）
    try:
        from lib.style_match import assemble_style_match
        result["style_match"] = assemble_style_match(result, args.symbol)
    except Exception:  # 装配失败不阻断报告
        pass
    if getattr(args, "strict_rigor", False):
        result.setdefault("_meta", {})["strict_rigor"] = True
    _warn_degraded_collection(result)
    if getattr(args, "material_gap", False):
        try:
            from lib.render_utils import format_material_gap, material_gap_report
            print(format_material_gap(material_gap_report(result)), file=sys.stderr)
        except Exception as exc:  # 缺口检查失败不阻断报告
            print(f"⚠️ material-gap 检查失败: {exc}", file=sys.stderr)
    if _no_sources_responded(result["summary"]):
        print("⚠️ 所有维度均不可用，无法生成报告")
        return 1
    if _HAS_STORE:
        store_mod.save_pipeline_step(args.symbol, "report", {"dims": dims, "mode": getattr(args, "mode", "full")})

    fmt = args.emit

    if fmt == "html":
        print(
            "⚠️ HTML 为 v0.1.2 旧版模板，迭代期请使用默认 Markdown 输出（省略 --emit 或 --emit md）",
            file=sys.stderr,
        )
        _ensure_render_ready(result, args.symbol)
        md_v2 = render.render_report_v2(result, args.symbol)
        output = render.render_html(result, args.symbol)
        from datetime import datetime
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d-%H-%M-%S")

        subdir = _report_basename(result, args.symbol, ts)
        outdir = Path(args.outdir).resolve() if args.outdir else Path.cwd()
        htmlpath = _report_filepath(outdir, subdir, ts).with_suffix(".html")
        htmlpath.parent.mkdir(parents=True, exist_ok=True)
        htmlpath.write_text(output, encoding="utf-8")

        mdfile = _report_filepath(outdir, subdir, ts)
        mdfile.write_text(md_v2, encoding="utf-8")

        print(render.render(result, args.symbol, "compact"))
        print(f"📄 HTML 报告: {htmlpath.resolve()}")
        print(f"📝 Markdown 报告: {mdfile.resolve()}")
        return 0

    output = render.render(result, args.symbol, fmt, mode=getattr(args, 'mode', 'full'))

    if getattr(args, 'save_raw', False):
        try:
            from lib.archiver import archive_collection
            filepath = archive_collection(args.symbol, result)
            if filepath:
                print(f"📦 原始数据已存档: {filepath}", file=sys.stderr)
        except Exception as exc:
            print(f"⚠️ 存档失败: {exc}", file=sys.stderr)

    if fmt == "md" and args.outdir:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        subdir = _report_basename(result, args.symbol, ts)
        outdir = Path(args.outdir).resolve()
        mdpath = _report_filepath(outdir, subdir, ts)
        mdpath.write_text(output, encoding="utf-8")
        print(f"📝 Markdown 报告: {mdpath.resolve()}")
        return 0

    print(output)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    env.print_missing_token_warnings()
    warn_if_proxy_detected(probe=True)
    ra = collector.collect_all(args.symbol_a)
    rb = collector.collect_all(args.symbol_b)
    da = {d["dimension"]: d for d in ra["dimensions"]}
    db = {d["dimension"]: d for d in rb["dimensions"]}
    lines = [f"# 对比: {args.symbol_a} vs {args.symbol_b}", ""]
    for dn in sorted(set(list(da.keys()) + list(db.keys()))):
        lines.append(f"## {da.get(dn, db.get(dn, {})).get('display', dn)}\n")
        if dn == "financials":
            lines.append("| 期间 | 标的A ROE | 标的B ROE | 标的A EPS | 标的B EPS |\n|------|-----------|-----------|-----------|-----------|")
            ra_ = {r["end_date"]: r for r in (da.get(dn, {}).get("data") or [])}
            rb_ = {r["end_date"]: r for r in (db.get(dn, {}).get("data") or [])}
            for d in sorted(set(list(ra_.keys()) + list(rb_.keys())), reverse=True)[:8]:
                lines.append(f"| {d} | {ra_.get(d,{}).get('roe','-')}% | {rb_.get(d,{}).get('roe','-')}% | {ra_.get(d,{}).get('eps','-')} | {rb_.get(d,{}).get('eps','-')} |")
            lines.append("")
    print("\n".join(lines))
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    warn_if_proxy_detected(probe=True)
    d = env.diagnose()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    proxy_hint = ""
    if d.get("proxy_detected"):
        if d.get("proxy_bypass_effective") and not d.get("proxy_user_action_needed"):
            proxy_hint = "代理环境: 已检测 — 采集器已自动绕过 HTTP 代理\n"
        elif d.get("proxy_hint_kind") == "tun_or_cdn":
            proxy_hint = (
                "代理环境: 已检测 — 已自动绕过 HTTP 代理，但东方财富 push2 接口不可达"
                "（可能为 TUN 劫持或 CDN 限制）\n"
            )
        elif d.get("proxy_user_action_needed"):
            proxy_hint = "代理环境: 已检测 — 无法自动绕过，请配置 Clash DIRECT 规则\n"
            if d.get("clash_rules_hint"):
                proxy_hint += f"\n{d['clash_rules_hint']}\n"
        else:
            proxy_hint = "代理环境: 已检测\n"
    print(f"=== 数据源诊断 ===\n配置: {d['config_source']}\n{proxy_hint}可用: {d['available_count']}/{d['total_count']}\n")
    for s, a in d["sources"].items():
        if isinstance(a, dict):
            em = a
            icon = "✅" if em.get("reachable") else "❌"
            detail = f" (HTTP {em.get('http_status') or 'N/A'})" if em.get("error") else ""
            print(f"  {icon} {s}{detail}")
            if em.get("error"):
                from lib.render import sanitize_error
                print(f"      ↳ {sanitize_error(em['error'], 80)}")
        else:
            print(f"  {'✅' if a else '❌'} {s}")
    print()
    return 0 if d["available_count"] > 0 else 1


def cmd_store(args: argparse.Namespace) -> int:
    if not _HAS_STORE:
        print("⚠️ store 模块不可用")
        return 1
    if args.action == "list":
        for r in store_mod.list_collections(20):
            print(f"  #{r['id']}: {r['symbol']} | {r.get('fetched_at','')[:19]} | {r.get('dimensions_ok','?')}/{r.get('dimensions_total','?')}")
        return 0
    if args.action == "stats":
        for k, v in store_mod.get_stats().items():
            print(f"  {k}: {v}")
        return 0
    if args.action == "clear":
        store_mod.clear_all()
        print("✅ 已清空")
        return 0
    if args.action == "valuations":
        sym = args.symbol.strip() if args.symbol else None
        rows = store_mod.list_valuations(symbol=sym, limit=20)
        if not rows:
            print("  (暂无估值记录)")
            return 0
        print(f"  {'ID':<5} {'symbol':<8} {'日期':<20} {'价格':>8} {'TTM PE':>8} {'PB':>7} {'中性区间':>16}")
        print(f"  {'─' * 5} {'─' * 8} {'─' * 20} {'─' * 8} {'─' * 8} {'─' * 7} {'─' * 16}")
        for r in rows:
            base_lo = f"{r.get('base_low', 0):.0f}" if r.get("base_low") is not None else "?"
            base_hi = f"{r.get('base_high', 0):.0f}" if r.get("base_high") is not None else "?"
            print(f"  {r['id']:<5} {r['symbol']:<8} {r.get('created_at', '')[:19]:<20} "
                  f"{r.get('price', 0) or 0:>8.2f} {r.get('ttm_pe', 0) or 0:>8.1f} "
                  f"{r.get('pb', 0) or 0:>7.2f} {base_lo}~{base_hi}")
        return 0
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """生成采集计划并输出 JSON。"""
    if not _HAS_PLANNER:
        print("⚠️ planner 模块不可用")
        return 1
    plan = planner_mod.generate_plan(args.symbol, args.intent)
    if args.emit == "json":
        import json as _json
        print(_json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        if _HAS_STORE:
            store_mod.save_pipeline_step(args.symbol, "plan", plan.to_dict())
        return 0
    return 1


def cmd_evidence(args: argparse.Namespace) -> int:
    """生成结构化证据表。"""
    if not _HAS_EVIDENCE:
        print("⚠️ evidence 模块不可用")
        return 1
    env.print_missing_token_warnings()
    dims = _apply_deep_dims(_dims_from_args(args), args.deep)
    result = collector.collect_all(args.symbol, dims, **_collect_kwargs(args))
    _warn_degraded_collection(result)
    if _no_sources_responded(result["summary"]):
        print("⚠️ 所有维度均不可用，无法生成证据表")
        return 1
    rows = evidence_mod.build_evidence_table(result["dimensions"])
    output = evidence_mod.render_evidence_table(rows, args.emit)
    print(output)
    if _HAS_STORE:
        store_mod.save_pipeline_step(args.symbol, "evidence", {"dims": dims})
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """中间分析步骤。采集数据并输出结构化分析 JSON。

    v0.1.5 中为占位实现：输出采集 + 证据表 + 可信度评分的综合 JSON。
    完整分析由 Claude 在 Skill 调用时完成。
    """
    import json as _json

    # 采集或加载
    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                result = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError) as exc:
            print(f"❌ 无法读取输入文件: {exc}", file=sys.stderr)
            return 1
    else:
        dims = _apply_deep_dims(list(_DEFAULT_DIMS), getattr(args, "deep", False))
        result = collector.collect_all(args.symbol, dims, **_collect_kwargs(args))

    if _no_sources_responded(result.get("summary")):
        print("⚠️ 所有维度均不可用", file=sys.stderr)
        return 1

    _warn_degraded_collection(result)
    _ensure_render_ready(result, args.symbol)

    cred = result.get("credibility", {})
    # 构建分析输出（保留 dimensions + 渲染快照供 synthesize --input 离线使用）
    analysis = {
        "symbol": args.symbol,
        "analyzed_at": result.get("fetched_at", ""),
        "fetched_at": result.get("fetched_at", ""),
        "dimensions": result.get("dimensions", []),
        "summary": result.get("summary", {}),
        "evidence_table": None,
        "credibility": cred,
        "credibility_scores": cred,
        "fusion": result.get("fusion", {}),
        "macro_context": result.get("macro_context", {}),
        "chain_context": result.get("chain_context", {}),
        "market_structure": result.get("market_structure"),
        "industry_peers": result.get("industry_peers"),
        "pe_band": result.get("pe_band"),
    }
    if result.get("phase2_extras_errors"):
        analysis["phase2_extras_errors"] = result["phase2_extras_errors"]

    # 证据表
    if _HAS_EVIDENCE:
        try:
            rows = evidence_mod.build_evidence_table(result["dimensions"])
            analysis["evidence_table"] = [
                {"dimension": r.dimension, "channel": r.channel,
                 "value": r.value_summary, "confidence": r.confidence,
                 "cross_validation": r.cross_validation}
                for r in rows
            ]
        except Exception as exc:
            print(f"⚠️ 证据表构建失败: {exc}", file=sys.stderr)

    # Fusion 结果（collect_all 已序列化为 dict）
    if result.get("fusion"):
        analysis["fusion"] = result["fusion"]

    if args.emit == "md" and _HAS_EVIDENCE and analysis.get("evidence_table"):
        print(evidence_mod.render_evidence_table(
            evidence_mod.build_evidence_table(result["dimensions"]), "md",
        ))
        if _HAS_STORE:
            store_mod.save_pipeline_step(args.symbol, "analyze", {"emit": "md"})
        return 0

    from lib.json_util import dumps_json
    print(dumps_json(analysis))
    if _HAS_STORE:
        store_mod.save_pipeline_step(args.symbol, "analyze", {"emit": args.emit})
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    """合成最终研究报告。

    若提供 --input（analyze 输出 JSON），从中恢复采集结果并渲染报告。
  否则等同于 report（现场采集+渲染）。
    """
    import json as _json

    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                analysis = _json.load(f)
        except (OSError, _json.JSONDecodeError) as exc:
            print(f"❌ 无法读取分析文件: {exc}", file=sys.stderr)
            return 1
        # analyze 输出不含完整 dimensions 时回退现场采集
        if analysis.get("dimensions"):
            result = _normalize_collection_for_render(analysis)
            attach_extras = not result.get("market_structure")
        else:
            print(
                "ⓘ analyze 输出缺少 dimensions，将补充现场采集",
                file=sys.stderr,
            )
            dims = _apply_deep_dims(list(_DEFAULT_DIMS), getattr(args, "deep", False))
            result = collector.collect_all(
                args.symbol, dims, **_collect_kwargs(args),
            )
            result = _normalize_collection_for_render({
                **result,
                "credibility": analysis.get(
                    "credibility_scores", result.get("credibility", {}),
                ),
                "fusion": analysis.get("fusion", result.get("fusion", {})),
                "macro_context": analysis.get("macro_context", {}),
                "chain_context": analysis.get("chain_context", {}),
            })
            attach_extras = True

        fmt = args.emit if args.emit != "json" else "md"
        output = render.render(
            result, args.symbol, fmt,
            mode=getattr(args, "mode", "full"),
            attach_extras=attach_extras,
        )
        if fmt == "md" and args.outdir:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            subdir = _report_basename(result, args.symbol, ts)
            outdir = Path(args.outdir).resolve()
            mdpath = _report_filepath(outdir, subdir, ts)
            mdpath.write_text(output, encoding="utf-8")
            print(f"📝 Markdown 报告: {mdpath.resolve()}")
            return 0
        print(output)
        return 0

    # 无 --input 时委托 cmd_report（dims 由 parser 默认 _CLI_DEFAULT_DIMS）
    if not hasattr(args, "with_macro"):
        args.with_macro = False
    if not hasattr(args, "deep"):
        args.deep = False

    return cmd_report(args)


def cmd_peer(args: argparse.Namespace) -> int:
    """行业横向对比 CLI：输出 Markdown 对比表。"""
    env.print_missing_token_warnings()
    try:
        result = collector.collect_peer_comparison(
            args.symbol, top_n=args.top, sort_by=args.sort_by,
        )
    except Exception as exc:
        print(f"❌ 同行对比采集失败: {exc}", file=sys.stderr)
        return 1

    if result.get("error"):
        print(f"❌ {result['error']}", file=sys.stderr)
        return 1

    peers = result.get("peers", [])
    target = result.get("target")
    industry_name = result.get("industry_name", "")
    peer_source = result.get("peer_source", "")
    sort_by = result.get("sort_by", "market_cap")

    target_name = target.get("name", "") if target else ""

    lines = [f"## 行业横向对比: {args.symbol} {target_name}"]
    if industry_name:
        lines.append(f"\n行业: {industry_name}")
    lines.append("")

    # 排序标签
    sort_labels_map = {
        "market_cap": "总市值", "revenue": "营收增速", "roe": "ROE",
    }
    sort_label = sort_labels_map.get(sort_by, sort_by)

    # Markdown 表头
    lines.append(
        "| 排名 | 代码 | 名称 | 总市值(亿) | PE(TTM) | PB | ROE(%) | 营收增速(%) |"
    )
    lines.append(
        "|------|------|------|-----------|---------|-----|--------|------------|"
    )

    sort_field_map = {
        "market_cap": "total_mv",
        "revenue": "revenue_yoy",
        "roe": "roe",
    }
    sf = sort_field_map.get(sort_by, "total_mv")

    def _fmt_row(code: str, name: str, entry: dict, bold: bool = False) -> str:
        """Format a single table row."""
        mv = entry.get("total_mv")
        pe = entry.get("pe_ttm")
        pb = entry.get("pb")
        roe = entry.get("roe")
        rev = entry.get("revenue_yoy")

        mv_s = f"{mv:.1f}" if mv is not None else "-"
        pe_s = f"{pe:.1f}" if pe is not None else "-"
        pb_s = f"{pb:.2f}" if pb is not None else "-"
        roe_s = f"{roe:.1f}" if roe is not None else "-"
        rev_s = f"{rev:+.1f}" if rev is not None else "-"

        if bold:
            code = f"**{code}**"
            name = f"**{name}**"
        return f"{code} | {name} | {mv_s} | {pe_s} | {pb_s} | {roe_s} | {rev_s} |"

    target_code = (target or {}).get("symbol", "")
    all_entries: list[dict] = []
    if target:
        all_entries.append(target)
    for p in peers:
        if target_code and p.get("symbol") == target_code:
            continue
        all_entries.append(p)

    ranked = sorted(
        all_entries, key=lambda p: (p.get(sf) is None, -(p.get(sf) or 0)),
    )

    for rank, entry in enumerate(ranked, start=1):
        code = entry.get("symbol", "")
        name = entry.get("name", "")
        is_target = bool(target_code and code == target_code)
        lines.append(f"| {rank} | {_fmt_row(code, name, entry, bold=is_target)}")

    lines.append("")

    # 数据来源标注
    source_labels = {
        "tushare_5000": "Tushare index_member（申万L3，需5000+积分）",
        "tushare_2000": (
            "Tushare stock_basic（申万粗分类，需2000+积分）"
        ),
        "akshare_fallback": (
            "akshare 东方财富行业板块"
            " [⚠️ 非申万 L3 精确成分，仅供参考]"
        ),
    }
    source_note = source_labels.get(peer_source, peer_source)
    lines.append(f"> 数据来源: {source_note}")
    lines.append(
        f"> 排序: {sort_label}降序 | "
        f"共 {len(ranked)} 行（含标的）",
    )

    print("\n".join(lines))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """对比同一股票两次快照的变化。"""
    if not _HAS_STORE:
        print("⚠️ store 模块不可用，diff 功能无法执行")
        return 1

    # 参数校验
    partial_ids = (args.from_id is not None) != (args.to_id is not None)
    if partial_ids:
        print("❌ --from 和 --to 必须同时指定，或都不指定（使用自动最近两次）",
              file=sys.stderr)
        return 1

    if args.from_id is not None and args.to_id is not None:
        old = store_mod.get_collection(args.from_id)
        new = store_mod.get_collection(args.to_id)
        if old is None:
            print(f"❌ 快照 #{args.from_id} 不存在", file=sys.stderr)
            return 1
        if new is None:
            print(f"❌ 快照 #{args.to_id} 不存在", file=sys.stderr)
            return 1
        # 校验 symbol 一致性
        old_sym = (old.get("raw_json") or old).get("symbol", "")
        new_sym = (new.get("raw_json") or new).get("symbol", "")
        if old_sym != args.symbol or new_sym != args.symbol:
            print(f"⚠️ 快照 symbol 不匹配: #{args.from_id}={old_sym}, #{args.to_id}={new_sym}, CLI={args.symbol}",
                  file=sys.stderr)
        # 确保 old 早于 new
        if (old.get("fetched_at", "") > new.get("fetched_at", "")):
            old, new = new, old
            print(f"ⓘ 已自动交换顺序（#{args.to_id} → #{args.from_id}）")
    else:
        pair = store_mod.get_latest_two(args.symbol)
        if pair is None:
            print(f"❌ {args.symbol} 至少需要 2 次 --store 采集才能 diff（当前不足）", file=sys.stderr)
            return 1
        old, new = pair

    diff_result = store_mod.diff_collections(old, new)
    key_diff = store_mod.diff_key_snapshots(old, new)
    diff_result["key_changes"] = key_diff

    # 数据源变化检测（基于 manifest 指纹，向后兼容）
    manifest_diff = _compare_store_manifests(old, new)
    diff_result["source_changes"] = manifest_diff

    if args.emit == "json":
        from lib.json_util import dumps_json
        print(dumps_json(diff_result))
        return 0

    if args.emit == "md":
        _print_diff_md(key_diff, diff_result)
        return 0

    _print_diff_compact(key_diff, diff_result)
    return 0


def _unwrap_raw(raw: dict) -> dict:
    """从 store 记录中提取 raw_json（兼容两种结构）。"""
    r = raw.get("raw_json")
    if isinstance(r, dict):
        return r
    if "dimensions" in raw:
        return raw
    return {}


def _compare_store_manifests(old: dict, new: dict) -> dict | None:
    """对比两次 store 记录的 manifest，返回源级变化摘要。

    向后兼容：旧版无 manifest 的快照返回 None。
    """
    old_raw = _unwrap_raw(old)
    new_raw = _unwrap_raw(new)
    old_manifest = old_raw.get("_meta", {}).get("manifest")
    new_manifest = new_raw.get("_meta", {}).get("manifest")
    if not old_manifest or not new_manifest:
        return None
    try:
        from lib.manifest import compare_manifests
        return compare_manifests(old_manifest, new_manifest)
    except Exception as exc:
        print(f"⚠️ manifest 对比失败: {exc}", file=sys.stderr)
        return None


def _print_source_changes(manifest_diff: dict | None) -> bool:
    """输出数据源变化摘要，返回是否有变化输出。"""
    if manifest_diff is None:
        return False

    added = manifest_diff.get("sources_added", [])
    removed = manifest_diff.get("sources_removed", [])
    changed = manifest_diff.get("sources_changed", [])
    status_changes = manifest_diff.get("status_changes", [])

    if not (added or removed or changed or status_changes):
        return False

    print("## 数据源变化")
    print()
    if added:
        print(f"- 新增源: {', '.join(added)}")
    if removed:
        print(f"- 移除源: {', '.join(removed)}")
    for sc in status_changes:
        print(f"- 状态变化: {sc['source']}: {sc['from']} → {sc['to']}")
    for sc in changed:
        parts = [f"{sc['source']}"]
        if sc.get("fields_added"):
            parts.append(f"新增字段: {', '.join(sc['fields_added'])}")
        if sc.get("fields_removed"):
            parts.append(f"移除字段: {', '.join(sc['fields_removed'])}")
        if sc.get("row_count"):
            rc = sc["row_count"]
            parts.append(f"行数: {rc['from']} → {rc['to']}")
        if sc.get("date_range"):
            dr = sc["date_range"]
            parts.append(f"日期范围: {dr['from']} → {dr['to']}")
        print(f"- 字段变化: {' | '.join(parts)}")
    print()
    return True


_CATEGORY_LABELS = {
    "valuation": "估值",
    "financials": "财务",
    "capital_flow": "资金",
    "technical": "技术",
    "risk": "风险",
}


def _category_label(cat: str) -> str:
    if _HAS_STORE:
        from lib.store import CATEGORY_LABELS
        return CATEGORY_LABELS.get(cat, cat)
    return _CATEGORY_LABELS.get(cat, cat)


def _diff_interval_str(old_at: str, new_at: str) -> str:
    from datetime import datetime
    old_s, new_s = old_at[:19], new_at[:19]
    try:
        old_dt = datetime.fromisoformat(old_s.replace("Z", "+00:00"))
        new_dt = datetime.fromisoformat(new_s.replace("Z", "+00:00"))
        days = (new_dt - old_dt).days
        return f" ({days}天)"
    except (ValueError, TypeError):
        return ""


def _print_key_changes(key_diff: dict) -> bool:
    """输出关键字段变化摘要，返回是否有变化。"""
    categories = key_diff.get("categories") or {}
    if not categories:
        return False
    print("## 关键字段变化")
    print()
    for cat, items in categories.items():
        label = _category_label(cat)
        print(f"### {label}")
        for item in items:
            field = item.get("field", "?")
            old_v, new_v = item.get("old"), item.get("new")
            pct = item.get("pct")
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            print(f"- {field}: {old_v} → {new_v}{pct_str}")
        print()
    return True


def _print_diff_events(key_diff: dict) -> None:
    """输出事件变化摘要。"""
    events_diff = key_diff.get("events")
    if not events_diff:
        return
    count_change = events_diff.get("count_change", 0)
    new_types = events_diff.get("new_types", [])
    removed_types = events_diff.get("removed_types", [])
    window_changed = events_diff.get("window_days_changed")

    parts: list[str] = []
    if window_changed:
        parts.append(
            f"事件窗口: {window_changed.get('old')}日 → {window_changed.get('new')}日",
        )
    if count_change != 0:
        sign = "+" if count_change > 0 else ""
        parts.append(f"事件数量变化: {sign}{count_change}")
    if new_types:
        parts.append(f"新增类型: {', '.join(new_types)}")
    if removed_types:
        parts.append(f"消失类型: {', '.join(removed_types)}")

    if parts:
        print("## 事件变化")
        print()
        for p in parts:
            print(f"- {p}")
        print()


def _print_diff_md(key_diff: dict, diff: dict) -> None:
    """Markdown 格式 diff 输出（按类别分组）。"""
    old_at = key_diff.get("old_at", diff.get("old_at", ""))[:19]
    new_at = key_diff.get("new_at", diff.get("new_at", ""))[:19]
    interval = _diff_interval_str(old_at, new_at)
    symbol = key_diff.get("symbol", diff.get("symbol", "?"))

    print(f"# {symbol} 变化摘要")
    print(f"采集间隔: {old_at} → {new_at}{interval}")
    print()

    if not _print_key_changes(key_diff):
        print("关键字段无显著变化。")
        print()

    _print_diff_events(key_diff)

    _print_source_changes(diff.get("source_changes"))

    _print_diff_dimension_supplement(diff)


def _print_diff_compact(key_diff: dict, diff: dict) -> None:
    """compact 格式 diff 输出。"""
    old_at = key_diff.get("old_at", diff.get("old_at", ""))[:19]
    new_at = key_diff.get("new_at", diff.get("new_at", ""))[:19]
    interval = _diff_interval_str(old_at, new_at)
    symbol = key_diff.get("symbol", diff.get("symbol", "?"))

    print(f"# {symbol} 变化摘要")
    print(f"采集间隔: {old_at} → {new_at}{interval}")
    print()

    if not _print_key_changes(key_diff):
        print("关键字段无显著变化。")
        print()

    _print_diff_events(key_diff)

    _print_source_changes(diff.get("source_changes"))

    _print_diff_dimension_supplement(diff)


def _print_diff_dimension_supplement(diff: dict) -> None:
    """维度级 diff 补充输出。"""
    changed = diff.get("changed", [])
    if changed:
        print("## 维度级变化（补充）")
        print()
        # 按维度分组
        by_dim: dict[str, list[dict]] = {}
        for c in changed:
            dim = c["path"].split(".")[0]
            by_dim.setdefault(dim, []).append(c)

        for dim, items in sorted(by_dim.items()):
            display = dim
            print(f"### {display}")
            for item in items:
                field = item["path"].split(".", 1)[1] if "." in item["path"] else item["path"]
                old_v = item.get("old")
                new_v = item.get("new")
                if old_v is None and new_v is None:
                    # 描述型变更（如新增记录数）
                    desc = item.get("description", "")
                    if desc:
                        print(f"- {field}: {desc}")
                    continue
                pct = item.get("pct")
                pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
                print(f"- {field}: {old_v} → {new_v}{pct_str}")
            print()

    unchanged = diff.get("unchanged", [])
    if unchanged:
        print("## 未变化")
        for dim in unchanged[:10]:
            print(f"- {dim}")
        if len(unchanged) > 10:
            print(f"  ... 共 {len(unchanged)} 个维度")
        print()

    skipped = diff.get("skipped", [])
    if skipped:
        print("## 跳过")
        for s in skipped:
            print(f"- {s.get('dimension', '?')}: {s.get('reason', '?')}")
        print()


def _watchlist_get_result(symbol: str) -> dict:
    """优先读 store 最新快照，否则现场采集。"""
    if _HAS_STORE:
        rows = store_mod.list_collections(limit=1, symbol=symbol)
        if rows:
            rec = store_mod.get_collection(rows[0]["id"])
            if rec and rec.get("raw_json"):
                return rec["raw_json"]
    return collector.collect_all(symbol)


def _watchlist_summary_fields(result: dict) -> dict:
    dims = {d["dimension"]: d for d in result.get("dimensions", [])}
    name = ""
    bi = dims.get("basic_info", {}).get("data", {})
    if isinstance(bi, dict):
        name = bi.get("name") or bi.get("股票简称") or ""
    price, change_pct = None, None
    quote = dims.get("quote", {}).get("data", {})
    if isinstance(quote, dict):
        price = quote.get("price") or quote.get("close")
        change_pct = quote.get("change_pct")
    pe_pct = pb_pct = None
    if _HAS_STORE:
        val = store_mod.extract_key_snapshot(result).get("valuation", {})
        pe_pct, pb_pct = val.get("pe_pct"), val.get("pb_pct")
    return {"name": name, "price": price, "change_pct": change_pct,
            "pe_pct": pe_pct, "pb_pct": pb_pct}


def _watchlist_key_changes_lines(key_diff: dict) -> list[str]:
    if _HAS_STORE:
        from lib.store import format_key_diff_markdown_lines
        return format_key_diff_markdown_lines(key_diff)
    categories = key_diff.get("categories") or {}
    if not categories:
        return ["- 关键字段无显著变化"]
    lines: list[str] = []
    for cat, items in categories.items():
        label = _category_label(cat)
        for item in items:
            field = item.get("field", "?")
            old_v, new_v = item.get("old"), item.get("new")
            pct = item.get("pct")
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"- **{label}** {field}: {old_v} → {new_v}{pct_str}")
    return lines


def _watchlist_needs_live_collect(symbols: list[str]) -> bool:
    """是否有标的缺少 store 快照、将触发现场采集。"""
    if not _HAS_STORE:
        return True
    for sym in symbols:
        if not store_mod.list_collections(limit=1, symbol=sym):
            return True
    return False


def _watchlist_symbol_section(symbol: str) -> list[str]:
    result = _watchlist_get_result(symbol)
    info = _watchlist_summary_fields(result)
    title = f"## {symbol}"
    if info["name"]:
        title += f" {info['name']}"
    lines = [title, ""]
    if info["name"]:
        lines.append(f"- **名称:** {info['name']}")
    if info["price"] is not None:
        chg_s = f" ({info['change_pct']:+.2f}%)" if info["change_pct"] is not None else ""
        lines.append(f"- **最新价:** {info['price']}{chg_s}")
    if info["pe_pct"] is not None:
        lines.append(f"- **PE 历史分位:** {info['pe_pct']:.1f}%")
    if info["pb_pct"] is not None:
        lines.append(f"- **PB 历史分位:** {info['pb_pct']:.1f}%")
    if _HAS_STORE:
        pair = store_mod.get_latest_two(symbol)
        if pair:
            old, new = pair
            key_diff = store_mod.diff_key_snapshots(old, new)
            old_at = key_diff.get("old_at", "")[:19]
            new_at = key_diff.get("new_at", "")[:19]
            interval = _diff_interval_str(old_at, new_at)
            lines.extend(["", f"### 相对上次快照变化 ({old_at} → {new_at}{interval})", ""])
            lines.extend(_watchlist_key_changes_lines(key_diff))
    lines.append("")
    return lines


def cmd_watchlist(args: argparse.Namespace) -> int:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if len(symbols) < 2:
        print("❌ watchlist 至少需要 2 只标的（逗号分隔）", file=sys.stderr)
        return 1
    warn_if_proxy_detected(probe=True)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    body: list[str] = [f"# 观察列表摘要 — {today}", "", f"> 共 {len(symbols)} 只标的"]
    if _watchlist_needs_live_collect(symbols):
        body.append(
            "> ⚠️ 部分标的无 `--store` 历史快照，将触发现场采集（较慢）。"
            "建议先执行 `invest.py collect SYMBOL --store`。"
        )
    body.append("")
    failures = 0
    for sym in symbols:
        try:
            body.extend(_watchlist_symbol_section(sym))
        except Exception as exc:
            failures += 1
            body.extend([f"## {sym} ❌ 采集失败", "", f"> {exc}", ""])
    output = "\n".join(body).rstrip() + "\n"
    if args.outdir:
        outdir = Path(args.outdir).resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        mdpath = outdir / f"watchlist_{today}.md"
        mdpath.write_text(output, encoding="utf-8")
        print(f"📝 Watchlist: {mdpath.resolve()}")
        if failures:
            print(f"⚠️ {failures}/{len(symbols)} 只标的采集失败", file=sys.stderr)
        return 1 if failures == len(symbols) else 0
    print(output, end="")
    return 1 if failures == len(symbols) else 0


def cmd_lint(args: argparse.Namespace) -> int:
    """合规扫描入口。"""
    if not _HAS_LINT:
        print("❌ lint 模块不可用（lib/lint.py 缺失）", file=sys.stderr)
        return 1

    target = Path(args.target)

    if not target.exists():
        print(f"❌ 目标不存在: {target}", file=sys.stderr)
        return 1

    if target.is_file():
        try:
            findings = lint_mod.lint_file(target, profile=args.profile)
        except lint_mod.RulesLoadError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        exit_code = lint_mod.print_results(target.name, findings, fail_on=args.fail_on)
        return exit_code

    if target.is_dir():
        try:
            results = lint_mod.lint_directory(target, profile=args.profile)
        except lint_mod.RulesLoadError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        if not results:
            return 0
        total_blocking = 0
        for fname, findings in results.items():
            lint_mod.print_results(fname, findings, fail_on=args.fail_on)
            total_blocking += lint_mod._count_by_severity(findings, args.fail_on)
        # 全局汇总
        print("---")
        blocking_files = sum(
            1 for findings in results.values()
            if lint_mod._count_by_severity(findings, args.fail_on) > 0
        )
        label = {"warning": "违规（含警告）", "error": "错误"}.get(args.fail_on, "违规")
        print(f"共扫描 {len(results)} 个文件，{blocking_files} 个文件存在{label}")
        return 1 if total_blocking > 0 else 0

    return 0


def cmd_rigor(args: argparse.Namespace) -> int:
    from lib.financial_rigor import has_blocking_failures, run_rigor

    env.print_missing_token_warnings()
    dims = _CLI_DEFAULT_DIMS.split(",")
    result = collector.collect_all(args.symbol, [d.strip() for d in dims if d.strip()])
    cmds: list[str] = []
    if args.verify_all or not args.calc:
        cmds.extend(["verify-market-cap", "verify-valuation", "cross-validate"])
    if args.calc:
        cmds.append("calc")
    reports = run_rigor(result, cmds, calc_expr=args.calc or None)
    for r in reports:
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(r.status, "?")
        print(f"{icon} [{r.command}] {r.field}: {r.detail} (偏差 {r.deviation_pct:.1f}%)")
    if has_blocking_failures(reports, strict=args.strict):
        print("❌ 严格模式：存在 >5% 验算失败", file=sys.stderr)
        return 1
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from lib.report_audit import extract_report, verdict_report
    from pathlib import Path

    path = Path(args.report)
    if not path.exists():
        print(f"❌ 文件不存在: {path}", file=sys.stderr)
        return 1
    if args.extract:
        out = extract_report(path)
        print(f"✅ 已抽取 {out['sampled_points']}/{out['total_points']} 点到 {out['output']}")
        return 0
    if args.verdict:
        v = verdict_report(path)
        print(f"判决: {v['verdict']} (已核验 {v.get('verified', 0)}, 失败 {v.get('failed', 0)}, 待填 {v.get('pending', 0)})")
        return 0 if v["verdict"] == "PASS" else 1
    print("请指定 --extract 或 --verdict", file=sys.stderr)
    return 1


def cmd_check(args: argparse.Namespace) -> int:
    from lib.quality_check import format_quality_check, run_quality_check

    env.print_missing_token_warnings()
    dims = ["basic_info", "financials", "quote", "valuation", "kline"]
    result = collector.collect_all(args.symbol, dims)
    qc = run_quality_check(result)
    print(format_quality_check(qc))
    return 1 if qc["summary"]["overall"] == "fail" else 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    from lib.portfolio_review import format_portfolio_review, load_holdings, review_portfolio
    from pathlib import Path

    holdings = load_holdings(Path(args.holdings))
    result = review_portfolio(holdings, stress=args.stress)
    print(format_portfolio_review(result))
    return 0


def cmd_thesis(args: argparse.Namespace) -> int:
    if not _HAS_STORE:
        print("❌ store 模块不可用", file=sys.stderr)
        return 1
    if args.init:
        r = store_mod.thesis_init(args.symbol)
        print(f"✅ 已初始化 thesis: {args.symbol} · 健康度 {r['health_score']} · {r['state']}")
        return 0
    if args.update:
        existing = store_mod.thesis_get(args.symbol)
        if not existing:
            r = store_mod.thesis_init(args.symbol)
            print(f"✅ 已初始化 thesis: {args.symbol} · 健康度 {r['health_score']} · {r['state']}")
            existing = store_mod.thesis_get(args.symbol)
        assumptions = list(existing.get("assumptions") or [])
        red_lines = list(existing.get("red_lines") or [])
        for aid in getattr(args, "invalidate", None) or []:
            for a in assumptions:
                if a.get("id") == aid:
                    a["valid"] = False
        for rid in getattr(args, "trigger_redline", None) or []:
            for rline in red_lines:
                if rline.get("id") == rid:
                    rline["triggered"] = True
        r = store_mod.thesis_update(args.symbol, assumptions=assumptions, red_lines=red_lines)
        print(f"✅ 已更新 thesis: {args.symbol} · 健康度 {r['health_score']} · {r['state']}")
        return 0
    if args.status or not (args.init or args.update):
        t = store_mod.thesis_get(args.symbol)
        if not t:
            print(f"⚠️ 未找到 {args.symbol} 的 thesis 记录，请先 --init")
            return 1
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0
    return 0


def cmd_shock(args: argparse.Namespace) -> int:
    from lib.events import calc_price_impact_interpolation

    r = calc_price_impact_interpolation(
        pre_price=args.pre_price,
        post_price=args.post_price,
        eps_base=args.eps_base,
        eps_hit=args.eps_hit,
        pe_normal=args.pe_normal,
        pe_stressed=args.pe_stressed,
    )
    sym = args.symbol or "—"
    print(f"# 价格冲击插值 — {sym}")
    print(f"场景: {r['scenario']} · 插值比例: {r['ratio']:.2%} · p_range: {r['p_range']}")
    print(f"V_真={r['v_true']} · V_假={r['v_false']}")
    if r.get("warn"):
        print(f"⚠️ {r['warn']}")
    print(r["disclaimer"])
    return 0


def cmd_risk_reward(args: argparse.Namespace) -> int:
    """DCF 三情景盈亏比分析。"""
    from lib.risk_reward import compute_dcf_risk_reward, format_risk_reward_table

    # 优先从 store 读取最近采集结果
    if args.store and _HAS_STORE:
        rows = store_mod.list_collections(limit=1, symbol=args.symbol)
        if rows:
            collection = store_mod.get_collection(rows[0]["id"])
        else:
            print(f"⚠️ store 中无 {args.symbol} 的采集记录，请先运行 collect --store",
                  file=sys.stderr)
            return 1
    else:
        # 实时采集最小维度集
        from lib.collector import collect_all
        print(f"采集 {args.symbol} 数据...", file=sys.stderr)
        collection = collect_all(args.symbol, dims=["kline", "financials", "basic_info",
                                                      "valuation"])
        if _no_sources_responded(collection.get("summary")):
            print("❌ 采集失败，无可用数据", file=sys.stderr)
            return 1

    result = compute_dcf_risk_reward(
        collection,
        rf_override=args.rf,
        erp_override=args.erp,
        terminal_g_override=args.terminal_g,
    )

    print(format_risk_reward_table(result))
    return 0 if "error" not in result else 1


def cmd_ic(args: argparse.Namespace) -> int:
    """投资委员会决策框架。"""
    from lib.risk_reward import compute_dcf_risk_reward, format_risk_reward_table
    from lib.quality_check import run_quality_check, format_quality_check
    from lib.risk_scanner import risk_report
    from lib.collector import collect_all
    from datetime import datetime

    # 采集数据
    collection = None
    if _HAS_STORE:
        rows = store_mod.list_collections(limit=1, symbol=args.symbol)
        if rows:
            collection = store_mod.get_collection(rows[0]["id"])

    if collection is None:
        print(f"采集 {args.symbol} 数据...", file=sys.stderr)
        collection = collect_all(args.symbol, dims=["kline", "financials",
                                                      "basic_info", "valuation",
                                                      "quote"])

    if (collection.get("summary") or {}).get("available", 0) == 0:
        print("❌ 采集失败，无可用数据", file=sys.stderr)
        return 1

    # 获取基本信息
    from lib.schema import index_dimensions
    dims = index_dimensions(collection)
    basic = (dims.get("basic_info") or {}).get("data") or {}
    name = (basic.get("name") or basic.get("名称") or args.symbol) if isinstance(basic, dict) else args.symbol
    date_str = datetime.now().strftime("%Y-%m-%d")

    # 调用各引擎
    rr = compute_dcf_risk_reward(collection, rf_override=args.rf, erp_override=args.erp)
    qc = run_quality_check(collection)
    risks = risk_report((dims.get("financials") or {}).get("data") or [])

    # 查询假设追踪（thesis）
    verifiable_assumptions = 0
    thesis_info = None
    if _HAS_STORE:
        thesis_info = store_mod.thesis_get(args.symbol)
    if thesis_info:
        assumptions = thesis_info.get("assumptions") or []
        # 可验证假设：valid=True 的假设（存在且被认为成立）
        verifiable_assumptions = sum(1 for a in assumptions if a.get("valid", True))
    else:
        # 无 thesis 数据 → 假设数为 0
        verifiable_assumptions = 0

    # 渲染 IC 决策模板
    # 决策规则（ic-framework.md §决策规则）：
    #   通过: 盈亏比 ≥ 2:1 + 质量检查无否决项 + 关键假设 ≥2 个可验证
    #   否决: 盈亏比 < 1:1 或 质量检查有否决项
    #   灰色: 其余情况（1:1~2:1 或 假设不足）
    rr_ok = "error" not in rr
    qc_pass = (qc.get("summary") or {}).get("overall", "fail") != "fail"
    rr_ratio = rr.get("risk_reward_ratio", 0) if rr_ok else 0
    rr_meets = rr.get("meets_threshold", False) if rr_ok else False
    assumptions_sufficient = verifiable_assumptions >= 2

    if not qc_pass:
        verdict = "❌ 否决"
        veto_reason = "质量检查存在否决项"
    elif rr_ok and rr_ratio < 1.0:
        verdict = "❌ 否决"
        veto_reason = f"盈亏比 {rr_ratio:.1f}:1 < 1:1"
    elif rr_ok and qc_pass and rr_meets and assumptions_sufficient:
        verdict = "✅ 通过"
        veto_reason = ""
    else:
        verdict = "灰色（需补充信息）"
        veto_reason = ""

    print(f"# 投资委员会决策备忘录 — {name} ({args.symbol})")
    print(f"> 决策日期: {date_str} | 引擎: invest-a-stock v0.2.3")
    print(f"> ⚠️ 本备忘录为自动化引擎输出，不构成投资建议。")
    print()

    # 质量检查
    print("## 质量速查")
    qc_output = format_quality_check(qc)
    print(qc_output)
    print()

    # 风险信号
    triggered = [s for s in risks if s.get("triggered")]
    if triggered:
        print(f"## 风险信号（{len(triggered)} 个触发）")
        for s in triggered:
            print(f"- {'🔴' if s.get('severity') == 'critical' else '🟡'} "
                  f"**{s.get('name', '?')}**: {s.get('detail', '')}")
    else:
        print("## 风险信号")
        print("✅ 无触发信号")
    print()

    # 盈亏比
    print(format_risk_reward_table(rr))
    print()

    # 关键假设
    print("## 关键假设")
    if thesis_info:
        print(f"可验证假设: **{verifiable_assumptions}** 个"
              f"（{'≥2 ✓' if assumptions_sufficient else '<2 ✗，需补充'}）")
        for a in (thesis_info.get("assumptions") or []):
            status = "✅" if a.get("valid", True) else "❌"
            checked = a.get("last_check_date") or "未验证"
            print(f"- {status} {a.get('statement', '?')}（置信度: {a.get('confidence', '?')}, "
                  f"上次检查: {checked}）")
    else:
        print(f"可验证假设: **0** 个（<2 ✗，需补充）")
        print(f"> 使用 `invest.py thesis {args.symbol} --update` 初始化假设追踪")
    print()

    # 判决
    print("## 判决")
    print(f"**{verdict}**")
    if verdict.startswith("✅"):
        print("> 盈亏比 ≥ 2:1，质量检查无否决项，关键假设 ≥2 个可验证。")
        print("> 请在深入验证关键假设后自行决策。")
    elif verdict.startswith("❌"):
        print(f"> 否决原因: {veto_reason}")
        print("> 建议等待条件改善后重新评估。")
    else:
        print("> 关键数据不完整或盈亏比处于灰色区间（1:1 ~ 2:1）。")
        print("> 建议补充以下信息后重新评估：")
        if not rr_ok:
            print(f">  - 估值数据: {rr.get('error', '未知错误')}")
        if rr_ok and not rr_meets:
            print(f">  - 盈亏比 {rr_ratio:.1f}:1，未达 2:1 阈值")
        if not qc_pass:
            print(f">  - 质量检查存在否决项")
        if not assumptions_sufficient:
            print(f">  - 关键假设仅 {verifiable_assumptions} 个（需 ≥2 个可验证）")

    print()
    print("> ⚠️ 免责声明：本备忘录由 invest-a-stock 自动化引擎生成。"
          "所有估值数据基于规则代理（非分析师预测）。不构成投资建议。")
    return 0


def _format_steady_block(steady: dict) -> str:
    """R2: 稳态盈利估值块文本渲染（value --steady）。"""
    ste = steady.get("steady") or {}
    lines = ["", "【稳态盈利估值（R2 · 穿越周期视角）】"]
    if not ste.get("available"):
        lines.append(f"  ⚠️ {ste.get('reason', '稳态盈利不可得')}")
        return "\n".join(lines)
    lines.append(f"  年度净利样本: {ste.get('period')}（{ste.get('n_years')} 年, method={ste.get('method')}）")
    lines.append(
        f"  稳态盈利: {ste['steady_earnings']/1e8:.2f} 亿元"
        f"（年度区间 {ste['min']/1e8:.2f}~{ste['max']/1e8:.2f} 亿元）"
    )
    band = steady.get("band")
    mv = steady.get("mv_vs_steady")
    if band:
        lines.append(
            f"  周期中枢 PE: {band['cycle_pe']} | 稳态市值带:"
            f" {band['low']/1e8:.0f}~{band['mid']/1e8:.0f}~{band['high']/1e8:.0f} 亿元（±{band['band_pct']*100:.0f}%）"
        )
    if mv and mv.get("total_mv_yi"):
        if mv["steady_mv_high_yi"] and mv["total_mv_yi"] > mv["steady_mv_high_yi"]:
            over = (mv["total_mv_yi"] / mv["steady_mv_high_yi"] - 1) * 100
            pos = f"高于稳态上沿 {over:.0f}%——历史经验：周期股盈利高点常伴随低 PE 错觉（海力士式），但并非充分条件"
        elif mv["steady_mv_low_yi"] and mv["total_mv_yi"] < mv["steady_mv_low_yi"]:
            under = (mv["steady_mv_low_yi"] / mv["total_mv_yi"] - 1) * 100
            pos = f"低于稳态下沿 {under:.0f}%——穿越周期视角存在低估"
        else:
            pos = "处于稳态市值带内"
        lines.append(f"  当期市值 {mv['total_mv_yi']:.0f} 亿元 vs 稳态带: {pos}")
    lines.append("  （稳态估值为多情景参考，非目标价；概率权重由用户自设）")
    return "\n".join(lines)


def _format_ev_ebitda_block(ev: dict) -> str:
    """R3: EV/EBITDA 桥接表文本渲染（value --ev-ebitda）。"""
    lines = ["", "【EV/EBITDA 企业价值桥接（R3）】"]
    if ev.get("exempt"):
        lines.append(f"  ⚠️ {ev.get('reason', '不适用')}")
        return "\n".join(lines)
    if not ev.get("available"):
        lines.append(f"  ⚠️ 桥接数据不可得（缺失: {', '.join(ev.get('missing') or [])}）")
        if ev.get("ebitda_note"):
            lines.append(f"  · {ev['ebitda_note']}")
        if ev.get("note"):
            lines.append(f"  · {ev['note']}")
        return "\n".join(lines)
    b = ev["bridge"]
    lines.append("  桥接表（逐项可审计）:")
    lines.append(f"    - 市值: {b['mv_yi']} 亿元")
    if b["interest_debt_yi"] is not None:
        lines.append(f"    + 有息负债: {b['interest_debt_yi']} 亿元（短贷+长贷+应付债券）")
    else:
        lines.append(f"    + 有息负债: 不可得（降级净现金口径）")
    lines.append(f"    - 现金: {b['cash_yi']} 亿元")
    lines.append(f"    = EV: {b['ev_yi']} 亿元")
    period_s = f"（{ev['ebitda_period']} 年报期）" if ev.get("ebitda_period") else ""
    lines.append(f"  EBITDA: {ev['ebitda_yi']} 亿元{period_s} → EV/EBITDA = {ev['ev_ebitda']}x")
    if ev.get("note"):
        lines.append(f"  ⚠️ {ev['note']}")
    if ev.get("takeover_payback_years"):
        lines.append(f"  私有化检验（研究问题）: 回本年限 ≈ {ev['takeover_payback_years']} 年")
        lines.append(f"    · {ev['takeover_note']}")
    return "\n".join(lines)


def cmd_classify(args: argparse.Namespace) -> int:
    """R1: 收益驱动假设分类（研究路径分流）。"""
    try:
        from lib.income_driver import classify_income_driver, format_classify_result
        from valuation_calc import get_annual_net_profit
        from lib.tushare_client import TushareClient
    except ImportError as exc:
        print(f"⚠️ classify 依赖模块不可用: {exc}", file=sys.stderr)
        return 1
    ts = TushareClient()
    ts_code = _fmt_code(args.symbol) if "_fmt_code" in globals() else None
    if ts_code is None:
        from valuation_calc import _fmt_code
        ts_code = _fmt_code(args.symbol)
    annual = get_annual_net_profit(ts, ts_code)
    if not annual:
        print("⚠️ 年度净利序列不可得（income 表查询为空），无法分类", file=sys.stderr)
        return 1
    # fina_indicator（fcff 等）由 collector 同款查询补入
    fin_rows: list[dict] = []
    try:
        from lib.collector import _q_tushare_financials
        fin_rows = _q_tushare_financials(args.symbol) or []
    except Exception:
        pass
    result = classify_income_driver(
        annual, fin_rows,
        div_years=args.div_years,
        div_yield=args.div_yield,
        refi_times=args.refi_times,
    )
    if args.emit == "json":
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_classify_result(result))
    return 0


def cmd_value(args: argparse.Namespace) -> int:
    """科学估值：多方法交叉估值（PE/PB/盈利收益/隐含增长/ROE-PB 匹配）。"""
    try:
        from valuation_calc import run_valuation, format_output
    except ImportError:
        print("⚠️ valuation_calc 模块不可用", file=sys.stderr)
        return 1

    result = run_valuation(
        symbol=args.symbol,
        rf_override=args.rf,
        erp_override=args.erp,
        steady=getattr(args, "steady", False),
        cycle_start=getattr(args, "cycle_start", None),
        cycle_end=getattr(args, "cycle_end", None),
        cycle_method=getattr(args, "cycle_method", "median"),
        cycle_pe=getattr(args, "cycle_pe", None),
        ev_ebitda=getattr(args, "ev_ebitda", False),
        ev_ebitda_industry=getattr(args, "industry", None),
    )

    if args.emit == "json":
        import json as _json
        print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(format_output(result))
        if result.steady:
            print(_format_steady_block(result.steady))
        if result.ev_ebitda:
            print(_format_ev_ebitda_block(result.ev_ebitda))

    if args.store:
        if not _HAS_STORE:
            print("⚠️ store 模块不可用，无法存储", file=sys.stderr)
        else:
            val_id = store_mod.save_valuation(result.to_dict())
            print(f"💾 已存入估值记录 (id={val_id})")

    if result.errors:
        critical = [e for e in result.errors if "失败" in e or "不可得" in e]
        if len(critical) >= 3:
            return 1
    return 0


def cmd_market_status(args: argparse.Namespace) -> int:
    """市场微观结构快照：杠杆/广度/情绪/估值温度；或 R5 行业景气状态卡（--industry）。

    --save  采集并保存当日快照
    --days  趋势表周期（默认 5 天）
    --json  输出原始 JSON
    --industry  输出行业景气状态卡（独立输出，不进入 snapshot 流程）
    """
    import json as _json
    if getattr(args, "industry", ""):
        try:
            from lib.climate import build_industry_climate, format_climate_card
            card = build_industry_climate(args.industry)
            if args.json:
                print(_json.dumps(card, ensure_ascii=False, indent=2, default=str))
                return 0
            print(format_climate_card(card))
            return 0
        except Exception as exc:
            print(f"⚠️ 行业景气状态卡失败: {exc}", file=sys.stderr)
            return 1
    try:
        from market_microstructure import snapshot, save_snapshot, latest_snapshot, load_history
    except ImportError:
        print("⚠️ market_microstructure 模块不可用", file=sys.stderr)
        return 1

    if args.save:
        # 确保 market_snapshots 表已创建（首次运行需要）
        if _HAS_STORE:
            store_mod.init_db()
        snap = save_snapshot()
        if snap is None:
            print("⚠️ 非交易日或数据缺失，已跳过保存")
            return 0
        if args.json:
            print(_json.dumps(snap, ensure_ascii=False, indent=2, default=str))
            return 0
        print("✅ 市场快照已保存")
        _print_env_labels(snap)
        return 0

    # 读取模式：优先最新持久化快照，降级当日实时快照
    latest = latest_snapshot()
    if latest and not args.save:
        snap = latest
    else:
        snap = snapshot()

    errors = snap.pop("_errors", [])
    if errors:
        for e in errors:
            print(f"⚠️ {e}", file=sys.stderr)

    if args.json:
        print(_json.dumps(snap, ensure_ascii=False, indent=2, default=str))
        return 1 if len(errors) >= 5 else 0

    # 环境标签
    _print_env_labels(snap)
    print()

    # 关键指标
    print("━━━ Tier 1 原始指标 ━━━")
    print(f"  两融余额:   {_fmt(snap.get('margin_balance'), '亿')}")
    print(f"  融资买入额: {_fmt(snap.get('margin_buy_amount'), '亿')}")
    print(f"  涨跌比:     {_fmt(snap.get('ad_ratio'))}")
    print(f"  涨停/跌停:  {snap.get('limit_up_count', '-')} / {snap.get('limit_down_count', '-')}")
    print(f"  全市场成交: {_fmt(snap.get('total_turnover'), '亿')}")
    print()

    # Tier 2
    print("━━━ Tier 2 衍生指标 ━━━")
    mtm = snap.get("margin_to_mcap")
    print(f"  两融/流通市值: {_fmt(mtm, '%') if mtm is not None else '待积累'}")
    mbt = snap.get("margin_buy_to_turnover")
    print(f"  融资买入/成交: {_fmt(mbt, '%') if mbt is not None else '待积累'}")
    m20 = snap.get("margin_20d_change")
    print(f"  融资20日变化:  {_fmt_pct(m20)}")
    ad5 = snap.get("ad_ratio_5d_ma")
    print(f"  涨跌比5日均值: {_fmt(ad5)}")
    ld_pct = snap.get("limit_down_20d_pct")
    print(f"  跌停20日分位:  {_fmt(ld_pct, '%') if ld_pct is not None else '待积累'}")
    print()

    # Tier 3
    print("━━━ Tier 3 估值温度 ━━━")
    print(f"  ERP (股权风险溢价): {_fmt(snap.get('erp'), '%')}")
    print(f"  50ETF PCR:          {_fmt(snap.get('pcr'))}")
    bb = snap.get("below_book_pct")
    print(f"  破净率:             {_fmt(bb, '%') if bb is not None else '—'}")
    print()

    # 近 N 日趋势 mini-table
    history = load_history(args.days)
    if history:
        print(f"━━━ 近 {args.days} 日趋势 ━━━")
        print(f"  {'日期':<12} {'两融(亿)':>10} {'涨跌比':>8} {'涨停':>5} {'跌停':>5} {'成交(亿)':>10}")
        for h in history[-args.days:]:
            print(
                f"  {h['date']:<12} "
                f"{h.get('margin_balance') or '—':>10} "
                f"{h.get('ad_ratio') or '—':>8} "
                f"{h.get('limit_up_count') or 0:>5} "
                f"{h.get('limit_down_count') or 0:>5} "
                f"{h.get('total_turnover') or '—':>10}"
            )
    else:
        print("⚠️ 历史数据为空（首次使用？运行 market-status --save 积累首条记录）")

    return 0


def _print_env_labels(snap: dict) -> None:
    """打印环境标签条。

    优先从独立字段读取（实时快照），
    缺失时从 env_label JSON 降级解析（DB 持久化快照）。
    """
    lev = snap.get("label_leverage") or ""
    brd = snap.get("label_breadth") or ""
    sent = snap.get("label_sentiment") or ""
    cap = snap.get("label_capital_flow") or ""
    summary = ""
    env_str = snap.get("env_label")
    if env_str:
        try:
            env = __import__("json").loads(env_str)
            # 独立字段缺失时从 JSON 解析
            if not lev:
                lev = env.get("leverage", "")
            if not brd:
                brd = env.get("breadth", "")
            if not sent:
                sent = env.get("sentiment", "")
            if not cap:
                cap = env.get("capital_flow", "")
            if not summary:
                summary = env.get("summary", "")
        except Exception:
            pass

    print()
    print("┌──────────────────────────────────────────────────┐")
    print(f"│ 🧊 杠杆: {lev or '—'}")
    print(f"│ 🌤  广度: {brd or '—'}")
    print(f"│ ⚠️  情绪: {sent or '—'}")
    print(f"│ 💵 资金: {cap or '—'}")
    if summary:
        print(f"│ → 综合: {summary}")
    print("└──────────────────────────────────────────────────┘")


def _fmt(val, unit: str = "") -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.2f}{unit}"
    return f"{val}{unit}"


def _fmt_pct(val) -> str:
    if val is None:
        return "待积累"
    arrow = "↑" if val > 0 else ("↓" if val < 0 else "→")
    return f"{arrow} {abs(val):.1f}%"


def cmd_etf_flow(args: argparse.Namespace) -> int:
    """ETF 份额变化趋势 CLI。"""
    symbol = args.symbol.strip().zfill(6)

    if args.save:
        from etf_data import save_etf_share_snapshot as _save
        snap = _save(symbol)
        if snap is None:
            print(f"⚠️ {symbol} 非交易日或数据不可得，跳过保存", file=sys.stderr)
            return 1
        msg = f"✅ {symbol} 份额快照已保存: {snap['shares']:.0f} 份, AUM {snap['aum']} 亿"
        if args.json:
            print(msg, file=sys.stderr)
            return 0
        else:
            print(msg)
            return 0

    from etf_data import etf_share_flow as _flow
    flow = _flow(symbol, days=args.days)

    if args.json:
        import json
        print(json.dumps(flow, ensure_ascii=False, default=str))
    else:
        hc = flow.get("history_count", 0)
        if hc == 0:
            note = flow.get("note", "无历史数据")
            print(f"⚠️ {symbol}: {note}（运行 etf-flow {symbol} --save 积累首条记录）")
            return 1

        print(f"\n📊 {symbol} ETF 份额变化趋势（近 {hc} 个交易日）\n")
        print(f"  最新份额: {flow['shares_current']:.0f} 份")
        print(f"  最新 AUM: {flow['aum_current']} 亿")
        print()
        print(f"  {'窗口':<8} {'份额变动':>14} {'估算资金流':>14}")
        print(f"  {'-' * 8} {'-' * 14} {'-' * 14}")
        for w, label in [(5, "5 日"), (20, "20 日"), (60, "60 日")]:
            sc = flow.get(f"share_change_{w}d")
            fe = flow.get(f"flow_est_{w}d")
            sc_str = f"{sc:+.0f}" if sc is not None else "待积累"
            fe_str = f"{fe:+.2f} 亿" if fe is not None else "待积累"
            print(f"  {label:<8} {sc_str:>14}  {fe_str:>14}")

    return 0


def cmd_catalyst(args: argparse.Namespace) -> int:
    """催化剂日历 CLI。"""
    from lib.catalyst import collect_catalyst_events, format_catalyst_calendar

    print(f"采集 {args.symbol} 未来 {args.days} 天催化剂...", file=sys.stderr)
    try:
        events = collect_catalyst_events(args.symbol, days=args.days)
    except Exception as e:
        print(f"❌ 催化剂采集失败: {e}", file=sys.stderr)
        return 1

    if not events:
        print("⚠️ 未获取到催化剂事件（可能数据源不可用）", file=sys.stderr)

    print(format_catalyst_calendar(events, symbol=args.symbol))
    return 0



def main() -> int:
    env.ensure_env_loaded()
    # 全局 socket 兜底超时：必须在任何网络调用之前（.env 注入后读取才生效）。
    # 覆盖 baostock/tickflow/akshare 无 timeout 参数的接口，防无限挂起。
    env.configure_socket_timeout()
    from lib.logutil import setup_logging
    setup_logging()  # INVEST_DEV=1 时启用开发日志；release 零文件 I/O
    args = build_parser().parse_args()
    if args.command == "collect":
        return cmd_collect(args)
    elif args.command == "report":
        return cmd_report(args)
    elif args.command == "compare":
        return cmd_compare(args)
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "watchlist":
        return cmd_watchlist(args)
    elif args.command == "diagnose":
        return cmd_diagnose(args)
    elif args.command == "lint":
        return cmd_lint(args)
    elif args.command == "peer":
        return cmd_peer(args)
    elif args.command == "store":
        return cmd_store(args)
    elif args.command == "plan":
        return cmd_plan(args)
    elif args.command == "evidence":
        return cmd_evidence(args)
    elif args.command == "analyze":
        return cmd_analyze(args)
    elif args.command == "synthesize":
        return cmd_synthesize(args)
    elif args.command == "rigor":
        return cmd_rigor(args)
    elif args.command == "audit":
        return cmd_audit(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "portfolio":
        return cmd_portfolio(args)
    elif args.command == "thesis":
        return cmd_thesis(args)
    elif args.command == "shock":
        return cmd_shock(args)
    elif args.command == "risk-reward":
        return cmd_risk_reward(args)
    elif args.command == "ic":
        return cmd_ic(args)
    elif args.command == "value":
        return cmd_value(args)
    elif args.command == "classify":
        return cmd_classify(args)
    elif args.command == "market-status":
        return cmd_market_status(args)
    elif args.command == "etf-flow":
        return cmd_etf_flow(args)
    elif args.command == "catalyst":
        return cmd_catalyst(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
