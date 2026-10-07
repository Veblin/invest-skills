"""Immutable report input contract shared by collection and read-only consumers."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation

from .json_util import json_default


REQUIRED_REPORT_KEYS = ("market_structure", "industry_peers", "pe_band",
                        "industry_pricing", "price_shock", "events", "value_result", "_meta")

#: 报告就绪信封版本。2 = 增加「依赖三态」记录（见 `dependency_states`）。
REPORT_READY_VERSION = 2

#: 依赖三态：`not_triggered` 仅对 `conditional_collect` 依赖合法（未触发 ≠ 缺数据）。
DEPENDENCY_STATES = ("available", "unavailable", "not_triggered")

#: 报告依赖合同——**声明与校验同表**（主方案 §3.1「可执行采集合同」）。
#: `requires`/`phase`/`failure` 供 plan 输出（采集方照此执行、失败按此留痕）；
#: `sealed` 是封存校验要看的落点（顶层键，或条件项的维度名）；
#: `conditional` 标记条件采集项——未触发时状态为 `not_triggered`，不是缺口。
#: 放进本模块而非 planner：读取侧（本模块）无需反向 import，避免两处各写一份而漂移。
REPORT_DEPENDENCIES: dict[str, dict] = {
    "market_structure": {
        "requires": ["basic_info"], "phase": "collect",
        "failure": "record_availability",
        "sealed": ["market_structure"], "conditional": False,
    },
    "industry_peers": {
        "requires": ["basic_info", "financials"], "phase": "collect",
        "failure": "record_availability",
        "sealed": ["industry_peers"], "conditional": False,
    },
    "pe_band": {
        "requires": ["valuation"], "phase": "collect",
        "failure": "record_availability",
        "sealed": ["pe_band"], "conditional": False,
    },
    "events": {
        "requires": ["basic_info"], "phase": "collect",
        "failure": "record_attempted_sources",
        "sealed": ["events"], "conditional": False,
    },
    "limit_streak": {
        "requires": ["kline"], "phase": "conditional_collect",
        "failure": "record_availability",
        "sealed": ["lhb", "zt_pool"], "conditional": True,
    },
    "benchmark_hs300": {
        # DCF beta 的基准序列：封存后渲染链才真正零网络（此前它是渲染期真联网点）。
        "requires": ["kline"], "phase": "collect",
        "failure": "record_availability",
        "sealed": ["market_structure.benchmark_hs300"], "conditional": False,
    },
}


def _lookup_path(collection: dict, path: str) -> tuple[object, bool]:
    """按点路径取值（`a.b.c`）；返回 (值, 是否存在)。"""
    node: object = collection
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def _availability_failed(value: object) -> bool:
    """单条可用性标记是否表示「不可得」。

    按**前缀**判定，覆盖 `_ms_try_fetch` 实际产出的词汇：
    - `"available"` / `"available (akshare fallback; …)"`：有数据；
    - `"partial: …"`（erp／put_call_ratio／new_high_ratio／etf_flow 的降级成功态）：
      **有数据但降级**——计入「有数据」，细粒度降级由标签本身在报告里披露；
    - `"unavailable: <原因>"` 及其它（`"empty"`／`"not_requested"`／`"missing"`）：不可得。

    曾用精确比对 `== "available"` / `== "unavailable"`，两头都会错：`"available (…)"`
    与 `"partial: …"` 被误判为不可得，`"unavailable: <原因>"`／`"empty"` 被误判为可得。
    """
    if value is None:
        return False
    if isinstance(value, str):
        if value.startswith("available") or value.startswith("partial"):
            return False
        return True
    if isinstance(value, dict):
        return bool(value.get("error"))
    return False


def _availability_dict_failed(availability: dict) -> bool:
    """`availability` 字典是否整体表示不可得。

    - `{"market_structure": "unavailable: …"}`：整体失败标记（`_prepare_report_input`
      与 `collect_all` 的兜底形状）。
    - 逐子源字典：**每一项都不可得**才算整体不可得。全部失败时
      `collect_market_structure` 走的是这种形状（无 token 时逐因子写
      `unavailable: TUSHARE_TOKEN not configured`），此前只认整体标记，于是把
      「全因子失败」读成了 available。部分成功（如 akshare 回退源可用）不算失败。
    """
    if not availability:
        return False
    if "market_structure" in availability:
        return True
    return all(_availability_failed(value) for value in availability.values())


def _is_unavailable_marker(node: object) -> bool:
    """该字段是否被显式标记为不可得（而非「有数据」）。"""
    if node is None:
        return True
    if not isinstance(node, dict):
        return False
    if node.get("error"):
        return True
    availability = node.get("availability")
    if isinstance(availability, str):
        return _availability_failed(availability)
    if isinstance(availability, dict):
        return _availability_dict_failed(availability)
    return False


#: `market_structure` 内的**非因子**键——可用性判定必须跳过，否则元数据会被读成因子数据。
#: - `availability` / `attempted_sources` / `error`：状态与留痕本身；
#: - `benchmark_hs300`：DCF beta 的基准序列（它自己是一条独立依赖，见 `REPORT_DEPENDENCIES`），
#:   有数据不等于任何市场结构因子可得；
#: - `latency_ms`：`_ms_try_fetch` 在 `finally` 中**无条件**写入（失败路径也写），
#:   曾因它是非空 dict 而被读成「有数据的因子」，把全源失败的面板封成 available。
_MS_METADATA_KEYS = frozenset({
    "availability", "attempted_sources", "benchmark_hs300", "error", "latency_ms",
})


def _market_structure_unavailable(node: object) -> bool:
    """Only actual factor data with a successful source makes the panel available."""
    if not isinstance(node, dict):
        return True
    availability = node.get("availability")
    if isinstance(availability, str) and _availability_failed(availability):
        return True
    if node.get("error"):
        return True
    statuses = availability if isinstance(availability, dict) else {}
    for key, value in node.items():
        if key in _MS_METADATA_KEYS:
            continue
        has_data = value is not None and (not isinstance(value, (dict, list)) or bool(value))
        if has_data and not _availability_failed(statuses.get(key)):
            return False
    return True


def dependency_states(collection: dict) -> dict[str, str]:
    """按依赖合同给出每个依赖的三态。

    判据只取自封存内容本身（不依赖调用方临时变量），使写入侧与读取侧用同一函数，
    不会出现「采集说采了、校验说没采」的两套口径。
    """
    meta = collection.get("_meta") or {}
    indexed = {
        item.get("dimension"): item
        for item in collection.get("dimensions") or []
        if isinstance(item, dict) and item.get("dimension")
    }
    states: dict[str, str] = {}
    for name, spec in REPORT_DEPENDENCIES.items():
        if meta.get(f"{name}_error"):
            states[name] = "unavailable"
            continue
        if name == "events" and not collection.get("events"):
            # 空数组**不得反推事实**：判据复用 `needs_events_backfill`（与渲染层
            # 同一个单一源）。它只认**公告腿本身**的结论——按「全腿失败」判会把
            # 「公告腿挂掉 + 两条辅助腿合法空表」读成「窗口内无公告」，把采集
            # 缺陷说成事实（C4：辅助腿只覆盖很窄的公告类型，空表不能替代一手核验）。
            from .events import needs_events_backfill

            if needs_events_backfill(collection):
                states[name] = "unavailable"
                continue
        keys = list(spec.get("sealed") or [name])
        if spec.get("conditional"):
            triggered = [key for key in keys if key in indexed]
            if not triggered:
                states[name] = "not_triggered"
            elif any(indexed[key].get("status") == "available" for key in triggered):
                states[name] = "available"
            else:
                states[name] = "unavailable"
            continue
        resolved = [_lookup_path(collection, key) for key in keys]
        present = [node for node, found in resolved if found]
        if not present:
            states[name] = "unavailable"
        elif all((_market_structure_unavailable(node) if name == "market_structure"
                  else _is_unavailable_marker(node)) for node in present):
            states[name] = "unavailable"
        else:
            states[name] = "available"
    return states


def digest(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=json_default)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def plan_digest(plan: dict) -> str:
    return digest({k: v for k, v in plan.items() if k != "plan_hash"})


def is_sealed(collection: dict) -> bool:
    """该集合是否来自封存快照（信封 v1+）。

    封存输入**一律不得在渲染期联网**：缺失字段按不可得披露，而不是现场补抓
    （补抓会改掉「同一输入」的语义，也让哈希与内容不再一致）。
    """
    meta = collection.get("_meta") if isinstance(collection, dict) else None
    return bool(isinstance(meta, dict) and meta.get("report_input_hash"))


def seal(collection: dict, *, plan_hash: str | None = None,
         options: dict | None = None) -> str:
    """Seal only after all report inputs and failed-source states have been attached."""
    meta = collection.setdefault("_meta", {})
    meta["report_ready_version"] = REPORT_READY_VERSION
    meta["plan_hash"] = plan_hash
    meta["report_options"] = options or {}
    # 依赖三态在计算内容哈希**之前**落盘，故它本身也被封存、可复核；
    # 读取侧据此判断「合同是否真的执行过」（见 validate）。
    meta["report_dependencies"] = dependency_states(collection)
    meta["report_input_hash"] = digest({k: v for k, v in collection.items() if k != "_meta"} | {
        "_meta": {k: v for k, v in meta.items() if k != "report_input_hash"}
    })
    return meta["report_input_hash"]


def validate(record: dict | None, symbol: str, *, plan_hash: str | None = None) -> list[str]:
    if record is None:
        return ["快照 ID 不存在"]
    errors = []
    if record.get("kind") != "collect":
        errors.append(f"快照类型须为 collect，实际为 {record.get('kind')!r}")
    if record.get("symbol") != symbol:
        errors.append(f"标的不一致：{record.get('symbol')!r} != {symbol!r}")
    data = record.get("raw_json")
    if not isinstance(data, dict):
        return errors + ["快照 JSON 无效"]
    if data.get("symbol") != symbol:
        errors.append("快照正文标的不一致")
    if record.get("fetched_at") != data.get("fetched_at"):
        errors.append("采集时刻与快照正文不一致")
    missing = [key for key in REQUIRED_REPORT_KEYS if key not in data]
    if missing:
        errors.append("缺少报告输入字段: " + ", ".join(missing))
    meta = data.get("_meta") or {}
    version = meta.get("report_ready_version")
    if version not in (1, REPORT_READY_VERSION):
        errors.append("旧快照未封存为 report-ready；请重新 collect --report-ready")
    elif version >= REPORT_READY_VERSION:
        # 依赖合同必须真的执行过：每个声明依赖都要有合法三态之一。
        # 「不可得」不算错误（渲染会如实标 ⚠️），「没有状态」才是合同没跑。
        recorded = meta.get("report_dependencies") or {}
        unknown = [name for name in REPORT_DEPENDENCIES
                   if recorded.get(name) not in DEPENDENCY_STATES]
        if unknown:
            errors.append("依赖状态缺失（采集合同未执行）: " + ", ".join(unknown))
    sealed_plan_hash = meta.get("plan_hash")
    if sealed_plan_hash is not None and plan_hash is None:
        errors.append("快照绑定采集计划，须提供原 --plan")
    elif plan_hash is not None and sealed_plan_hash != plan_hash:
        errors.append("计划哈希不一致")
    actual_hash = digest({k: v for k, v in data.items() if k != "_meta"} | {
        "_meta": {k: v for k, v in meta.items() if k != "report_input_hash"}
    })
    if meta.get("report_input_hash") != actual_hash:
        errors.append("快照内容哈希不一致")
    return errors


def _written_decimals(value: object) -> int:
    """字面量写出的十进制精度（小数点后位数），含科学计数法。

    容差取「半个最末位数位」——writer 可能对引擎值四舍五入（`0.7424` 写成
    `0.742` 不该报不一致）。精度必须从**字面量**取，不能去数字符串里的小数点：
    `str(1e-5)` 是 `'1e-05'`（无小数点），旧算法得 0 → 容差 0.5，于是
    `value: 1e-5` 能对着封存值 `0.49` 通过固定快照的数值门。

    指数为正的大数（`str(1.23e18)`）按「该量级的整数」处理（decimals=0，容差
    0.5）：旧算法把 `'23e+18'` 数成 6 位小数 → 容差 5e-7，对 1e18 量级等于
    要求逐位全等，是反向的假阳性。
    """
    try:
        return max(0, -Decimal(str(value)).as_tuple().exponent)
    except (InvalidOperation, ValueError):
        return 0


def verify_facts(sections: list[dict], collection: dict) -> list[str]:
    """Check declared engine fact values against the sealed collection.

    External facts carry source_url and still need manual original-document review.
    Formula facts may name a source_path plus explicit source_scale.
    """
    root = dict(collection)
    root["dimension_by_name"] = {
        item.get("dimension"): item for item in collection.get("dimensions", [])
        if isinstance(item, dict) and item.get("dimension")
    }
    errors = []
    for section in sections:
        for fact in section.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            label = f"{section.get('module', '?')}.{fact.get('id', '?')}"
            path = fact.get("source_path")
            if not path:
                if not fact.get("source_url"):
                    errors.append(f"{label}: 缺 source_path 或 source_url")
                continue
            node = root
            try:
                for part in str(path).split("."):
                    node = node[int(part)] if isinstance(node, list) else node[part]
                if isinstance(node, bool):
                    raise TypeError("布尔值不是数值来源")
                observed = float(node) * float(fact.get("source_scale", 1))
                stated = float(fact["value"])
                if not math.isfinite(observed) or not math.isfinite(stated):
                    raise ValueError("非有限数")
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                errors.append(f"{label}: source_path {path!r} 无法读取数值（{exc}）")
                continue
            decimals = _written_decimals(fact["value"])
            tolerance = 0.5 * 10 ** (-decimals) + 1e-9
            if abs(observed - stated) > tolerance:
                errors.append(f"{label}: 封存值 {observed} 与 fact {stated} 不一致（{path}）")
    return errors
