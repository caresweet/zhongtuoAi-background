# -*- coding: utf-8 -*-
"""封面/扉页字段抽取 —— 正则均取自**实测的真实材料片段**。

背景（业主 2026-10-10）：
    「材料一定要做好，不要有漏的或者分析不出来的还去问用户」
所以这些字段只做材料抽取，抽不到就静默降级（扉页那一行不出现），不弹窗。
"""

from app.services.front_matter_extract import (
    extract_front_matter_fields, merge_front_matter,
)

# ── 实测片段（明远佳苑二期那批材料）──
KD = ("土 地 勘 测 定 界 技 术 报 告 书\n"
      "用 地 单位： 中铁建工集团有限公司\n"
      "项目用地名称： 临时工棚\n"
      "单位名称 中铁建工集团有限公司 经办人 段剑锋\n"
      "单位地址 电 话 15208624251\n"
      "主管单位 土地用途 临时用地")
APPROVAL = ("你单位《关于明远路以南地块商住项目核准的请示》…\n"
            "项目建设单位：淮安科教产业投资控股有限公司\n"
            "二、项目建设地点：位于淮安经济技术开发区创业路以北")
EIA = ("建设单位\n淮安科教产业投资控股有限公司\n法定代表人或者主要负责人\n付元超\n"
       "联系人\n唐卫国\n联系电话\n15951478630\n项目投资(万元)\n250000")


def test_project_unit_tolerates_spaced_heading():
    """🔴 勘测定界的表格头被排成「用 地 单位」—— 不放宽就漏掉这一份，
    项目单位会落到后面批复的「项目建设单位」上（样本扉页取的是勘测定界那家）。"""
    assert extract_front_matter_fields(KD)["project_unit"] == "中铁建工集团有限公司"


def test_empty_unit_address_is_not_matched():
    """「单位地址 电 话 15208624251」—— 地址栏是空的，不能把电话当地址抓走。"""
    assert "unit_address" not in extract_front_matter_fields(KD)


def test_extract_from_approval_and_eia():
    a = extract_front_matter_fields(APPROVAL)
    assert a["project_unit"] == "淮安科教产业投资控股有限公司"
    e = extract_front_matter_fields(EIA)
    assert e["project_unit"] == "淮安科教产业投资控股有限公司"
    assert e["contact_name"] == "唐卫国"
    assert e["contact_phone"] == "15951478630"


def test_missing_fields_are_absent_not_empty():
    """抽不到就**不出现在结果里** —— 不返回空串占位，扉页那一行才可能整行不出现。"""
    r = extract_front_matter_fields("一段没有任何封面字段的文字。")
    assert r == {}


def test_contact_pair_prefers_same_material():
    """🔴 成对优先：勘测定界只给电话、环保批文只给姓名时，
    「各取首个」会拼出「唐卫国 / 15208624251」两个毫不相干的人。"""
    kd = extract_front_matter_fields(KD)        # 只有 contact_phone
    eia = extract_front_matter_fields(EIA)      # 成对
    merged = merge_front_matter([kd, eia])
    assert merged["contact_name"] == "唐卫国"
    assert merged["contact_phone"] == "15951478630"   # 取成对那组的电话，不是勘测定界的
    assert merged["project_unit"] == "中铁建工集团有限公司"  # 首个产出者胜


def test_merge_first_wins_for_non_contact():
    merged = merge_front_matter([
        {"project_unit": "甲", "unit_address": "A路1号"},
        {"project_unit": "乙", "unit_address": "B路2号", "submit_date": "2026-01-01"},
    ])
    assert merged["project_unit"] == "甲"
    assert merged["unit_address"] == "A路1号"
    assert merged["submit_date"] == "2026-01-01"
