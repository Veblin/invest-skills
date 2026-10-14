# 按需经营证据链

沿用 ETF report，按研究问题选择本次披露持仓中的证券并说明原因；不逐持仓运行完整 stock 报告。没有执行时不声称已完成产业研究。

```bash
uv run python skills/invest-a-etf/scripts/etf.py report SYMBOL --json --history \
  --research /path/research.json --payload-out reports/SYMBOL-NAME/TIMESTAMP.payload.json
```

原料与解释在准备态完成后交给 report。入口验证实际文件及片段，只对显式证券调用公开 `data_bridge.get_financials`，一起封存；未指定参数时零额外研究请求。补证或修改解释须新代，旧签认失效。

## research.json（schema_version=1）

| 字段 | 合同 |
|---|---|
| symbol / as_of / holdings_as_of | ETF 六位代码／研究截止 ISO 日／本次持仓报告期末（绑定 holdings.report_date，非公告披露日） |
| question | 待检验问题，不预设结论 |
| budget | `max_names` 正整数、`target_cov_pct` (0,100]；本题显式预算，无行业标准默认 |
| selection | 非空对象列表，每项 `code/reason`；必须属于本次有权重的披露持仓；不补零、不重归一化 |
| sources | 每项 `id/title/url/source_type=original/published_date/read_at/locator/excerpt/sha256/text_sha256/path/text_path`。read_at 带时区；相对路径从 JSON 所在目录解析；PDF 定位给物理页及章节。原文件与提取正文分别核哈希，excerpt 须存在于正文 |
| facts | 每项 `id/kind/statement/source_ids/period_start/period_end`；kind 为 company/industry/event，company 另有 `code`。来源须存在，期间和披露不得晚于截止；事件期间表示发生或生效时点，发布日单列 |
| insights | 每项 `id/title/fact_ids/codes/scope=selected_holdings/interpretation/transmission/etf_link/conditional_conclusion/alternative/falsifier/boundary`。解释须回指事实，范围仅所选证券 |
| insights[].historical_links | 可选历史解释边，每项 `cause/effect` 是事实 ID；原因发生/生效日在被解释期末之后时拒绝；前瞻条件不解释此前财报 |
| insights[].comparisons | 可选同比证据配对，每项 `left/right` 为事实 ID；两个报告窗口月日起止须相同，不混半年、年度和单季 |

研究者须实际阅读相关章节，核对事实陈述与重要反证。机器检查文件/片段/日期不能证明因果充分。一次性收益、业务关系、口径和竞争解释保持人工逻辑核验。

`read_at` 换算至 Asia/Shanghai 的日期须不早于 `published_date` 且不晚于 `as_of`。披露字段只有日期时，同一天内的先后时刻无法确认，不能据此声称已验证具体发布时间。

## 写作与消费

report 输出 `research.narrative_md` 与 `research.appendix_md`：前者进入正文，解释事实意义和传导；后者放附录，列选择理由、经营事实、期间与原文定位。其余 ETF 职责仍按原模板完成。生成块不得封存后另改；调整判断必须修改准备材料、重新封存。

正文首屏先写经济含义（本题数据揭示的变化、对这只 ETF 的意义），再写交叉核验的方向/分歧，最后给有证据支撑的条件总判断与最关键反证；按实际证据选择，不机械套「身份/风险/缺口」三桶——身份介绍放既有产品段，**影响结论的限制只在相关判断处以读者语言点明（不强制首屏缺口表、不罗列未采功能）**，不相关维度省略；内部未核/未执行记录留 TaskRecords 与数据状态，不进读者报告。每个实质判断行保留来源与证据绑定（标签用读者能对应附录的名称）、推测仍以 `[推测，待验证]` 标注，不得以可读性删除未核状态；份额/金额估算与「申赎估算≠机构身份」等口径声明放后文，不在首屏重复列账。

完整共享财务返回保存在 `research.financials`；有同截止已知披露日的观察行单列，不拆单季、不平均公司增速。采集渠道不冒充原始披露；合并字段的公告时点仍须回原文核对。缺披露日的行只留底稿，不作确认性观察；来源冲突并列，不自动裁决。

HTML 原样嵌入完整 MD 并显示研究范围，不重新取证。QC 从冻结输入重建研究正文与附录，并核实际 HTML 研究节的覆盖、期间、限定与嵌入全文；仅保留代际哈希或研究标记不能通过，刷新 QC 记录亦不豁免内容核对。旧代缺研究块继续兼容，不代表经营证据链验收通过。经营改善、估值假设、价格与份额分别讨论，不能由份额变化识别未知机构动机，不能将所选样本当全组合。

按共享 delivery-qc 做机器准出及数字/合规/逻辑复检，原文层分开签认。完整计时包括选题、材料获取与阅读、财务采集、写作、复检返工、decision/HTML/QC；首次、可比缓存任务和源失败分列，不能以引擎耗时证明整体提速。
