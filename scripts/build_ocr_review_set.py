# -*- coding: utf-8 -*-
"""构建「问卷 OCR 校对」数据集：对文本层案卷 PDF 定位问卷页，渲染页图 + 存页文本。

来源：knowledge_documents 指定 id 的 PDF（淮安本地项目：黄码17/大湖娃/朱坝西朱坝）。
输出：
  storage/ocr_review/<did>/page_<n>.png  问卷页渲染图
  data/ocr_review_dataset.json           记录清单（项目/页/图路径/页文本）

用法：cd backend && python3 scripts/build_ocr_review_set.py
"""
import json
import os
import re
import sqlite3
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "knowledge_base.db"
IMG_DIR = ROOT / "storage" / "ocr_review"
OUT = ROOT / "data" / "ocr_review_dataset.json"

# 淮安本地问卷源：文档 id → 显示名
DOCS = {
    77: "黄码征地17号（稳评案卷）",
    80: "大湖娃幼儿园北侧绿地…案卷（送审稿）",
    81: "朱坝西片区成片开发地块项目（朱坝）",
}

KEYS = ("问卷调查表", "调查问卷", "姓名", "住址", "联系电话")
TITLE_PAT = re.compile(r"(稳评|群众|部门|公众)?(问卷调查表|调查问卷|调查表)")


def main():
    conn = sqlite3.connect(str(DB))
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    records = []

    for did, label in DOCS.items():
        row = conn.execute(
            "SELECT file_path,title FROM knowledge_documents WHERE id=?", (did,)
        ).fetchone()
        if not row:
            print(f"⚠️ id={did} 不存在")
            continue
        fpath = ROOT / "storage" / row[0]
        if not fpath.exists():
            print(f"⚠️ {did} 文件缺失 {fpath}")
            continue

        doc = fitz.open(str(fpath))
        proj_dir = IMG_DIR / str(did)
        proj_dir.mkdir(parents=True, exist_ok=True)

        page_text = {}
        for i in range(doc.page_count):
            t = doc[i].get_text()
            if t and len(t.strip()) > 40:
                page_text[i + 1] = t

        # 问卷页：命中标题/字段较多；文本层则筛
        q_pages = []
        for pno, t in page_text.items():
            hits = sum(1 for k in KEYS if k in t)
            has_title = bool(TITLE_PAT.search(t))
            if hits >= 2 or (has_title and "问卷" in t):
                q_pages.append(pno)
        q_pages = sorted(set(q_pages))
        print(f"id={did} 总页{doc.page_count} 文本页{len(page_text)} 问卷页{q_pages}")

        for pno in q_pages:
            img = proj_dir / f"page_{pno}.png"
            if not img.exists():
                pix = doc[pno - 1].get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
                pix.save(str(img))
            records.append({
                "doc_id": did,
                "doc": label,
                "page": pno,
                "image": str(img.relative_to(ROOT / "storage")),
                "text": page_text.get(pno, "")[:8000],
                "corrected": None,
                "status": "todo",
            })
        doc.close()

    # 校正暂存（若已有则合并保留）
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            merged = {f"{r['doc_id']}:{r['page']}": r for r in records}
            for r in old:
                key = f"{r['doc_id']}:{r['page']}"
                if key in merged:
                    merged[key]["corrected"] = r.get("corrected")
                    merged[key]["status"] = r.get("status", "todo")
            records = list(merged.values())
            records.sort(key=lambda r: (r["doc_id"], r["page"]))
        except Exception:
            pass

    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{len(records)} 条问卷页记录 → {OUT}")


if __name__ == "__main__":
    main()
