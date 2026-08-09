# 用户级 `~/.workbuddy/memory/MEMORY.md` 模板

> 用途：WorkBuddy 用户级记忆文件（对应 Claude Code 的 `~/.claude/CLAUDE.md` 记忆层）。
> 复制以下内容到 `~/.workbuddy/memory/MEMORY.md`（Windows: `%USERPROFILE%\.workbuddy\memory\MEMORY.md`），
> 替换 `<repo 绝对路径>`。skill 执行规范一律以各 SKILL.md 为准，本文件仅存运行命令速查与项目背景。

```markdown
# Memory — invest-skills（WorkBuddy）

> 项目：A 股投研技能集（invest-skills）。仓库：`<repo 绝对路径>`，
> 环境变量 `INVEST_SKILLS_ROOT=<repo 绝对路径>`。研究工具，非决策工具。

## 运行命令速查（全部必须带 cd 前缀；cwd 默认仓库根）

### 个股研究（invest-a-stock）

```bash
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py diagnose    # 数据源可用性
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py collect 600176          # 采集（--with-news-pack 新闻三层）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py report 600176           # 报告（--mode brief|full|concise）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py rigor 600176 --verify-all  # 财务验算
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py check 600176            # 7 指标质地检查
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858   # 双标的对比
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py diff 600176             # 两次快照对比
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py store list              # 历史采集记录
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py audit report.md --extract|--verdict  # 报告审计
```

### ETF（invest-a-etf）

```bash
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py report 563300 --json   # 数据快照（--history --playbook 历史深度）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py industry-pe            # 31 行业 PE 排名
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py collect-weekly         # 手动触发行业 PE 采集
```

### 市场脉搏（invest-a-pulse）与缺口扫描（invest-a-gap-scan）

```bash
# pulse 采集块见 SKILL.md（引擎在 invest-a-journal/scripts/lib，须从该目录跑）：
cd "${INVEST_SKILLS_ROOT:-.}/skills/invest-a-journal/scripts/lib" && uv run python -c "from market_microstructure import snapshot; import json; print(json.dumps(snapshot(), ensure_ascii=False))" 2>/dev/null
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-gap-scan/scripts/scan.py                  # 跳空缺口扫描（--json 供脚本消费）
```

### 交易日志（invest-a-journal）

引擎在 `skills/invest-a-journal/scripts/lib`（query_data / market_microstructure / db / etf_data），
必须从该目录运行；落库走 `db.save_journal`（含 evaluation_json；卖出自动关联最近 buy）。

## 项目背景

- 核心原则：P0 — AI 禁止做数学计算，所有数字必须来自 Python 引擎；报告复检三层（数字/合规/逻辑）
- 多源降级链：L3 行情类串联降级（防限流），L2 财务类并行双源先到先用；失败不阻塞
- 禁止买卖建议（LAW 6）；允许多情景估值参考价（须假设前提+概率权重）；允许交易结构分析（入场区间/假设失效触发/操作纪律）
- 东财 API 需直连：Clash 配 `DOMAIN-SUFFIX,eastmoney.com,DIRECT`（另加 gtimg.cn / baostock.com / tickflow.org）
- 版本规则：v{major}.{minor}.{patch}，canonical 源为 pyproject.toml
```

## 放置位置说明

| 平台 | 路径 |
|------|------|
| macOS | `~/.workbuddy/memory/MEMORY.md` |
| Windows | `%USERPROFILE%\.workbuddy\memory\MEMORY.md` |

修改后重启 WorkBuddy 客户端生效。
