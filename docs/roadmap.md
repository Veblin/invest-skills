# Roadmap

> 对外精简版。实现状态以 `CHANGELOG.md` 与 `skills/invest-A/SKILL.md` 为准。

## 数据源

| 数据源 | 状态 | 说明 |
|--------|------|------|
| Tushare Pro | ✅ | 有 Token 时与 akshare 并行 |
| akshare | ✅ | 多源验证；东方财富接口受代理影响见 CONFIGURATION |
| baostock | ✅ | K 线免费兜底 |
| 腾讯行情 | ✅ | 实时报价兜底 |
| FRED | ✅ | 美宏观（ERP 等） |
| **efinance** | 🔜 | 无 Token 用户的并行免费源候选 |
| **yfinance** | 🔜 | 港股 `.HK` 兜底（当前 Skill 聚焦 A 股） |

接入新数据源时需同步：`pyproject.toml`、`collector.py`、`env.py`、`source-guide.md`、`SKILL.md`。

## 版本方向（概要）

| 版本 | 主题 |
|------|------|
| **v0.1.3**（当前） | 九模块研究备忘录、市场结构、LAW 10–16、快照 diff |
| **v0.1.4**（规划） | 报告质量与 SKILL 工作流打磨（层叠输出、引用规范等） |

具体任务以各版本 CHANGELOG 与 Skill 规格为准。
