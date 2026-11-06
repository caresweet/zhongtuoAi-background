# -*- coding: utf-8 -*-
"""模板版式反读：标题/正文/表格的字体字号从模板 docx 取。"""

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from app.services.template_style_profile import (
    build_style_profile, load_style_profile, style_summary,
)


def _make_tpl(path):
    d = Document()
    st = d.styles['Normal']
    st.font.size = Pt(12)
    rpr = st.element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn('w:eastAsia'), '仿宋')
    d.add_heading('1 前言', level=1)
    d.add_heading('1.1 编制背景', level=2)
    d.add_heading('1.1.1 项目由来', level=3)
    d.add_paragraph('本项目为储气库工程临时用地，复垦方向为水田，面积 2.3248 公顷。')
    d.add_table(rows=2, cols=2)
    d.save(str(path))


def test_style_profile_reads_styles(tmp_path):
    p = tmp_path / 'tpl.docx'
    _make_tpl(p)
    prof = build_style_profile(p)
    assert prof['body']['font'] == '仿宋'
    assert prof['body']['size'] == 12.0
    for k in ('h1', 'h2', 'h3', 'table', 'caption', 'page'):
        assert k in prof


def test_style_summary_shape(tmp_path):
    p = tmp_path / 'tpl.docx'
    _make_tpl(p)
    s = style_summary(load_style_profile(p))
    assert s['body']['font'] == '仿宋'
    assert s['h1']['size'] is not None


def test_style_profile_non_docx():
    assert build_style_profile('/tmp/nope.pdf') is None


def test_assembler_typography_prefers_template():
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = 'reclamation'
    a._tpl_style = {'h1': {'font': '黑体', 'size': 18.0},
                    'body': {'font': '仿宋', 'size': 14.0},
                    'table': {'font': '仿宋', 'size': 11.0},
                    'caption': {'font': '黑体', 'size': 10.5, 'bold': True}}
    assert a._typo('h1') == ('黑体', 18.0)
    assert a._typo('body') == ('仿宋', 14.0)
    assert a._table_typo()[0] == '仿宋'
    assert a._table_typo()[1].pt == 11.0
    # 模板没识别的槽位回落到按域常量（不抛异常）
    assert a._typo('h2')[0]


def _set_borders(table, sz=8, color='000000', sides=('top','left','bottom','right','insideH','insideV')):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tblPr = table._tbl.tblPr
    old = tblPr.find(qn('w:tblBorders'))
    if old is not None:
        tblPr.remove(old)
    bd = OxmlElement('w:tblBorders')
    for side in sides:
        el = OxmlElement('w:' + side)
        el.set(qn('w:val'), 'single'); el.set(qn('w:sz'), str(sz))
        el.set(qn('w:space'), '0'); el.set(qn('w:color'), color)
        bd.append(el)
    tblPr.append(bd)


def test_table_border_and_shading_detected(tmp_path):
    p = tmp_path / 'bordered.docx'
    d = Document()
    d.add_paragraph('正文' * 20)
    t = d.add_table(rows=2, cols=2)
    _set_borders(t, sz=8)
    # 表头底纹
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tcPr = t.rows[0].cells[0]._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear'); shd.set(qn('w:fill'), 'D9D9D9')
    tcPr.append(shd)
    d.save(str(p))
    prof = build_style_profile(p)
    assert prof['table_border']['size_pt'] == 1.0
    assert prof['table_border']['inside'] is True
    assert prof['table_layout'].get('header_shading') == 'D9D9D9'


def test_assembler_applies_template_borders():
    from docx import Document as _D
    from docx.oxml.ns import qn
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = 'reclamation'
    a._tpl_style = {
        'table_border': {'val': 'single', 'size_pt': 1.0, 'color': '000000',
                         'inside': False, 'none': False},
        'table_layout': {'align': 'center', 'header_shading': 'D9D9D9',
                         'cell_margin_cm': {'top': 0, 'left': 0.19,
                                            'bottom': 0, 'right': 0.19}},
    }
    doc = _D()
    t = doc.add_table(rows=2, cols=2, style='Table Grid')
    a._apply_tpl_table_style(t)
    bd = t._tbl.tblPr.find(qn('w:tblBorders'))
    assert bd is not None
    top = bd.find(qn('w:top'))
    assert top.get(qn('w:sz')) == '8'
    inside = bd.find(qn('w:insideH'))
    assert inside.get(qn('w:val')) == 'nil'
    shd = t.rows[0].cells[0]._tc.tcPr.find(qn('w:shd'))
    assert shd.get(qn('w:fill')) == 'D9D9D9'


def test_document_defaults_use_template_fonts():
    from docx import Document as _D
    from docx.oxml.ns import qn
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = 'reclamation'
    a._tpl_style = {
        'h1': {'font': '黑体', 'size': 16.0},
        'h2': {'font': '黑体', 'size': 15.0},
        'h3': {'font': '黑体', 'size': 14.0},
        'body': {'font': '仿宋', 'size': 12.0},
        'table': {'font': '仿宋', 'size': 10.5},
        'caption': {'font': '黑体', 'size': 10.5, 'bold': True},
    }
    doc = _D()
    a._apply_document_fonts(doc)
    def ea(style):
        rpr = style.element.find(qn('w:rPr'))
        rf = rpr.find(qn('w:rFonts')) if rpr is not None else None
        return rf.get(qn('w:eastAsia')) if rf is not None else None
    assert ea(doc.styles['Normal']) == '仿宋'
    assert ea(doc.styles['Heading 1']) == '黑体'
    assert doc.styles['Heading 1'].font.size.pt == 16.0


def test_wide_table_gets_adaptive_widths_and_smaller_font():
    from docx import Document as _D
    from docx.oxml.ns import qn
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = 'reclamation'
    a._tpl_style = {'table': {'font': '仿宋', 'size': 10.5}}
    assert a._table_typo(6)[1].pt == 10.5
    assert a._table_typo(8)[1].pt == 9.0
    assert a._table_typo(11)[1].pt == 8.0
    doc = _D()
    t = doc.add_table(rows=2, cols=8)
    header = ['一级影响因素', '权重', '二级影响因素', '权重', '三级影响因素', '权重',
              '因素特征分级标准', '评价分值']
    a._fit_wide_table(t, header, [['自然条件', '0.55', '地形条件', '0.10', '自然坡度', '0.10', '3-7', '80']])
    widths = [g.get(qn('w:w')) for g in t._tbl.tblGrid.findall(qn('w:gridCol'))]
    assert len(set(widths)) > 1, '列宽应随内容变化，而不是等宽'
    assert sum(int(w) for w in widths) <= 14.6 * 567 + 60
