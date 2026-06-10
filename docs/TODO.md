# TODO — invest-A 待实现功能

## 待接入数据源

以下数据源已在早期文档中规划，但当前版本（v0.1.0）尚未接入。待后续版本评估优先级后实现。

### efinance

- **用途**: A股免费数据源，无需 Token，提供 `stock.get_base_info` / `stock.get_quote_history`
- **稳定性**: ★★★★
- **接入原因**: 可以作为 Tushare 的并行首选（与 Tushare 竞速，先到先用），对无 Token 用户尤其重要
- **依赖**: `pip install efinance`
- **关联文档**: `references/source-guide.md`（v0.2.0 旧版已包含 efinance 优先级）

### baostock

- **用途**: A股免费数据源，无需 Token，提供 `query_history_k_data_plus`
- **稳定性**: ★★★
- **接入原因**: akshare 不可用时的兜底方案
- **依赖**: `pip install baostock`
- **关联文档**: `references/source-guide.md`（v0.2.0 旧版已包含 baostock 优先级）

### yfinance

- **用途**: 美股/港股数据，通过 `.HK` 后缀支持港股
- **稳定性**: ★★
- **接入原因**: 港股分析的兜底方案（当前版本仅支持 A 股）
- **依赖**: `pip install yfinance`
- **关联文档**: `AGENTS.md`（v0.2.0 旧版港股数据源链）

---

## 接入后需同步更新

1. `pyproject.toml` — 添加依赖
2. `AGENTS.md` — 更新数据源优先级链
3. `references/source-guide.md` — 恢复对应数据源行
4. `skills/invest-A/SKILL.md` — 更新数据源状态
5. `lib/collector.py` — 实现对应的采集函数和 fallback 逻辑
6. `lib/env.py` — 添加可用性检测函数

---

*记录时间: 2026-06-10 · 版本 v0.1.0*
