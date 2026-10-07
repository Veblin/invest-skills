"""开发模式日志（canonical，共享层）。

INVEST_DEV=1 → stderr INFO（gap-scan 格式）+ 轮转文件；
release（默认）→ 不做任何事（root lastResort 仅 WARNING，零文件 I/O）。

**文件身份判据**：「仅限当前项目」不靠文档约定，靠文件身份——只有真身就是
`<repo>/skills/lib/logutil.py`（且根有 `.git`）时才落盘；两种**分发副本**布局
（SkillHub 的 `<pkg>/scripts/lib/logutil.py`、WorkBuddy 的 `<pkg>/skills/lib/logutil.py`）
永不写文件，与 `INVEST_DEV` 设没设无关。

本模块刻意**只依赖 stdlib**（不 import `env` / `invest_path` / `report_qc`）：
① 共享层裸模块，无循环导入面；② 构建器把共享层文件原样落进包内 `scripts/lib/`，
   若带相对导入（如 `from . import env`）会在包内 ImportError——包内没有 `env.py`。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 7
_setup_done = False
_setup_skill = ""  #: 首个完成初始化的 skill 名（见 setup_logging 的幂等分支说明）


def _safe_tag(skill: str) -> str:
    """skill 名进 logging 格式串前的转义。

    格式串里的裸 `%` 会被 `logging.Formatter` 当成字段占位符：`skill="invest-100%"`
    会让每条记录抛 `TypeError`、打印 "--- Logging error ---" 后**丢弃该条**——日志
    全空且每行一段栈。
    """
    return skill.replace("%", "%%")


def _safe_filename_part(skill: str) -> str:
    """skill 名进文件名前的过滤：路径分隔等字符会写到 `logs/` 之外（如 `../x`）。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", skill)


def _repo_root_from(file_path: Path) -> Path | None:
    """从文件位置向上找带 `pyproject.toml` 的项目根；找不到返回 None。

    只认 `pyproject.toml` 作标记（不认 `.env`：用户 home 下可能杂散存在，
    会造出假阳性项目根）。本函数只回答「根在哪」，是否**真身**由
    `_canonical_log_dir()` 的恒等比较回答。
    """
    start = file_path.resolve().parent
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def _canonical_log_dir(file_path: Path) -> Path | None:
    """真身判据：仅当 `file_path` 正是本仓的 `skills/lib/logutil.py` 时返回 `<repo>/logs`。

    **为什么恒等比较之外还要 `.git`**：WorkBuddy 打包器把 `skills/` rsync 进包、
    并把 `pyproject.toml`/`uv.lock` 拷到包根，使包内副本 `<pkg>/skills/lib/logutil.py`
    与包根恰好满足「根下有 skills/lib/logutil.py」这一形状——只比路径会把**分发副本
    判成真身**，于是往用户安装目录写 dev 日志。工作副本才有 `.git`（worktree 下是
    文件，`.exists()` 同样成立），据此区分。

    四种布局的结果：

    | file_path | 结果 |
    |---|---|
    | `<repo>/skills/lib/logutil.py`（根有 `.git`） | `<repo>/logs` |
    | WorkBuddy 包 `<pkg>/skills/lib/logutil.py` | None |
    | `<repo>/<pkg>/scripts/lib/logutil.py`（分包副本） | None |
    | 仓外临时路径（无 pyproject.toml 祖先） | None |

    代价：从无 `.git` 的源码压缩包运行时不落盘（退化为仅 stderr）。这是有意的——
    无法区分「源码树」与「分发副本」时，宁可不写。
    """
    root = _repo_root_from(file_path)
    if root is None:
        return None
    if (root / "skills" / "lib" / "logutil.py").resolve() != file_path.resolve():
        return None
    if not (root / ".git").exists():
        return None
    return root / "logs"


def repo_logs_dir() -> Path | None:
    """本模块真身所在仓库的 `logs/` 目录；非真身布局返回 None。"""
    return _canonical_log_dir(Path(__file__))


def setup_logging(
    dev: bool | None = None,
    skill: str = "",
    *,
    log_dir: Path | str | None = None,
) -> bool:
    """启用开发模式日志；返回是否实际启用。幂等（重复调用不重复挂 handler）。

    dev: None 时读 `INVEST_DEV == "1"`。
    skill: 日志行携带的 skill 名（如 `invest-a-gap-scan`），便于跨 skill 辨别。
    log_dir: 仅供测试的落盘目录覆盖；**不豁免身份门**——非真身即使显式传入也不落盘。

    显式 handler 构建而非 `basicConfig`：root 已有 handler 时 `basicConfig` 会 no-op。
    release 分支直接返回，不碰 root logger（lastResort 行为原样保留）。
    """
    global _setup_done, _setup_skill
    if dev is None:
        dev = os.environ.get("INVEST_DEV") == "1"
    if _setup_done:
        # 幂等，但**进程内换 skill 不会再配置**：handler 与文件名绑定首个调用方，
        # 后续 skill 的行会带着前一个 skill 的标识、写进前一个文件。
        # 逐 skill 隔离的前提是**逐进程**（各入口独立 CLI 调用）；进程内组合两个 CLI
        # （如 INVEST_DEV=1 下跑 pytest 会话）会命中这一限制——说出来，不静默错标。
        if skill and _setup_skill and skill != _setup_skill:
            print(
                f"[logutil] 已在 skill={_setup_skill!r} 下初始化，"
                f"本次 skill={skill!r} 不会被单独标识/分文件（逐 skill 隔离按进程生效）",
                file=sys.stderr,
            )
        return True
    if not dev:
        return False

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    tag = f"[{_safe_tag(skill)}] " if skill else ""
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(
        f"%(asctime)s [%(levelname)s] {tag}%(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)

    canonical = _canonical_log_dir(Path(__file__))
    if canonical is not None:
        target = Path(log_dir) if log_dir is not None else canonical
        stamp = f"{datetime.now():%Y%m%d}"
        name = (f"invest_{_safe_filename_part(skill)}_{stamp}.log" if skill
                else f"invest_{stamp}.log")
        try:
            target.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                target / name,
                maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(logging.Formatter(
                f"%(asctime)s [%(levelname)s] {tag}%(name)s: %(message)s"))
            root.addHandler(fh)
        except OSError:
            pass  # 目录不可写不致命，退化为仅 stderr
    _setup_done = True
    _setup_skill = skill
    return True


__all__ = ["setup_logging", "repo_logs_dir"]
