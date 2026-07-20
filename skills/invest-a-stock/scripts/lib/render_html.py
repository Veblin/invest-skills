"""HTML report rendering."""
from __future__ import annotations

import html as _html_mod
import json
import logging
import re
from pathlib import Path
from typing import Any

from lib.nums import coalesce_field, fmt_amount, safe_float as _safe_num
from lib.technical import compute, sort_kline_asc

from . import render_utils as _u
from .render_utils import (
    ENGINE_VERSION,
    _data_fields,
    _fmt_v2,
    _get_dim_data,
    _get_dim_meta,
    _index_dims,
    sanitize_error,
)
from .version import get_package_version

logger = logging.getLogger(__name__)

_CHART_JS_CACHE: str | None = None

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
.ss{font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono)}
.sd{flex:1;height:1px;background:var(--bdr)}

/* card */
.card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-lg);padding:var(--space-5);box-shadow:var(--sh)}
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
.gn{font-size:var(--text-xs);color:var(--tx-m);width:56px;flex-shrink:0}
.gtrack{flex:1;height:6px;background:var(--sur3);border-radius:3px;overflow:visible;position:relative}
.gfill{height:6px;border-radius:3px;position:relative;transition:width 1s cubic-bezier(.16,1,.3,1)}
.gmk{position:absolute;right:-3px;top:-3px;width:12px;height:12px;border-radius:50%;border:2px solid var(--sur)}
.gval{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx);width:64px;text-align:right;flex-shrink:0}
.gpct{font-family:var(--font-mono);font-size:var(--text-xs);width:44px;text-align:right;flex-shrink:0}

/* indicator pill */
.ipill{background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-md);padding:var(--space-3)}
.iname{font-size:var(--text-xs);color:var(--tx-f);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-1)}
.ival{font-family:var(--font-mono);font-size:var(--text-base);font-weight:600}
.isig{font-size:var(--text-xs);margin-top:var(--space-1)}
.sig-bear{color:var(--dn)}.sig-bull{color:var(--up)}.sig-neutral{color:var(--wn)}

/* fin table */
.ft th{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--tx-f);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi);text-align:right}
.ft th:first-child{text-align:left}
.ft td{font-family:var(--font-mono);font-size:var(--text-xs);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr);text-align:right;color:var(--tx-m)}
.ft td:first-child{text-align:left;color:var(--tx-f)}
.ft tr:last-child td{border-bottom:none;font-weight:600;color:var(--tx)}
.roe-hi{color:var(--up)!important}.roe-lo{color:var(--wn)!important}

/* flow */
.flr{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--bdr)}
.flr:last-child{border-bottom:none}
.fldate{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-f);width:48px}
.flbar{height:6px;border-radius:3px;min-width:2px}
.fl-in{background:var(--up)}.fl-out{background:var(--dn)}
.flval{font-family:var(--font-mono);font-size:var(--text-xs);width:64px;text-align:right}
.fp{color:var(--up)}.fn{color:var(--dn)}

/* holder */
.hlr{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--bdr)}
.hlr:last-child{border-bottom:none}
.hlrk{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-f);width:16px;text-align:right;flex-shrink:0}
.hln{flex:1;min-width:0}
.hlname{font-size:var(--text-xs);color:var(--tx-m);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hlbar{height:3px;border-radius:2px;background:var(--ac);margin-top:3px;transition:width .8s cubic-bezier(.16,1,.3,1)}
.hlpct{font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600;flex-shrink:0}

/* ref */
.rtog{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-3) var(--space-4);background:var(--sur2);border-radius:var(--r-md);cursor:pointer;font-size:var(--text-xs);color:var(--tx-m);border:1px solid var(--bdr);user-select:none;transition:background var(--trans)}
.rtog:hover{background:var(--sur3)}
.rbody{display:none;margin-top:var(--space-3)}
.rbody.open{display:block}
.ref-ok{color:var(--up)}.ref-err{color:var(--dn)}
code{font-family:var(--font-mono);font-size:.85em;background:var(--sur3);padding:1px 5px;border-radius:var(--r-sm);color:var(--tx-m)}

/* verify */
.vnote{display:flex;align-items:flex-start;gap:var(--space-2);padding:var(--space-2) var(--space-3);background:var(--wn-d);border-radius:var(--r-sm);border-left:2px solid var(--wn);font-size:var(--text-xs);color:var(--tx-m);margin-top:var(--space-3)}

/* pending */
.pend{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:var(--space-3);padding:var(--space-10);background:var(--sur2);border-radius:var(--r-md);border:1px dashed var(--bdr-hi);text-align:center}
.pend svg{width:36px;height:36px;color:var(--tx-f)}
.pend-t{font-size:var(--text-sm);font-weight:600;color:var(--tx-m)}
.pend-d{font-size:var(--text-xs);color:var(--tx-f);max-width:32ch}

/* disclaimer */
.disc{font-size:var(--text-xs);color:var(--tx-f);padding:var(--space-4);background:var(--sur2);border-radius:var(--r-md);border:1px solid var(--bdr);line-height:1.8}
.disc strong{color:var(--wn)}

/* chart */
.cw{position:relative;height:220px}
.cw-sm{position:relative;height:160px}

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

// sidebar active
document.querySelectorAll('.sbi').forEach(el=>el.addEventListener('click',()=>{
  document.querySelectorAll('.sbi').forEach(e=>e.classList.remove('active'));
  el.classList.add('active');
}));

// trend label
const tl=document.getElementById('maTrendLabel');
if(tl&&trendLabel) tl.textContent=trendLabel;

