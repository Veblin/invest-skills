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
import sys
from datetime import datetime
from typing import Any

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


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _diff_pct(a: float, b: float) -> float:
    """相对差异百分比（基于均值）。"""
    avg = (abs(a) + abs(b)) / 2.0
    if avg < 1e-12:
        return 0.0 if abs(a - b) < 1e-12 else 100.0
    return abs(a - b) / avg * 100.0


def load_collection(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def extract_dim_data(collection: dict, dim_name: str) -> dict | list | None:
    """从 collection JSON 提取指定维度的 data 字段。"""
    for dim in collection.get("dimensions", []):
        if dim.get("dimension") == dim_name:
            return dim.get("data")
    return None


def extract_latest_financial(fin_data) -> dict:
    """从 financials 数据提取最新一期的关键字段。"""
    if isinstance(fin_data, list) and fin_data:
        return fin_data[-1] if isinstance(fin_data[-1], dict) else {}
    if isinstance(fin_data, dict):
        return fin_data
    return {}


def extract_valuation_snapshot(val_data) -> dict:
    """从 valuation 数据提取当前快照。"""
    if isinstance(val_data, list) and val_data:
        return val_data[-1] if isinstance(val_data[-1], dict) else {}
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
        val_a = _safe_float(row_a.get(field))
        val_b = _safe_float(row_b.get(field))

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

    # 构建合并结果
    result_dimensions = []
    cv_results = []  # cross-validation results

    for dim_name, dims in merged_dims.items():
        if len(dims) == 1:
            result_dimensions.append(dims[0])
        else:
            # 多源 → 保留第一条作为主数据，标注多源
            primary = dims[0].copy()
            alt_sources = [
                {
                    "source": d.get("_meta", {}).get("source", "unknown"),
                    "fetched_at": d.get("_meta", {}).get("fetched_at", ""),
                }
                for d in dims[1:]
            ]
            primary.setdefault("_meta", {})["alternative_sources"] = alt_sources
            primary["_meta"]["multi_source_count"] = len(dims)
            result_dimensions.append(primary)

            # 交叉验证（仅对比前两个源）
            if len(dims) >= 2 and dim_name in CRITICAL_FIELDS:
                cv = cross_validate_dim(
                    dim_name,
                    dims[0].get("data"),
                    dims[1].get("data"),
                    dims[0].get("_meta", {}).get("source", str(dims[0].get("_meta", {}))),
                    dims[1].get("_meta", {}).get("source", str(dims[1].get("_meta", {}))),
                )
                if cv["max_diff_pct"] > 0:
                    cv_results.append(cv)

    # 汇总统计
    all_dim_names = sorted(merged_dims.keys())
    multi_source_dims = [name for name, ds in merged_dims.items() if len(ds) >= 2]

    merged = {
        "symbol": collections[0].get("symbol", "?"),
        "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "dimensions": result_dimensions,
        "summary": {
            "total": len(all_dim_names),
            "available": sum(
                1 for d in result_dimensions
                if d.get("status") != "failed"
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
