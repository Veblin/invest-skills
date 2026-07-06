#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# check-version.sh — invest-A 版本一致性检查
# canonical: pyproject.toml
# 校验 5 个分发 manifest 版本一致，并检测 SKILL.md 缓存路径
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

SKILL_FILE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skill=*)
            SKILL_FILE="${1#*=}"
            shift
            ;;
        *)
            echo "未知参数: $1" >&2
            echo "用法: $0 [--skill=path/to/SKILL.md]" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$SKILL_FILE" ]]; then
    SKILL_FILE="$REPO_ROOT/skills/invest-A/SKILL.md"
fi

check_cache_path() {
    local skill_path="$1"
    local resolved
    resolved="$(cd "$(dirname "$skill_path")" && pwd)/$(basename "$skill_path")"
    if echo "$resolved" | grep -qF '/.claude/plugins/cache/'; then
        echo "cache"
    else
        echo "normal"
    fi
}

if [[ ! -f "$SKILL_FILE" ]]; then
    echo "❌ 文件不存在: $SKILL_FILE" >&2
    exit 1
fi

CACHE_STATUS="$(check_cache_path "$SKILL_FILE")"
if [[ "$CACHE_STATUS" == "cache" ]]; then
    echo "⛔ invest-A 从缓存路径加载 SKILL.md"
    echo "   路径: $(cd "$(dirname "$SKILL_FILE")" && pwd)/$(basename "$SKILL_FILE")"
    echo "   请通过 /plugin 重新安装"
    exit 2
fi

cd "$REPO_ROOT"
if uv run python scripts/version_sync.py check; then
    echo "✅ SKILL.md 从正常路径加载"
    exit 0
fi
exit 1
