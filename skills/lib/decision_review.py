"""复盘对照纯函数（sidecar → 可核验清单 → 纪要）。

设计：``host-docs/v0.3.0/review-material-design.md`` D3（用户已批准）。
验收要点：**到期清单可机器核验**（execution-plan §5 第 4 项）。
合规（LAW 6）：只对照假设状态，**不产生建议**——渲染文本里不得出现动作化措辞。

共享层（stock 侧后续复用时直接 import；本版只在 invest-a-etf 侧接线）。
"""

from __future__ import annotations

import datetime as _dt
import re as _re
from pathlib import Path
from typing import Any

from decision_schema import DecisionSchemaError, load_decision_json

DUE_SOON_DAYS = 14      # 「临近到期」窗口（自然日）
_SIDECAR_SUFFIX = ".decision.json"
# 复盘纪要**自身不是「报告 md」**：它落在同一目录，若被当成报告收进序列会自我
# 污染——实测踩过：`--init` 把 report_ts 取成了 `20260911-review`（字典序靠后）。
# 用 `\d{8}` 而非裸 `-review.md`：后者**内容无关**，用户把真报告存成
# `2026-09-10-review.md` 会被误排除出报告序列。
# ⚠️ 与 `skills/lib/report_qc._REVIEW_MEMO_RE` 保持一致（本处为 owner）。
REVIEW_NAME_RE = _re.compile(r"^\d{8}-review\.md$")


def falsifier_rows(payload: dict | None, *, today: _dt.date) -> list[dict]:
    """证伪条件 → 可核验行，按 ``due`` 升序（到期清单即按此核验）。

    ``bucket`` 由 ``due`` 与今天**推导**（不依赖 sidecar 里手写的 status）：
    - ``expired``   已过 ``due``——该回看的那批（无论 status 是否已标 triggered）
    - ``due_soon``  ``<= DUE_SOON_DAYS`` 天内到期
    - ``open``      尚早
    - ``unknown``   ``due`` 不可解析（结构损坏时兜底，不静默丢弃）
    """
    rows: list[dict] = []
    for f in (payload or {}).get("falsifiers") or []:
        if not isinstance(f, dict):
            continue
        due = str(f.get("due") or "")
        try:
            d = _dt.date.fromisoformat(due)
        except ValueError:
            rows.append({"condition": str(f.get("condition") or ""), "due": due,
                         "status": str(f.get("status") or "open"), "days_left": None,
                         "bucket": "unknown"})
            continue
        days = (d - today).days
        bucket = ("expired" if days < 0
                  else ("due_soon" if days <= DUE_SOON_DAYS else "open"))
        rows.append({"condition": str(f.get("condition") or ""), "due": due,
                     "status": str(f.get("status") or "open"),
                     "days_left": days, "bucket": bucket})
    rows.sort(key=lambda r: r["due"])
    return rows


def collect_sidecars(report_dir: Path | str) -> list[dict]:
    """目录下每份报告 md 一条记录：[{ts, payload|None, error}]，按 ts 升序。

    **无 sidecar 的报告不会被跳过**——它带 ``error`` 出现（存量报告早于本协议，
    必须显式可见而非静默消失）。目录里没有对应 md 的孤儿 sidecar 同样列出。
    """
    d = Path(report_dir)
    out: list[dict] = []
    if not d.is_dir():
        return out
    stems = {p.stem for p in d.glob("*.md") if not REVIEW_NAME_RE.match(p.name)}
    stems |= {p.name[: -len(_SIDECAR_SUFFIX)] for p in d.glob(f"*{_SIDECAR_SUFFIX}")}
    for ts in sorted(stems):
        p = d / f"{ts}{_SIDECAR_SUFFIX}"
        if not p.is_file():
            out.append({"ts": ts, "payload": None,
                        "error": "无复盘原料（该报告早于 sidecar 协议，或未落盘）"})
            continue
        try:
            out.append({"ts": ts, "payload": load_decision_json(p), "error": None})
        except DecisionSchemaError as exc:
            out.append({"ts": ts, "payload": None, "error": str(exc)})
    return out


