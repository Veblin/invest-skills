"""Tests for compliance lint rule loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


class TestLintRulesLoad:
    def test_load_rules_raises_when_pyyaml_missing(self):
        from lib import lint as lint_mod

        lint_mod._RULES_CACHE = None
        with patch.object(lint_mod, "yaml", None):
            with pytest.raises(lint_mod.RulesLoadError, match="pyyaml"):
                lint_mod.load_rules()
        lint_mod._RULES_CACHE = None

    def test_lint_file_raises_when_rules_unavailable(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("# test\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        with patch.object(lint_mod, "load_rules", side_effect=lint_mod.RulesLoadError("test")):
            with pytest.raises(lint_mod.RulesLoadError):
                lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None

    def test_load_rules_succeeds_with_yaml(self):
        from lib import lint as lint_mod

        lint_mod._RULES_CACHE = None
        rules = lint_mod.load_rules()
        assert isinstance(rules, list)
        assert len(rules) > 0
        lint_mod._RULES_CACHE = None
