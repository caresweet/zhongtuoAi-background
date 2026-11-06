# -*- coding: utf-8 -*-
"""把「公司资质 + 主要编制人员」导入数据库（模板 / 人员统计表）。

用法（在 backend 目录）：

    # 1) 从复垦范文模板导入（公司资质进同名公司，人员补到所有内部公司）
    .venv/bin/python -m scripts.import_personnel_profiles --templates

    # 2) 业主后期给的人员统计表（xlsx/csv：姓名 / 职称/职务 / 专业 / 单位）
    .venv/bin/python -m scripts.import_personnel_profiles \
        --file ~/Downloads/人员统计.xlsx --company-id 1

    # 3) 指定单个模板文件
    .venv/bin/python -m scripts.import_personnel_profiles --template 路径.docx

入库规则：只补空，不覆盖已有人工维护的值。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _active_reclamation_templates(knowledge_db: Path, storage_dir: Path) -> list:
    if not knowledge_db.exists():
        return []
    conn = sqlite3.connect(str(knowledge_db))
    try:
        rows = conn.execute(
            "SELECT template_file_path FROM templates "
            "WHERE is_active = 1 AND lower(domain) = 'reclamation'").fetchall()
    finally:
        conn.close()
    out = []
    for (rel,) in rows:
        if not rel:
            continue
        p = Path(str(rel))
        p = p if p.is_absolute() else storage_dir / p
        if p.exists():
            out.append(str(p))
        else:
            print(f"⚠️ 模板文件缺失：{p}", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="导入公司资质与主要编制人员")
    ap.add_argument("--templates", action="store_true", help="从所有启用的复垦模板导入")
    ap.add_argument("--template", action="append", default=[], help="指定模板文件，可重复")
    ap.add_argument("--file", help="人员统计表（xlsx/xlsm/csv）")
    ap.add_argument("--company-id", type=int, default=0, help="统计表导入到哪家公司")
    ap.add_argument("--storage-dir", default="")
    ap.add_argument("--auth-db", default="")
    ap.add_argument("--knowledge-db", default="")
    args = ap.parse_args()

    from app.config import settings
    storage_dir = Path(args.storage_dir) if args.storage_dir else Path(settings.STORAGE_DIR)
    auth_db = Path(args.auth_db) if args.auth_db else Path(settings.DATA_DIR) / "auth.db"
    knowledge_db = (Path(args.knowledge_db) if args.knowledge_db
                    else Path(settings.DATA_DIR) / "knowledge_base.db")

    from app.services import personnel_profile_import as imp

    summary = {}
    template_paths = list(args.template or [])
    if args.templates:
        template_paths += _active_reclamation_templates(knowledge_db, storage_dir)
    if template_paths:
        summary["templates"] = imp.import_from_templates(
            str(storage_dir), str(auth_db), str(knowledge_db), template_paths)
    if args.file:
        if not args.company_id:
            print("✗ --file 必须同时给 --company-id", file=sys.stderr)
            return 2
        summary["stats"] = imp.import_stats_file(
            args.file, args.company_id, str(knowledge_db))
    if not summary:
        ap.print_help()
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
