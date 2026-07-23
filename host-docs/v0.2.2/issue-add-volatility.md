# Issue: 增加年化波动率计算（invest-a-stock）

**标签:** `enhancement`  
**影响范围:** 数据采集管道 / 报告格式  
**提出人:** @openclaw  
**日期:** 2026-07-23

---

## 要解决什么问题？

目前 `skills/invest-a-stock/scripts/lib/technical.py` 只实现了 MA、MACD、RSI 等指标，**没有年化波动率计算**。

波动率在投资决策中很实用，但目前只能靠投资者凭经验估计：

| 场景 | 问题 |
|:----|:-----|
| **止损线设定** | 科创50（年化波动~55%）和银行股（~25%）的止损线应该完全不同，但没有数据支撑 |
| **仓位管理** | 高波动品种应占更小仓位，但没有量化依据 |
| **风险评估** | 用户无法直观了解一只股票的"正常波动范围" |

invest-a-etf 端已有对等实现（`skills/invest-a-etf/scripts/lib/etf_data.py:410-413`），建议对齐到 invest-a-stock 端。

---

## 建议的解决方案

### 核心算法

在 `technical.py` 中新增年化波动率计算函数：

```python
def calc_volatility(closes: list[float]) -> float | None:
    """计算年化波动率（%）
    
    每日收益率 → 标准差 → 年化（×√252）
    复用已有的 kline close 数据，无需新增数据源。
    """
    if len(closes) < 5:
        return None
    returns = [(closes[i] - closes[i-1]) / closes[i-1] 
               for i in range(1, len(closes))]
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(variance)
    return round(daily_vol * math.sqrt(252) * 100, 2)


def calc_volatility_windows(closes: list[float]) -> dict:
    """返回多窗口年化波动率
    
    返回:
        vol_20d: 短期波动率（近20个交易日）
        vol_60d: 中期波动率（近60个交易日）
        vol_annual: 长期年化波动率（全部数据）
    """
    return {
        "vol_20d": calc_volatility(closes[-20:]) if len(closes) >= 20 else None,
        "vol_60d": calc_volatility(closes[-60:]) if len(closes) >= 60 else None,
        "vol_annual": calc_volatility(closes),
    }
```

### 输出位置

在 `invest.py report` 的 **模块8 技术指标附录** 中新增一行：

```
- 波动率: 20日 XX% / 60日 YY% / 年化 ZZ%
```

### 参考实现（invest-a-etf）

```python
# skills/invest-a-etf/scripts/lib/etf_data.py:410-413
mean_ret = sum(returns) / len(returns)
variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
daily_vol = math.sqrt(variance)
result["volatility_annualized"] = round(daily_vol * math.sqrt(252) * 100, 2)
```

### 涉及的变更文件

| 文件 | 变更内容 |
|:----|:--------|
| `skills/invest-a-stock/scripts/lib/technical.py` | 新增 `calc_volatility()` / `calc_volatility_windows()` |
| `skills/invest-a-stock/scripts/lib/render.py` | 模块8新增波动率输出行 |
| `skills/invest-a-stock/tests/test_technical.py` | 新增波动率单元测试 |

---

## 验收标准

- [ ] `calc_volatility()` 在 K 线数据不足5根时返回 `None`
- [ ] 20日/60日/全部三个窗口均正确输出
- [ ] 波动率输出在 report 模块8中可见
- [ ] 与 invest-a-etf 的对等实现在相同输入下结果一致
- [ ] 单元测试覆盖正常路径 + 边界条件（数据不足）
