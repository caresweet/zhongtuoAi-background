#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板库：补地区字段、修正领域、登记复垦/投标模板（幂等，可重复跑）。

背景（业主 2026-10-30）：
  「复垦和投标文件都要有模板 …… 稳评的模板和所有的模板一样，都是不同地区
   不同模板，有的都会在官方发布的文档或者要求中」

现状问题：
  · templates 表没有地区列，稳评模板里「洪泽」「金湖」两版混在一起，生成时只能
    靠装配器的硬编码排序（洪泽优先）挑，换个地方就挑错；
  · 三条招标投标文档（第3批次林地报批 / 土地征收报批招标 / 投标文件）domain
    被写成了 stability —— 按域查永远查不到；
  · 复垦方案一份模板都没有。

本脚本做三件事（都可重复执行）：
  1. 给 templates 加 region 列（应用启动时也会加，这里兜底）；
  2. 按名称补齐地区、按分类修正领域；
  3. 用 --import-file 把一份模板 docx 登记进库（复制到 storage/templates）。

用法：
    # 只做 1+2（修现有数据）
    python scripts/register_domain_templates.py --db data/knowledge_base.db --storage storage

    # 登记一份复垦模板
    python scripts/register_domain_templates.py --db data/knowledge_base.db --storage storage \
        --import-file "/path/江苏盐业…土地复垦方案.docx" \
        --name "盐业井神复垦方案模板" --domain reclamation --region "" --category 复垦方案
"""
import argparse
import hashlib
import shutil
import sqlite3
from pathlib import Path

# 名称里出现这些地区词 → 补到 region（长的在前，避免「淮安」吃掉「淮安区」）
REGION_BY_NAME = [
    ("金湖", "金湖"),
    ("洪泽", "洪泽"),
    ("盱眙", "盱眙"),
    ("涟水", "涟水"),
    ("淮安北", "淮安"),
    ("淮安", "淮安"),
    ("通港路", "淮安"),
]

# 分类是招标投标、但 domain 还挂在稳评上的老数据 → 改到 bidding
BIDDING_CATEGORY = ("招标投标", "投标文件", "招标公告")

# 这些模板要真正参与生成，必须启用（以前是当范文存着、is_active=0）
ACTIVATE_BY_NAME = [("投标文件", "bidding")]


def ensure_region_column(db: sqlite3.Connection):
    cols = {r[1] for r in db.execute("PRAGMA table_info(templates)")}
    if "region" not in cols:
        db.execute("ALTER TABLE templates ADD COLUMN region VARCHAR(50) DEFAULT ''")
        print("  + 已添加 region 列")


def fix_existing(db: sqlite3.Connection):
    rows = db.execute("select id, name, category, domain, coalesce(region,''), is_active from templates").fetchall()
    for tid, name, category, domain, region, active in rows:
        new_region = region
        if not new_region:
            for kw, reg in REGION_BY_NAME:
                if kw in (name or ""):
                    new_region = reg
                    break
        new_domain = domain
        if category in BIDDING_CATEGORY and domain != "bidding":
            new_domain = "bidding"
        new_active = active
        for _nm, _dm in ACTIVATE_BY_NAME:
            if (name or "").strip() == _nm and new_domain == _dm:
                new_active = 1
        if new_region != region or new_domain != domain or new_active != active:
            db.execute(
                "update templates set region=?, domain=?, is_active=? where id=?",
                (new_region, new_domain, new_active, tid),
            )
            print(f"  ~ {tid} {name}: domain {domain}→{new_domain}, "
                  f"region {region or '空'}→{new_region or '空'}, active {active}→{new_active}")


def import_template(db, storage: Path, src: Path, name: str, domain: str, region: str, category: str):
    if not src.exists():
        raise SystemExit(f"文件不存在：{src}")
    tdir = storage / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(src.read_bytes()).hexdigest()
    dst_name = f"{digest}{src.suffix.lower()}"
    dst = tdir / dst_name
    if not dst.exists():
        shutil.copy2(src, dst)
    rel = f"templates/{dst_name}"

    old = db.execute("select id from templates where name=? and domain=?", (name, domain)).fetchone()
    if old:
        db.execute(
            "update templates set template_file_path=?, region=?, category=?, is_active=1, "
            "file_size=?, analysis_status='pending' where id=?",
            (rel, region, category, dst.stat().st_size, old[0]),
        )
        print(f"  ~ 更新已有模板 #{old[0]}《{name}》（{rel}）")
        return old[0]

    cur = db.execute(
        "insert into templates (name, description, category, domain, region, template_file_path, "
        "analysis_status, file_size, is_active, created_at, updated_at) "
        "values (?,?,?,?,?,?, 'pending', ?, 1, datetime('now','localtime'), datetime('now','localtime'))",
        (name, "按地区登记的官方模板", category, domain, region, rel, dst.stat().st_size),
    )
    print(f"  + 新增模板 #{cur.lastrowid}《{name}》{domain}/{region or '通用'}（{rel}）")
    return cur.lastrowid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--storage", required=True)
    ap.add_argument("--import-file")
    ap.add_argument("--name")
    ap.add_argument("--domain")
    ap.add_argument("--region", default="")
    ap.add_argument("--category", default="通用")
    a = ap.parse_args()

    db = sqlite3.connect(a.db)
    try:
        print("1) 字段检查")
        ensure_region_column(db)
        print("2) 修正既有数据")
        fix_existing(db)
        if a.import_file:
            if not (a.name and a.domain):
                raise SystemExit("--import-file 需要同时给 --name 和 --domain")
            print("3) 登记模板文件")
            import_template(db, Path(a.storage), Path(a.import_file),
                            a.name, a.domain, a.region or "", a.category)
        db.commit()
    finally:
        db.close()
    print("完成")


if __name__ == "__main__":
    main()
