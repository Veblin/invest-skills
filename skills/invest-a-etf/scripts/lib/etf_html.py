"""invest-a-etf HTML 报告渲染器 — 仪表盘 + md 全文原样嵌入。

设计约束（与 render_html.py 同族，但仅 ETF 专属节）：
- 零依赖零网络：Chart.js 本地 vendored（skills/invest-a-etf/scripts/lib/assets/），
  file:// 直开离线可用（opaque origin 下 fetch 被拦，故资产必须内联本地文件）；
- 渲染层不做任何计算：只直映引擎字段（格式化/排序/转义），无聚合、无派生推导
  （MA 序列引擎未输出 → 不重算）——P0 红线；
- md artifact 原样嵌入（etf_md.render_markdown），不重新撰写、不摘要、不修正
  目录锚点小瑕疵；
- 单字面量 _CHART_JS_REL = "assets/chart.umd.min.js" —— 供 build_skillhub_packages
  的 "assets/" 扫描触发资产复制（L355 机制，多 harness 闭包契约）。
"""

from __future__ import annotations

import html as _html_mod
import json
import logging
from pathlib import Path
from typing import Any

from _invest_path import (  # noqa: E402
    ensure_invest_a_scripts_on_path,
    ensure_skills_lib_on_path,
)

ensure_invest_a_scripts_on_path()
ensure_skills_lib_on_path()

from lib.nums import safe_float  # noqa: E402
from etf_md import MarkdownSubsetError, render_markdown  # noqa: E402

logger = logging.getLogger(__name__)

_CHART_JS_REL = "assets/chart.umd.min.js"
_CHART_JS_CACHE: str | None = None

_ONE_CN = "资产"  # placeholder 未使用，保持命名空间干净

# ── 渲染辅助（仅格式化/直映，无计算） ──


def _fmt(v, nd: int = 2, dash: str = "—") -> str:
    """引擎数值格式化；None → '—'。"""
    f = safe_float(v)
    return dash if f is None else f"{f:.{nd}f}"


def _fmt_signed(v, nd: int = 1, dash: str = "—") -> str:
    """带符号格式化（百分比类）；None → '—'。"""
    f = safe_float(v)
    return dash if f is None else f"{f:+.{nd}f}"


def _fmt_grouped(v, dash: str = "—") -> str:
    """整数千分位（金额类，元原样直映）。"""
    f = safe_float(v)
    return dash if f is None else f"{f:,.0f}"


def _pos_var(v, neutral: str = "var(--tx)") -> str:
    """正负 → 涨/跌色（0 → 中性）。A 股惯例：正=红（涨），负=绿（跌）。
    仅颜色直映，非判断。"""
    f = safe_float(v)
    if f is None:
        return neutral
    if f > 0:
        return "var(--rise)"
    if f < 0:
        return "var(--fall)"
    return neutral


def _esc(v) -> str:
    return _html_mod.escape("" if v is None else str(v), quote=True)


def _badge(text: str | None, cls: str = "b-ok") -> str:
    if not text:
        return ""
    return f'<span class="badge {cls}">{_esc(text)}</span>'


def _flags_badges(flags) -> str:
    """profile.flags 列表 → 徽章行（引擎 flag 文案原样）。"""
    if not isinstance(flags, list):
        return ""
    out = []
    for fl in flags:
        cls = "b-wn" if "⚠" in str(fl) else "b-ok"
        out.append(f'<span class="badge {cls}">{_esc(fl)}</span>')
    return "".join(out)


# ── Chart.js 加载（本地资产，失败回退空串——图表禁用，余下照常） ──


def _load_chart_js() -> str:
    """读取本地 chart.umd.min.js（缓存）。资产缺失 → ''（图表禁用不阻断）。"""
    global _CHART_JS_CACHE
    if _CHART_JS_CACHE is not None:
        return _CHART_JS_CACHE
    p = Path(__file__).resolve().parent / _CHART_JS_REL
    try:
        _CHART_JS_CACHE = p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart.umd.min.js not found at %s; charts disabled: %s", p, exc)
        _CHART_JS_CACHE = ""
    return _CHART_JS_CACHE


# ── CSS（app-shell 同族：主题变量 / 卡片 / 仪表盘 / md-body） ──

