#!/usr/bin/env python3
"""合并多个 collection JSON 并进行跨源交叉验证。

使用方式:
    uv run python skills/invest-a-stock/scripts/merge_collections.py \
        /tmp/002466_collect_A.json \
        /tmp/002466_collect_B.json \
        /tmp/002466_collect_C.json \
        -o /tmp/002466_merged.json

验证规则:
    - 关键字段（ROE/EPS/毛利率/PE/PB）跨源对比
    - 差异 <5% → 通过，取均值
    - 差异 5-20% → 标注分歧，保留两者
    - 差异 >20% → 🔴 严重分歧，建议触发 tie-breaker

输出:
    - 合并后的 collection JSON（含 _cross_validation 注释块）
    - 分歧报告（stdout）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))

from lib.data_util import has_data, merge_first_non_empty  # noqa: E402
from lib.nums import safe_float  # noqa: E402

logger = logging.getLogger(__name__)

# 关键交叉验证字段（维度 → 比较字段列表）
CRITICAL_FIELDS: dict[str, list[str]] = {
    "financials": ["roe", "eps", "grossprofit_margin", "netprofit_margin"],
    "valuation": ["pe_ttm", "pb", "ps_ttm"],
    "quote": ["close", "total_mv"],
}

# 差异阈值
THRESHOLD_OK = 0.05       # <5% → OK
THRESHOLD_WARN = 0.20     # 5-20% → 标注分歧
# >20% → 🔴 严重分歧


def _diff_pct(a: float, b: float) -> float:
    """相对差异百分比（基于均值，委托 canonical schema.relative_diff_pct）。

    avg 传 (|a|+|b|)/2（绝对值均值）——同号对与有符号均值数学等价；
    异号对完全复刻旧实现（|a-b|/((|a|+|b|)/2)，上限 100% 或 200% 精确值），
    避免有符号均值近抵消导致的爆炸（review 第三轮 #3：5 vs -4.9 →
    19800% 假警报）。
    """
    from lib.schema import relative_diff_pct

    d = relative_diff_pct(max(a, b), min(a, b), (abs(a) + abs(b)) / 2)
    if d is None:
        return 0.0 if abs(a - b) < 1e-12 else 100.0
    return d * 100.0


def _meta_of(dim) -> dict:
    """维度 _meta 安全取值：缺失或非 dict（如 null）一律视为 {}。"""
    m = dim.get("_meta") if isinstance(dim, dict) else None
    return m if isinstance(m, dict) else {}


def load_collection(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def extract_latest_financial(fin_data) -> dict:
    """从 financials 数据提取最新一期的关键字段。

    按 end_date 显式取最大报告期（兼容两种行序：Tushare fina_indicator
    返回 newest-first 未排序；akshare THS 按报告期升序），避免跨源
    比较时错配到最旧财季（记录数不等时 fin_data[-1] 语义分裂）。
    """
    if isinstance(fin_data, list) and fin_data:
        rows = [r for r in fin_data if isinstance(r, dict)]
        if not rows:
            return {}
        return max(
            rows,
            key=lambda r: str(r.get("end_date") or r.get("report_date")
                              or r.get("trade_date") or ""),
        )
    if isinstance(fin_data, dict):
        return fin_data
    return {}


def extract_valuation_snapshot(val_data) -> dict:
    """从 valuation 数据提取当前快照。

    按 trade_date 显式取最大日期（Tushare daily_basic 已升序，但其他
    源行序不定，取值不能依赖 [-1] 约定）。
    """
    if isinstance(val_data, list) and val_data:
        rows = [r for r in val_data if isinstance(r, dict)]
        if not rows:
            return {}
        return max(
            rows,
            key=lambda r: str(r.get("trade_date") or r.get("end_date") or ""),
        )
    if isinstance(val_data, dict):
        return val_data
    return {}


def cross_validate_dim(
    dim_name: str,
    data_a: dict | list | None,
    data_b: dict | list | None,
    source_a: str,
    source_b: str,
) -> dict[str, Any]:
    """对单个维度进行交叉验证。

    Returns:
        {
            "dimension": str,
            "fields": {field: {"a": val, "b": val, "diff_pct": float, "status": str}},
            "overall_status": "pass" | "warn" | "fail",
            "recommendation": str,
        }
    """
    fields_result = {}
    max_diff = 0.0
    worst_status = "pass"

    # 根据维度类型提取数据
    if dim_name == "financials":
        row_a = extract_latest_financial(data_a)
        row_b = extract_latest_financial(data_b)
    elif dim_name == "valuation":
        row_a = extract_valuation_snapshot(data_a)
        row_b = extract_valuation_snapshot(data_b)
    else:
        row_a = data_a if isinstance(data_a, dict) else {}
        row_b = data_b if isinstance(data_b, dict) else {}

    for field in CRITICAL_FIELDS.get(dim_name, []):
        val_a = safe_float(row_a.get(field))
        val_b = safe_float(row_b.get(field))

        if val_a is None and val_b is None:
            fields_result[field] = {"a": None, "b": None, "diff_pct": None,
                                     "status": "both_missing"}
            continue
        if val_a is None:
            fields_result[field] = {"a": None, "b": round(val_b, 4), "diff_pct": None,
                                     "status": "single_source"}
            continue
        if val_b is None:
            fields_result[field] = {"a": round(val_a, 4), "b": None, "diff_pct": None,
                                     "status": "single_source"}
            continue

        diff = _diff_pct(val_a, val_b)
        if diff < THRESHOLD_OK:
            status = "pass"
        elif diff < THRESHOLD_WARN:
            status = "warn"
        else:
            status = "fail"

        max_diff = max(max_diff, diff)
        if status == "fail":
            worst_status = "fail"
        elif status == "warn" and worst_status != "fail":
            worst_status = "warn"

        fields_result[field] = {
            "a": round(val_a, 4),
            "b": round(val_b, 4),
            "diff_pct": round(diff, 2),
            "status": status,
        }

    recommendation = ""
    if worst_status == "fail":
        recommendation = (
            f"🔴 {dim_name} 跨源严重分歧（最大差异 {max_diff:.1f}%），"
            "建议触发 tie-breaker（第三源 baostock）"
        )
    elif worst_status == "warn":
        recommendation = (
            f"🟡 {dim_name} 跨源存在差异（最大差异 {max_diff:.1f}%），"
            "保留双源数据并标注"
        )
    else:
        recommendation = f"🟢 {dim_name} 跨源一致（最大差异 {max_diff:.1f}%）"

    return {
        "dimension": dim_name,
        "source_a": source_a,
        "source_b": source_b,
        "fields": fields_result,
        "max_diff_pct": round(max_diff, 2),
        "overall_status": worst_status,
        "recommendation": recommendation,
    }


def merge_collections(collections: list[dict]) -> dict:
    """合并多个 collection JSON 为一。

    规则:
    - 同一维度出现在多个 collection 中 → 保留（标记 multi_source）
    - 只出现在一个中 → 直接使用
    - 维度名冲突 → 重命名为 {dim}_{source}
    """
    if not collections:
        raise ValueError("merge_collections: collections 不能为空（至少需要一份 collection）")
    for i, coll in enumerate(collections):
        if not isinstance(coll, dict):
            raise ValueError(f"merge_collections: collections[{i}] 不是 dict（{type(coll).__name__}）")

    merged_dims: dict[str, list[dict]] = {}
    sources: list[str] = []

    for coll in collections:
        src = coll.get("symbol", "?")
        sources.append(src)
        for dim in coll.get("dimensions", []):
            dim_name = dim.get("dimension", "unknown")
            if dim_name not in merged_dims:
                merged_dims[dim_name] = []
            merged_dims[dim_name].append(dim)

    # 跨 collection symbol 不一致：取第一份（调用方应自检批次来源）
    distinct_symbols = {s for s in sources if s}
    if len(distinct_symbols) > 1:
        logger.warning("merge_collections: 输入 symbol 不一致 %s，merged 取第一份",
                       sorted(distinct_symbols))

    # 构建合并结果
    result_dimensions = []
    cv_results = []  # cross-validation results

    for dim_name, dims in merged_dims.items():
        if len(dims) == 1:
            result_dimensions.append(dims[0])
        else:
            # 多源 → 选取首个有数据的维度作为主数据（连带 status/_meta 跟随，
            # 避免 status 与 data 错配），标注多源
            data_bearing = [d for d in dims if has_data(d.get("data"))]
            chosen = data_bearing[0] if data_bearing else dims[0]
            # 浅拷贝 + 全新 _meta：后续只写 _meta 键与顶层 research_summary
            # （整体替换，不原地改 dict），data 载荷共享但从不被原地修改 →
            # D7 输入隔离保持；deepcopy 整维数据载荷纯属浪费
            primary = dict(chosen)
            primary["_meta"] = dict(chosen.get("_meta") or {})
            alt_sources = [
                {
                    "source": _meta_of(d).get("source", "unknown"),
                    "fetched_at": _meta_of(d).get("fetched_at", ""),
                }
                for d in dims
                if d is not chosen
            ]
            primary["_meta"]["alternative_sources"] = alt_sources
            primary["_meta"]["multi_source_count"] = len(dims)
            # 合并 all_sources：不同采集器可能覆盖不同源（如 research 维度
            # 一个采集器拿到 report_rc、另一个拿到 forecast）——按 source 名
            # 并集。去重优先级：chosen 维度条目（payload == primary.data）>
            # 有数据条目 > 失败条目（保留供 provenance 展示；evidence/
            # render_utils 已有 ❌ 失败渲染分支，financial_rigor/fusion/
            # valuation/manifest 均按 success/data_available 过滤）。
            # 不重算 source_count/multi_source/cross_validation：chosen 维度
            # 自身取值（浅拷贝已保留，schema 口径 = 有数据的源数）描述
            # primary.data 的真实验证——并集计数会虚增 rerank 的
            # MULTI_SOURCE_BONUS（+5）并跳过 SINGLE_SOURCE（-10）。
            seen: dict[str, dict] = {}
            for s in _meta_of(chosen).get("all_sources", []):
                seen.setdefault(s.get("source") or "unknown", s)
            for d in dims:
                for s in _meta_of(d).get("all_sources", []):
                    name = s.get("source") or "unknown"
                    if name not in seen and has_data(s.get("data")):
                        seen[name] = s
            for d in dims:
                for s in _meta_of(d).get("all_sources", []):
                    name = s.get("source") or "unknown"
                    if name not in seen:
                        seen[name] = s
            if seen:
                primary["_meta"]["all_sources"] = list(seen.values())
            # 按字段形状（而非维度名硬编码）逐 key 合并 research_summary：
            # 各渲染消费者（_concise/_render_dcf/analysis_templates）只读
            # research_summary，all_sources 并集不参与渲染——B 采集器独有的
            # forecast/业绩预告此前在 merged 报告中静默消失。
            # 只从"有数据的维度"合并（失败采集器的骨架默认值
            # latest_ratings:[]/profit_forecasts:[]/status:'no_data' 不得遮蔽
            # 健康采集器的真实数据）；空容器（[]/{}/''）由共享
            # merge_first_non_empty 视为缺失。
            merged_summary = merge_first_non_empty(
                [d.get("research_summary") for d in data_bearing])
            if merged_summary:
                primary["research_summary"] = merged_summary
            result_dimensions.append(primary)

            # 交叉验证：对比两个"有数据的"源（chosen 之后首个有数据维度）；
            # 此前硬编码 dims[0] vs dims[1]，dims[0] 无数据时真实分歧静默消失
            if len(data_bearing) >= 2 and dim_name in CRITICAL_FIELDS:
                cv = cross_validate_dim(
                    dim_name,
                    data_bearing[0].get("data"),
                    data_bearing[1].get("data"),
                    _meta_of(data_bearing[0]).get("source", "unknown"),
                    _meta_of(data_bearing[1]).get("source", "unknown"),
                )
                if cv["max_diff_pct"] > 0:
                    cv_results.append(cv)

    # 汇总统计
    all_dim_names = sorted(merged_dims.keys())
    multi_source_dims = [name for name, ds in merged_dims.items() if len(ds) >= 2]

    merged = {
        "symbol": next(
            (c.get("symbol") for c in collections
             if isinstance(c, dict) and c.get("symbol")),
            "?",
        ),
        # v0.2.7 P2-2：aware UTC（与 collector._assemble_result 同口径），
        # 渲染侧按 UTC 假定转换北京时间——naive 本地时区会双重偏移
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": result_dimensions,
        "summary": {
            "total": len(all_dim_names),
            # 与 collect_all 共用 lib.data_util.has_data 口径（空 list/dict 不计
            # available）：同一 collection 的两个消费者计数一致
            "available": sum(
                1 for d in result_dimensions
                if has_data(d.get("data"))
                and d.get("status") in ("available", "partial")
            ),
            "multi_source_count": len(multi_source_dims),
            "multi_source_dims": multi_source_dims,
            "sources_merged": len(collections),
        },
        "_cross_validation": {
            "results": cv_results,
            "need_tiebreaker": any(cv["overall_status"] == "fail" for cv in cv_results),
            "tiebreaker_dims": [
                cv["dimension"] for cv in cv_results if cv["overall_status"] == "fail"
            ],
        },
    }
    return merged


def print_cv_report(cv_results: list[dict]) -> None:
    """打印交叉验证报告。"""
    if not cv_results:
        print("✅ 无可对比的多源维度")
        return

    print(f"\n{'='*60}")
    print("  交叉验证报告")
    print(f"{'='*60}")

    all_pass = True
    for cv in cv_results:
        icon = {"pass": "✅", "warn": "🟡", "fail": "🔴"}.get(cv["overall_status"], "?")
        if cv["overall_status"] != "pass":
            all_pass = False
        print(f"\n{icon} {cv['dimension']} ({cv['source_a']} vs {cv['source_b']})")
        print(f"   {cv['recommendation']}")
        for field, result in cv["fields"].items():
            if result.get("status") == "both_missing":
                continue
            s_icon = {"pass": "  ", "warn": "⚠️", "fail": "🔴", "single_source": "📡"}.get(
                result["status"], "?"
            )
            diff_str = f"差异 {result['diff_pct']:.1f}%" if result.get("diff_pct") is not None else "单源"
            print(f"   {s_icon} {field}: {result.get('a', '?')} vs {result.get('b', '?')} ({diff_str})")

    if all_pass:
        print(f"\n✅ 所有关键字段跨源一致 — 数据可信度高")
    else:
        print(f"\n⚠️  存在跨源分歧 — 见上表，严重分歧建议触发 tie-breaker")


def main():
    parser = argparse.ArgumentParser(description="合并多个 collection JSON 并交叉验证")
    parser.add_argument("files", nargs="+", help="collection JSON 文件路径（至少 2 个）")
    parser.add_argument("-o", "--output", help="输出合并 JSON 路径")
    parser.add_argument("--json", action="store_true", help="仅输出合并 JSON（不打印报告）")
    args = parser.parse_args()

    if len(args.files) < 1:
        print("错误: 至少需要一个 collection JSON 文件", file=sys.stderr)
        sys.exit(1)

    collections = []
    for fp in args.files:
        try:
            collections.append(load_collection(fp))
        except Exception as e:
            print(f"错误: 无法读取 {fp}: {e}", file=sys.stderr)
            sys.exit(1)

    merged = merge_collections(collections)

    # 打印报告
    if not args.json:
        cv_results = merged.get("_cross_validation", {}).get("results", [])
        print(f"合并 {len(collections)} 个 collection → "
              f"{merged['summary']['total']} 维度 "
              f"（{merged['summary']['multi_source_count']} 个多源交叉验证）")
        print_cv_report(cv_results)

    # 保存
    if args.output:
        with open(args.output, "w") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n📦 合并结果: {args.output}")
    elif args.json:
        print(json.dumps(merged, ensure_ascii=False, indent=2, default=str))
    else:
        print("\n⚠️  未指定 -o 输出路径，合并结果未保存到文件")


if __name__ == "__main__":
    main()
