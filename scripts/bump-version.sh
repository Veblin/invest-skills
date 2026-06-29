#!/usr/bin/env bash
# ============================================================
# bump-version.sh — invest-A 批量版本号更新工具
#
# 用法:
#   bash scripts/bump-version.sh 0.1.6
#
# 作用: 一次性更新以下三个文件的版本号，保证一致性：
#   - skills/invest-A/SKILL.md  (frontmatter version:)
#   - CLAUDE.md                 (当前版本：vX.Y.Z)
#   - pyproject.toml            (version = "X.Y.Z")
# ============================================================
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "用法: bash scripts/bump-version.sh <新版本号>"
    echo "示例: bash scripts/bump-version.sh 0.1.6"
    exit 1
fi

NEW_VER="$1"

# 验证版本号格式: X.Y.Z (纯数字)
if ! echo "$NEW_VER" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "❌ 版本号格式错误: $NEW_VER"
    echo "   预期格式: X.Y.Z (例如 0.1.6)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."

SKILL_FILE="$REPO_ROOT/skills/invest-A/SKILL.md"
CLAUDE_FILE="$REPO_ROOT/CLAUDE.md"
PYPROJECT_FILE="$REPO_ROOT/pyproject.toml"

# ---- 逐文件更新 ----
UPDATED=0

# 1. SKILL.md — frontmatter version: "X.Y.Z" 或 version: X.Y.Z
if [[ -f "$SKILL_FILE" ]]; then
    sed -i.bak -E 's/^(version[[:space:]]*:[[:space:]]*"?)[0-9]+\.[0-9]+\.[0-9]+("?)/\1'"$NEW_VER"'\2/' "$SKILL_FILE"
    rm -f "$SKILL_FILE.bak"
    echo "  ✅ SKILL.md"
    ((UPDATED++))
else
    echo "  ⏭️ SKILL.md 不存在"
fi

# 2. CLAUDE.md — 当前版本：vX.Y.Z | 分支：feat/vX.Y.Z (两处同时更新)
if [[ -f "$CLAUDE_FILE" ]]; then
    sed -i.bak -E \
      -e 's/(当前版本：)v[0-9]+\.[0-9]+\.[0-9]+/\1v'"$NEW_VER"'/' \
      -e "s|(分支：feat/)v[0-9]+\.[0-9]+\.[0-9]+|\1v$NEW_VER|" \
      "$CLAUDE_FILE"
    rm -f "$CLAUDE_FILE.bak"
    echo "  ✅ CLAUDE.md (版本+分支)"
    ((UPDATED++))
else
    echo "  ⏭️ CLAUDE.md 不存在"
fi

# 3. pyproject.toml — version = "X.Y.Z"
if [[ -f "$PYPROJECT_FILE" ]]; then
    sed -i.bak -E 's/^(version[[:space:]]*=[[:space:]]*")[0-9]+\.[0-9]+\.[0-9]+(")/\1'"$NEW_VER"'\2/' "$PYPROJECT_FILE"
    rm -f "$PYPROJECT_FILE.bak"
    echo "  ✅ pyproject.toml"
    ((UPDATED++))
else
    echo "  ⏭️ pyproject.toml 不存在"
fi

echo ""
if [[ "$UPDATED" -eq 3 ]]; then
    echo "✅ 版本已全部更新为 v${NEW_VER}"
    echo ""
    echo "下一步:"
    echo "  1. git checkout -b feat/v${NEW_VER}  # 新建分支（或重命名当前分支）"
    echo "  2. git add -A && git commit -m \"chore: bump version to v${NEW_VER}\""
    echo "  3. git tag v${NEW_VER}"
    echo "  4. git push && git push --tags"
else
    echo "⚠️ 部分更新 (${UPDATED}/3)"
    exit 1
fi
