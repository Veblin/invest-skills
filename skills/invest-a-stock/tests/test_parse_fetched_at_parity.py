"""A05-1 R1：store._parse_fetched_at 委托共享 dates.parse_utc_iso（离线对照）。

审计七例（空值/非法串/大小写 Z/±偏移/naive）两实现输出必须一致；
私有签名与返回契约（aware UTC / None）保留。
"""

from __future__ import annotations

from datetime import timezone

import pytest

# 先经 lib.store（其 shim 把 skills/lib 入 path）再导入顶层 canonical
from lib.store import _parse_fetched_at  # noqa: E402
from dates import parse_utc_iso  # noqa: E402

_CASES = [
    None,
    123,
    "",
    "   ",
    "2026-08-22T17:10:41+00:00",
    "2026-08-22T17:10:41Z",
    "2026-08-22t17:10:41z",
    "2026-08-22T17:10:41",
    "2026-08-22T01:10:41+08:00",
    "not-a-date",
    "2026-13-01T00:00:00",
]


@pytest.mark.parametrize("raw", _CASES, ids=[repr(c) for c in _CASES])
def test_matches_shared_parse_utc_iso(raw) -> None:
    assert _parse_fetched_at(raw) == parse_utc_iso(raw)


def test_contract_details() -> None:
    # naive 按 UTC 假定
    naive = _parse_fetched_at("2026-08-22T17:10:41")
    assert naive is not None and naive.tzinfo == timezone.utc
    # Z 后缀与 +00:00 等价；偏移换算到 UTC
    assert _parse_fetched_at("2026-08-22T17:10:41Z") == _parse_fetched_at(
        "2026-08-22T17:10:41+00:00")
    assert _parse_fetched_at("2026-08-22T01:10:41+08:00").hour == 17
    # 失败 → None（不抛）
    assert _parse_fetched_at(None) is None
    assert _parse_fetched_at("2026-08-22T17:10:41Z").isoformat().endswith("+00:00")
