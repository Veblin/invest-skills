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


def test_portfolio_positions_flag(tmp_path):
    """P-1（v0.2.9）：portfolio 子命令接受 --positions。"""
    holdings = tmp_path / "h.json"
    holdings.write_text(
        '[{"symbol": "300308", "weight": 0.4, "cost": 150.0, "buy_date": "2025-06-01"}]',
        encoding="utf-8",
    )
    args = _parse(["portfolio", str(holdings), "--positions"])
    assert args.positions is True
    assert args.stress is False


def test_attribution_parser_accepts_snapshot():
    """V-1（v0.2.9）：attribution 子命令 --snapshot/--start/--end。"""
    args = _parse(["attribution", "300750", "--snapshot", "s.json",
                   "--start", "2021-12", "--end", "2023-12"])
    assert args.symbol == "300750" and args.snapshot == "s.json"
    assert args.start == "2021-12" and args.end == "2023-12"


def test_cmd_attribution_snapshot_output(capsys):
    """V-1：宁德 fixture 快照 → 三行分解 + 校验残差 ≈ 0 + 免责声明。"""
    import argparse
    from pathlib import Path

    import invest
    from lib.attribution import load_catl_fixture

    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "v0.2.9"
        / "catl_2021_2023_snapshot.json"
    )
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="300750", snapshot=str(fixture),
        start="2021-12", end="2023-12",
    ))
    out = capsys.readouterr().out
    assert rc == 0
    assert "价格贡献" in out and "盈利贡献" in out and "估值贡献" in out
    assert "-47.7%" in out                       # 市值口径价格贡献
    assert "-88.3%" in out                       # 估值贡献（可见 TTM 口径）
    assert "恒等式校验" in out
    assert "不构成投资建议" in out


def test_cmd_attribution_no_snapshot_degrades(capsys):
    """V-1：无 --snapshot 时显式降级（K 线统一前复权，raw 通道不可得，LAW 5）。"""
    import argparse

    import invest
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="300750", snapshot=None, start=None, end=None,
    ))
    out = capsys.readouterr().out
    assert rc == 1
    assert "不可得" in out


def test_cmd_attribution_bad_snapshot_no_crash(tmp_path, capsys):
    """V-1：快照损坏/缺键/除零 → 友好报错 exit 1，不裸 traceback。"""
    import argparse

    import invest

    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="300750", snapshot=str(broken), start=None, end=None,
    ))
    out = capsys.readouterr().out
    assert rc == 1 and "JSON 解析失败" in out

    missing = tmp_path / "missing.json"
    missing.write_text('{"start_mcap": 0}', encoding="utf-8")
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="300750", snapshot=str(missing), start=None, end=None,
    ))
    out = capsys.readouterr().out
    assert rc == 1 and "缺必需字段" in out

    zerodiv = tmp_path / "zerodiv.json"
    zerodiv.write_text(
        '{"start_mcap": 0, "end_mcap": 1, "start_np_ttm_visible": 1, "end_np_ttm_visible": 2}',
        encoding="utf-8",
    )
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="300750", snapshot=str(zerodiv), start=None, end=None,
    ))
    out = capsys.readouterr().out
    assert rc == 1 and "start_mcap 为 0" in out


def test_cmd_portfolio_positions_prints_state_table(tmp_path, monkeypatch, capsys):
    """P-1：--positions 输出位置状态表（档位无盈亏数值、无成本数字）。"""
    import argparse
    import json
    from unittest.mock import patch

    import invest
    from lib._invest_path import ensure_skills_lib_on_path
    ensure_skills_lib_on_path()
    from lib import collector as col

    holdings = tmp_path / "h.json"
    holdings.write_text(
        json.dumps([{"symbol": "300308", "weight": 0.4, "cost": 150.0,
                     "buy_date": "2025-06-01"}], ensure_ascii=False),
        encoding="utf-8",
    )
    with patch.object(col, "collect_kline", return_value={
        "dimension": "kline",
        "data": [{"trade_date": "2026-09-04", "close": 135.0}],
        "status": "available",
    }):
        rc = invest.cmd_portfolio(argparse.Namespace(
            holdings=str(holdings), positions=True, stress=False,
        ))
    out = capsys.readouterr().out
    assert rc == 0
    assert "标的" in out and "档位" in out
    assert "浅亏" in out            # 档位词渲染
    assert "150" not in out         # 成本数值不得进入输出
    assert "-10" not in out         # 盈亏数值不得进入输出


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


