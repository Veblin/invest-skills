"""R12 round-4/5：四维证据标签语法（skills/lib/evidence_tags.py）单一来源测试。

round-5 收紧：四维齐全、固定顺序、图标-注解配对；命名空间剥离覆盖
ASCII/中文/全角括号与键空格变体。覆盖：合法标签完全匹配（含交付件语料）；
非法标签（缺维度/重复/图标注解矛盾/断言夹带/伪造来源等级/换行）不掩码
不豁免；掩码保持跨度；report_qc 与 evidence_tags 的同源绑定（防漂移）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from evidence_tags import (  # noqa: E402
    FOUR_DIM_TAG_LINE_RE,
    FOUR_DIM_TAG_RE,
    is_four_dim_tag,
    mask_four_dim_tags,
    strip_strength_tag_spans,
)

LEGAL = [
    "[证据强度: ⚠️ 中 🌐多源 📅近季度 ✓✗]",
    "[证据强度: ⚠️ 中 📡单源 🗄️滞后 > 1 年 —]",
    "[证据强度: ⚠️ 中 📡单源 🕐近 30 日 —]",
    "[证据强度: ✅ 强 🌐 多源 🕐 近30日 ✓✓ 跨源一致]",
    "[证据强度:✅强🌐多源🕐近30日✓✗]",
    "[证据强度：✅ 强 🌐多源 📅报告期已注明 ✓✗]",
    "[证据强度: ❓ 弱 🔮推测 📅报告期已注明 —]",
    "[证据强度: ✅ 强 📡单源 🗄️滞后 >1 年 —]",
]
ILLEGAL = [
    # round-4 原反例族
    "[证据强度: ✅ 公司盈利增长999%]",
    "[证据强度: ✅ 强 🌐多源 🕐近30日 公司盈利增长999%]",
    "[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗] 公司盈利增长999%",
    # round-5 六类：缺维度
    "[证据强度: ✅ 强 🕐近30日 ✓✓]",
    "[证据强度: ✅ 强 🌐多源 ✓✓]",
    # round-5 六类：图标/注解矛盾
    "[证据强度: ✅ 强 🌐单源 🕐滞后 >1 年 ✓✓]",
    "[证据强度: ✅ 中 🌐多源 🕐近30日 ✓✓]",
    "[证据强度: ✅ 强 🌐多源 📅滞后 >1 年 ✓✗]",
    # round-5：重复维度 / 单维简式不再属四维
    "[证据强度: ✅ 强 ✅ 强 🌐多源 🕐近30日 ✓✓]",
    "[证据强度: ✅ 强]",
    "[证据强度: ⚠️ 中]",
    "[证据强度: 数据不足]",
    # 伪造来源/等级包入标签
    "[证据强度: ✅ 强 [来源: 假] 公司盈利增长999%]",
    "[证据强度: ✅ 强 来源: 公司公告显示盈利增长999%]",
    # 换行（标签不得跨行）
    "[证据强度: ✅ 强\n🌐多源 🕐近30日 ✓✗]",
    # 中文/全角括号包装（非合法 ASCII 语法）
    "【证据强度：✅ 强 🌐多源 🕐近30日 ✓✗】",
]


def test_legal_tags_fullmatch():
    for tag in LEGAL:
        assert FOUR_DIM_TAG_RE.fullmatch(tag), tag
        assert is_four_dim_tag(tag), tag
        assert FOUR_DIM_TAG_LINE_RE.match(f"  {tag}  "), tag


def test_illegal_tags_rejected():
    for tag in ILLEGAL:
        assert not FOUR_DIM_TAG_RE.fullmatch(tag), tag
        assert not is_four_dim_tag(tag), tag


def test_mask_only_legal_spans_and_preserve_length():
    line = "判断成立。[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗] 公司盈利增长999%"
    masked = mask_four_dim_tags(line)
    assert len(masked) == len(line)
    assert "999" in masked and "✅" not in masked
    for bad in ("[证据强度: ✅ 强 🕐近30日 ✓✓]",
                "[证据强度: ✅ 强 🌐单源 🕐滞后 >1 年 ✓✓]",
                "【证据强度：✅ 强 🌐多源 🕐近30日 ✓✗】"):
        assert mask_four_dim_tags(bad) == bad, bad


def test_strip_namespace_variants():
    """命名空间剥离：ASCII/中文/全角括号、键空格、大小写冒号、嵌套、未闭合。"""
    fake_bodies = [
        "[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✓ [来源: fake] 断言]",
        "[证据强度 : ✅ 强 🌐多源 🕐近30日 ✓✓ [来源: fake] 断言]",
        "[ 证据强度：✅ 强 🌐多源 🕐近30日 ✓✓ [证据: A] 断言]",
        "【证据强度：✅ 强 🌐多源 🕐近30日 ✓✓ [来源: fake] 断言】",
        "【证据强度：✅ 强 🌐多源 🕐近30日 ✓✓ [证据: A] 断言】",
        "【证据强度：✅ 强 🌐多源 🕐近30日 ✓✗ [事实: F1] 断言】",
        "[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗ 断言",
        "［证据强度：✅ 强 🌐多源 🕐近30日 ✓✗ [来源: fake] 断言］",
    ]
    for body in fake_bodies:
        stripped = strip_strength_tag_spans(body)
        assert len(stripped) == len(body)
        assert "[来源: fake]" not in stripped
        assert "[证据: A]" not in stripped
        assert "[事实: F1]" not in stripped
    outside = "[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗] 增长42%[来源: engine]"
    assert "[来源: engine]" in strip_strength_tag_spans(outside)


def test_report_qc_binds_shared_grammar():
    """防漂移：report_qc 必须使用本模块的同一对象（不得再复制正则）。"""
    import report_qc

    import evidence_tags

    assert report_qc._FOUR_DIM_TAG_RE is evidence_tags.FOUR_DIM_TAG_LINE_RE
    assert report_qc._evidence_ge_c("[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗]") is False
    assert report_qc._evidence_ge_c(
        "[证据强度: ✅ 强 [来源: 假] 断言]") is False
    assert report_qc._evidence_ge_c(
        "【证据强度：✅ 强 🌐多源 🕐近30日 ✓✗ [来源: 假] 断言】") is False
    assert report_qc._evidence_ge_c("断言 [来源: engine]") is True
