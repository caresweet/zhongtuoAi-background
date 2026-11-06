# -*- coding: utf-8 -*-
"""把人工投标模板（297 页 PDF）里的证书/证明扫描件导入知识库。

来源：`~/Downloads/众拓测绘-投标文件.pdf`
  · 人员证书：第 233–239 页 → company_personnel_images（并补齐 3 名人员）
  · 社保权益记录单：第 240–241 页 → asset_images
  · 仪器设备发票/照片：第 242–254 页 → asset_images
  · 单位名称变更材料：第 256–257 页 → asset_images
  · 体系认证证书：第 258–261 页 → asset_images
  · 类似业绩合同：第 262–296 页 → asset_images（按 5 个业绩分段）

用法：python scripts/import_template_certificates.py [--pdf 路径]
"""
from __future__ import annotations

import argparse
import base64
import io
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

KB = BACKEND / "data" / "knowledge_base.db"
PERSON_DIR = BACKEND / "storage" / "images" / "person_certs"
SOURCE = "众拓测绘-投标文件.pdf（人工模板）"


def _photo(pix, max_w=1500, quality=82):
    """Pixmap → JPEG bytes（限宽、压缩，控制数据库体积）。"""
    import fitz
    from PIL import Image
    if pix.n - pix.alpha >= 4:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), im.width, im.height


def _page_images(doc, page_no):
    """第 page_no 页（1 基）的嵌入图 → [(jpeg_bytes,w,h)]；无图则整页渲染。"""
    import fitz
    page = doc[page_no - 1]
    out = []
    for img in page.get_images(full=True):
        pix = fitz.Pixmap(doc, img[0])
        out.append(_photo(pix))
    if not out:
        pix = page.get_pixmap(dpi=150)
        out.append(_photo(pix))
    return out


def _ensure_person(conn, name, title, major, cert_no, certs):
    row = conn.execute(
        "SELECT id FROM company_personnel WHERE name=? AND company_id=4", (name,)
    ).fetchone()
    if row:
        pid = row[0]
        conn.execute(
            "UPDATE company_personnel SET title=COALESCE(NULLIF(title,''),?), "
            "major=COALESCE(NULLIF(major,''),?), cert_no=COALESCE(NULLIF(cert_no,''),?) "
            "WHERE id=?", (title, major, cert_no, pid))
        return pid
    cur = conn.execute(
        "INSERT INTO company_personnel (name,gender,age,education,major,title,role,certs,"
        "has_wenshen_cert,sort_order,company_id,updated_at,cert_no) "
        "VALUES (?,?,?,?,?,?,?,?,0,99,4,?,?)",
        (name, "男", "", "", major, title, "项目组成员",
         __import__("json").dumps(certs, ensure_ascii=False),
         datetime.now().isoformat(timespec="seconds"), cert_no))
    return cur.lastrowid


def _save_person_image(conn, pid, name, image_name, category, data):
    PERSON_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in f"{pid}_{image_name}" if ch not in '\\/:*?"<>|')[:80]
    path = PERSON_DIR / f"{safe}.jpg"
    path.write_bytes(data)
    rel = str(path.relative_to(BACKEND))
    row = conn.execute(
        "SELECT id,image_path FROM company_personnel_images "
        "WHERE personnel_id=? AND image_name=?", (pid, image_name)).fetchone()
    if row:
        conn.execute("UPDATE company_personnel_images SET image_path=?,category=?,company_id=4,"
                     "source=? WHERE id=?", (rel, category, SOURCE, row[0]))
    else:
        conn.execute(
            "INSERT INTO company_personnel_images (personnel_id,image_name,category,"
            "image_path,mime_type,source,company_id,updated_at) "
            "VALUES (?,?,?,?,?,?,4,?)",
            (pid, image_name, category, rel, "image/jpeg", SOURCE,
             datetime.now().isoformat(timespec="seconds")))


def _save_asset(conn, image_name, category, data, w, h):
    b64 = base64.b64encode(data).decode()
    row = conn.execute("SELECT id FROM asset_images WHERE image_name=?",
                       (image_name,)).fetchone()
    if row:
        conn.execute(
            "UPDATE asset_images SET image_data=?,mime_type='image/jpeg',width_px=?,"
            "height_px=?,category=?,is_active=1,source_file=? WHERE id=?",
            (b64, w, h, category, SOURCE, row[0]))
        return "updated"
    conn.execute(
        "INSERT INTO asset_images (image_name,category,image_data,mime_type,width_px,"
        "height_px,source_file,source_template_id,search_keywords,created_at,is_active) "
        "VALUES (?,?,?,'image/jpeg',?,?,?,NULL,'',?,1)",
        (image_name, category, b64, w, h, SOURCE,
         datetime.now().isoformat(timespec="seconds")))
    return "added"


