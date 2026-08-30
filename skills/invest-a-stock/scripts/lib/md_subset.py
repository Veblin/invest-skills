"""md 子集渲染器 —— invest-a-stock 分析段专用（零依赖，stdlib re/html）。

支持子集（与报告 md 常用语法对齐）：
    #/##/###/#### 标题、> 引用块、| 表格（横线分隔 + 对齐行）、--- 分隔线、
    - 无序列表、1. 有序列表（1–1 层；续行折进上一层）、
    **加粗**、`代码`、[文字](#锚点 / https) 链接。
不支持 → raise MarkdownSubsetError（D5 fail-loud，携带行号，格式 `L<line>: <msg>`）：
    fenced code（```/~~~）、图片（![）、原始 HTML、###### 六级标题、
    setext 标题、任务列表（- [ ]）、4 空格缩进代码块、嵌套列表。
行内 *斜体*、~~删除线~~ 不支持：按字面输出（不 raise）。
"""
from __future__ import annotations

import html as _html
import re

_HEAD_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_HEAD6_RE = re.compile(r"^(#{5,})")
_HR_RE = re.compile(r"^-{3,}\s*$")
_SETEXT_RE = re.compile(r"^(?:={3,}|-{3,})\s*$")
_QR_RE = re.compile(r"^>\s?(.*)$")
_TB_ROW_RE = re.compile(r"^\|.*\|$")
_TB_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_IMG_RE = re.compile(r"^\s*!\[")
_TASK_RE = re.compile(r"^\s*- \[[ xX]\]")
_CODEBLOCK_RE = re.compile(r"^    ")
_UL_RE = re.compile(r"^(\s*)(- )(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+[.])(.*)$")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

_TS = "L{line}: {msg}"


class MarkdownSubsetError(ValueError):
    """md 子集之外的语法 → 携带行号（fail-loud，配合 schema 校验）。"""
    def __init__(self, line: int, message: str):
        super().__init__(_TS.format(line=line, msg=message))
        self.line = line
        self.message = message


_INLINE_RE = re.compile(
    r"\[([^\]]+)\]\(([^)\s]+)\)"
    r"|\*\*([^*]+)\*\*"
    r"|`([^`]+)`"
)


def _inline(t: str) -> str:
    """行内语法：单遍 `finditer` 分段 — 未命中片段逐段 `escape(quote=False)`，
    捕获组内：链接 href 走 `escape(quote=True)`，文本递归 `_inline`。"""
    parts: list[str] = []
    pos = 0
    for m in _INLINE_RE.finditer(t):
        parts.append(_html.escape(t[pos:m.start()], quote=False))
        if m.group(1) is not None:
            parts.append(f'<a href="{_html.escape(m.group(2), quote=True)}">{_inline(m.group(1))}</a>')
        elif m.group(3) is not None:
            parts.append(f"<strong>{_html.escape(m.group(3), quote=False)}</strong>")
        else:
            parts.append(f"<code>{_html.escape(m.group(4), quote=False)}</code>")
        pos = m.end()
    parts.append(_html.escape(t[pos:], quote=False))
    return "".join(parts)


