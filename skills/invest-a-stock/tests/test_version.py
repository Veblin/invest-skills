"""Tests for lib/version.py and ENGINE_VERSION wiring."""

from __future__ import annotations

from pathlib import Path


class TestPackageVersion:
    def test_get_package_version_matches_pyproject(self):
        from lib.version import get_package_version

        ver = get_package_version()
        assert ver != "unknown"

        root = Path(__file__).resolve().parents[3]
        pyproject = root / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        in_project = False
        expected = None
        for raw in text.splitlines():
            line = raw.strip()
            if line == "[project]":
                in_project = True
                continue
            if line.startswith("[") and line.endswith("]"):
                in_project = line == "[project]"
                continue
            if in_project and line.startswith("version") and "=" in line:
                expected = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        assert expected is not None
        assert ver == expected

    def test_render_engine_version_matches_package(self):
        from lib.render import ENGINE_VERSION
        from lib.version import get_package_version

        assert ENGINE_VERSION == get_package_version()