_HTML_CSS = r"""
:root {
  --font-body: "Inter","PingFang SC","Noto Sans SC",system-ui,sans-serif;
  --font-mono: "IBM Plex Mono","SF Mono",monospace;
  --text-xs:clamp(.75rem,.7rem + .25vw,.875rem);
  --text-sm:clamp(.8125rem,.75rem + .3vw,.9375rem);
  --text-base:clamp(.9375rem,.88rem + .3vw,1.0625rem);
  --text-lg:clamp(1.0625rem,.95rem + .6vw,1.375rem);
  --text-xl:clamp(1.375rem,1.1rem + 1.4vw,2rem);
  --space-1:.25rem;--space-2:.5rem;--space-3:.75rem;--space-4:1rem;
  --space-5:1.25rem;--space-6:1.5rem;--space-8:2rem;--space-10:2.5rem;
  --r-sm:.25rem;--r-md:.5rem;--r-lg:.75rem;--r-xl:1.25rem;
  --trans:180ms cubic-bezier(.16,1,.3,1);
  --bg:#0d0f12;--sur:#111417;--sur2:#161a1f;--sur3:#1c2128;
  --bdr:rgba(255,255,255,.07);--bdr-hi:rgba(255,255,255,.12);
  --tx:#e2e8f0;--tx-m:#8892a4;--tx-f:#4a5568;
  --ac:#38bdf8;--ac-dim:rgba(56,189,248,.12);
  --up:#34d399;--up-d:rgba(52,211,153,.12);
  --dn:#f87171;--dn-d:rgba(248,113,113,.12);
  --rise:#f87171;--rise-d:rgba(248,113,113,.14);
  --fall:#34d399;--fall-d:rgba(52,211,153,.14);
  --wn:#fbbf24;--wn-d:rgba(251,191,36,.1);
  --c1:#38bdf8;--c2:#818cf8;--c3:#34d399;--c4:#f87171;--c5:#fb923c;
  --sh:0 1px 3px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
}
[data-theme="light"]{
  --bg:#f4f6f9;--sur:#fff;--sur2:#f8fafc;--sur3:#f1f5f9;
  --bdr:rgba(0,0,0,.07);--bdr-hi:rgba(0,0,0,.12);
  --tx:#1a2030;--tx-m:#6b7a99;--tx-f:#a8b4cc;
  --ac:#0284c7;--ac-dim:rgba(2,132,199,.08);
  --up:#059669;--up-d:rgba(5,150,105,.08);
  --dn:#dc2626;--dn-d:rgba(220,38,38,.08);
  --rise:#dc2626;--rise-d:rgba(220,38,38,.1);
  --fall:#059669;--fall-d:rgba(5,150,105,.1);
  --wn:#d97706;--wn-d:rgba(217,119,6,.08);
  --sh:0 1px 2px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.06);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{-webkit-font-smoothing:antialiased;scroll-behavior:smooth;scroll-padding-top:52px}
body{font-family:var(--font-body);font-size:var(--text-base);color:var(--tx);background:var(--bg);min-height:100dvh;line-height:1.6}
button{cursor:pointer;background:none;border:none;font:inherit;color:inherit}
table{border-collapse:collapse;width:100%}
a{color:var(--ac);text-decoration:none}

/* layout */
.app{display:grid;grid-template-columns:200px 1fr;grid-template-rows:52px 1fr;min-height:100dvh}
.topbar{grid-column:1/-1;display:flex;align-items:center;gap:var(--space-3);padding:0 var(--space-6);height:52px;border-bottom:1px solid var(--bdr);background:var(--sur);position:sticky;top:0;z-index:100}
.sidebar{grid-row:2;background:var(--sur);border-right:1px solid var(--bdr);padding:var(--space-3) 0;position:sticky;top:52px;height:calc(100dvh - 52px);overflow-y:auto}
.main{grid-row:2;padding:var(--space-6) var(--space-8);display:flex;flex-direction:column;gap:var(--space-6)}

/* topbar */
.tl{display:flex;align-items:center;gap:var(--space-2);font-size:var(--text-xs);font-weight:700;letter-spacing:.08em;color:var(--tx-m);text-transform:uppercase}
.tl svg{color:var(--ac)}
.td{width:1px;height:18px;background:var(--bdr-hi)}
.tn{font-size:var(--text-base);font-weight:700}
.tc{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m);background:var(--sur3);padding:2px 8px;border-radius:var(--r-sm)}
.tp{font-family:var(--font-mono);font-size:var(--text-lg);font-weight:600;margin-left:auto}
.tch{font-family:var(--font-mono);font-size:var(--text-xs);padding:2px 8px;border-radius:var(--r-sm)}
.badge{font-size:var(--text-xs);font-family:var(--font-mono);padding:2px 8px;border-radius:var(--r-sm);border:1px solid}
.b-ok{color:var(--up);border-color:var(--up-d);background:var(--up-d)}
.b-wn{color:var(--wn);border-color:var(--wn-d);background:var(--wn-d)}
.tbtn{width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:var(--r-md);color:var(--tx-m);transition:background var(--trans),color var(--trans)}
.tbtn:hover{background:var(--sur3);color:var(--tx)}

/* sidebar */
.sbl{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--tx-f);padding:var(--space-3) var(--space-3) var(--space-1)}
.sbi{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-2) var(--space-4);font-size:var(--text-sm);color:var(--tx-m);transition:background var(--trans),color var(--trans);cursor:pointer;border-left:2px solid transparent;text-decoration:none}
.sbi:hover{background:var(--sur3);color:var(--tx);text-decoration:none}
.sbi.active{color:var(--ac);background:var(--ac-dim);border-left-color:var(--ac)}
.sbi svg{flex-shrink:0;opacity:.7}

/* section */
.sh{display:flex;align-items:baseline;gap:var(--space-3);margin-bottom:var(--space-4)}
.st{font-size:var(--text-lg);font-weight:700}
.ss{font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono);white-space:nowrap}
.sd{flex:1;height:1px;background:var(--bdr)}

/* card */
.card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-lg);padding:var(--space-5);box-shadow:var(--sh);transition:border-color var(--trans)}
.card:hover{border-color:var(--bdr-hi)}
.card-sm{padding:var(--space-4)}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--space-4)}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-4)}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-4)}
.g21{display:grid;grid-template-columns:2fr 1fr;gap:var(--space-4)}

/* kpi */
.kl{font-size:var(--text-xs);color:var(--tx-m);font-weight:500;text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-2)}
.kv{font-family:var(--font-mono);font-size:var(--text-xl);font-weight:600;line-height:1.1}
.ks{font-size:var(--text-xs);color:var(--tx-f);margin-top:var(--space-1);font-family:var(--font-mono)}

/* gauge */
.gr{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--bdr)}
.gr:last-child{border-bottom:none}
.gn{font-size:var(--text-xs);color:var(--tx-m);width:72px;flex-shrink:0}
.gtrack{flex:1;height:6px;background:var(--sur3);border-radius:3px;overflow:visible;position:relative}
.gfill{height:6px;border-radius:3px;position:relative;transition:width 1s cubic-bezier(.16,1,.3,1)}
.gmk{position:absolute;right:-3px;top:-3px;width:12px;height:12px;border-radius:50%;border:2px solid var(--sur)}
.gval{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx);width:72px;text-align:right;flex-shrink:0}
.gpct{font-family:var(--font-mono);font-size:var(--text-xs);width:44px;text-align:right;flex-shrink:0}

/* indicator pill */
.ipill{background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-md);padding:var(--space-3)}
.iname{font-size:var(--text-xs);color:var(--tx-f);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-1)}
.ival{font-family:var(--font-mono);font-size:var(--text-base);font-weight:600}
.isig{font-size:var(--text-xs);margin-top:var(--space-1)}
/* 强弱语义：A 股惯例 牛/强=红（涨色），熊/弱=绿（跌色） */
.sig-bear{color:var(--fall)}.sig-bull{color:var(--rise)}.sig-neutral{color:var(--wn)}

/* table (engine data / md-body) */
.ft th{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--tx-f);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi);text-align:right;white-space:nowrap}
.ft th:first-child{text-align:left}
.ft td{font-family:var(--font-mono);font-size:var(--text-xs);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr);text-align:right;color:var(--tx-m)}
.ft td:first-child{text-align:left;color:var(--tx-f)}
.ft tr:last-child td{border-bottom:none}

/* ref */
.rtog{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-3) var(--space-4);background:var(--sur2);border-radius:var(--r-md);cursor:pointer;font-size:var(--text-xs);color:var(--tx-m);border:1px solid var(--bdr);user-select:none;transition:background var(--trans);flex-wrap:wrap}
.rtog:hover{background:var(--sur3)}
.rbody{display:none;margin-top:var(--space-3)}
.rbody.open{display:block}
.ref-ok{color:var(--up)}.ref-err{color:var(--dn)}
code{font-family:var(--font-mono);font-size:.85em;background:var(--sur3);padding:1px 5px;border-radius:var(--r-sm);color:var(--tx-m)}

/* verify note */
.vnote{display:flex;align-items:flex-start;gap:var(--space-2);padding:var(--space-2) var(--space-3);background:var(--wn-d);border-radius:var(--r-sm);border-left:2px solid var(--wn);font-size:var(--text-xs);color:var(--tx-m);margin-top:var(--space-3)}
.vnote svg{flex-shrink:0;margin-top:2px}

/* chart */
.cw{position:relative;height:220px}
.cw-sm{position:relative;height:160px}

/* md embed (etf_md 输出结构) */
.md-body{font-size:var(--text-sm);line-height:1.75;color:var(--tx-m)}
.md-body h1{font-size:var(--text-xl);color:var(--tx);margin:var(--space-5) 0 var(--space-3);padding-bottom:var(--space-2);border-bottom:1px solid var(--bdr)}
.md-body h2{font-size:var(--text-lg);color:var(--tx);margin:var(--space-5) 0 var(--space-2);padding-bottom:var(--space-2);border-bottom:1px solid var(--bdr)}
.md-body h3{font-size:var(--text-base);color:var(--tx);margin:var(--space-4) 0 var(--space-2)}
.md-body h4{font-size:var(--text-sm);color:var(--tx);margin:var(--space-4) 0 var(--space-2)}
.md-body p{margin:var(--space-2) 0}
.md-body ul,.md-body ol{margin:var(--space-2) 0;padding-left:var(--space-6)}
.md-body li{margin:var(--space-1) 0}
.md-body blockquote{margin:var(--space-3) 0;padding:var(--space-2) var(--space-4);border-left:3px solid var(--ac);background:var(--sur2);border-radius:0 var(--r-md) var(--r-md) 0;color:var(--tx-m)}
.md-body blockquote p{margin:var(--space-1) 0}
.md-body hr{margin:var(--space-4) 0;border:none;border-top:1px solid var(--bdr)}
.md-body strong{color:var(--tx)}
.md-body a{text-decoration:underline}
.md-body .mdt{margin:var(--space-3) 0;font-family:var(--font-mono);font-size:var(--text-xs)}
.md-body .mdt th{font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--tx-f);padding:var(--space-2);border-bottom:1px solid var(--bdr-hi);white-space:nowrap}
.md-body .mdt td{padding:var(--space-2);border-bottom:1px solid var(--bdr);color:var(--tx-m)}
.md-body .mdt tr:last-child td{border-bottom:none}

/* disclaimer */
.disc{font-size:var(--text-xs);color:var(--tx-f);padding:var(--space-4);background:var(--sur2);border-radius:var(--r-md);border:1px solid var(--bdr);line-height:1.8}
.disc strong{color:var(--wn)}

/* scrollbar */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bdr-hi);border-radius:3px}

@media(max-width:900px){
  .app{grid-template-columns:1fr}
  .sidebar{display:none}
  .main{padding:var(--space-4)}
  .g4{grid-template-columns:repeat(2,1fr)}
  .g3,.g2,.g21{grid-template-columns:1fr}
}
"""

