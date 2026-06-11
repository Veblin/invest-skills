"""proxy 检测与 Clash 规则提示测试。"""

from __future__ import annotations

import os

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
        assert "MATCH,PROXY" in text

    def test_proxy_bypass_is_noop(self, monkeypatch):
        from lib.proxy import proxy_bypass

        monkeypatch.setenv("HTTP_PROXY", "http://test-proxy:8080")
        with proxy_bypass():
            assert os.environ.get("HTTP_PROXY") == "http://test-proxy:8080"
        assert os.environ.get("HTTP_PROXY") == "http://test-proxy:8080"

    def test_warn_if_proxy_detected_prints_once(self, monkeypatch, capsys):
        from lib import proxy as proxy_mod
        from lib.proxy import warn_if_proxy_detected

        proxy_mod._warned = False
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        warn_if_proxy_detected()
        warn_if_proxy_detected()
        out = capsys.readouterr().out
        assert out.count("eastmoney.com") == 1
