---
name: verify
description: 运行时验证 invest-a-stock/etf/journal CLI — 隔离 DB 驱动真实命令行（store._db_override + runpy launcher）
---

# Verify — invest CLI 运行时观察

## Handle（关键配方）

`lib.store` 的 DB 路径是硬编码 `~/.local/share/investment/research.db`，无环境变量。
CLI 验证必须隔离：写一个 launcher 脚本，先 import invest-a-stock 的 `lib.store`、
设 `store_mod._db_override = <tmp db>`，再用 `runpy.run_path(etf.py, run_name="__main__")`
执行真实 CLI。脚本模板（已验证可用的路径设置顺序）：

```python
import os, runpy, sys
from pathlib import Path
CODE = Path("/Users/veblin/Study/claude-bigA-financial/code")
sys.path.insert(0, str(CODE / "skills/invest-a-stock/scripts"))   # lib 包 = invest-a-stock
from lib import store as store_mod
store_mod._db_override = Path(os.environ["SF_VERIFY_DB"])
# P0-2 自检：override 未生效必须立即失败——静默写真实库曾造成
# collections 记录被外部进程删除/污染（2026-08-23 现场：session 内
# #48/#53/#54 消失，总行数 55→52）。
assert store_mod._get_path() == Path(os.environ["SF_VERIFY_DB"]), (
    "store override 未生效，禁止继续（会写真实 research.db）"
)
sys.argv = ["etf.py"] + sys.argv[1:]
runpy.run_path(str(CODE / "skills/invest-a-etf/scripts/etf.py"), run_name="__main__")
```

种数据：同 launcher 方式设 override，import `sector_flow`（insert scripts/lib 到 sys.path），
调真实 `save_sector_flow_snapshot` 逐日种快照。注意**不要种未来日期**——MAX(date)
是 C5 全等检测的比对基线，未来日期会让当天 collect 永远不跳过（首次驱动时的坑）。

## 值得驱动的流

- `sector-flow <symbol>` 人读表格 + `--json`：跨度标注（trend_span_days ≠7 → 「跨度 N 日」）、
  缺失行业 note、趋势标签（净额近零/近端归零/金额量级小）
- `collect-sector-flow` 连跑两次：首次 360 行写入 → 二次全等跳过（note 区分
  「非交易日（日历判定）」vs「疑似盘前未刷新」）；⚠ 映射自检 drift 计数（约 51 个正常）
- 错误路径：非法 symbol（exit 2）、未映射 ETF（exit 0 + ⏳ note）

## 坑

- akshare 进度条在 stderr；CLI 观察加 `2>/dev/null`
- `uv run python` 启动 launcher（依赖在 .venv）
- 真实 DB（~/.local/share/investment/research.db）含用户历史数据，只读观察可以，
  写操作（collect）一律走隔离 DB
