# -*- coding: utf-8 -*-
"""把人工投标案例里的人员与证书图片导入公司档案（众拓测绘）。

业主 2026-11-05：先把案例报告中的人员填入数据库，保留身份证等信息，
后期再补更多人员资质。

数据来源：`/Users/mac/Downloads/投标文件（众拓测绘）3.27.docx`
  · 人员配备表（姓名/性别/年龄/学历/专业/职称/相关证书/拟任岗位）
  · 第四部分（三）人员配备：每个人的职称/注册/培训证书图片
  · 文末法定代表人、被授权代表身份证图片

用法：python scripts/import_bid_case_personnel.py [--case 路径] [--company-id 4]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

from lxml import etree

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DB = BACKEND / "data" / "knowledge_base.db"
IMG_DIR = BACKEND / "storage" / "images" / "person_certs"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _person_table(doc) -> dict:
    """人员配备表 → {姓名: 字段}。"""
    out = {}
    for tb in doc.tables:
        rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
        if not rows:
            continue
        head = rows[0]
        if "姓名" not in head or "职称" not in head:
            continue
        idx = {h: i for i, h in enumerate(head)}
        for r in rows[1:]:
            if len(r) <= idx.get("姓名", 0):
                continue
            name = r[idx["姓名"]].strip()
            if not name or name == "示例":
                continue
            out.setdefault(name, {
                "gender": r[idx["性别"]] if "性别" in idx else "",
                "age": r[idx["年龄"]] if "年龄" in idx else "",
                "education": r[idx["学历"]] if "学历" in idx else "",
                "major": r[idx["专业"]] if "专业" in idx else "",
                "title": r[idx["职称"]] if "职称" in idx else "",
                "certs": r[idx["相关证书"]] if "相关证书" in idx else "",
                "role": r[idx["拟任岗位"]] if "拟任岗位" in idx else "",
            })
    return out


def _walk_person_images(docx_zip, doc):
    """（三）人员配备 段 → {姓名: [(证书名, media 路径)]}；文末身份证单独返回。"""
    body = etree.fromstring(docx_zip.read("word/document.xml")).find(f"{W}body")
    rels = etree.fromstring(docx_zip.read("word/_rels/document.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}

    out: dict = {}
    ids: list = []
    cur_person, cur_label = "", ""
    on = False
    for el in body.iterchildren():
        if etree.QName(el).localname != "p":
            continue
        t = "".join(x.text or "" for x in el.iter(f"{W}t")).strip()
        if "（三）人员配备" in t:
            on = True
            continue
        if "（四）技术方案" in t:
            on = False
            continue
        if not on and not t:
            continue

        imgs = [b.get(f"{R}embed") for b in el.iter(f"{A}blip")]
        if on:
            if t:
                m = re.match(r'^[0-9]+[.、]\s*(项目负责人|技术负责人)[-－—](.+)$', t)
                m2 = re.match(r'^[（(][0-9]+[)）]\s*(.+)$', t)
                if m:
                    cur_person = m.group(2).strip()
                    out.setdefault(cur_person, [])
                    cur_label = ""
                elif m2:
                    cur_person = m2.group(1).strip()
                    out.setdefault(cur_person, [])
                    cur_label = ""
                elif "项目组成员" in t:
                    cur_person = ""
                elif cur_person and len(t) <= 40 and not imgs:
                    cur_label = t
            for rid in imgs:
                target = relmap.get(rid) or ""
                if cur_person and target:
                    out[cur_person].append((cur_label or "证书", "word/" + target))
            continue
        # 文末身份证
        if imgs:
            ids.append((t, [relmap.get(r, "") for r in imgs]))
    return out, ids


def _upsert_person(conn, company_id: int, name: str, info: dict, certs: list) -> int:
    row = conn.execute(
        "select id from company_personnel where company_id=? and name=?",
        (company_id, name)).fetchone()
    fields = (info.get("gender") or "", info.get("age") or "", info.get("education") or "",
              info.get("major") or "", info.get("title") or "",
              info.get("role") or "", json.dumps(certs, ensure_ascii=False))
    if row:
        conn.execute(
            "update company_personnel set gender=coalesce(nullif(?,''),gender),"
            " age=coalesce(nullif(?,''),age), education=coalesce(nullif(?,''),education),"
            " major=coalesce(nullif(?,''),major), title=coalesce(nullif(?,''),title),"
            " role=coalesce(nullif(?,''),role), certs=?, updated_at=datetime('now')"
            " where id=?", (*fields, row[0]))
        return row[0]
    cur = conn.execute(
        "insert into company_personnel (name,gender,age,education,major,title,role,certs,"
        " has_wenshen_cert,sort_order,company_id,updated_at)"
        " values (?,?,?,?,?,?,?,?,0,99,?,datetime('now'))", (name, *fields, company_id))
    return cur.lastrowid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=str(Path.home() / "Downloads" /
                                          "投标文件（众拓测绘）3.27.docx"))
    ap.add_argument("--company-id", type=int, default=4)   # 江苏众拓测绘有限公司
    args = ap.parse_args()
    case = Path(args.case).expanduser()
    if not case.exists():
        print("找不到案例文件：", case)
        return 1

    from docx import Document
    doc = Document(str(case))
    table = _person_table(doc)
    z = zipfile.ZipFile(str(case))
    person_imgs, id_imgs = _walk_person_images(z, doc)
    IMG_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB))
    imported = []
    for name, info in table.items():
        imgs = person_imgs.get(name, [])
        labels = [lb for lb, _ in imgs]
        extra = [x.strip() for x in re.split(r'[\n、，,]', info.get("certs") or "") if x.strip()]
        certs = list(dict.fromkeys(extra + labels))
        pid = _upsert_person(conn, args.company_id, name, info, certs)
        saved = []
        for i, (label, target) in enumerate(imgs):
            try:
                data = z.read(target)
            except Exception:
                continue
            ext = Path(target).suffix or ".jpg"
            fn = IMG_DIR / f"{args.company_id}_{name}_{i}{ext}"
            fn.write_bytes(data)
            conn.execute(
                "insert into company_personnel_images (personnel_id,image_name,category,"
                "image_path,image_data,mime_type,source,updated_at,company_id)"
                " values (?,?,?,?,NULL,?,?,datetime('now'),?)",
                (pid, f"{name} {label}#{i + 1}", label or "证书",
                 str(fn.relative_to(BACKEND)), "image/jpeg" if ext == ".jpg" else "image/png",
                 "bid_case", args.company_id))
            saved.append(str(fn.relative_to(BACKEND)))
        imported.append((name, len(certs), len(saved)))

    # 身份证图片进公司资料图库
    for label, targets in id_imgs:
        for j, target in enumerate(targets):
            if not target:
                continue
            try:
                data = z.read("word/" + target)
            except Exception:
                continue
            ext = Path(target).suffix or ".jpg"
            name = (label or "身份证") + ("" if j == 0 else f"_{j + 1}")
            conn.execute(
                "insert into asset_images (image_name,category,image_data,mime_type,"
                "source_file,search_keywords,created_at,is_active)"
                " values (?,?,?,?,?,?,datetime('now'),1)",
                (name[:100], "身份证", base64.b64encode(data).decode(), "image/jpeg"
                 if ext == ".jpg" else "image/png", case.name, "身份证 法定代表人 授权代表"))
    conn.commit()
    conn.close()

    print(f"导入 {len(imported)} 人（company_id={args.company_id}）：")
    for name, nc, ni in imported:
        print(f"  - {name}：{nc} 项证书、{ni} 张证书图片")
    print(f"身份证图片 {len(id_imgs)} 组 → asset_images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
