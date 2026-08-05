"""Package version from pyproject.toml [project].version."""

from __future__ import annotations

from pathlib import Path


def get_package_version(default: str = "unknown", *,
                        stop_at_first: bool = False,
                        _start_dir: Path | None = None) -> str:
    """Read invest:a-stock version from the nearest pyproject.toml [project] section.

    stop_at_first=True 时遇到第一个存在的 pyproject.toml 即停止（gap report_formatter
    语义，无论其中是否含 [project].version）；False 时继续向上直至找到 version 字段
    （stock 语义）。_start_dir 为私有测试钩子。
    """
    try:
        root = _start_dir or Path(__file__).resolve().parent
        for parent in [root, *root.parents]:
            pp = parent / "pyproject.toml"
            if not pp.exists():
                continue
            in_project = False
            for raw in pp.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line == "[project]":
                    in_project = True
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_project = line == "[project]"
                    continue
                if in_project and line.startswith("version") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
            if stop_at_first:
                break
    except OSError:
        pass
    return default
