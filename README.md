# invest-A — A 股/港股投资学习 Skill

> **一个帮助你学习投资分析的工具，不是帮你做决策的工具。**
>
> 覆盖个股调研的七大维度：基本信息、财务报告、行业产业链、估值/市场状态、机构分析师、情绪舆情、宏观政策。每个数据点标注来源，每个判断标注依据，每个推测明确声明。

---

## 项目定位

| 是 | 不是 |
|----|------|
| 教你"如何分析一只股票" | 告诉你"该买哪只股票" |
| 聚合多维度数据，帮你看到全貌 | 给出买卖建议 |
| 解释"这个估值意味着什么" | 断言"这个价格是否合理" |
| 标注数据来源和不确定性 | 假装结论是确定的 |

**禁止**：买入/卖出/持有建议、目标价预测、仓位建议、投资价值综合评分。

---

## 快速开始

### 前置要求

- **Python 3.12+**
- **Claude Code**（CLI）或 **Hermes**（Skill 安装）或其他支持 Agent Skills 的运行时

### 1. 安装依赖

```bash
pip install akshare efinance yfinance fredapi pandas requests pyyaml
```

### 2. 配置环境变量（可选）

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# Tushare Pro Token（可选，注册即送 120 积分）
# 注册地址：https://tushare.pro
TUSHARE_TOKEN=your_token_here

# FRED API Key（可选，免费注册，推荐配置）
# 注册地址：https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY=your_key_here

# Tavily Search API Key（可选，免费 1000 次/月）
# 注册地址：https://tavily.com
TAVILY_API_KEY=your_key_here
```

> **所有 API Key 都是可选的。** 不配置时使用免费数据源（akshare / efinance / yfinance / WebSearch），报告会标注降级情况。

### 3. 在 Claude Code 中使用

```bash
# 安装 Skill
npx skills install

# A 股分析
/invest-A 600519

# 港股分析
/invest-A 00700

# 对比分析
/invest-A 600519 --compare 000858

# 宏观快报
/invest-A macro

# 学习模式
/invest-A learn ROE
```

### 4. 命令行测试（不依赖 Claude Code）

```bash
# 测试数据管道
python -m scripts.data_pipeline --test 600519

# 测试单个模块
python -m scripts.lib.a_share_data --test 600519
python -m scripts.lib.global_macro --snapshot
python -m scripts.lib.fund_flow --test
```

---

## 平台兼容性

| 功能 | Claude Code (Python) | Claude Code (MCP) | Hermes (MCP) |
|------|---------------------|-------------------|--------------|
| A 股分析 | ✅ | ✅ | ✅ |
| 港股分析 | ✅ | ✅ | ✅ |
| 对比分析 | ✅ | ⚠️ | ⚠️ |
| 宏观仪表盘 | ✅ | ✅ | ⚠️ |
| 教学模式 | ✅ | ✅ | ✅ |
| ETF 分析 | 占位 | 占位 | 占位 |

### MCP 配置（Hermes / 无 Python 环境）

当无法直接运行 Python 脚本时，可通过 MCP Server 获取数据：

**FinanceMCP**（推荐，免费公网服务）：
```json
{
  "mcpServers": {
    "finance-mcp": {
      "type": "streamableHttp",
      "url": "https://finvestai.top/mcp",
      "headers": {
        "X-Tushare-Token": "你的tushare令牌（可选）"
      }
    }
  }
}
```

**FinanceAgent MCP**（港股免 Key）：
```json
{
  "mcpServers": {
    "finance-agent": {
      "type": "streamableHttp",
      "url": "https://finance-agent-mcp.example.com/mcp"
    }
  }
}
```

---

## 架构

```
触发层:  /invest-A 600519 [--flags]     → SKILL.md
编排层:  data_pipeline.py                → Step 0 → 1 → 2 → 3
采集层:  10 lib modules                  → a_share / hk / global_macro / news / sentiment / ...
分析层:  strategies/*.yaml + knowledge/*.md
渲染层:  report_render.py               → Markdown 报告
```

详细架构见 `docs/执行计划-架构关系.md`。

---

## 项目结构

```
code/
  SKILL.md              ← 运行时规格（LAWs + 工作流 + 专业知识）
  AGENTS.md             ← AI 协作规则
  README.md             ← 本文件
  .env.example          ← 环境变量模板
  config/               ← 配置文件
    source_credibility.yaml      ← 数据源可信度评分
    dimension_baselines.yaml     ← 维度基准权重
    cross_validation_rules.yaml  ← 交叉验证规则
  strategies/           ← 分析方法库（9 个 YAML）
  knowledge/            ← 投资知识库（7 篇文档）
  scripts/              ← 数据采集引擎
    data_pipeline.py    ← 主编排引擎
    lib/                ← 采集模块
  docs/                 ← 设计文档
```

---

## 依赖

### 核心（必装）

| 包 | 用途 | 费用 |
|----|------|------|
| `akshare` | A 股/港股行情/财务/情绪 | 免费 |
| `efinance` | A 股数据 fallback | 免费 |
| `yfinance` | 全球宏观/港股 fallback | 免费 |
| `pandas` | 数据处理 | 免费 |
| `requests` | HTTP 调用 | 免费 |
| `pyyaml` | YAML 配置解析 | 免费 |

### 可选

| 包 | 用途 | 费用 |
|----|------|------|
| `fredapi` | FRED 宏观数据（推荐） | 免费注册 |
| `baostock` | A 股 fallback | 免费 |

---

## 不做

- ❌ 雪球爬虫（违反 ToS）
- ❌ 买卖建议 / 目标价
- ❌ Reddit 情绪（纯 A 股场景覆盖不到）
- ❌ 社交功能入口
- ❌ 定时推送（个人 Cron 除外）

---

## 开发路线

| Phase | 内容 | 状态 |
|-------|------|------|
| **Phase 0** | Skill 骨架（SKILL.md / AGENTS.md / README.md） | ✅ 已完成 |
| **Phase 1** | 核心数据管道（10 lib + data_pipeline） | 🔜 待实现 |
| **Phase 2** | 分析引擎（knowledge + strategies + report_render） | 🔜 待实现 |
| **Phase 3** | 工作流串联（Step 0-3 完整流程） | 🔜 待实现 |
| **Phase 4** | 增强功能（对比/宏观/教学/分发） | 🔜 待实现 |
| **Phase 5** | 验收合规（LAWs 检查 + 示例报告 + 测试矩阵） | 🔜 待实现 |
| **Phase 6+** | ETF 专项、产业链图谱、AH 溢价等 | 📅 远期 |

详见 `docs/执行计划.md`。

---

## License

MIT
