---
name: invest-a-pattern-scan
version: "0.2.6"
description: "底部形态扫描 — LMW 双底/三角形底全市场检出 + 数据窥探防护（RC p）。研究信号，非决策。触发词：双底/形态扫描/三角形底/底部形态"
argument-hint: "/invest-a-pattern-scan → 双底/三角形底全市场扫描"
allowed-tools: Bash, Read, Write
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
---

# invest-a-pattern-scan v0.2.6

底部反转形态扫描器（MVP：双底 + 三角形底）。方法学：Lo-Mamaysky-Wang (2000, JF) 核平滑 + 5 极值模板；参数表与证据分级见 `skills/lib/references/scenario-plans.md` 同源设计（ABCD 设计 §2.3）。

**定位：研究信号，非决策。** 检出 = "该标的历史价格符合双底/三角形底的客观形态定义"，不构成任何交易建议（LAW 6）。

## 运行

```bash
cd "${INVEST_SKILLS_ROOT:-.}/skills/invest-a-pattern-scan/scripts/lib" && \
uv run python ../scan.py --universe csi300 a500 star50 --days 150
# 输出 docs/data/pattern_scan_result.json + stdout 摘要
```

## 输出解读

- `hits[]`：命中形态（ts_code / pattern / bandwidth / 形态几何详情）
- `reality_check`：**数据窥探防护**（White 2000 RC）——全规则宇宙（形态×带宽×窗口 = 12 规则）的最优规则是否显著优于噪声；`p_value < 0.05` 才可声称"该规则组合有统计增量信息"，否则命中列表仅作观察清单
- 带宽 0.3/0.5/1.0 三档敏感性：仅单一带宽命中的形态置信度低

## 方法学要点（报告引用时强制）

1. 形态客观化 = 核平滑 + 极值模板 + 容差（LMW 2000）；双底容差 1.5%、间距 ≥22 日、前回撤 ≥20%（120 日）
2. 因果滚动平滑（防 look-ahead）；终点 = 突破确认
3. **数据窥探防护不是选项**（全市场扫描 = 数千股票 × 多形态 × 多带宽 = 数十万检验）——RC p 必须随结果输出
4. 回踩状态分类（v0.2.6 补漏落地）：`classify_retest` 对每个命中输出
   `retest_status` ∈ {no_retest, clean_retest, deep_retest} + `retest_day`
   （突破后 3-10 日首次回踩参考位、收盘是否站回）。**C 级实操统计，非学术**
   ——锚点：无回踩突破后续表现最好、clean retest 优于 deep retest（ABCD B 类 §2.1）；
   仅作形态质量标注，不构成任何操作含义
5. 报告数字全部引用 `pattern_scan_result.json` 字段（P0 数字纪律）；形态几何值来自引擎计算

## 边界

- 不输出买卖/仓位建议；命中清单是研究观察清单
- A 股动量缺失（Liu-Stambaugh-Yuan 2019）→ "突破后延续"须降权，形态检出 ≠ 趋势确认
- 涨跌停扭曲形态统计：一字板日已在数据层保留，报告须注明
- 幸存者偏差：池子为指数成分并集（csi300+a500+star50），退市股不在池内

## Self-Check

1. ✅ 所有数字来自引擎/JSON 字段或 [来源: Python calc:]；无 AI 心算
2. ✅ RC p 值已输出且与结论一致（p≥0.05 时明确写"无统计增量信息"）
3. ✅ 无"双底=买入信号"类断言；形态名称后带几何数值与带宽档
4. ✅ 命中列表为观察清单，附证据分级（LMW A 级模板 / 参数 C 级阈值）