def _table_block(lines: list[str], start: int) -> tuple[str, int]:
    """表格块：| 表头 | / | 分隔 | / | 行 |...（对齐行仅判定，不渲染宽度语义）。"""
    body = [lines[start]]
    i = start + 1
    while i < len(lines) and _TB_ROW_RE.match(lines[i].strip()):
        body.append(lines[i])
        i += 1
    if len(body) < 2 or not _TB_SEP_RE.match(body[1].strip()):
        raise MarkdownSubsetError(start + 1, "表格必须是 表头|分隔行|数据行 结构")

    def _cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    header = _cells(body[0])
    ncol = len(header)
    rows_html = []
    for r in body[2:]:
        cells = _cells(r)
        if len(cells) != ncol:
            raise MarkdownSubsetError(start + 1 + body.index(r), f"表格列数不一致: {len(cells)} vs {ncol}")
        rows_html.append("<tr>\n" + "\n".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
    head = "\n".join(f"<th>{_inline(c)}</th>" for c in header)
    return ("<table>\n<thead><tr>\n" + head + "\n</tr></thead>\n<tbody>\n"
            + "\n".join(rows_html) + "\n</tbody>\n</table>"), i


def _list_block(lines: list[str], start: int) -> tuple[str, int, int]:
    """无序(- )/有序(1.) 列表：嵌套列表不在子集；续行（>=2 空格起始）折进当前项。"""
    first = _UL_RE.match(lines[start]) or _OL_RE.match(lines[start])
    if first is None:
        raise MarkdownSubsetError(start + 1, "列表起始行格式错误")
    ordered = bool(_UL_RE.match(lines[start])) is False
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    item: list[str] = []
    cur: list[str] = []
    i = start
    while i < len(lines):
        m_ul = _UL_RE.match(lines[i])
        m_ol = _OL_RE.match(lines[i])
        if m_ul or m_ol:
            m = m_ul or m_ol
            if cur:
                items.append("\n".join("<li>" + _inline(" ".join(cur)) + "</li>"))
                cur = []
            if (m_ul and ordered) or (m_ol and not ordered):
                raise MarkdownSubsetError(i + 1, "无序/有序列表混用不在子集")
            cur.append(_inline(m.group(3)))
        elif lines[i].strip() and not lines[i].startswith(("\t", "    ")):
            if len(lines[i]) - len(lines[i].lstrip()) >= 2:
                cur.append(_inline(lines[i].strip()))
            else:
                break
        else:
            break
        i += 1
    if cur:
        items.append("<li>" + _inline(" ".join(cur)) + "</li>")
    if len(items) == 1:
        return (f"<{tag}>" + items[0] + f"</{tag}>"), i, 1
    if len(items) > 1:
        return (f"<{tag}>" + "\n".join(items) + f"</{tag}>"), i, len(items)
    raise MarkdownSubsetError(start + 1, "列表无内容")


def render_markdown(md: str) -> str:
    """md 子集 → HTML。不支持语法 fail-loud（MarkdownSubsetError 带行号）。"""
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue
        if _FENCE_RE.match(stripped):
            raise MarkdownSubsetError(i + 1, "fenced code 不在子集")
        if _IMG_RE.match(stripped):
            raise MarkdownSubsetError(i + 1, "图片不在子集")
        if _TASK_RE.match(stripped):
            raise MarkdownSubsetError(i + 1, "任务列表不在子集")
        if _CODEBLOCK_RE.match(raw):
            raise MarkdownSubsetError(i + 1, "4 空格缩进代码块不在子集")
        if _HEAD6_RE.match(raw):
            raise MarkdownSubsetError(i + 1, "五级及以上标题不在子集")
        # setext 标题：上一行是正文文本 + 本行 = --- / === （须先于 hr 判定）
        if _SETEXT_RE.match(stripped) and i > 0 and lines[i - 1].strip():
            raise MarkdownSubsetError(i + 1, "setext 标题不在子集")
        m_head = _HEAD_RE.match(raw)
        if m_head:
            level = len(m_head.group(1))
            out.append(f"<h{level}>{_inline(m_head.group(2))}</h{level}>")
            i += 1
            continue
        if _TB_ROW_RE.match(stripped):
            html, i = _table_block(lines, i)
            out.append(html)
            continue
        if _QR_RE.match(raw):
            out.append(f"<blockquote>{_inline(_QR_RE.match(raw).group(1))}</blockquote>")
            i += 1
            continue
        if _HR_RE.match(stripped):
            out.append("<hr/>")
            i += 1
            continue
        if _UL_RE.match(raw) or _OL_RE.match(raw):
            html, i, _ = _list_block(lines, i)
            out.append(html)
            continue
        # 段落（连续非空、非结构行合并为一段）
        para: list[str] = []
        while i < n and lines[i].strip() and not _HEAD_RE.match(lines[i]) and not _QR_RE.match(lines[i]) \
                and not _TB_ROW_RE.match(lines[i].strip()) and not _HR_RE.match(lines[i].strip()) \
                and not _UL_RE.match(lines[i]) and not _OL_RE.match(lines[i]) and "\t" not in lines[i][:1]:
            para.append(_inline(lines[i].strip()))
            i += 1
        out.append("<p>" + " ".join(para) + "</p>" if para else "")
    return "\n".join(o for o in out if o)
