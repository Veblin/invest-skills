"""md 子集渲染器：列表块（_list_block）回归测试。

背景（2026-09-17 报告缺陷）：多条目列表除末项外全部被 `"\\n".join(<str>)`
逐字符拆行——`<li>` 被撕成 `<` `\\n` `l` `\\n` `i`，浏览器按纯文本渲染，
报告出现裸 HTML 标签；同时 `_inline` 被应用两次，生成的 `<strong>` 二次转义
成 `&lt;strong&gt;` 字面输出。单条目列表与末项不受第一处影响，故长期未被发现。
"""

from __future__ import annotations


def test_multi_item_list_not_character_split():
    """多条目列表：每个条目一个完整 <li>，不得出现字符级换行。"""
    from lib.md_subset import render_markdown

    out = render_markdown("- 甲\n- 乙\n- 丙")
    assert out == "<ul><li>甲</li>\n<li>乙</li>\n<li>丙</li></ul>"
    assert "<\n" not in out


def test_ordered_list_multi_item():
    """有序列表同一路径（ordered 分支共用 items 组装）。

    条目文本保留 `1.` 后的一个前导空格——`_OL_RE` 前缀组不含该空格，
    与无序列表（`- ` 前缀组含空格）不对称。属既有行为、HTML 空白折叠，
    本测试锁定现状而非主张该空格正确。
    """
    from lib.md_subset import render_markdown

    out = render_markdown("1. 甲\n2. 乙")
    assert out == "<ol><li> 甲</li>\n<li> 乙</li></ol>"


def test_list_item_inline_rendered_once():
    """行内语法只渲染一次：** → <strong> 而非 &lt;strong&gt;。"""
    from lib.md_subset import render_markdown

    out = render_markdown("- **倍数 vs 盈利**：净利 638 亿元\n- **存货**：47.2%")
    assert out == ("<ul><li><strong>倍数 vs 盈利</strong>：净利 638 亿元</li>"
                   "\n<li><strong>存货</strong>：47.2%</li></ul>")
    assert "&lt;" not in out


def test_single_item_list_inline_rendered_once():
    """单条目列表走末项组装路径，同样不得二次转义。"""
    from lib.md_subset import render_markdown

    out = render_markdown("- **甲**：乙")
    assert out == "<ul><li><strong>甲</strong>：乙</li></ul>"


def test_list_item_escaped_once_only():
    """HTML 转义恰好一次：`&` 不得二次转义为 &amp;amp;。"""
    from lib.md_subset import render_markdown

    out = render_markdown("- a < b & c\n- d")
    assert out == "<ul><li>a &lt; b &amp; c</li>\n<li>d</li></ul>"


def test_list_item_code_span_not_escaped():
    """代码段同样只渲染一次。"""
    from lib.md_subset import render_markdown

    out = render_markdown("- `x = 1` 取值\n- 乙")
    assert out == "<ul><li><code>x = 1</code> 取值</li>\n<li>乙</li></ul>"


def test_list_continuation_line_folds_into_item():
    """续行（>=2 空格）折进当前项，且仍只渲染一次。"""
    from lib.md_subset import render_markdown

    out = render_markdown("- **甲**：\n  续行\n- 乙")
    assert out == "<ul><li><strong>甲</strong>： 续行</li>\n<li>乙</li></ul>"
