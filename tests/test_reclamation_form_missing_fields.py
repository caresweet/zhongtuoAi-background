# -*- coding: utf-8 -*-
"""报告表「没写」的那几格：能补问的就写进表，补不到的标「待补充」。

🔴 业主 2026-11-03：报告表里发证机关/联系人/电话、人员职称/专业全是空白。
   公司档案没有发证机关/等级/电话；人员库有些行只有姓名（证书扫描件入库）。
   这些都在生成前的「数据核对」里补问，答案写进 filled_data。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.reclamation_report_form import collect_form_data   # noqa: E402


def test_person_title_and_major_use_asked_values():
    filled = {"_person_title_刘利伟": "高级工程师", "_person_major_刘利伟": "土地管理"}
    d = collect_form_data(filled, {"name": "江苏众拓"}, [{"姓名": "刘利伟"}])
    assert d["人员行"][0][:3] == ["刘利伟", "高级工程师", "土地管理"]


def test_person_without_title_is_marked_placeholder():
    d = collect_form_data({}, {"name": "江苏众拓"}, [{"姓名": "张三"}])
    assert d["人员行"][0][1:3] == ["待补充", "待补充"]


def test_person_title_from_personnel_store_wins_over_placeholder():
    d = collect_form_data({}, {"name": "江苏众拓"},
                          [{"姓名": "李玉涛", "职称/职务": "高级工程师", "专业": "林业"}])
    assert d["人员行"][0][:3] == ["李玉涛", "高级工程师", "林业"]


def test_company_fields_use_asked_values():
    filled = {"qualification_authority": "江苏省自然资源厅",
              "qualification_level": "甲级",
              "company_contact": "李四",
              "company_phone": "0517-88888888"}
    d = collect_form_data(filled, {"name": "江苏众拓"}, [])
    assert d["发证机关"] == "江苏省自然资源厅"
    assert d["资质等级"] == "甲级"
    assert d["编制单位联系人"] == "李四"
    assert d["编制单位电话"] == "0517-88888888"


def test_company_fields_fall_back_to_placeholder():
    d = collect_form_data({}, {"name": "江苏众拓"}, [])
    assert d["发证机关"] == "待补充"
    assert d["资质等级"] == "待补充"
    assert d["编制单位联系人"] == "待补充"
    assert d["编制单位电话"] == "待补充"


def test_personnel_capped_at_three_rows():
    """范文报告表只有 3 行 —— 复垦最多 3 人，多的不渲染。"""
    people = [{"姓名": f"人员{i}", "职称/职务": "工程师", "专业": "土地"} for i in range(5)]
    d = collect_form_data({}, {"name": "江苏众拓"}, people)
    assert [r[0] for r in d["人员行"]] == ["人员0", "人员1", "人员2"]