# ── HTML 片段构建 ──


def _html_topbar(symbol: str, name: str, price_str: str, change_str: str,
                 price_color: str, chg_color: str) -> str:
    return f'''<header class="topbar">
  <div class="tl">
    <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
      <rect x="1.5" y="1.5" width="19" height="19" rx="4" stroke="currentColor" stroke-width="1.5"/>
      <path d="M4 15L8.5 9L11.5 12L18 5.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    invest:a-etf
  </div>
  <div class="td"></div>
  <span class="tn">{_esc(name or symbol)}</span>
  <span class="tc">{_esc(symbol)}</span>
  <span class="tp" style="color:{price_color}">{price_str}</span>
  <span class="tch" style="color:{chg_color}">{change_str}</span>
  <button class="tbtn" data-theme-toggle aria-label="切换主题">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
  </button>
</header>'''


def _html_sidebar() -> str:
    return '''<nav class="sidebar">
  <div class="sbl">概览</div>
  <a class="sbi active" href="#overview"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>产品快照</a>
  <div class="sbl">数据</div>
  <a class="sbi" href="#valuation"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>指数估值</a>
  <a class="sbi" href="#holdings"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>持仓透视</a>
  <a class="sbi" href="#quality"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>跟踪质量</a>
  <a class="sbi" href="#history"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 17 9 11 13 15 21 7"/></svg>历史演变</a>
  <a class="sbi" href="#flows"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M2 12l10-10 10 10"/></svg>资金流向</a>
  <div class="sbl">叙事</div>
  <a class="sbi" href="#research"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>研究备忘</a>
  <a class="sbi" href="#refs"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>数据来源</a>
</nav>'''


def _kpi_card(label: str, value: str, sub: str = "", color: str = "") -> str:
    color_attr = f' style="color:{color}"' if color else ""
    sub_html = f'<div class="ks">{sub}</div>' if sub else ""
    return (f'<div class="card card-sm"><div class="kl">{_esc(label)}</div>'
            f'<div class="kv"{color_attr}>{value}</div>{sub_html}</div>')


def _ipill(name: str, value: str, sig: str = "", sig_cls: str = "sig-neutral",
           color: str = "") -> str:
    color_attr = f' style="color:{color}"' if color else ""
    sig_html = f'<div class="isig {sig_cls}">{_esc(sig)}</div>' if sig else ""
    return (f'<div class="ipill"><div class="iname">{_esc(name)}</div>'
            f'<div class="ival"{color_attr}>{value}</div>{sig_html}</div>')


def _gauge(label: str, val: str, pct: str, color: str) -> str:
    return (f'<div class="gr"><div class="gn">{_esc(label)}</div>'
            f'<div class="gtrack"><div class="gfill" style="width:{pct}%;background:{color}">'
            f'<div class="gmk" style="background:{color}"></div></div></div>'
            f'<div class="gval">{val}</div>'
            f'<div class="gpct" style="color:{color}">{pct}%</div></div>')


def _vnote(text: str) -> str:
    return (f'<div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/>'
            f'<path d="M12 8v4m0 4h.01"/></svg>{_esc(text)}</div>')


def _section_overview(profile: dict, quote: dict, kline: dict) -> str:
    price = safe_float(quote.get("price"))
    chg = safe_float(quote.get("change_pct"))
    price_str = _fmt(price, 3) if price is not None else "—"
    price_color = _pos_var(chg) if chg is not None else "var(--tx)"
    chg_str = _fmt_signed(chg) + "%" if chg is not None else "—"
    chg_color = "var(--fall)" if (chg is not None and chg < 0) else ("var(--rise)" if (chg is not None and chg > 0) else "var(--tx-m)")

    aum = profile.get("aum")
    pd = profile.get("premium_discount")
    vol_ann = kline.get("volatility_annualized")
    derived = kline.get("derived") or {}
    daily_vol = derived.get("daily_volatility_pct")
    rsi = kline.get("rsi")
    rsi_period = kline.get("rsi_period")
    rsi_sub = f"RSI({rsi_period})" if rsi_period else "RSI"
    rsi_color = _pos_var(None)  # RSI 无正负含义，中性
    rsi_cls = "sig-neutral"
    if safe_float(rsi) is not None and safe_float(rsi) > 70:
        rsi_cls = "sig-bull"
    elif safe_float(rsi) is not None and safe_float(rsi) < 30:
        rsi_cls = "sig-bear"

    nav_ma20 = derived.get("nav_vs_ma20_pct")
    nav_ma60 = derived.get("nav_vs_ma60_pct")
    bol_pos = derived.get("boll_position_pct")

    def pos_sig(v: float | None) -> tuple[str, str]:
        f = safe_float(v)
        if f is None:
            return "—", "sig-neutral"
        if f > 0:
            return f"上方 {f:+.1f}%", "sig-bull"
        if f < 0:
            return f"下方 {f:+.1f}%", "sig-bear"
        return "附近", "sig-neutral"

    s20, c20 = pos_sig(nav_ma20)
    s60, c60 = pos_sig(nav_ma60)
    bol_color = "var(--rise)" if (safe_float(bol_pos) or 0) > 60 else (
        "var(--fall)" if (safe_float(bol_pos) or 0) < 40 else "var(--tx)")
    bol_sig = f"带内位置 {_fmt(bol_pos)}%" if bol_pos is not None else "—"

    flags_html = _flags_badges(profile.get("flags"))
    return f'''<section id="overview">
  <div class="sh"><span class="st">产品快照</span><div class="sd"></div><span class="ss">{_esc(profile.get("category", {}).get("label", "") or "")}</span></div>
  <div class="g4">
    {_kpi_card("最新价", price_str, f"较昨收 {chg_str}", price_color)}
    {_kpi_card("AUM（亿元）", _fmt(aum) if aum is not None else "—", "profile.aum")}
    {_kpi_card("折溢价（%）", _fmt_signed(pd, 2) if pd is not None else "—", "profile.premium_discount", _pos_var(pd))}
    {_kpi_card("成交额（元）", _fmt_grouped(quote.get("amount")), "quote.amount")}
  </div>
  <div class="g3" style="margin-top:var(--space-4)">
    {_kpi_card("年化波动（%）", _fmt(vol_ann), "kline.volatility_annualized")}
    {_kpi_card("日均波动（%）", _fmt(daily_vol), "kline.derived.daily_volatility_pct")}
    {_kpi_card(rsi_sub, _fmt(rsi, 1) if rsi is not None else "—", "状态描述，非信号", rsi_color)}
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">NAV 相对均线/布林带（引擎 derived）</div>
    <div class="g3">
      {_ipill("NAV vs MA20", _fmt_signed(nav_ma20) + "%" if nav_ma20 is not None else "—", s20, c20, _pos_var(nav_ma20))}
      {_ipill("NAV vs MA60", _fmt_signed(nav_ma60) + "%" if nav_ma60 is not None else "—", s60, c60, _pos_var(nav_ma60))}
      {_ipill("BOLL 位置", bol_sig, "boll_position_pct 0%=下轨 100%=上轨", "sig-neutral", bol_color)}
    </div>
    <div style="margin-top:var(--space-3);display:flex;gap:var(--space-3);flex-wrap:wrap">
      {_badge("BOLL 带宽 " + (_fmt(derived.get("boll_bandwidth_pct")) + "%"), "b-ok")}
      {_badge("NAV 距下轨 " + (_fmt_signed(derived.get("nav_to_boll_lower_pct")) + "%"), "b-ok")}
      {_badge("NAV 距上轨 " + (_fmt_signed(derived.get("nav_to_boll_upper_pct")) + "%"), "b-ok")}
    </div>
  </div>
  <div class="card card-sm" style="margin-top:var(--space-4)">
    <div class="kl">引擎自动标记（flags）</div>
    <div style="display:flex;gap:var(--space-2);flex-wrap:wrap">{flags_html or '<span class="badge b-ok">无违规旗标</span>'}</div>
  </div>
</section>'''


