"""etf_md 子集渲染器测试（D13：断言于渲染产物，离线 fixture 驱动）。

- 骨架 fixture（tests/fixtures/md_skeleton.md，脱敏语法覆盖）为 CI 基线；
- 黄金样例（reports/515050-通信ETF/*.md，gitignored 本地产出）仅在有该报告时
  追加全量渲染测试（skipif，不进公开仓库——对外内容红线）。
"""

import re
from pathlib import Path

import pytest

from etf_md import MarkdownSubsetError, _slugify_heading, render_markdown

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
REPO_ROOT = TESTS_DIR.parents[2]
SKELETON = (FIXTURES / "md_skeleton.md").read_text(encoding="utf-8")
GOLDEN_DIR = REPO_ROOT / "reports" / "515050-通信ETF"

_GOLDEN = next(iter(sorted(GOLDEN_DIR.glob("*.md"))), None) if GOLDEN_DIR.is_dir() else None


def test_skeleton_renders_clean():
    html = render_markdown(SKELETON)
    assert html
    assert "<h1 id=" in html
    assert html.count("<h2 id=") >= 4
    assert '<table class="mdt">' in html
    assert "<blockquote>" in html
    assert "<ul>" in html
    assert "<ol>" in html
    assert "<strong>要点一</strong>" in html
    assert "<code>holdings</code>" in html
    assert '<a href="#1-产品快照">1-产品快照</a>' in html
    assert "<td" in html


def test_heading_slug_rules():
    # 与黄金样例目录锚点对齐（GitHub slug 规则复现）
    assert _slugify_heading("1. 产品快照") == "1-产品快照"
    assert _slugify_heading("2. 持仓透视（R12，`holdings`）") == "2-持仓透视r12holdings"
    assert _slugify_heading("7. 资金流向与趋势（R15，`sector-flow`）") == "7-资金流向与趋势r15sector-flow"
    assert _slugify_heading("13. 「致命一击」归纳") == "13-致命一击归纳"
    # 已知手写瑕疵：7.5 节 GitHub 生成 id 应为 ...f-系列v026（md 目录锚点与之不一致，不代修）
    assert _slugify_heading("7.5 动态基差与持仓（F 系列，v0.2.6）") == "75-动态基差与持仓f-系列v026"


def test_heading_ids_in_skeleton():
    html = render_markdown(SKELETON)
    assert '<h1 id="示例000000研究备忘">' in html
    assert '<h2 id="1-产品快照">' in html
    assert '<h2 id="2-持仓透视r12holdings">' in html
    assert '<h3 id="事件-价格对照表r11b">' in html


def test_table_alignment():
    html = render_markdown("| a | b | c |\n|:---:|:---:|---:|\n| 1 | 2 | 3 |")
    assert 'style="text-align:center"' in html
    assert 'style="text-align:right"' in html
    # 左对齐列不带 style（与默认一致）
    assert "text-align:left" not in html


def test_inline_escape_and_unsafe_links():
    # 行内转义测试置于引用块上下文（裸 `<script>` 行首按 CommonMark 属原始 HTML 块 → fail-loud）
    text = (
        "> 示例 <script>alert(1)</script> 与 [危险链接](javascript:alert(1))"
        " 与 [文档](docs/a.md)。"
    )
    html = render_markdown(text)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert '<a href="javascript:alert(1)"' not in html
    assert "危险链接</a>" not in html  # 不安全 href → 仅渲染文字
    assert "危险链接" in html
    assert '<a href="docs/a.md">文档</a>' in html


def test_code_span_not_bold():
    html = render_markdown("`**x**` 与 **y**")
    assert "<code>**x**</code>" in html
    assert "<strong>y</strong>" in html


def test_ol_nested_and_continuation():
    md = (
        "1. 第一项：普通\n"
        "2. 第二项：替代框架：\n"
        "   - 子项甲：a；\n"
        "   - 子项乙：b。\n"
        "3. 第三项：后文。\n"
    )
    html = render_markdown(md)
    assert "<ol>" in html
    assert "<li>第一项：普通</li>" in html
    assert "替代框架：" in html
    assert "<ul><li>子项甲：a；</li><li>子项乙：b。</li></ul>" in html
    assert html.index("子项甲") < html.index("第三项")


def test_ul_with_indent_continuation():
    md = "- 第一项\n  - 嵌套甲\n  - 嵌套乙\n- 第二项\n"
    html = render_markdown(md)
    assert "<ul><li>第一项<ul><li>嵌套甲</li><li>嵌套乙</li></ul></li><li>第二项</li></ul>" in html


def test_blockquote_multi_line():
    md = "> 第一行。\n> 第二行：`代码` 与 **加粗**。\n"
    html = render_markdown(md)
    assert "<blockquote>" in html
    assert "<p>第一行。</p>" in html
    assert "<code>代码</code>" in html
    assert "<strong>加粗</strong>" in html


def test_paragraph_soft_join():
    html = render_markdown("第一行\n第二行")
    assert html == "<p>第一行 第二行</p>"


@pytest.mark.parametrize(
    "bad, msg",
    [
        ("```python\nx\n```\n", "fenced code"),
        ("![图](a.png)\n", "图片"),
        ("###### 六级\n", "h5/h6"),
        ("一 二\n---\n", "setext"),
        ("一 二\n===\n", "setext"),
        ("- [ ] 待办\n", "任务列表"),
        ("<div>raw</div>\n", "原始 HTML"),
        ("    code\n", "缩进代码块"),
    ],
)
def test_fail_loud(bad, msg):
    with pytest.raises(MarkdownSubsetError) as ei:
        render_markdown(bad)
    assert msg in str(ei.value)
    assert "L" in str(ei.value)  # 携带行号


def test_empty_idempotent():
    assert render_markdown("") == ""
    assert render_markdown("   \n\n  ") == ""


def test_hr_after_blockquote_not_setext():
    # 引用块后的 --- 是不带前段的普通分隔线（非 setext），不应 fail-loud
    html = render_markdown("> 引用\n\n---\n")
    assert "<hr>" in html


@pytest.mark.skipif(_GOLDEN is None, reason="无本地产出黄金样例（reports/ gitignored）")
def test_golden_report_full_render():
    """黄金样例全量渲染：无异常、节标题 id 与目录锚点对齐（已知 7.5 瑕疵除外）。"""
    md = _GOLDEN.read_text(encoding="utf-8")
    html = render_markdown(md)
    assert html

    # 全部 ## 标题产出 id
    md_h2 = re.findall(r"^## .+$", md, flags=re.M)
    html_h2_ids = set(re.findall(r'<h2 id="([^"]+)"', html))
    assert len(md_h2) == len(html_h2_ids)
    for h2 in md_h2:
        content = h2[3:].strip()
        assert _slugify_heading(content) in html_h2_ids

    # 目录锚点（除已知手写瑕疵外）全部应可跳转；瑕疵照原样保留（GitHub 同源不一致，不代修）
    # 瑕疵 1：'75-动态基差与持仓f-系列'（正确 id 结尾为 ...v026）
    # 瑕疵 2：'13-致命一击-归纳'（正确 id 为 '13-致命一击归纳'）
    quirks = {"75-动态基差与持仓f-系列", "13-致命一击-归纳"}
    toc_links = re.findall(r"\[([^\]]+)\]\(#([^)]+)\)", md)
    for label, anchor in toc_links:
        if anchor in quirks:
            continue
        assert anchor in html_h2_ids, f"目录锚点缺失: {anchor}"

    # 合规底线：风险声明原文保留（md 未过滤）
    assert "风险声明（尾部）" in html
    assert "不构成投资建议" in html
