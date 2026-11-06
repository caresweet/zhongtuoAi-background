# -*- coding: utf-8 -*-
"""复垦地区模板的逐节三色画像。"""

from docx import Document

from app.services.reclamation_template_profile import (
    build_profile_from_docx, profile_data_gaps, profile_prompt_block,
    template_analysis_summary,
)


def _make_template(path):
    d = Document()
    d.add_heading("1 前言", level=1)
    d.add_heading("1.1 编制背景及过程", level=2)
    d.add_paragraph("本项目为储气库工程，临时用地在建设过程中形成。")
    d.add_paragraph("项目临时用地面积 2.3248 公顷。")
    d.add_heading("1.2.1 复垦方案服务年限", level=3)
    d.add_paragraph("复垦方案服务年限为 48 个月。")
    d.add_heading("2 编制总则", level=1)
    d.add_heading("2.1 编制目的", level=2)
    d.add_paragraph("为落实土地复垦义务，保护耕地资源，特编制本方案。")
    d.add_heading("2.3.1 法律法规", level=3)
    d.add_paragraph("《中华人民共和国土地管理法》（2019 年）。")
    d.save(str(path))


def test_profile_classifies_sections(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_template(p)
    prof = build_profile_from_docx(p)
    assert set(prof["chapters"]) == {"1", "2"}
    s11 = prof["chapters"]["1"]["sections"][0]
    assert s11["no"] == "1.1"
    assert s11["create"] and s11["slot"]
    s211 = [s for s in prof["chapters"]["2"]["sections"] if s["no"] == "2.3.1"][0]
    assert s211["fixed"], "法规清单应判为固定"
    assert not s211["create"]


def test_prompt_block_has_three_colors(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_template(p)
    block = profile_prompt_block(p, 1)
    assert "模板画像" in block
    assert "🟩" in block and "🟨" in block and "🟦" in block
    assert "1.1 编制背景及过程" in block
    assert "2.1 编制目的" not in block   # 只取本章


def test_slot_examples_come_from_template(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_template(p)
    prof = build_profile_from_docx(p)
    sec = prof["chapters"]["1"]["sections"][0]
    slots = {s["key"]: s["example"] for s in sec["slots"]}
    assert slots.get("area_m2") == "2.3248 公顷"
    block = profile_prompt_block(p, 1)
    assert "area_m2（模板 2.3248 公顷" in block
    # 给了本项目资料 → 直接给出「模板值 → 本项目值」
    block2 = profile_prompt_block(p, 1, filled={"area_m2": "8070"})
    assert "area_m2（模板 2.3248 公顷 → 本项目 8070）" in block2
    # 没给的键标成缺，不许沿模板值
    assert "service_years（模板 48 个月 → 本项目缺）" in block2


def test_summary_lists_data_slots_and_rewrite_sections(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_template(p)
    prof = build_profile_from_docx(p)
    s = template_analysis_summary(prof)
    keys = {x["key"] for x in s["data_slots"]}
    assert "area_m2" in keys and "service_years" in keys
    assert any(r["chapter"] == "1" for r in s["rewrite_sections"])
    assert s["section_counts"]["create"] > 0


def test_data_gaps_never_keep_template_values(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_template(p)
    prof = build_profile_from_docx(p)
    gaps = profile_data_gaps(prof, {"area_m2": "8070"})
    assert "area_m2" not in gaps
    assert "service_years" in gaps
    # 缺口要在提示词里显式写出来
    block = profile_prompt_block(p, 1, filled={"area_m2": "8070"})
    assert "资料缺口" in block and "service_years" in block
