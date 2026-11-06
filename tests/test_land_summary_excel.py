# -*- coding: utf-8 -*-
"""制式《土地分类面积汇总表》读取：临时/主体两类，按地类汇总。"""

from openpyxl import Workbook

from app.services.land_summary_excel import (
    class_sort_key, form_classes, read_land_summary,
)


def _build(path, label, values, name="土地分类面积汇总表_临时.xlsx"):
    """构造最小 80 列制式表：湿地/耕地/园地/交通运输/水域。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "土地分类面积表"
    ws.cell(row=4, column=2).value = "被征(用)地单位"
    ws.cell(row=4, column=3).value = "权属类别"
    ws.cell(row=4, column=4).value = "面积总计"
    ws.cell(row=4, column=5).value = "耕地（01）"
    ws.cell(row=4, column=8).value = "种植园地（02）"
    ws.cell(row=5, column=5).value = "合计"
    ws.cell(row=5, column=6).value = "水田(0101)"
    ws.cell(row=5, column=7).value = "旱地(0103)"
    ws.cell(row=5, column=8).value = "合计"
    ws.cell(row=5, column=9).value = "果园(0201)"
    ws.cell(row=8, column=2).value = "集体合计"
    ws.cell(row=10, column=2).value = "合计"
    for col, v in values.items():
        ws.cell(row=8, column=col).value = v
        ws.cell(row=10, column=col).value = v
    p = path / name if label == "temp" else path / "土地分类面积汇总表_主体.xlsx"
    wb.save(str(p))
    return p


def test_read_temp_summary(tmp_path):
    p = _build(tmp_path, "temp", {4: 20000, 7: 14000, 9: 6000})
    d = read_land_summary(p)
    assert d["kind"] == "temp"
    assert d["合计_m2"] == 20000
    pairs = {(r["一级地类"], r["二级地类"]): r["面积_m2"] for r in d["rows"]}
    assert pairs[("耕地", "旱地")] == 14000
    assert pairs[("种植园地", "果园")] == 6000


def test_read_permanent_summary(tmp_path):
    p = _build(tmp_path, "permanent", {4: 54000, 7: 54000})
    d = read_land_summary(p)
    assert d["kind"] == "permanent"
    assert d["合计_m2"] == 54000


def test_blank_summary_returns_none(tmp_path):
    p = _build(tmp_path, "temp", {})
    assert read_land_summary(p) is None


def test_form_classes_loaded_from_knowledge_base():
    cs = form_classes()
    if not cs:
        return                      # 知识库尚未登记时跳过（脚本 register_land_summary_form）
    names = [c["二级地类"] for c in cs]
    assert "旱地" in names and "果园" in names
    assert class_sort_key("旱地") < class_sort_key("果园")
