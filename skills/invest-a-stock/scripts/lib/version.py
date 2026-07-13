"""Package version from pyproject.toml [project].version."""

from __future__ import annotations

from pathlib import Path


def get_package_version() -> str:
    """Read invest:a-stock version from the nearest pyproject.toml [project] section."""
    try:
        root = Path(__file__).resolve().parent
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
    except OSError:
        pass
    return "unknown"
