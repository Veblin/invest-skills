"""md 子集渲染器 —— invest-a-etf 报告专用（零依赖，stdlib re/html）。

支持子集（黄金样例 reports/515050-通信ETF/2026-08-28-22-47-25.md +
report-template.md 实测语法）：
    #/##/###/#### 标题（id=GitHub 风格 slug）、> 引用块、--- 分隔线、
    | 表格（含 :---:/---:/:--- 列对齐）、- 无序列表、1. 有序列表
    （含 ≥2 空格缩进续行 + 嵌套 - 子列表）、**加粗**、`代码`、
    [文字](#锚点 / http(s) / 相对路径链接)。

不支持 → raise MarkdownSubsetError（D5 fail-loud，携带行号）：
    fenced code（```/~~~）、图片（![）、原始 HTML 块、###### 六级标题、
    setext 标题（===/--- 下划线式）、任务列表（- [ ]）、4 空格缩进代码块。
行内未支持语法（如 *斜体*、~~删除线~~）保持转义后的字面输出，不 raise——
防拦腰截断 artifact（行内误判风险高于块级）。

设计约束：仅直映原文（转义 + 结构包装），不做任何计算；数字按原文输出。
"""

from __future__ import annotations

import html as _html
import re

_HEAD_RE = re.compile(r"^(#{1,4})\s+(.+)$")          # h1-h4
_HEAD_DEEP_RE = re.compile(r"^(#{5,})")               # h5+ → fail-loud
_HR_RE = re.compile(r"^-{3,}\s*$")                    # 分隔线
_SETEXT_RE = re.compile(r"^(?:={3,}|-{3,})\s*$")      # setext → fail-loud
_QR_RE = re.compile(r"^>\s?(.*)$")                    # 引用块
_TB_ROW_RE = re.compile(r"^\|.*\|$")                  # 表格行（首尾 |）
_TB_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")            # 表格分隔行 |------|:---:|
_OL_RE = re.compile(r"^(\d+)\.\s+(.+)$")              # 有序列表项
_UL_RE = re.compile(r"^[-*]\s+(.+)$")                 # 无序列表项
_TASK_RE = re.compile(r"^[-*]\s+\[[ xX]\]")           # 任务列表 → fail-loud
_FENCED_RE = re.compile(r"^(```|~~~)")                # fenced code → fail-loud
_IMAGE_RE = re.compile(r"^!\s*\[")                    # 图片 → fail-loud
_HTML_BLOCK_RE = re.compile(r"^\s*</?[a-zA-Z][^>]*>")  # 原始 HTML 块 → fail-loud
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")   # 行内链接
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")             # 行内加粗
_CODE_RE = re.compile(r"(`[^`]+`)")                   # 行内代码（split 捕获）
_SLUG_STRIP_RE = re.compile(r"[^\w一-鿿\-]+")          # slug：词字符+CJK+- 以外
_SAFE_HREF_PREFIXES = ("#", "http://", "https://", "./", "../", "/")


class MarkdownSubsetError(Exception):
    """不支持的块级语法（D5 fail-loud）：携带行号与原文行。"""


def _slugify_heading(text: str) -> str:
    """GitHub 风格 slug：小写 → 空白→'-' → 移除词字符与 '-' 以外字符（CJK 保留）。

    与黄金样例目录锚点对齐（"2. 持仓透视（R12，`holdings`）" → "2-持仓透视r12holdings"）。
    注：样例 7.5 节目录锚点为手写瑕疵（与 GitHub 同源不一致），本函数按规则产出 id，不代修。
    """
    s = text.lower()
    s = re.sub(r"\s+", "-", s)
    return _SLUG_STRIP_RE.sub("", s)


def _is_safe_href(href: str) -> bool:
    """href 白名单：#锚点 / http(s) / ./ ../ / 相对路径；其余（javascript:/data: 等）拒绝。"""
    if href.startswith(_SAFE_HREF_PREFIXES):
        return True
    return ":" not in href


def _render_bold(text: str) -> str:
    """作用于已转义文本：**…** → <strong>…</strong>。"""
    return _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)


def _render_link(inner: str, href: str) -> str:
    """行内链接（inner/href 原文）：不安全 href → 仅渲染文字。"""
    inner = _html.escape(inner, quote=True)
    if not _is_safe_href(href):
        return _render_bold(inner)
    return f'<a href="{_html.escape(href, quote=True)}">{_render_bold(inner)}</a>'


def _render_inline_plain(seg: str) -> str:
    """普通文本段（原文）：链接 → 加粗，各文字片段只转义一次。"""
    out: list[str] = []
    last = 0
    for m in _LINK_RE.finditer(seg):
        out.append(_render_bold(_html.escape(seg[last : m.start()], quote=True)))
        out.append(_render_link(m.group(1), m.group(2)))
        last = m.end()
    out.append(_render_bold(_html.escape(seg[last:], quote=True)))
    return "".join(out)


