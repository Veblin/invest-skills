#!/usr/bin/env bash
#
# check_report.sh — invest-A 研究报告措辞检查工具
#
# 委托 lint.py (YAML 规则引擎) 执行所有合规检查。
# 保留此脚本以兼容现有 pre-commit hook。
#
# 用法:
#   ./check_report.sh                   # 扫描 reports/ 下最新 .md 文件
#   ./check_report.sh reports/xxx.md   # 指定文件检查

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../" && pwd)"
INVEST_PY="$SCRIPT_DIR/invest.py"

if [ $# -ge 1 ]; then
    TARGET="$1"
else
    # 查找 reports/ 下最新修改的 .md 文件
    REPORTS_DIR="$PROJECT_ROOT/reports"
    if [ ! -d "$REPORTS_DIR" ]; then
        echo "错误: reports/ 目录不存在" >&2
        exit 1
    fi
    TARGET=$(find "$REPORTS_DIR" -maxdepth 1 -name '*.md' -type f -exec ls -t {} + 2>/dev/null | head -1)
    if [ -z "$TARGET" ]; then
        echo "错误: reports/ 目录中未找到任何 .md 文件" >&2
        exit 1
    fi
fi

# 委托 lint.py（YAML 驱动，规则源: compliance_rules.yaml）
cd "$PROJECT_ROOT" && exec uv run python "$INVEST_PY" lint "$TARGET"
