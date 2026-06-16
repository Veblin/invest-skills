# CLAUDE.md — invest-A 投研助手

> 当前版本：v0.1.3 | 分支：feat/v0.1.3

## 版本规则

- 最多三位：`v{major}.{minor}.{patch}`
- 小幅迭代递增末位：`v0.1.3 → v0.1.4 → ...`
- 同版本内多次修订用日期区分，不自创四位版本号
- Git 分支：`feat/v{version}`

## 运行命令

```bash
# 所有命令必须用 uv run python，确保从 .venv 加载依赖
uv run python skills/invest-A/scripts/invest.py <subcommand> <symbol> [--flags]

# 常用子命令
diagnose     # 检查数据源可用性
collect      # 采集数据
report       # 生成报告
compare      # 双标的对比
diff         # 对比两次快照
store list   # 历史采集记录
```

## 数据源

| 源 | 状态 | 注意 |
|----|------|------|
| Tushare Pro | ✅ | 需 TOKEN |
| akshare | ✅ | 东方财富接口可能被代理拦截 |
| Baostock | ✅ | 免注册 |
| 腾讯行情 | ✅ | 免注册 |
| FRED | ✅ | 美宏观数据 |

**代理问题：** 东方财富 API 需直连。若 Clash/VPN 开启，需配置 `DOMAIN-SUFFIX,eastmoney.com,DIRECT`。

## 关键架构

- **多源并行**：`_run_sources_parallel`，非串行降级
- **所有源独立记录**：失败不阻塞，全失败标注 "未获取到任何有效数据"
- **禁止买卖建议、目标价、仓位建议**（LAW 6）
- **archive/ 目录**是 v0.2 遗留，不要引用

## 报告路径

- 数据源扩展方案：`code/reports/A股数据源扩展研究报告_v0.1.3.md`
- 个股报告：`code/reports/{symbol}-{name}-{date}.md`
