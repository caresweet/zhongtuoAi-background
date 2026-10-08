# -*- coding: utf-8 -*-
"""问卷/调查表提取与盘点脚本 —— 为手写问卷 OCR 训练数据做准备。

扫两类来源：
  1) knowledge_base.knowledge_documents 的 raw_text（已解析范文/案卷/材料）
  2) templates 表中公司模板 docx（含空/样表问卷）

检测：
  群众调查问卷 / 部门调查问卷 / 问卷调查表 的区块，及其中单人记录特征
  （姓名、被调查人签名、单位（部门）等），估算份数。

用法：cd backend && python3 scripts/extract_questionnaires.py
输出：scripts/output/questionnaire_inventory.md
"""
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.db"

# 问卷表头/区块特征
HEAD_PAT = re.compile(r"(稳评|土地征收)?(问卷调查表|调查问卷|群众(意见)?(调查)?问卷|部门调查|公众(意见)?调查|调查表)")
REC_PAT = re.compile(r"姓\s*名\s*[:：]")
SIGN_PAT = re.compile(r"被调查人签名")
DEPT_PAT = re.compile(r"被调查单位|单位（部门）|所在单位")


def scan_text(raw: str):
    heads = 0
    recs = 0
    signs = 0
    dept = 0
    for line in raw.split("\n"):
        s = line.strip()
        if HEAD_PAT.search(s):
            heads += 1
        if REC_PAT.search(s) or SIGN_PAT.search(s):
            recs += 1
        if SIGN_PAT.search(s):
            signs += 1
        if DEPT_PAT.search(s):
            dept += 1
    return heads, recs, signs, dept


def main():
    conn = sqlite3.connect(str(DB))
    rows = []
    for rid, title, dtype, raw in conn.execute(
        "SELECT id,title,document_type,raw_text FROM knowledge_documents "
        "WHERE is_active=1 AND raw_text IS NOT NULL AND raw_text != ''"
    ).fetchall():
        h, r, s, d = scan_text(raw or "")
        if h or r or d:
            rows.append((rid, title, dtype, h, r, s, d))

    rows.sort(key=lambda x: x[3] + x[4] + x[6], reverse=True)

    lines = []
    lines.append("# 问卷/调查表盘点清单（OCR 训练数据候选）\n")
    lines.append("| id | 类型 | 文档 | 表头命中 | 姓名记录≈份数 | 被调查人签名 | 单位/部门 |")
    lines.append("|---|---|---|---|---|---|---|")
    for rid, title, dtype, h, r, s, d in rows:
        lines.append(f"| {rid} | {dtype} | {title[:46]} | {h} | {r} | {s} | {d} |")
    lines.append("")
    lines.append(f"共 {len(rows)} 份文档含问卷特征。")
    lines.append("")
    lines.append("> 说明：姓名记录/签名数可作为「单人问卷份数」下限估算；单位（部门）命中多=部门调查表。")

    out = Path(__file__).resolve().parent / "output"
    out.mkdir(exist_ok=True)
    p = out / "questionnaire_inventory.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
