# Contributors

invest-A is a personal learning tool, open-sourced for the community.

## Author

Built by [@veblin](https://github.com/veblin) — an investment learner who wanted a tool that teaches _how_ to analyze, not _what_ to buy.

## Contributing

### Ways to contribute

1. **Report bugs**: Is a data source broken? Is a LAW violated? Open an issue.
2. **Add data sources**: New free APIs for A-share/HK-share data are always welcome.
3. **Improve knowledge base**: The `knowledge/` directory is meant to grow — add clear, source-cited explanations of financial concepts.
4. **Fix LAWs violations**: If you find a report that breaks any of the 9 LAWs, that's a bug.
5. **Platform support**: Help make invest-A work on more Agent Skills harnesses (Codex, Cursor, GitHub Copilot, etc.).

### Before submitting

- Run `uv run python skills/invest-A/scripts/invest.py diagnose` to verify environment
- Run `uv run pytest` to verify tests pass
- Ensure no API keys or secrets are committed

### Cutting a release

1. 在 `CHANGELOG.md` 写好 `## vX.Y.Z` 章节（Release 正文从此提取）
2. 同步四处版本号：`SKILL.md`、`pyproject.toml`、`.claude-plugin/plugin.json`、`gemini-extension.json`
3. **合并到 `main`** → [Release Draft Notes](.github/workflows/release-draft.yml) 自动根据 `pyproject.toml` 版本创建/更新 **Draft Release**（正文来自 CHANGELOG）
4. 确认 Draft 内容后打 tag：`git tag vX.Y.Z && git push origin vX.Y.Z`
5. [Release workflow](.github/workflows/release.yml) 打包 tarball 并**正式发布**（`draft: false`）

本地预览 Release 正文：

```bash
python3 .github/scripts/extract_release_notes.py vX.Y.Z
# 或读取 pyproject.toml 当前版本
python3 .github/scripts/extract_release_notes.py --from-pyproject
```

### Design constraints

- **No buy/sell advice** — this is an absolute constraint (LAW 6)
- **No unverified claims** — every statement needs a source (LAW 1)
- **No system Python pollution** — use `uv sync` + `.venv/` for dependencies
- **Multi-harness** — the skill should work on any Agent Skills compatible runtime, not just Claude Code

---

## Inspired by

- [last30days-skill](https://github.com/mvanhorn/last30days-skill) by Matt Van Horn — Agent Skills package structure, multi-platform publishing patterns, `uv` + `pyproject.toml` dependency management
