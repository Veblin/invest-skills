#!/usr/bin/env bash
# ------------------------------------------------------------------
# 构建 WorkBuddy 发布包：bundle zip（完整仓库布局 + 入口 SKILL.md + bootstrap）
# 输出：dist/invest-skills-wb-v<version>.zip
# 复用：vX.Y.Z 发布时直接重跑本脚本即可
# ------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null \
  || uv run python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
ZIP="dist/invest-skills-wb-v${VERSION}.zip"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

PKG="$STAGE/invest-skills"
mkdir -p "$PKG/scripts" dist

# 入口 SKILL.md + bootstrap + 安装说明
cp scripts/wb_bundle/SKILL.md       "$PKG/SKILL.md"
cp scripts/wb_bundle/bootstrap.sh   "$PKG/scripts/bootstrap.sh"
cp scripts/wb_bundle/README-安装.md "$PKG/README-安装.md"

# 仓库布局：6 技能 + 共享 lib（排除 limit-up 与测试/缓存）
rsync -a \
  --exclude 'invest-a-limit-up' \
  --exclude 'tests' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  skills/ "$PKG/skills/"

# 依赖清单（uv sync 依据）
cp pyproject.toml uv.lock "$PKG/"

# 打包（顶层目录 invest-skills/，解压即得目录）
rm -f "$ZIP"
(cd "$STAGE" && zip -r -q "$OLDPWD/$ZIP" invest-skills)
echo "✅ $ZIP ($(du -h "$ZIP" | cut -f1))"

# T5-4 内容核验（R-B9）：HTML 渲染资产/渲染文件必须进包；禁止 pyc
LIST="$(unzip -l "$ZIP")"
echo "$LIST" | grep -q "assets/echarts.umd.min.js" \
  && echo "✅ echarts 资产在包" || { echo "❌ 缺 echarts 资产"; exit 1; }
echo "$LIST" | grep -q "lib/render_html.py" \
  && echo "✅ render_html 在包" || { echo "❌ 缺 render_html"; exit 1; }
if echo "$LIST" | grep -qE "__pycache__|\.pyc"; then
  echo "❌ 包内含 pyc"; exit 1
else
  echo "✅ 无 pyc"
fi
unzip -l "$ZIP" | tail -3
