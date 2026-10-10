"""ETF 同代输入封存与配对核验（B05，v0.3.2）。

职责边界（负责人裁决 D2：抽取现有 snapshot 通用能力再复用，不复制第二套）：

- 哈希/seal/verify 的**通用机制**在 ``skills/lib/snapshot_core.py``（与 stock
  ``report_snapshot`` 同一实现）；本模块只做 ETF 领域事项：
  1. 冻结 payload 的元信息形状（``_meta.payload_hash`` / ``payload_version`` /
     ``frozen_at`` / ``symbol`` —— ETF **不套** stock 的 REQUIRED_REPORT_KEYS）；
  2. 报告目录内 ``<ts>.md`` / ``<ts>.payload.json`` / ``<ts>.html`` /
     ``<ts>.decision.json`` 的**同代配对核验**（输入/外部证据变化创建新代；
     payload 哈希不符 → 旧签认失效）；
  3. **产物实际内容绑定**（B06）：各代返回 ``artifacts``（md/html/decision/
     payload 的 sha256）与 ``qc_record``（共享 report_qc 的 QC 记录侧车核验，
     见其 ``verify_qc_record``）——改 MD/HTML/decision 任一即令旧记录 stale；
     ``paired`` 只表示输入配对，不表示整体通过。

生成语义（负责人 2026-10-08 纠偏后的最终契约）：``report --json --payload-out
PATH`` 落盘冻结 payload；``html --payload`` 只读该输入渲染（零网络零 DB 写）。
**缺 payload / 损坏 / 错 symbol / 哈希不符 / 与报告 ts 不对应 → 一律 fail-loud
rc 2**；HTML 重采路径（非同代渲染）已在本包退出，不保留回退。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from _invest_path import ensure_skills_lib_on_path  # noqa: E402

ensure_skills_lib_on_path()

from snapshot_core import seal as _core_seal  # noqa: E402
from snapshot_core import verify_seal as _core_verify_seal  # noqa: E402

PAYLOAD_VERSION = 1
PAYLOAD_SUFFIX = ".payload.json"
#: 报告 md 文件名（ts）形状：YYYY-MM-DD-HH-MM-SS；decision/review 另有命名。
REPORT_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")
_REVIEW_NAME_RE = re.compile(r"^\d{8}-review\.md$")


# ---------------------------------------------------------------------------
# 冻结 payload（report --payload-out）
# ---------------------------------------------------------------------------

def seal_etf_payload(payload: dict, *, frozen_at: str | None = None) -> str:
    """就地封存 ETF payload（写 ``_meta``），返回 payload_hash。

    ``frozen_at`` 由调用方给（上海时区 ISO）；缺省留 ``generated_at`` 兜底，
    不在此模块产生时间源，便于离线重放逐字节稳定。
    """
    if not isinstance(payload, dict):
        raise TypeError("seal_etf_payload 需要 dict payload")
    symbol = payload.get("symbol")
    if not symbol:
        raise ValueError("payload 缺 symbol，拒绝封存")
    extra: dict[str, Any] = {
        "payload_version": PAYLOAD_VERSION,
        "skill": payload.get("skill", "invest-a-etf"),
        "symbol": symbol,
        "generated_at": payload.get("generated_at"),
        "frozen_at": frozen_at or payload.get("generated_at"),
    }
    return _core_seal(payload, extra=extra)


def verify_etf_payload(payload: object) -> list[str]:
    """校验 ETF 冻结 payload；返回错误列表（空 = 通过）。"""
    errors = _core_verify_seal(payload, version_field="payload_version",
                               expected_version=PAYLOAD_VERSION)
    if errors:
        return errors
    meta = payload.get("_meta") or {}
    if meta.get("symbol") != payload.get("symbol"):
        errors.append("封存 meta.symbol 与 payload.symbol 不一致")
    if not payload.get("generated_at"):
        errors.append("payload 缺 generated_at（无法判定同代时点）")
    if not meta.get("frozen_at"):
        errors.append("payload 缺 frozen_at（冻结时点）")
    return errors


def load_etf_payload(path: str | Path) -> tuple[dict | None, list[str]]:
    """读取并校验冻结 payload；损坏 JSON fail-loud（返回 (None, [错误])）。"""
    p = Path(path)
    if not p.is_file():
        return None, [f"payload 文件不存在: {p}"]
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"payload JSON 读取/解析失败: {exc}"]
    if not isinstance(payload, dict):
        return None, ["payload 不是 JSON 对象"]
    errors = verify_etf_payload(payload)
    return (payload if not errors else None), errors


# ---------------------------------------------------------------------------
# 报告目录同代配对核验
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _decision_report_ts(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ts = data.get("report_ts")
    return str(ts) if ts else None


def verify_report_generation(report_dir: str | Path, *,
                             ts: str | None = None) -> dict:
    """核验报告目录内各代的 md/payload/html/decision 配对状态。

    返回 ``{"dir": str, "generations": [{ts, symbol, status, payload_hash,
    artifacts, qc_record, issues: [...]}]}``；``status``：
    - ``paired``：md + payload 齐备且哈希自洽（含 html/decision 一致时同样算；
      html/decision 缺失不作为失败，逐项在 issues 记录为提示级）；
    - ``legacy_no_payload``：md 存在但无冻结 payload（旧代/未冻结）；
    - ``mismatch``：payload 损坏/哈希不符/symbol 与目录前缀不一致/decision 的
      report_ts 不配对（旧签认与当前代不符）/QC 记录与产物不一致。

    **paired 只表示输入配对，不表示整体通过**：``qc_record`` 记录本代产物
    （md/html/decision/payload）的实际 sha256 与分层签认状态——产物改动后
    旧记录 stale（改 MD 一行不得沿用旧 manual PASS；保留 meta 哈希改 HTML
    数字不再算 paired）。``artifacts`` 为当前实际产物的 sha256。

    只读；不改任何历史文件（``ts`` 过滤单代时其余不动）。
    """
    d = Path(report_dir)
    out: dict[str, Any] = {"dir": str(d), "generations": []}
    if not d.is_dir():
        return {"dir": str(d), "generations": [], "error": f"目录不存在: {d}"}
    symbol = d.name.split("-", 1)[0] if "-" in d.name else ""
    mds = [p for p in sorted(d.glob("*.md")) if not _REVIEW_NAME_RE.match(p.name)]
    if ts:
        mds = [p for p in mds if p.stem == ts]
    for md in mds:
        gen_ts = md.stem
        if not REPORT_TS_RE.match(gen_ts):
            # 非报告命名（人工笔记等）不纳入代际核验，但不静默：记为 unknown_name
            out["generations"].append({"ts": gen_ts, "symbol": symbol,
                                       "status": "unknown_name", "payload_hash": None,
                                       "artifacts": None, "qc_record": None,
                                       "issues": ["文件名不符合报告 ts 形状，跳过代际核验"]})
            continue
        issues: list[str] = []
        payload_path = d / f"{gen_ts}{PAYLOAD_SUFFIX}"
        html_path = d / f"{gen_ts}.html"
        decision_path = d / f"{gen_ts}.decision.json"
        payload_hash: str | None = None
        if not payload_path.is_file():
            status = "legacy_no_payload"
            issues.append("无冻结 payload（旧代或未冻结；HTML 若存在为回采渲染）")
        else:
            payload, errs = load_etf_payload(payload_path)
            if payload is None:
                status = "mismatch"
                issues.extend(errs)
            else:
                meta = payload["_meta"]
                payload_hash = meta["payload_hash"]
                status = "paired"
                if symbol and payload.get("symbol") != symbol:
                    status = "mismatch"
                    issues.append(
                        f"payload.symbol={payload.get('symbol')!r} ≠ 目录前缀 {symbol!r}")
                html_text = _read_text(html_path) if html_path.is_file() else ""
                if not html_path.is_file():
                    issues.append("html 不存在（提示级）")
                elif f'name="invest-generation" content="{payload_hash}"' not in html_text:
                    status = "mismatch"
                    issues.append("html 内嵌代际哈希与 payload 不一致（旧签认已失效或 html 为回采渲染）")
        decision_ts = _decision_report_ts(decision_path) if decision_path.is_file() else None
        if decision_path.is_file():
            if decision_ts is None:
                status = "mismatch"
                issues.append("decision.json 无法解析或缺 report_ts")
            elif decision_ts != gen_ts:
                status = "mismatch"
                issues.append(f"decision.report_ts={decision_ts!r} ≠ md ts {gen_ts!r}（张冠李戴）")
        else:
            issues.append("无 decision sidecar（提示级）")
        artifacts, qc_record = _artifact_binding(md)
        if qc_record.get("status") == "stale":
            status = "mismatch"
            issues.append("QC 记录与产物不一致（" + "；".join(qc_record["issues"]) + "）")
        elif qc_record.get("status") == "invalid":
            status = "mismatch"
            issues.append("QC 记录非法（" + "；".join(qc_record["issues"]) + "）")
        elif qc_record.get("status") == "none":
            issues.append("无 QC 记录侧车（产物哈希/分层签认未记录；未执行 ≠ PASS）")
        out["generations"].append({"ts": gen_ts, "symbol": symbol, "status": status,
                                   "payload_hash": payload_hash,
                                   "artifacts": artifacts, "qc_record": qc_record,
                                   "issues": issues})
    return out


def _artifact_binding(md_path: Path) -> tuple[dict, dict]:
    """本代产物 sha256 与 QC 记录核验（复用共享 report_qc，不复制机制）。

    report_qc 不可用（最小包/裁剪环境）时降级为「未执行」——显式记录
    ``status="unavailable"``，不得静默当成通过。
    """
    try:
        from report_qc import generation_artifacts, verify_qc_record  # noqa: E402
    except ImportError as exc:  # pragma: no cover
        return {}, {"status": "unavailable",
                    "issues": [f"report_qc 不可用，产物哈希/记录未核验: {exc}"],
                    "changed": [], "signoffs": {}}
    artifacts = generation_artifacts(md_path)
    return artifacts, verify_qc_record(md_path)


def render_generation_line(gen: dict | None) -> str:
    """HTML 顶部代际行文案（同代/回采两态；给 HTML 渲染器复用）。"""
    if not gen or not gen.get("same_generation"):
        return ("⚠️ HTML 回采渲染（非同代输入）：本次数据为另一次采集，"
                "不受原报告数字签认保护。P3 起将移除该回退路径。")
    h = str(gen.get("payload_hash") or "")[:12]
    return (f"同代输入 · payload {h}… · 冻结于 {gen.get('frozen_at') or '未知'} · "
            f"generated_at {gen.get('generated_at') or '未知'}")
