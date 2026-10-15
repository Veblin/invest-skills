# 公共市场服务 owner 与 API（A03，v0.3.2）

> 内部模块说明（不新增公开技能）。适用于仓库布局与 SkillHub 分发闭包。

## 1. owner 归属

| 能力 | canonical owner | 兼容面 |
|---|---|---|
| 市场快照 / 两融 / 涨跌比 / 涨停（`snapshot`、`save_snapshot`、`load_history`、`latest_snapshot`、`compute_market_form`、`compute_dispersion`、`compute_chip_clearance`） | `skills/lib/market_microstructure.py` | journal `scripts/lib/market_microstructure.py` 为**身份保持 shim**（`sys.modules` 替换 → 同一实例） |
| 涨停双窗口（`zt_industry_flow` / `zt_seesaw` / `zt_rotation_bundle`） | 同上 | 同上；`zt_industry_flow`/`zt_seesaw` 签名与输出结构保留 |
| 研究库路径/连接/事务 | stock `lib/store.py`（公开 `get_db_path` / `open_connection` / `close_connection` / `connection` / `init_db`；`_db_override` 隔离） | 私有名 `_conn`/`_safe_close`/`_connection` 为同实现别名 |
| 宏观 provider | stock `lib/macro.py`（公开 `fetch_fred_series`；ERP 路径消费） | 私有名 `_fetch_fred_series` 保留别名 |
| 全市场横截面 | stock `lib/market_daily.py` + 共享 `market_pctile.py` | — |

`skills/lib/market_microstructure.py` 对 stock 领域只依赖上述**公开 provider**（AST 扫描 `skills/lib/tests/test_shared_owner_migration.py` 的 D11 豁免集 `lib.market_daily` / `lib.store` / `lib.macro` 锁定；基础模块 env/proxy/tushare_client/nums/stats/dates/cache/trade_cal 一律走 skills/lib canonical）。

## 2. 公共加载入口

- `invest_path.load_journal_module(module_file="market_microstructure")`（历史名保留）：
  按**文件路径**解析 `skills/lib/market_microstructure.py` → 返回 sys.modules 缓存的
  公共实例；同进程已按顶层名导入同一文件时复用该实例（monkeypatch 接缝不分叉）；
  顶层名空缺时 `setdefault` 注册，后续 `import market_microstructure` 同实例。
  canonical 缺失 → `ImportError`（`data_bridge.get_microstructure` 降级 `None` + warning，不抛出）。
- **provider 不依赖 journal 源码**：journal 目录整体缺席时，公共模块与上述 loader 仍可用；
  journal shim 只是旧命令形态（`cd scripts/lib`）的兼容入口。
- 分发闭包：包内全部模块单份落 `lib/`（pulse `lib/`、journal `scripts/lib/`），
  生成版 `invest_path.load_journal_module` 以 `import_module("lib.market_microstructure")`
  取同一 lib 包实例。

## 3. 消费方（谁在调）

| 消费方 | 入口 | 说明 |
|---|---|---|
| invest-a-stock `market-status` | 顶层 `import market_microstructure`（`lib._invest_path.ensure_skills_lib_on_path()` 引导） | 读/存快照、`basis_is_current` 标签过滤；不再注入 journal 私有目录 |
| invest-a-stock `etf-flow` | `invest_path.load_invest_a_etf_module()` | ETF 数据 canonical（A04 公共 loader） |
| invest-a-journal 数据管道 | `from market_microstructure import …`（本目录 shim → 同一实例） | `query_data` 经 `data_bridge.get_microstructure` 5min 缓存 |
| invest-a-pulse | `cd …/skills/lib && from market_microstructure import …` | `zt_rotation_bundle(10, 30)` 一次拉长窗矩阵派生两窗口（A03 R4） |
| data_bridge | `load_journal_module().snapshot` | 维度缓存 `microstructure`（5min；失败信封不缓存） |
| invest-a-discover-scan `sources.py` | 显式文件路径（优先 `skills/lib`，包内动态 seed 落本目录） | `latest_snapshot().env_label` 取 `market_form` 头注记 |

## 4. 失败与降级语义（源失败透明）

- 逐维度采集失败 → 该字段 `None` + `_errors` 逐条记录；全部维度失败 → 信封 `status=all_failed`（不缓存）。
- 涨停池：日历失败 → `fatal:` 记录；单日失败/空数据 → 该日缺位 + `日期: 异常类名` 记录。
  - `zt_industry_flow(days)` / `zt_rotation_bundle(...).rotation`：`_errors` 只含**该窗口内**失败日；
  - `zt_seesaw(days)` / `bundle.seesaw`：成功分支同样携带窗口失败记录（A-F02，partial 不吞）；
  - `zt_rotation_bundle` 顶层 `_errors` = 长窗完整逐日失败记录（两子窗口是其子集）。
  - 短窗 = 长窗末 N 个**计划交易日**内的成功日（不是「最近 N 个成功日」）。
- ERP 折算率经 `macro.fetch_fred_series`（公开名）；Tushare 不可用 → 该字段降级/标注源头。

## 5. 旧路径与退出

- journal `scripts/lib/market_microstructure.py`（shim）：保留以兼容既有文档命令与外部调用；
  不作为第二实现维护（新增函数只落 skills/lib）。
- pulse 旧命令文本（`cd …/invest-a-journal/scripts/lib`）：已从 SKILL.md 退出（A03）；
  stock `market-status` 私有路径注入已退出；发现新残留路径按 D11 同族扫描处理。
