# Phase 4：跨时点与阅读体验

> v0.1.3 子计划 4/4 · 目标版本 `v0.1.3`（正式版）
> 权威来源：`docs/invest-A-blueprint-v013-final.md` §十 Phase 4
> 工期：1–2 周 · 新增代码约 ~200 行 + 测试 ~40 行

---

## 阶段命题（验收一句话）

**能回答：「相对上次调研有什么变化、批量看哪些票在变？」**

---

## 前置条件

- [Phase 3](./invest-A-v013-phase3-risk-divergence.md) 完成：九模块无占位
- `test_v013_phase1.py` ~ `test_v013_phase3.py` 全绿
- Phase 2 的 `pe_band_series()` 数据层已就绪

---

## 本阶段交付范围

本阶段**不新增分析维度**，聚焦：

1. **跨时点对比** — 两次采集间的关键字段变化
2. **批量扫描** — watchlist 多标的摘要
3. **长报告可读性** — TOC、折叠、Mermaid、PE Band 文本表

### 能力清单

| 能力 | 说明 | 文件 |
|------|------|------|
| 快照 diff | `store.py` 扩展；`invest.py diff` 输出变化摘要 | `store.py`, `invest.py` |
| Watchlist 批量 | `cmd_watchlist()` 对股票列表生成变化摘要 | `invest.py` |
| Markdown TOC | 报告顶部锚点目录 | `render.py` |
| `<details>` 折叠 | 长节默认折叠（纯 MD 兼容） | `render.py` |
| Mermaid 流程图 | 研究框架嵌入模块 0 或附录 | `render.py` |
| PE Band 文本表 | 消费 Phase 2 `pe_band_series()` | `render.py` |

### 报告模块增强映射

| 模块 | 增强 |
|------|------|
| 0 研究问题卡 | 可选 Mermaid 问题树 |
| 1 当前状态快照 | diff 对比字段 |
| 8 附录 | TOC 锚点、PE Band 表、折叠长节 |

---

## 任务分解

### Task 4.0 — 快照 diff 增强（~80 行）

**`store.py` 扩展：**

- 持久化关键字段快照（估值、财务摘要、资金流向等）
- 记录采集时间戳

**`invest.py diff` 增强：**

```bash
uv run python invest.py report 000001 --store --outdir ./out   # 第一次
uv run python invest.py report 000001 --store --outdir ./out   # 第二次
uv run python invest.py diff 000001 --emit md
```

**diff 输出字段（建议）：**

| 类别 | 字段示例 |
|------|---------|
| 估值 | PE/PB 分位变化 |
| 财务 | 营收/净利润同比变化 |
| 资金 | 北向净流入、融资余额变化 |
| 技术 | MA 排列变化、RSI 区间 |
| 风险 | 新触发/消除的风险信号 |

### Task 4.1 — `cmd_watchlist()`（~60 行）

```bash
uv run python invest.py watchlist 000001,600519 --outdir ./out
```

| 项目 | 说明 |
|------|------|
| 输入 | 逗号分隔股票代码列表 |
| 输出 | 各标的摘要 + 若有历史快照则标注变化 |
| 约束 | ≥2 只标的；单只失败不阻断其余 |

### Task 4.2 — Markdown UX（~60 行）

**TOC（报告顶部）：**

```markdown
## 目录
- [研究问题卡](#0-研究问题卡)
- [当前状态快照](#1-当前状态快照)
...
```

**`<details>` 折叠：**

对模块 4（基本面 12 题）、模块 7（风险表）等长节默认折叠，展开可看全文。

**Mermaid 流程图：**

在模块 0 或附录嵌入九模块研究框架流程图（纯 Markdown 兼容）。

**PE Band 文本表：**

消费 `pe_band_series()`，输出 5 年 PE 轨道 Markdown 表格（±1σ/±2σ）。

### Task 4.3 — pytest（~40 行）

文件：`tests/test_v013_phase4.py`

| 用例 | 验证 |
|------|------|
| `test_diff_key_fields` | 两次采集后 diff 含估值/财务/资金变化 |
| `test_watchlist_multi_symbol` | ≥2 只标的生成摘要 |
| `test_report_has_toc` | 报告顶部有目录锚点 |
| `test_details_collapsible` | 长节含 `<details>` 标签 |
| `test_pe_band_text_table` | PE Band 表含 5 年轨道 |

---

## 验收命令

```bash
uv run python skills/invest-A/scripts/invest.py report 000001 --store --outdir ./out
uv run python skills/invest-A/scripts/invest.py report 000001 --store --outdir ./out
uv run python skills/invest-A/scripts/invest.py diff 000001 --emit md
uv run python skills/invest-A/scripts/invest.py watchlist 000001,600519 --outdir ./out
uv run pytest skills/invest-A/scripts/tests/test_v013_phase4.py -v
uv run pytest
```

## 验收清单

- [ ] 同一标的两次 `--store` 采集后，`diff` 输出关键字段变化
- [ ] watchlist 批量生成各标的摘要（≥2 只）
- [ ] 报告顶部有 TOC，长节可折叠
- [ ] PE Band 文本表含 5 年 PE 轨道
- [ ] Mermaid 流程图在模块 0 或附录可渲染（GitHub/Obsidian 兼容）
- [ ] 全量 pytest 全绿
- [ ] CHANGELOG 发布 `v0.1.3` 正式版说明

---

## 文件变更总览

| 文件 | 变更 | 行数 |
|------|------|------|
| `lib/store.py` | 快照 diff 增强 | +40 |
| `invest.py` | `diff` 增强 + `cmd_watchlist()` | +100 |
| `lib/render.py` | TOC / details / Mermaid / PE Band 表 | +60 |
| `tests/test_v013_phase4.py` | 新建 | +40 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 报告 800–1000 行难读 | TOC + details 折叠；watchlist 只输出摘要 |
| diff 字段过多噪音 | 只对比高信号字段；变化低于阈值不展示 |
| Mermaid 部分渲染器不支持 | 放附录；TOC 仍可用 |
| store  schema 变更 | 版本化快照格式；旧快照 graceful 降级 |

---

## 阶段外 backlog：HTML 可视化

> **不在本阶段范围内。** 启动条件：九模块 Markdown 结构连续 2 个小版本无 breaking change。

| 任务 | 说明 |
|------|------|
| `render_html_v3()` | 从 `render_report_v3()` 单一数据源生成 |
| ECharts 图表 | K 线、均线、估值历史分位 |
| PE Band 交互图 | 消费 `pe_band_series()` |
| 章节侧边栏导航 | 模块间跳转 |

CLI 策略：届时再评估是否恢复 `--emit html` 为默认。

---

## 完成标志

四阶段全部完成后，invest-A v0.1.3 达到：

```
① 解释当下（动态驱动 + 市场结构 + 左/右概率）
② 静态深度（12 核心题 + 隐性预期差 + 同行对比）
③ 风险闭环（17 信号 + Bull/Bear + 情绪分位数）
④ 跨时点体验（diff + watchlist + 长报告 UX）
```

回到 [子计划索引](./invest-A-v013-subplans-index.md) 查看全局映射。