def _section_valuation(profile: dict) -> str:
    pe = profile.get("index_pe")
    pct = profile.get("index_pe_pct")
    status = profile.get("index_pe_status")
    vg = profile.get("valuation_guide") or {}
    pe_str = _fmt(pe) if pe is not None else "—"
    pct_str = _fmt(pct, 1) if pct is not None else "0"
    pct_val = safe_float(pct)
    width = max(0.0, min(100.0, pct_val)) if pct_val is not None else 0.0
    gauge_color = "var(--rise)" if (pct_val or 0) > 80 else ("var(--fall)" if (pct_val or 0) < 20 else "var(--c1)")
    status_cls = "b-ok" if status == "mapped" else "b-wn"

    primary = vg.get("primary") if isinstance(vg, dict) else None
    secondary = vg.get("secondary") if isinstance(vg, dict) else None
    pe_timing = vg.get("pe_timing")
    timing_text = "true" if pe_timing else ("false" if pe_timing is not None else "未知")

    guide_rows = ""
    if isinstance(vg, dict) and vg:
        guide_rows = (f'<tr><td>行业/子环节</td><td>{_esc(vg.get("industry", ""))} / {_esc(vg.get("sub_sector", ""))}</td></tr>'
                      f'<tr><td>主要估值指标</td><td>{_esc(primary)}</td></tr>'
                      f'<tr><td>次要指标</td><td>{_esc(secondary)}</td></tr>'
                      f'<tr><td>PE 可用于择时</td><td>{_esc(timing_text)}</td></tr>')

    return f'''<section id="valuation">
  <div class="sh"><span class="st">指数估值</span><div class="sd"></div><span class="ss">csindex PE · 引擎累积分位（非长历史）</span></div>
  <div class="card">
    <div style="font-size:var(--text-xs);color:var(--tx-f);margin-bottom:var(--space-4)">分位越低代表估值越低（相对引擎历史累积窗口；累积长度有限，参考性弱于长历史序列）</div>
    <div class="gr">
      <div class="gn">指数 PE</div>
      <div class="gtrack"><div class="gfill" style="width:{width:.1f}%;background:{gauge_color}"><div class="gmk" style="background:{gauge_color}"></div></div></div>
      <div class="gval">{pe_str}</div><div class="gpct" style="color:{gauge_color}">{pct_str}%</div>
    </div>
    <div style="margin-top:var(--space-3)">
      {_badge("index_pe_status: " + str(status), status_cls)}
      {_badge(index_pe_note_short(profile))}
    </div>
    {_vnote(profile.get("index_pe_note") or "指数 PE 历史分位基于引擎累积序列，参考性弱于长历史样本。")}
  </div>
  {('<div class="card" style="margin-top:var(--space-4)">'
     '<div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">估值框架（引擎 valuation_guide）</div>'
     '<table class="ft"><tbody>' + guide_rows + '</tbody></table>'
     '</div>') if guide_rows else ''}
</section>'''


def index_pe_note_short(profile: dict) -> str:
    return "PE(1) 股本加权口径" if profile.get("index_pe") is not None else "指数 PE 不可得"


def _section_holdings(holdings: dict) -> str:
    if not holdings.get("available", True) or holdings.get("status") != "ok":
        return f'''<section id="holdings">
  <div class="sh"><span class="st">持仓透视</span><div class="sd"></div><span class="ss">R12</span></div>
  <div class="card"><div style="padding:2rem;text-align:center;color:var(--tx-f)">{_esc(holdings.get("note", "持仓数据不可得"))}</div></div>
</section>'''

    rows = holdings.get("rows") or []
    clusters = holdings.get("clusters") or []
    top1 = holdings.get("top1_pct")
    top5 = holdings.get("top5_sum_pct")
    top10 = holdings.get("top10_sum_pct")

    table_rows = ""
    for i, r in enumerate(rows, start=1):
        table_rows += (f'<tr><td>{i}</td><td style="text-align:left">{_esc(r.get("name", ""))}</td>'
                       f'<td style="text-align:left">{_esc(r.get("code", ""))}</td>'
                       f'<td>{_fmt(r.get("pct"))}</td>'
                       f'<td>{_fmt(r.get("shares"))}</td><td>{_fmt_grouped(r.get("amount"))}</td></tr>')
    cluster_li = "".join(
        f'<div class="gr"><div class="gn" style="width:auto;min-width:130px">{_esc(c.get("cluster", "未归类"))}</div>'
        f'<div class="gtrack"><div class="gfill" style="width:{max(2.0, min(100.0, safe_float(c.get("sum_pct")) or 0.0)):.0f}%;background:var(--c1)"></div></div>'
        f'<div class="gval">{_fmt(c.get("sum_pct"))}%</div></div>'
        for c in clusters
    )
    member_notes = [c.get("members") for c in clusters if c.get("members")]
    member_txt = "；".join(
        ", ".join(f"{m.get('name')} {_fmt(m.get('pct'), 1)}%" for m in ms)
        for ms in member_notes if ms
    )

    return f'''<section id="holdings">
  <div class="sh"><span class="st">持仓透视</span><div class="sd"></div><span class="ss">{_esc(holdings.get("report_date", ""))}（{_esc(holdings.get("quarter", ""))}）</span></div>
  <div class="g3">
    {_kpi_card("top1", _fmt(top1) + "%" if top1 is not None else "—", "holdings.top1_pct")}
    {_kpi_card("top5 合计", _fmt(top5) + "%" if top5 is not None else "—", "holdings.top5_sum_pct")}
    {_kpi_card("top10 合计", _fmt(top10) + "%" if top10 is not None else "—", "holdings.top10_sum_pct")}
  </div>
  <div class="g21" style="margin-top:var(--space-4)">
    <div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">前十大持仓（来源: {_esc(holdings.get("source", ""))}）</div>
      <table class="ft"><thead><tr><th>#</th><th>名称</th><th>代码</th><th>占比%</th><th>持股(万股)</th><th>市值(万元)</th></tr></thead>
      <tbody>{table_rows}</tbody></table>
    </div>
    <div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">子环节聚类（引擎 HOLDINGS_CLUSTER_MAP 聚合）</div>
      <div style="display:flex;flex-direction:column">
        {cluster_li or '<div style="padding:1rem;text-align:center;color:var(--tx-f)">无聚类数据</div>'}
      </div>
      {_vnote("未映射股票归入「未归类」；报告 layer 如有补充归类须标注「AI 归类」。成员: " + member_txt) if member_txt else ""}
      {_vnote(_esc(holdings.get("note", "")))}
    </div>
  </div>
</section>'''


