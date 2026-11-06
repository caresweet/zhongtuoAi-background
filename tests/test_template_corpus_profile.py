# -*- coding: utf-8 -*-
"""范文/模板结构画像：docx 与 PDF 都能分析出章节、版式与数据位。"""

from docx import Document
from docx.shared import Pt

from app.services.template_corpus_profile import (
    analyze_template_file, load_corpus_profile, save_corpus_profile,
)


def _tpl(path):
    d = Document()
    d.styles['Normal'].font.size = Pt(12)
    d.add_heading('1 前言', level=1)
    d.add_heading('1.1 编制背景及过程', level=2)
    d.add_paragraph('本项目为储气库工程临时用地，临时用地面积 2.3248 公顷，'
                    '复垦方向为水田。')
    d.add_heading('2 编制总则', level=1)
    d.add_heading('2.1 编制目的', level=2)
    d.add_paragraph('为落实土地复垦义务，保护耕地资源，特编制本方案。')
    d.add_heading('3 项目概况', level=1)
    d.add_heading('3.1 项目简介', level=2)
    d.add_paragraph('项目位于淮安市淮安区，建设内容包括井场与施工便道。')
    d.save(str(path))


def test_analyze_docx_structure_and_typography(tmp_path):
    p = tmp_path / 'tpl.docx'
    _tpl(p)
    prof = analyze_template_file(p, domain='reclamation')
    assert prof['format'] == 'docx'
    assert prof['chapters'] == 3
    assert prof['sections'] >= 6
    assert prof['typography']['body']['size'] == 12.0
    keys = {s['key'] for s in prof['data_slots']}
    assert 'area_m2' in keys
    assert 'reclaim_direction' in keys


def test_analyze_skeleton_reference_has_no_chapters(tmp_path):
    """只有格式说明、没有章标题的参考件：章数为 0，但也要能落盘。"""
    p = tmp_path / 'fmt.docx'
    d = Document()
    d.add_paragraph('一、正文字体：仿宋，小四。')
    d.add_paragraph('二、标题字体：黑体。')
    d.save(str(p))
    prof = analyze_template_file(p, domain='stability')
    assert prof['chapters'] == 0


def test_corpus_profile_roundtrip(tmp_path):
    p = tmp_path / 'tpl.docx'
    _tpl(p)
    prof = analyze_template_file(p, domain='reclamation')
    out = save_corpus_profile(prof)
    assert out.exists()
    loaded = load_corpus_profile(p, domain='reclamation')
    assert loaded and loaded['chapters'] == prof['chapters']


def test_analyze_unsupported_file(tmp_path):
    # .doc 二进制老格式不支持；.txt/.md/.html 现在都能做结构分析
    p = tmp_path / 'x.doc'
    p.write_bytes(b'\xd0\xcf\x11\xe0hello')
    assert analyze_template_file(p) is None

    q = tmp_path / 'y.txt'
    q.write_text('# 1 项目概况\n\n正文。\n', encoding='utf-8')
    prof = analyze_template_file(q, domain='stability')
    assert prof and prof['chapters'] == 1
