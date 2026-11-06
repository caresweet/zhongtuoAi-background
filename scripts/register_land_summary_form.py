# -*- coding: utf-8 -*-
"""把制式《土地分类面积汇总表》登记进项目知识库（每次出 表4-3/表4-4 时按它取地类顺序）。

源文件：~/Downloads/4_土地分类面积汇总表空白(1).xls
落库：
    storage/knowledge_docs/土地分类面积汇总表_制式.xls
    knowledge_base.db → knowledge_documents（domain=reclamation, type=standard）
    结构化数据 JSON 写进 structured_data_json，检索文本写进 extracted_text

用法：
    python scripts/register_land_summary_form.py
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SRC_CANDIDATES = [
    Path.home() / "Downloads" / "4_土地分类面积汇总表空白(1).xls",
    Path.home() / "Downloads" / "4_土地分类面积汇总表空白(1).xlsx",
]
DST = BACKEND / "storage" / "knowledge_docs" / "土地分类面积汇总表_制式.xls"
DB = BACKEND / "data" / "knowledge_base.db"
TITLE = "土地分类面积汇总表（制式空白表）"


def parse_form(path: Path) -> dict:
    import xlrd
    wb = xlrd.open_workbook(str(path))
    sh = wb.sheet_by_index(0)
    grid = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]

    def s(v):
        return str(v).replace("\n", "").replace("\r", "").strip() if v is not None else ""

    r1 = next(i for i, row in enumerate(grid) if any("面积总计" in s(c) for c in row))
    row1, row2 = grid[r1], grid[r1 + 1]

    # 一级地类（含编码）→ 二级叶子（含编码）
    cats = []
    cur = None
    for c, v in enumerate(row1):
        t = s(v)
        if not t or t in ("被征(用)地单位", "权属类别", "面积总计", "备注"):
            continue
        m = re.match(r'^(.*?)[（(]\s*([0-9A-Za-z]+)\s*[)）]$', t)
        name, code = (m.group(1), m.group(2)) if m else (t, "")
        cur = {"name": name, "code": code, "leaves": []}
        cats.append(cur)
        # 类别列本身也是「合计」列，叶子在后续列
    # 把 row2 的叶子按列归属回类别：重新按列扫描
    cols = []
    cat = None
    for c, v in enumerate(row1):
        t = s(v)
        if t and t not in ("被征(用)地单位", "权属类别", "面积总计", "备注"):
            cat = t
        cols.append(cat)
    seen = set()
    for c, v in enumerate(row2):
        t = s(v)
        if not t or t == "合计" or "单位" in t:
            continue
        parent = cols[c] if c < len(cols) else ""
        m = re.match(r'^(.*?)[（(]\s*([0-9A-Za-z]+)\s*[)）]$', t)
        name, code = (m.group(1), m.group(2)) if m else (t, "")
        key = (parent, name)
        if key in seen:
            continue
        seen.add(key)
        target = next((x for x in cats
                       if f"{x['name']}({x['code']})" == parent
                       or x["name"] == re.sub(r'[（(].*?[)）]', '', parent).strip()), None)
        if target is None:
            target = {"name": re.sub(r'[（(].*?[)）]', '', parent).strip(),
                      "code": "", "leaves": []}
            cats.append(target)
        target["leaves"].append({"name": name, "code": code})

    rows = [s(row[1]) or s(row[0]) for row in grid[r1 + 2:]
            if (s(row[1]) or s(row[0])) in ("集体合计", "国有合计", "合计")]
    return {"categories": cats, "rows": rows,
            "columns": ["被征(用)地单位", "权属类别", "面积总计", "备注"]}


def main() -> int:
    src = next((p for p in SRC_CANDIDATES if p.exists()), None)
    if src is None:
        print("找不到源文件：", [str(p) for p in SRC_CANDIDATES])
        return 1
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, DST)

    if DST.suffix.lower() == ".xls":
        form = parse_form(DST)
    else:
        form = parse_form(DST)

    outline_lines = ["《土地分类面积汇总表》制式表（复垦报告 表4-3 / 表4-4 的地类依据）",
                     "列：被征(用)地单位｜权属类别｜面积总计｜各地类（按下列顺序）｜备注",
                     "行：集体合计｜国有合计｜合计"]
    for cat in form["categories"]:
        leaves = "、".join(f"{x['name']}({x['code']})" for x in cat["leaves"] if x["name"])
        outline_lines.append(f"- {cat['name']}({cat['code']})：{leaves}")
    outline = "\n".join(outline_lines)

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    rel = str(DST.relative_to(BACKEND / "storage"))
    row = cur.execute(
        "select id from knowledge_documents where title=? limit 1", (TITLE,)).fetchone()
    values = (TITLE, "standard", "reclamation", rel, DST.stat().st_size, "xls",
              "completed", 0, None, "completed", "1", outline, outline,
              json.dumps(form, ensure_ascii=False), None, None, outline, outline,
              "confirmed")
    if row:
        cur.execute(
            "update knowledge_documents set document_type=?, domain=?, file_path=?,"
            " file_size=?, file_type=?, indexed_status=?, chunk_count=?, index_error=?,"
            " extraction_status=?, extraction_version=?, extracted_text=?,"
            " retrieval_text=?, structured_data_json=?, image_summary=?,"
            " collection_name=?, raw_text=?, cleaned_text=?, clean_status=?,"
            " is_active=1, updated_at=datetime('now') where id=?",
            (*values[1:], row[0]))
        did = row[0]
    else:
        cur.execute(
            "insert into knowledge_documents (title, document_type, domain, file_path,"
            " file_size, file_type, indexed_status, chunk_count, index_error,"
            " extraction_status, extraction_version, extracted_text, retrieval_text,"
            " structured_data_json, image_summary, collection_name, raw_text,"
            " cleaned_text, clean_status, is_active, created_at, updated_at)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,datetime('now'),datetime('now'))",
            values)
        did = cur.lastrowid
    conn.commit()
    conn.close()
    print("REGISTERED", did, DST)
    print(outline[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