def test_cmd_attribution_type_error_message(tmp_path, capsys):
    """字段为字符串（JSON 导出常见）→ 类型错误提示而非误导性"缺字段"。"""
    import argparse
    import invest

    bad = tmp_path / "typed.json"
    bad.write_text('{"start_mcap": 1.0, "end_mcap": "0.523", '
                   '"start_np_ttm_visible": 99, "end_np_ttm_visible": 443}',
                   encoding="utf-8")
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="300750", snapshot=str(bad), start=None, end=None,
    ))
    out = capsys.readouterr().out
    assert rc == 1
    assert "类型错误" in out
    assert "缺必需字段" not in out        # 不应误导用户去补已存在的键


def test_cmd_attribution_rejects_symbol_mismatch(tmp_path, capsys):
    """review2 A-7：快照 symbol ≠ 命令 symbol → 拒绝（防把宁德分解打印成其他标的）。"""
    import argparse
    from pathlib import Path

    import invest

    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "v0.2.9"
        / "catl_2021_2023_snapshot.json"
    )
    rc = invest.cmd_attribution(argparse.Namespace(
        symbol="600176", snapshot=str(fixture), start=None, end=None,   # 错配！
    ))
    out = capsys.readouterr().out
    assert rc == 1
    assert "不符" in out and "拒绝" in out


# ---- 2026-09-18 评审批次：交付链文档断言（#1/#2）----------------------------


def _skill_md() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]      # skills/invest-a-stock/tests → 仓库根
    return (root / "skills" / "invest-a-stock" / "SKILL.md").read_text(encoding="utf-8")


def test_skill_md_plan_command_redirects_to_file():
    """#1：plan 只把 JSON 打到 stdout，文档里的 plan 必须重定向落盘。

    漏 `> /tmp/plan.json` 时链上三条 `--plan` 全读不到文件，只打一行警告后
    **静默退回 CLI 默认维度**（丢 segments/research）——采集不完整却不报错。
    """
    lines = [ln for ln in _skill_md().splitlines()
             if "invest.py plan " in ln and "--intent" in ln]
    assert lines, "SKILL.md 未文档化 plan 调用形式，请更新本解析"
    offenders = [ln for ln in lines if "> /tmp/plan.json" not in ln]
    assert not offenders, f"plan 命令未重定向落盘（--plan 将静默退回默认维度）: {offenders}"


def test_skill_md_qc_targets_current_report():
    """#2：交付链末步 qc 须点名本次产物，不得 `--latest`。

    `--latest` 按全局 mtime 取 reports/ 下最新 .md：并发或多标的运行时会复检到
    别的报告，本次产物反而漏检（闸门空转）。
    """
    text = _skill_md()
    assert "### 标准交付链" in text, "标准交付链小节缺失或改名，请更新本解析"
    chain = text.split("### 标准交付链", 1)[1].split("\n### ", 1)[0]
    qc_lines = [ln for ln in chain.splitlines() if "report_qc.py" in ln]
    assert len(qc_lines) == 1, f"交付链中 report_qc 命令数异常: {qc_lines}"
    line = qc_lines[0]
    assert "--latest" not in line, "交付链末步用 --latest：会复检 reports/ 下最新 .md（未必是本次产物）"
    target = line.split("report_qc.py", 1)[1].strip().split(" ", 1)[0]
    assert target and not target.startswith("--"), f"交付链末步未给 qc 显式目标路径: {line}"
