#!/usr/bin/env bash
# ------------------------------------------------------------------
# invest-A 环境配置检测脚本
# 在 SessionStart 钩子中运行，检测依赖是否就绪
# ------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

issues=0

echo "=== invest-A 环境检测 ==="

# 检测 Python
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1 | cut -d' ' -f2)
    echo "✅ Python $PY_VER"
else
    echo "❌ Python3 未安装"
    issues=$((issues + 1))
fi

# 检测 uv（推荐）或 pip
if command -v uv &>/dev/null; then
    echo "✅ uv $(uv --version 2>&1 | head -1)"
    VENV_EXISTS=""
    if [ -d "$PROJECT_ROOT/.venv" ]; then
        echo "✅ .venv 已创建"
    else
        echo "⚪ .venv 未创建 — 运行 'uv sync' 初始化"
    fi
elif command -v pip3 &>/dev/null; then
    echo "⚪ uv 未安装（推荐使用 uv 管理虚拟环境）"
else
    echo "❌ 未找到 uv 或 pip3"
    issues=$((issues + 1))
fi

# 检测可选 Token
for token in TUSHARE_TOKEN FRED_API_KEY TAVILY_API_KEY BOCHA_API_KEY; do
    if [ -n "${!token:-}" ]; then
        echo "✅ $token 已配置"
    else
        echo "⚪ $token 未配置（不配置可使用免费数据源）"
    fi
done

if [ "$issues" -gt 0 ]; then
    echo ""
    echo "⚠️  发现 $issues 个问题。运行 'uv sync' 安装依赖。"
    echo "   详见: https://github.com/veblin/invest-A"
else
    echo ""
    echo "✅ invest-A 环境就绪"
fi
