# -*- coding: utf-8 -*-
"""新旧报告对比审计。用法：python3 compare_reports.py <新报告.docx>
输出对已知问题维度的检查（对比旧报告 0901_1144 基线）。
"""
import sys
import re
from docx import Document
from docx.oxml.ns import qn

def audit(path):
    d = Document(path)
    paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    full = "\n".join(paras)
    checks = {}

    # 1. 内容问题
    checks["markdown残留**"] = full.count("**")
    checks["会议室错误词"] = full.count("会议室")
    checks["待补充残留"] = full.count("待补充")
    checks["326342出现"] = full.count("326342")
    checks["地类构成错误段"] = "从土地利用现状看" in full

    # 2. 表格
    tables = d.tables
    checks["表格数"] = len(tables)
    # 同章重复表（表头相似）
    import collections
    seen = []
    dup = 0
    body = d.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:tbl'):
            tr = child.find(qn('w:tr'))
            if tr is None: continue
            hdr = '|'.join(''.join(n.text or '' for n in tc.iter(qn('w:t')))[:10] for tc in tr.findall(qn('w:tc'))[:5])
            hdr = re.sub(r'\W', '', hdr)
            if hdr and len(hdr) > 5:
                seen.append(hdr)
    dup = len(seen) - len(set(seen))
    checks["疑似重复表头数"] = dup

    # 3. 责任单位/实施单位合并
    mg = re.search(r'稳评责任单位[^\n]*', full)
    mg2 = re.search(r'稳评实施单位[^\n]*', full)
    checks["责任单位行"] = bool(mg)
    checks["实施单位行"] = bool(mg2)

    # 4. 第3章照片
    img_caps = [p for p in paras if re.match(r'^图3-', p)]
    checks["图3-图题数"] = len(img_caps)

    # 5. 评分一致性（措施前/后）
    checks["措施前分"] = re.findall(r'措施前.*?(\d+)分', full)[:2]
    checks["措施后分"] = re.findall(r'措施后.*?(\d+)分', full)[:2]

    print(f"\n=== 报告审计: {path.split('/')[-1]} ===")
    for k, v in checks.items():
        print(f"  {k}: {v}")
    return checks

if __name__ == "__main__":
    audit(sys.argv[1])
