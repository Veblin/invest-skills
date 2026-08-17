#!/usr/bin/env bash
# ------------------------------------------------------------------
# invest-skills 环境自举脚本（幂等，可重复执行）
# 检测 Python/uv → 可选安装 uv → uv sync → 记录包根 → 引擎冒烟自检
# 用法：bash scripts/bootstrap.sh [--install]   # --install 仅 macOS 自动装 uv
# ------------------------------------------------------------------
set -euo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$HOME/.config/investment"
ROOT_FILE="$ENV_DIR/install_root"

echo "=== invest-skills 环境自检 ==="
echo "包根目录: $PACKAGE_ROOT"

# ---------- 1. 定位 uv ----------
find_uv() {
  command -v uv 2>/dev/null || true
}

UV="$(find_uv)"
if [ -n "$UV" ]; then
  echo "✅ uv: $("$UV" --version 2>&1 | head -1) ($UV)"
else
  echo "❌ 未找到 uv（Python 包管理器，安装一次长期可用）"
  echo ""
  echo "macOS / Linux —— 终端执行："
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "  # 装完后新开终端，或执行: source ~/.local/bin/env"
  echo "Windows —— PowerShell 执行："
  echo "  winget install --id=astral-sh.uv -e"
  if [ "${1:-}" = "--install" ] && [ "$(uname -s)" = "Darwin" ]; then
    echo ""
    echo ">> 检测到 --install，正在自动安装 uv ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV="$(find_uv)"
  fi
  if [ -z "$UV" ]; then
    echo ""
    echo "安装完成后重新运行: bash scripts/bootstrap.sh"
    exit 1
  fi
  echo "✅ uv 已就绪: $UV"
fi

# ---------- 2. 同步依赖（在包根创建 .venv） ----------
echo ""
echo "=== 同步依赖（uv sync，首次约 1-3 分钟） ==="
cd "$PACKAGE_ROOT"
if [ -f uv.lock ] && "$UV" sync --frozen 2>/dev/null; then
  echo "✅ 依赖同步完成（--frozen，锁文件一致）"
else
  echo "⚠️ --frozen 未通过，退回普通 uv sync（锁文件过期属正常，不影响使用）"
  "$UV" sync
  echo "✅ 依赖同步完成"
fi

# ---------- 3. 记录包根（供技能命令定位引擎） ----------
mkdir -p "$ENV_DIR"
echo "$PACKAGE_ROOT" > "$ROOT_FILE"
chmod 700 "$ENV_DIR"
chmod 600 "$ROOT_FILE"
echo "✅ 包根已记录: $ROOT_FILE -> $PACKAGE_ROOT"

# ---------- 4. 引擎冒烟测试 ----------
echo ""
echo "=== 引擎冒烟测试 ==="
if "$UV" run python -c "import akshare, pandas; print('✅ akshare + pandas 导入正常')" 2>/dev/null; then
  :
else
  echo "❌ 依赖导入异常，请把上方错误信息反馈给维护者"
  exit 1
fi

# ---------- 5. Token 提示 ----------
echo ""
echo "=== Token 配置（可选） ==="
if [ -f "$ENV_DIR/.env" ]; then
  echo "✅ 已存在 $ENV_DIR/.env"
else
  echo "0 token 也可用（免费数据源 akshare 覆盖 A 股核心数据）"
  echo "进阶可选：TUSHARE_TOKEN（财务/资金/股东数据，注册即送）、FRED_API_KEY（美债/VIX）、TAVILY_API_KEY（新闻搜索）"
  echo "获取与写入方法见 README-安装.md「Token 配置」节；也可在对话中把 token 直接交给技能代写"
fi

echo ""
echo "=== 自检完成 ✅ ==="
echo "下一步：在对话中直接说「分析 600176」或「市场情绪如何」即可"
