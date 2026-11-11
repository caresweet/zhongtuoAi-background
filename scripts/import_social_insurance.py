# -*- coding: utf-8 -*-
"""导入每月社保参保信息（业主每月提供）→ `company_social_insurance`。

用法示例：

    .venv/bin/python scripts/import_social_insurance.py \
        --company-id 4 --period 2026-11 \
        --roster ~/Downloads/2026年11月社保名单.xlsx \
        --images ~/Downloads/社保缴费证明2026-11.pdf

- `--roster`：姓名/身份证号 名单（xlsx / csv），用于判断某人当月是否参保；
  招标文件要求拟派人员社保时，只会从当月名单里选人。
- `--images`：社保缴费证明/权益记录单（pdf / jpg / png）。PDF 逐页转 PNG，
  图片原样归档到 `storage/images/social_insurance/<period>/`，投标文件按最近月份附。
- 同一 (公司, 月份) 重复导入会**先清空再写入**，方便每月覆盖更新。
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DB = BACKEND / "data" / "knowledge_base.db"
IMG_DIR = BACKEND / "storage" / "images" / "social_insurance"

_HEAD_NAME = ("姓名", "名字", "人员姓名", "职工姓名")
_HEAD_ID = ("身份证号", "身份证号码", "证件号码", "公民身份号码")


def _rows_from_xlsx(path: Path):
    from openpyxl import load_workbook
    wb = load_workbook(str(path), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _rows_from_csv(path: Path):
    text = path.read_text("utf-8-sig", errors="ignore")
    return [row for row in csv.reader(io.StringIO(text))]


def _roster_from_pdf(path: Path):
    """从**电子版**单位权益记录单 PDF 里直接解析参保人（姓名+身份证号）。

    权益记录单就是人员社保信息：表格行是「序号 姓名 身份证号 缴费起止年月 月数」。
    扫描件（没有文字层）解析不到时，退回 `--roster` 名单。
    """
    import re
    try:
        import fitz
        doc = fitz.open(str(path))
    except Exception:
        return []
    out = []
    try:
        text = "\n".join(p.get_text() for p in doc)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    # 表格行：1 甘文锋 429006197804105139 202608 - 202608 1
    pat = re.compile(
        r'\d{1,3}\s+([一-龥][一-龥·]{1,5})\s+'
        r'([0-9Xx]{18,19})\s+\d{6}')
    for name, id_no in pat.findall(text):
        out.append((name, id_no.upper()))
    return out


def parse_roster(path: Path):
    """→ [(姓名, 身份证号)]；表头认常见写法，找不到「姓名」列就报错。"""
    rows = (_rows_from_xlsx(path) if path.suffix.lower() in (".xlsx", ".xlsm")
            else _rows_from_csv(path))
    if not rows:
        return []
    header = [str(c or "").strip().replace(" ", "") for c in rows[0]]
    ni = next((i for i, h in enumerate(header) if h in _HEAD_NAME), None)
    ii = next((i for i, h in enumerate(header) if h in _HEAD_ID), None)
    if ni is None:
        raise SystemExit(f"名单里找不到「姓名」列：{header}")
    out = []
    for r in rows[1:]:
        name = str(r[ni] or "").strip() if ni < len(r) else ""
        if not name:
            continue
        id_no = str(r[ii] or "").strip() if (ii is not None and ii < len(r)) else ""
        out.append((name, id_no))
    return out


def _store_images(period: str, files):
    out_dir = IMG_DIR / period
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        f = Path(f).expanduser()
        if not f.exists():
            print("找不到材料：", f)
            continue
        if f.suffix.lower() == ".pdf":
            from app.services.pdf_key_images import render_all_pages
            pages = render_all_pages(str(f), str(out_dir))
            saved += [Path(p) for p in pages]
        elif f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            dest = out_dir / f.name
            if f.resolve() != dest.resolve():
                shutil.copy2(f, dest)
            saved.append(dest)
    return saved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company-id", type=int, default=4)
    ap.add_argument("--period", required=True, help="YYYY-MM，例如 2026-11")
    ap.add_argument("--roster", help="姓名/身份证号 名单（xlsx/csv）")
    ap.add_argument("--images", nargs="*", default=[],
                    help="社保证明 pdf/jpg/png（可多个）")
    a = ap.parse_args()

    from app.services.personnel_store import ensure_tables
    ensure_tables()

    people = parse_roster(Path(a.roster).expanduser()) if a.roster else []
    if not people:
        # 只给单位权益记录单（电子版 PDF）也能用：直接从表里解析参保人
        for _f in a.images or []:
            if str(_f).lower().endswith(".pdf"):
                people = _roster_from_pdf(Path(_f).expanduser())
                if people:
                    print(f"从单位权益记录单解析到 {len(people)} 名参保人员")
                    break
    imgs = _store_images(a.period, a.images) if a.images else []

    con = sqlite3.connect(str(DB))
    try:
        con.execute(
            "delete from company_social_insurance where company_id=? and period=?",
            (a.company_id, a.period))
        for name, id_no in people:
            con.execute(
                "insert or ignore into company_social_insurance "
                "(company_id, period, person_name, id_no, image_path, mime_type, "
                " source, created_at) values (?,?,?,?,?,?,?,datetime('now'))",
                (a.company_id, a.period, name, id_no, "", "", "monthly_roster"))
        for p in imgs:
            rel = str(p.relative_to(BACKEND)) if p.is_absolute() else str(p)
            con.execute(
                "insert or ignore into company_social_insurance "
                "(company_id, period, person_name, id_no, image_path, mime_type, "
                " source, created_at) values (?,?,?,?,?,?,?,datetime('now'))",
                (a.company_id, a.period, "", "",
                 rel, "image/png" if p.suffix.lower() == ".png" else "image/jpeg",
                 "monthly_proof"))
        con.commit()
    finally:
        con.close()
    print(f"完成：{a.period} 参保 {len(people)} 人，证明材料 {len(imgs)} 页")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
