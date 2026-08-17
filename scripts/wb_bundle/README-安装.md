# invest-skills · WorkBuddy 安装指南（普通用户版）

> 本包 = 6 个 A股投研技能（个股/ETF/交易日志/市场情绪/缺口扫描/底部形态）+ 一个 Python 数据引擎。
> 研究工具，非决策工具：不提供买卖建议，所有数字带来源标注。

## 一、安装（二选一，推荐方式一）

### 方式一：技能市场 GUI 导入（官方推荐，零终端）

1. 打开 WorkBuddy → 进入技能市场（技能/Claw 相关入口）
2. 点「添加技能」→「上传技能」
3. 选择本 zip 包（`invest-skills-wb-vX.Y.Z.zip`），确认导入
4. 官方说明：系统将自动完成配置，无需额外操作

> 导入后若搜索/唤起不到：重启客户端，或参考常见问题第 1 条。

### 方式二：手动拷贝（Windows 或方式一失败时）

1. 解压 zip，得到 `invest-skills/` 目录
2. 把整个目录放入技能目录：
   - macOS：`~/.workbuddy/skills/invest-skills/`
   - Windows：`%USERPROFILE%\.workbuddy\skills\invest-skills\`（社区对路径有分歧，若无效可试 `WorkBuddy\Claw\skills\`）
3. 完全重启 WorkBuddy

## 二、首次使用

在对话中说：**「检查 invest-skills 环境」**

技能会运行自检脚本，输出类似：

```
✅ uv: uv 0.x.x
✅ 依赖同步完成
✅ 包根已记录
✅ akshare + pandas 导入正常
=== 自检完成 ✅ ===
```

- 若提示 **未找到 uv**：把脚本给出的 1 条命令复制到「终端」（macOS）或 PowerShell（Windows）执行，再回来说一次「检查 invest-skills 环境」
- 依赖同步首次需 1-3 分钟，属正常

自检通过后，直接说需求即可，例如：

- 「分析 600176 中国巨石」
- 「563300 这只 ETF 怎么样」
- 「我要记录一笔买入」
- 「当前市场情绪如何」
- 「扫描一下跳空缺口」

## 三、Token 配置（可选，0 token 也能用）

免费数据源 akshare 已覆盖 A 股核心行情/公告。进阶功能需要 token：

| Key | 用途 | 获取 | 必须？ |
|---|---|---|---|
| `TUSHARE_TOKEN` | 财务/估值/资金/股东/K线（补充源） | tushare.pro 注册即送 | 推荐 |
| `FRED_API_KEY` | 美国 10Y 国债（DCF）、VIX | fred.stlouisfed.org 免费 | 可选 |
| `TAVILY_API_KEY` | 新闻搜索第三层 | tavily.com 免费 | 可选 |
| `BOCHA_API_KEY` | 研报/机构观点 | 按服务方注册 | 可选 |

配置方法（任选）：
1. **对话式（最简单）**：在对话中把 token 直接发给技能，说「帮我配置 TUSHARE_TOKEN=xxx」，技能会写入全局配置并限制权限
2. **手动**：创建文件 `~/.config/investment/.env`（Windows: `%USERPROFILE%\.config\investment\.env`），每行一个 `KEY=value`；写入后无需重启

## 四、常见问题

| 现象 | 处理 |
|---|---|
| 装完搜不到/唤起不了技能 | 重启客户端（技能索引自动重建约 10 秒）；仍不行删除 `index.db`/`fts_index.db` 再重启 |
| 采集报连接错误（东财相关） | Clash/VPN 需加直连规则：`DOMAIN-SUFFIX,eastmoney.com / gtimg.cn / baostock.com / tickflow.org → DIRECT` |
| 脚本执行逐条确认很烦 | 在 WorkBuddy 权限设置中使用 Craft 模式（免确认执行） |
| Windows 下 Bash 命令不可用 | WorkBuddy 有 Bash（git-bash）与 PowerShell 双通道，确认走 Bash 通道 |
| 报告在哪 | 包根目录下 `reports/` 文件夹 |

## 五、数据与隐私

- 技能在本机执行，采集数据缓存于 `~/.local/share/investment/`
- token 只写入本机 `~/.config/investment/.env`（权限 600），不上传
- 报告输出为本地 Markdown 文件，可自行备份/删除
