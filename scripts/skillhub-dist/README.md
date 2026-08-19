# invest-skills-skillhub

invest-skills 的 SkillHub 分发仓库——每个 skill 一个自包含包（≤200 文件），经 CI 自动发布到 [skillhub.cn](https://skillhub.cn)。

> 主仓库：[Veblin/invest-skills](https://github.com/Veblin/invest-skills)（开发态，含测试）｜ 本仓库仅存**发布产物**。

## 目录

```
skills/
├── invest-a-stock/        # 个股投研（126 文件）
├── invest-a-etf/          # ETF 研究（54 文件）
├── invest-a-journal/      # 交易日志（45 文件）
├── invest-a-pulse/        # 市场情绪（36 文件）
├── invest-a-gap-scan/     # 缺口扫描（47 文件）
└── invest-a-pattern-scan/ # 形态扫描（41 文件）
```

每个包自包含：`SKILL.md`（双格式 frontmatter）+ `scripts/`（引擎）+ `references/` + `lib/`（合并共享库）+ `requirements.txt`。

## 更新流程

1. 主仓库 `bash scripts/bump-version.sh X.Y.Z`
2. 主仓库生成分发包：
   ```bash
   uv run python scripts/build_skillhub_packages.py --out ../invest-skills-skillhub/skills
   ```
   （或 `.venv/bin/python`；脚本仅依赖标准库）
3. 本仓库 `git add skills/ && git commit`（diff 可审：仅版本号/引擎变更）
4. `git tag vX.Y.Z && git push origin main --tags` → CI 自动发布

## 发布（CI）

`.github/workflows/skillhub-publish.yml`：push tag 或手动触发 → 安装 CLI → `skillhub login`（`SKILLHUB_TOKEN` secret）→ 逐包 `--dry-run` 预检 → 正式发布。

### 一次性前置（需在 skillhub.cn 完成）

1. 注册 + 实名认证（人脸核身）
2. 个人中心 → API keys → 创建 key（`skh_xxx`）
3. 本仓库 Settings → Secrets → `SKILLHUB_TOKEN` = 该 key
4. `slug` 唯一性：发布前 `skillhub search invest` 检查（冲突则改 slug）

## 本地预检（可选）

```bash
curl -fsSL https://skillhub.cn/install/install.sh | bash -s -- --cli-only
skillhub login --key skh_xxx --host https://api.skillhub.cn
skillhub publish skills/invest-a-stock --dry-run
```

## 注意

- skillhub.cn 单包 ≤200 文件、单文件 ≤1MB、总包 ≤10MB（本仓库所有包满足）
- frontmatter 为双格式：`name/description`（标准 Agent Skills）+ `slug/displayName/version/summary/license`（skillhub.cn）
- 引擎依赖：包内 `requirements.txt`（替代 pyproject.toml，skillhub 白名单不含 .toml）；首次使用 `uv venv && uv pip install -r requirements.txt`
