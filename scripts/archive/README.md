# scripts/archive — 一次性脚本归档（v0.2.7 起）

本目录存放**已完成使命的一次性脚本**，仅保留作复现/追溯用途，**不再维护**。归档依据见 host-docs/v0.2.7/base-repo-conformance-review.md。

| 脚本 | 用途 | 结论沉淀位置 |
|---|---|---|
| `tmp_4050_band.py` | 4050 关口带历史触达统计（验证参考位有效性） | 点位规范 report-conventions.md §2.4 |
| `tmp_wave_analysis.py` / `tmp_wave_analysis2.py` | 沪指波浪回撤位量化锚点分析 | 直播总结复核记录 |
| `backtest_h1/h2/h3/h4/h6.py`、`backtest_calendar.py`、`backtest_futures.py` | H 系列（见底/低吸/金价 beta/缺口/日历）+ F 系列期货回测 | CHANGELOG 裁决条目 + `skills/lib/references/backtest_prereg/` 预注册 + `docs/data/*_backtest_result.json` |
| `scenario_baselines.py` | E-002~E-007 候选预案基线生成 | `skills/lib/references/scenario-plans.md` + `docs/data/scenario_baselines_E002_E007.json` |
| `protect-main-branch.sh` | GitHub main 分支保护 Ruleset 一次性配置 | 配置已生效于 Veblin/invest-skills，无再执行场景 |
| `backfill_futures_daily.py` | F 系列期货主力合约日线历史回填（C9a v0.2.7 移入） | `docs/data/F*_backtest_result.json` |
| `backfill_market_daily.py` | 全市场日线缺日回填（C9a v0.2.7 移入） | store market_daily 数据层 |

约定：**新的一次性分析脚本不得直接放 `scripts/` 根**；分析结束即移入本目录（或直接在此目录开发），文件名保持原样不改写历史引用。