def _section_quality(kline: dict, profile: dict) -> str:
    tracking_note = profile.get("tracking_error_note") or kline.get("_error")
    adj = kline.get("adj_applied")
    adj_note = kline.get("adj_note")
    return f'''<section id="quality">
  <div class="sh"><span class="st">跟踪质量</span><div class="sd"></div><span class="ss">NAV {_fmt(kline.get("latest_nav"), 3)} @ {_esc(kline.get("latest_nav_date", ""))}</span></div>
  <div class="card">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">近 {_fmt(kline.get("nav_rows"), 0)} 交易日 NAV 序列（history 链路）</div>
    <div class="cw"><canvas id="navChart"></canvas></div>
    <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>NAV 直接引用引擎序列；引擎未输出 MA 序列故不绘制均线（渲染层不重算）。</div>
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    <div class="g3">
      {_kpi_card("复权状态", str(adj) if adj is not None else "—", str(adj_note or ""))}
      {_kpi_card("年化波动（%）", _fmt(kline.get("volatility_annualized")), "kline.volatility_annualized")}
      {_kpi_card("跟踪误差", "—" if profile.get("tracking_error") is None else _fmt(profile.get("tracking_error"), 2), "引擎未实现；不得填写估算数字")}
    </div>
    {_vnote(tracking_note) if tracking_note else ""}
  </div>
</section>'''


def _section_history(history: dict, events: dict, playbook: dict) -> str:
    hist = history.get("history") or {}
    stats = history.get("stats")
    avail = hist.get("status") == "available" and stats
    if not avail:
        return f'''<section id="history">
  <div class="sh"><span class="st">历史演变</span><div class="sd"></div><span class="ss">R11a</span></div>
  <div class="card"><div style="padding:2rem;text-align:center;color:var(--tx-f)">{_esc(hist.get("error") or "历史行情数据不可得")}</div></div>
</section>'''

    ah = stats.get("annual_high") or {}
    al = stats.get("annual_low") or {}
    md = stats.get("max_drawdown") or {}
    moves = stats.get("big_move_days") or []

    playbook_html = ""
    if playbook.get("available"):
        rows = "".join(
            f'<tr><td>{(_fmt_signed(safe_float(lv.get("level_pct")), 0) + "%") if safe_float(lv.get("level_pct")) is not None else "—"}</td>'
            f'<td>{_esc(str(lv.get("sigma_multiple")) + "σ" if lv.get("sigma_multiple") is not None else "N/A")}</td>'
            f'<td style="text-align:left">{_esc(lv.get("verification_depth", ""))}</td></tr>'
            for lv in playbook.get("drawdown_levels") or []
        )
        checklist = "".join(f'<li>{_esc(s)}</li>' for s in playbook.get("checklist") or [])
        playbook_html = f'''<div class="card" style="margin-top:var(--space-4)" id="playbook">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-2)">情景预案（R11c · σ 回撤档位 → 触发核验深度）</div>
      <div style="font-size:var(--text-xs);color:var(--tx-f);margin-bottom:var(--space-3)">60 日日均波动 {_fmt(playbook.get("vol_60d_daily_pct"))}%（来源: {_esc(playbook.get("vol_source", ""))}）——核验深度筛选规则，非操作阈值</div>
      <table class="ft"><thead><tr><th>回撤档位</th><th>σ 倍数</th><th>触发核验深度</th></tr></thead><tbody>{rows}</tbody></table>
      <ul style="margin-top:var(--space-3);padding-left:var(--space-6);font-size:var(--text-xs);color:var(--tx-m)">{checklist}</ul>
      {_vnote(playbook.get("disclaimer") or "")}
    </div>'''

    events_html = ""
    if events.get("available"):
        aligned = events.get("aligned") or []
        if aligned:
            erows = "".join(
                f'<tr><td>{_esc(r.get("date", ""))}</td><td style="text-align:left">{_esc(r.get("event", ""))}</td>'
                f'<td style="text-align:left">{_esc("；".join(r.get("同日事实") or []))}</td>'
                f'<td style="text-align:left">{_esc(r.get("可能关联（待验证）") or "")}</td></tr>'
                for r in aligned
            )
            events_html = f'''<div class="card" style="margin-top:var(--space-4)">
          <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">事件-价格对照（R11b）</div>
          <table class="ft"><thead><tr><th>日期</th><th>事件</th><th>同日事实</th><th>可能关联（待验证）</th></tr></thead><tbody>{erows}</tbody></table>
        </div>'''
        else:
            events_html = f'<div class="vnote" style="margin-top:var(--space-4)">{_esc(events.get("note") or "事件文件无对照行")}</div>'
    else:
        events_html = f'<div class="vnote" style="margin-top:var(--space-4)">{_esc(events.get("note") or "事件文件不存在，跳过（不阻断）")}</div>'

    return f'''<section id="history">
  <div class="sh"><span class="st">历史演变</span><div class="sd"></div><span class="ss">{_fmt(stats.get("rows"), 0)} 行 · {_esc(stats.get("date_range", ""))} · {_esc(hist.get("source", ""))}</span></div>
  <div class="g4">
    {_kpi_card("年度高点", _fmt(ah.get("close"), 4), _esc(ah.get("date", "")))}
    {_kpi_card("年度低点", _fmt(al.get("close"), 4), _esc(al.get("date", "")))}
    {_kpi_card("最大回撤", _fmt(md.get("drawdown_pct")) + "%", f'{_fmt(md.get("peak_close"), 4)} @ {_esc(md.get("peak_date", ""))} → {_fmt(md.get("trough_close"), 4)} @ {_esc(md.get("trough_date", ""))}')}
    {_kpi_card("当前 vs 高点", _fmt_signed(stats.get("current_vs_high_pct")) + "%", "stats.current_vs_high_pct")}
  </div>
  <div class="g4" style="margin-top:var(--space-4)">
    {_kpi_card("当前 vs 低点", _fmt_signed(stats.get("current_vs_low_pct")) + "%", "stats.current_vs_low_pct")}
    {_kpi_card("|±5%| 交易日", str(stats.get("big_move_days_count", len(moves))), f"上行 {stats.get('big_move_up_days', 0)} / 下行 {stats.get('big_move_down_days', 0)}")}
    {_kpi_card("MA20 / MA60 / MA120", f'{_fmt(stats.get("ma20"))} / {_fmt(stats.get("ma60"))} / {_fmt(stats.get("ma120"))}', "stats.ma*")}
    {_kpi_card("ATR14", _fmt(stats.get("atr14"), 4), (f'{_fmt(stats.get("atr14_pct"), 2)}%' if stats.get("atr14_pct") is not None else ""))}
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">走势与大幅波动日（引擎 big_move_days · |涨跌幅|≥5%）</div>
    <div class="cw"><canvas id="historyChart"></canvas></div>
    <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>NAV 序列与事件清单直接引用引擎字段；阶段划分见「研究备忘」全文。</div>
  </div>
  {playbook_html}
  {events_html}
</section>'''


