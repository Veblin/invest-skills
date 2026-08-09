# WorkBuddy 全局 Token 配置模板（`~/.config/investment/.env`）

> 用途：为 WorkBuddy 中的 invest-skills 提供数据源 API Token。**引擎零改动**——
> 引擎加载优先级固定为 `os.environ > 项目 .env > 全局 ~/.config/investment/.env`
> （见 `skills/invest-a-stock/scripts/lib/env.py` 顶部 docstring）。
> 该方案与平台无关（macOS / Windows 均生效），**推荐优先于系统环境变量**。

## 写入步骤

```bash
mkdir -p ~/.config/investment
vim ~/.config/investment/.env     # 或任意编辑器，逐行填入 Key=Value
chmod 600 ~/.config/investment/.env   # 含密钥，限制权限
```

Windows（git-bash / PowerShell 均可）：

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.config\investment"
# 用记事本/VSCode 创建 $HOME\.config\investment\.env，内容同上
```

写入后**无需重启客户端**——引擎按需读取（仅设置环境变量需要完全重启客户端）。

## 9 个 Token 清单（与 gemini-extension.json.in settings 一致）

| Key | 用途 | 获取 |
|-----|------|------|
| `TUSHARE_TOKEN` | 财务/估值/资金/股东/K 线 | [tushare.pro](https://tushare.pro) 注册即送（积分档位见 README CONFIGURATION） |
| `FRED_API_KEY` | 美国 10Y 国债（DCF WACC）、VIX | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) 免费 |
| `TAVILY_API_KEY` | 新闻搜索 Layer3（可选，无 Key 静默降级） | [tavily.com](https://tavily.com) 免费 |
| `BOCHA_API_KEY` | 研报/机构观点补充源 | 按服务方注册 |
| `LONGBRIDGE_APP_KEY` | 长桥（港美股数据） | 长桥开放平台 |
| `LONGBRIDGE_APP_SECRET` | 长桥 App Secret | 长桥开放平台 |
| `LONGBRIDGE_ACCESS_TOKEN` | 长桥访问令牌 | 长桥开放平台 |
| `FINNHUB_API_KEY` | 美股/全球行情补充 | [finnhub.io](https://finnhub.io) 免费档 |
| `ALPHAVANTAGE_API_KEY` | 全球行情/基本面补充 | [alphavantage.co](https://www.alphavantage.co) 免费档 |

## 引擎加载优先级（验证过的行为，勿改）

```
os.environ > 项目 .env（仓库根 .env） > 全局 ~/.config/investment/.env
```

- 同名 Key 高优先级覆盖低优先级
- 未配置的 Key 走各模块降级链（引擎内标注「不可用」+ 降级来源），不阻塞流程
- 排查 token 问题时：先 `echo $KEY` 检查环境变量，再检查仓库 `.env`，最后检查全局文件

## 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 提示某数据源「不可用」但已填 token | Key 名拼写不一致 | 对照上表逐字符核对（大小写敏感） |
| Windows 下 token 不生效 | 填到了系统环境变量但未重启，或路径分隔符问题 | 改用 `~/.config/investment/.env` 方案（平台无关） |
| 想临时覆盖全局 token | 在 WorkBuddy 会话中 export 同名 Key | `os.environ` 优先级最高，覆盖全局文件 |
