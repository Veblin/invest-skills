"""LAW 收敛重做（v0.3.1 泳道 B）的防漂移测试。

守卫四件事：
1. 全部 18 个 legacy 标识在可执行契约（`SKILL.md`）中仍可解析；
2. 降级／废止清单精确，且废止项不被描述为现行 hard rule；
3. `law` 家族 rule id 一个都没改名（它们被 `test_lint.py`、`report_qc.py`、`hk.py` 硬编码）；
4. `law_ref` 已带新契约组，不再残留裸 `LAW n` 形式。

**只读仓内文件**：canonical 映射表在 `host-docs/`（被外层 `.gitignore:30` 忽略、CI 不可见），
故测试一律以随包的 `SKILL.md` 与 `compliance_rules.yaml` 为断言对象。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SKILL_MD = _ROOT / "skills" / "invest-a-stock" / "SKILL.md"
_RULES_YAML = _ROOT / "skills" / "invest-a-stock" / "scripts" / "references" / "compliance_rules.yaml"

# 18 个 legacy 标识 = LAW 1–17 + 独立 LAW 6a（区别于「17 条」：漏掉 6a 等于把 A1 禁令扩到交易结构）
_ALL_LEGACY = {str(n) for n in range(1, 18)} | {"6a"}

# 处置（与 host-docs/v0.3.1/law-contract-v0.3.1.md §1 一致）
_GROUPED = {"1", "3", "4", "6", "6a", "7", "8", "9", "12", "16", "17"}  # 11 条
_DOWNGRADED = {"10", "11", "13", "14", "15"}                             # 5 条
_RETIRED = {"2", "5"}                                                    # 2 条

# 冻结：law 家族 rule id（rename = 破坏 report_qc / test_lint / hk.py 的硬编码）
_FROZEN_LAW_RULE_IDS = {
    "law3-tongchang-unqualified", "law3-wangwang-unqualified",
    "law6-buy-advice", "law6-buy-standalone", "law6-hold-advice", "law6-hold-standalone",
    "law6-position-advice", "law6-sell-advice", "law6-sell-standalone", "law6-target-price",
    "law16-current-left-right", "law16-left-right-standalone",
    "law17-no-conclusion-sentence", "law17-no-logic-chain",
    "law17-noun-section-title", "law17-process-module-title",
}


def _skill() -> str:
    return _SKILL_MD.read_text(encoding="utf-8")


def _yaml() -> str:
    return _RULES_YAML.read_text(encoding="utf-8")


def _rule_ids(text: str) -> set[str]:
    return set(re.findall(r'^\s*- id:\s*"?([\w.\-]+)"?', text, re.M))


# ---------------------------------------------------------------- 覆盖与处置

def test_all_18_legacy_ids_still_resolve_in_contract():
    """归组 11 + 降级 5 + 废止 2 = 18，一个都不能从契约中消失。"""
    present = set(re.findall(r"LAW\s?(\d+a?)", _skill()))
    missing = _ALL_LEGACY - present
    assert not missing, f"契约未覆盖 legacy 标识：{sorted(missing)}"


def test_disposal_lists_are_exact_and_disjoint():
    """三份清单互斥且并集为 18——防「计数对账」再次跑偏（源文档曾误记「保留 12 条」）。"""
    assert _GROUPED | _DOWNGRADED | _RETIRED == _ALL_LEGACY
    assert not (_GROUPED & _DOWNGRADED) and not (_GROUPED & _RETIRED)
    assert not (_DOWNGRADED & _RETIRED)
    assert (len(_GROUPED), len(_DOWNGRADED), len(_RETIRED)) == (11, 5, 2)


def test_downgraded_and_retired_are_named_in_contract():
    """降级/废止必须写在契约里（agent 要能读到），不能只存在于 host-docs。"""
    skill = _skill()
    assert "降级与废止" in skill
    for n in sorted(_DOWNGRADED):
        assert f"LAW {n}" in skill, f"降级项 LAW {n} 未在契约中列出"
    for n in sorted(_RETIRED):
        assert f"LAW {n}" in skill, f"废止项 LAW {n} 未在契约中列出"
    assert "废止" in skill


def test_retired_laws_not_described_as_current():
    """LAW 2 / LAW 5 的旧条文不得再写成现行要求。"""
    skill = _skill()
    assert "**LAW 2** —" not in skill, "LAW 2 应已废止，不得保留为编号硬规则"
    assert "**LAW 5** —" not in skill, "LAW 5 应已废止（内容由 C4 重立）"


def test_old_parallel_wording_is_gone():
    """旧的 LAW 5 措辞「并行取证，汇总为证」不得再作为规则出现；改用可判定的「多源取证」。

    注意：废止说明里**提到**该旧措辞是允许的（那不是把它当现行规则），
    故这里断言的是**原措辞全文**消失，而非该词一切出现都禁止。
    """
    skill = _skill()
    assert "并行取证，汇总为证" not in skill
    assert "多源取证" in skill
    # 若仍出现「并行取证」字样，其所在行必须同时标注「废止」——否则就是被当作现行规则复述
    for line in skill.splitlines():
        if "并行取证" in line:
            assert "废止" in line, f"「并行取证」出现在非废止语境：{line.strip()}"


def test_contract_groups_all_present():
    """四组齐备，B 组（唯一无 legacy 来源的组）不得漏。"""
    skill = _skill()
    for g in ("契约 A", "契约 B", "契约 C", "契约 D"):
        assert g in skill, f"缺 {g}"
    for clause in ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4",
                   "C1", "C2", "C3", "C4", "D1", "D2", "D3", "D4"):
        assert f"**{clause}" in skill, f"缺子条款 {clause}"


# ---------------------------------------------------------------- rule id 与 law_ref

def test_law_family_rule_ids_frozen():
    """rule id 不得随 LAW 编号重构而改名（被三处硬编码）。"""
    ids = _rule_ids(_yaml())
    assert _FROZEN_LAW_RULE_IDS <= ids, f"缺失：{sorted(_FROZEN_LAW_RULE_IDS - ids)}"


def test_no_bare_law_ref_remains():
    """law_ref 必须同时指向新子条款与 legacy 标识，不得残留裸 `LAW n`。"""
    bare = re.findall(r'law_ref:\s*"(LAW\s?\d+a?[^"]*)"', _yaml())
    assert not bare, f"仍为裸 legacy 形式的 law_ref：{bare}"


# v0.3.1 声称同步过的分发面。**不含** `invest-a-stock/SKILL.md` 本体：其「降级与废止」
# 一节按设计就要写出 legacy 编号（见 test_downgraded_and_retired_are_named_in_contract）。
_BARE_LAW_SCAN_FILES = (
    "skills/invest-a-stock/references/agent-prompts.md",
    "skills/invest-a-journal/SKILL.md",
    "skills/invest-a-pattern-scan/SKILL.md",
    "skills/invest-hk-stock/SKILL.md",
    "skills/invest-a-etf/SKILL.md",
    "skills/invest-a-discover-scan/SKILL.md",
    "skills/invest-a-pulse/SKILL.md",
)


def test_no_bare_law_ref_in_synced_contract_surface():
    """分发面不得残留裸 `LAW n` —— 上一条测试只扫 YAML 的 `law_ref`，看不见这里。

    理由不是文风：LAW 1–17 的**定义文本只在 `host-docs/`**（不随包分发），
    分发用户拿到「LAW 6」无从解析。本次同步覆盖了这批文件，但漂移无人守卫——
    加一条扫描把它们钉住。允许两种形态：

    - `原 LAW n`：新子条款旁的 legacy 括注（如「A1／原 LAW 6」）
    - `JOURNAL-LAW n`：journal 自有命名空间，与全局 LAW 编号无关

    小写 `law6-sell-standalone` 是冻结的 rule id（另由 test_law_family_rule_ids_frozen
    守卫），不匹配本正则。
    """
    pattern = re.compile(r"LAW\s?\d+a?")
    offenders: list[str] = []
    for rel in _BARE_LAW_SCAN_FILES:
        p = _ROOT / rel
        if not p.is_file():
            continue
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                before = line[:m.start()].rstrip()
                if before.endswith("原") or before.endswith("JOURNAL-"):
                    continue
                snippet = line[max(0, m.start() - 24):m.end() + 12]
                offenders.append(f"{rel}:{lineno}: …{snippet}…")
    assert not offenders, "裸 LAW 引用（分发用户无法解析）：\n" + "\n".join(offenders)


def test_law_refs_stay_non_empty():
    """`test_v030_doc_checks.py:232,284` 对具体 rule 断言过该字段非空——重写后仍须成立。"""
    refs = re.findall(r'law_ref:\s*"([^"]*)"', _yaml())
    assert refs, "law_ref 字段消失"
    assert all(r.strip() for r in refs), "存在空 law_ref"


def test_yaml_still_ships_and_loads():
    """改文本不得破坏包内动态加载链（`test_build_skillhub_packages.py:435` 同源）。"""
    assert _RULES_YAML.is_file()
    assert len(_rule_ids(_yaml())) == 73, "rule 数量变化——本次只应改 law_ref 文本"