def _render_inline(text: str) -> str:
    """行内子集：`代码` 优先（code 内不解析加粗/链接），其余段 escape → 链接 → 加粗。"""
    out: list[str] = []
    for idx, seg in enumerate(_CODE_RE.split(text)):
        if idx % 2 == 1:
            out.append(f"<code>{_html.escape(seg[1:-1])}</code>")
        else:
            out.append(_render_inline_plain(seg))
    return "".join(out)


def _split_table_row(row: str) -> list[str]:
    r"""| a | b | → [a, b]；支持 \| 转义。"""
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    sentinel = None
    if "\\|" in s:
        sentinel = "\x02"
        s = s.replace("\\|", sentinel)
    cells = s.split("|")
    if sentinel is not None:
        cells = [c.replace(sentinel, "|") for c in cells]
    return [c.strip() for c in cells]


def _table_aligns(sep: str) -> list[str]:
    """|:---:|:---:|:---:| → [center, right, left]。"""
    aligns: list[str] = []
    for cell in _split_table_row(sep):
        c = cell.strip()
        if c.startswith(":") and c.endswith(":"):
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    return aligns


def _render_table(header: str, sep: str, body: list[str], line_no: int) -> str:
    aligns = _table_aligns(sep)
    cells = _split_table_row(header)
    if len(cells) != len(aligns):
        raise MarkdownSubsetError(
            f"L{line_no}: 表头列数 {len(cells)} 与分隔行列数 {len(aligns)} 不一致"
        )

    def style(i: int) -> str:
        return f' style="text-align:{aligns[i]}"' if aligns[i] != "left" else ""

    head = "<thead><tr>" + "".join(
        f"<th{style(i)}>{_render_inline(c)}</th>" for i, c in enumerate(cells)
    ) + "</tr></thead>"
    rows = []
    for row in body:
        rcells = _split_table_row(row)
        if not rcells:
            continue
        # 列数不齐：按 header 列数对齐（缺列留空、多列丢弃）——原文忠实不报错
        tds = "".join(
            f"<td{style(i)}>{_render_inline(rcells[i]) if i < len(rcells) else ''}</td>"
            for i in range(len(cells))
        )
        rows.append("<tr>" + tds + "</tr>")
    return f'<table class="mdt">\n{head}\n<tbody>{"".join(rows)}</tbody>\n</table>'


def _render_list(items: list[tuple[str, str]], sub_lists: dict[int, list[str]]) -> str:
    """有序/无序列表 → <ol>/<ul>；sub_lists: item 序号 → 其嵌套子列表（-> <ul>）。"""
    tag = "ol" if items and items[0][0] == "ol" else "ul"
    lis: list[str] = []
    for idx, (_, text) in enumerate(items):
        inner = _render_inline(text)
        sub = sub_lists.get(idx)
        if sub:
            inner += "<ul>" + "".join(f"<li>{_render_inline(t)}</li>" for t in sub) + "</ul>"
        lis.append(f"<li>{inner}</li>")
    return f"<{tag}>" + "".join(lis) + f"</{tag}>"


def _is_setext_prev(prev_line: str) -> bool:
    """分隔线紧跟前一行时：仅为「普通段落续行」才算 setext（引用块/标题/列表后不是）。"""
    if not prev_line.strip():
        return False
    for pat in (_QR_RE, _HEAD_RE, _HEAD_DEEP_RE, _OL_RE, _UL_RE, _HR_RE, _TASK_RE):
        if pat.match(prev_line):
            return False
    return True