def _section_flows(sector_flow: dict, peers: dict, share_history: dict, kline: dict) -> str:
    sf_block = ""
    if sector_flow.get("available"):
        rows = "".join(
            f'<tr><td style="text-align:left">{_esc(r.get("industry", ""))}</td>'
            f'<td style="color:{_pos_var(r.get("net_1d"))}">{_fmt_signed(r.get("net_1d"))}</td>'
            f'<td style="color:{_pos_var(r.get("net_3d"))}">{_fmt_signed(r.get("net_3d"))}</td>'
            f'<td style="color:{_pos_var(r.get("net_5d"))}">{_fmt_signed(r.get("net_5d"))}</td>'
            f'<td style="color:{_pos_var(r.get("net_10d"))}">{_fmt_signed(r.get("net_10d"))}</td>'
            f'<td>{_fmt_signed(r.get("chg_10d"))}</td>'
            f'<td style="text-align:left">{_esc(r.get("trend_label", ""))} · {_esc(r.get("trend_detail", ""))}</td>'
            f'<td>{_fmt_signed(r.get("trend_5d"))}</td></tr>'
            for r in sector_flow.get("industries") or []
        )
        sf_block = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">行业资金流趋势（R15 · 同花顺大单口径 · 亿元 · {_esc(sector_flow.get("sw_name", ""))}）</div>
      <table class="ft"><thead><tr><th>行业</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>10日涨跌%</th><th>趋势标签 / 详情</th><th>5日Δ</th></tr></thead><tbody>{rows}</tbody></table>
      <div class="cw" style="margin-top:var(--space-4)"><canvas id="sectorFlowChart"></canvas></div>
      <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>as_of={_esc(sector_flow.get("as_of", ""))} · history_days={_esc(sector_flow.get("history_days", ""))} · 趋势标签为引擎判定的事实描述（证据非信号），不做方向性预测。</div>
    </div>'''
    else:
        sf_block = f'<div class="card"><div style="padding:1.5rem;text-align:center;color:var(--tx-f)">{_esc("；".join(sector_flow.get("notes") or ["行业资金流不可得"]))}</div></div>'

    peers_block = ""
    if peers.get("available"):
        names = peers.get("names") or {}
        prow = ""
        for r in peers.get("flow", {}).get("rows") or []:
            code = r.get("symbol", "")
            prow += (f'<tr><td style="text-align:left">{_esc(names.get(code, code))}</td>'
                     f'<td style="text-align:left">{_esc(code)}</td>'
                     f'<td style="color:{_pos_var(r.get("flow_20d_e"))}">{_fmt_signed(r.get("flow_20d_e"))}</td>'
                     f'<td style="color:{_pos_var(r.get("flow_5d_e"))}">{_fmt_signed(r.get("flow_5d_e"))}</td>'
                     f'<td>{_fmt_signed(r.get("share_change_pct"))}</td>'
                     f'<td style="text-align:left">{_esc(r.get("trend", ""))}</td></tr>')
        rs = peers.get("rs") or {}
        rank = rs.get("rank_20d") or {}
        peer_note = (
            f"{_esc(peers.get('peer_source', ''))} · 窗口 "
            f"{_esc(peers.get('flow', {}).get('window_days', 20))} 日 · "
            "RS 基准=同赛道等权均值，20 日收益排名 "
            f"{_esc(rank.get('rank', '?'))}/{_esc(rank.get('total', '?'))}（20 日窗口）"
        )
        peers_block = f'''<div class="card" style="margin-top:var(--space-4)">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">同赛道资金流对比（R13 · Tushare 份额×均价估算 · 亿元）</div>
      <table class="ft"><thead><tr><th>名称</th><th>代码</th><th>20日流</th><th>5日流</th><th>份额变化%</th><th>趋势</th></tr></thead><tbody>{prow}</tbody></table>
      <div class="g3" style="margin-top:var(--space-3)">
        {_ipill("RS 最新", _fmt(rs.get("rs_latest"), 1), "101 = 与基准持平", "sig-neutral", _pos_var(rs.get("rs_change")) if rs.get("rs_change") else "")}
        {_ipill("RS 窗口起点", _fmt(rs.get("rs_window_start"), 1), f"n={_esc(rs.get('n', ''))}", "sig-neutral")}
        {_ipill("RS 20日变化", _fmt_signed(rs.get("rs_change"), 1), "相对强弱状态参考，非信号", "sig-neutral", _pos_var(rs.get("rs_change")))}
      </div>
      {_vnote(peer_note)}
    </div>'''
    else:
        peers_block = f'<div class="card" style="margin-top:var(--space-4)"><div style="padding:1.5rem;text-align:center;color:var(--tx-f)">{_esc("；".join(peers.get("notes") or ["同赛道对比不可得"]))}</div></div>'

    sh_row = ""
    if share_history.get("available"):
        s = share_history.get("summary") or {}
        sh_row = (f'<div class="g4" style="margin-top:var(--space-4)">'
                  f'{_kpi_card("份额流合计（亿）", _fmt_signed(s.get("total_flow_est"), 2), f"{_esc(s.get('trend', ''))}（引擎趋势标签）", _pos_var(s.get("total_flow_est")))}'
                  f'{_kpi_card("份额变化（万份）", _fmt_grouped(s.get("share_total_change")), "share_history.summary")}'
                  f'{_kpi_card("流入/流出天数", f"{s.get('inflow_days', '—')} / {s.get('outflow_days', '—')}", f"近端{s.get('recent_flow_days', '—')}日 {_fmt_signed(s.get('recent_flow_est'), 2)} 亿")}'
                  f'{_kpi_card("日均成交（亿）", _fmt(s.get("avg_amount_e")), f"峰值 {_fmt(s.get("max_amount_e"))}")}'
                  f'</div>')
    shares_avail = share_history.get("available") and bool(share_history.get("rows"))

    return f'''<section id="flows">
  <div class="sh"><span class="st">资金流向与趋势</span><div class="sd"></div><span class="ss">R13 / R15 · 大单口径 + 份额口径</span></div>
  {sf_block}
  {sh_row if sh_row else ""}
  <div class="card" style="margin-top:var(--space-4)">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">份额资金流与收盘价（近 20 日 · Tushare fund_share）</div>
    <div class="cw"><canvas id="shareFlowChart"></canvas></div>
    {'<div class="vnote">近 20 日份额流 + 收盘价；T+1 延迟，最新 1-2 日字段可能为空。</div>' if shares_avail else ''}
  </div>
  {peers_block}
