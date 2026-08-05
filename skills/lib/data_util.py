"""通用数据处理工具：空值判定与逐 key 字典合并，供各 skill 共享（无业务依赖）。

历史：has_data 的"空值判定"此前在 merge_collections（_dim_has_data/
_source_has_data 双份）与 collector/_orchestrate（_source_has_data + collect_all
内联）共 4 处复制，且 collect_all 用 `is not None` 与 merge 的非空口径发生分歧；
merge_first_non_empty 此前在 merge_collections（research_summary 按维度名
硬编码分支）与 financial_rigor._merge_share_fields 各有一份。统一收敛至此。
"""

from __future__ import annotations

import math

from typing import Any

# 常规"空值"集合：None + 空容器（空 list/dict/str）。
# 注意：默认参数引用可变容器是安全的（本模块只读比较，从不修改）。
_EMPTY_DEFAULTS: tuple[Any, ...] = (None, [], {}, "")


def _is_empty_value(v: Any, empty_values: tuple[Any, ...]) -> bool:
    """v 是否视为"空/缺失"。

    - None 恒为空；str/list/dict/tuple/set 按 empty_values 成员判断；
    - float 标量 NaN（含 np.float64/float32）视为空——NaN 恒不等于任何值，
      旧 `v in empty_values` 会把 NaN 当非空混入合并结果；
    - 带 ndim/shape 的对象（numpy 数组 / pandas Series/DataFrame）直接放行
      （非空）：元素级 == 会触发 ValueError（ambiguous truth），且本模块
      不引入 numpy/pandas 依赖。注意 numpy 标量（np.float64）也有 ndim=0，
      只放行 ndim>0 的真实容器，标量继续走 isnan 分支；
    - 其他类型回退 `v in empty_values`，比较异常时视为非空。
    """
    if v is None:
        return True
    if hasattr(v, "ndim") and hasattr(v, "shape") and v.ndim > 0:
        return False
    if not isinstance(v, (str, bytes, list, dict, tuple, set, frozenset)):
        try:
            return math.isnan(v)
        except (TypeError, ValueError):
            pass
    try:
        return v in empty_values
    except (TypeError, ValueError):
        return False


def has_data(data: Any) -> bool:
    """数据是否有实质内容：None 与空 list/dict 均视为无数据。

    采集器可能合法返回 data=[]（非交易日 quote 空行、窗口过滤后空列表），
    `is not None` 判断会把空维度计为 available。collect_all 与
    merge_collections 均以本函数统一"有数据"口径。
    """
    if data is None:
        return False
    if isinstance(data, (list, dict)) and len(data) == 0:
        return False
    return True


def merge_first_non_empty(
    mappings: list[Any],
    empty_values: tuple[Any, ...] = _EMPTY_DEFAULTS,
) -> dict:
    """逐 key 合并多个字典：每个 key 取首个非空值（first-non-empty wins）。

    前序字典的既有非空值不会被后序覆盖；仅当 key 缺失或当前值属于
    empty_values 时才接受新值。空容器（[]/{}/''）与 None 默认视为"缺失"——
    失败采集器的骨架默认值（latest_ratings:[] 等）不会遮蔽健康采集器的
    真实数据。

    Args:
        mappings: 按优先级排序的字典列表（靠前的优先）；非 dict 条目跳过。
        empty_values: 视为"空/缺失"的值集合。

    Returns:
        合并结果字典；无有效输入时返回空 dict。
    """
    merged: dict = {}
    for m in mappings:
        if not isinstance(m, dict):
            continue
        for k, v in m.items():
            if _is_empty_value(v, empty_values):
                continue
            if k not in merged or _is_empty_value(merged[k], empty_values):
                merged[k] = v
    return merged
