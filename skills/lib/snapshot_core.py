"""封面封存最小公共核心：规范化哈希 / seal / verify（跨 skill 共用）。

来源：从 invest-a-stock ``lib/report_snapshot.py`` 抽取**通用机制**（v0.3.2 B05，
负责人裁决 D2：优先抽取现有 snapshot 通用核验能力再复用，不复制第二套）。
本模块只承载与具体报告结构无关的三件事：

- :func:`digest` —— 规范化 JSON 哈希（与 stock 原实现逐字节等价；
  ``default`` 可注入调用方的类型回退策略，stock 传 ``json_default``）；
- :func:`sealed_hash` —— 「剔除哈希字段自身、合并 meta 视图」后的重算（seal 与
  verify 必须用同一个函数，否则两侧口径漂移）；
- :func:`seal` / :func:`verify_seal` —— 把哈希/版本写进 ``_meta`` 的通用封装。

**领域合同不在本模块**：stock 的 ``REQUIRED_REPORT_KEYS`` / 依赖三态
（``REPORT_DEPENDENCIES``）/ ``verify_facts`` 仍留在 ``report_snapshot.py``；ETF
不套 stock 的 required keys，只经 :func:`seal` / :func:`verify_seal` 冻结自己的
payload。历史报告的既有哈希值不受影响（digest 实现未变，仅位置迁移）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

__all__ = [
    "DEFAULT_HASH_FIELD",
    "default_serializer",
    "digest",
    "sealed_hash",
    "seal",
    "verify_seal",
]

DEFAULT_HASH_FIELD = "payload_hash"


def default_serializer(obj: Any) -> Any:
    """兜底序列化：日期/Decimal/bytes/set/numpy → JSON 原生类型。

    stock 侧仍传自己的 ``json_util.json_default``（含 logger 调试）；本函数供
    ETF payload 等无 stock 依赖的调用方使用，语义与之对齐（常见类型一致，
    未知类型回落 ``str``）。
    """
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, (set, frozenset)):
        return sorted(obj) if all(isinstance(x, (str, int, float)) for x in obj) else list(obj)
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    return str(obj)


def digest(value: object, *, default: Callable[[Any], Any] | None = None) -> str:
    """规范化 JSON 哈希（sha256 hex）。

    ``default`` 为类型回退序列化器；缺省用 :func:`default_serializer`。
    规范（与 stock 既有实现一致，不得改动）：``ensure_ascii=False``、
    ``sort_keys=True``、``separators=(",", ":")``。
    """
    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=default or default_serializer,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sealed_hash(
    payload: dict,
    *,
    meta_key: str = "_meta",
    hash_field: str = DEFAULT_HASH_FIELD,
    default: Callable[[Any], Any] | None = None,
) -> str:
    """计算封存哈希：正文 + （剔除 ``hash_field`` 自身后的）meta 视图。

    seal 侧写入 ``meta[hash_field]`` 前调用；verify 侧读取后重算比对——同一
    函数保证「写入口径 = 校验口径」。meta 非 dict（损坏）时按空 meta 处理，
    由 verify 层另行报「meta 缺失/非法」。
    """
    if not isinstance(payload, dict):
        raise TypeError("sealed_hash 需要 dict payload")
    meta = payload.get(meta_key)
    meta_view = {k: v for k, v in meta.items() if k != hash_field} if isinstance(meta, dict) else {}
    body = {k: v for k, v in payload.items() if k != meta_key}
    return digest({**body, meta_key: meta_view}, default=default)


def seal(
    payload: dict,
    *,
    meta_key: str = "_meta",
    hash_field: str = DEFAULT_HASH_FIELD,
    version_field: str | None = None,
    version: int | None = None,
    extra: dict | None = None,
    default: Callable[[Any], Any] | None = None,
) -> str:
    """写入封存元信息并返回哈希。

    ``extra`` 键先落盘、哈希后计算——保证写入 ``_meta`` 的一切（版本、冻结
    时点、选项）本身可复核。返回 ``payload[meta_key][hash_field]``。
    """
    if not isinstance(payload, dict):
        raise TypeError("seal 需要 dict payload")
    meta = payload.setdefault(meta_key, {})
    if not isinstance(meta, dict):
        raise TypeError(f"{meta_key} 必须是对象")
    if version_field is not None and version is not None:
        meta[version_field] = version
    if extra:
        meta.update(extra)
    meta[hash_field] = sealed_hash(payload, meta_key=meta_key, hash_field=hash_field,
                                   default=default)
    return meta[hash_field]


def verify_seal(
    payload: object,
    *,
    meta_key: str = "_meta",
    hash_field: str = DEFAULT_HASH_FIELD,
    version_field: str | None = None,
    expected_version: int | None = None,
    default: Callable[[Any], Any] | None = None,
) -> list[str]:
    """校验封存完整性；返回错误列表（空 = 通过）。

    负例覆盖：非 dict / 缺 ``_meta`` / 哈希缺失 / 版本缺失或非法 / 内容被篡改。
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload 不是对象"]
    meta = payload.get(meta_key)
    if not isinstance(meta, dict):
        return [f"缺少封存元信息（{meta_key}）"]
    if version_field is not None:
        v = meta.get(version_field)
        if v is None:
            errors.append(f"缺少封存版本（{meta_key}.{version_field}）")
        elif expected_version is not None and v != expected_version:
            errors.append(f"封存版本不支持：{v!r}（期望 {expected_version}）")
    sealed = meta.get(hash_field)
    if not sealed:
        errors.append(f"缺少封存哈希（{meta_key}.{hash_field}）")
        return errors
    actual = sealed_hash(payload, meta_key=meta_key, hash_field=hash_field, default=default)
    if sealed != actual:
        errors.append("封存哈希不一致（内容与封存不一致或已被篡改）")
    return errors
