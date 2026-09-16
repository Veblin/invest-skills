"""CLI 级契约：md / html 产物分支的 ``--analysis`` 侧车写入（与 insight 分支对齐）。

三分支共用 ``_write_analysis_sidecar``，但只有 insight 分支在调用点实现了
「真值判断 + 先写侧车 + OSError→exit 2」。md/html 分支是「先写正文 → 后写
侧车 → 无异常处理」，于是：

- ``--analysis`` 传 ``[]`` 时写出空侧车，而正文身份行写着「未注入」——
  审计者按侧车回查会拿到一份自相矛盾的产物（QC 报 sidecar-invalid 而非可操作的
  missing）；
- 侧车写失败（磁盘满/目录被改）时抛 OSError → traceback + exit 1，且已落盘的
  正文自称「已注入」，属无从追溯的孤儿成品。

注：本文件用 ``mode="brief"``——被测的是 **emit 分支**（md / html 写入顺序与
异常处理），与渲染模式无关；full 渲染含 DCF/九模块，单例数十秒，不适合做
CLI 契约测试。
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from fixtures.collections import collection_v2_minimal

_ANALYSIS = [{
    "module": "financials",
    "title": "单位盈利下滑的性质",
    "facts_md": "综合毛利率 25.02% → 23.93%。",
    "analysis_md": "**结论：** 判别变量是三季报境内毛利率。",
    "evidence_tag": "B",
    "position": "financials",
}]


def _args(tmp_path: Path, **overrides) -> Namespace:
    base = dict(symbol="600176", store=False, dims="basic_info,quote",
                with_macro=False, deep=False, plan="", save_raw=False,
                resume=False, emit="md", mode="brief",
                outdir=str(tmp_path / "reports"), strict_rigor=False,
                material_gap=False, with_news_pack=False, analysis=None)
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture()
def _invest(monkeypatch):
    """只保留被测的 emit 分支：渲染与联网补采全部打桩。

    不打桩则 `attach_extras=True` 会走市场结构补采（每只样本股 8s 超时），
    离线环境下单例测试要数分钟——那属于渲染层的事，与「侧车写入顺序/异常处理」
    这一被测行为无关。
    """
    import invest

    monkeypatch.setattr(invest, "_HAS_STORE", False)
    monkeypatch.setattr(invest.collector, "collect_all",
                        lambda *a, **k: collection_v2_minimal())
    monkeypatch.setattr(invest, "_ensure_render_ready", lambda *a, **k: None)
    monkeypatch.setattr(invest.render, "render", lambda *a, **k: "")
    monkeypatch.setattr(invest.render, "render_report_v3", lambda *a, **k: "")
    monkeypatch.setattr(invest.render, "render_html", lambda *a, **k: "")
    return invest


@pytest.mark.parametrize("emit", ["md", "html"])
def test_full_empty_analysis_array_writes_no_sidecar(_invest, tmp_path, emit):
    """``--analysis []`` 不携带任何分析段 → 不得写出自相矛盾的侧车。"""
    upstream = tmp_path / "upstream.analysis.json"
    upstream.write_text("[]", encoding="utf-8")

    assert _invest.cmd_report(
        _args(tmp_path, analysis=str(upstream), emit=emit)) == 0

    reports = [p for p in (tmp_path / "reports").rglob("*")
               if p.suffix in (".md", ".html")]
    assert reports, "应产出正文"
    assert not list((tmp_path / "reports").rglob("*.analysis.json")), \
        "空 payload 不得写侧车（正文写着「未注入」，侧车却是空数组）"


@pytest.mark.parametrize("emit", ["md", "html"])
def test_sidecar_write_failure_returns_2_without_orphan(_invest, tmp_path,
                                                        monkeypatch, emit):
    """侧车写失败必须 fail-loud（exit 2）且不留下「自称已注入」的正文。"""
    upstream = tmp_path / "upstream.analysis.json"
    upstream.write_text(json.dumps(_ANALYSIS, ensure_ascii=False), encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_invest, "_write_analysis_sidecar", _boom)
    rc = _invest.cmd_report(_args(tmp_path, analysis=str(upstream), emit=emit))

    assert rc == 2, "侧车写失败不得以 traceback/exit 1 收场"
    body = [p for p in (tmp_path / "reports").rglob("*")
            if p.suffix in (".md", ".html")]
    assert body == [], f"失败时不得落任何主体产物（孤儿成品）: {body}"


def test_full_valid_analysis_still_writes_sidecar(_invest, tmp_path):
    """守卫：合法 payload 的既有行为不变（同代侧车 + 正文注入）。"""
    upstream = tmp_path / "upstream.analysis.json"
    upstream.write_text(json.dumps(_ANALYSIS, ensure_ascii=False), encoding="utf-8")

    assert _invest.cmd_report(_args(tmp_path, analysis=str(upstream))) == 0
    sidecars = list((tmp_path / "reports").rglob("*.analysis.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text(encoding="utf-8")) == _ANALYSIS
