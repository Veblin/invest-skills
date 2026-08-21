#!/usr/bin/env bash
# link-skills.sh — 在 macOS/Linux 上为 .agents/skills/ 建立技能 symlink（DSH/Kimi CLI/Amp 发现路径）
#
# 背景：.agents/skills/ 是 DSH（Amp/Kimi CLI 等）项目级技能发现目录（rank 200）。
# 形态与 .workbuddy/skills/ 同款先例一致：相对路径 symlink 指向 ../../skills/<name>，
# 避免绝对路径进版本库。仓库仅跟踪链接本身，目标以 skills/ 为准。
#
# Windows 用户请改用 scripts/setup_workbuddy_windows.ps1（NTFS junction 重建，
# 含 .agents\skills\ 共 23 条入口，无需管理员/开发者模式）。
#
# 幂等：已是 symlink 则跳过；真实目录/文件拒绝覆盖（防误删）；目标缺失时告警跳过。
# 可重复运行，第二次起全部 OK(skip)。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINK_DIR="$REPO_ROOT/.agents/skills"

SKILLS=(
    invest-a-stock
    invest-a-etf
    invest-a-journal
    invest-a-pulse
    invest-a-gap-scan
    invest-a-pattern-scan
)

mkdir -p "$LINK_DIR"

created=0
skipped=0
for name in "${SKILLS[@]}"; do
    link="$LINK_DIR/$name"
    target="../../skills/$name"

    if [ ! -d "$REPO_ROOT/skills/$name" ]; then
        echo "WARN: 跳过 $name：目标 skills/$name 不存在"
        continue
    fi

    if [ -L "$link" ]; then
        echo "OK(skip): $link 已是 symlink"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -e "$link" ]; then
        echo "WARN: 跳过 $name：$link 是真实目录/文件（非 symlink），拒绝覆盖——请人工处理后重跑"
        continue
    fi

    ln -s "$target" "$link"
    echo "OK: $link -> $target"
    created=$((created + 1))
done

echo ""
echo "完成：新建 $created 条，跳过 $skipped 条。验证：ls -la .agents/skills/"
