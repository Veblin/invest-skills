"""测试卫生静态检查：私有辅助函数的 monkeypatch 不得打在 re-export 包命名空间。

背景（v0.2.3 CI 教训）：collector 拆分后 `lib.collector` 经
`_legacy.py`/`__init__.py` 逐层 star-import re-export，`lib.collector._xxx`
是**拷贝属性**。

判别规则（公共/私有二分）：
- **私有名（下划线前缀）** 如 `_run_sources_parallel`：按约定模块内部使用，
  调用方是定义模块（`_orchestrate`）自己的模块全局 → patch 包命名空间
  不生效 → 测试退化为环境依赖（本地有 token 时真实源成功而碰巧通过，
  CI 无源时失败，'No data returned' 骨架）。**必须**打定义模块命名空间
  `lib.collector._orchestrate._xxx` / `_base.` / `_sources.` / `_legacy.`。
- **公共名** 如 `attach_phase2_extras`：包 API，消费方经模块属性查找
  （`collector.attach_phase2_extras(...)`，如 render_markdown/_v2.py）→
  包级 patch 在调用时生效 → 合法。

另见 development-rules.md D13。
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# 合法 patch 目标前缀：定义模块命名空间（含其属性链）。
_ALLOWED_SUBMODULES = ("_orchestrate.", "_base.", "_sources.", "_legacy.")

# setattr/patch 上下文内的目标字符串（\s* 兼容跨行写法）
_TARGET_RE = re.compile(
    r"""(?:monkeypatch\.)?(?:setattr|patch)\(\s*["'](lib\.collector\.[A-Za-z_][A-Za-z0-9_.]*)["']"""
)


class TestMonkeypatchTargetHygiene:
    def test_private_helper_patches_use_definition_module(self):
        offenders: list[str] = []
        for test_file in sorted(TESTS_DIR.glob("test_*.py")):
            for lineno, line in enumerate(
                test_file.read_text(encoding="utf-8").splitlines(), 1
            ):
                for m in _TARGET_RE.finditer(line):
                    target = m.group(1)
                    rest = target[len("lib.collector."):]
                    if rest.startswith(_ALLOWED_SUBMODULES):
                        continue  # 定义模块命名空间 → 合法
                    if not rest.startswith("_"):
                        continue  # 公共 API：包命名空间属性查找是有效 patch 目标
                    offenders.append(f"{test_file.name}:{lineno}: {target}")
        assert not offenders, (
            "私有辅助函数的 patch 目标打在 re-export 包命名空间（无效拷贝，"
            "内部自调走定义模块全局）：\n"
            + "\n".join(offenders)
            + "\n应改用定义模块命名空间，如 lib.collector._orchestrate._run_sources_parallel"
        )
