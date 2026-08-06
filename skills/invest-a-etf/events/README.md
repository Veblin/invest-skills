# invest-a-etf 事件文件（R11b）

每只 ETF 一个文件：`events/{symbol}.json`（JSON Lines，UTF-8，一行一条事件）。

```json
{"date": "2026-05-11", "event": "科创50 指数创历史新高", "source_url": "https://example.com/x", "published_date": "2026-05-11", "confidence": "一手"}
```

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `date` | ✅ | ISO 日期（YYYY-MM-DD），事件发生日 |
| `event` | ✅ | 事件描述（一句话） |
| `source_url` | ✅ | 一手来源链接 |
| `published_date` | ✅ | ISO 日期，来源发布时间 |
| `confidence` | ✅ | 枚举 `一手` / `二手` |

任一字段非法 → 整文件拒绝并报行号（`validate_events_file`）。

## 消费方式

```bash
uv run python skills/invest-a-etf/scripts/etf.py report 588000 --history \
    [--events 可选覆盖路径] [--playbook]
```

- `--history`：拉取历史行情（nav 链路优先，失败回退 baostock）并计算历史统计
- `--events`（缺省自动读 `events/588000.json`，无文件不阻断）：事件与 ±1 交易日
  大波动对齐，输出「同日事实」（纯时间线罗列，不做因果断言）与
  「可能关联（待验证）」（confidence=一手 可进高可信说明，其余一律待验证）
- `--playbook`：回撤档位 σ 分级（触发核验深度）+ 三步核查清单 + LAW 6a 声明
  （预案为研究流程规则，非买卖指令）