def _fmt_days(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 0:
        return f"已过 {abs(n)} 天"
    return f"{n} 天后"


def render_review(symbol: str, *, sidecars: list[dict], today: _dt.date) -> str:
    """三段式复盘纪要：报告序列 → 证伪条件状态（到期清单）→ 假设对照。

    只对照**假设状态**：不评价该不该行动、不给仓位/买卖语义。
    """
    lines = [
        f"# 🔍 复盘纪要 — {symbol}",
        "",
        f"> 生成日：{today.isoformat()}｜对照口径：报告 md 与其 sidecar（复盘原料）配对",
        "> 本纪要只对照**当时写下的假设与证伪条件**的当前状态，不含任何买卖建议。",
        "> 研究工具，非决策工具，不构成投资建议。",
        "",
        "## ① 报告序列",
        "",
    ]
    if not sidecars:
        lines.append(f"— 该标的**无复盘原料**（reports/{symbol}-* 下无报告 md 或 sidecar）")
    else:
        lines += ["| 报告 ts | 复盘原料 | 情景假设 | 证伪条件 |",
                  "|------|------|----------|----------|"]
        for s in sidecars:
            if s["payload"] is None:
                lines.append(f"| {s['ts']} | ❌ 无复盘原料 | — | — |")
            else:
                p = s["payload"]
                lines.append(f"| {s['ts']} | ✅ | {len(p.get('scenarios') or [])} 条 "
                             f"| {len(p.get('falsifiers') or [])} 条 |")
        missing = [s["ts"] for s in sidecars if s["payload"] is None]
        if missing:
            lines.append("")
            lines.append(f"> ⚠ {len(missing)} 份报告**无复盘原料**（早于 sidecar 协议或未落盘）："
                         + "、".join(missing[:5]) + ("…" if len(missing) > 5 else ""))

    # ② 证伪条件状态（到期清单——可机器核验）
    all_rows: list[tuple[str, dict]] = []
    for s in sidecars:
        for r in falsifier_rows(s["payload"], today=today):
            all_rows.append((s["ts"], r))
    all_rows.sort(key=lambda t: t[1]["due"])
    lines += ["", "## ② 证伪条件状态（按到期日排序）", ""]
    if not all_rows:
        lines.append("— 无证伪条件可对照（无复盘原料，或 sidecar 未写证伪条件）")
    else:
        lines += ["| 到期日 | 状态* | 距今 | 条件 | 来源报告 ts |",
                  "|--------|-------|------|------|--------------|"]
        for ts, r in all_rows:
            mark = {"expired": "⏰ 已过期", "due_soon": "🔔 临近",
                    "open": "—", "unknown": "❓ 到期日不可解析"}[r["bucket"]]
            lines.append(f"| {r['due']} | {mark} | {_fmt_days(r['days_left'])} "
                         f"| {r['condition']} | {ts} |")
        n_exp = sum(1 for _, r in all_rows if r["bucket"] == "expired")
        n_soon = sum(1 for _, r in all_rows if r["bucket"] == "due_soon")
        lines += ["", f"> 合计 {len(all_rows)} 条：**已过期 {n_exp}**、临近（{DUE_SOON_DAYS} 日内）"
                      f"{n_soon}、其余 {len(all_rows) - n_exp - n_soon} 条 "
                      "[来源: decision_review.falsifier_rows]",
                  "> *状态由到期日与生成日推导（非 sidecar 内手写 status）——"
                  "已过期即该回看，无论当时是否标注过。"]

    # ③ 假设对照（只列当时写下的假设，不评价）
    lines += ["", "## ③ 假设对照（当时写下的情景前提）", ""]
    any_scenario = False
    for s in sidecars:
        if s["payload"] is None:
            continue
        for sc in s["payload"].get("scenarios") or []:
            any_scenario = True
            lines.append(f"- **{sc.get('key')}**（权重 {sc.get('weight')}，"
                         f"参考价 {sc.get('valuation_ref')}）：{sc.get('assumption')} "
                         f"〔来源报告 {s['ts']}〕")
    if not any_scenario:
        lines.append("— 无情景假设可对照")
    lines += ["", "> 参考价来自当时报告，非当前估值；权重与假设均为当时判断，"
                  "本纪要不对其正确性作评价。"]
    return "\n".join(lines)
