"""采集工件清单 — 记录每个数据源的指纹信息。

每次 collect 调用后生成 manifest.json 式结构化元数据，
记录各维度的字段结构、行数、日期范围、状态等指纹。
使 invest.py diff 能够区分"股票变化了"还是"数据源变化了"。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 常见日期字段名列表（遍历 data 时用于提取日期范围）
_DATE_FIELD_CANDIDATES = (
    "trade_date", "end_date", "date", "日期", "报告期",
    "持股日期", "ann_date", "f_ann_date",
)


def _get_version() -> str:
    """从 pyproject.toml 获取当前 invest-A 版本。"""
    try:
        # 向上查找 pyproject.toml（与 collect_all 中 Symbol 查找路径一致）
        root = Path(__file__).resolve().parent  # lib/
        # lib/ -> scripts/ -> 可能根目录
        for parent in [root, *root.parents]:
            pp = parent / "pyproject.toml"
            if pp.exists():
                for line in pp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("version") and "=" in line:
                        raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                        return raw
    except Exception as exc:
        logger.debug("读取 version 失败: %s", exc)
    return "unknown"


def _classify_status(dim: dict) -> str:
    """分类维度的状态: success / partial / failed / skipped。"""
    meta = dim.get("_meta", {})
    if meta.get("success"):
        data = dim.get("data")
        if data is not None:
            if hasattr(data, "__len__"):
                if len(data) > 0:
                    return "success"
                return "partial"  # 空长度
            return "success"  # 非空 dict/标量
        return "partial"
    status = dim.get("status", "")
    if status == "missing":
        # 检查是否有 skipped 标记
        if dim.get("error") and "跳过" in str(dim.get("error", "")):
            return "skipped"
        return "failed"
    return "failed"


def _extract_field_names(data) -> list[str]:
    """从数据中提取列/字段名（DataFrame 或 dict 列表 或 单 dict）。"""
    if data is None:
        return []
    # pandas DataFrame
    if hasattr(data, "columns"):
        return list(data.columns)
    # list of dicts
    if isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], dict):
            return list(data[0].keys())
        return []
    # single dict
    if isinstance(data, dict):
        return list(data.keys())
    return []


def _count_rows(data) -> int:
    """统计数据行数。"""
    if data is None:
        return 0
    if hasattr(data, "__len__"):
        try:
            return len(data)
        except (TypeError, ValueError):
            return 0
    return 0


def _extract_date_range(data) -> dict:
    """从数据中提取日期范围，返回 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} 或 {}。

    遍历列表或查找指定 key，识别常见日期字段并取其最小/最大值。
    """
    if data is None:
        return {}

    records: list[dict] = []
    if isinstance(data, list):
        records = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        records = [data]
    else:
        return {}

    dates: list[str] = []
    for rec in records:
        for candidate in _DATE_FIELD_CANDIDATES:
            val = rec.get(candidate)
            if val and isinstance(val, str) and val.strip():
                cleaned = val.strip().replace("-", "").replace("/", "")
                if len(cleaned) >= 8 and cleaned.isdigit():
                    # 转为 YYYY-MM-DD 格式
                    formatted = f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:8]}"
                    dates.append(formatted)
                elif len(cleaned) == 6 and cleaned.isdigit():
                    # YYYYMM 格式（如报告期 202412）
                    formatted = f"{cleaned[:4]}-{cleaned[4:6]}-01"
                    dates.append(formatted)
                break  # 每个记录只取第一个匹配日期字段

    if not dates:
        return {}

    sorted_dates = sorted(dates)
    return {"start": sorted_dates[0], "end": sorted_dates[-1]}


def _fingerprint_source(data, dim: dict, source_name: str) -> dict:
    """生成单个数据源的指纹。"""
    return {
        "source": source_name,
        "status": _classify_status(dim),
        "fields": _extract_field_names(data),
        "row_count": _count_rows(data),
        "date_range": _extract_date_range(data),
        "missing_fields": [],  # 预留：后续可标记期望字段列表中的缺失
    }


def generate_manifest(collection: dict) -> dict:
    """生成描述采集数据源及其指纹的清单。

    清单记录:
    - 逐源: 字段名、日期范围、行数、缺失字段、状态
    - 采集元数据: 时间戳、invest-A 版本、symbol
    - Schema 版本用于前向兼容

    这让 diff 能够区分"股票变化了"和"数据源变化了"。
    """
    manifest: dict[str, Any] = {
        "schema_version": "0.1",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "symbol": collection.get("symbol", ""),
        "name": collection.get("name", ""),
        "version": _get_version(),
        "sources": {},
        "dimensions": [],
    }

    # 遍历 collection["dimensions"] 记录逐源指纹
    dims = collection.get("dimensions", [])
    for dim in (dims or []):
        dim_name = dim.get("dimension", "unknown")
        manifest["dimensions"].append(dim_name)

        source_meta = dim.get("_meta", {})
        source_name = source_meta.get("source", "unknown")

        data = dim.get("data")
        source_fingerprint = _fingerprint_source(data, dim, source_name)
        manifest["sources"][source_name] = source_fingerprint

    # 也记录 macro / chain 等附加信息的来源
    macro = collection.get("macro_context", {})
    if macro and isinstance(macro, dict):
        macro_status = macro.get("status", "unknown")
        manifest["sources"]["macro"] = {
            "source": "macro",
            "status": macro_status,
            "fields": _extract_field_names(macro.get("indicators", {})),
            "row_count": 0,
            "date_range": {},
            "missing_fields": [],
        }

    chain = collection.get("chain_context", {})
    if chain and isinstance(chain, dict):
        chain_status = chain.get("status", "unknown")
        manifest["sources"]["chain"] = {
            "source": "chain",
            "status": chain_status,
            "fields": [k for k in chain.keys() if k != "status"],
            "row_count": 0,
            "date_range": {},
            "missing_fields": [],
        }

    return manifest


def compare_manifests(old_manifest: dict, new_manifest: dict) -> dict:
    """比较新旧两个清单，识别源级别的变化。

    返回 diff dict，包含:
    - sources_added: 新清单有而旧清单无的源
    - sources_removed: 旧清单有而新清单无的源
    - sources_changed: 字段指纹不同的源
    - status_changes: 状态变化的源（success → failed 等）
    """
    old_sources: dict = old_manifest.get("sources", {})
    new_sources: dict = new_manifest.get("sources", {})

    old_names = set(old_sources.keys())
    new_names = set(new_sources.keys())

    sources_added = sorted(new_names - old_names)
    sources_removed = sorted(old_names - new_names)
    common_names = old_names & new_names

    sources_changed: list[dict] = []
    status_changes: list[dict] = []

    for name in sorted(common_names):
        old_src = old_sources[name]
        new_src = new_sources[name]

        # 状态变化
        old_status = old_src.get("status", "")
        new_status = new_src.get("status", "")
        if old_status != new_status:
            status_changes.append({
                "source": name,
                "from": old_status,
                "to": new_status,
            })

        # 字段/指纹变化
        old_fields = set(old_src.get("fields", []))
        new_fields = set(new_src.get("fields", []))
        fields_added = sorted(new_fields - old_fields)
        fields_removed = sorted(old_fields - new_fields)

        old_rows = old_src.get("row_count", 0)
        new_rows = new_src.get("row_count", 0)
        row_count_changed = old_rows != new_rows

        old_range = old_src.get("date_range", {})
        new_range = new_src.get("date_range", {})
        range_changed = old_range != new_range

        if fields_added or fields_removed or row_count_changed or range_changed:
            changes: dict[str, Any] = {"source": name}
            if fields_added:
                changes["fields_added"] = fields_added
            if fields_removed:
                changes["fields_removed"] = fields_removed
            if row_count_changed:
                changes["row_count"] = {"from": old_rows, "to": new_rows}
            if range_changed:
                changes["date_range"] = {"from": old_range, "to": new_range}
            sources_changed.append(changes)

    return {
        "sources_added": sources_added,
        "sources_removed": sources_removed,
        "sources_changed": sources_changed,
        "status_changes": status_changes,
    }
