"""公告正文管道（v0.3.1）测试——全离线，网络经 monkeypatch 打桩。

实测依据（2026-09-24 探针）：正文固定 5000 字截断；港股公告为繁体（按**原文**输出，
不做繁简转换）；接口属东财，须经 `akshare_direct_session()`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "skills" / "invest-a-stock" / "scripts"))

from lib import notice_body as NB  # noqa: E402


# ---------------------------------------------------------------- art_code

@pytest.mark.parametrize("url,expected", [
    ("https://data.eastmoney.com/notices/detail/600176/AN202609171829519061.html",
     "AN202609171829519061"),
    ("https://data.eastmoney.com/notices/detail/300750/AN202609211829726640.html",
     "AN202609211829726640"),
    ("", None),
    ("https://example.com/no-code.html", None),
])
def test_extract_art_code(url, expected):
    assert NB.extract_art_code(url) == expected


# ---------------------------------------------------------------- 取正文（打桩）

class _Resp:
    def __init__(self, payload):
        self._b = payload

    def read(self):
        return self._b

    # urlopen 的返回值以 `with` 使用（失败路径也要关掉响应体）→ 打桩须是上下文管理器
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_net(monkeypatch, body: str, *, raises: Exception | None = None):
    """打桩 urlopen + 绕过限流/代理上下文（测试不触网、不 sleep）。"""
    import contextlib
    import urllib.request

    from lib import proxy

    @contextlib.contextmanager
    def _noop_session():
        yield

    monkeypatch.setattr(proxy, "akshare_direct_session", _noop_session)

    def _fake_urlopen(req, timeout=None):
        if raises:
            raise raises
        return _Resp(json.dumps({"data": {"notice_content": body}}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


@pytest.fixture(autouse=True)
def _tmp_cache(monkeypatch, tmp_path):
    """缓存落到 tmp，不污染 ~/.local/share/investment。"""
    from lib import env
    monkeypatch.setattr(env, "STORE_DIR", tmp_path)


def test_fetch_ok_and_cache_roundtrip(monkeypatch):
    _stub_net(monkeypatch, "股份購回數量 1,000 股")
    r = NB.fetch_notice_body("AN000000000000000001")
    assert r["status"] == "ok"
    assert r["char_count"] == len("股份購回數量 1,000 股")
    assert r["text"] == "股份購回數量 1,000 股", "原文须逐字保留"
    assert r["truncated"] is False

    # 二次调用命中缓存（网络已被打桩成必然成功，故用缓存文件存在性佐证）
    assert NB.read_cached_body("AN000000000000000001") is not None
    assert NB.fetch_notice_body("AN000000000000000001")["status"] == "ok"


def test_traditional_body_is_returned_verbatim(monkeypatch):
    """繁体按**原文**返回，不做繁简转换。

    本命令的用途是溯源：转换会让回收到的正文与交易所原文不再一致。简体关键词
    在繁体正文上匹配不到是**调用方**要处理的差异（见模块 docstring 第 2 条），
    不靠改写原文解决。
    """
    _stub_net(monkeypatch, "翌日披露報表 (股份發行人 ── 已發行股份或庫存股份變動、股份購回)")
    r = NB.fetch_notice_body("AN000000000000000003")
    assert r["status"] == "ok"
    assert "購回" in r["text"] and "庫存股份" in r["text"], "繁体原文须原样保留"
    assert "购回" not in r["text"] and "库存股份" not in r["text"], "不得做繁简转换"


def test_cache_hit_is_flagged(monkeypatch):
    """缓存命中须打标：`fetched_at` 是首次取数时刻，不得让调用方当成本次读取。"""
    _stub_net(monkeypatch, "公告正文")
    first = NB.fetch_notice_body("AN000000000000000012")
    assert first["cached"] is False
    second = NB.fetch_notice_body("AN000000000000000012")
    assert second["cached"] is True
    assert second["fetched_at"] == first["fetched_at"], "缓存保留原始取数时刻"
    assert second["text"] == first["text"]


def test_truncation_flagged_at_cap(monkeypatch):
    """达到 5000 字上限 → 保守标截断（不得让调用方以为拿到了全文）。"""
    _stub_net(monkeypatch, "回" * NB.BODY_CHAR_CAP)
    r = NB.fetch_notice_body("AN000000000000000002")
    assert r["char_count"] == NB.BODY_CHAR_CAP
    assert r["truncated"] is True
    assert "可能截断" in NB.describe(r)


def test_fetch_error_is_three_state_not_raise(monkeypatch):
    _stub_net(monkeypatch, "", raises=OSError("boom"))
    r = NB.fetch_notice_body("AN000000000000000003")
    assert r["status"] == "error"
    assert r["text"] == ""
    assert "不可得" in NB.describe(r)


def test_empty_body_is_missing(monkeypatch):
    _stub_net(monkeypatch, "")
    r = NB.fetch_notice_body("AN000000000000000004")
    assert r["status"] == "missing"
    assert "不可得" in NB.describe(r)


def test_no_art_code_short_circuits(monkeypatch):
    """无 art_code → missing，且**不得触网**。"""
    import urllib.request

    def _must_not_call(*a, **k):
        raise AssertionError("无 art_code 时不得发起网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", _must_not_call)
    r = NB.fetch_notice_body("")
    assert r["status"] == "missing"
    assert NB.fetch_body_by_url("https://example.com/x.html")["status"] == "missing"


def test_corrupt_cache_does_not_raise(monkeypatch, tmp_path):
    """坏缓存按缺失处理（与 gap-scan 的『缓存校验谓词不抛异常』同纪律）。"""
    from lib import env
    d = env.STORE_DIR / "notice_bodies"
    d.mkdir(parents=True, exist_ok=True)
    (d / "AN000000000000000005.json").write_text("{ 不是合法 json", encoding="utf-8")

    assert NB.read_cached_body("AN000000000000000005") is None


def test_non_object_cache_does_not_raise(tmp_path):
    from lib import env
    d = env.STORE_DIR / "notice_bodies"
    d.mkdir(parents=True, exist_ok=True)
    (d / "AN000000000000000006.json").write_text("[1,2,3]", encoding="utf-8")

    assert NB.read_cached_body("AN000000000000000006") is None


def test_cap_constant_matches_observed_limit():
    """5000 是实测值；若上游改动，此断言要人主动更新而不是静默漂移。"""
    assert NB.BODY_CHAR_CAP == 5000


# ---------------------------------------------------------------- 分发可达性

def test_registered_as_cli_subcommand():
    """模块必须有一条**真实运行路径**，否则构建器按 import 闭包打包时不会带上它。"""
    import invest

    parser = invest.build_parser()
    subs = [a for a in parser._actions
            if getattr(a, "choices", None) and a.dest == "command"]
    assert subs, "未找到子命令注册表"
    assert "notice-body" in subs[0].choices
    assert "notice-body" in invest.CMD_DISPATCH
    assert invest.CMD_DISPATCH["notice-body"] is invest.cmd_notice_body


def test_builder_closure_includes_notice_body():
    """打进 stock 包（此前只被测试引用 → 被闭包排除 → 分发形态无此能力）。"""
    import sys

    sys.path.insert(0, str(_ROOT / "scripts"))
    import build_skillhub_packages as b

    c = b._Closure("invest-a-stock")
    c.compute([b.SKILLS_DIR / "invest-a-stock" / "scripts" / "invest.py"])
    assert "notice_body" in c.included, "notice_body 未进闭包——分发形态将拿不到该能力"


def test_cli_reports_three_state_and_exit_code(monkeypatch, capsys):
    """CLI 层：不可得时打印原因并返回 1（不抛栈）。"""
    import argparse

    import invest

    _stub_net(monkeypatch, "", raises=OSError("boom"))
    rc = invest.cmd_notice_body(
        argparse.Namespace(target="AN000000000000000009", no_cache=True, json=False))
    out = capsys.readouterr().out
    assert rc == 1
    assert "正文不可得" in out and "boom" in out


def test_cli_marks_cached_read_and_uses_beijing_time(monkeypatch, capsys):
    """取数时刻走统一时区口径；缓存命中须标注（本命令的全部意义就是溯源）。"""
    import argparse

    import invest

    _stub_net(monkeypatch, "公告正文")
    ns = argparse.Namespace(target="AN000000000000000013", no_cache=False, json=False)

    assert invest.cmd_notice_body(ns) == 0
    fresh = capsys.readouterr().out
    assert "(北京时间)" in fresh, "取数时刻未走 fmt_fetched_at 口径"
    assert "本地缓存" not in fresh

    assert invest.cmd_notice_body(ns) == 0
    cached = capsys.readouterr().out
    assert "本地缓存" in cached, "缓存命中未标注——旧正文会冒充本次取数"


def test_cli_accepts_url_and_extracts_art_code(monkeypatch, capsys):
    import argparse

    import invest

    _stub_net(monkeypatch, "公告正文")
    rc = invest.cmd_notice_body(argparse.Namespace(
        target="https://data.eastmoney.com/notices/detail/600176/AN000000000000000010.html",
        no_cache=True, json=False))
    assert rc == 0
    assert "AN000000000000000010" in capsys.readouterr().out
