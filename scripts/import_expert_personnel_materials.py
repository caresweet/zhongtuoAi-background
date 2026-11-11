# -*- coding: utf-8 -*-
"""把人工投标文件里的人员证书材料导入公司人员库。

来源：《投标文件-众拓测绘-佳富村》第七章后附的证书扫描件（2026-11-11 业主：
「获取专家写的人员资料」）。按图片的 docPr descr / 证书内容识别到人：

    rId34 徐作诚 注册测绘师证书
    rId36 徐作诚 高级专业技术资格证书
    rId37 王明   中级专业技术资格证书（工程师）
    rId38 郝怡佳 工程师证书
    rId39 刘倩   助理工程师证书

图片落盘 `storage/images/person_cert_*.{jpg,png}`，登记到
`company_personnel_images`（按人员姓名找该公司 row），并补证书编号。
幂等：同名图片已存在则跳过。
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))

EXPERT = Path.home() / "Downloads" / "投标文件-众拓测绘-佳富村.docx"
COMPANY_ID = 4

ITEMS = [
    ("rId34", "徐作诚", "注册测绘师证书", "注册测绘师"),
    ("rId36", "徐作诚", "高级专业技术资格证书", "职称证书"),
    ("rId37", "王明", "中级专业技术资格证书", "职称证书"),
    ("rId38", "郝怡佳", "工程师证书", "职称证书"),
    ("rId39", "刘倩", "助理工程师证书", "职称证书"),
]

CERT_NO = {
    "王明": "243208006163320199",
    "郝怡佳": "HAZ2018010P2830266",
    "刘倩": "243208710014410002",
}


def main() -> int:
    if not EXPERT.exists():
        print("找不到人工稿：", EXPERT)
        return 1
    from docx import Document

    doc = Document(str(EXPERT))
    rels = doc.part.rels
    out_dir = BACKEND / "storage" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(BACKEND / "data" / "knowledge_base.db"))
    ids = {name: pid for pid, name in con.execute(
        "select id,name from company_personnel where company_id=?", (COMPANY_ID,))}

    for rid, name, label, category in ITEMS:
        part = rels[rid].target_part
        ext = os.path.splitext(part.partname)[1] or ".jpg"
        fname = f"person_cert_{name}_{label}{ext}"
        fpath = out_dir / fname
        if not fpath.exists():
            fpath.write_bytes(part.blob)
        rel_path = str(fpath.relative_to(BACKEND))
        image_name = f"{name} {label}"
        pid = ids.get(name)
        if not pid:
            print(f"人员库没有 {name}，跳过图片登记")
            continue
        exists = con.execute(
            "select 1 from company_personnel_images where personnel_id=? and image_name=?",
            (pid, image_name)).fetchone()
        if not exists:
            con.execute(
                "insert into company_personnel_images "
                "(personnel_id,image_name,category,image_path,mime_type,source,company_id) "
                "values (?,?,?,?,?,?,?)",
                (pid, image_name, category, rel_path,
                 "image/png" if ext.lower() == ".png" else "image/jpeg",
                 "expert_bid", COMPANY_ID))
            print("新增图片", name, label)
        if name == "徐作诚" and category == "注册测绘师":
            row = con.execute("select certs from company_personnel where id=?", (pid,)).fetchone()
            import json
            certs = json.loads(row[0] or "[]") if row and row[0] else []
            if "注册测绘师" not in certs:
                certs.append("注册测绘师")
                con.execute("update company_personnel set certs=? where id=?",
                            (json.dumps(certs, ensure_ascii=False), pid))

    # 补证书编号（人工证书上的编号）
    for name, no in CERT_NO.items():
        pid = ids.get(name)
        if pid:
            con.execute("update company_personnel set cert_no=? where id=?", (no, pid))
    # 刘倩：助理工程师
    if ids.get("刘倩"):
        con.execute("update company_personnel set title='助理工程师', certs=? where id=?",
                    ('["助理工程师"]', ids["刘倩"]))

    # ── 人工稿里出现、库中还没有的人：张红艳（授权委托书受托人）、钟亚峰（授权代表）
    #    业主 2026-11-11：「我提供给你的资料和人员都要放入数据库中」。
    for name, id_no, phone in (("张红艳", "320829198709070629", "15949194424"),
                               ("钟亚锋", "320882199111205636", "")):
        con.execute(
            "insert into company_personnel "
            "(name, role, id_no, phone, company_id, updated_at) "
            "values (?,?,?,?,?,datetime('now')) "
            "on conflict(name, company_id) do update set "
            "id_no=excluded.id_no, "
            "phone=coalesce(nullif(excluded.phone,''), company_personnel.phone), "
            "updated_at=excluded.updated_at",
            (name, "项目组成员", id_no, phone, COMPANY_ID))
    # 法人陈春的身份证号（取自身份证扫描件）
    con.execute("update company_personnel set id_no='320828197512040011' "
                "where company_id=? and name='陈春'", (COMPANY_ID,))

    # ── 身份证正反面 → asset_images（投标授权书要用；已有同名图就跳过）
    for rid, iname in (("rId26", "陈春 身份证正面（人像面）"),
                       ("rId27", "陈春 身份证反面（国徽面）"),
                       ("rId28", "钟亚峰 身份证正面（人像面）"),
                       ("rId29", "钟亚峰 身份证反面（国徽面）")):
        if con.execute("select 1 from asset_images where image_name=?",
                       (iname,)).fetchone():
            continue
        part = rels[rid].target_part
        con.execute(
            "insert into asset_images "
            "(image_name, category, image_data, mime_type, source_file, is_active) "
            "values (?,?,?,?,?,1)",
            (iname, "证件", base64.b64encode(part.blob).decode(),
             "image/png", "expert_bid"))
        print("新增身份证图", iname)

    con.commit()
    print("完成：证书图片、身份证图与人员已入库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
