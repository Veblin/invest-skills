"""CLI 分发与 parser 契约（code-review 2026-08-22 #8/#9/#2/#3/#4）。

- #8/#9：CMD_DISPATCH 与 build_parser 是双注册表（注释自述「新增子命令须同步两处」），
  失步时须 fail-loud：友好错误指向 CMD_DISPATCH + exit 1，非裸 KeyError traceback
- #2：SKILL.md 文档化的 `report SYM --resume` 必须可用（子 parser 注册旗标）
- #3/#4：根级前置 --plan/--mode 不得被子 parser 非 SUPPRESS 默认值静默覆盖
"""
import argparse


def _parse(argv):
    import invest
    return invest.build_parser().parse_args(argv)


def test_main_dispatch_desync_fails_loud(monkeypatch, capsys):
    """CMD_DISPATCH 缺条目（与 build_parser 失步）→ 友好错误 + exit 1。"""
    import invest
    from lib import env as env_mod
    from lib import logutil

    monkeypatch.setattr(env_mod, "ensure_env_loaded", lambda: None)
    monkeypatch.setattr(env_mod, "configure_socket_timeout", lambda: None)
    monkeypatch.setattr(logutil, "setup_logging", lambda: None)

    class FakeParser:
        def parse_args(self):
            return argparse.Namespace(command="ghost_cmd")

    monkeypatch.setattr(invest, "build_parser", FakeParser)
    rc = invest.main()
    assert rc == 1
    assert "未注册 CMD_DISPATCH 分发表" in capsys.readouterr().err


def test_report_subparser_accepts_resume_and_save_raw():
    """#2: report 子命令后置 --resume/--save-raw 可用（SKILL.md 文档形式）。"""
    args = _parse(["report", "600176", "--plan", "x.json", "--resume", "--save-raw"])
    assert args.plan == "x.json"
    assert args.resume is True
    assert args.save_raw is True


def test_root_plan_survives_report():
    """#3: 根级前置 --plan 不被 report 子 parser 覆盖为空。"""
    args = _parse(["--plan", "x.json", "report", "600176"])
    assert args.plan == "x.json"


def test_root_mode_survives_report():
    """#4: 根级前置 --mode brief 不被 report 子 parser 覆盖为 full。"""
    args = _parse(["--mode", "brief", "report", "600176"])
    assert args.mode == "brief"


def test_root_mode_survives_synthesize():
    """#4: 根级前置 --mode 对 synthesize 同样生效。"""
    args = _parse(["--mode", "brief", "synthesize", "600176"])
    assert args.mode == "brief"


def test_report_defaults_unchanged():
    """未给 --plan/--mode 时 report 默认值保持 plan='' / mode='full'。"""
    args = _parse(["report", "600176"])
    assert args.plan == ""
    assert args.mode == "full"


def test_resume_warns_when_store_unavailable(monkeypatch, capsys):
    """#3: _HAS_STORE=False 时 --resume 显式警告，不再静默失效。

    collect/report 的 resume 分支同形（`if args.resume and _HAS_STORE` +
    `elif args.resume` 警告），此处覆盖 collect 侧即可锁定契约。
    """
    import invest
    from lib import env as env_mod

    monkeypatch.setattr(invest, "_HAS_STORE", False)
    monkeypatch.setattr(env_mod, "print_missing_token_warnings", lambda: None)
    monkeypatch.setattr(invest, "warn_if_proxy_detected", lambda *a, **k: None)

    class _FakeCollector:
        def collect_all(self, symbol, dims, **_kw):
            return {"summary": {"dimensions": []}, "dimensions": []}

    monkeypatch.setattr(invest, "collector", _FakeCollector())
    monkeypatch.setattr(invest, "_warn_degraded_collection", lambda *a: None)
    monkeypatch.setattr(invest, "_no_sources_responded", lambda s: False)
    # review #6: cmd_collect 的 kline 维度会执行真实 _kline_cache.cleanup_old()，
    # 删除开发机真实 kline 缓存目录（invest.py 内 `from lib.collector import
    # _kline_cache` 为子模块导入）——测试须 stub 模块级 cleanup_old，不得有破坏性副作用
    import lib.collector._kline_cache as _kc_mod

    monkeypatch.setattr(_kc_mod, "cleanup_old", lambda: None)

    class _FakeRender:
        @staticmethod
        def render(result, symbol, style):
            return ""

    monkeypatch.setattr(invest, "render", _FakeRender)

    args = invest.build_parser().parse_args(["collect", "600000", "--resume"])
    invest.cmd_collect(args)
    err = capsys.readouterr().err
    assert "store 模块不可用" in err
    assert "--resume" in err
