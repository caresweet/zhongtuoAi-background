# -*- coding: utf-8 -*-
"""表1-1 临时用地功能分区：名称与面积由用户提供（与甲方沟通结果）。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.reclamation_zones import (      # noqa: E402
    parse_zones, table_1_1, zones_from_filled,
)


def test_parse_zones_with_units_and_separators():
    text = ("井场 0.2310公顷；施工场站 1.1950；"
            "施工便道:0.5788公顷\n表土堆放区，0.3200")
    z = parse_zones(text)
    assert [x["name"] for x in z] == ["井场", "施工场站", "施工便道", "表土堆放区"]
    assert abs(sum(x["area_m2"] for x in z) - 23248) < 1
    assert z[0]["method"] == "挖损和压占" and z[0]["degree"] == "重度"
    assert z[-1]["degree"] == "中度"


def test_parse_zones_square_meters():
    z = parse_zones("井场 2310平方米；施工场站 11950㎡")
    assert abs(z[0]["area_m2"] - 2310) < 0.1


def test_table_1_1_rows_and_total():
    filled = {"area_m2": "23248", "land_use_zones":
              "井场 0.2310公顷；施工场站 1.1950公顷；"
              "施工便道 0.5788公顷；表土堆放区 0.3200公顷"}
    t = table_1_1(filled)
    assert t["caption"] == "表1-1 临时用地拟损毁土地情况表"
    assert len(t["rows"]) == 5
    assert t["rows"][0] == ["井场", "0.2310", "挖损和压占", "重度", "待复垦"]
    assert t["rows"][-1] == ["合计", "2.3248", "", "", ""]


def test_table_1_1_without_zones_is_none():
    assert table_1_1({"area_m2": "23248"}) is None


def test_fix_derives_hard_area_from_zones():
    from app.services.field_fixer import fix_reclamation_fields
    filled = {"area_m2": "23248", "land_use_zones":
              "井场 0.2310公顷；施工场站 1.1950公顷；"
              "施工便道 0.5788公顷；表土堆放区 0.3200公顷"}
    fix_reclamation_fields(filled, [], title="")
    assert filled["hard_area_m2"] == "20048.00"
    assert len(zones_from_filled(filled)) == 4


def test_tables_4_7_and_4_10_use_user_zones():
    from app.services.reclamation_ch4_tables import table_4_7, table_4_10
    filled = {"area_m2": "23248",
              "land_classification": {"合计_m2": 23248, "rows": [
                  {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 22215}]},
              "land_use_zones":
              "井场 0.2310公顷 挖损和压占 重度 水田；"
              "施工场站 1.1950公顷 压占 重度 水田、农村道路、沟渠；"
              "施工便道 0.5788公顷 压占 重度；"
              "表土堆放区 0.3200公顷 压占 中度 水田"}
    t7 = table_4_7(filled)
    assert [r[1] for r in t7["rows"][:-1]] == ["井场", "施工场站", "施工便道", "表土堆放区"]
    assert t7["rows"][0][2] == "挖损和压占"
    assert t7["rows"][-1][3] == "2.3248"
    t10 = table_4_10(filled)
    assert [r[1] for r in t10["rows"][:-1]] == ["井场", "施工场站", "施工便道", "表土堆放区"]
    assert t10["rows"][1][2] == "水田、农村道路、沟渠"   # 占用原土地类型（现状）
    assert t10["rows"][1][5] == "水田"                   # 复垦方向（一个地类）
    assert t10["rows"][-1][3] == "2.3248"


def test_tables_4_7_and_4_10_absent_without_zones():
    from app.services.reclamation_ch4_tables import table_4_7, table_4_10
    f = {"area_m2": "23248",
         "land_classification": {"合计_m2": 23248, "rows": [
             {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 22215}]}}
    assert table_4_7(f) is None
    assert table_4_10(f) is None
