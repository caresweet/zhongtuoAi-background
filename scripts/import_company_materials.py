# -*- coding: utf-8 -*-
"""把电脑里找到的三家公司证照导入 `asset_images`（按 company_id 隔离，幂等）。

来源：
  · 众拓评估营业执照 / 房地产估价机构备案证书 / 土地估价机构备案函
      ← 微信文件《众拓评估简介 - 2026.5.31.docx》内嵌图（第 17/16/14 张）
  · 众拓代理营业执照 ← ~/Downloads/公司营业执照.png
  · 众拓测绘甲级测绘资质 ← ~/Downloads/众拓测绘有关资料/甲级测绘资质.jpg
"""
from __future__ import annotations

import base64
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
KB = BACKEND / "data" / "knowledge_base.db"
WX = Path("/Users/mac/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
          "xwechat_files/wxid_5j2htgpg1xxk21_c913/msg/file/2026-06")
EVAL_INTRO = WX / "众拓评估简介 - 2026.5.31.docx"
AGENCY_LICENSE = Path.home() / "Downloads" / "公司营业执照.png"
SURVEY_A_JIA = Path.home() / "Downloads" / "众拓测绘有关资料" / "甲级测绘资质.jpg"


def _intro_images(docx_path: Path):
    from docx import Document
    d = Document(str(docx_path))
    out = []
    for rel in d.part.rels.values():
        if "image" in rel.reltype:
            out.append(rel.target_part.blob)
    return out


def _insert(con, company_id: int, name: str, blob: bytes, mime: str,
            category: str, source: str):
    row = con.execute(
        "select id from asset_images where image_name=? and company_id=?",
        (name, company_id)).fetchone()
    if row:
        print("已存在", company_id, name)
        return
    con.execute(
        "insert into asset_images (image_name, category, image_data, mime_type, "
        "company_id, source_file, is_active) values (?,?,?,?,?,?,1)",
        (name, category, base64.b64encode(blob).decode(), mime, company_id, source))
    print("导入", company_id, name)


def main() -> int:
    con = sqlite3.connect(str(KB))
    try:
        if EVAL_INTRO.exists():
            imgs = _intro_images(EVAL_INTRO)
            picks = [(17, "1.营业执照", "证照"),
                     (16, "资质证书-房地产估价机构备案证书", "资质"),
                     (14, "资质证书-土地估价机构备案函", "资质")]
            for idx, name, cat in picks:
                if idx <= len(imgs):
                    _insert(con, 5, name, imgs[idx - 1], "image/png", cat,
                            EVAL_INTRO.name)
        if AGENCY_LICENSE.exists():
            _insert(con, 1, "1.营业执照", AGENCY_LICENSE.read_bytes(),
                    "image/png", "证照", AGENCY_LICENSE.name)
        if SURVEY_A_JIA.exists():
            _insert(con, 4, "资质证书-甲级测绘资质", SURVEY_A_JIA.read_bytes(),
                    "image/jpeg", "资质", SURVEY_A_JIA.name)
        con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
