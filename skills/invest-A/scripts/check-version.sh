#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# check-version.sh — invest-A 版本一致性检查
# 用途：检查 SKILL.md / CLAUDE.md / pyproject.toml 版本一致性
#       并检测 SKILL.md 是否从缓存路径加载
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# ---- 参数解析 ----
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

CLAUDE_FILE="$REPO_ROOT/CLAUDE.md"
PYPROJECT_FILE="$REPO_ROOT/pyproject.toml"

# ---- 辅助函数：从文件提取版本 ----
extract_version_skill() {
    local f="$1"
    # 匹配 YAML frontmatter 中的 version: "x.y.z" 或 version: x.y.z
    sed -n '/^---$/,/^---$/p' "$f" 2>/dev/null \
        | grep -E '^version[[:space:]]*:' \
        | sed -E 's/^version[[:space:]]*:[[:space:]]*"?([^"]*)"?/\1/'
}

extract_version_claude() {
    local f="$1"
    # 匹配 当前版本：vX.Y.Z
    grep -E '当前版本：v[0-9]+\.[0-9]+\.[0-9]+' "$f" 2>/dev/null \
        | sed -E 's/.*当前版本：v([0-9]+\.[0-9]+\.[0-9]+).*/\1/'
}

extract_version_pyproject() {
    local f="$1"
    # 匹配 version = "x.y.z"
    grep -E '^version[[:space:]]*=' "$f" 2>/dev/null \
        | sed -E 's/^version[[:space:]]*=[[:space:]]*"([^"]*)"/\1/'
}

# ---- 检测缓存路径 ----
check_cache_path() {
    local skill_path="$1"
    # 规范化路径，展开所有 ..
    local resolved
    resolved="$(cd "$(dirname "$skill_path")" && pwd)/$(basename "$skill_path")"
    if echo "$resolved" | grep -qF '/.claude/plugins/cache/'; then
        echo "cache"
    else
        echo "normal"
    fi
}

# ---- 主逻辑 ----

# 检查文件是否存在
for f in "$SKILL_FILE" "$CLAUDE_FILE"; do
    if [[ ! -f "$f" ]]; then
        echo "❌ 文件不存在: $f" >&2
        exit 1
    fi
done

V_SKILL="$(extract_version_skill "$SKILL_FILE")"
V_CLAUDE="$(extract_version_claude "$CLAUDE_FILE")"

if [[ -z "$V_SKILL" ]]; then
    echo "❌ 无法从 SKILL.md 解析 version（检查 frontmatter 中的 version: 字段）" >&2
    exit 1
fi
if [[ -z "$V_CLAUDE" ]]; then
    echo "❌ 无法从 CLAUDE.md 解析版本（期望行：当前版本：vX.Y.Z）" >&2
    exit 1
fi

if [[ -f "$PYPROJECT_FILE" ]]; then
    V_PYPROJECT="$(extract_version_pyproject "$PYPROJECT_FILE")"
else
    V_PYPROJECT=""
fi

# 检查是否从缓存加载
CACHE_STATUS="$(check_cache_path "$SKILL_FILE")"

if [[ "$CACHE_STATUS" == "cache" ]]; then
    echo "⛔ invest-A 从缓存路径加载 SKILL.md"
    echo "   路径: $(cd "$(dirname "$SKILL_FILE")" && pwd)/$(basename "$SKILL_FILE")"
    echo "   请通过 /plugin 重新安装"
    exit 2
fi

# 版本一致性检查
MISMATCH=0
REPORT_LINES=()

append_report() {
    REPORT_LINES+=("$1")
}

append_report "SKILL.md=${V_SKILL}"
append_report "CLAUDE.md=${V_CLAUDE} (预期 ${V_SKILL})"
if [[ -n "$V_PYPROJECT" ]]; then
    append_report "pyproject.toml=${V_PYPROJECT}"
fi

if [[ "$V_SKILL" != "$V_CLAUDE" ]]; then
    MISMATCH=1
fi
if [[ -n "$V_PYPROJECT" && "$V_SKILL" != "$V_PYPROJECT" ]]; then
    MISMATCH=1
fi

if [[ "$MISMATCH" -eq 0 ]]; then
    if [[ -n "$V_PYPROJECT" ]]; then
        echo "✅ invest-A 版本一致: SKILL.md=${V_SKILL}, CLAUDE.md=${V_CLAUDE}, pyproject.toml=${V_PYPROJECT}"
    else
        echo "✅ invest-A 版本一致: SKILL.md=${V_SKILL}, CLAUDE.md=${V_CLAUDE}"
    fi
    echo "✅ SKILL.md 从正常路径加载"
    exit 0
else
    echo "⚠️ invest-A 版本不一致:"
    for line in "${REPORT_LINES[@]}"; do
        echo "  $line"
    done
    echo "❌ 修复方法：bash scripts/bump-version.sh ${V_SKILL}  # 统一更新三个文件的版本号"
    exit 1
fi
