"""分析段注入状态判定（渲染层共用，唯一实现）。

为什么独立成模块而不是放进 ``analysis_schema``：
``analysis_schema`` 自身在模块级 ``from lib.md_subset import ...``
（见其 :15），依赖缺失时**整个模块不可导入**——模块观察不到自己的
import 失败，故「依赖不可用」这一状态只能在别处判定。放在这里，
渲染层与 HTML 状态卡共用同一份判定，不再各自拷一份（review C2：
两份 fail-closed 拷贝曾把「工具故障」与「内容缺失」压成同一个 False，
产物因而对报告内容写出「分析合成未完成」这一不实断言）。

本模块模块级只依赖标准库，可安全导入。
"""
from __future__ import annotations

ANALYSIS_OK = "ok"                  # 非空且通过正式 schema
ANALYSIS_ABSENT = "absent"          # 未提供分析段（合法状态：仅引擎结论）
ANALYSIS_INVALID = "invalid"        # 提供了但未通过 schema
ANALYSIS_UNAVAILABLE = "unavailable"  # 校验组件不可导入（工具故障，非内容缺失）


def analysis_payload_status(analysis: list[dict] | None) -> str:
    """返回 ok / absent / invalid / unavailable 之一。

    ``unavailable`` 专指校验组件导入失败——调用方据此渲染「状态无法校验」，
    而**不得**降级成「分析合成未完成」：后者是关于报告内容的事实性断言，
    在工具故障时并不成立。
    """
    if not isinstance(analysis, list) or not analysis:
        return ANALYSIS_ABSENT
    try:
        from lib.analysis_schema import validate_sections
    except Exception:  # noqa: BLE001 —— 依赖不可导入即工具故障，须显式外显
        return ANALYSIS_UNAVAILABLE
    try:
        errs = validate_sections(analysis)
    except Exception:  # noqa: BLE001 —— 校验器自身抛错同属工具故障
        return ANALYSIS_UNAVAILABLE
    return ANALYSIS_INVALID if errs else ANALYSIS_OK
