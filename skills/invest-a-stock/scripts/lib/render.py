"""报告渲染模块（facade）。

实现拆分至:
  render_utils / render_html / render_dcf / render_risk / render_markdown
本文件 re-export（含下划线私有名），保持 `from lib.render import ...` 兼容。

测试 monkeypatch 注意
---------------------
``from lib.render import foo`` / 子模块内 ``from .render_utils import foo`` 会在
**导入时**绑定函数对象。此后 ``monkeypatch.setattr("lib.render.foo", ...)``
只改 facade 命名空间，**不会**影响已绑定的引用。

例外（markdown 估值热路径已 facade-aware）：
  ``_v3_valuation_percentiles`` / ``_v3_load_valuation_summary``
  在 ``render_markdown`` 内为 wrapper，会读 ``lib.render.__dict__``，
  故 patch ``lib.render._v3_valuation_percentiles`` 对 markdown 调用生效。
  亦可直接 patch ``lib.render_utils._…``（wrapper 委托实现）。

其余符号仍建议：
1. patch 实际定义模块（如 ``lib.render_utils.foo`` / ``lib.render_risk.foo``）；或
2. 被测函数在运行时经 ``lib.render.foo`` 查找。
"""
from __future__ import annotations

from . import render_dcf as _render_dcf
from . import render_html as _render_html
from . import render_markdown as _render_markdown
from . import render_risk as _render_risk
from . import render_utils as _render_utils


def _reexport(mod) -> None:
    for name, value in vars(mod).items():
        if name.startswith("__"):
            continue
        globals()[name] = value


for _mod in (_render_utils, _render_html, _render_dcf, _render_risk, _render_markdown):
    _reexport(_mod)

# 显式钉住常用公共 API，便于静态检查与文档
ENGINE_VERSION = _render_utils.ENGINE_VERSION
sanitize_error = _render_utils.sanitize_error
render = _render_markdown.render
render_compact = _render_markdown.render_compact
render_json = _render_markdown.render_json
render_report_v2 = _render_markdown.render_report_v2
render_report_v3 = _render_markdown.render_report_v3
render_valuation_section = _render_markdown.render_valuation_section
render_technical_section = _render_markdown.render_technical_section
ReportEnhancer = _render_markdown.ReportEnhancer
setup_default_enhancers = _render_markdown.setup_default_enhancers
render_html = _render_html.render_html