# ── 页码 → 内容 ────────────────────────────────────────────────
PERSONNEL = {
    233: ("徐作诚", "江苏省高级专业技术资格证书", "职称证书"),
    234: ("徐作诚", "注册测绘师证书", "执业资格证书"),
    235: ("陈春", "高级工程师（土地）资格证书", "职称证书"),
    236: ("汪菲菲", "高级城市规划师资格证书", "职称证书"),
    237: ("甘文锋", "浙江省高级专业技术职务任职资格证书", "职称证书"),
    238: ("卢烈", "南京市中级专业技术资格证书", "职称证书"),
    239: ("王佩", "江苏省中级专业技术资格证书", "职称证书"),
}

EQUIPMENT = {
    242: ("仪器设备-全站仪-1", 1), 243: ("仪器设备-全站仪-2", 1),
    244: ("仪器设备-全站仪-3", 1), 245: ("仪器设备-全站仪-4", 1),
    246: ("仪器设备-无人机", 2),
    247: ("仪器设备-CASS成图软件-1", 1), 248: ("仪器设备-CASS成图软件-2", 1),
    249: ("仪器设备-CASS成图软件-3", 1), 250: ("仪器设备-CASS成图软件-4", 1),
    251: ("仪器设备-绘图仪", 2),
    252: ("仪器设备-GNSS接收器-1", 1),
    253: ("仪器设备-GNSS接收器-2", 2),
    254: ("仪器设备-GNSS接收器-3", 1),
}

SYSTEM_CERTS = {
    258: "质量管理体系认证",
    259: "环境管理体系认证",
    260: "职业健康安全管理体系认证",
    261: "测量管理体系认证证书",
}

PERF_SECTIONS = [
    (262, 270, "1.2024年食品科技产业园部分地块征地报批综合服务项目"),
    (271, 275, "2.井神绿化通道项目征地服务"),
    (276, 282, "3.张兴矿区采输卤扩建项目征地服务"),
    (283, 289, "4.世坤迪地块征地报批（含稳评及林地报批）服务"),
    (290, 296, "5.大王庄路网接线提升改造工程（颜莲路、拂晓路）用地报批咨询服务采购项目"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(Path.home() / "Downloads" / "众拓测绘-投标文件.pdf"))
    args = ap.parse_args()
    import fitz

    doc = fitz.open(args.pdf)
    conn = sqlite3.connect(str(KB))
    stats = {"person": 0, "asset": 0}

    # 人员：先补齐 3 人
    pids = {}
    for name, title, major, no, certs in (
        ("甘文锋", "高级工程师", "测绘", "G3300164679", ["高级职称", "高级工程师——测绘"]),
        ("卢烈", "工程师", "国土资源工程", "NJZ20210210026", ["中级职称"]),
        ("王佩", "规划师", "国土空间规划", "233201000213320243", ["中级职称"]),
    ):
        pids[name] = _ensure_person(conn, name, title, major, no, certs)
    for row in conn.execute("SELECT name,id FROM company_personnel WHERE company_id=4"):
        pids.setdefault(row[0], row[1])

    for page_no, (name, image_name, category) in PERSONNEL.items():
        imgs = _page_images(doc, page_no)
        if not imgs or name not in pids:
            continue
        data, _w, _h = imgs[0]
        _save_person_image(conn, pids[name], name, f"{name} {image_name}",
                           category, data)
        stats["person"] += 1

    # 社保
    for page_no, idx in ((240, 1), (241, 2)):
        for data, w, h in _page_images(doc, page_no):
            _save_asset(conn, f"江苏省社会保险权益记录单 第{idx}页",
                        "社保纳税证明", data, w, h)
            stats["asset"] += 1

    # 仪器设备
    for page_no, (base, expect) in EQUIPMENT.items():
        imgs = _page_images(doc, page_no)
        for j, (data, w, h) in enumerate(imgs, 1):
            nm = base if len(imgs) == 1 else f"{base}-{j}"
            _save_asset(conn, nm, "仪器设备", data, w, h)
            stats["asset"] += 1

    # 单位名称变更
    for page_no, idx in ((256, 1), (257, 2)):
        for data, w, h in _page_images(doc, page_no):
            _save_asset(conn, f"单位名称变更材料-{idx}", "资质证书", data, w, h)
            stats["asset"] += 1

    # 体系认证
    for page_no, name in SYSTEM_CERTS.items():
        for data, w, h in _page_images(doc, page_no):
            _save_asset(conn, name, "资质证书", data, w, h)
            stats["asset"] += 1

    # 业绩合同
    for start, end, title in PERF_SECTIONS:
        for page_no in range(start, end + 1):
            imgs = _page_images(doc, page_no)
            for j, (data, w, h) in enumerate(imgs, 1):
                nm = f"{title} 第{page_no - start + 1}页" + (f"-{j}" if len(imgs) > 1 else "")
                _save_asset(conn, nm, "业绩证明", data, w, h)
                stats["asset"] += 1

    conn.commit()
    conn.close()
    print("IMPORTED", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
