# invest-skills — A 股投研技能集

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-skills/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-skills/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-skills/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-skills?include_prereleases&label=v0.2.5" alt="Release" /></a>
</p>

**A 股投研助手：把数小时的手工研究压缩到几分钟。** 输入代码，自动采集多源数据、以科学方法计算、按学术框架分析，产出带来源追溯的研究备忘录。学习工具，非决策工具。

> 普通 AI 给你「看起来对的分析」；invest-skills 给你「能拿来做决策的研究备忘录」。

---

## 价值主张

**最大的价值是省时间**：数据采集、交叉验证、报告撰写——数小时的手工流程压缩到几分钟。

省下的时间之外，报告质量由四层能力支撑，目标是**尽可能覆盖常见认知错误、你没关注到的关键信息，减少误判**：

- **广泛的相关因子数据** — 6 数据源并行采集（财务/行情/估值/股东/资金/市场），单源失败不阻塞，跨源差异显式标注
- **科学的计算方法** — 分位/偏离度/波动率等衍生指标全部由 Python 引擎计算，AI 只引用不加工
- **学术的研究框架** — R1-R12h 框架匹配、16 条法则约束、证据强度四维标注、来源追溯
- **AI 整合引擎** — 多 Agent 并行采集与分析，冲突并列不掩盖，主编统一合成

## 它如何解决你的问题

| 你遇到的问题 | 直接问 AI 的结果 | 用 invest-skills |
|:---|:---|:---|
| 数字靠不住 | AI 心算、编数字、张冠李戴 | 禁止 AI 做数学计算（P0）：所有数字由 Python 引擎计算，AI 只引用不加工 |
| 数据源单一、会失效 | 单源拉取，过时/封禁/限流就挂 | 6 数据源并行采集 + 降级链：单源失败不阻塞，跨源差异显式标注 |
| 说法无法验证 | 无来源、「据消息称」 | 每条事实标注来源，每条判断标注依据；事实边界三态标注（可验证/不可验证/未知） |
| 报告像营销文案 | 形容词堆砌，只说好不说坏 | 16 条法则约束 + 三层复检（数字/合规/逻辑），Bull/Bear 必须数值化传导 |
| 研究不可复现 | 换个话题换个说法 | 同输入 → 同结构输出；数据入库 SQLite，支持快照 diff 回溯 |

## 典型应用场景

