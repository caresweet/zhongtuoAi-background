# -*- coding: utf-8 -*-
"""第4章表格按**当前模板**的表号/顺序出（不再按明远样本定死）。"""

import re
from pathlib import Path

import pytest

from app.services.reclamation_ch4_tables import build_ch4_tables
from app.services.template_table_copy import chapter_table_plan

TPL = Path(__file__).resolve().parent.parent / "storage" / "templates" / \
    "user_95946f347730449c8716ef49c2a5be41.docx"

pytestmark = pytest.mark.skipif(not TPL.exists(), reason="测试模板不在仓库里")


def _filled():
    return {
        "project_name": "城东污水处理厂扩建工程临时用地",
        "area_m2": 26800,
        "topsoil_thickness": "25",
        "reclaim_direction": "旱地",
        "land_classification": {"合计_m2": 26800.0, "rows": [
            {"一级地类": "耕地", "二级地类": "旱地", "面积_m2": 14000.0},
            {"一级地类": "园地", "二级地类": "果园", "面积_m2": 6000.0},
            {"一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 3800.0},
            {"一级地类": "交通运输用地", "二级地类": "农村道路", "面积_m2": 3000.0},
        ]},
        "_land_use_zones": [
            {"name": "施工场站", "area_m2": 9800, "method": "压占", "degree": "重度",
             "land_types": "旱地、果园", "reclaim_direction": "旱地"},
            {"name": "表土堆放区", "area_m2": 3000, "method": "压占", "degree": "中度",
             "land_types": "旱地", "reclaim_direction": "旱地"},
        ],
    }


def test_plan_has_template_numbers():
    plan = chapter_table_plan(str(TPL), 4)
    assert [e["no"] for e in plan] == [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    assert "指标体系" in next(e["title"] for e in plan if e["no"] == 8)


def test_ch4_tables_follow_template_numbers():
    plan = chapter_table_plan(str(TPL), 4)
    built = build_ch4_tables(_filled(), plan=plan)
    caps = [t["caption"] for _, t in built]
    # 模板里没有「等级标准」表 → 不出
    assert not any("等级标准" in c for c in caps)
    # 有「土地质量等级」表 → 出，并用模板表号 4-10
    assert any(c.startswith("表4-10") and "土地质量等级" in c for c in caps)
    # 复垦方向 → 模板表号 4-11；表土 → 4-12；水 → 4-13；结构对比 → 4-14
    assert any(c.startswith("表4-11") and "复垦方向" in c for c in caps)
    assert any(c.startswith("表4-12") and "表土" in c for c in caps)
    assert any(c.startswith("表4-13") and "水资源" in c for c in caps)
    assert any(c.startswith("表4-14") and "复垦前" in c for c in caps)
    # 引导语里的表号同步改过
    for _, t in built:
        m = re.match(r'表4-(\d+)', t["caption"])
        if m and t.get("lead"):
            assert f"表4-{m.group(1)}" in t["lead"] or "表4-" not in t["lead"]


def test_ownership_table_and_quality_before_direction():
    plan = chapter_table_plan(str(TPL), 4)
    built = build_ch4_tables(_filled(), plan=plan)
    caps = [t["caption"] for _, t in built]
    assert any(c.startswith("表4-6") and "权属" in c for c in caps)
    i_q = next(i for i, c in enumerate(caps) if "土地质量等级" in c)
    i_d = next(i for i, c in enumerate(caps) if "复垦方向" in c)
    assert i_q < i_d


def test_duplicate_structure_tables_leave_one():
    from docx import Document as _D
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = "reclamation"
    doc = _D()
    for i in range(2):
        doc.add_paragraph("表4-3")
        t = doc.add_table(rows=2, cols=3)
        for ci, h in enumerate(("地类名称及编码", "复垦前面积", "复垦后面积")):
            t.cell(0, ci).text = h
    assert a._dedup_structure_tables(doc) == 1
    assert len(doc.tables) == 1


def test_damage_inventory_merges_same_zone_and_counts_plots():
    from app.services.reclamation_ch4_tables import table_damage_inventory
    f = _filled()
    f["_land_use_zones"] = [
        {"name": "施工场站", "area_m2": 7200, "method": "压占", "degree": "重度",
         "land_types": "旱地", "plot_count": 1},
        {"name": "施工场站", "area_m2": 2600, "method": "压占", "degree": "重度",
         "land_types": "旱地", "plot_count": 1},
        {"name": "施工便道", "area_m2": 4800, "method": "压占", "degree": "重度",
         "land_types": "旱地", "plot_count": 1},
    ]
    t = table_damage_inventory(f)
    rows = t["rows"]
    assert rows[0][1] == "施工场站" and rows[1][1] == "施工便道"
    assert rows[-1][0] == "合计"
    assert rows[0][2] == "0.9800" and rows[0][3] == "2"


def test_damage_inventory_splits_per_plot_rows():
    """同名分区多行 = 逐个地块：面积列「小计 | 地块面积」两栏，其余列纵向合并。"""
    from app.services.reclamation_ch4_tables import table_damage_inventory
    f = _filled()
    f["_land_use_zones"] = [
        {"name": "施工场站", "area_m2": 9800, "degree": "重度",
         "plot_areas_m2": [4200, 5600], "plot_count": 2},
        {"name": "施工便道", "area_m2": 4800, "degree": "重度", "plot_count": 1},
    ]
    t = table_damage_inventory(f)
    assert t["plot_groups"] is True
    assert t["headers"][2] == "面积（hm2）" and t["headers"][3] == ""
    assert t["group_sizes"] == [2, 1]
    assert t["rows"][0][:4] == ["1", "施工场站", "0.9800", "0.4200"]
    assert t["rows"][1] == ["", "", "", "0.5600", "", ""]
    assert t["rows"][2][:4] == ["2", "施工便道", "0.4800", ""]
    assert t["rows"][-1][0] == "合计" and t["rows"][-1][2] == "1.4600"


def test_plot_group_cells_merged_like_template():
    from docx import Document as _D
    from app.services.report_assembler import ReportAssembler
    from app.services.reclamation_ch4_tables import table_damage_inventory

    f = _filled()
    f["_land_use_zones"] = [
        {"name": "施工场站", "area_m2": 9800, "degree": "重度",
         "plot_areas_m2": [4200, 5600], "plot_count": 2},
        {"name": "施工便道", "area_m2": 4800, "degree": "重度", "plot_count": 1},
    ]
    t = table_damage_inventory(f)
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = "reclamation"
    doc = _D()
    a._render_table_from_dict(doc, t, caption="表4-2 临时用地拟损毁情况统计表")
    tb = doc.tables[0]
    # 表头「面积」跨两栏
    assert tb.cell(0, 2).text == tb.cell(0, 3).text == "面积（hm2）"
    assert tb.cell(0, 2)._tc is tb.cell(0, 3)._tc
    # 施工场站两行：序号/名称/小计/地块个数/损毁程度 都是同一个合并单元格
    for col in (0, 1, 2, 4, 5):
        assert tb.cell(1, col)._tc is tb.cell(2, col)._tc, col
    # 地块面积不合并
    assert tb.cell(1, 3)._tc is not tb.cell(2, 3)._tc
    # 合计行前两栏合并
    last = len(tb.rows) - 1
    assert tb.cell(last, 0)._tc is tb.cell(last, 1)._tc
