"""分发文档引用回归锁 — 运行指令不得引用包内不存在的规则。

**为什么需要源侧测试而不是只靠构建器**：CI 的 `build-skillhub-mirror.yml`
只构建 `invest-a-stock` / `invest-a-etf` 两个包，其余 7 个 skill 不进该路径。
只加在构建脚本里的门禁对它们没有保护；本文件在 `pyproject.testpaths` 内，
`validate.yml` 的每个 PR 都会跑，覆盖全部 8 个公开 skill。

**为什么源文件必须自己干净**：SkillHub 渠道会改写 SKILL.md，但 **WorkBuddy
渠道原样复制、零改写** —— 任何「靠改写器兜住」的方案在 WB 侧必然悬空。故正确
形态是源文件直接引用随包携带的共享规范，改写器只作路径形态适配。

口径（复核 §9.2）：目标是「**运行指令不得引用包内不存在的规则**」，**不是**
「包内不得出现 CLAUDE.md 字样」——说明性提及（如 pulse 的「WorkBuddy 环境无
CLAUDE.md，本规范自包含」）是有效内容，必须保留。故判据落在**引用形态**
（带章节引号 / 带路径），不落在字样。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL_MDS = sorted((ROOT / "skills").glob("*/SKILL.md"))
REFS_DIR = ROOT / "skills" / "lib" / "references"

# 带章节引号的引用：说明性提及定义上不带「」→ 零误伤
_CHAPTER_QUOTE_RE = re.compile(r"CLAUDE\.md\s*「")
# 共享规范路径引用（仓库相对形态 ../../../skills/lib/references/<file>）
_SHARED_REF_RE = re.compile(r"(?:\.\./)*skills/lib/references/([^\s)\]，。）」`]+)")
# 包内模块路径引用（skills/lib/<mod>.py）
_LIB_MODULE_RE = re.compile(r"(?:\.\./)*skills/lib/([A-Za-z_]\w*\.py)")


def test_all_skill_md_present():
    """锁覆盖面：8 个公开 skill 的根文档都在扫描集内（防 glob 静默变空）。"""
    assert len(SKILL_MDS) == 8, [p.parent.name for p in SKILL_MDS]


def test_no_chapter_quote_of_root_doc():
    """运行指令不得引用根指令文件的章节（两渠道都读不到该文件）。

    迁移完成后源文件应为 0 条 → 本断言从「改写规则」转为**回归锁**：
    将来任何人写回 `CLAUDE.md「…」`，这里立刻报红。
    """
    offenders = [f"{p.relative_to(ROOT)}:{i}"
                 for p in SKILL_MDS
                 for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if _CHAPTER_QUOTE_RE.search(line)]
    assert not offenders, f"SKILL.md 含根文档章节引用（包内悬空）：{offenders}"


def test_shared_ref_links_resolve():
    """SKILL.md 引用的共享规范必须在仓库内真实存在。"""
    missing = []
    for p in SKILL_MDS:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for name in _SHARED_REF_RE.findall(line):
                if not (REFS_DIR / name).is_file():
                    missing.append(f"{p.relative_to(ROOT)}:{i} → {name}")
    assert not missing, f"引用了不存在的共享规范：{missing}"


def test_lib_module_links_resolve():
    """SKILL.md 引用的共享模块必须在仓库内真实存在。"""
    missing = []
    for p in SKILL_MDS:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for name in _LIB_MODULE_RE.findall(line):
                if not (ROOT / "skills" / "lib" / name).is_file():
                    missing.append(f"{p.relative_to(ROOT)}:{i} → {name}")
    assert not missing, f"引用了不存在的共享模块：{missing}"


def test_pulse_declares_self_containment():
    """pulse 必须声明「复检规范随包、不依赖仓库根指令文件」。

    这是它作为独立分发包的有效依据。若为「清干净 CLAUDE.md 字样」把该声明一起删了，
    读者会以为该包依赖一个包里没有的文件（复核 §9.1「过重表述防线」的反面：
    不许因为要「去干净」而把有效说明删掉）。断言**意图**而非某句旧措辞。
    """
    pulse = (ROOT / "skills" / "invest-a-pulse" / "SKILL.md").read_text(encoding="utf-8")
    assert "不依赖仓库根指令文件" in pulse, "pulse 的自包含依据被删"


def test_shared_refs_carry_layout_relative_paths():
    """共享规范内的 `skills/lib/` 路径**在源仓是正确的**（仓库相对形态），

    构建期按包布局改写（script 布局 → `scripts/lib/`、pulse → `lib/`；WB/release
    树未变 → 保留原样）。本用例只锁定「改写器必须存在」这一前提的反面证据：
    源文件里确实有需要改写的路径 → 包侧断言（见 test_build_skillhub_packages.py）
    因此不是空跑。
    """
    with_paths = [p.relative_to(ROOT) for p in REFS_DIR.glob("**/*.md")
                  if "skills/lib/" in p.read_text(encoding="utf-8")]
    assert with_paths, (
        "没有任何共享规范含 skills/lib/ 路径 → 包侧改写断言成了空跑，"
        "需检查 B5 改写是否还有存在意义")
