# CLAUDE.md — invest-A 投研助手

## 版本规则

- 最多三位：`v{major}.{minor}.{patch}`
- 小幅迭代递增末位：`v0.1.3 → v0.1.4 → ...`
- 同版本内多次修订用日期区分，不自创四位版本号
- Git 分支：`feat/v{version}`

### 版本号同步（统一修改）

**canonical 源**：`pyproject.toml` 的 `[project].version`（运行时经 `version.py` 读取）。

**禁止手动改多处版本号**。发布时只运行 bump 脚本，由 `scripts/version_sync.py` 同步 5 个分发 manifest：

```bash
bash scripts/bump-version.sh X.Y.Z
bash skills/invest-A/scripts/check-version.sh
```

运行 bump 后务必执行分支重命名（如当前分支是 `feat/v0.1.5`，重命名为 `feat/v0.1.6`）。

版本一致性仅在发布/CI 时校验（`check-version.sh`），**不在 Skill 运行时或 SessionStart 钩子中执行**。

## 运行命令

```bash
# 所有命令必须用 uv run python，确保从 .venv 加载依赖
uv run python skills/invest-A/scripts/invest.py <subcommand> <symbol> [--flags]

# 常用子命令
diagnose     # 检查数据源可用性
collect      # 采集数据（--with-news-pack 新闻三层架构）
report       # 生成报告
compare      # 双标的对比
diff         # 对比两次快照
store list   # 历史采集记录
rigor        # 财务验算（市值/估值/跨源）
audit        # 报告审计 extract/verdict
check        # 单标的质地检查（7 指标）
portfolio    # 组合风险特征
thesis       # 投资假设追踪
shock        # 价格冲击插值比例
```

### v0.1.9 新闻采集

- **Layer 1**：akshare 公告（始终可用）
- **Layer 2**：声明式 `query_pack`（供 Claude WebSearch）
- **Layer 3**：Tavily REST（`TAVILY_API_KEY` 可选）

```bash
uv run python skills/invest-A/scripts/invest.py collect 600176 --with-news-pack
```

## pip 规范

**永远不要在项目目录下直接运行 `pip install`** — `pip` 指向的是 Homebrew 全局 Python（`/opt/homebrew`），安装的包会污染系统环境，而且 `.venv` 里反而没有。

正确的操作：

| 场景 | 命令 |
|------|------|
| 安装/同步项目依赖 | `uv sync`（自动根据 `pyproject.toml` + `uv.lock` 同步） |
| 添加新依赖 | 编辑 `pyproject.toml` 的 `dependencies`，然后 `uv sync` |
| 查看已安装包 | `uv run python -m pip list` |
| 临时运行脚本 | `uv run python script.py` |
| 激活 .venv 后使用 pip | `source .venv/bin/activate && pip list` |

验证 .venv 是否生效：
```bash
uv run python -c "import sys; print(sys.executable)"
# 应输出 .../.venv/bin/python3，而不是 /opt/homebrew/...
```

## 数据源

详见 `skills/invest-A/references/source-guide.md` — 各数据源的注册要求、权限层级及代理注意事项的完整说明。

**代理问题：** 东方财富 API 需直连。若 Clash/VPN 开启，需配置 `DOMAIN-SUFFIX,eastmoney.com,DIRECT`。

### akshare 进度条过滤

akshare 调用时 tqdm 进度条输出到 stderr，不要用复杂 grep 过滤：

```bash
# ❌ 错误模式（ugrep/GNU grep 下 \|\| 解析为交替操作符，报 "empty subexpression"）
uv run python -c "..." 2>&1 | grep -v '^\d+%\|\|'

# ✅ 正确：直接丢弃 stderr（进度条在 stderr，数据在 stdout）
uv run python -c "..." 2>/dev/null

# ✅ 如果同时需要看错误信息：用 -E 扩展正则
uv run python -c "..." 2>&1 | grep -vE '^[0-9]+%\|'
```

## 宏观情景

每次分析时，在 SKILL.md SOP-M1 指导下：

1. 若 `--with-macro` 启用：FRED 数据注入 collector
2. Claude 在采集后读取以下指标并生成标签：
   - 增长: 中国 PMI [来源: akshare macro_china_pmi]
   - 通胀: CPI / PPI [来源: akshare macro_china_cpi / ppi]
   - 利率: LPR / US 10Y [来源: akshare macro_china_lpr / FRED]
   - 汇率: USD/CNY [来源: FRED]

输出格式（简报首行）：
[宏观情景] 增长（PMI XX.X）+ 通胀（CPI +X.X%）+ 政策（LPR X.X%）→ 偏宽松/中性/偏紧

## 关键架构

- **多源并行**：`_run_sources_parallel`，非串行降级
- **所有源独立记录**：失败不阻塞，全失败标注 "未获取到任何有效数据"
- **禁止买卖建议、仓位建议**（LAW 6）。**允许多情景估值参考价**（乐观/中性/悲观），须标注各情景的假设前提与概率权重，且注明"仅供参考，不构成投资建议"。**不允许不标注假设前提的单一目标价数字**（如"目标价 XX 元"）
- **archive/ 目录**是 v0.2 遗留，不要引用

