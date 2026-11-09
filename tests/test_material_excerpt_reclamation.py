# -*- coding: utf-8 -*-
"""复垦：用户上传的「概况/施工报告/可研」原文要能进正文（可直接引用）。

业主 2026-11-09：「这个资料属于用户有的话会上传的，然后需要全部解析并且部分内容
是可以直接复制粘贴到正文内容上的。」
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PLAN = """工程概况
1.3.1 项目建设单位概况
江苏井神盐化股份有限公司是江苏省盐业集团有限责任公司的控股子公司，
连续多年入选中国制盐行业十强企业。
1.3.2 工程规模及实施步骤计划
工程分三期建设，一期 6 对井组、二期 4 对、三期 9 对井组。
3.26 公顷，共 8 个地块
"""

_PLAN2 = """一、项目概况
ZXK11、ZXK12 井组每井组有两个井场，井口地面距离约 220 米。
3.1 钻井目的：采盐井，了解矿层厚度与品位。
"""


def _state(*mats):
    return {"_domain": "reclamation",
            "_project_materials": [{"source_name": n, "text_content": t}
                                   for n, t in mats]}


def test_heading_detection_skips_body_sentences():
    from app.services.material_excerpt import _looks_like_any_heading

    assert _looks_like_any_heading("一、项目概况")
    assert _looks_like_any_heading("1.3.1 项目建设单位概况")
    assert _looks_like_any_heading("3.1 钻井目的：")
    assert not _looks_like_any_heading("3.26 公顷，共 8 个地块")


def test_ch3_excerpt_contains_project_overview_verbatim():
    from app.services.material_excerpt import excerpt_for_chapter

    ex = excerpt_for_chapter(
        3, _state(("概况.docx", _PLAN), ("ZXK11钻井施工报告.docx", _PLAN2)))
    assert "江苏井神盐化股份有限公司" in ex
    assert "工程分三期建设" in ex
    assert "每井组有两个井场" in ex


def test_overview_material_without_headings_is_used_for_ch3():
    """没有标题、但文件名是「概况/施工报告」的材料，整份进第3章。"""
    from app.services.material_excerpt import excerpt_for_chapter

    text = "本项目位于淮安市淮安区，临时用地 3.26 公顷，用于施工场站与便道。"
    ex = excerpt_for_chapter(3, _state(("博里花园概况.txt", text)))
    assert "临时用地 3.26 公顷" in ex


def test_inject_instruction_allows_verbatim_copy():
    from app.services.material_excerpt import inject_excerpt

    prompt = inject_excerpt(3, _state(("概况.docx", _PLAN)), "写第3章")
    assert "可以直接复制原文" in prompt
    assert "江苏井神盐化股份有限公司" in prompt


def test_stability_excerpt_untouched_by_reclamation_materials():
    from app.services.material_excerpt import excerpt_for_chapter

    state = {"_domain": "stability",
             "_project_materials": [{"source_name": "概况.docx",
                                     "text_content": _PLAN}]}
    # 稳评按「第X章」切块，这份材料没有章标题 → 不注入
    assert excerpt_for_chapter(3, state) == ""
