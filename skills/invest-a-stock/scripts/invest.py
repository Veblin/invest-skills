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

    pval = sub.add_parser("value", help="科学估值：多方法交叉（PE/PB/盈利收益/隐含增长/ROE-PB匹配）")
    pval.add_argument("symbol", help="股票代码，如 002466")
    pval.add_argument("--rf", type=float, help="无风险利率（小数），默认自动获取中国10Y国债")
    pval.add_argument("--erp", type=float, default=0.06, help="股权风险溢价（默认 0.06）")
    pval.add_argument("--store", action="store_true", help="结果存入数据库便于回溯")
    pval.add_argument("--emit", default="text", choices=["text", "json"])

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
    result = collector.collect_all(args.symbol, dims, **_collect_kwargs(args))
    _warn_degraded_collection(result)
    if result["summary"]["available"] == 0:
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
    """生成报告完整路径：{outdir}/{subdir}/{date}.md。"""
    date = ts[:10]  # 仅日期部分，如 2026-07-05
    report_dir = outdir / subdir
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir / f"{date}.md"


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
    if getattr(args, "strict_rigor", False):
        result.setdefault("_meta", {})["strict_rigor"] = True
    _warn_degraded_collection(result)
    if result["summary"]["available"] == 0:
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
    if result["summary"]["available"] == 0:
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

    if result.get("summary", {}).get("available", 0) == 0:
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
    )

    if args.emit == "json":
        import json as _json
        print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(format_output(result))

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


def main() -> int:
    env.ensure_env_loaded()
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
    elif args.command == "value":
        return cmd_value(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
