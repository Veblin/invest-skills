"""Unified emoji/icon dictionary for render modules.

All render code should import icons from here rather than hardcoding emoji strings.
This ensures visual consistency across sections (same concept = same icon everywhere).

Usage:
    from .render_icons import ICON_OK, ICON_WARN, ICON_CV
"""

# ── Data / Source Status ──────────────────────────────────────────
ICON_OK = "✅"          # data available, source reachable
ICON_FAIL = "❌"        # data unavailable, source failed
ICON_SKIP = "⏭️"        # source not attempted, placeholder

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

# ── Evidence Strength（月球相位：满→缺 = 强→弱）────────────────
ICON_EVIDENCE_STRONG = "🌕"   # 强 — 满月
ICON_EVIDENCE_MEDIUM = "🌗"   # 中 — 下弦月
ICON_EVIDENCE_WEAK = "🌘"     # 弱 — 残月

# ── Semantic Markers ──────────────────────────────────────────────
ICON_WARN = "⚠️"        # warning, risk alert, caveat
ICON_VERIFY = "🔍"      # needs independent verification
ICON_REFERENCES = "📚"  # references / sources section
ICON_STAR = "⭐"         # star rating
ICON_CORE = "⚡️"        # core contradiction / key tension

# ── Direction ─────────────────────────────────────────────────────
ICON_UP = "⬆️"
ICON_DOWN = "⬇️"
ICON_FLAT = "➡️"