def render_markdown(text: str) -> str:
    """md 子集 → HTML 片段。空输入幂等返回空串；不支持语法 raise（带行号）。"""
    if not text.strip():
        return ""
    lines = text.split("\n")
    n = len(lines)
    out: list[str] = []
    i = 0
    while i < n:
        line = lines[i]
        line_no = i + 1
        stripped = line.strip()

        if not stripped:  # 空行（块终结）
            i += 1
            continue

        # fail-loud：块级不支持语法
        if _FENCED_RE.match(line):
            raise MarkdownSubsetError(f"L{line_no}: fenced code 块不支持（行: {stripped[:80]}）")
        if _IMAGE_RE.match(line):
            raise MarkdownSubsetError(f"L{line_no}: 图片语法不支持（行: {stripped[:80]}）")
        if _HTML_BLOCK_RE.match(line):
            raise MarkdownSubsetError(f"L{line_no}: 原始 HTML 块不支持（行: {stripped[:80]}）")
        if _HEAD_DEEP_RE.match(line):
            raise MarkdownSubsetError(f"L{line_no}: h5/h6 标题不支持（行: {stripped[:80]}）")
        if _TASK_RE.match(line):
            raise MarkdownSubsetError(f"L{line_no}: 任务列表不支持（行: {stripped[:80]}）")

        # 标题
        m = _HEAD_RE.match(line)
        if m:
            level = len(m.group(1))
            content = m.group(2).strip()
            out.append(
                f'<h{level} id="{_slugify_heading(content)}">{_render_inline(content)}</h{level}>'
            )
            i += 1
            continue

        # 分隔线（setext 下划线式不支持）
        if _HR_RE.match(line):
            if _is_setext_prev(lines[i - 1] if i > 0 else ""):
                raise MarkdownSubsetError(
                    f"L{line_no}: setext 标题（--- 下划线式）不支持；请改用 # 标题"
                )
            out.append("<hr>")
            i += 1
            continue
        if _SETEXT_RE.match(line):
            raise MarkdownSubsetError(
                f"L{line_no}: setext 标题（===/--- 下划线式）不支持；请改用 # 标题"
            )

        # 引用块（连续 > 行）
        if _QR_RE.match(line):
            inner: list[str] = []
            while i < n and _QR_RE.match(lines[i]):
                inner.append(_QR_RE.match(lines[i]).group(1))
                i += 1
            paras = "".join(f"<p>{_render_inline(t)}</p>" for t in inner if t.strip())
            out.append(f"<blockquote>\n{paras}\n</blockquote>")
            continue

        # 表格（当前行 + 下一行分隔行）
        if _TB_ROW_RE.match(line) and i + 1 < n and _TB_SEP_RE.match(lines[i + 1]):
            header, sep = line, lines[i + 1]
            i += 2
            body: list[str] = []
            while i < n and lines[i].strip() and _TB_ROW_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            out.append(_render_table(header, sep, body, line_no))
            continue

        # 有序列表（项 + ≥2 空格缩进续行 + 缩进子列表）；缩进判定先于项判定
        if _OL_RE.match(line):
            items: list[tuple[str, str]] = []
            sub_lists: dict[int, list[str]] = {}
            while i < n:
                raw = lines[i]
                l = raw.strip()
                if not l:
                    break
                if raw[:2] in ("  ", "\t"):  # 缩进行：续行 或 嵌套子项
                    if not items:
                        break
                    sub_m = _OL_RE.match(l)
                    if sub_m:
                        sub_lists[len(items) - 1].append(sub_m.group(2))
                    else:
                        sub_m = _UL_RE.match(l)
                        if sub_m:
                            sub_lists[len(items) - 1].append(sub_m.group(1))
                        else:
                            items[-1] = ("ol", items[-1][1] + " " + l)
                    i += 1
                    continue
                if _TASK_RE.match(l):
                    raise MarkdownSubsetError(f"L{i + 1}: 任务列表不支持（行: {l[:80]}）")
                m2 = _OL_RE.match(l)
                if m2:
                    items.append(("ol", m2.group(2)))
                    sub_lists[len(items) - 1] = []
                    i += 1
                    continue
                break  # 顶格非有序项 → 终止 ol（下一个块）
            out.append(_render_list(items, sub_lists))
            continue

        # 无序列表（项 + 缩进续行/子列表）；缩进判定先于项判定
        if _UL_RE.match(line):
            items = []
            sub_lists = {}
            while i < n:
                raw = lines[i]
                l = raw.strip()
                if not l:
                    break
                if raw[:2] in ("  ", "\t"):  # 缩进行：续行 或 嵌套子项
                    if not items:
                        break
                    m5 = _UL_RE.match(l)
                    if m5:
                        sub_lists[len(items) - 1].append(m5.group(1))
                    else:
                        items[-1] = ("ul", items[-1][1] + " " + l)
                    i += 1
                    continue
                if _TASK_RE.match(l):
                    raise MarkdownSubsetError(f"L{i + 1}: 任务列表不支持（行: {l[:80]}）")
                m4 = _UL_RE.match(l)
                if m4:
                    items.append(("ul", m4.group(1)))
                    sub_lists[len(items) - 1] = []
                    i += 1
                    continue
                break  # 顶格非无序项 → 终止 ul
            out.append(_render_list(items, sub_lists))
            continue

        # 4 空格缩进代码块 → fail-loud
        if line.startswith("    ") or line.startswith("\t"):
            raise MarkdownSubsetError(f"L{line_no}: 缩进代码块不支持（行: {stripped[:80]}）")

        # 段落（连续非空、非块开始行；GitHub 同款软换行 → 空格连接）
        para = [line]
        i += 1
        while i < n:
            l = lines[i]
            if not l.strip():
                break
            if _block_break_line(l):
                break
            if _TB_ROW_RE.match(l) and i + 1 < n and _TB_SEP_RE.match(lines[i + 1]):
                break  # 表格紧随段落（无空行）时接管
            para.append(l)
            i += 1
        out.append("<p>" + " ".join(_render_inline(t) for t in para) + "</p>")

    return "\n".join(out)


def _block_break_line(line: str) -> bool:
    """段落的终止行（下一个块开始）。

    全量审查 P2（与 invest-a-stock md_subset 同补丁）：4 空格/tab 缩进续行
    必须终止段落——否则被静默吸收为段落文本（缩进代码块在段首会 fail-loud、
    段中却被吞——fail-loud 承诺破洞）。
    """
    if line.startswith(("    ", "\t")):
        return True
    for pat in (_HEAD_RE, _HEAD_DEEP_RE, _FENCED_RE, _IMAGE_RE, _HTML_BLOCK_RE,
                _HR_RE, _SETEXT_RE, _QR_RE, _OL_RE, _UL_RE, _TASK_RE):
        if pat.match(line):
            return True
    return False
