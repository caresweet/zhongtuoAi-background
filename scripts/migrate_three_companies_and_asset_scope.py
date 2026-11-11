# -*- coding: utf-8 -*-
"""三家公司 + 档案图片按公司隔离（幂等）。

业主 2026-11-11：「每次会从三家公司挑选一家去投标」「人员不要弄错弄混」。

做两件事：
1. `auth.db.companies` 补齐第三家 **江苏众拓土地房地产资产评估造价咨询有限公司**
   （众拓评估）；顺手补测绘公司的统一社会信用代码。
2. `knowledge_base.db.asset_images` 加 `company_id` 列，并把现存图片全部划给
   **江苏众拓测绘有限公司（id=4）** —— 现存图片都来自「众拓测绘有关资料」目录。
   之后扫描 `_asset_images` 一律按当前投标公司过滤，绝不跨公司取图。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
AUTH = BACKEND / "data" / "auth.db"
KB = BACKEND / "data" / "knowledge_base.db"

EVAL_COMPANY = {
    "name": "江苏众拓土地房地产资产评估造价咨询有限公司",
    "short_name": "众拓评估",
    "address": "淮安经济技术开发区迎宾大道39号联东U谷11号101室",
    "leader_name": "陈春",
    "leader_title": "法定代表人",
    "business_license_no": "913208007986173321",
    "qualification_level": "乙级预评价",
    "qualification_no": "乙预112026010396",
    "qualification_authority": "江苏省工程咨询协会",
}
SURVEY_COMPANY_ID = 4


def main() -> int:
    # ── 1. 公司 ──
    c = sqlite3.connect(str(AUTH))
    c.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(companies)")}
        exists = c.execute("select id from companies where name=?",
                           (EVAL_COMPANY["name"],)).fetchone()
        if exists:
            print("评估公司已存在 id=", exists[0])
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fields = [k for k in EVAL_COMPANY if k in cols]
            c.execute(
                f"insert into companies (is_internal, status, created_at, updated_at, "
                f"{', '.join(fields)}) values (1,'active',?,?,"
                f"{','.join('?' * len(fields))})",
                [now, now] + [EVAL_COMPANY[k] for k in fields])
            print("新增评估公司 id=", c.execute("select last_insert_rowid()").fetchone()[0])
        # 测绘的统一社会信用代码（取自单位权益记录单）
        c.execute("update companies set business_license_no='9132089155376844XP' "
                  "where id=? and (business_license_no is null or business_license_no='')",
                  (SURVEY_COMPANY_ID,))
        c.commit()
    finally:
        c.close()

    # ── 2. 档案图片按公司隔离 ──
    k = sqlite3.connect(str(KB))
    try:
        cols = {r[1] for r in k.execute("PRAGMA table_info(asset_images)")}
        if "company_id" not in cols:
            k.execute("ALTER TABLE asset_images ADD COLUMN company_id INTEGER")
            print("asset_images 加 company_id 列")
        n = k.execute("update asset_images set company_id=? where company_id is null",
                      (SURVEY_COMPANY_ID,)).rowcount
        k.execute("CREATE INDEX IF NOT EXISTS idx_ai_company ON asset_images(company_id)")
        k.commit()
        print(f"档案图片归属测绘公司：{n} 张")
    finally:
        k.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
