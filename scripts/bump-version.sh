#!/usr/bin/env bash
# ============================================================
# bump-version.sh — invest-A 批量版本号更新工具
#
# 用法:
#   bash scripts/bump-version.sh 0.1.8
#
# canonical: pyproject.toml
# 同步更新 5 个文件（由 scripts/version_sync.py 执行）：
#   - pyproject.toml
#   - skills/invest-A/SKILL.md (frontmatter version:)
#   - .claude-plugin/plugin.json
#   - .claude-plugin/marketplace.json (plugins[0].version)
#   - gemini-extension.json
# ============================================================
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "用法: bash scripts/bump-version.sh <新版本号>"
    echo "示例: bash scripts/bump-version.sh 0.1.8"
    exit 1
fi

NEW_VER="$1"

if ! echo "$NEW_VER" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "❌ 版本号格式错误: $NEW_VER"
    echo "   预期格式: X.Y.Z (例如 0.1.8)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."

cd "$REPO_ROOT"
echo "更新版本号为 v${NEW_VER} ..."
if uv run python scripts/version_sync.py bump "$NEW_VER"; then
    echo ""
    echo "下一步:"
    echo "  1. git checkout -b feat/v${NEW_VER}  # 新建分支（或重命名当前分支）"
    echo "  2. git add -A && git commit -m \"chore: bump version to v${NEW_VER}\""
    echo "  3. git tag v${NEW_VER}"
    echo "  4. git push && git push --tags"
else
    exit 1
fi
