"""假设注册表（C1-C17）文档级断言 — 无网络、无引擎导入，纯文件读取 + 正则。

R-F01 验收：**C1-C17 全部登记 + 首批裁决记录**；裁决流程引用 STW 1999 数据窥探 +
滚动窗口（禁止全样本一次性结论）。另加一条**防升格**断言：源文档标 ⚠️ 的证据等级
在本表中不得被改写为 ✅。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY = "skills/lib/references/hypothesis-registry.md"

_COLUMNS = ("断言 ID", "直播出处", "可计算定义", "证据等级", "验证方法", "裁决状态", "日期")
_STATUSES = ("未裁决", "采纳(降级改造)", "否决", "待验证假设")


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def _rows() -> list[list[str]]:
    """登记表数据行（`| C<n> | … |`，7 列）。"""
    out = []
    for ln in _read(_REGISTRY).splitlines():
        if not ln.startswith("| C"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        out.append(cells)
    return out


def test_registry_exists_and_header_has_all_columns():
    text = _read(_REGISTRY)
    header = next(ln for ln in text.splitlines() if ln.startswith("| 断言 ID"))
    for col in _COLUMNS:
        assert col in header, f"表头缺列：{col}"


def test_registry_covers_c1_to_c17():
    ids = {r[0] for r in _rows()}
    expected = {f"C{i}" for i in range(1, 18)}
    assert expected <= ids, f"未登记：{sorted(expected - ids, key=lambda x: int(x[1:]))}"


def test_registry_rows_have_all_columns_non_empty():
    for cells in _rows():
        assert len(cells) == len(_COLUMNS), f"{cells[0]} 列数 {len(cells)} ≠ {len(_COLUMNS)}"
        for name, val in zip(_COLUMNS, cells):
            assert val, f"{cells[0]} 的「{name}」为空"


def test_registry_status_values_are_closed_set():
    for cells in _rows():
        status = cells[5]
        assert any(status.startswith(s) for s in _STATUSES), \
            f"{cells[0]} 裁决状态非法：{status}"


def test_registry_has_first_batch_adjudications():
    """首批裁决：至少 7 条非「未裁决」，且每条带日期。"""
    decided = [r for r in _rows() if not r[5].startswith("未裁决")]
    assert len(decided) >= 7, f"首批裁决不足（{len(decided)} < 7）"
    for r in decided:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[6]), f"{r[0]} 日期格式非法：{r[6]}"


def test_registry_adjudication_process_is_mandatory():
    """裁决流程须写死数据窥探防护 + 滚动窗口 + 禁止全样本一次性结论。"""
    text = _read(_REGISTRY)
    for token in ("STW 1999", "滚动窗口", "禁止全样本一次性结论", "预注册"):
        assert token in text, f"裁决流程缺：{token}"
    assert "multiple_testing" in text, "须指向本项目的 Reality Check 实现"


def test_registry_evidence_grade_not_upgraded():
    """防升格：源文档标 ⚠️ 的文献在本表中不得变成 ✅。

    以 Yang et al. 2019 为例——它是 C3 的降级表述锚点（源文档标 ⚠️），
    本表必须保留 ⚠️，否则「降级表述」会被静默升格为实证支持。
    """
    text = _read(_REGISTRY)
    assert "Yang et al. 2019" in text, "C3 的降级表述锚点在注册表中丢失"
    idx = text.index("Yang et al. 2019")
    # 只看**紧随其后**的标记——前一条引用的 ✅ 不属于它（首版断言用固定窗口，
    # 把前一条的 ✅ 误判为升格痕迹）
    tail = text[idx + len("Yang et al. 2019"): idx + len("Yang et al. 2019") + 40]
    assert "⚠️" in tail, f"Yang et al. 2019 须保持 ⚠️（不得升格为 ✅），实得：{tail!r}"
    # 明确写入「禁止升格」条款
    assert "不得改写为 ✅" in text or "禁止升格" in text


def test_registry_has_no_advisory_language():
    """治理文档仍受 LAW 6 约束：不得出现买卖/仓位建议。"""
    text = _read(_REGISTRY)
    for banned in ("建议买入", "建议卖出", "建议仓位", "目标价"):
        assert banned not in text, f"注册表出现建议性表述：{banned}"
