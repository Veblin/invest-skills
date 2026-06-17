# invest-A Configuration Guide

配置优先级（高→低）：

1. **os.environ** — 进程环境变量
2. **项目 `.env`** — 项目根目录 `.env` 文件
3. **全局 `~/.config/investment/.env`** — 用户级配置

---

## Environment Variables

```bash
cp .env.example .env
```

| Variable | Required | Purpose | Registration |
|----------|----------|---------|--------------|
| `TUSHARE_TOKEN` | 推荐 | Tushare Pro API（A股数据主力源） | [tushare.pro](https://tushare.pro) |
| `FRED_API_KEY` | 可选 | FRED 美国宏观数据 | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |
| `INVEST_A_FORCE_AKSHARE_EM` | 可选 | 强制尝试东方财富 akshare 接口（跳过 push2 可达性预检） | 设为 `1` / `true` / `yes` |

**不配置 Tushare 时**：实时行情可通过腾讯免费接口获取，但财务指标、股东、资金流向等维度将不可用。
**不配置 FRED 时**：宏观维度需要通过 WebSearch 补充。

### 代理与东方财富（Clash / VPN）

采集器会自动绕过 `HTTP_PROXY` 等环境变量，使国内金融域名直连。若仍无法访问东方财富 push2（常见于 **TUN 模式**），引擎会跳过 akshare 行情/基本信息并回退 Tushare/Baostock。

- 运行 `invest.py diagnose` 查看 `proxy_bypass_effective` 与 `akshare_eastmoney_api` 状态
- HTTP 代理未绕过：在 Clash 规则中将 `eastmoney.com` 等设为 `DIRECT`（`diagnose` 会输出规则片段）
- TUN/CDN 阻断：暂时关闭 TUN 或全局代理后重试
- **`INVEST_A_FORCE_AKSHARE_EM=1`**：忽略 push2 预检，仍调度 akshare 东方财富任务（连接失败时由单源降级处理，适合排查网络）

---

## Tushare 积分与功能对照

积分规则以 [Tushare 官方权限说明](https://tushare.pro/document/1?doc_id=108) 及各接口文档为准。以下为 **invest-A 实际调用**的接口与降级行为。

### 按积分档次

| 积分 | 典型获取 | invest-A 可用能力 |
|------|---------|------------------|
| **无 Token** | 不配置 | 腾讯行情、baostock K 线；财务/资金/市场结构高分接口标注跳过 |
| **≥ 120** | 注册 + 完善资料 | `stock_basic`、`daily`（基本信息、行情、K 线） |
| **≥ 2000** | 社区捐助约 200 元/年 | 财务指标、股东、估值历史、资金流向、北向、申万**分类**（`index_classify`）、沪深300 等 `index_daily`、**业绩预告（`forecast`）** |
| **≥ 5000** | 更高捐助档位 | **`sw_daily` 申万行业日线**、**`opt_daily` 期权日线**（50ETF 认沽认购比） |
| **≥ 10000** | 高捐助档位 / 特色数据包 | **`report_rc` 研报盈利预测**（含评级+目标价）、**特色大数据集** |

### 按功能模块（市场结构 / v0.1.3）

| invest-A 功能 | Tushare 接口 | 官方最低积分 | 积分不足时的降级 |
|--------------|-------------|-------------|-----------------|
| 申万行业指数（20 日涨跌） | `sw_daily` | **5000** | **akshare `index_hist_sw`**（[接口文档](https://tushare.pro/document/2?doc_id=327)） |
| 申万行业分类/成分 | `index_classify` / `index_member_all` | 2000 | 因子跳过 |
| 沪深300 基准 | `index_daily` | 2000 | akshare `stock_zh_index_daily_em` |
| 主力资金 | `moneyflow` | 2000 | 因子跳过 |
| 北向个股 | `hsgt_top10` | 2000 | akshare 北向回退 |
| 融资余额 | `margin_detail` | 2000 | 因子跳过 |
| 换手率 | `daily_basic` | 2000 | 因子跳过 |
| ERP（沪深300 PE） | `index_dailybasic` | **4000** | 部分可得 / 标注 partial |
| 50ETF 认沽认购比 | `opt_daily` | **5000** | 因子跳过 |
| 机构研报评级+目标价 | `report_rc` | **10000**（特色大数据）| 降级至 forecast（业绩预告）→ akshare → 跳过 |
| 业绩预告（公司披露） | `forecast` | **2000** | 降级至 akshare → 跳过 |

说明：

- `index_daily` **不包含**申万行业行情，申万日线须用 `sw_daily`（5000 分）或引擎 akshare 回退。
- 权限不足时 `invest.py diagnose` 与各因子 `availability` 字段会标注；报告内显示 `[数据源不可用，该因子跳过]` 或 `akshare fallback`。
- 可用 `uv run python skills/invest-A/scripts/invest.py diagnose` 自查连通性与代理状态。

---

## CLI Flags

所有命令支持 `--help` 查看参数详情：

```bash
# 采集维度裁剪（从 code/ 目录运行）
python3 skills/invest-A/scripts/invest.py collect 600176 --dims=basic_info,financials,quote

# 机构研报维度（非默认，显式启用）
python3 skills/invest-A/scripts/invest.py collect 600176 --dims=basic_info,financials,quote,research

# 扩展日期范围
python3 skills/invest-A/scripts/invest.py collect 600176  # 默认范围已自动计算
```

---

## Per-Harness Install

### Claude Code

```bash
npx skills add . -g -y
# 或 symlink 开发模式：
ln -sfn "$PWD/skills/invest-A" ~/.agents/skills/invest-A
```

---

## Dependency Management

使用 `uv` 隔离依赖：

```bash
uv sync              # 安装所有依赖
uv run pytest        # 运行测试
uv run python skills/invest-A/scripts/invest.py diagnose
```
