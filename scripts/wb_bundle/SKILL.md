---
name: invest-skills
description: "A股投研技能集：个股投研、ETF研究、交易日志、市场情绪脉搏、跳空缺口扫描、底部形态扫描，六大技能共用一个 Python 数据引擎，产出带来源追溯的研究备忘录。研究工具，非决策工具。触发词：个股投研/估值/财报/ETF/指数基金/交易日志/买入评估/卖出评估/市场情绪/大盘/市场脉搏/缺口扫描/跳空扫描/双底/形态扫描/三角形底/底部形态"
agent_created: true
user-invocable: true
---

# invest-skills（A股投研技能集入口）

本包是 6 个投研技能的 bundle：**共用一个 Python 数据引擎**，子技能是流程指令 + 引擎命令。

## 子技能路由表

| 用户意图（触发词） | 先 Read 的文件 | 说明 |
|---|---|---|
| 个股投研 / 估值 / 财报 | `skills/invest-a-stock/SKILL.md` | 九模块研究备忘录 |
| ETF / 指数基金 | `skills/invest-a-etf/SKILL.md` | 指数估值/折溢价/跟踪质量 |
| 交易日志 / 买入评估 / 卖出评估 | `skills/invest-a-journal/SKILL.md` | 四维评估 + 落库 |
| 市场情绪 / 大盘 / 市场脉搏 | `skills/invest-a-pulse/SKILL.md` | 五维情绪 + 环境标签 |
| 缺口扫描 / 跳空扫描 | `skills/invest-a-gap-scan/SKILL.md` | 成分股池缺口扫描 |
| 双底 / 形态扫描 / 三角形底 / 底部形态 | `skills/invest-a-pattern-scan/SKILL.md` | LMW 双底/三角形底 |

**执行约定**：识别意图后，先 Read 对应子技能 SKILL.md 并完整遵循其 SOP；子技能引用的 references/ 在其各自目录或 `skills/lib/references/` 下。

## 环境约定（重要）

本包内子技能命令统一形如：

```
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/<子技能>/scripts/xxx.py ...
```

执行时把 `${INVEST_SKILLS_ROOT:-.}` 解析为包根（含 `skills/` 与 `pyproject.toml` 的目录），按优先级：

1. 环境变量 `INVEST_SKILLS_ROOT` 已设置 → 用它；
2. 否则 `~/.config/investment/install_root` 存在 → 用其文件内容（bootstrap 会写入）；
3. 否则当前目录下直接可见 `skills/` 与 `pyproject.toml`（已在包根）→ 用 `.`。

三种都不满足时：先运行 step 0 自检，或询问用户包根位置。

## Step 0 — 首次使用：环境自检（每个新会话先跑一次）

运行（`install_root` 不存在而你已定位包根时，用包根绝对路径替换 `$(...)` 部分）：

```bash
bash "$(cat ~/.config/investment/install_root 2>/dev/null || echo .)/scripts/bootstrap.sh"
```

- 缺 uv（Python 包管理器）→ bootstrap 给出 1 条安装命令；macOS 加 `--install` 参数可代装
- 缺 token → **不阻塞**：akshare 免费数据源可用；进阶 token（TUSHARE/FRED 等）见 README-安装.md，用户可在对话中直接提供 token，由你代写 `~/.config/investment/.env`（权限 600，9 个 key 清单见 `docs/workbuddy/env-template.md` 的仓库版）
- 自检通过后按路由表执行子技能

## 输出位置

- 个股/ETF 报告：包根 `reports/{symbol}-{name}/`（时间戳命名）
- 交易日志 / 情绪 / 扫描报告：`reports/journal/`、`reports/pulse/`、`reports/gap-scan/`
- 采集数据缓存：`~/.local/share/investment/`（引擎自动管理，勿手动改）

## 边界

研究工具，非决策工具：不提供买卖/仓位建议；所有数字带来源标注；多情景估值仅供学习参考，不构成投资建议。
