#!/usr/bin/env bash
#
# check_report.sh — invest-A 研究报告措辞检查工具
#
# 用法:
#   ./check_report.sh                   # 扫描 reports/ 下最新 .md 文件
#   ./check_report.sh reports/xxx.md   # 指定文件检查
#
# 检查项:
#   1. 禁止词 (估值崩溃风险, 经典周期顶部信号, 股价也可能下跌, 往往是估值峰值,
#      往往伴随, 极度高估/极度低估, 崩盘)
#   2. 无来源"往往"/"通常"
#   3. 占位残留 ⏸ / 占位 / TBD / TODO
#   4. [事实] 前置 — [分析] 前必须有 [事实]
#   5. 绝对化表述 (无数据支撑的绝对判断)
#
# 退出码: 0 = 全部 PASS, 1 = 存在 FAIL 项

set -o pipefail

# 从脚本所在路径推算项目根: skills/invest-A/scripts/ -> 向上 3 层到 code/ 项目根
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# skills/invest-A/scripts/ -> skills/invest-A/ -> skills/ -> code/(项目根)
REPORTS_DIR="$(cd "$SCRIPT_DIR/../../../" && pwd)/reports"
# 验证 reports 目录存在
if [ ! -d "$REPORTS_DIR" ]; then
    echo "错误: 无法定位 reports/ 目录 (尝试: $REPORTS_DIR)" >&2
    exit 1
fi

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

PASS="${GREEN}PASS${NC}"
FAIL="${RED}FAIL${NC}"
WARN="${YELLOW}WARN${NC}"
PASS_S="${GREEN}✅ PASS${NC}"
FAIL_S="${RED}❌ FAIL${NC}"
WARN_S="${YELLOW}⚠️ WARN${NC}"

total_fail=0

