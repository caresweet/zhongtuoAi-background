# -*- coding: utf-8 -*-
"""勘测定界「土地分类面积表」的视觉抽取（复垦表4-2 / 工程量测算的数据源）。

背景：这份 PDF 的**文本层把单元格边界丢了** —— 实测数据行抽出来是
`集体 74.02211.02763.02580.02727.0 5307.0 8070.0`（五个数字连成一串）。
`land_survey_parser` 返回 {}、`pdfplumber.extract_tables()` 一张表都认不出。
所以改走视觉；本文件测的是**视觉之外的确定性部分**（选页、去重、单位折算）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.land_class_extract import (      # noqa: E402
    _dedupe_by_area, _to_m2, candidate_pages, summarize,
    is_land_class_material, read_land_class_xlsx, read_survey_meta_xlsx,
)

_PDF = BACKEND / "storage" / "images" / "勘测定界报告_81e1aaf0.pdf"


def test_land_class_material_name_rules():
    """文件名判据要认「勘界图/现状图/宗地图」—— 井神那批就叫这些名字。"""
    assert is_land_class_material("井神临时用地勘界报告.xlsx")
    assert is_land_class_material("勘界图-已去除林地.pdf")
    assert is_land_class_material("井神现状图-已去除林地.pdf")
    assert is_land_class_material("宗地图 Model (1).pdf")
    assert not is_land_class_material("调查表.pdf")


def test_read_land_class_xlsx_three_level_header(tmp_path):
    """勘界 Excel 的三段表头（农用地→耕地→水田）要按列展开，读出的数=合计。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "土地分类面积表"
    ws["A4"] = "土地分类面积表"
    # 三行表头：一级 / 二级 / 叶子
    ws["A7"] = "权属单位"; ws["B7"] = "农用地"; ws["R7"] = "建设用地"
    ws["Z7"] = "未利用地"; ws["AB7"] = "合计"
    ws.merge_cells("B7:Q8"); ws.merge_cells("R7:Y8")
    ws.merge_cells("Z7:AA8"); ws.merge_cells("AB7:AB10")
    ws["B9"] = "合计"; ws["C9"] = "耕地"; ws["G9"] = "园地"
    ws["I9"] = "林地"; ws["L9"] = "草地"; ws["M9"] = "交通用地"
    ws["N9"] = "陆地水域"; ws["R9"] = "合计"
    ws["C10"] = "合计"; ws["D10"] = "水田"; ws["E10"] = "旱地"
    ws["M10"] = "农村道路"; ws["N10"] = "坑塘水面"; ws["P10"] = "沟渠"
    ws["A11"] = "XX村村民委员会"
    for col, val in {"B": 23248, "C": 22215, "D": 22215, "M": 417, "P": 616, "AB": 23248}.items():
        ws[f"{col}11"] = val
    ws["A19"] = "合计"
    for col, val in {"B": 23248, "C": 22215, "D": 22215, "M": 417, "P": 616, "AB": 23248}.items():
        ws[f"{col}19"] = val

    p = tmp_path / "井神临时用地勘界报告.xlsx"
    ws2 = wb.create_sheet("勘测界定表")
    ws2["A1"] = "勘 测 定 界 表"
    ws2["A2"] = "土地座落"; ws2["B2"] = "淮安市淮安区"
    ws2["A3"] = "单位地址"                     # 值是空的 → 不该出现在结果里
    ws2["A4"] = "界址点数"; ws2["B4"] = 36
    wb.save(p)
    d = read_land_class_xlsx(str(p))
    got = {(r["一级地类"], r["二级地类"]): r["面积_m2"] for r in d["rows"]}
    assert got == {("农用地", "水田"): 22215.0,
                   ("农用地", "农村道路"): 417.0,
                   ("农用地", "沟渠"): 616.0}, got
    assert d["合计_m2"] == 23248.0
    assert d["权属单位"] == "XX村村民委员会"
    # 封面级事实：只回填读到值的键
    meta = read_survey_meta_xlsx(str(p))
    assert meta.get("location") == "淮安市淮安区"
    assert meta.get("plot_count") == "36"
    assert "unit_address" not in meta


def test_candidate_pages_skips_toc_and_other_tables():
    """选页要避开两类**看起来像**的页：目录页、以及「勘测定界表」那页。

    实测踩过的坑：
      · 目录页也印着「土地分类面积表」三个字（带 `………` 前导点）；
      · 「勘测定界表」那页同样有「耕地/地类/合计」（那是它的地类小计列）——
        两页一起读会把同一批数字读两遍、合计 8070 变 16140。
    两份文件都在 `storage/`（gitignore），没有就跳过。
    """
    if not _PDF.exists():
        pytest.skip("勘测定界报告不在本机 storage 下")
    pages = [p + 1 for p in candidate_pages(str(_PDF))]
    assert 5 in pages, pages            # 真正的「土地分类面积表」在第 5 页
    assert 2 not in pages, pages        # 第 2 页是目录
    assert 4 not in pages, pages        # 第 4 页是「勘测定界表」，不是这张


def test_to_m2_handles_hectare():
    assert _to_m2("478.0", "平方米") == 478.0
    assert _to_m2("0.0478", "公顷") == 478.0
    assert _to_m2("1,234.5", "平方米") == 1234.5
    assert _to_m2("—", "平方米") is None
    assert _to_m2("", "平方米") is None


def test_dedupe_by_area_prefers_full_labels():
    """同一个面积只留标签最完整的那一行（表跨页/读了两页时会重复）。"""
    rows = [
        {"一级地类": "", "二级地类": "水田", "面积_m2": 478.0},
        {"一级地类": "耕地", "二级地类": "水田", "面积_m2": 478.0},
        {"一级地类": "林地", "二级地类": "其他林地", "面积_m2": 2211.0},
    ]
    out = _dedupe_by_area(rows)
    assert len(out) == 2
    _m = {r["面积_m2"]: r for r in out}
    assert _m[478.0]["一级地类"] == "耕地"      # 留的是标签更全的那条


def test_summarize_empty():
    assert summarize({}) == ""
    assert summarize({"rows": []}) == ""
    s = summarize({"rows": [{"二级地类": "水田", "面积_m2": 478.0}], "合计_m2": 8070.0})
    assert "0.0478 公顷" in s and "0.8070 公顷" in s
