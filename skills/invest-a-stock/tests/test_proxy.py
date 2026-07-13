"""proxy 检测与 Clash 规则提示测试。"""

from __future__ import annotations

import os
import threading
import time

import pytest


class TestProxyDetection:
    def test_detect_proxy_from_env(self, monkeypatch):
        from lib.proxy import detect_proxy

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        info = detect_proxy()
        assert info["detected"] is True
        assert "HTTP_PROXY" in info["env_keys"]

    def test_detect_proxy_clean_env(self, monkeypatch):
        from lib.proxy import detect_proxy

        for key in list(os.environ):
            if "proxy" in key.lower():
                monkeypatch.delenv(key, raising=False)
        info = detect_proxy()
        assert isinstance(info["detected"], bool)

    def test_clash_rules_yaml_format(self):
        from lib.proxy import clash_rules_yaml

        text = clash_rules_yaml()
        assert "DOMAIN-SUFFIX,eastmoney.com,DIRECT" in text
        assert "push2his.eastmoney.com" in text
        assert "MATCH,PROXY" in text

    def test_proxy_bypass_clears_http_proxy(self, monkeypatch):
        from lib.proxy import proxy_bypass, requests_use_proxy

        monkeypatch.setenv("HTTP_PROXY", "http://test-proxy:8080")
        monkeypatch.setenv("ALL_PROXY", "http://test-proxy:8080")
        monkeypatch.delenv("no_proxy", raising=False)
        with proxy_bypass():
            assert os.environ.get("HTTP_PROXY") is None
            assert os.environ.get("ALL_PROXY") is None
            assert ".eastmoney.com" in os.environ.get("no_proxy", "")
            assert requests_use_proxy() is False
        assert os.environ.get("HTTP_PROXY") == "http://test-proxy:8080"

    def test_warn_skips_when_bypass_effective(self, monkeypatch, capsys):
        from lib import proxy as proxy_mod
        from lib.proxy import warn_if_proxy_detected

        proxy_mod._warned = False
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7890")
        monkeypatch.delenv("no_proxy", raising=False)

        warn_if_proxy_detected(probe=False)
        out = capsys.readouterr().out
        assert "eastmoney.com" not in out

    def test_warn_clash_rules_when_bypass_ineffective(self, monkeypatch, capsys):
        from lib import proxy as proxy_mod
        from lib.proxy import warn_if_proxy_detected

        proxy_mod._warned = False
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setattr(proxy_mod, "requests_use_proxy", lambda *a, **k: True)

        warn_if_proxy_detected(probe=False)
        out = capsys.readouterr().out
        assert "eastmoney.com" in out

    def test_proxy_status_user_action_for_tun(self, monkeypatch):
        from lib import proxy as proxy_mod
        from lib.proxy import proxy_status

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        proxy_mod._push2_cache = {"reachable": None, "checked_at": 0.0, "detail": None}

        def _fake_probe(timeout):
            return {"reachable": False, "http_status": None, "error": "ConnectionError"}

        monkeypatch.setattr(proxy_mod, "_probe_push2_eastmoney_unlocked", _fake_probe)
        status = proxy_status(probe=True)
        assert status["bypass_effective"] is True
        assert status["user_action_needed"] is True
        assert status["hint_kind"] == "tun_or_cdn"
        assert status["push2"]["error"] == "ConnectionError"

    def test_proxy_status_preserves_push2_detail(self, monkeypatch):
        from lib import proxy as proxy_mod
        from lib.proxy import proxy_status

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        proxy_mod._push2_cache = {"reachable": None, "checked_at": 0.0, "detail": None}
        monkeypatch.setattr(
            proxy_mod,
            "_probe_push2_eastmoney_unlocked",
            lambda timeout: {
                "reachable": False,
                "http_status": 403,
                "error": "HTTP 403: 请求失败",
            },
        )
        status = proxy_status(probe=True)
        assert status["push2"]["http_status"] == 403
        assert "403" in status["push2"]["error"]

    def test_proxy_status_skips_push2_probe_without_proxy(self, monkeypatch):
        from lib import proxy as proxy_mod
        from lib.proxy import proxy_status

        monkeypatch.setattr(
            proxy_mod,
            "detect_proxy",
            lambda: {
                "detected": False,
                "env_keys": [],
                "system_proxies": {},
                "requests_proxies": {},
            },
        )
        calls: list[int] = []

        def _fake_probe(timeout):
            calls.append(1)
            return {"reachable": True, "http_status": 200, "error": None}

        monkeypatch.setattr(proxy_mod, "_probe_push2_eastmoney_unlocked", _fake_probe)
        status = proxy_status(probe=True)
        assert status["push2"] is None
        assert calls == []

    def test_akshare_push2_skips_probe_without_proxy(self, monkeypatch):
        from lib import proxy as proxy_mod
        from lib.proxy import akshare_push2_available

        monkeypatch.setattr(
            proxy_mod,
            "detect_proxy",
            lambda: {
                "detected": False,
                "env_keys": [],
                "system_proxies": {},
                "requests_proxies": {},
            },
        )
        calls: list[int] = []

        def _fake_probe(timeout):
            calls.append(1)
            return {"reachable": True, "http_status": 200, "error": None}

        monkeypatch.setattr(proxy_mod, "_probe_push2_eastmoney_unlocked", _fake_probe)
        proxy_mod._push2_cache = {"reachable": None, "checked_at": 0.0, "detail": None}
        assert akshare_push2_available() is True
        assert calls == []

    def test_concurrent_akshare_direct_sessions(self, monkeypatch):
        from lib.proxy import akshare_direct_session

        monkeypatch.setenv("HTTP_PROXY", "http://test-proxy:8080")
        errors: list[str] = []
        barrier = threading.Barrier(4)

        def worker():
            try:
                barrier.wait(timeout=5)
                with akshare_direct_session():
                    time.sleep(0.05)
                    assert os.environ.get("HTTP_PROXY") is None
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert os.environ.get("HTTP_PROXY") == "http://test-proxy:8080"

    def test_akshare_push2_cache_ttl(self, monkeypatch):
        from lib import proxy as proxy_mod
        from lib.proxy import PUSH2_CACHE_TTL_SEC, akshare_push2_available

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        calls: list[int] = []

        def _fake_probe(timeout):
            calls.append(1)
            return {"reachable": True, "http_status": 200, "error": None}

        monkeypatch.setattr(proxy_mod, "_probe_push2_eastmoney_unlocked", _fake_probe)
        proxy_mod._push2_cache = {"reachable": None, "checked_at": 0.0, "detail": None}

        assert akshare_push2_available(force_probe=True) is True
        assert akshare_push2_available() is True
        assert len(calls) == 1

        proxy_mod._push2_cache["checked_at"] = time.monotonic() - PUSH2_CACHE_TTL_SEC - 1
        assert akshare_push2_available() is True
        assert len(calls) == 2

    def test_concurrent_proxy_bypass_restores_env(self, monkeypatch):
        from lib.proxy import proxy_bypass

        monkeypatch.setenv("HTTP_PROXY", "http://test-proxy:8080")
        errors: list[str] = []

        def worker():
            try:
                for _ in range(20):
                    with proxy_bypass():
                        assert os.environ.get("HTTP_PROXY") is None
            except AssertionError as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert os.environ.get("HTTP_PROXY") == "http://test-proxy:8080"

    def test_warn_if_proxy_detected_prints_once(self, monkeypatch, capsys):
        from lib import proxy as proxy_mod
        from lib.proxy import warn_if_proxy_detected

        proxy_mod._warned = False
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setattr(proxy_mod, "requests_use_proxy", lambda *a, **k: True)

        warn_if_proxy_detected(probe=False)
        warn_if_proxy_detected(probe=False)
        out = capsys.readouterr().out
        assert out.count("无法自动绕过") == 1

    def test_no_proxy_session_exports(self):
        import lib.proxy as proxy_mod

        assert hasattr(proxy_mod, "no_proxy_session")
        assert hasattr(proxy_mod, "proxy_bypass")
        assert hasattr(proxy_mod, "akshare_direct_session")

    def test_proxy_status_reuses_push2_cache(self, monkeypatch):
        from lib import proxy as proxy_mod
        from lib.proxy import proxy_status

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        calls: list[int] = []

        def _fake_probe(timeout):
            calls.append(1)
            return {"reachable": True, "http_status": 200, "error": None}

        monkeypatch.setattr(proxy_mod, "_probe_push2_eastmoney_unlocked", _fake_probe)
        proxy_mod._push2_cache = {"reachable": None, "checked_at": 0.0, "detail": None}

        proxy_status(probe=True)
        proxy_status(probe=True)
        assert len(calls) == 1

    def test_diagnose_and_warn_share_push2_probe(self, monkeypatch):
        from lib import env, proxy as proxy_mod
        from lib.proxy import warn_if_proxy_detected

        proxy_mod._warned = False
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        calls: list[int] = []

        def _fake_probe(timeout):
            calls.append(1)
            return {"reachable": True, "http_status": 200, "error": None}

        monkeypatch.setattr(proxy_mod, "_probe_push2_eastmoney_unlocked", _fake_probe)
        proxy_mod._push2_cache = {"reachable": None, "checked_at": 0.0, "detail": None}

        warn_if_proxy_detected(probe=True)
        env.diagnose({"TUSHARE_TOKEN": "", "FRED_API_KEY": ""})
        assert len(calls) == 1

    def test_akshare_direct_session_patches_all_verbs(self, monkeypatch):
        import requests
        import requests.api as api_mod
        from lib.proxy import akshare_direct_session

        orig_request = requests.request
        orig_put = requests.put
        orig_api_put = api_mod.put

        monkeypatch.setenv("HTTP_PROXY", "http://test-proxy:8080")
        with akshare_direct_session():
            assert requests.request is not orig_request
            assert requests.put is not orig_put
            assert api_mod.put is not orig_api_put
        assert requests.request is orig_request
        assert requests.put is orig_put
