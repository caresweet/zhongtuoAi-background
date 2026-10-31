#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复垦成稿体检：逐章核对图片落位/图注/图后解读、前言是否纯文字、附件清单状态。

用法：
    .venv/bin/python scripts/check_reclamation_report.py storage/generated/xxx.docx \
        [--template storage/templates/2c868be4f55aedd89004ada6e6f88fbc.docx]

输出是**给人看的对照表**：模板 vs 成稿，一眼能看出问题落在哪一章、哪一段。
"""

import argparse
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


def _has_img(p) -> bool:
    return bool(p._element.findall('.//' + qn('w:drawing')))


def _text(p) -> str:
    return (p.text or '').strip()


def _style(p) -> str:
    return (p.style.name if p.style is not None else '') or ''


def headings(path, ch_prefix=None):
    d = Document(path)
    out = []
    for p in d.paragraphs:
        s, t = _style(p), _text(p)
        if s.startswith('Heading') and t:
            if ch_prefix and not t.startswith(ch_prefix):
                continue
            out.append((s.replace('Heading ', 'H'), t))
    return out


def ch1_section_headings(path):
    return [t for s, t in headings(path) if re.match(r'^1(\.\d+)*\s', t)]


def ch1_tables(path):
    """第1章范围内的表格数（按标题位置切）。"""
    d = Document(path)
    body = d.element.body
    in_ch1, n = False, 0
    for child in body.iterchildren():
        tag = child.tag.split('}')[1]
        if tag == 'p':
            t = ''.join(child.itertext()).strip()
            if re.match(r'^1\s+前\s*言', t):
                in_ch1 = True
                continue
            if in_ch1 and re.match(r'^2\s+', t):
                break
        elif tag == 'tbl' and in_ch1:
            n += 1
    return n


def ch1_analysis_sentences(path):
    d = Document(path)
    in_ch1 = False
    hits = []
    for p in d.paragraphs:
        t = _text(p)
        if re.match(r'^1\s+前\s*言', t):
            in_ch1 = True
            continue
        if in_ch1 and re.match(r'^2\s+', t):
            break
        if in_ch1 and re.search(r'(上表|如表所示|见表|如下表|如表\d|上表中)', t):
            hits.append(t[:60])
    return hits


def images_with_context(path):
    """每张图：所在章/小节、图注、图后第一段正文（判断有没有数据解读）。"""
    d = Document(path)
    ps = d.paragraphs
    out = []
    h1 = h2 = h3 = ''
    for i, p in enumerate(ps):
        s, t = _style(p), _text(p)
        if s.startswith('Heading 1'):
            h1, h2, h3 = t, '', ''
        elif s.startswith('Heading 2'):
            h2, h3 = t, ''
        elif s.startswith('Heading 3'):
            h3 = t
        if not _has_img(p):
            continue
        caption = ''
        for j in range(i + 1, min(i + 3, len(ps))):
            tt = _text(ps[j])
            if tt and (tt.startswith('图') or '图' in tt[:6]):
                caption = tt
                break
            if tt:
                break
        after = ''
        for j in range(i + 1, min(i + 6, len(ps))):
            tt = _text(ps[j])
            if tt and not tt.startswith('图') and not _has_img(ps[j]):
                after = tt
                break
        out.append({"chapter": h1, "section": (h2 + (' / ' + h3 if h3 else '')), "caption": caption,
                    "after_has_number": bool(re.search(r'\d', after)),
                    "after": after[:50]})
    return out


def appendix_status(path):
    d = Document(path)
    items, sections = [], []
    in_list = False
    for p in d.paragraphs:
        t, s = _text(p), _style(p)
        if t == '附件清单':
            in_list = True
            continue
        if in_list and re.match(r'^附件\s*\d+\s', t):
            items.append(t)
        if s.startswith('Heading 1') and re.match(r'^附件\s*\d+\s', t):
            sections.append(t)
    return items, sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('docx')
    ap.add_argument('--template', default='storage/templates/2c868be4f55aedd89004ada6e6f88fbc.docx')
    a = ap.parse_args()
    p = Path(a.docx)

    print('=' * 70)
    print('成稿:', p.name)
    print('=' * 70)

    print('\n【第1章 前言】')
    def _n(x):
        return re.sub(r'\s+', '', x)
    tpl = [_n(t) for s, t in headings(a.template) if re.match(r'^1(\.\d+)*\s', t)] if Path(a.template).exists() else []
    got = [_n(t) for t in ch1_section_headings(str(p))]
    print('  模板小节:', tpl)
    print('  成稿小节:', got)
    print('  小节一致:', tpl == got)
    print('  第1章表格数:', ch1_tables(str(p)), '(口径：前言只写文字 → 应为 0)')
    hits = ch1_analysis_sentences(str(p))
    print('  表格引导/解读句:', len(hits))
    for h in hits[:5]:
        print('    ·', h)

    print('\n【正文配图】')
    imgs = images_with_context(str(p))
    print('  图总数:', len(imgs))
    for im in imgs:
        if im['chapter'].startswith('附件'):
            continue
        print(f"  · [{im['chapter']} / {im['section']}] {im['caption'] or '(无图注)'}"
              f" | 图后有数字: {'是' if im['after_has_number'] else '否'} | 后文: {im['after']}")

    print('\n【附件】')
    items, sections = appendix_status(str(p))
    missing = [i for i in items if '待补充' in i]
    print('  清单项:', len(items), '| 缺材料(待补充):', len(missing))
    for i in items:
        print('   ', i)
    print('  实际有内容的附件节:', sections)
    print('\n提示：图注应与其实际内容一致（影像图 vs 现场照片 vs 合同扫描件）；'
          '图应落在小节正文之后、且图后有数据解读。')


if __name__ == '__main__':
    main()
