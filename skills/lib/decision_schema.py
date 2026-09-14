"""复盘原料 sidecar（decision.json）schema + 校验器。

设计依据：``host-docs/v0.3.0/review-material-design.md`` D1/D2
（2026-09-10 用户批准）。与 md 产物**同目录并存**：
``reports/{symbol}-{name}/{ts}.decision.json``——沿用 ``analysis_schema`` 的
失效即 loud 形态（Claude 写、引擎校验），但**不复用其段结构**：analysis.json 是
「报告正文段落」协议，本文件是「决策假设」协议，两者语义正交（D2）。

核心约束（LAW 6）：
- 多情景参考价**必须**带假设前提（``assumption``）+ 概率权重（``weight``），
  不允许无假设的单一目标价
- ``disclaimer`` 必填
- ``falsifiers[].due`` 必填且为 YYYY-MM-DD——review 的「到期清单可机器核验」靠它

最小 schema（无假设/预案时仍须落盘）：只填 5 个顶层键，``scenarios``/``falsifiers``
为空数组。否则「有的报告有 sidecar、有的没有」不可机器区分。
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

REQUIRED_TOPLEVEL = ("schema_version", "symbol", "report_ts", "as_of", "disclaimer")
SCENARIO_KEYS = ("optimistic", "neutral", "pessimistic")
FALSIFIER_STATUS = ("open", "triggered", "expired")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DEFAULT_DISCLAIMER = "多情景参考价基于上述假设，仅供参考，不构成投资建议"


class DecisionSchemaError(ValueError):
    """decision.json 结构非法（fail-loud，不静默降级）。"""


def _is_date(v: Any) -> bool:
    """YYYY-MM-DD **且日期真实存在**。

    仅校验形状与 ``1<=m<=12 / 1<=d<=31`` 会放行 2026-02-31 这类不存在的日期：
    写侧（``decision --from``）收下并 exit 0，读侧 ``date.fromisoformat`` 解析失败
    → 该条落 review 的 unknown 桶、**永不进「已过期·该回看」清单**——静默丢项
    即「到期清单可机器核验」（L4 验收标准）失效。
    """
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return False
    try:
        _dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _is_number(v: Any) -> bool:
    """只接受有限实数，防止 NaN/Infinity 写入可复盘的参考价。"""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)))


def _validate_scenario(sec: Any, idx: int) -> list[str]:
    errs: list[str] = []
    if not isinstance(sec, dict):
        return [f"scenarios[{idx}] 必须是对象"]
    if sec.get("key") not in SCENARIO_KEYS:
        errs.append(f"scenarios[{idx}].key 须为 {'/'.join(SCENARIO_KEYS)}")
    # LAW 6：假设前提必填且不得空白（否则退化成「无假设的单一目标价」）
    a = sec.get("assumption")
    if not isinstance(a, str) or not a.strip():
        errs.append(f"scenarios[{idx}].assumption 必填（多情景参考价须标注假设前提）")
    w = sec.get("weight")
    if not _is_number(w) or not (0.0 <= float(w) <= 1.0):
        errs.append(f"scenarios[{idx}].weight 须为 0-1 的数值（概率权重）")
    if not _is_number(sec.get("valuation_ref")):
        errs.append(f"scenarios[{idx}].valuation_ref 须为有限数值")
    return errs


def _validate_falsifier(f: Any, idx: int) -> list[str]:
    errs: list[str] = []
    if not isinstance(f, dict):
        return [f"falsifiers[{idx}] 必须是对象"]
    c = f.get("condition")
    if not isinstance(c, str) or not c.strip():
        errs.append(f"falsifiers[{idx}].condition 必填")
    if not _is_date(f.get("due")):
        errs.append(f"falsifiers[{idx}].due 必填且为 YYYY-MM-DD（到期清单靠它机器核验）")
    if f.get("observe_from") is not None and not _is_date(f.get("observe_from")):
        errs.append(f"falsifiers[{idx}].observe_from 须为 YYYY-MM-DD 或省略")
    if f.get("status", "open") not in FALSIFIER_STATUS:
        errs.append(f"falsifiers[{idx}].status 须为 {'/'.join(FALSIFIER_STATUS)}")
    return errs


def validate_decision(payload: Any) -> list[str]:
    """返回错误列表（空 = 合法）。不抛异常，便于调用方聚合报告。"""
    if not isinstance(payload, dict):
        return ["顶层必须为对象"]
    errs: list[str] = []
    for k in REQUIRED_TOPLEVEL:
        v = payload.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(f"missing:{k}")

    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        errs.append("scenarios 须为数组")
    else:
        for i, s in enumerate(scenarios):
            errs.extend(_validate_scenario(s, i))

    falsifiers = payload.get("falsifiers", [])
    if not isinstance(falsifiers, list):
        errs.append("falsifiers 须为数组")
    else:
        for i, f in enumerate(falsifiers):
            errs.extend(_validate_falsifier(f, i))

    if payload.get("playbook") is not None and not isinstance(payload["playbook"], dict):
        errs.append("playbook 须为对象或省略")
    return errs


def minimal_decision(*, symbol: str, report_ts: str, as_of: str) -> dict:
    """最小 schema：无情景假设/预案的报告也要落盘（可机器区分「有 sidecar 但空」）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "report_ts": report_ts,
        "as_of": as_of,
        "scenarios": [],
        "falsifiers": [],
        "playbook": {},
        "disclaimer": DEFAULT_DISCLAIMER,
    }


def load_decision_json(path: Path | str) -> dict:
    """读取并校验；任何失败抛 ``DecisionSchemaError``（fail-loud）。"""
    p = Path(path)
    if not p.is_file():
        raise DecisionSchemaError(f"sidecar 不存在: {p}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionSchemaError(f"sidecar 解析失败（{p}）: {exc}") from exc
    errs = validate_decision(payload)
    if errs:
        raise DecisionSchemaError(f"sidecar 校验失败（{p}）: " + "；".join(errs))
    return payload