| 场景 | 怎么做 | 得到什么 |
|:---|:---|:---|
| 看上一家公司 | `/invest-a-stock 600176` | 九模块研究备忘录（[示例报告](#示例报告)） |
| 财报数字可信吗 | 对话中要求「财务验算」 | 市值/估值/跨源验算，差异 >20% 触发仲裁 |
| 两只票纠结 | 对话中要求「双标对比」 | 同结构对比 |
| 想投 ETF | `/invest-a-etf 563300` | 指数估值/折溢价/AUM/跟踪质量 |
| 交易方案拿不准 | `/invest-a-journal` | 四维评估：逻辑/盲点/仓位匹配/风险收益 |
| 今天市场什么状态 | `/invest-a-pulse` | 杠杆/广度/情绪/资金/估值全景 + 行业轮动 |
| 等回补缺口 | `/invest-a-gap-scan` | 向上缺口 + MA60 上方 + 未回补扫描 |

## 快速开始（5 分钟）

```bash
git clone https://github.com/Veblin/invest-skills.git && cd invest-skills
uv sync
cp .env.example .env                     # 填入 TUSHARE_TOKEN（tushare.pro 注册即送）
uv run python skills/invest-a-stock/scripts/invest.py diagnose     # 验证数据源
```

然后在 Claude Code 里开始研究：

```
/plugin marketplace add Veblin/invest-skills
/invest-a-stock 600176
```

不装客户端也完全可用——命令行提供等价能力（27 个子命令），见 [docs/cli.md](docs/cli.md)。

## 安装

配置 Token（至少一个）：

| Key              | 作用                      | 获取                                                                          |
| ---------------- | ------------------------- | ----------------------------------------------------------------------------- |
| `TUSHARE_TOKEN`  | 财务/估值/资金/股东       | [tushare.pro](https://tushare.pro) 注册即送                                   |
| `FRED_API_KEY`   | 美国 10Y 国债（DCF WACC） | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) 免费 |
| `TAVILY_API_KEY` | 新闻搜索补充              | [tavily.com](https://tavily.com) 免费                                         |

| 方式 | 命令 |
|:---|:---|
| Claude Code（推荐） | `/plugin marketplace add Veblin/invest-skills` |
| npx skills | `npx skills add Veblin/invest-skills` |
| Hermes Agent | 安装 `Veblin/invest-skills` 插件后自然语言调用，如「用 invest-a-stock 研究 600176」 |
| 手动（开发者） | `git clone` + `uv sync` |

Tushare 积分档位与功能对照见 [CONFIGURATION.md](CONFIGURATION.md)。

> **命名约定**：用户 slash 一律连字符（`/invest-a-*`）。Claude 插件 marketplace 包名可保留冒号（`invest:a-stock`），二者不是同一层。

### WorkBuddy 安装

WorkBuddy 与 Claude Code 共享同一套 SKILL.md 格式（同源同构），引擎命令已统一带 `${INVEST_SKILLS_ROOT:-.}` cd 前缀，双 harness 兼容。3 步安装：

1. **安装 WorkBuddy 桌面版**（macOS / Windows）并登录（新用户 14 天全功能试用；免费版并行上限 2，付费版 8）
2. **拷贝或 symlink skills**：将本仓库 `skills/invest-a-{stock,etf,journal,pulse,gap-scan}` 放入技能目录（macOS 推荐 symlink：`ln -sfn <repo>/skills/invest-a-pulse ~/.workbuddy/skills/invest-a-pulse`，仓库已预置 `.workbuddy/skills/` 下 5 个 symlink 可直接 `ln -sfn` 链接；Windows 见下方专节）
3. **配置环境**：export `INVEST_SKILLS_ROOT=<repo 绝对路径>`（macOS 写 `~/.zshrc`；Windows 设用户级环境变量）+ 写全局 token 文件 `~/.config/investment/.env`（9 个 token 清单与写入说明见 [docs/workbuddy/env-template.md](docs/workbuddy/env-template.md)）

**权限模式**：建议在 WorkBuddy 中使用 Craft 模式（免确认执行）——本套技能需频繁调用 Bash 引擎命令，逐条确认体验差。

**常见坑**：

- **技能索引未更新**：装完技能后搜索/唤起不到 → 删除 `index.db` / `fts_index.db` 重启客户端（约 10s 自动重建），或使用 `/reload-skills`
- **东财被代理阻断**：Clash/VPN 需配置 DIRECT 规则：`DOMAIN-SUFFIX,eastmoney.com / gtimg.cn / baostock.com / tickflow.org`
- **marketplace vs SkillHub**：marketplace 插件仅 4 个（invest:a-stock / etf / journal / gap-scan，模板缺 invest-a-pulse，既有缺口）；SkillHub 上架 5 个 skill（含 invest-a-pulse，中文 description 已带触发词）
- **`.workbuddy/skills` symlink 解析**：仓库已提交 git symlink，WorkBuddy 是否解析 symlink 需真机验证；若不解析，备选方案为 lean copy 脚本（只拷贝 SKILL.md + references/，引擎经 `INVEST_SKILLS_ROOT` 调用，体积约 KB 级，不推荐整目录 copy ~12MB）

#### macOS

```bash
export INVEST_SKILLS_ROOT="/path/to/invest-skills"   # 追加到 ~/.zshrc
mkdir -p ~/.workbuddy/skills
ln -sfn "$INVEST_SKILLS_ROOT/.workbuddy/skills/invest-a-stock" ~/.workbuddy/skills/invest-a-stock
ln -sfn "$INVEST_SKILLS_ROOT/.workbuddy/skills/invest-a-etf"   ~/.workbuddy/skills/invest-a-etf
ln -sfn "$INVEST_SKILLS_ROOT/.workbuddy/skills/invest-a-journal" ~/.workbuddy/skills/invest-a-journal
ln -sfn "$INVEST_SKILLS_ROOT/.workbuddy/skills/invest-a-pulse"  ~/.workbuddy/skills/invest-a-pulse
ln -sfn "$INVEST_SKILLS_ROOT/.workbuddy/skills/invest-a-gap-scan" ~/.workbuddy/skills/invest-a-gap-scan
```

#### Windows

- **技能目录路径社区有分歧**（`.workbuddy\skills` vs `WorkBuddy\Claw\skills`），**须真机实测**后选择
- **Bash 双通道**：WorkBuddy 有 Bash + PowerShell 双通道，引擎调用统一走 Bash（git-bash），`cd "${INVEST_SKILLS_ROOT:-.}/..."` 语法在 git-bash 可用
- **ACP 安全策略可能拦截 python.exe 启动**（社区实测；Shell 子系统有间歇性静默失败报告）——若 Bash 不可用，转 MCP 包装（FastMCP stdio 包 invest.py 子命令）
- **环境变量**：用户级环境变量（注册表）设置后必须**完全重启客户端**才生效；`~/.config/investment/.env` 方案与平台无关（引擎原生加载），**推荐优先**
- **权限**：默认执行脚本需逐条确认；Full Access 模式免确认

#### T1-T12 真机验收表（用户后置执行）

> 阻塞项：WorkBuddy 安装（由用户后置执行）。判定标准：同一输入，WorkBuddy 输出与 Claude Code 逐项一致（含数据值、格式、落盘位置）。

| # | 用例 | 通过标准 | 阻塞项 |
|---|------|---------|--------|
| T1 | `/invest-a-pulse` 全流程 | 5 维分析 + market_snapshots 入库 | WB 安装 |
| T2 | `collect 600176 --with-macro --with-news-pack` | 11 维度采集 + 新闻三层 | token .env、120s 超时 |
| T3 | `report 600176 --mode brief` | reports/ 落盘、模板一致 | — |
| T4 | journal Q&A 全流程 | ≤4 问×4 选项、两轮分拆、save_journal 落库 | AskUserQuestion 实测 |
| T5 | `--deep`（付费版） | 3+4 Agent 并行 | 付费版决策 |
| T6 | `--deep` 免费版降级 | 串行完成 | 并行上限实测 |
| T7 | collect 超 120s | 自动后台化不杀进程 | WB 超时行为实测 |
| T8 | SessionStart hooks | check-config.sh 正常 | hooks 官方未背书 |
| T9 | token 生效 | 无 .env 降级链、写入后完整 | — |
| T10 | journal ETF 路径 | etf_data shim 加载成功 | — |
| T11 | gap-scan 全量 | reports/gap-scan/ 落盘、命中数一致 | 耗时 |
| T12 | 用户唤起 | /invest-a-stock、@skill:name、description 自动触发 | 索引重建坑 |

## Skills 一览

| Skill | 解决什么问题 | 入口 |
|:---|:---|:---|
| **invest-a-stock** | 单标的多因子交叉验证研究（9 模块） | `/invest-a-stock 600176` |
| **invest-a-etf** | ETF 结构化研究（估值/折溢价/AUM/跟踪质量） | `/invest-a-etf 563300` |
| **invest-a-journal** | 交易方案四维评估（逻辑/盲点/仓位/风险收益） | `/invest-a-journal` |
| **invest-a-pulse** | 市场情绪全景（杠杆/广度/情绪/资金/估值） | `/invest-a-pulse` |
| **invest-a-gap-scan** | 跳空缺口扫描（向上缺口+MA60 上方+未回补） | `/invest-a-gap-scan` |

## 一张图：流程如何固化到 Python

```
        ┌─────────────────────────────────────────────────────┐
        │  Skill 层 — 你的对话                                  │
        │  /invest-a-stock 600176 · /invest-a-pulse · ...      │
        │  标准化流程 + 个性化要求 → 研究备忘录                  │
        └───────────────────────┬─────────────────────────────┘
        ┌───────────────────────▼─────────────────────────────┐
        │  规则层 — AI 不得越过的边界                           │
        │  R1-R12h 框架匹配 · 16 条法则 · 三层复检              │
        │  事实/分析分离 · 证据四维标注 · 禁止买卖建议           │
        └───────────────────────┬─────────────────────────────┘
        ┌───────────────────────▼─────────────────────────────┐
        │  引擎层 — Python 固化：数据获取 + 计算                 │
        │  6 源并行采集 ─→ 降级链 ─→ 交叉验证                    │
        │  （单源失败不阻塞 · 差异 >20% 触发 tie-breaker）        │
        │  derived 计算引擎：分位/偏离度/波动率/带宽              │
        │  —— 所有数字引擎计算，AI 只引用不加工                   │
        └───────────────────────┬─────────────────────────────┘
        ┌───────────────────────▼─────────────────────────────┐
        │  存储层                                              │
        │  SQLite 快照 → diff 回溯 · 多 Agent 并行编排          │
        └─────────────────────────────────────────────────────┘
```

规则、流程与计算全部固化在 Python 引擎（`skills/*/scripts/`），AI 按规则执行、按框架产出。脚本级操作（27 个 CLI 子命令的完整参考）见 [docs/cli.md](docs/cli.md)。

### 产出流水线

```
Phase 1: 并行采集（3 Agent）
  Collector A (Tushare) ∥ Collector B (akshare交叉) ∥ Collector C (股东/研报)
  → merge + 交叉验证，差异 >20% 触发 tie-breaker

Phase 2: 并行分析（4 Agent）
  生意质量 ∥ 财务估值 ∥ 行业竞争 ∥ 风险治理

Phase 3: 主编合成 → .md 报告
```

模板见 [references/agent-prompts.md](skills/invest-a-stock/references/agent-prompts.md)。

## 示例报告

真实产出示例（2026-07-21，潍柴动力 000338）：

> **核心结论**：潍柴动力正经历从重卡周期股向 AIDC 电力基础设施龙头的战略转型。当前 PE(TTM) 20.3x 处于近 4 年 88.1% 分位（中位数 15.1x），看似昂贵——但 2026E 前瞻 PE 仅 16x（EPS 1.65），且 AIDC 柴发业务以 240%+ 增速贡献 70% 以上增量利润。
>
> [证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 引擎采集+WebSearch+机构研报 交叉一致]

| 维度 | 结论 | 关键数据 | 置信度 |
|:---|:---|:---|:---:|
| 估值 | PE 20.3x 历史高分位，但前瞻 16x 合理 | PE 20.3x vs 中位 15.1x vs 2026E 16x | ⚠️ |
| 经营 | Q1 利润 +13.8%，AIDC 柴发 +240% 成为核心增量 | Q1 归母 30.85 亿；大缸径柴发 >500 台 | ✅ |
| 资金 | 控股股东增持 2-4 亿 + 22 家机构一致看多 | 增持公告 7/21；22/22 机构买入/增持 | ✅ |
| 风险 | 重卡周期下行 + 跨源差异 102% | 60 日最大回撤 24.9%；Tushare vs akshare 差异 102.4% | 🟡 |

注意风险行：**数据源打架时，报告显式标注差异并给出验证路径，而不是悄悄取一个数**。全文见 [docs/demos/000338-潍柴动力-2026-07-21.md](docs/demos/000338-潍柴动力-2026-07-21.md)。

更多 demo（真实运行产出）：

| Demo | 类型 |
|:---|:---|
| [300308-中际旭创-复盘-2026-08-04](docs/demos/300308-中际旭创-复盘-2026-08-04.md) | 多报告对比复盘（追踪认知漂移） |
| [588000-科创50ETF-2026-08-08](docs/demos/588000-科创50ETF-2026-08-08.md) | ETF 研究备忘录 |
| [market-pulse-2026-08-03](docs/demos/market-pulse-2026-08-03.md) | 市场情绪脉搏 |

> 以上为精选 demo；日常研究产出的全部报告保存在本地 `reports/` 目录（不纳入版本库）。

## 数据

多源并行采集，单源失败不阻塞，差异标注跨源分歧。采集层遵循降级链（R12h）：首选源单发，失败按序降级（防东财限流/首选源挂死）；所有源独立记录，全失败标注「未获取到任何有效数据」。

| 维度     | 内容                             | 源                                                                              |
| -------- | -------------------------------- | ------------------------------------------------------------------------------- |
| 基本信息 | 公司概况、行业                   | Tushare ∥ akshare                                                               |
| 财务     | ROE/EPS/毛利率/OCF/杜邦          | Tushare ∥ akshare                                                               |
| 行情     | OHLCV                            | Tushare ∥ 腾讯                                                                  |
| 估值     | PE/PB 序列、分位、PE Band        | Tushare                                                                         |
| K 线     | 日线 + MA/MACD/RSI（统一前复权） | Tushare(adj_factor 自算) ∥ akshare ∥ baostock(无 Token 时兜底) [+tickflow 可选] |
| 股东     | 十大流通股东 + 增减持            | Tushare ∥ akshare                                                               |
| 资金     | 北向/主力/融资/融券              | Tushare ∥ akshare                                                               |
| 市场     | 行业指数、ERP、PCR               | Tushare + FRED                                                                  |

结果自动存入 SQLite（`~/.local/share/investment/research.db`），支持历史回溯。详见 [CONFIGURATION.md](CONFIGURATION.md)。

## 输出规范

16 条法则约束（[SKILL.md](skills/invest-a-stock/SKILL.md)），核心：

- 每条事实标注来源，每条判断标注依据
- 多源交叉验证，单源标注不确定性
- 禁止买卖建议；允许多情景估值（须假设前提 + 概率 + 免责）
- Bull/Bear 须含数值场景化传导链
- 左/右概率并列，禁止单一方向结论

**方法论引擎（v0.2.4）**：报告不再一套模板打天下。R1-R12h 先做框架匹配——行业景气状态卡（R5）、行业关键因素（R4）、成长股四分类分流（R7）、趋势/价值双路径分流（R12g）、数据源降级链（R12h）。AI 先确认「该用哪套框架」，再按框架产出，最后以事实边界 §2.3 约束（禁止猜测/推断，冲突并列不裁决）。

## 项目结构

```
skills/
  lib/                     ← 共用层（nums/stats/technical/cache/data_bridge）
    references/            ← 共享规范（report-conventions.md）
  invest-a-stock/          ← 个股研究
    SKILL.md               ← 核心规格
    references/            ← 专项（modules/financials/sentiment/game-theory）
    scripts/
      invest.py            ← CLI（27 个子命令）
      valuation_calc.py    ← 科学估值
      lib/                 ← collector/store/valuation/risk_scanner/...
    tests/
  invest-a-etf/            ← ETF 研究（数据层供 journal 共用）
  invest-a-journal/        ← 交易方案评估
    scripts/lib/
      market_microstructure.py  ← 市场微观结构管道（17 指标）
  invest-a-pulse/          ← 市场情绪全景
  invest-a-gap-scan/       ← 跳空缺口扫描
.claude-plugin/            ← Claude Code 插件
```

## 开发与贡献

```bash
uv sync && uv run pytest
bash scripts/bump-version.sh X.Y.Z
```

提交前确保测试通过，无 API Key 泄露。详见 [CONTRIBUTORS.md](CONTRIBUTORS.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)；版本变更见 [CHANGELOG.md](CHANGELOG.md)。

## 未来方向

- **定时任务 + 主动通知**：快照分析 → 定期采集 → 变化主动推送（watchlist/thesis 已就位，调度层进行中）
- **webapp 化**：降低使用门槛，把「命令行 + Markdown」变成「界面 + 订阅」
- **历史回测**：AI 研报结论 vs 实际股价表现，让方法论可证伪

## License

MIT · 本地运行 · 不跟踪不上报

报告仅供研究参考，不构成任何投资建议。
