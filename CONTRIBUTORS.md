# Contributors

invest skills is a personal learning toolkit, open-sourced for the community.

## Author

Built by [@veblin](https://github.com/veblin) — an investment learner who wanted a tool that teaches _how_ to analyze, not _what_ to buy.

## Contributing

### Ways to contribute

1. **Report bugs**: Is a data source broken? Is a LAW violated? Open an issue.
2. **Add data sources**: New free APIs for A-share/HK-share data are always welcome.
3. **Improve knowledge base**: The `knowledge/` directory is meant to grow — add clear, source-cited explanations of financial concepts.
4. **Fix LAWs violations**: If you find a report that breaks any of the 9 LAWs, that's a bug.
5. **Platform support**: Help make invest:a-stock work on more Agent Skills harnesses (Codex, Cursor, GitHub Copilot, etc.).

### Before submitting

- Follow the applicable stages in [Development workflow](docs/development-workflow.md); keep implementation, independent verification and product acceptance evidence distinct.
- Run `uv run python skills/invest-a-stock/scripts/invest.py diagnose` to verify environment
- Run `uv run pytest` to verify tests pass
- Ensure no API keys or secrets are committed

### Cutting a release

**发布前检查清单**（原 `AGENTS.md` 维护，v0.3.1 迁入此处——版本与发布细节属本文件职责）：

- [ ] `CHANGELOG.md` 已更新（`###` 小节标题 = Release 正文「主要修改」清单，正文自动精简）
- [ ] 按 [开发执行与验收流程](docs/development-workflow.md) 留有版本主目标验收记录；未达项和未验证项已明确，不能仅凭测试通过签认整体完成
- [ ] `bash scripts/bump-version.sh X.Y.Z` 已执行（`pyproject.toml` 为唯一 canonical 源）→ `uv run python scripts/sync_version.py check` 通过
- [ ] `.claude-plugin/marketplace.json` 描述准确
- [ ] `.agents/plugins/marketplace.json` 与 claude-plugin 描述同步
- [ ] `gemini-extension.json.in` env vars 与 `.env.example` 一致
- [ ] `uv run pytest` 通过
- [ ] `uv run python skills/invest-a-stock/scripts/invest.py diagnose` 输出正常
- [ ] `bash scripts/build_wb_package.sh` 可运行，`dist/invest-skills-wb-vX.Y.Z.zip` 内容完整（发布时由 `release.yml` 自动构建并随 Release 附带，此条为本地预检）
- [ ] `invest-a-stock`/`invest-a-etf` 的 `SKILL.md` 为最新规格（工作流、反模式完整）
- [ ] 无 API Key 或敏感信息泄露（`validate.yml` Security scan 内联 secrets grep 已验证）

1. 在 `CHANGELOG.md` 写好 `## vX.Y.Z` 章节（Release 正文从此提取）
2. 运行 `bash scripts/bump-version.sh X.Y.Z`（同步 pyproject + SKILL + plugin + marketplace + gemini 共 5 文件）
3. **合并到 `main`** 前可选：`INVEST_RUN_E2E=1 uv run pytest skills/invest-a-stock/tests/test_v017_e2e.py -v`（四标的 live 冒烟）
4. **合并到 `main`** → [Release Draft Notes](.github/workflows/release-draft.yml) 自动根据 `pyproject.toml` 版本创建/更新 **Draft Release**（正文来自 CHANGELOG）
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
