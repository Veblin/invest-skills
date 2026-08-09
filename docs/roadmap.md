# Roadmap

> 对外精简版。实现状态以 `CHANGELOG.md` 与 `skills/invest-a-stock/SKILL.md` 为准。

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
| **v0.2.5**（当前） | 交易纪律框架（D1-D8）+ WorkBuddy 兼容 |
| **v0.2.4** | 方法论引擎 R1-R12h 落地 + 事实边界规范 + 多轮 /code-review 修复 |
| **v0.2.3** | 数据桥接层（data_bridge）落地 + 采集管线性能与健壮性优化（socket 超时/K 线缓存/慢源降级） |
| **v0.2.2** | 市场微观结构指标体系 + invest-a-pulse 新 Skill + 共用函数层/TTL 缓存 + invest-a-etf 行业基础设施 |
| **v0.2.1** | invest-a-etf / invest-a-journal / invest-a-gap-scan 新 Skill + 宏观扩展（VIX/SOX）+ slash 连字符统一 |
| **v0.2.0** | 多 Skill 组合（invest: 命名空间）+ 科学估值计算器 + 多 Agent 并行深度分析 |

具体任务以各版本 CHANGELOG 与 Skill 规格为准。
