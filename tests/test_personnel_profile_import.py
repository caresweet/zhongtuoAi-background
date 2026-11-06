# -*- coding: utf-8 -*-
"""人员资质：从范文模板 / 统计表导入数据库（只补空，不覆盖）。

🔴 业主 2026-11-03：报告表的发证机关/等级/联系人/电话、人员职称/专业，
   不要每次弹窗问 —— 从模板或后续统计表导入数据库，生成时查库。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import personnel_profile_import as imp   # noqa: E402

_TEMPLATE = BACKEND / "storage/templates/b810630b5b489550cbec057745e2eab8.pdf"


def test_extract_company_and_personnel_from_template_pdf():
    if not _TEMPLATE.exists():
        import pytest
        pytest.skip("模板文件不在本仓库布局内")
    d = imp.extract_from_template(str(_TEMPLATE))
    assert d.get("name") == "江苏众拓测绘有限公司"
    assert d.get("qualification_level") == "二级"
    assert d.get("qualification_authority") == "江苏省土地学会"
    assert d.get("qualification_no") == "Z2064"
    assert d.get("contact") == "刘利伟"
    assert d.get("phone") == "13770380120"
    people = {p["name"]: p for p in d.get("personnel") or []}
    assert people["陈春"]["title"] == "高级工程师"
    assert people["陈春"]["major"] == "土地资源管理"
    assert people["刘利伟"]["title"] == "助理工程师"
    assert people["刘利伟"]["major"] == "地理信息科学"


def _cp_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE company_personnel (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT, major TEXT, title TEXT, role TEXT, company_id INTEGER)")
    return conn


def test_upsert_personnel_only_fills_empty(tmp_path):
    db = tmp_path / "kb.db"
    conn = _cp_db(db)
    conn.execute("INSERT INTO company_personnel (name,major,title,role,company_id) "
                 "VALUES ('刘利伟','','','项目组成员',1)")
    conn.execute("INSERT INTO company_personnel (name,major,title,role,company_id) "
                 "VALUES ('陈春','土地','高级工程师','项目组成员',1)")
    conn.commit(); conn.close()

    r = imp.upsert_personnel(str(db), 1, [
        {"name": "刘利伟", "title": "助理工程师", "major": "地理信息科学"},
        {"name": "陈春", "title": "教授级高工", "major": "规划"},   # 已有值，不得覆盖
        {"name": "夏杰", "title": "工程师", "major": "测绘"},
    ])
    assert r["inserted"] == 1 and r["updated"] == 1

    conn = sqlite3.connect(str(db))
    rows = {n: (t, m) for n, t, m in conn.execute(
        "SELECT name,title,major FROM company_personnel WHERE company_id=1")}
    conn.close()
    assert rows["刘利伟"] == ("助理工程师", "地理信息科学")
    assert rows["陈春"] == ("高级工程师", "土地")          # 原值保留
    assert rows["夏杰"] == ("工程师", "测绘")


def test_read_stats_file_csv(tmp_path):
    p = tmp_path / "人员统计.csv"
    p.write_text("姓名,职称/职务,专业,单位\n刘利伟,助理工程师,地理信息科学,江苏众拓\n", encoding="utf-8")
    people = imp.read_stats_file(str(p))
    assert people == [{"name": "刘利伟", "title": "助理工程师",
                       "major": "地理信息科学", "unit": "江苏众拓"}]


def test_upsert_company_profile_only_fills_empty(tmp_path):
    db = tmp_path / "auth.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, "
                 "qualification_text TEXT, qualification_level TEXT, "
                 "qualification_authority TEXT, qualification_no TEXT, "
                 "contact TEXT, phone TEXT, leader_name TEXT)")
    conn.execute("INSERT INTO companies (id,name,qualification_text) VALUES (4,'众拓测绘','')")
    conn.commit(); conn.close()
    imp.upsert_company_profile(str(db), 4, {
        "qualification_text": "土地综合整治项目",
        "qualification_level": "二级",
        "qualification_authority": "江苏省土地学会",
        "qualification_no": "Z2064",
        "contact": "刘利伟",
        "phone": "13770380120",
    })
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT qualification_text,qualification_level,"
                       "qualification_authority,qualification_no,contact,phone "
                       "FROM companies WHERE id=4").fetchone()
    conn.close()
    assert row == ("土地综合整治项目", "二级", "江苏省土地学会", "Z2064",
                   "刘利伟", "13770380120")
