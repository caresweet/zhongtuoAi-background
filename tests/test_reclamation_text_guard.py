# -*- coding: utf-8 -*-
"""复垦正文收口：串项目、面积/土方口径、单位（专家评审 2026-11-09）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LAND = [
    {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 14200},
    {"一级地类": "耕地", "二级地类": "旱地", "面积_m2": 8600},
    {"一级地类": "园地", "二级地类": "果园", "面积_m2": 3100},
    {"一级地类": "水域及水利设施用地", "二级地类": "坑塘水面", "面积_m2": 2400},
    {"一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 2700},
    {"一级地类": "交通运输用地", "二级地类": "农村道路", "面积_m2": 1600},
]
FILLED = {
    "project_name": "淮安市淮安区博里镇农民集中居住区（博里花园）建设工程临时用地",
    "location": "淮安市淮安区博里镇博里村、南涧村",
    "land_classification": {"rows": LAND, "合计_m2": 32600},
    "topsoil_thickness": 30,
    "area_m2": 32600,
    "_land_use_zones": [
        {"name": "施工场站", "area_m2": 11800, "reclaim_direction": "水田",
         "land_types": "水田、旱地"},
        {"name": "施工营地", "area_m2": 5500, "reclaim_direction": "旱地",
         "land_types": "旱地、果园"},
        {"name": "表土堆放区", "area_m2": 3300, "reclaim_direction": "水田",
         "land_types": "水田、坑塘水面"},
    ],
}


def test_topsoil_facts_uses_strip_area_not_farmland():
    from app.services.reclamation_text_guard import topsoil_facts

    f = topsoil_facts(FILLED)
    assert round(f["strip_ha"], 4) == 2.59       # 水田+旱地+果园
    assert round(f["volume_m3"], 1) == 7770.0    # 2.59 × 0.3 × 10000
    assert round(f["farm_ha"], 4) == 2.28        # 仅耕地（不能拿来当剥离面积）
    assert f["directions"] == ["水田", "旱地"]


def test_foreign_project_paragraph_is_removed():
    from app.services.reclamation_text_guard import apply_guards

    md = ("4.1.1 土地损毁环节与时序\n"
          "本项目为博里花园农民集中居住区建设工程，临时用地用于施工场站。\n"
          "江苏盐业井神张兴矿区采输卤扩建项目新增 7 组采卤接替井组，"
          "生产规模由 300 万吨/年变更为 600 万吨/年。\n"
          "临时用地面积 3.2600 公顷。\n")
    out = apply_guards(md, FILLED, 4, "reclamation")
    assert "张兴矿区" not in out and "采输卤" not in out
    assert "博里花园" in out and "3.2600 公顷" in out


def test_strip_numbers_are_corrected():
    from app.services.reclamation_text_guard import apply_guards

    md = ("剥离表土面积 2.2800 hm2，可供表土量为 6840 m，需土量为 6840 m，"
          "可实现剥覆平衡。耕地面积为 2.2800 公顷。")
    out = apply_guards(md, FILLED, 4, "reclamation")
    assert "2.5900 hm²" in out
    assert "7770" in out and "6840" not in out
    assert "hm2" not in out
    # 单纯说「耕地面积」的地方不改
    assert "耕地面积为 2.2800 公顷" in out


def test_strip_numbers_in_model_wording_are_corrected():
    """模型常用「需剥离表土量 / 需回填表土面积 / 可供土方量 / 共需表土」等写法。"""
    from app.services.reclamation_text_guard import apply_guards

    md = ("需剥离表土面积 2.5900 hm²，需剥离表土量 6840 m³。"
          "需回填表土面积共 2.2800 hm²，共需表土 6840 m³。"
          "临时用地需土量为 7770 m³，可供土方量为 6840 m³，可实现剥覆平衡。")
    out = apply_guards(md, FILLED, 4, "reclamation")
    assert "6840" not in out
    assert out.count("7770") == 4
    assert "需回填表土面积共 2.5900" in out


def test_model_invented_volume_is_forced_to_canonical():
    """模型自造的数（6812.6）和「剥离范围合计 22800」都要归到系统口径。"""
    from app.services.reclamation_text_guard import apply_guards

    md = ("临时用地可供表土 6812.6 m³，复垦覆土实际可用 6840.0 m³。"
          "剥离范围涉及水田 14200 平方米、旱地 8600 平方米。合计 22800 平方米。"
          "剥离厚度 30 厘米，共计剥离表土 6840 立方米。"
          "本临时用地表土可供方量 6840 立方米，表土回覆所需土方 6840 立方米。")
    out = apply_guards(md, FILLED, 4, "reclamation")
    assert "6812.6" not in out
    assert "6840" not in out
    assert "合计 25900" in out
    assert out.count("7770") >= 3


def test_figure_reference_without_figure_is_rewritten_in_ch4():
    from app.services.reclamation_text_guard import apply_guards

    md = "由图可知，临时用地不占用永久基本农田。"
    out = apply_guards(md, FILLED, 4, "reclamation")
    assert "由图可知" not in out
    # 其他章不动
    assert "由图可知" in apply_guards(md, FILLED, 3, "reclamation")


def test_table_4_12_uses_strip_area_and_both_directions():
    from app.services.reclamation_ch4_tables import table_4_12

    t = table_4_12(FILLED)
    row = t["rows"][0]
    assert row[1] == "3.2600"          # 临时用地面积
    assert row[2] == "2.5900"          # 可复垦耕地面积 = 剥离面积
    assert row[3] == "水田、旱地"       # 不再只写「旱地」
