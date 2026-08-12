# 持仓状态数据层 — 设计文档

> 日期：2026-08-12 ｜ 状态：待实现 ｜ 分支：feat/v0.2.5

## 1. 背景与定位

用户操作复盘（2026-08-12）显示两周成交 7 笔、卖出 100% 参考点驱动，交易日志（`trade_journals`）已具备行为复盘能力。但系统**没有任何「当前持仓状态」的数据源**：`invest.py portfolio` 只吃静态权重 JSON（无成本/数量），journal 只有交易事件（无持仓状态）。持仓状态（成本/数量/浮盈亏/仓位漂移）是**持续变化的常驻数据**，与 skill 的「一次生成、即用即走」工具性质不符，价值在时间维度。

**决策**：持仓分析作为未来 webapp 的核心模块（产品方向：快照分析 → 定时任务+主动通知 → webapp 化）。当前阶段只做**形态无关的数据层闭环**：券商导出 → 持仓快照存储 → 分析引擎 → JSON 输出。webapp 未来直接消费 JSON，不绑定展示形态。

## 2. 目标与非目标

### 目标
- 券商持仓导出文件（Excel/CSV）→ 标准化持仓记录，落 SQLite 快照表
- 当日现价批量获取（多源降级），计算浮盈亏/市值/仓位占比
- 输出结构化 JSON（消费接口）+ 人类可读表格（验证用）
- 与 `trade_journals` 联动：持仓标的关联最近买入、错误条件、attribution

### 非目标（首版明确不做）
- 净值曲线 / 历史绩效（快照攒够历史后再做）
- webapp / 定时任务 / 主动通知
- 券商增量同步（每次手动导出导入）
- 买卖建议、仓位建议（LAW 6）
- 组合相关性/压力测试（已有 `invest.py portfolio` 覆盖）

## 3. 架构

```
券商导出文件 (Excel/CSV, GBK/UTF-8)
   │
   ▼
① 解析器 holdings_parser.py
   ├─ 编码自动检测 (GBK/UTF-8/BOM)
   ├─ 配置化列映射 (symbol/name/shares/cost_price/cost_amount)
   └─ 字段校验 (必需列缺失报错; 数量/成本 ≥0)
   │
   ▼
② 存储 holdings_snapshots 表 (research.db)
   ├─ snapshot_date / symbol / name / shares / cost_price
   ├─ cost_amount / source_file / created_at
   └─ 语义: 每次导入 = 当日快照; 同日重复导入覆盖; 历史快照永久保留
   │
   ▼
③ 行情层 (腾讯批量接口 qt.gtimg.cn, 一次请求全部代码)
   ├─ 东财不可用时自动降级 (2026-08-12 实证: push2his 被拒)
   └─ 8 态降级标注 (available/missing/...)
   │
   ▼
④ 分析引擎 position_analysis.py
   ├─ 持仓明细: 现价/市值/浮盈亏(金额+%)/仓位占比
   ├─ 汇总: 总成本/总市值/总浮盈亏/仓位分布
   ├─ 集中度: 单标的权重提示 (>20% 纯事实警示, 非建议)
   └─ journal 联动: 持仓 ↔ trade_journals (最近买入/错误条件/attribution)
   │
   ▼
⑤ 输出: --json (webapp 消费) + 表格 (人类验证)
```

## 4. 组件规范

### 4.1 解析器 `skills/lib/holdings/holdings_parser.py`

**编码检测**：读文件头 4KB → BOM 检测（UTF-8-SIG）→ `chardet` 兜底 → 默认 GBK（国内券商导出常见）。

**列映射**：配置文件 `skills/lib/holdings/broker_maps.json`：

```json
{
  "ht_zlt": {
    "name": "华泰涨乐财富通",
    "detect": {"columns_required": ["证券代码", "证券名称", "可用数量", "成本价"]},
    "map": {
      "symbol": "证券代码",
      "name": "证券名称",
      "shares": "可用数量",
      "cost_price": "成本价"
    },
    "encoding": "auto",
    "format": "excel"
  }
}
```

- 自动匹配：按 `detect.columns_required` 逐候选尝试；匹配失败 → 报错列出实际列名与支持列表
- `cost_amount` 可选项：无此列时由 `shares × cost_price` 计算（P0：引擎计算）
- 字段校验：shares/cost_price 非负可转 float；symbol 6 位数字

**功能依赖**：首版交付需用户提供一份真实券商导出文件（华泰/东财/同花顺任一），固化对应映射并加 fixture。未提供前以 fixture 样例（CSV）交付并标注映射为示例。

### 4.2 存储 `holdings_snapshots` 表（research.db）

```sql
CREATE TABLE IF NOT EXISTS holdings_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,            -- 'YYYY-MM-DD'
  symbol        TEXT NOT NULL,
  name          TEXT NOT NULL,
  shares        REAL NOT NULL CHECK (shares >= 0),
  cost_price    REAL NOT NULL CHECK (cost_price >= 0),
  cost_amount   REAL NOT NULL,            -- shares × cost_price (引擎算)
  source_file   TEXT NOT NULL,            -- 导入来源文件名
  created_at    TEXT DEFAULT (datetime('now')),
  UNIQUE (snapshot_date, symbol)          -- 同日重复导入 → REPLACE 覆盖
);
```

