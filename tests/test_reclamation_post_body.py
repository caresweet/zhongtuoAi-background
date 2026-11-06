# -*- coding: utf-8 -*-
"""复垦正文之后的三部分：11 报告表 / 12 请示 / 13 图件 / 14 附件（12、13 本轮补齐）。"""

from pathlib import Path

from docx import Document

from app.services.reclamation_request_letter import render_request_letter


def _letter_text(doc):
    return "\n".join(p.text for p in doc.paragraphs)


def test_request_letter_uses_facts():
    doc = Document()
    render_request_letter(doc, {
        "project_name": "某污水处理厂扩建工程临时用地",
        "build_unit": "某某水务投资有限公司",
        "location": "淮安市淮安区钦工镇宋集村",
        "area_m2": 26800,
        "permanent_area_m2": 54000,
        "reclaim_direction": "旱地",
        "service_years": "3.0",
        "project_investment": 21500,
        "implement_unit": "江苏众拓项目代理咨询有限公司",
        "submit_date": "2027-01-10",
    })
    t = _letter_text(doc)
    assert "关于申请审查《某污水处理厂扩建工程临时用地土地复垦方案》的请示" in t
    assert "淮安市自然资源和规划局" in t
    assert "2.6800公顷" in t and "5.4000公顷" in t
    assert "复垦方向为旱地" in t
    assert "复垦服务年限3年" in t and "3.0 个月" not in t
    assert "21500.00万元" in t
    assert "某某水务投资有限公司（盖章）" in t
    assert "2027年1月10日" in t


def test_request_letter_skips_missing_fields():
    doc = Document()
    render_request_letter(doc, {"project_name": "测试项目"})
    t = _letter_text(doc)
    assert "测试项目" in t
    assert "公顷" not in t          # 没有面积就不写这句


def test_figures_section_without_images(tmp_path):
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a.storage_dir = tmp_path
    a._img_catalog_data = {}
    doc = Document()
    a._add_reclamation_figures(doc, {})
    assert "本次未提供图件。" in _letter_text(doc)


def test_figures_section_includes_system_location_map(tmp_path):
    from PIL import Image
    from app.services.report_assembler import ReportAssembler
    (tmp_path / "images").mkdir()
    Image.new("RGB", (400, 300), "white").save(
        str(tmp_path / "images" / "图3-1 工程位置示意图.jpg"))
    a = ReportAssembler.__new__(ReportAssembler)
    a.storage_dir = tmp_path
    a._img_catalog_data = {}
    doc = Document()
    a._add_reclamation_figures(doc, {})
    assert "图件1 项目地理位置示意图" in _letter_text(doc)
