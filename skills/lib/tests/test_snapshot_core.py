"""snapshot_core：规范化哈希/seal/verify 的通用契约（v0.3.2 B05）。"""

from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parent.parent
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from snapshot_core import (  # noqa: E402
    DEFAULT_HASH_FIELD,
    digest,
    seal,
    sealed_hash,
    verify_seal,
)


class TestDigest:
    def test_stable_and_order_insensitive(self):
        a = {"b": 1, "a": [1, 2, {"x": None}]}
        b = {"a": [1, 2, {"x": None}], "b": 1}
        assert digest(a) == digest(b)
        assert digest(a) == digest(a)

    def test_different_content_different_hash(self):
        assert digest({"a": 1}) != digest({"a": 2})

    def test_serializer_handles_common_types(self):
        d = digest({"t": datetime(2026, 1, 2, 3, 4, 5), "d": date(2026, 1, 2),
                    "dec": Decimal("1.23"), "s": {1, 2}, "b": b"xy"})
        assert isinstance(d, str) and len(d) == 64

    def test_mixed_type_set_serializes_deterministically(self):
        """review #12：{1, "a"} 通过原类型校验后 sorted() 抛 TypeError（digest 崩）；
        现按（类型名, 值）全序——可序列化且内容相同即哈希一致。"""
        from snapshot_core import default_serializer

        # 类型名分组按字母序：float < int < str（确定性优先，非数值序）
        assert default_serializer({1, "a", 2.5}) == [2.5, 1, "a"]
        d1 = digest({"s": {1, "a"}})
        d2 = digest({"s": {"a", 1}})
        assert isinstance(d1, str) and len(d1) == 64
        assert d1 == d2

    def test_custom_default_used(self):
        calls: list = []

        def _default(obj):
            calls.append(obj)
            return "X"

        class Weird:
            pass

        digest({"w": Weird()}, default=_default)
        assert calls and isinstance(calls[0], Weird)


class TestSealVerify:
    def test_seal_roundtrip(self):
        p = {"symbol": "x", "v": 1}
        h = seal(p)
        assert p["_meta"][DEFAULT_HASH_FIELD] == h
        assert verify_seal(p) == []

    def test_hash_excludes_hash_field_itself(self):
        """改 meta 其它字段（如 frozen_at）会使旧哈希失配——meta 本身受封存。"""
        p = {"symbol": "x"}
        seal(p, extra={"frozen_at": "t1"})
        assert verify_seal(p) == []
        p["_meta"]["frozen_at"] = "t2"
        assert verify_seal(p)  # 失配
        # 重算（seal）后恢复
        assert sealed_hash(p) == seal(p)
        assert verify_seal(p) == []

    def test_tamper_detected(self):
        p = {"symbol": "x", "v": 1}
        seal(p)
        p["v"] = 2
        assert any("哈希不一致" in e for e in verify_seal(p))

    def test_missing_meta_and_non_dict(self):
        assert verify_seal({"a": 1})
        assert verify_seal(None)
        p = {"a": 1, "_meta": {}}
        assert any("缺少封存哈希" in e for e in verify_seal(p))

    def test_version_check(self):
        p = {"a": 1}
        seal(p, version_field="payload_version", version=1)
        assert verify_seal(p, version_field="payload_version", expected_version=1) == []
        errs = verify_seal(p, version_field="payload_version", expected_version=2)
        assert any("版本" in e for e in errs)
        assert any(
            "缺少封存版本" in e
            for e in verify_seal({"a": 1, "_meta": {}}, version_field="payload_version"))
