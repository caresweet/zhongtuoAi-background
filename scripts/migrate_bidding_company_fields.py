# -*- coding: utf-8 -*-
"""投标用公司/人员字段迁移：银行账户、办公电话、中小企业声明数据、身份证号/手机。

数据来源：人工投标文件《投标文件-众拓测绘-佳富村》（2026-11-11 业主指示
「将专家稿中有的公司数据都纳入公司数据库中，还有人员的一些信息」）。

用法：
    python -m scripts.migrate_bidding_company_fields
幂等：列已存在则跳过；人员按姓名更新，只补空字段。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))

COMPANY_FIELDS = {
    "bank_name": "TEXT DEFAULT ''",
    "bank_account": "TEXT DEFAULT ''",
    "office_phone": "TEXT DEFAULT ''",
    "employee_count": "TEXT DEFAULT ''",
    "annual_revenue_wan": "TEXT DEFAULT ''",
    "total_assets_wan": "TEXT DEFAULT ''",
    "enterprise_type": "TEXT DEFAULT ''",
    "postcode": "TEXT DEFAULT ''",
}

COMPANY_VALUES = {
    # 江苏众拓测绘有限公司
    4: {
        "bank_name": "交通银行股份有限公司淮安分行",
        "bank_account": "397899991010003109470",
        "office_phone": "0517-83663008",
        "employee_count": "31",
        "annual_revenue_wan": "508.95",
        "total_assets_wan": "741.68",
        "enterprise_type": "小型企业",
        "postcode": "223005",
    },
}

PERSON_FIELDS = {"id_no": "TEXT DEFAULT ''", "phone": "TEXT DEFAULT ''"}
PERSON_VALUES = {
    ("徐作诚", 4): {"id_no": "320325198102196513", "phone": "15005184030"},
    ("王明", 4): {"id_no": "320821199205180713", "phone": "18252355210"},
    ("郝怡佳", 4): {"id_no": "320826198604185631", "phone": "15162929434"},
}


def _cols(con, table: str) -> set:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    auth = sqlite3.connect("data/auth.db")
    cur = _cols(auth, "companies")
    for col, ddl in COMPANY_FIELDS.items():
        if col not in cur:
            auth.execute(f"ALTER TABLE companies ADD COLUMN {col} {ddl}")
            print("companies +", col)
    for cid, vals in COMPANY_VALUES.items():
        sets = ", ".join(f"{k}=?" for k in vals)
        auth.execute(f"UPDATE companies SET {sets} WHERE id=?", (*vals.values(), cid))
    auth.commit()
    print("companies 数据已写入")

    kb = sqlite3.connect("data/knowledge_base.db")
    cols = _cols(kb, "company_personnel")
    for col, ddl in PERSON_FIELDS.items():
        if col not in cols:
            kb.execute(f"ALTER TABLE company_personnel ADD COLUMN {col} {ddl}")
            print("company_personnel +", col)
    for (name, cid), vals in PERSON_VALUES.items():
        sets = ", ".join(f"{k}=?" for k in vals)
        kb.execute(f"UPDATE company_personnel SET {sets} WHERE name=? AND company_id=?",
                   (*vals.values(), name, cid))
    # 人工稿里有、库里没有的成员：刘倩
    has = kb.execute(
        "select count(*) from company_personnel where name='刘倩' and company_id=4").fetchone()[0]
    if not has:
        kb.execute(
            "insert into company_personnel (name,gender,age,education,major,title,role,certs,"
            "has_wenshen_cert,sort_order,company_id,id_no,phone) "
            "values ('刘倩','女','26','本科','测绘','','项目组成员','[]',0,90,4,?,?)",
            ("320882200011115022", "18605172295"))
        print("新增人员 刘倩")
    kb.commit()
    print("人员数据已写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