</section>'''


def _section_research(md_html: str) -> str:
    return f'''<section id="research">
  <div class="sh"><span class="st">研究备忘（md 全文原样嵌入）</span><div class="sd"></div><span class="ss">已通过三层复检的合规 artifact</span></div>
  <div class="card">
    <article class="md-body">
      {md_html}
    </article>
  </div>
</section>'''


def _section_refs(payload: dict, events: dict, playbook: dict) -> str:
    report = payload
    kline = report.get("kline") or {}
    share_history = report.get("share_history") or {}
    history = report.get("history") or {}
    holdings = payload.get("holdings") or {}
    peers = payload.get("peers") or {}
    sector_flow = payload.get("sector_flow") or {}

    rows = [
        ("产品数据", "etf_data.query_etf_data", bool(report.get("profile"))),
        ("行情报价", "etf_data.query_etf_quote", quote_ok(report.get("quote"))),
        ("净值/技术", "etf_data.query_etf_kline", bool(kline.get("latest_nav"))),
        ("份额资金流", "etf_data.query_etf_share_history", bool(share_history.get("available"))),
        ("历史深度", "etf_data.query_etf_kline_history", bool((history.get("history") or {}).get("status") == "available")),
        ("持仓透视", "etf_data.query_etf_holdings", bool(holdings.get("status") == "ok")),
        ("赛道对比", "etf_peers.query_etf_peers", bool(peers.get("available"))),
        ("行业资金流", "sector_flow.query_sector_flow", bool(sector_flow.get("available"))),
        ("事件文件", "etf_timeline.load_events_file", bool(events.get("available"))),
        ("情景预案", "etf_playbook.drawdown_levels", bool(playbook.get("available"))),
    ]
    tr_html = "".join(
        f'<tr><td>{_esc(d)}</td><td><code>{_esc(api)}</code></td>'
        f'<td><span class="{"ref-ok" if ok else "ref-err"}">{"✓ 有数据" if ok else "✗ 不可得"}</span></td></tr>'
        for d, api, ok in rows
    )
    return f'''<section id="refs">
  <div class="sh"><span class="st">数据来源</span><div class="sd"></div><span class="ss">引擎调用路径 · 可追溯</span></div>
  <div class="rtog" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.ra').textContent=this.nextElementSibling.classList.contains('open')?'▴':'▾'">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
    展开数据追溯表<span class="ra" style="margin-left:auto">▾</span>
  </div>
  <div class="rbody">
    <div class="card" style="margin-top:var(--space-3)">
      <table>
        <thead><tr>
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">维度</td>
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">接口</td>
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">状态</td>
        </tr></thead>
        <tbody>{tr_html}</tbody>
      </table>
    </div>
  </div>
