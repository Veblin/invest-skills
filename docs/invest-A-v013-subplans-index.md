# invest-A v0.1.3 子计划索引

> 由 `docs/invest-A-blueprint-v013-final.md` 拆分
> 日期: 2026-06-14
>
> **拆分原则：** 每阶段交付相对完整的用户可感知能力，有独立验收命令与 pytest 用例，可单独合并发布。

---

## 总览

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► [HTML backlog]
 动态投研      基本面估值      风险分歧       跨时点体验
 内核          体系            闭环
 ~2 周         ~2 周           ~2 周          ~1-2 周
```

| 阶段 | 文档 | 版本标签 | 验收一句话 |
|------|------|---------|-----------|
| **1** | [phase1-dynamic-kernel.md](./invest-A-v013-phase1-dynamic-kernel.md) | `v0.1.3-alpha` | 能回答「现在为什么在动、左/右概率结构、什么条件会变」 |
| **2** | [phase2-fundamentals-valuation.md](./invest-A-v013-phase2-fundamentals-valuation.md) | `v0.1.3-beta` | 能回答「公司质地如何、估值隐含什么预期、在行业什么位置」 |
| **3** | [phase3-risk-divergence.md](./invest-A-v013-phase3-risk-divergence.md) | `v0.1.3-rc` | 能回答「多空分歧在哪、有哪些定量风险信号、情绪是否极端」 |
| **4** | [phase4-cross-timepoint-ux.md](./invest-A-v013-phase4-cross-timepoint-ux.md) | `v0.1.3` | 能回答「相对上次有什么变化、批量看哪些票在变」 |

**阶段间依赖：** 1 → 2 → 3 顺序不可跳；4 可在 3 完成后并行收尾。

---

## 九模块 × 阶段映射

| 报告模块 | Ph1 | Ph2 | Ph3 | Ph4 |
|---------|-----|-----|-----|-----|
| 0 研究问题卡 | ✅ | — | — | Mermaid |
| 1 当前状态快照 | ✅ | — | — | diff 增强 |
| 2 动态驱动 | ✅ | — | — | — |
| 3 市场结构 | ✅ | — | 情绪增强 | — |
| 4 静态基本面 | 占位 | ✅ | — | — |
| 5 市场分歧 | 占位 | — | ✅ | — |
| 6 左/右概率 | ✅ | — | CV-8 | — |
| 7 风险 | 占位 | — | ✅ | — |
| 8 附录 | ✅ | — | — | TOC/折叠 |
| CLI / 基础设施 | md 默认 | — | — | watchlist |

---

## 配套权威文档

| 文件 | 用途 |
|------|------|
| `docs/invest-A-blueprint-v013-final.md` | 需求蓝图（总纲） |
| `docs/invest-A-v013-execution-plan.md` | 任务级执行计划（含日级排期） |
| `docs/invest-A-SKILL-LAW10-16.md` | LAW 10–16 源文档（Phase 1 阻塞项） |
| `skills/invest-A/SKILL.md` | 运行时技能规格 |

---

## 全局约束（各阶段均须遵守）

1. **禁止荐股** — 不输出买卖建议、目标价、仓位建议（AGENTS.md 约束 1）
2. **默认 Markdown** — `report` 默认 `--emit md`；HTML 冻结 v0.1.2，须显式 `--emit html`
3. **数据降级** — Tushare 权限不足时标注 `[数据源不可用，该因子跳过]`，不阻断报告
4. **LAW 16 防线** — 左/右章节禁止「当前是左侧/右侧」确定性结论；SKILL.md + render 双层约束 + pytest 正则

---

## 阶段外 backlog

HTML 可视化（ECharts、PE Band 交互图、侧边栏导航）不在上述四阶段内。
启动条件：九模块 Markdown 结构连续 2 个小版本无 breaking change。

详见蓝图 §10.5 与 [phase4](./invest-A-v013-phase4-cross-timepoint-ux.md) 文末说明。
