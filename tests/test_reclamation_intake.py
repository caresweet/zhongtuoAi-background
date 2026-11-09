# -*- coding: utf-8 -*-
"""复垦基础资料 Excel：生成 → 填写 → 读回 → 写入事实卡。"""

from openpyxl import load_workbook

from app.services.reclamation_intake import (
    apply_intake, build_intake_workbook, read_intake_workbook,
    SHEET_BASE, SHEET_ZONES, SHEET_DESIGN,
)


def _fill(path, pairs, zones, design):
    wb = load_workbook(path)
    ws = wb[SHEET_BASE]
    for r in range(2, ws.max_row + 1):
        k = ws.cell(row=r, column=1).value
        if k in pairs:
            ws.cell(row=r, column=2, value=pairs[k])
    ws = wb[SHEET_DESIGN]
    for r in range(2, ws.max_row + 1):
        k = ws.cell(row=r, column=1).value
        if k in design:
            ws.cell(row=r, column=2, value=design[k])
    ws = wb[SHEET_ZONES]
    for i, row in enumerate(zones, start=3):
        ws.cell(row=i, column=1, value=i - 2)
        for j, v in enumerate(row, start=2):
            ws.cell(row=i, column=j, value=v)
    wb.save(path)


def test_roundtrip(tmp_path):
    path = tmp_path / "复垦基础资料.xlsx"
    build_intake_workbook(path)
    _fill(
        path,
        {"项目名称": "张兴储气库", "建设单位": "苏盐井神一分公司",
         "项目位置": "淮安市淮安区山阳街道黄土桥村", "所属地区": "淮安区",
         "法人代表": "张三", "单位性质": "有限责任公司",
         "主体工程用地面积(公顷)": "8.0762", "用地期限(月)": "24",
         "复垦期(月)": "12", "管护期(月)": "12"},
        [("井场", "0.2310", "挖损和压占", "重度", "水田", "水田"),
         ("施工场站", "1.1950", "压占", "重度", "水田", "水田"),
         ("施工便道", "0.5788", "压占", "重度", "水田", "水田"),
         ("表土堆放区", "0.3200", "压占", "中度", "水田", "水田")],
        {"表土剥离厚度(cm)": "30", "硬化结构类型": "混凝土面层+垫层",
         "硬化面积(公顷)": "1.4260", "面层厚度(cm)": "20",
         "垫层厚度(cm)": "30", "复垦方向": "水田"},
    )
    data = read_intake_workbook(path)
    assert data is not None
    f = data["fields"]
    assert f["region"] == "淮安区"
    assert f["area_m2"] == 23248.0          # 四个分区合计
    assert f["project_area_m2"] == 104010.0  # 永久 + 临时
    assert f["service_years"] == 4.0
    assert f["hard_area_m2"] == 14260.0
    assert len(data["zones"]) == 4

    filled = {}
    changed = apply_intake(filled, data)
    assert "land_use_zones" in changed
    assert filled["project_name"] == "张兴储气库"
    assert filled["legal_rep"] == "张三"
    assert filled["unit_nature"] == "有限责任公司"
    assert filled["_land_use_zones"][0]["name"] == "井场"
    assert filled["topsoil_thickness"] == "30"


def test_rejects_unrelated_xlsx(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "随便.xlsx"
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.active["A1"] = "无关内容"
    wb.save(p)
    assert read_intake_workbook(p) is None


def test_repeated_zone_rows_are_plots(tmp_path):
    """同名分区写多行 = 该分区下的逐个地块（表4-2 面积列要岔开）。"""
    path = tmp_path / "复垦基础资料.xlsx"
    build_intake_workbook(path)
    _fill(
        path,
        {"项目名称": "测试项目", "所属地区": "淮安区",
         "用地期限(月)": "24", "复垦期(月)": "12", "管护期(月)": "12"},
        [("井场", "0.1000", "挖损和压占", "重度", "水田", "水田"),
         ("井场", "0.1310", "挖损和压占", "重度", "水田", "水田")],
        {"复垦方向": "水田"},
    )
    data = read_intake_workbook(path)
    assert len(data["zones"]) == 1
    z = data["zones"][0]
    assert z["name"] == "井场"
    assert z["area_m2"] == 2310.0
    assert z["plot_areas_m2"] == [1000.0, 1310.0]
    assert z["plot_count"] == 2
    assert data["fields"]["area_m2"] == 2310.0