// charts
let charts={};
function renderCharts(){
  Object.values(charts).forEach(c=>c.destroy());charts={};
  const isDark=document.documentElement.getAttribute('data-theme')!=='light';
  const tc=isDark?'#8892a4':'#6b7a99',gc=isDark?'rgba(255,255,255,.06)':'rgba(0,0,0,.06)';
  const tt={backgroundColor:isDark?'#1c2128':'#fff',titleColor:isDark?'#e2e8f0':'#1a2030',bodyColor:tc,borderColor:isDark?'rgba(255,255,255,.1)':'rgba(0,0,0,.1)',borderWidth:1};
  const xs={ticks:{color:tc,font:{family:'IBM Plex Mono',size:10}},grid:{color:'transparent'}};
  const ys={ticks:{color:tc,font:{family:'IBM Plex Mono',size:10}},grid:{color:gc}};

  if(finLabels.length>0){
    charts.roe=new Chart(document.getElementById('roeChart'),{type:'line',data:{labels:finLabels,datasets:[{label:'ROE(%)',data:roeData,borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.15)',fill:true,tension:.35,pointRadius:4,pointBackgroundColor:'#38bdf8'},{label:'EPS(元)',data:epsData,borderColor:'#818cf8',borderDash:[4,4],tension:.35,pointRadius:3,pointBackgroundColor:'#818cf8',yAxisID:'y2'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'top',labels:{color:tc,font:{size:11},boxWidth:10,padding:10}},tooltip:{...tt,mode:'index',intersect:false}},scales:{x:xs,y:ys,y2:{position:'right',ticks:{color:'#818cf8',font:{family:'IBM Plex Mono',size:10}},grid:{color:'transparent'}}}}});
    const pVals=profitData.filter(v=>v!=null);
    const pAvg=pVals.length?pVals.reduce((a,b)=>a+b,0)/pVals.length:0;
    charts.profit=new Chart(document.getElementById('profitChart'),{type:'bar',data:{labels:finLabels,datasets:[{data:profitData,backgroundColor:profitData.map(v=>v==null?'rgba(128,128,128,.3)':(v<pAvg?'rgba(248,113,113,.5)':'rgba(52,211,153,.5)')),borderColor:profitData.map(v=>v==null?'#666':(v<pAvg?'#f87171':'#34d399')),borderWidth:1,borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{...tt}},scales:{x:xs,y:ys}}});
  }

  if(flowData.length>0){
    const fLabels=flowData.map(d=>d[0]);
    const fVals=flowData.map(d=>Math.round(d[1]/10000*100)/100);
    const fClose=closePriceSeries.slice(-flowData.length);
    charts.flow=new Chart(document.getElementById('flowChart'),{
      type:'bar',
      data:{labels:fLabels,datasets:[
        {type:'bar',label:'日净流向(万)',data:fVals,backgroundColor:fVals.map(v=>v>0?'rgba(52,211,153,0.75)':'rgba(248,113,113,0.75)'),borderColor:fVals.map(v=>v>0?'#34d399':'#f87171'),borderWidth:1,borderRadius:3,yAxisID:'yFlow',order:2},
        {type:'line',label:'收盘价',data:fClose,borderColor:isDark?'rgba(226,232,240,0.9)':'rgba(30,40,60,0.9)',borderWidth:1.5,pointRadius:3,pointBackgroundColor:isDark?'#e2e8f0':'#1e2840',tension:.3,yAxisID:'yPrice',order:1}
      ]},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{...tt,callbacks:{label:ctx=>{if(ctx.datasetIndex===0)return ' 净流向: '+(ctx.raw>0?'+':'')+ctx.raw.toFixed(2)+'万';return ' 收盘价: '+ctx.raw+'元';}}}},scales:{x:{...xs,grid:{color:'transparent'},ticks:{maxRotation:0}},yFlow:{...ys,position:'left',title:{display:true,text:'净流向(万)',color:tc,font:{size:10,family:'IBM Plex Mono'}}},yPrice:{position:'right',grid:{color:'transparent'},ticks:{color:tc,font:{family:'IBM Plex Mono',size:10}},title:{display:true,text:'收盘价(元)',color:tc,font:{size:10,family:'IBM Plex Mono'}}}}}
    });
  }
}

window.addEventListener('load',renderCharts);
"""


def _lazy_section_research_summary(*args, **kwargs):
    from .render_markdown import _section_research_summary
    return _section_research_summary(*args, **kwargs)



# --- _load_chart_js ---
def _load_chart_js() -> str:
    """读取本地 chart.umd.min.js。离线可用，避免 CDN 依赖。

    优先从本地资产目录读取；回退为空字符串（图表不渲染，其余内容正常）。
    """
    global _CHART_JS_CACHE
    if _CHART_JS_CACHE is not None:
        return _CHART_JS_CACHE

    p = Path(__file__).resolve().parent / "assets" / "chart.umd.min.js"
    try:
        _CHART_JS_CACHE = p.read_text(encoding="utf-8")
        return _CHART_JS_CACHE
    except Exception:
        _CHART_JS_CACHE = ""
        return ""


# --- _html_topbar ---
def _html_topbar(
    symbol: str, name: str, price_str: str, change_str: str,
    price_color: str, chg_color: str, summary: dict,
) -> str:
    av = summary.get("available", 0)
    total = summary.get("total", 0)
    deg = summary.get("degraded", 0)
    badge_cls = "b-ok" if av >= total * 0.5 else "b-wn"
    badge_text = f"{av}/{total} 维度" + (f"（{deg} 降级）" if deg else "")
    ver_badge = f"v{ENGINE_VERSION}"
    return f'''<header class="topbar">
  <div class="tl">
    <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
      <rect x="1.5" y="1.5" width="19" height="19" rx="4" stroke="currentColor" stroke-width="1.5"/>
      <path d="M7 15.5L11 7L15 15.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M8.8 12.5H13.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
    invest:a-stock
  </div>
  <div class="td"></div>
  <span class="tn">{_html_mod.escape(name or symbol)}</span>
  <span class="tc">{_html_mod.escape(symbol)}</span>
  <span class="tp" style="color:{price_color}">{price_str}</span>
  <span class="tch" style="color:{chg_color};background:{chg_color.replace("var(--up)","var(--up-d)").replace("var(--dn)","var(--dn-d)")}">{change_str}</span>
  <span class="badge {badge_cls}">{badge_text}</span>
  <span class="badge b-ok">{ver_badge}</span>
  <button class="tbtn" data-theme-toggle aria-label="切换主题">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
  </button>
</header>'''


# --- _html_sidebar ---
def _html_sidebar() -> str:
    return '''<nav class="sidebar">
  <div class="sbl">概览</div>
  <a class="sbi active" href="#overview"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>行情快照</a>
  <a class="sbi" href="#valuation"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>估值分析</a>
  <div class="sbl">财务</div>
  <a class="sbi" href="#financials"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>财务指标</a>
  <div class="sbl">市场</div>
  <a class="sbi" href="#technicals"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 17 9 11 13 15 21 7"/></svg>技术指标</a>
  <a class="sbi" href="#northbound"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M2 12l10-10 10 10"/></svg>北向资金</a>
  <a class="sbi" href="#holders"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>股东结构</a>
  <div class="sbl">分析</div>
  <a class="sbi" href="#events"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>事件 &amp; 综合</a>
  <a class="sbi" href="#refs"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>数据来源</a>
</nav>'''


# --- _html_overview ---
def _html_overview(
    price_str: str, change_str: str, price_color: str, chg_color: str,
    volume_str: str, turover_str: str, atr_str: str, vol5d_str: str,
    dv_str: str, ma250_str: str, ma250_pos: str, kline_days: int,
) -> str:
    # 默认值
    price_str = price_str or "--"
    change_str = change_str or "--"
    volume_str = volume_str or "--"
    turover_str = turover_str or "--"
    atr_str = atr_str or "--"
    vol5d_str = vol5d_str or "--"
    dv_str = dv_str or "--"
    ma250_str = ma250_str or "--"
    ma250_color = "var(--up)" if "上方" in ma250_pos else ("var(--dn)" if "下方" in ma250_pos else "var(--tx)")
    return f'''<section id="overview">
  <div class="sh"><span class="st">行情快照</span><div class="sd"></div><span class="ss">交易日 {kline_days}d</span></div>
  <div class="g4">
    <div class="card card-sm"><div class="kl">最新价</div><div class="kv" style="color:{price_color}">{price_str}</div><div class="ks">较昨收 {change_str}</div></div>
    <div class="card card-sm"><div class="kl">换手率</div><div class="kv">{turover_str}</div><div class="ks">ATR(14) = {atr_str}</div></div>
    <div class="card card-sm"><div class="kl">近5日均量</div><div class="kv" style="font-size:var(--text-lg)">{volume_str}</div><div class="ks">MA250 = {ma250_str} <span style="color:{ma250_color}">{ma250_pos}</span></div></div>
    <div class="card card-sm"><div class="kl">股息率</div><div class="kv">{dv_str.split("%")[0] if "%" in dv_str else dv_str}%</div><div class="ks">dv_ratio 最近交易日</div></div>
  </div>
</section>'''


# --- _html_valuation ---
def _html_valuation(
    pe_pct: str, pe_val: str, pe_color: str,
    pb_pct: str, pb_val: str, pb_color: str,
    ps_pct: str, ps_val: str, ps_color: str,
    pe_median: str, pb_median: str, zone_signal: str, zone_color: str,
    n_samples: int, window_label: str,
    pe_above_median: bool, pb_above_median: bool,
) -> str:
    if not pe_val or pe_val == "--":
        return f'''<section id="valuation">
  <div class="sh"><span class="st">估值分析</span><div class="sd"></div><span class="ss">数据不可得</span></div>
  <div class="card" style="padding:var(--space-10);text-align:center">
    <div style="font-size:var(--text-sm);color:var(--tx-f)">估值维度无数据，请配置 Tushare Token 获取历史估值序列。</div>
  </div>
</section>'''
    pe_pct_s = "0" if pe_pct is None else str(pe_pct)
    pb_pct_s = "0" if pb_pct is None else str(pb_pct)
    ps_pct_s = "0" if ps_pct is None else str(ps_pct)
    pe_v = "0" if pe_val is None else str(pe_val)
    pb_v = "0" if pb_val is None else str(pb_val)
    ps_v = "0" if ps_val is None else str(ps_val)

    pe_med_str = f"{pe_median}x" if pe_median and pe_median != "--" else "--"
    pb_med_str = f"{pb_median}x" if pb_median and pb_median != "--" else "--"

    pe_below = "当前低于中位数" if not pe_above_median else "当前高于中位数"
    pb_below = "当前低于中位数" if not pb_above_median else "当前高于中位数"

    return f'''<section id="valuation">
  <div class="sh"><span class="st">估值分析</span><div class="sd"></div><span class="ss">{window_label}分位 · {n_samples}交易日</span></div>
  <div class="g21">
    <div class="card">
      <div style="font-size:var(--text-xs);color:var(--tx-f);margin-bottom:var(--space-4)">分位越低代表估值越便宜（相对{window_label}）</div>
      <div class="gr">
        <div class="gn">PE(TTM)</div>
        <div class="gtrack"><div class="gfill" style="width:{pe_pct_s}%;background:var(--c1)"><div class="gmk" style="background:var(--c1)"></div></div></div>
        <div class="gval">{pe_v}</div><div class="gpct" style="color:var(--c1)">{pe_pct_s}%</div>
      </div>
      <div class="gr">
        <div class="gn">PB</div>
        <div class="gtrack"><div class="gfill" style="width:{pb_pct_s}%;background:var(--c2)"><div class="gmk" style="background:var(--c2)"></div></div></div>
        <div class="gval">{pb_v}</div><div class="gpct" style="color:var(--c2)">{pb_pct_s}%</div>
      </div>
      <div class="gr">
        <div class="gn">PS(TTM)</div>
        <div class="gtrack"><div class="gfill" style="width:{ps_pct_s}%;background:var(--wn)"><div class="gmk" style="background:var(--wn)"></div></div></div>
        <div class="gval">{ps_v}</div><div class="gpct" style="color:var(--wn)">{ps_pct_s}%</div>
      </div>
      <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>PE 亏损期已剔除；行业相对估值 v0.1.2 未覆盖，分位不构成买卖判断。</div>
    </div>
    <div class="card">
      <div style="font-size:var(--text-xs);color:var(--tx-f);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-4)">历史中位数</div>
      <div style="display:flex;flex-direction:column;gap:var(--space-5)">
        <div><div class="kl">PE 中位数</div><div style="font-family:var(--font-mono);font-size:var(--text-lg);font-weight:600">{pe_med_str}</div><div class="ks">{pe_below}</div></div>
        <div><div class="kl">PB 中位数</div><div style="font-family:var(--font-mono);font-size:var(--text-lg);font-weight:600">{pb_med_str}</div><div class="ks">{pb_below}</div></div>
        <div><div class="kl">综合信号</div><div style="font-size:var(--text-base);font-weight:600;color:{zone_color}">{zone_signal}</div></div>
      </div>
    </div>
  </div>
</section>'''


# --- _html_financials ---
def _html_financials(fin_table_html: str, fin_note: str) -> str:
    return f'''<section id="financials">
  <div class="sh"><span class="st">财务指标</span><div class="sd"></div><span class="ss">近8期季报</span></div>
  <div class="g2">
    <div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">ROE / EPS 趋势</div>
      <div class="cw"><canvas id="roeChart"></canvas></div>
    </div>
    <div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">扣非净利润（亿元）</div>
      <div class="cw"><canvas id="profitChart"></canvas></div>
    </div>
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    {fin_table_html}
    <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>{_html_mod.escape(fin_note)}</div>
  </div>
</section>'''


# --- _html_technicals ---
def _html_technicals(
    macd_html: str, rsi_kdj_html: str, boll_html: str, ma_grid_html: str,
    tech_note: str, tech_source: str,
) -> str:
    return f'''<section id="technicals">
  <div class="sh"><span class="st">技术指标</span><div class="sd"></div><span class="ss">{_html_mod.escape(tech_source)}</span></div>
  <div class="g3">
    {macd_html}
    {rsi_kdj_html}
    {boll_html}
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">均线排列 <span style="font-size:var(--text-xs);font-weight:400;margin-left:var(--space-2)" id="maTrendLabel"></span></div>
    {ma_grid_html}
  </div>
</section>'''


# --- _html_northbound ---
def _html_northbound(nb_html: str) -> str:
    return f'''<section id="northbound">
  <div class="sh"><span class="st">北向资金</span><div class="sd"></div><span class="ss">近7日净流向 · moneyflow（估算值）</span></div>
  <div class="card">
    {nb_html}
  </div>
</section>'''


# --- _html_holders ---
def _html_holders(holders_html: str) -> str:
    return f'''<section id="holders">
  <div class="sh"><span class="st">股东结构</span><div class="sd"></div><span class="ss">前十大流通股东 · 最新报告期</span></div>
  <div class="card">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">持股比例</div>
    {holders_html}
    <div class="vnote" style="margin-top:var(--space-3)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>报告期数据约有1季度滞后，以公司公告为准。</div>
  </div>
</section>'''


# --- _html_research ---
def _html_research(research_md: str) -> str:
    # research_md already rendered by caller
    """机构观点 HTML 段；无数据时返回空字符串。"""
    if not research_md:
        return ""
    import html as _html_mod
    body_lines: list[str] = []
    for line in research_md.splitlines():
        if line.startswith("## "):
            continue
        if line.startswith("> "):
            body_lines.append(
                f'<div class="vnote" style="margin-top:var(--space-3)">'
                f'{_html_mod.escape(line[2:])}</div>'
            )
        elif line.startswith("- "):
            body_lines.append(
                f'<div style="font-size:var(--text-sm);margin-bottom:var(--space-2)">'
                f'{_html_mod.escape(line[2:])}</div>'
            )
        elif line.startswith("  - "):
            body_lines.append(
                f'<div style="font-size:var(--text-sm);margin-left:var(--space-4);'
                f'margin-bottom:var(--space-1);color:var(--tx-s)">'
                f'{_html_mod.escape(line[4:])}</div>'
            )
        elif line.strip():
            body_lines.append(
                f'<div style="font-size:var(--text-sm);color:var(--tx-s)">'
                f'{_html_mod.escape(line)}</div>'
            )
    if not body_lines:
        return ""
    return f'''<section id="research">
  <div class="sh"><span class="st">机构观点与盈利预测</span><div class="sd"></div><span class="ss">卖方一致预期 · 公司业绩预告</span></div>
  <div class="card">
    {"".join(body_lines)}
  </div>
</section>'''


# --- _html_events ---
def _html_events() -> str:
    return '''<section id="events">
  <div class="sh"><span class="st">事件分析 &amp; 综合判断</span><div class="sd"></div><span class="ss">待 Claude 分析阶段填写</span></div>
  <div class="g2">
    <div class="pend"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg><div class="pend-t">事件分层分析</div><div class="pend-d">由 Claude 通过 WebSearch 补充近期公告、行业动态、重大事件</div></div>
    <div class="pend"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><div class="pend-t">综合研判</div><div class="pend-d">等待 Claude 分析阶段填写</div></div>
  </div>
</section>'''


# --- _html_refs ---
def _html_refs(ref_rows_html: str) -> str:
    return f'''<section id="refs">
  <div class="sh"><span class="st">数据来源</span><div class="sd"></div><span class="ss">可追溯调用路径</span></div>
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
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">数据详情</td>
        </tr></thead>
        <tbody>
          {ref_rows_html}
        </tbody>
      </table>
    </div>
  </div>
</section>'''


# --- _html_risk_banner ---
def _html_risk_banner() -> str:
    return (
        f'<div class="disc" style="margin-bottom:var(--space-4);border-left:3px solid var(--wn)">'
        f'<strong>⚠ 风险提示</strong> — 本报告由 invest:a-stock v{ENGINE_VERSION} 自动化引擎生成，'
        f'仅供学习研究参考，<strong>不构成任何投资建议、买卖指令或目标价预测</strong>。'
        f'</div>'
    )


# --- _html_disclaimer ---
def _html_disclaimer() -> str:
    return (
        f'<div class="disc"><strong>⚠ 免责声明</strong> — 本报告由 invest:a-stock v{ENGINE_VERSION} 自动化引擎生成，'
        f'仅供学习研究参考，<strong>不构成任何投资建议、买卖指令或目标价预测</strong>。'
        f'所有技术指标均为市场状态描述，非交易信号。'
        f'数据来源见上文 References 表，可能与实际公告存在差异，请以公司公告和交易所数据为准。'
        f'</div>'
    )


# --- _extract_financials_data ---
def _extract_financials_data(dims: dict) -> tuple[list, list, list, list, str, str]:
    """从 dimensions 提取财务数据，返回 (labels, roe, eps, profit, table_html, note)。"""
    fin = _get_dim_data(dims, "financials")
    if not fin or not isinstance(fin, list) or not fin:
        return [], [], [], [], "<div style='padding:2rem;text-align:center;color:var(--tx-f)'>财务数据不可得</div>", "财务数据不可得"

    fin = sort_kline_asc(fin)
    recent = fin[-8:] if len(fin) >= 8 else fin

    labels = []
    roe_data = []
    eps_data = []
    profit_data = []
    for r in recent:
        ed = str(r.get("end_date", ""))
        if len(ed) >= 7:
            labels.append(ed[2:4] + "Q" + str((int(ed[4:6]) - 1) // 3 + 1))
        else:
            labels.append(ed)
        roe_v = r.get("roe")
        roe_data.append(round(roe_v, 2) if roe_v is not None else None)
        eps_v = r.get("eps")
        eps_data.append(round(eps_v, 2) if eps_v is not None else None)
        pd_v = r.get("profit_dedt")
        profit_data.append(round(pd_v / 1e8, 2) if pd_v is not None else None)

    # 财务表格 HTML
    rows_html = ""
    for r in recent:
        ed = str(r.get("end_date", ""))
        if len(ed) >= 7:
            qlabel = ed[:4] + "-" + ed[4:6] + "-" + ed[6:8] if len(ed) == 8 else ed
        else:
            qlabel = ed
        roe_v = r.get("roe")
        roe_str = f"{roe_v:.2f}" if roe_v is not None else "-"
        eps_str = f"{eps_v:.2f}" if (eps_v := r.get("eps")) is not None else "-"
        pd_v = r.get("profit_dedt")
        pd_str = _fmt_v2(pd_v) if pd_v is not None else "-"
        rev_v = r.get("revenue")
        rev_str = _fmt_v2(rev_v) if rev_v is not None else "-"
        np_v = r.get("net_profit")
        np_str = _fmt_v2(np_v) if np_v is not None else "-"
        # ROE 高/低标记
        roe_cls = ""
        if len(recent) >= 3:
            all_roe = [x.get("roe") for x in recent if x.get("roe") is not None]
            if all_roe and roe_v is not None:
                avg = sum(all_roe) / len(all_roe)
                roe_cls = ' class="roe-hi"' if roe_v > avg * 1.1 else (' class="roe-lo"' if roe_v < avg * 0.9 else "")
        rows_html += f"<tr><td>{qlabel}</td><td{roe_cls}>{roe_str}</td><td>{eps_str}</td><td>{pd_str}</td><td>{rev_str}</td><td>{np_str}</td></tr>\n"

    table_html = f'''<table class="ft">
      <thead><tr><th>报告期</th><th>ROE(%)</th><th>EPS(元)</th><th>扣非净利润</th><th>营收</th><th>净利润</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>'''

    note = "营收/净利润字段为空（akshare接口降级）。" if not any(r.get("revenue") is not None for r in recent) else "财务数据来自第三方数据源，应与公司年报/季报交叉核对。"
    return labels, roe_data, eps_data, profit_data, table_html, note


# --- _extract_valuation_data ---
def _extract_valuation_data(dims: dict) -> dict:
    """提取估值数据用于 gauge 和 JS。"""
    val_data = _get_dim_data(dims, "valuation")
    result: dict = {
        "pe_pct": None, "pe_val": None, "pe_color": "var(--c1)",
        "pb_pct": None, "pb_val": None, "pb_color": "var(--c2)",
        "ps_pct": None, "ps_val": None, "ps_color": "var(--wn)",
        "pe_median": None, "pb_median": None,
        "zone_signal": "--", "zone_color": "var(--tx-m)",
        "n_samples": 0, "window_label": "近5年",
        "pe_above_median": False, "pb_above_median": False,
    }
    if not val_data or not isinstance(val_data, list) or not val_data:
        return result

    from lib.valuation import valuation_summary

    vs = sort_kline_asc(val_data)
    pe_seq = [r.get("pe_ttm") for r in vs]
    pb_seq = [r.get("pb") for r in vs]
    ps_seq = [r.get("ps_ttm") or r.get("ps") for r in vs]
    dv = next((r.get("dv_ratio") for r in reversed(vs) if r.get("dv_ratio") is not None), None)

    if len(vs) >= 1250:
        wl = "近5年"
    elif len(vs) >= 250:
        wl = f"近{len(vs) // 250}年"
    else:
        wl = "上市以来（数据有限）"

    summary = valuation_summary(pe_seq, pb_seq, ps_seq=ps_seq, dv_ratio=dv, window_label=wl)
    result["window_label"] = wl
    result["n_samples"] = summary.get("n_samples", 0)

    pe = summary.get("pe", {})
    if pe.get("current") is not None:
        result["pe_val"] = f"{pe['current']:.2f}x"
        result["pe_pct"] = f"{pe['pct']:.1f}" if pe.get("pct") is not None else None
        result["pe_median"] = f"{pe['median']:.2f}" if pe.get("median") is not None else None
        result["pe_above_median"] = (pe.get("current") is not None and pe.get("median") is not None
                                      and pe["current"] > pe["median"])

    pb = summary.get("pb", {})
    if pb.get("current") is not None:
        result["pb_val"] = f"{pb['current']:.2f}x"
        result["pb_pct"] = f"{pb['pct']:.1f}" if pb.get("pct") is not None else None
        result["pb_median"] = f"{pb['median']:.2f}" if pb.get("median") is not None else None
        result["pb_above_median"] = (pb.get("current") is not None and pb.get("median") is not None
                                      and pb["current"] > pb["median"])

    ps = summary.get("ps", {})
    if ps.get("current") is not None:
        result["ps_val"] = f"{ps['current']:.2f}x"
        result["ps_pct"] = f"{ps['pct']:.1f}" if ps.get("pct") is not None else None

    # 综合信号
    zones = []
    if pe.get("zone"):
        zones.append(pe["zone"])
    if pb.get("zone"):
        zones.append(pb["zone"])
    if any("偏" in z for z in zones):
        result["zone_signal"] = "偏低" if zones.count("偏低") > zones.count("偏高") else ("偏高" if zones.count("偏高") > zones.count("偏低") else "适中区间")
        if "偏低" in result["zone_signal"]:
            result["zone_color"] = "var(--up)"
        elif "偏高" in result["zone_signal"]:
            result["zone_color"] = "var(--dn)"
        else:
            result["zone_color"] = "var(--wn)"
    else:
        result["zone_signal"] = "适中区间"
        result["zone_color"] = "var(--wn)"
    return result


# --- _extract_technical_html ---
def _extract_technical_html(dims: dict) -> dict:
    """提取技术指标数据，返回结构化 dict 和 HTML 片段。"""
    kd = _get_dim_data(dims, "kline")
    result: dict = {
        "macd_html": "", "rsi_kdj_html": "", "boll_html": "",
        "ma_grid_html": "", "trend_label": "", "atr_14": None,
        "vol5d": None, "ma250_val": None, "ma250_pos": "",
        "kline_days": 0, "tech_source": "",
        "ma_20_slope": None, "ma_60_slope": None,
    }
    if not kd or not isinstance(kd, list) or not kd:
        empty = '<div style="padding:2rem;text-align:center;color:var(--tx-f);grid-column:1/-1">K 线数据不可得</div>'
        result.update(macd_html=empty, rsi_kdj_html="", boll_html="", ma_grid_html=empty)
        return result

    kd = sort_kline_asc(kd)
    result["kline_days"] = len(kd)
    meta = _get_dim_meta(dims, "kline")
    result["tech_source"] = f"不复权 · {meta.get('source', '未知')}"

    tech = compute(kd)
    if "error" in tech:
        err = tech.get("message", "未知错误")
        err_html = f'<div style="padding:2rem;text-align:center;color:var(--dn);grid-column:1/-1">技术指标计算失败: {sanitize_error(err, 80)}</div>'
        result.update(macd_html=err_html, rsi_kdj_html="", boll_html="", ma_grid_html=err_html)
        return result

    closes = [r.get("close", 0) or 0 for r in kd]
    latest_close = closes[-1] if closes else 0

    # MACD
    macd = tech.get("momentum", {}).get("macd", {})
    if macd.get("available"):
        dif_v = macd["dif"]
        dea_v = macd["dea"]
        hist_v = macd["histogram"]
        cross = macd.get("cross", {})
        cross_desc = cross.get("desc", "")
        has_bear = "下方" in cross_desc or "下穿" in cross_desc
        has_bull = "上方" in cross_desc or "上穿" in cross_desc
        macd_col = "var(--dn)" if has_bear else ("var(--up)" if has_bull else "var(--tx)")
        hist_trend = macd.get("histogram_trend", "")
        result["macd_html"] = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">MACD <span style="font-size:var(--text-xs);color:var(--tx-f);font-weight:400">(12,26,9)</span></div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2)">
        <div class="ipill"><div class="iname">DIF</div><div class="ival" style="color:{macd_col}">{dif_v:.2f}</div></div>
        <div class="ipill"><div class="iname">DEA</div><div class="ival" style="color:{macd_col}">{dea_v:.2f}</div></div>
        <div class="ipill"><div class="iname">柱</div><div class="ival" style="color:{macd_col}">{hist_v:.2f}</div></div>
      </div>
      <div style="margin-top:var(--space-3);font-size:var(--text-xs);color:{macd_col}">{'▼' if has_bear else '▲'} {cross_desc}{(' · ' + hist_trend) if hist_trend else ''}</div>
    </div>'''
    else:
        reason = macd.get("reason", "MACD 不可得")
        result["macd_html"] = f'<div class="card"><div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">MACD</div><div style="font-size:var(--text-xs);color:var(--tx-f);padding:1rem 0;text-align:center">{reason}</div></div>'

    # RSI / KDJ
    rsi = tech.get("overbought_oversold", {}).get("rsi", {})
    kdj = tech.get("overbought_oversold", {}).get("kdj", {})
    rsi_pills = ""
    for p in ("6", "12", "24"):
        r = rsi.get(p, {})
        if r.get("available"):
            v = r["value"]
            zone = r.get("zone", "中性")
            sig_cls = "sig-bear" if zone == "偏低" else ("sig-bull" if zone == "偏高" else "sig-neutral")
            v_color = "var(--dn)" if zone == "偏低" else ("var(--up)" if zone == "偏高" else "var(--tx)")
            rsi_pills += f'<div class="ipill"><div class="iname">RSI({p})</div><div class="ival" style="color:{v_color}">{v:.1f}</div><div class="isig {sig_cls}">{zone}</div></div>'
        else:
            rsi_pills += f'<div class="ipill"><div class="iname">RSI({p})</div><div class="ival" style="font-size:var(--text-xs);color:var(--tx-f)">--</div><div class="isig sig-neutral">N/A</div></div>'

    kdj_pills = ""
    kdj_color = "var(--tx)"
    if kdj.get("available"):
        k_val = kdj["k"]
        d_val = kdj["d"]
        j_val = kdj["j"]
        kdj_color = "var(--dn)" if j_val < 20 else ("var(--up)" if j_val > 80 else "var(--tx)")
        kdj_pills = f'''<div class="ipill"><div class="iname">K</div><div class="ival" style="color:{kdj_color}">{k_val:.1f}</div></div>
        <div class="ipill"><div class="iname">D</div><div class="ival" style="color:{kdj_color}">{d_val:.1f}</div></div>
        <div class="ipill"><div class="iname">J</div><div class="ival" style="color:{kdj_color}">{j_val:.1f}</div></div>'''
    else:
        kdj_pills = '<div class="ipill" style="grid-column:1/-1;text-align:center"><div class="iname">KDJ</div><div style="font-size:var(--text-xs);color:var(--tx-f)">不可得</div></div>'

    result["rsi_kdj_html"] = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">RSI / KDJ</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2);margin-bottom:var(--space-2)">
        {rsi_pills}
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2)">
        {kdj_pills}
      </div>
    </div>'''

    # BOLL
    boll = tech.get("volatility", {}).get("boll", {})
    if boll.get("available"):
        upper = boll["upper"]
        mid = boll["mid"]
        lower = boll["lower"]
        pos = boll.get("position", "")
        pos_pct = 50
        if pos == "上轨上方":
            pos_pct = 5
        elif pos == "中轨上方":
            pos_pct = 35
        elif pos == "中轨附近":
            pos_pct = 50
        elif pos == "中轨下方":
            pos_pct = 65
        elif pos == "下轨下方":
            pos_pct = 90
        boll_range = upper - lower
        if boll_range > 0:
            pos_pct = max(5, min(95, (latest_close - lower) / boll_range * 100))

        if latest_close <= mid:
            boll_cls = "var(--dn)" if latest_close <= lower * 1.02 else "var(--tx)"
        else:
            boll_cls = "var(--up)" if latest_close >= upper * 0.98 else "var(--tx)"

        result["boll_html"] = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">布林带 <span style="font-size:var(--text-xs);color:var(--tx-f);font-weight:400">(20,2)</span></div>
      <div style="display:flex;flex-direction:column;gap:var(--space-2)">
        <div style="display:flex;justify-content:space-between"><span style="font-size:var(--text-xs);color:var(--tx-f)">上轨</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m)">{upper:.2f}</span></div>
        <div style="position:relative;height:48px;background:linear-gradient(180deg,rgba(56,189,248,.04) 0%,rgba(56,189,248,.14) 50%,rgba(56,189,248,.04) 100%);border-radius:var(--r-sm);border:1px solid var(--bdr)">
          <div style="position:absolute;left:0;right:0;top:50%;height:1px;background:rgba(56,189,248,.25)"></div>
          <div style="position:absolute;left:{pos_pct:.0f}%;top:83%;transform:translate(-50%,-50%);width:8px;height:8px;border-radius:50%;background:{boll_cls};box-shadow:0 0 8px {boll_cls}"></div>
        </div>
        <div style="display:flex;justify-content:space-between"><span style="font-size:var(--text-xs);color:var(--tx-f)">中轨 MA20</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m)">{mid:.2f}</span></div>
        <div style="display:flex;justify-content:space-between"><span style="font-size:var(--text-xs);color:var(--tx-f)">下轨</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m)">{lower:.2f}</span></div>
        <div style="display:flex;justify-content:space-between;border-top:1px solid var(--bdr);padding-top:var(--space-2);margin-top:2px">
          <span style="font-size:var(--text-xs);color:{boll_cls}">收盘（{pos}）</span>
          <span style="font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600;color:{boll_cls}">{latest_close:.2f}</span>
        </div>
      </div>
    </div>'''
    else:
        reason = boll.get("reason", "BOLL 不可得")
        result["boll_html"] = f'<div class="card"><div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">布林带</div><div style="font-size:var(--text-xs);color:var(--tx-f);padding:1rem 0;text-align:center">{reason}</div></div>'

    # MA grid
    trend = tech.get("trend", {})
    ma = trend.get("ma", {})
    alignment = trend.get("alignment", {})
    slopes = trend.get("slope", {})
    result["trend_label"] = alignment.get("trend_label", "")

    ma_pills = ""
    for p in (5, 10, 20, 60, 120, 250):
        vals = ma.get(str(p), [])
        if vals and vals[-1] is not None:
            ma_v = vals[-1]
            slope = slopes.get(str(p))
            slope_str = f"斜率{'+' if slope and slope >= 0 else ''}{slope:.1f}%" if slope is not None else "--"
            pos_str = "上方" if latest_close > ma_v else ("下方" if latest_close < ma_v else "附近")
            pos_color = "var(--up)" if pos_str == "上方" else ("var(--dn)" if pos_str == "下方" else "var(--tx)")
            slp_color = "var(--up)" if slope and slope >= 0 else ("var(--dn)" if slope and slope < 0 else "var(--tx)")
            border_extra = ';border-color:rgba(56,189,248,.25)' if p == 250 else ''
            name_color = ' style="color:var(--ac)"' if p == 250 else ''
            ma_pills += f'<div class="ipill" style="text-align:center{border_extra}"><div class="iname"{name_color}>MA{p}</div><div style="font-family:var(--font-mono);font-size:var(--text-sm);color:{pos_color}">{ma_v:.2f}</div><div style="font-size:var(--text-xs);color:{slp_color}">{pos_str} · {slope_str}</div></div>'
        else:
            avail = trend.get("ma_availability", {}).get(str(p), "")
            err_txt = avail or "数据不足"
            ma_pills += f'<div class="ipill" style="text-align:center;opacity:.5"><div class="iname">MA{p}</div><div style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-f)">{err_txt}</div></div>'

    result["ma_grid_html"] = f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:var(--space-3)">{ma_pills}</div>'

    # ATR
    atr = tech.get("volatility", {}).get("atr", {})
    if atr.get("available"):
        result["atr_14"] = f"{atr['value']:.2f}"

    # Volume
    vol_info = tech.get("volume", {})
    result["vol5d"] = vol_info.get("avg_vol_5d")

    # MA250
    ma250_vals = ma.get("250", [])
    if ma250_vals and ma250_vals[-1] is not None:
        result["ma250_val"] = f"{ma250_vals[-1]:.2f}"
        result["ma250_pos"] = "上方" if latest_close > ma250_vals[-1] else ("下方" if latest_close < ma250_vals[-1] else "附近")

    return result


# --- _extract_northbound_data ---
def _extract_northbound_data(dims: dict) -> dict:
    """提取北向资金数据。"""
    nb = _get_dim_data(dims, "northbound")
    result: dict = {
        "flow_data": [], "total_flow": 0, "pos_days": 0, "total_days": 0,
        "has_data": False,
    }
    if not nb or not isinstance(nb, list) or not nb:
        return result

    nb = sort_kline_asc(nb)
    recent = nb[-7:] if len(nb) >= 7 else nb
    result["total_days"] = len(recent)
    flow_total = 0
    pos = 0
    for r in recent:
        td = str(r.get("trade_date", ""))
        if len(td) >= 10:
            md = td[5:10]
        elif len(td) >= 8:
            md = td[4:6] + "-" + td[6:8]
        else:
            md = td
        nv = r.get("net_mf_vol", 0) or 0
        flow_total += nv
        if nv > 0:
            pos += 1
        result["flow_data"].append([md, round(nv, 2), td, None])
    result["total_flow"] = round(flow_total, 2)
    result["pos_days"] = pos
    result["has_data"] = True
    return result


# --- _extract_holders_data ---
def _extract_holders_data(dims: dict) -> dict:
    """提取股东数据（最新报告期前十大）。"""
    sh = _get_dim_data(dims, "shareholders")
    result: dict = {"holders": [], "has_data": False}
    if not sh or not isinstance(sh, list) or not sh:
        return result
    result["holders"] = [
        (str(r.get("holder_name", "?")), r.get("hold_ratio", 0) or 0)
        for r in sh[:10]
    ]
    result["has_data"] = bool(result["holders"])
    return result


# --- _extract_refs_data ---
def _extract_refs_data(collection: dict) -> list[tuple[str, str, bool, str]]:
    """提取数据追溯信息，返回 [(维度, 接口, 是否可用, 详情), ...]。"""
    refs = []
    for dim in collection.get("dimensions", []):
        display = dim.get("display", dim.get("dimension", "?"))
        dn = dim.get("dimension", "")
        dim_data = dim.get("data")
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            meta = dim.get("_meta", {})
            qp = meta.get("query_params", "")
            src_name = meta.get("source", "?")
            avail = dim_data is not None
            detail = _data_fields(dn, dim_data) if avail else ""
            refs.append((display, f"{src_name}: {qp}" if qp else src_name, avail, detail))
        else:
            for s in all_src:
                sn = s.get("source", "?")
                qp = s.get("query_params", "")
                avail = s.get("data_available", False)
                # all_sources 中每个源有独立 data 吗？没有——只有 data_available 布尔。
                # 同一维度下所有源共享 dim_data，但为保持列准确，失败源标为空。
                detail = _data_fields(dn, dim_data) if avail else ""
                refs.append((display, f"{sn}: {qp}" if qp else sn, avail, detail))
    return refs


# --- _build_html_app_script ---
def _build_html_app_script(
    fin_labels_json: str,
    fin_roe_json: str,
    fin_eps_json: str,
    fin_profit_json: str,
    flow_data_json: str,
    closep_series: str,
    trend_label_json: str,
) -> str:
    """组装 HTML 内联脚本：数据行用 f-string 注入，逻辑块为普通字符串。"""
    data_lines = f"""// data
const finLabels={fin_labels_json};
const roeData={fin_roe_json};
const epsData={fin_eps_json};
const profitData={fin_profit_json};
const flowData={flow_data_json};
const closePriceSeries={closep_series};
const trendLabel={trend_label_json};
"""
    return data_lines + _HTML_APP_SCRIPT_LOGIC


# --- render_html ---
def render_html(collection: dict[str, Any], symbol: str, md_text: str | None = None) -> str:
    """HTML 研究报告（新版模板）。

    直接构建结构化 HTML，匹配 host-docs/stock-report.html 模板样式和交互。
    支持 Chart.js 图表、暗/亮主题切换、侧边栏导航。

    Args:
        collection: collector.collect_all() 的结果
        symbol: 股票代码（如 "600519"）
        md_text: 已弃用，保留仅为 CLI 向后兼容；HTML 仅读取 collection
    """
    del md_text  # stdout Markdown 由 invest.py 单独渲染
    dims = _index_dims(collection)
    basic = _get_dim_data(dims, "basic_info") or {}
    summary = collection.get("summary", {})
    fetched_at = collection.get("fetched_at", "")[:19]

    name = basic.get("name", "") or basic.get("股票简称", "")
    industry = basic.get("industry", "")

    # ── 行情数据 ──
    quote = _get_dim_data(dims, "quote")
    price = None
    change_pct = None
    turnover = None
    if isinstance(quote, dict):
        price = quote.get("price") or quote.get("close")
        change_pct = quote.get("change_pct")
        turnover = quote.get("turnover_rate")
    elif isinstance(quote, list) and quote:
        qsorted = sorted(quote, key=lambda x: x.get("trade_date", ""))
        last = qsorted[-1]
        price = last.get("close") or last.get("price")

    price_str = f"{price:.2f}" if price is not None else "--"
    is_down = change_pct is not None and change_pct < 0
    is_up = change_pct is not None and change_pct > 0
    price_color = "var(--dn)" if is_down else ("var(--up)" if is_up else "var(--tx)")
    change_str = f"{change_pct:+.2f}%" if change_pct is not None else "--"
    chg_color = "var(--dn)" if is_down else ("var(--up)" if is_up else "var(--tx-m)")
    turnover_str = f"{turnover:.2f}%" if turnover is not None else "--"

    # ── 财务数据 ──
    fin_labels, fin_roe, fin_eps, fin_profit, fin_table_html, fin_note = _extract_financials_data(dims)

    # ── 估值数据 ──
    val = _extract_valuation_data(dims)

    # ── 技术数据 ──
    tech = _extract_technical_html(dims)
    atr_str = tech.get("atr_14") or "--"
    vol5d_raw = tech.get("vol5d")
    vol5d_str = _fmt_v2(vol5d_raw) if vol5d_raw is not None else "--"
    ma250_val = tech.get("ma250_val")
    ma250_str = ma250_val or "--"
    ma250_pos = tech.get("ma250_pos", "")
    kline_days = tech.get("kline_days", 0)

    # ── 股息率 ──
    dv_str = "--"
    val_data = _get_dim_data(dims, "valuation")
    if isinstance(val_data, list) and val_data:
        vs = sort_kline_asc(val_data)
        dv = next((r.get("dv_ratio") for r in reversed(vs) if r.get("dv_ratio") is not None), None)
        if dv is not None:
            dv_str = f"{dv:.2f}%"

    # ── 北向资金 ──
    nb = _extract_northbound_data(dims)
    flow_data_json = json.dumps(nb["flow_data"]) if nb["has_data"] else "[]"
    flow_total = nb.get("total_flow", 0)
    flow_pos = nb.get("pos_days", 0)
    flow_days = nb.get("total_days", 0)
    flow_color = "var(--dn)" if flow_total < 0 else ("var(--up)" if flow_total > 0 else "var(--tx)")
    flow_total_str = _fmt_v2(flow_total, "") if flow_total else "0"
    nb_html = ""
    if nb["has_data"]:
        nb_html = f'''
    <div style="display:flex;align-items:center;gap:var(--space-4);margin-bottom:var(--space-3);flex-wrap:wrap">
      <div style="display:flex;align-items:center;gap:6px;font-size:var(--text-xs);color:var(--tx-m)"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--up)"></span>净流入</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:var(--text-xs);color:var(--tx-m)"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--dn)"></span>净流出</div>
      <div style="margin-left:auto;display:flex;gap:var(--space-3)">
        <div class="ipill" style="padding:4px 10px"><span style="font-size:var(--text-xs);color:var(--tx-f)">7日净流入&nbsp;</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600;color:{flow_color}">{flow_total_str}</span></div>
        <div class="ipill" style="padding:4px 10px"><span style="font-size:var(--text-xs);color:var(--tx-f)">净入天数&nbsp;</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600">{flow_pos}/{flow_days}</span></div>
      </div>
    </div>
    <div style="position:relative;height:240px"><canvas id="flowChart"></canvas></div>
    <div class="vnote" style="margin-top:var(--space-3)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>左轴：日净流向（万元）；右轴：收盘价（元）。北向资金为估算值，仅供参考。</div>'''
    else:
        nb_html = '<div style="padding:2rem;text-align:center;color:var(--tx-f)">北向资金数据不可得</div>'

    # ── 股东数据 ──
    holders_data = _extract_holders_data(dims)
    if holders_data["has_data"]:
        max_hold = max(h[1] for h in holders_data["holders"]) if holders_data["holders"] else 1
        holder_rows = "".join(
            f'<div class="hlr"><div class="hlrk">{i+1}</div><div class="hln"><div class="hlname">{_html_mod.escape(h[0])}</div><div class="hlbar" style="width:{(h[1]/max_hold*100):.0f}%"></div></div><div class="hlpct">{_fmt_v2(h[1], "%")}</div></div>'
            for i, h in enumerate(holders_data["holders"])
        )
        holders_html = f'<div id="holderList">{holder_rows}</div>'
    else:
        holders_html = '<div style="padding:2rem;text-align:center;color:var(--tx-f)">股东数据不可得</div>'

    # ── 引用来源 ──
    refs_data = _extract_refs_data(collection)
    ref_rows = "".join(
        f'<tr><td style="font-family:var(--font-mono);font-size:var(--text-xs);padding:8px 12px;border-bottom:1px solid var(--bdr);color:var(--tx-m)">{_html_mod.escape(d)}</td>'
        f'<td style="font-family:var(--font-mono);font-size:var(--text-xs);padding:8px 12px;border-bottom:1px solid var(--bdr)"><code>{_html_mod.escape(a)}</code></td>'
        f'<td style="font-family:var(--font-mono);font-size:var(--text-xs);padding:8px 12px;border-bottom:1px solid var(--bdr)"><span class="{"ref-ok" if ok else "ref-err"}">{detail if ok else ("✗ " + "不可用")}</span></td></tr>'
        for d, a, ok, detail in refs_data
    )

    # ── Chart.js 数据序列化 ──
    fin_labels_json = json.dumps(fin_labels, ensure_ascii=False)
    fin_roe_json = json.dumps(fin_roe, ensure_ascii=False)
    fin_eps_json = json.dumps(fin_eps, ensure_ascii=False)
    fin_profit_json = json.dumps(fin_profit, ensure_ascii=False)

    # ── 构建各模块 ──
    topbar = _html_topbar(symbol, name, price_str, change_str, price_color, chg_color, summary)
    sidebar = _html_sidebar()
    overview = _html_overview(price_str, change_str, price_color, chg_color,
                              vol5d_str, turnover_str, atr_str, vol5d_str,
                              dv_str, ma250_str, ma250_pos, kline_days)
    valuation = _html_valuation(
        val.get("pe_pct") or "0", val.get("pe_val") or "--", val.get("pe_color", "var(--c1)"),
        val.get("pb_pct") or "0", val.get("pb_val") or "--", val.get("pb_color", "var(--c2)"),
        val.get("ps_pct") or "0", val.get("ps_val") or "--", val.get("ps_color", "var(--wn)"),
        val.get("pe_median") or "--", val.get("pb_median") or "--",
        val.get("zone_signal", "--"), val.get("zone_color", "var(--tx-m)"),
        val.get("n_samples", 0), val.get("window_label", "近5年"),
        val.get("pe_above_median", False), val.get("pb_above_median", False),
    )
    financials = _html_financials(fin_table_html, fin_note)
    technicals = _html_technicals(
        tech.get("macd_html", ""), tech.get("rsi_kdj_html", ""), tech.get("boll_html", ""),
        tech.get("ma_grid_html", ""), "", tech.get("tech_source", ""),
    )
    northbound = _html_northbound(nb_html)
    holders_sec = _html_holders(holders_html)

    research_md = _lazy_section_research_summary(collection, symbol, dims)
    research_sec = _html_research(research_md)
    events_sec = _html_events()
    refs_sec = _html_refs(ref_rows)
    risk_banner = _html_risk_banner()
    disclaimer = _html_disclaimer()

    # ── Trend label (filled by JS) ──
    trend_label_json = json.dumps(tech.get("trend_label", ""), ensure_ascii=False)

    # ── Quote price series for flow chart ──
    kd = _get_dim_data(dims, "kline")
    closep_series = "[]"
    if isinstance(kd, list) and kd:
        kd = sort_kline_asc(kd)
        recent_closes = [r.get("close") for r in kd[-14:]]
        closep_series = json.dumps(recent_closes, ensure_ascii=False)

    # ── 构建完整 HTML ──
    html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_mod.escape(f"{symbol} {name}")} — invest:a-stock 研报</title>
<style>
{_HTML_CSS}
</style>
</head>
<body>
<div class="app">
{topbar}
{sidebar}
<main class="main">
<div style="display:flex;align-items:center;gap:var(--space-4);padding-bottom:var(--space-4);border-bottom:1px solid var(--bdr)">
  <div>
    <div style="font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono);margin-bottom:2px">采集时间 {_html_mod.escape(fetched_at)}</div>
    <div style="font-size:var(--text-xs);color:var(--tx-f)">维度 <span style="color:var(--wn);font-weight:600">{summary.get("available", 0)}/{summary.get("total", 0)} 有数据</span>{f'（{summary.get("degraded", 0)} 个接口降级）' if summary.get("degraded") else ''} · 不复权</div>
  </div>
  <span style="margin-left:auto;font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono)">tushare · akshare · baostock</span>
</div>

{risk_banner}
{overview}
{valuation}
{financials}
{technicals}
{northbound}
{holders_sec}
{research_sec}
{events_sec}
{refs_sec}
{disclaimer}
</main>
</div>

<script>
{_load_chart_js()}
</script>
<script>
{_build_html_app_script(
    fin_labels_json, fin_roe_json, fin_eps_json, fin_profit_json,
    flow_data_json, closep_series, trend_label_json,
)}
</script>
</body>
</html>"""

    return html

