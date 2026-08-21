# invest-skills-skillhub

invest-skills 的 [skillhub.cn](https://skillhub.cn) 分发镜像仓库——每个 skill 一个自包含包（≤200 文件），由**主仓库 CI 自动同步**、本仓库 CI 自动发布。

> 主仓库：[Veblin/invest-skills](https://github.com/Veblin/invest-skills)（开发态，含测试）｜ 本仓库仅存**发布产物**，`skills/` 之外的骨架文件（README、workflow）仅首次 seed，之后不经主仓库同步。
>
> ⚠️ 平台区分：[skillhub.cn](https://skillhub.cn)（腾讯云，实名认证 + API key + 审核）与 **skillhub.club**（GitHub 推仓库即收录的聚合站）是两个不同平台，本仓库只针对 skillhub.cn。

## 目录

```
skills/
├── invest-a-stock/        # 个股投研
└── invest-a-etf/          # ETF 研究
```

每个包自包含：`SKILL.md`（双格式 frontmatter）+ `scripts/`（引擎）+ `references/` + `lib/`（合并共享库）+ `requirements.txt`。

其余 skill（journal/pulse/gap-scan/pattern-scan）暂不发布（跨 skill 依赖未逐一实测），主仓库构建脚本保留其支持，后续逐个实测后启用。

## 更新流程（全自动，零手工）

1. 主仓库 `bash scripts/bump-version.sh X.Y.Z`（同步版本号）
2. 主仓库打 tag `vX.Y.Z` → 主仓库 CI（`build-skillhub-mirror.yml`）自动：
   - 构建 stock + etf 两包（>200 文件守卫触发即 workflow 失败）
   - push 本仓库 `main`（commit 消息 = `sync vX.Y.Z` + CHANGELOG 条目；构建产物与 HEAD 无差异时跳过）
3. 本仓库 CI（`skillhub-publish.yml`）收到 `skills/**` 变更自动发布 → 返回 `status=pending_review`

全链路唯一手工动作是主仓库打 tag；本仓库无需任何手工操作。

## 发布（CI）

`.github/workflows/skillhub-publish.yml`：push main（`paths: ["skills/**"]` 过滤，工作流自身变更不触发）或手动触发 → 安装 CLI → `skillhub login`（`SKILLHUB_API_KEY` secret）→ 逐包 `--dry-run` 预检 → 正式发布（changelog 取同步 commit 消息）。

## 一次性前置（seed 时配置）

| 步骤 | 仓库 | 操作 |
|------|------|------|
| 1 | skillhub.cn | 注册 + 实名认证（人脸核身）；个人中心 → API keys → 创建 key（`skh_xxx`） |
| 2 | **本仓库** | Settings → Secrets → Actions → `SKILLHUB_API_KEY` = 该 key |
| 3 | **主仓库** | Settings → Secrets → Actions → `SKILLHUB_REPO_TOKEN` = PAT（仅本仓库 contents:write 权限） |
| 4 | 本地 | `skillhub search invest` 检查 slug 唯一性（冲突则改主仓库构建脚本常量） |

## 本地预检（可选）

```bash
curl -fsSL https://skillhub.cn/install/install.sh | bash -s -- --cli-only
skillhub login --key skh_xxx --host https://api.skillhub.cn
skillhub publish skills/invest-a-stock --dry-run
```

## 注意

- skillhub.cn 单包 ≤200 文件、单文件 ≤1MB、总包 ≤10MB（本仓库所有包满足）
- frontmatter 为双格式：`name/description`（标准 Agent Skills）+ `slug/displayName/version/summary/license`（skillhub.cn）
- 引擎依赖：包内 `requirements.txt`（替代 pyproject.toml，skillhub.cn 白名单不含 .toml）；首次使用 `uv venv && uv pip install -r requirements.txt`
- 同一版本重复同步时主仓库 CI 检测无差异跳过 push，不会重复发布
