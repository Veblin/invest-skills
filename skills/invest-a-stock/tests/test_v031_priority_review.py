"""2026-10-07: ROE/01/02/05/10/11，真实生产者、消费者及负例。"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import report_qc
from fixtures.collections import collection_v2_minimal, make_kline_rows
from lib.analysis_schema import validate_sections
from lib.render import render_report_v3
from lib.render_html import _extract_technical_html
from lib.render_markdown._base import _render_ma_system
from lib.render_markdown._v3 import _FundamentalsContext, _section_4d_valuation_expectation


def collection_with_financials(rows):
    c = collection_v2_minimal()
    for dim in c['dimensions']:
        if dim['dimension'] == 'financials':
            dim['data'] = rows
    return c


@pytest.mark.parametrize('mmdd', ['1231', '0630'])
@pytest.mark.parametrize('missing', [[], [{'end_date': '2023{mmdd}', 'roe': None}]])
def test_roe_gap_cannot_claim_year_over_year(mmdd, missing):
    rows = [{'end_date': '2022'+mmdd, 'roe': 10}]
    rows += [{**r, 'end_date': r['end_date'].format(mmdd=mmdd)} for r in missing]
    rows += [{'end_date': '2024'+mmdd, 'roe': 15}]
    md = render_report_v3(collection_with_financials(rows), '600176')
    b1 = md.split('#### B-①')[1].split('#### B-②')[0]
    assert '较上年' not in b1
    assert '2024 年相对 2022 年变化 +5.00pp（非同比' in b1
    assert f'2022-{mmdd[:2]}-{mmdd[2:]}: 10.00%' in b1


def test_roe_series_limits_count_and_preserves_restated_period():
    rows = [{'end_date': f'{y}1231', 'roe': 10} for y in range(2018, 2025)]
    rows += [{'end_date': '20241231', 'ann_date': '20250301', 'roe': 15}]
    md = render_report_v3(collection_with_financials(rows), '600176')
    b1 = md.split('#### B-①')[1].split('#### B-②')[0]
    assert '近 5 个有效年报' in b1
    assert '2024-12-31: 15.00%' in b1
    assert '较上年 +5.00pp' in b1


@pytest.mark.parametrize('heading,scanner', [
    ('核心结论', report_qc.conclusion_evidence_findings),
    ('事件时间线', report_qc.event_analysis_evidence_findings),
])
@pytest.mark.parametrize('first', ['| 公司盈利持续改善 |', '| 公司盈利持续改善 | 999 |'])
def test_headerless_first_row_is_checked(heading, scanner, first):
    findings = scanner(f'## {heading}\n{first}\n| 本行有来源 [来源: 公告原文] |\n')
    assert any(f['line'] == 2 and f['severity'] == 'error' for f in findings)


@pytest.mark.parametrize('tag', ['B', 'L1', 'B ✅ 强 🌐 多源 📅 报告期已注明 ✓✓ 跨源一致',
    'B ⚠️ 中 📡 单源 🗄️ 滞后 > 1 年 — 单源无验证'])
def test_schema_valid_evidence_label_rendered_and_checked(tag):
    sec = {'module': 'events', 'title': '结论：字段核对',
           'facts_md': '已读取公告字段。[来源: 公告原文]',
           'analysis_md': '字段含义已核对。[证据: B]', 'evidence_tag': tag,
           'position': 'events'}
    assert validate_sections([sec]) == []
    md = render_report_v3(collection_v2_minimal(), '600176', analysis=[sec])
    assert f'**证据等级：** {tag}' in md
    assert report_qc.conclusion_evidence_findings('## 核心结论\n'+f'**证据等级：** {tag}\n') == []


@pytest.mark.parametrize('tag', ['B ✅ 🌐 🕐 ✓✓', 'B 🕐 近30日', 'B 公司盈利增长999%',
                                  'B ✅ 强 📡 多源 📅 报告期已注明 ✓✓',
                                  'B\n✅ 强 🌐 多源 📅 报告期已注明 ✓✓'])
def test_illegal_evidence_tail_fails_schema(tag):
    sec = {'module': 'events', 'title': '结论', 'facts_md': '事实[来源: 公告原文]',
           'analysis_md': '分析[证据: B]', 'evidence_tag': tag, 'position': 'events'}
    assert any('evidence_tag' in e for e in validate_sections([sec]))


@pytest.mark.parametrize('source', ['公告原文第3页，缺失字段按空值处理',
    '披露文件第3页：未取得收入属于会计事项', '公告原文第3页，不存在担保事项', '公告原文不存在担保事项'])
def test_source_content_absence_is_not_source_unavailable(source):
    assert report_qc.event_analysis_evidence_findings(
        f'## 事件时间线\n披露字段已核。[来源: {source}]\n') == []


@pytest.mark.parametrize('source', ['公告原文未读取', '公告原文未逐条读取',
    '公告原文未获取', '公告原文不存在', '来源不可得', '未取得公告原文', '仅公告标题', '公告原文（未读取）', '未核验的披露文件'])
def test_unavailable_original_or_title_still_fails(source):
    assert report_qc.event_analysis_evidence_findings(
        f'## 事件时间线\n该事项确定影响盈利。[来源: {source}]\n')


@pytest.mark.parametrize('pe', [None, 0, -1])
def test_missing_pe_does_not_claim_rate_gap(pe):
    c = collection_v2_minimal()
    ctx = _FundamentalsContext({d['dimension']: d for d in c['dimensions']}, c, {})
    ctx.current_pe = pe
    ctx.ms = {'erp': {'cn10y': 2.5}}
    statuses = []
    text = '\n'.join(_section_4d_valuation_expectation(ctx, statuses))
    assert statuses[-1][-1] == '数据不足'
    assert '本次 PE 或 g_implied 不可得' in text
    assert '本次无风险利率不可得' not in text
    assert '先取得同估值时点的实际利率' not in text


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), 'not-a-price'])
def test_real_compute_invalid_tail_agrees_between_formats(bad):
    c = collection_v2_minimal()
    rows = make_kline_rows(251)
    for r in rows:
        r.update(open=10.11, high=10.11, low=10.11, close=10.11)
    rows[-1]['close'] = bad
    for d in c['dimensions']:
        if d['dimension'] == 'kline': d['data'] = rows
    md = '\n'.join(_render_ma_system(c))
    html = _extract_technical_html({d['dimension']: d for d in c['dimensions']})
    assert '收盘价与 MA60 持平' in md
    assert '收盘价与 MA60 持平' in html['ma_grid_html']
    assert html['ma250_pos'] == '收盘价与 MA250 持平'
    assert '收盘价不可得' not in html['ma_grid_html']
