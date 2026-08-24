"""Unified emoji/icon dictionary for render modules.

All render code should import icons from here rather than hardcoding emoji strings.
This ensures visual consistency across sections (same concept = same icon everywhere).

Usage:
    from .render_icons import ICON_CV, ICON_EVIDENCE_STRONG
"""

# ── Cross-Validation ──────────────────────────────────────────────
ICON_CV = {
    "convergence": "🟢",   # multi-source data agrees
    "divergence": "🟡",    # multi-source data disagrees
    "gap": "🔴",           # missing cross-validation
}
ICON_CV_LABELS = {
    "convergence": "印证",
    "divergence": "分歧",
    "gap": "缺口",
}

# ── Evidence Strength（对齐 CLAUDE.md：✅ 强 / ⚠️ 中 / ❓ 弱）──
ICON_EVIDENCE_STRONG = "✅ 强"
ICON_EVIDENCE_MEDIUM = "⚠️ 中"
ICON_EVIDENCE_WEAK = "❓ 弱"
ICON_EVIDENCE_INSUFFICIENT = "数据不足"
