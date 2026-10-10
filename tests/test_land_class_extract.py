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
)

_PDF = BACKEND / "storage" / "images" / "勘测定界报告_81e1aaf0.pdf"


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
