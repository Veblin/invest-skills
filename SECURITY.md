# 安全策略

## 支持的版本

| 版本 | 支持状态 |
|------|---------|
| 0.1.x (最新) | ✅ 安全更新 |
| 更早版本 | ❌ 不再维护 |

## 报告漏洞

invest-A 是一个本地运行的学习工具，不涉及远程服务或用户数据收集。但仍然可能存在以下安全风险：

1. **API Key 泄漏** — `.env` 文件中的 Tushare Token 或 FRED API Key 被意外提交
2. **依赖漏洞** — 使用的第三方库（requests、pandas 等）存在已知 CVE
3. **代码注入** — 恶意输入导致不受预期的代码执行

### 报告方式

请**不要**通过公开的 GitHub Issue 报告安全漏洞。请通过以下方式之一私下报告：

1. **GitHub Security Advisory** — 访问仓库的 [Security 页面](https://github.com/Veblin/invest-A-skill/security/advisories) 创建私有报告
2. **邮件** — 联系项目作者：veblin.w@gmail.com

我们承诺在收到报告后 **72 小时内**确认，并尽快发布修复。

## 安全措施

本项目已配置以下自动化安全机制：

- **Security Scan CI** (`.github/workflows/security.yml`) — 每次 Push/PR 扫描已跟踪文件中是否存在 API Key 泄漏模式
- **自动化依赖审计** (`.github/workflows/security.yml`) — 每周一运行 `pip-audit` 检查已知 CVE
- **`.gitignore`** — `.env` 文件被排除在版本控制之外
- **`.env.example`** — 提供不含真实 Key 的配置模板

### 最佳实践（给贡献者）

- 永远不要提交 `.env` 文件到版本控制（已通过 `.gitignore` 和 CI 双重保护）
- 定期运行 `uv run pip-audit` 检查依赖安全
- 报告模板中引用的 API Key 应使用占位符而非真实值