## 措辞规范

### 禁止词与替换表

| 禁止词 | 替换为 | 适用场景 |
|--------|--------|---------|
| "估值崩溃风险" | "剧烈估值收缩风险：在 XXX 场景下，估值从 AAAx 收敛至 BBBx" | 风险模块 |
| "经典周期顶部信号" | "历史上周期股出现 X、Y 行为时，往往处于高景气或周期顶部附近；当前行为模式与这些模式相似，但并非充分条件" | 动态驱动 |
| "股价也可能下跌 60-80%" | "若盈利路径不及 AAA 预测、且估值回到周期中枢 BBBx，则与当前市值相比，价格有 60-80% 的回调空间" | 风险模块 |
| "往往是估值峰值" | "在多次周期品历史走势中，盈利峰值与估值峰值出现时间高度重叠（案例：XXX），但并非必然规律" | 市场结构 |
| "往往伴随 20-30% 的快速修正" | "在 AAA、BBB 等类似斜率的高涨中，随后 N 个月内出现 20-30% 回调（案例研究待补）" | 技术结构 |
| "极度高估"/"极度低估" | 不用形容词，改为 "PE 665x vs 同赛道平均 40x，溢价约 16 倍" | 估值段落 |
| "强烈触发" | 保留（客观的门槛判断），但后面接具体触发值 | 问题卡 |
| "崩盘" | "剧烈回调"（后接条件描述） | 任何场景 |

### 已知违规模式（禁止重复）

1. **"当前处于左侧/右侧"** — 违反 LAW 16。正确写法："左侧特征更强：XXXX；右侧支撑：YYYY。综合概率：左偏（≈60% vs 40%）"
2. **"建议买入/卖出/持有"** — 违反 LAW 6。永远不给买卖建议。
3. **"目标价 XX 元"（不标注假设前提的单一数字）** — 违反 LAW 6。允许多情景估值参考价，但须标注假设前提。格式："乐观情景（假设 AAA, 概率 BB%）: CC~DD 元；中性情景: EE~FF 元；悲观情景: GG~HH 元。仅供参考，不构成投资建议。"
4. **无来源的"往往""通常"** — 违反 LAW 3。口头经验必须标注"待补"或转为案例。
5. **PE 分位数用于亏损标的** — 违反 P0-2。亏损期标的的分位数必须标注"仅作位置参考"。
6. **"极度高估/低估"** — 违反措辞规则。必须用数值比较替换。

## 估值分位使用规则

1. **仅模块 1（当前状态快照）的"估值位置"段使用"分位"一词**。其余模块用"历史位置"或"区间位置"替代。
2. **亏损期标的强制标注**：如果 PE 历史序列中有超过 30% 的交易日为亏损期（负数 PE 已剔除），在分位数旁边标注：
   > "该标的历史大部分时间微利/亏损，PE 分位数仅作位置参考，不反映估值贵贱"
3. **分位数不单独使用**：每个分位数字必须伴随对应中位数或均值。
   - ✅ "PE 93.11x，近4年 98.3% 分位（中位数 37.52x）"
   - ❌ "PE 处于历史 98.3% 分位"

## 分析标记规范

每个分析性段落必须遵循以下结构：
1. 先列出 **[事实]** 块（引用数据来源），然后
2. 再写 **[分析]** 块（基于事实的逻辑推演）
3. **[分析]** 块末尾附带四维证据标签（SOP-EV）

### 证据强度四维标注

**维度 1 — 数据可靠性：** ✅ 强 / ⚠️ 中 / ❓ 弱
**维度 2 — 来源丰富度：** 🌐 多源 / 📡 单源 / 🔮 推测
**维度 3 — 时效性：** 🕐 近 30 日 / 📅 近季度 / 🗄️ 滞后 >1 年
**维度 4 — 交叉验证：** ✓✓ 多源一致 / ✓✗ 源间有差异 / — 单源无验证

组合示例：
[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ Tushare+akshare 一致]

示例：

[事实]
- 2026Q1 营收 131.38 亿（+52.7%），归母净利 11.10 亿（+143.5%）
- 毛利率 19.33%（同比 +5.19pp），净利率 8.56%（+3.25pp）
[来源: WebSearch / query: "公司 2026Q1 业绩" / 2026-06-17]

[分析]
利润增速远超收入增速，叠加毛利率跳升，反映：
(a) 规模效应释放（固定成本摊薄）
(b) 产品结构升级（高毛利新品占比提升）
(c) 两者均非一次性脉冲，而是结构性改善

[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 跨源可验证]

## 报告路径

- 数据源扩展方案：`reports/A股数据源扩展研究报告_v0.1.3.md`
- 个股报告：`reports/{symbol}-{name}/{date}.md`
- 财报 F 规范：`skills/invest-A/references/financials.md`
- 九模块结构：`skills/invest-A/references/modules.md`
