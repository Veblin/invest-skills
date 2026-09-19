#!/usr/bin/env bash
# link-skills.sh — 在 macOS/Linux 上重建**全部**技能发现面 symlink
#
# 四个发现面（与 Windows 版 scripts/setup_workbuddy_windows.ps1 的链接表一一对应）：
#   .workbuddy/skills/<name>    目录链接（WorkBuddy）
#   .claude/skills/<name>       目录链接（Claude Code 项目级技能）
#   .agents/skills/<name>       目录链接（DSH/Amp/Kimi CLI，rank 200）
#   .claude/commands/<name>.md  文件链接 → skills/<name>/SKILL.md（slash command）
#
# 技能清单由 skills/*/SKILL.md **动态派生**（R1 验收 H6b 泛化）：此前本脚本只建
# .agents 一面且清单手写枚举，新增技能时其余三面各自滞后——实测 commands 面曾缺 4 条
# （新技能的 slash command 在 macOS/Linux 上无文件可加载，Windows 因走 ps1
# 硬链接而不复现）。
#
# 形态与 .workbuddy/skills/ 同款先例一致：相对路径 symlink（../../skills/<name>），
# 避免绝对路径进版本库。仓库仅跟踪链接本身，目标以 skills/ 为准。
#
# Windows 用户请改用 scripts/setup_workbuddy_windows.ps1（NTFS junction/hardlink，
# 无需管理员权限）。
#
# 幂等：已是 symlink 则跳过；真实目录/文件拒绝覆盖（防误删）；目标缺失时告警跳过。
# 可重复运行，第二次起全部 OK(skip)。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SKILLS=()
for skill_md in "$REPO_ROOT"/skills/*/SKILL.md; do
    [ -f "$skill_md" ] || continue
    SKILLS+=("$(basename "$(dirname "$skill_md")")")
done

if [ "${#SKILLS[@]}" -eq 0 ]; then
    echo "ERROR: skills/ 下未发现任何 SKILL.md" >&2
    exit 1
fi

created=0
skipped=0
warned=0

# link_one <链接路径> <链接目标（相对）> <目标存在性检查路径>
link_one() {
    local link="$1" target="$2" dest="$3"

    if [ ! -e "$dest" ]; then
        echo "WARN: 跳过 $link：目标不存在 $dest"
        warned=$((warned + 1))
        return
    fi

    if [ -L "$link" ]; then
        echo "OK(skip): $link 已是 symlink"
        skipped=$((skipped + 1))
        return
    fi

    if [ -e "$link" ]; then
        echo "WARN: 跳过 $link：已存在且非 symlink，拒绝覆盖——请人工处理后重跑"
        warned=$((warned + 1))
        return
    fi

    mkdir -p "$(dirname "$link")"
    ln -s "$target" "$link"
    echo "OK: $link -> $target"
    created=$((created + 1))
}

for name in "${SKILLS[@]}"; do
    # 三个目录面与 commands 面同为深度 2 → 相对目标统一为 ../../skills/...
    for surface in .workbuddy/skills .claude/skills .agents/skills; do
        link_one "$REPO_ROOT/$surface/$name" \
                 "../../skills/$name" \
                 "$REPO_ROOT/skills/$name"
    done
    link_one "$REPO_ROOT/.claude/commands/$name.md" \
             "../../skills/$name/SKILL.md" \
             "$REPO_ROOT/skills/$name/SKILL.md"
done

echo ""
echo "完成：新建 $created 条，跳过 $skipped 条，告警 $warned 条。"
echo "验证：ls -la .claude/commands/ .workbuddy/skills/ .claude/skills/ .agents/skills/"
