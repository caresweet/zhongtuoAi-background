# -*- coding: utf-8 -*-
"""模板按「域 + 地区」选择的单元测试。

业主口径（2026-10-30）：「稳评的模板和所有的模板一样，都是不同地区不同模板」。
这几个用例钉住三件事：
  1. 地区命中取**最长**的（「淮安区」优先于「淮安」）；
  2. 没命中时只有复垦/投标退化到通用模板，稳评保持原有兜底（返回空）；
  3. 投标生成器能从模板 docx 读到页面尺寸与页边距（只解 sectPr，不加载整份文档）。
"""
from types import SimpleNamespace

import pytest

from app.services.template_selector import pick_region_match, build_match_text
from app.services.bidding_docx_generator import _read_page_geometry


def _tpl(region, name="模板"):
    return SimpleNamespace(name=name, region=region)


def test_pick_region_match_prefers_longest_region():
    items = [_tpl("淮安"), _tpl("淮安区"), _tpl("洪泽")]
    region, hit = pick_region_match(items, "淮安市淮安区某地块征收")
    assert region == "淮安区"
    assert hit.region == "淮安区"


def test_pick_region_match_ignores_empty_and_misses():
    items = [_tpl(""), _tpl("洪泽")]
    assert pick_region_match(items, "金湖县黎城街道") == ("", None)


def test_build_match_text_skips_empty_parts():
    assert build_match_text("洪泽区", None, "", " 项目A ") == "洪泽区 项目A"


def test_read_page_geometry_from_docx(tmp_path):
    from docx import Document
    from docx.shared import Cm

    p = tmp_path / "tpl.docx"
    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Cm(21), Cm(29.7)
    s.top_margin, s.bottom_margin = Cm(2.54), Cm(2.54)
    s.left_margin, s.right_margin = Cm(3.0), Cm(2.5)
    d.save(str(p))

    geo = _read_page_geometry(str(p))
    assert geo is not None
    w, h, top, bottom, left, right = geo
    # EMU → cm（1cm = 360000 EMU）
    assert round(w / 360000) == 21
    assert h / 360000 == pytest.approx(29.7, abs=0.05)
    assert left / 360000 == pytest.approx(3.0, abs=0.05)
    assert right / 360000 == pytest.approx(2.5, abs=0.05)


def test_read_page_geometry_missing_file_returns_none():
    assert _read_page_geometry("/nonexistent/x.docx") is None