### 4.3 行情层 `skills/lib/holdings/quote_client.py`

- 腾讯批量：`https://qt.gtimg.cn/q=sh515050,sh588000,...`（一次请求，GBK 解码），现价取 `~` 分隔第 4 字段（注意：腾讯字段序号与个股/ETF 一致为「现价」位）
- 降级链：东财批量 → 腾讯批量（东财 2026-08-12 实证被拒，首版以腾讯为主源，留东财作为可切换备源）
- 单标的失败 → 标注 `missing`，不阻断整批
- 响应字段需引擎校验：价格 >0 且为有限浮点

### 4.4 分析引擎 `skills/lib/holdings/position_analysis.py`

输入：某 snapshot_date 的持仓 + 当日行情 + journal 关联。输出（JSON schema）：

```json
{
  "snapshot_date": "2026-08-12",
  "asof_price_source": "tencent",
  "summary": {
    "total_cost": 0.0, "total_market_value": 0.0,
    "total_pnl_amount": 0.0, "total_pnl_pct": 0.0,
    "position_count": 0
  },
  "holdings": [
    {
      "symbol": "515050", "name": "通信ETF华夏",
      "shares": 0.0, "cost_price": 0.0, "cost_amount": 0.0,
      "quote": {"price": 0.0, "quality": "available"},
      "market_value": 0.0, "pnl_amount": 0.0, "pnl_pct": 0.0,
      "weight_pct": 0.0,
      "journal": {
        "latest_buy": {"date": "2026-07-31", "price": 0.93},
        "wrong_conditions": ["..."],
        "attribution": "capability"
      }
    }
  ],
  "concentration": [
    {"symbol": "515050", "weight_pct": 0.0, "flag": "over_20pct"}
  ]
}
```

- 所有计算（市值/浮盈亏/占比/汇总）引擎完成（P0），AI 不心算
- `weight_pct` = 单标的市值 / 总市值
- 集中度提示仅事实陈述（>20% 标 `over_20pct`），不输出任何建议（LAW 6）
- journal 联动按 symbol 关联 `trade_journals`：最近一条 buy（entry_date/entry_price）、该 buy 的 wrong_conditions、该标的最新 attribution；无记录 → `journal: null`
- 浮盈亏引用当日收盘价口径（标注 `asof_price_source` 与日期）

### 4.5 输出与 CLI

入口：`skills/lib/holdings/cli.py`（`uv run python` 直跑，不挂任何 skill 子命令）：

```bash
uv run python skills/lib/holdings/cli.py import <file> [--date YYYY-MM-DD] [--broker ht_zlt|auto]
uv run python skills/lib/holdings/cli.py snapshot [--date YYYY-MM-DD] [--json]
uv run python skills/lib/holdings/cli.py history              # 列出已有快照日期
```

- `import`：解析 → 校验 → 写表（同日 REPLACE）
- `snapshot`：读持仓 → 拉行情 → 引擎计算 → `--json` 输出或表格
- 表格输出供人工验证，JSON 为机器消费主接口

## 5. 错误处理

| 场景 | 行为 |
|------|------|
| 文件不存在/不可读 | 报错退出，码 2 |
| 编码无法识别 | 报错列出检测结果，提示手动指定 |
| 列映射不匹配 | 报错列出文件实际列名 + 支持列表 |
| shares/cost_price 非法 | 逐行报错（含行号），有错则整批不落库 |
| 行情单标的失败 | 该标的 `quote.quality=missing`，其余正常 |
| 行情全失败 | `asof_price_source=unavailable`，持仓仍输出（无行情列） |
| 同日重复导入 | 覆盖当日快照（REPLACE），历史不动 |

## 6. 测试

- 解析器：fixture CSV（GBK 编码）→ 断言标准化输出；缺列样例 → 断言报错
- 引擎：固定输入（已知持仓+mock 行情）→ 断言 summary/holdings/concentration 数值与 journal 联动
- 行情层：mock 腾讯响应 → 断言解析/降级标注
- 存储：同日 REPLACE 语义 + 历史快照保留

## 7. 合规约束

- P0：所有数值计算由引擎完成，输出即引用源
- LAW 6：无买卖/仓位/止损建议；集中度 >20% 仅事实警示
- JOURNAL-LAW 6：行情缺失用 8 态标注，不静默跳过
- 版本：数据层不新增版本号，随主项目发布（v0.2.5 分支内合入）

## 8. 交付依赖

1. 用户提供一份真实券商持仓导出文件（用于固化列映射 + fixture）——未提供时以示例 CSV 交付，映射标为示例
2. 无其他外部依赖（腾讯行情为公开接口，无 key）
