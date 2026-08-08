"""报告渲染模块（facade）。

实现拆分至:
  render_utils / render_html / render_dcf / render_risk / render_markdown
本文件延迟 re-export（模块级 __getattr__），保持 `from lib.render import ...` 兼容。

测试 monkeypatch 注意
---------------------
门面不复制名字，每次访问经模块级 ``__getattr__`` 从真实模块实时解析
（render_utils / render_html / render_dcf / render_risk / render_markdown，
markdown 最后解析，逆序取首个命中与旧版覆盖语义一致）。因此：

1. ``monkeypatch.setattr("lib.render.foo", fake)`` 对运行期经 ``lib.render.foo``
   查找的调用生效——含 render_markdown 内 facade-aware wrapper
   （``_v3_valuation_percentiles`` / ``_v3_load_valuation_summary`` 读
   ``lib.render.__dict__``）。
2. 直接 patch 真实模块（如 ``lib.render_utils.foo``）同样生效：facade 延迟解析
   命中最新值，wrapper 在未 patch 时经 ``_ru.foo`` 委托。

注意：``from lib.render import foo`` 在 patch 之前执行会持有当时的对象（Python
import-time 绑定语义），patch 需在调用方 import 之前完成，或在被测路径运行期经
``lib.render.foo`` 查找。
"""
from __future__ import annotations

from . import render_dcf as _render_dcf
from . import render_html as _render_html
from . import render_markdown as _render_markdown
from . import render_risk as _render_risk
from . import render_utils as _render_utils

# 解析顺序与旧版 _reexport 循环语义一致：原循环后列模块覆盖前列（markdown 最后），
# 故 __getattr__ 按逆序解析、返回首个命中 —— markdown（含 facade-aware wrapper）优先。
# 注意：不缓存解析结果（每次访问实时解析），patch 真实定义模块后经 facade 的
# 运行期查找立即命中新值；monkeypatch.setattr("lib.render.X", ...) 仍会临时写入
# __dict__ 覆盖本解析（pytest undo 会以 setattr 恢复旧值，属 pytest 语义）。
_ORIGIN_MODULES = (_render_utils, _render_html, _render_dcf, _render_risk, _render_markdown)


def __getattr__(name: str):
    """Facade 延迟解析：每次访问从真实模块实时解析，不缓存。"""
    for mod in reversed(_ORIGIN_MODULES):
        try:
            return getattr(mod, name)
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 显式钉住常用公共 API，便于静态检查与文档
ENGINE_VERSION = _render_utils.ENGINE_VERSION
sanitize_error = _render_utils.sanitize_error
render = _render_markdown.render
render_json = _render_markdown.render_json
render_report_v2 = _render_markdown.render_report_v2
render_report_v3 = _render_markdown.render_report_v3
render_valuation_section = _render_markdown.render_valuation_section
render_technical_section = _render_markdown.render_technical_section
ReportEnhancer = _render_markdown.ReportEnhancer
setup_default_enhancers = _render_markdown.setup_default_enhancers
render_html = _render_html.render_html
