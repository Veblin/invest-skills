# 参与者行为扫描专项

> 受 [SKILL.md](../SKILL.md) LAW 1–17 约束。`plan --intent game_theory` 时加载本文件。

## 定位（v0.1.9）

本专项实现 **参与者行为扫描**，不是完整博弈论建模。

- ✅ 基于现有数据描述各类参与者近期行为事实
- ✅ 北向 vs 主力等交叉验证分歧
- ❌ 策略矩阵、纳什均衡、操作建议
- ❌ 龙虎榜 / 大宗交易（规划 v0.2.0，见 [source-guide.md](source-guide.md)）

引擎渲染：`_section_participant_behavior_scan()`（`lib/participant_scan.py`），位于市场结构之后、事件时间线之前。

## 采集工作流

```bash
uv run python skills/invest-a-stock/scripts/invest.py plan 600176 --intent game_theory > /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py evidence 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --plan /tmp/plan.json --mode full
```

采集维度：`quote`、`shareholders`、`northbound`、`holder_changes`、`kline`、`basic_info`。

`market_structure`（moneyflow/margin/turnover 等）由 `render` 阶段 `attach_market_structure()` 自动补采。

## 参与者类型与数据源

| 参与者 | 代理指标 | 数据来源 |
|--------|---------|---------|
| 北向（外资） | 近 10 日净额、与股价背离 | `market_structure.northbound` |
| 主力（游资代理） | 近 5/10 日主力净额 | `market_structure.moneyflow` |
| 杠杆资金 | 融资余额变化 | `market_structure.margin` |
| 产业/内部人 | 增减持一致性 | `holder_changes` + `shareholders` |
| 散户情绪代理 | 换手极端、PCR | `turnover` + `put_call_ratio` |

## Claude 分析 SOP

1. 读取引擎「参与者行为扫描」节（事实块）
2. 填写 `[分析]`：行为一致性 / 分歧点（禁止因果定论）
3. 附四维证据标签（SOP-EV）
4. `game_theory` intent 简报：行为扫描摘要 + 1–2 个核心矛盾 + 下一观察节点
5. **不写**完整九模块

### 输出模板

```
[事实]
- 北向近10日净流入 X [来源: market_structure.northbound]
- 主力近10日净流入 Y [来源: market_structure.moneyflow]
- 内部人信号: Z [来源: lib.scoring.insider_signal]

[分析]
北向与主力方向{一致/相反}，{若相反须列可能滞后或参与者差异，非操作建议}
[证据强度: ✅/⚠️/❓ ...]
```

## 模块边界

| 模块 | 本专项区别 |
|------|-----------|
| 模块 3 市场结构 | 模块 3 列因子状态；本专项做参与者维度交叉对照 |
| 模块 5 Bull/Bear | Bull/Bear 是逻辑情景；本专项是行为事实扫描 |
| 资金与筹码（legacy） | legacy 罗列股东/北向；本专项强调行为分歧 |

## 禁用词表（LAW 6）

禁止：买入、卖出、建仓、策略建议、均衡点、散户应、机构将、目标仓位。

允许：行为描述、分歧标注、观察节点、情景假设（须标注前提）。

## 不可得处理

全部参与者维度无数据时，引擎输出 LAW 5 标准句；Claude 不推测参与者意图。