# ---------- 确定检查文件 ----------
if [ $# -ge 1 ]; then
    TARGET_FILE="$1"
else
    # 查找 reports/ 下最新修改的 .md 文件 (兼容 macOS BSD find)
    TARGET_FILE=$(find "$REPORTS_DIR" -maxdepth 1 -name '*.md' -type f -exec ls -t {} + 2>/dev/null | head -1)
    if [ -z "$TARGET_FILE" ]; then
        echo "错误: reports/ 目录中未找到任何 .md 文件"
        echo "用法: $0 [reports/xxx.md]"
        exit 1
    fi
fi

if [ ! -f "$TARGET_FILE" ]; then
    echo "错误: 文件不存在: $TARGET_FILE"
    exit 1
fi

# 解析为绝对路径（兼容相对路径参数）
TARGET_FILE="$(cd "$(dirname "$TARGET_FILE")" && pwd)/$(basename "$TARGET_FILE")"

echo "## invest-A 措辞检查报告"
echo "检查文件: $TARGET_FILE"
echo ""
echo "| 检查项 | 状态 | 说明 |"
echo "|--------|------|------|"

# ============================================================
# 1. 禁止词检查
# ============================================================
check_banned_words() {
    local file="$1"
    local found_lines=""
    local count=0
    local detail=""

    # 定义禁止词列表 (每个模式一行)
    local patterns=(
        "估值崩溃风险"
        "经典周期顶部信号"
        "往往是估值峰值"
        "往往伴随"
        "极度高估"
        "极度低估"
        "崩盘"
    )

    for pat in "${patterns[@]}"; do
        # 用 grep -n 找匹配行，排除风险提示/免责声明区域（行内包含"风险提示"、"免责声明"或"⚠️"）
        while IFS= read -r line; do
            lineno=$(echo "$line" | cut -d: -f1)
            content=$(echo "$line" | cut -d: -f2-)
            # 跳过风险提示/免责声明行
            if echo "$content" | grep -qiE '风险提示|免责声明|不构成|⚠️'; then
                continue
            fi
            found_lines="$found_lines 第 ${lineno} 行: $(echo "$content" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/|/./g; s/\r//' | head -c 60)"
            count=$((count + 1))
        done < <(grep -n "$pat" "$file" 2>/dev/null)
    done

    # 特殊处理: "股价也可能下跌" — 检查是否出现在条件句式 "若...则..." 中
    while IFS= read -r line; do
        lineno=$(echo "$line" | cut -d: -f1)
        content=$(echo "$line" | cut -d: -f2-)
        # 跳过风险提示/免责声明行
        if echo "$content" | grep -qiE '风险提示|免责声明|不构成|⚠️'; then
            continue
        fi
        # 检查是否为独立预测（不在若/如果/假设条件句中）
        # 获取该行的前后几行上下文来判断是否在条件句中
        local context
        context=$(sed -n "$((lineno-2)),$((lineno+2))p" "$file" 2>/dev/null)
        if ! echo "$context" | grep -qiE '若|如果|假设|scenario|conditional|condition|假如'; then
            found_lines="$found_lines 第 ${lineno} 行: $(echo "$content" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/|/./g; s/\r//' | head -c 60)"
            count=$((count + 1))
        fi
    done < <(grep -n "股价也可能下跌" "$file" 2>/dev/null)

    if [ "$count" -eq 0 ]; then
        echo "| 禁止词 | $PASS_S | 未发现禁止词 |"
        return 0
    else
        detail=$(echo "$found_lines" | head -c 200)
        echo "| 禁止词 | $FAIL_S | 发现 ${count} 处: ${detail}... |"
        total_fail=$((total_fail + 1))
        return 1
    fi
}

# ============================================================
# 2. 无来源"往往"/"通常"检查
# ============================================================
check_unsourced_wangwang() {
    local file="$1"
    local count=0
    local detail=""

    # grep 匹配含 "往往" 或 "通常" 的行，排除包含来源标记的行
    while IFS= read -r line; do
        lineno=$(echo "$line" | cut -d: -f1)
        content=$(echo "$line" | cut -d: -f2-)
        # 排除: 包含 来源/source/WebSearch/tushare/akshare/baostock/腾讯/表头/分隔符
        if echo "$content" | grep -qiE '来源|source|WebSearch|tushare|akshare|baostock|tencent|腾讯|^\|---|^\||^[[:space:]]*$'; then
            continue
        fi
        # 排除表格行 (行含 | 且可能包含来源信息的)
        if echo "$content" | grep -qE '^\|.*\|.*\|'; then
            # 检查表格行是否明显不含来源
            if echo "$content" | grep -qiE '来源|source|WebSearch|tushare'; then
                continue
            fi
        fi
        # 排除表头/分隔行
        if echo "$content" | grep -qE '^[-]+|^[|][-]+|^[|][[:space:]]*[-]'; then
            continue
        fi
        count=$((count + 1))
        detail="$detail 第${lineno}行 "
    done < <(grep -nE '往往|通常' "$file" 2>/dev/null)

    if [ "$count" -eq 0 ]; then
        echo "| 无来源\"往往\" | $PASS_S | 未发现无数据支撑的经验式措辞 |"
        return 0
    else
        echo "| 无来源\"往往\" | $WARN_S | 发现 ${count} 处:${detail}"
        return 0  # WARN 不影响退出码
    fi
}

# ============================================================
# 3. 占位残留检查
# ============================================================
check_placeholders() {
    local file="$1"
    local count=0
    local detail=""

    local patterns=("⏸" "占位" "TBD" "TODO" "FIXME" "XXX" "待补充" "待填")

    for pat in "${patterns[@]}"; do
        while IFS= read -r line; do
            lineno=$(echo "$line" | cut -d: -f1)
            content=$(echo "$line" | cut -d: -f2-)
            count=$((count + 1))
            detail="$detail 第${lineno}行 "
        done < <(grep -n "$pat" "$file" 2>/dev/null)
    done

    if [ "$count" -eq 0 ]; then
        echo "| 占位残留⏸ | $PASS_S | 无占位标记 |"
        return 0
    else
        echo "| 占位残留⏸ | $WARN_S | 发现 ${count} 处:${detail}"
        return 0  # WARN 不影响退出码
    fi
}

# ============================================================
# 4. [事实] 前置检查
# ============================================================
check_fact_before_analysis() {
    local file="$1"
    local count=0
    local detail=""

    # 找到所有含 [分析] 的行，检查前面是否有 [事实]
    local last_fact_line=0

    while IFS= read -r line; do
        lineno=$(echo "$line" | cut -d: -f1)
        content=$(echo "$line" | cut -d: -f2-)

        # 记录最近出现的 [事实]
        if echo "$content" | grep -q '\[事实\]'; then
            last_fact_line=$lineno
        fi

        # 当遇到 [分析] 时检查
        if echo "$content" | grep -q '\[分析\]'; then
            # 从该行往前找 [事实]，如果在同一小节内（大约 50 行以内）有 [事实] 则通过
            local found_fact=0
            local check_start=$((lineno < 50 ? 1 : lineno - 50))
            for ((i = lineno - 1; i >= check_start; i--)); do
                local prev_line
                prev_line=$(sed -n "${i}p" "$file" 2>/dev/null)
                if echo "$prev_line" | grep -q '\[事实\]'; then
                    found_fact=1
                    break
                fi
                # 遇到新章节标题则停止搜索
                if echo "$prev_line" | grep -qE '^## '; then
                    break
                fi
            done

            if [ "$found_fact" -eq 0 ]; then
                count=$((count + 1))
                detail="$detail 第${lineno}行 "
            fi
        fi
    done < <(grep -n '\[分析\]' "$file" 2>/dev/null)

    if [ "$count" -eq 0 ]; then
        echo "| [事实]前置 | $PASS_S | 所有[分析]前有[事实] |"
        return 0
    else
        echo "| [事实]前置 | $FAIL_S | 发现 ${count} 处[分析]前缺少[事实]:${detail}"
        total_fail=$((total_fail + 1))
        return 1
    fi
}

# ============================================================
# 5. 绝对化表述检查
# ============================================================
check_absolute_statements() {
    local file="$1"
    local count=0
    local detail=""

    # 绝对化表述模式: 无来源的 "必然"/"一定"/"肯定"/"绝不"/"毫无疑问"
    # 但排除免责声明、风险提示行以及带来源的
    local patterns=("必然" "一定" "肯定" "绝不" "毫无疑问" "一定不会" "绝对不会")

    for pat in "${patterns[@]}"; do
        while IFS= read -r line; do
            lineno=$(echo "$line" | cut -d: -f1)
            content=$(echo "$line" | cut -d: -f2-)
            # 跳过风险/免责行
            if echo "$content" | grep -qiE '风险提示|免责声明|不构成|⚠️'; then
                continue
            fi
            # 跳过带来源的行
            if echo "$content" | grep -qiE '来源|source|WebSearch|tushare|akshare|baostock|腾讯'; then
                continue
            fi
            # 跳过表格行（通常结构化的表格不视为绝对化表述）
            if echo "$content" | grep -qE '^\|.*\|.*\|.*\|'; then
                continue
            fi
            count=$((count + 1))
            detail="$detail 第${lineno}行 "
        done < <(grep -n "$pat" "$file" 2>/dev/null)
    done

    if [ "$count" -eq 0 ]; then
        echo "| 绝对化表述 | $PASS_S | 未发现无数据支撑的绝对判断 |"
        return 0
    else
        echo "| 绝对化表述 | $WARN_S | 发现 ${count} 处:${detail}"
        return 0  # WARN 不影响退出码
    fi
}

# ============================================================
# 执行检查
# ============================================================
check_banned_words "$TARGET_FILE"
check_unsourced_wangwang "$TARGET_FILE"
check_placeholders "$TARGET_FILE"
check_fact_before_analysis "$TARGET_FILE"
check_absolute_statements "$TARGET_FILE"

echo ""

if [ "$total_fail" -gt 0 ]; then
    echo "结果: ${FAIL_S} (共 ${total_fail} 项 FAIL)"
    exit 1
else
    echo "结果: ${PASS_S} 全部通过"
    exit 0
fi
