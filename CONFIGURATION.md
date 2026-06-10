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

**不配置 Tushare 时**：实时行情可通过腾讯免费接口获取，但财务指标、股东、资金流向等维度将不可用。
**不配置 FRED 时**：宏观维度需要通过 WebSearch 补充。

---

## CLI Flags

所有命令支持 `--help` 查看参数详情：

```bash
# 采集维度裁剪（从 code/ 目录运行）
python3 skills/invest-A/scripts/invest.py collect 600176 --dims=basic_info,financials,quote

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
