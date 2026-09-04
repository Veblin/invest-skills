"""T5-1（R-B2）：--emit html CLI 落盘约定。

覆盖：① 过期弃用警告已删除 ② outdir 默认 cwd/reports（与 md 分支一致）
③ _html_report_path 路径约定。cmd_report 全链路 stub 过重——以源码级断言
锁定行为（行内注释锚定），配合路径函数单测。
"""

from __future__ import annotations

from pathlib import Path

import invest


def test_html_report_path_convention():
    """_html_report_path = {outdir}/{subdir}/{ts}.html（与 md 同目录）。"""
    p = invest._html_report_path(
        Path("/tmp/o"), "600176-测试股份", "2026-09-03-12-00-00")
    assert str(p).endswith("600176-测试股份/2026-09-03-12-00-00.html")


def test_emit_html_no_stale_deprecation_warning():
    """旧「v0.1.2 旧版模板」警告已删除（新渲染器 render_html 非旧版）。"""
    src = Path(invest.__file__).read_text(encoding="utf-8")
    assert "v0.1.2 旧版模板" not in src
    assert "旧版模板" not in src


def test_emit_html_default_outdir_matches_md_branch():
    """html 分支 outdir 默认 = cwd/reports（与 md 分支同一约定，非 cwd 根）。"""
    src = Path(invest.__file__).read_text(encoding="utf-8")
    html_branch = src[src.index("if fmt == \"html\":"):src.index("if fmt == \"md\":")]
    assert "(Path.cwd() / \"reports\").resolve()" in html_branch
    # html 路径走 _html_report_path（with_suffix .html）
    assert "_html_report_path(outdir, subdir, ts)" in html_branch