</section>'''


def quote_ok(quote) -> bool:
    return bool(quote and quote.get("status") == "available")


def _html_risk_banner() -> str:
    return (
        '<div class="disc" style="margin-bottom:var(--space-4);border-left:3px solid var(--wn)">'
        '<strong>⚠ 风险提示</strong> — 本报告由 invest-a-etf 自动化引擎生成，'
        '仅供学习研究参考，<strong>不构成任何投资建议、买卖指令或目标价预测</strong>。'
        '</div>'
    )


def _html_disclaimer() -> str:
    return (
        '<div class="disc"><strong>⚠ 免责声明</strong> — 本报告由 invest-a-etf 自动化引擎生成，'
        '仅供学习研究参考，<strong>不构成任何投资建议、买卖指令或目标价预测</strong>。'
        '所有技术指标均为市场状态描述，非交易信号；情景参考价带为分析情景权重，'
        '假设≠预测（见「研究备忘」多情景参考节）。'
        '数据来源见上文 References 表，可能与实际公告存在差异，请以交易所公告为准。'
        '</div>'
    )


# ── JS：数据注入 + renderCharts（主题切换 destroy/rebuild） ──

_HTML_APP_SCRIPT_LOGIC = r"""
// theme
(function(){
  const btn=document.querySelector('[data-theme-toggle]'),html=document.documentElement;
  let t='dark';html.setAttribute('data-theme',t);
  btn&&btn.addEventListener('click',()=>{
    t=t==='dark'?'light':'dark';html.setAttribute('data-theme',t);
    btn.innerHTML=t==='dark'?'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>':'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';
    renderCharts();
  });
})();

// sidebar active（click + scroll 跟随）
document.querySelectorAll('.sbi').forEach(el=>el.addEventListener('click',()=>{
  document.querySelectorAll('.sbi').forEach(e=>e.classList.remove('active'));
  el.classList.add('active');
}));
(function(){
  if(!window.IntersectionObserver)return;
  const spy=new IntersectionObserver(es=>{
    es.forEach(en=>{
      if(!en.isIntersecting)return;
      const a=document.querySelector('.sbi[href="#'+en.target.id+'"]');
      if(!a)return;
      document.querySelectorAll('.sbi').forEach(e=>e.classList.remove('active'));
      a.classList.add('active');
    });
  },{rootMargin:'-15% 0px -75% 0px'});
  document.querySelectorAll('section[id]').forEach(s=>spy.observe(s));
})();

// charts
let charts={};
function renderCharts(){
  Object.values(charts).forEach(c=>c.destroy());charts={};
  if(typeof Chart==='undefined')return;
  const isDark=document.documentElement.getAttribute('data-theme')!=='light';
  const tc=isDark?'#8892a4':'#6b7a99',gc=isDark?'rgba(255,255,255,.06)':'rgba(0,0,0,.06)';
  // 轴刻度用更高对比亮色（深色主题下刻度密度高，需比 tooltip 文字更亮一档）
  const tcAxis=isDark?'#a9b4c7':'#526180';
  const tt={backgroundColor:isDark?'#1c2128':'#fff',titleColor:isDark?'#e2e8f0':'#1a2030',bodyColor:tc,borderColor:isDark?'rgba(255,255,255,.1)':'rgba(0,0,0,.1)',borderWidth:1};
  // A 股惯例：涨=红 跌=绿
  const upBg=isDark?'rgba(248,113,113,.6)':'rgba(220,38,38,.6)',dnBg=isDark?'rgba(52,211,153,.6)':'rgba(5,150,105,.6)';
  const upBd=isDark?'#f87171':'#dc2626',dnBd=isDark?'#34d399':'#059669';
  const xs={ticks:{color:tcAxis,autoSkip:true,maxTicksLimit:10,font:{family:'IBM Plex Mono',size:11}},grid:{color:'transparent'}};
  const ys={ticks:{color:tcAxis,font:{family:'IBM Plex Mono',size:11}},grid:{color:gc}};
  const hoverCur=(evt,items)=>{if(evt&&evt.native){evt.native.target.style.cursor=items&&items.length?'pointer':'default';}};
  const byDate=(a,b)=>a.date<b.date?-1:1;

  // NAV 250d
  const hist=report.history&&report.history.history&&report.history.history.rows||[];
  if(hist.length>0){
    const rows=[...hist].sort(byDate);
    charts.nav=new Chart(document.getElementById('navChart'),{type:'line',data:{labels:rows.map(r=>r.date),datasets:[{label:'NAV',data:rows.map(r=>r.nav),borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.12)',fill:true,tension:.25,pointRadius:0,borderWidth:1.5}]},options:{responsive:true,maintainAspectRatio:false,onHover:hoverCur,plugins:{legend:{display:false},tooltip:{...tt,mode:'index',intersect:false}},scales:{x:xs,y:ys}}});
  }

  // big move days bars
  const mv=report.history&&report.history.stats&&report.history.stats.big_move_days||[];
  if(mv.length>0){
    charts.mv=new Chart(document.getElementById('historyChart'),{type:'bar',data:{labels:mv.map(r=>r.date),datasets:[{label:'|涨跌幅|≥5%',data:mv.map(r=>r.change_pct),backgroundColor:mv.map(r=>r.change_pct>0?upBg:dnBg),borderColor:mv.map(r=>r.change_pct>0?upBd:dnBd),borderWidth:1,borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,onHover:hoverCur,plugins:{legend:{display:false},tooltip:{...tt}},scales:{x:xs,y:ys}}});
  }

  // share flow dual-axis
  const shRows=(report.share_history&&report.share_history.rows)||[];
  const sh=shRows.filter(r=>r.flow_est!=null);
  if(sh.length>0){
    charts.shareFlow=new Chart(document.getElementById('shareFlowChart'),{type:'bar',data:{labels:sh.map(r=>r.date),datasets:[
      {type:'bar',label:'份额净流入(亿)',data:sh.map(r=>r.flow_est),backgroundColor:sh.map(r=>r.flow_est>0?upBg:dnBg),borderColor:sh.map(r=>r.flow_est>0?upBd:dnBd),borderWidth:1,borderRadius:3,yAxisID:'yFlow',order:2},
      {type:'line',label:'收盘价',data:sh.map(r=>r.close),borderColor:isDark?'rgba(226,232,240,.9)':'rgba(30,40,60,.9)',borderWidth:1.5,pointRadius:3,pointBackgroundColor:isDark?'#e2e8f0':'#1e2840',tension:.3,yAxisID:'yPrice',order:1}
    ]},options:{responsive:true,maintainAspectRatio:false,onHover:hoverCur,interaction:{mode:'index',intersect:false},plugins:{legend:{display:true,position:'top',labels:{color:tc,font:{size:11},boxWidth:10,padding:10}},tooltip:{...tt,callbacks:{label:ctx=>{if(ctx.datasetIndex===0)return ' 净流入: '+(ctx.raw>0?'+':'')+ctx.raw.toFixed(2)+'亿';return ' 收盘价: '+ctx.raw;}}}},scales:{x:{...xs,grid:{color:'transparent'}},yFlow:{...ys,position:'left',title:{display:true,text:'净流入(亿)',color:tc,font:{size:10,family:'IBM Plex Mono'}}},yPrice:{position:'right',grid:{color:'transparent'},ticks:{color:tcAxis,font:{family:'IBM Plex Mono',size:11}},title:{display:true,text:'收盘价',color:tc,font:{size:10,family:'IBM Plex Mono'}}}}}});
  }

  // sector flow grouped bars
  const inds=(report.sector_flow&&report.sector_flow.industries)||[];
  if(inds.length>0){
    const cols=['#38bdf8','#818cf8','#34d399'];
    charts.sf=new Chart(document.getElementById('sectorFlowChart'),{type:'bar',data:{labels:inds.map(r=>r.industry),datasets:[
      {label:'1日',data:inds.map(r=>r.net_1d),backgroundColor:'rgba(56,189,248,.55)',borderColor:'#38bdf8',borderWidth:1,borderRadius:3},
      {label:'5日',data:inds.map(r=>r.net_5d),backgroundColor:'rgba(129,140,248,.55)',borderColor:'#818cf8',borderWidth:1,borderRadius:3},
      {label:'10日',data:inds.map(r=>r.net_10d),backgroundColor:'rgba(52,211,153,.55)',borderColor:'#34d399',borderWidth:1,borderRadius:3}
    ]},options:{responsive:true,maintainAspectRatio:false,onHover:hoverCur,plugins:{legend:{display:true,position:'top',labels:{color:tc,font:{size:11},boxWidth:10,padding:10}},tooltip:{...tt}},scales:{x:xs,y:ys}}});
  }

}


window.addEventListener('load',renderCharts);
"""


def _build_data_js(payload: dict) -> str:
    """报告 JSON 注入（</ 转义防 script 截断；数据行与逻辑分离）。"""
    payload_json = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    return (
        "// data\nconst report=" + payload_json + ";\n"
        + _HTML_APP_SCRIPT_LOGIC
    )


# ── 主入口 ──


def render_etf_html(payload: dict[str, Any], *, md_text: str | None = None) -> str:
    """生成 invest-a-etf HTML 报告（仪表盘 + md 全文原样嵌入）。

    Args:
        payload: report 形状的 dict（profile/quote/kline/share_history/history/events/
            playbook + 可选 holdings/peers/sector_flow 合并键）。
        md_text: 三层复检后的 md 研究备忘全文（原样嵌入；不支持语法 raise
            MarkdownSubsetError——由调用方决定退出码）。
    """
    profile = payload.get("profile") or {}
    quote = payload.get("quote") or {}
    kline = payload.get("kline") or {}
    history = payload.get("history") or {}
    events = payload.get("events") or {}
    playbook = payload.get("playbook") or {}

    symbol = str(payload.get("symbol", ""))
    name = profile.get("category", {}).get("label", "") if isinstance(profile.get("category"), dict) else ""
    if not name:
        name = profile.get("name") or ""

    price = safe_float(quote.get("price"))
    price_str = _fmt(price, 3) if price is not None else "—"
    chg = safe_float(quote.get("change_pct"))
    chg_str = _fmt_signed(chg) + "%" if chg is not None else "—"
    price_color = _pos_var(chg) if chg is not None else "var(--tx)"
    chg_color = "var(--fall)" if (chg is not None and chg < 0) else ("var(--rise)" if (chg is not None and chg > 0) else "var(--tx-m)")

    md_html = render_markdown(md_text) if md_text else ""

    topbar = _html_topbar(symbol, name, price_str, chg_str, price_color, chg_color)
    sidebar = _html_sidebar()
    risk_banner = _html_risk_banner()
    disclaimer = _html_disclaimer()

    sections = [
        _section_overview(profile, quote, kline),
        _section_valuation(profile),
        _section_holdings(payload.get("holdings") or {"available": False, "note": "持仓数据未采集（可运行 etf.py holdings）"}),
        _section_quality(kline, profile),
        _section_history(history, events, playbook),
        _section_flows(payload.get("sector_flow") or {"available": False, "notes": []},
                       payload.get("peers") or {"available": False, "notes": []},
                       payload.get("share_history") or {},
                       kline),
        _section_research(md_html),
        _section_refs(payload, events, playbook),
    ]

    chart_js = _load_chart_js()
    data_js = _build_data_js(payload)

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(f"{symbol} {name}")} — invest:a-etf 研究报告</title>
<style>
{_HTML_CSS}
</style>
</head>
<body>
<div class="app">
{topbar}
{sidebar}
<main class="main">
{risk_banner}
{sections[0]}
{sections[1]}
{sections[2]}
{sections[3]}
{sections[4]}
{sections[5]}
{sections[6]}
{sections[7]}
{disclaimer}
</main>
</div>

<script>
{chart_js}
</script>
<script>
{data_js}
</script>
</body>
</html>"""
