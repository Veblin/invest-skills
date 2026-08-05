"""search_cache CLI put 载荷校验测试（A10，/code-review max，无网络）。

坏载荷（dict 等非 list）此前会被写入并成为 30 天永久 miss——get() 要求
isinstance(results, list) 且非空，agent 以为已缓存实则每次都重搜。
"""

from __future__ import annotations

import json

import lib.env as env
from lib import search_cache


class TestValidResults:
    def test_valid_list(self):
        assert search_cache._valid_results([{"url": "u", "title": "t"}])
        assert search_cache._valid_results([{"url": "u", "title": "t", "snippet": ""}])

    def test_dict_rejected(self):
        assert not search_cache._valid_results({"url": "u", "title": "t"})

    def test_empty_or_non_list_rejected(self):
        assert not search_cache._valid_results([])
        assert not search_cache._valid_results("not-a-list")

    def test_missing_url_or_title_rejected(self):
        assert not search_cache._valid_results([{"url": "", "title": "t"}])
        assert not search_cache._valid_results([{"url": "u", "title": None}])
        assert not search_cache._valid_results([{"url": "u"}])
        assert not search_cache._valid_results(["not-a-dict"])


class TestCliPutValidation:
    def test_invalid_payload_not_written(self, monkeypatch, tmp_path):
        monkeypatch.setattr(env, "STORE_DIR", tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"url": "x", "title": "y"}), encoding="utf-8")
        rc = search_cache._main(["search_cache.py", "put", "300981", "q", str(bad)])
        assert rc == 2
        assert search_cache.get("300981", "q") is None
        assert list((tmp_path / "search_cache").rglob("*.json")) == []

    def test_valid_payload_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(env, "STORE_DIR", tmp_path)
        good = tmp_path / "good.json"
        good.write_text(json.dumps([{"url": "u1", "title": "t1"}]), encoding="utf-8")
        rc = search_cache._main(["search_cache.py", "put", "300981", "q", str(good)])
        assert rc == 0
        assert search_cache.get("300981", "q") == [{"url": "u1", "title": "t1"}]
