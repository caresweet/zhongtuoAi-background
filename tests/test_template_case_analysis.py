# -*- coding: utf-8 -*-
"""模板「批注式分析」与图6-1 内置图绑定。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "storage" / "templates" / "user_95946f347730449c8716ef49c2a5be41.docx"
IMG = ROOT / "storage" / "images" / "reclamation" / "图6-1_临时拦挡措施断面示意图.png"


def test_case_analysis_marks_rules_and_sources():
    from app.services.template_case_analysis import analyze_template_file, render_markdown

    if not TPL.exists():
        pytest.skip("复垦模板不在仓库里")
    a = analyze_template_file(TPL, domain="reclamation", name="淮安区复垦方案模板")
    by_no = {s["no"]: s for s in a["sections"]}
    assert by_no["4.2.5"]["kind"].startswith("重写")
    assert "不可避免" in by_no["4.2.5"]["replace"]
    assert "勘测定界" in by_no["4.2.3"]["source"]
    assert "耕地" in by_no["6.2"]["formula"]
    assert "Excel" in by_no["7.2.1"]["source"]
    assert by_no["7.1.1"]["kind"].startswith("不变")
    md = render_markdown(a)
    assert "## 4 土地复垦方向可行性分析" in md
    assert "临时用地占用耕地不可避免" in md


def test_case_analysis_stability_uses_generic_kinds():
    from app.services.template_case_analysis import analyze_template_file

    p = ROOT / "storage" / "templates" / "8a0ba039a30a448a8778db6c476bdafd.docx"
    if not p.exists():
        pytest.skip("稳评模板不在仓库里")
    a = analyze_template_file(p, domain="stability", name="金湖稳评模板")
    kinds = {s["kind"] for s in a["sections"]}
    by_title = {s["title"]: s for s in a["sections"]}
    # 不应套用复垦口径
    assert not any("剥离" in (s.get("formula") or "") for s in a["sections"])
    assert "换数（只替换数据位）" in kinds
    # 稳评自己的口径：面积以勘界为准、风险等级按系统量化表
    assert "勘界" in by_title["征收范围、面积及地上附着物情况"]["source"]
    assert any("量化" in (s.get("formula") or "") for s in a["sections"])


def test_case_prompt_block_for_any_domain():
    """生成提示词里，任何域都能注入「逐节取数口径」。"""
    from app.services.template_case_analysis import case_prompt_block

    if not TPL.exists():
        pytest.skip("复垦模板不在仓库里")
    b = case_prompt_block(TPL, 7, domain="reclamation")
    assert "本章逐节取数口径" in b and "表7-3" in b
    p = ROOT / "storage" / "templates" / "8a0ba039a30a448a8778db6c476bdafd.docx"
    if p.exists():
        b2 = case_prompt_block(p, 1, domain="stability")
        assert "本章逐节取数口径" in b2
        assert ("勘界" in b2) or ("批文" in b2)


def test_builtin_figure_binds_to_chapter_6():
    from app.services.image_catalog import build_image_catalog

    if not IMG.exists():
        pytest.skip("图6-1 内置图不在仓库里")
    cat = build_image_catalog(
        [{"path": str(IMG), "name": IMG.name, "label": "临时拦挡措施断面示意图"}],
        domain="reclamation")
    by_ch = cat.get("by_chapter") or {}
    assert by_ch.get(6), "图6-1 应绑定第6章"
    assert by_ch[6][0]["label"] == "临时拦挡措施断面示意图"
    assert not by_ch.get(5), "不能落到第5章硬化层断面"


def test_chapter6_has_body_image_anchor():
    from app.agent.chapter_catalog import body_image_anchor

    assert body_image_anchor("reclamation", 6) == "工程技术措施设计"


def test_analyze_markdown_report(tmp_path):
    """Markdown 范文也能做批注式分析（本地案例库大量是 .md）。"""
    from app.services.template_case_analysis import analyze_template_file

    p = tmp_path / "稳评报告.md"
    p.write_text(
        "# 1 项目概况\n\n本项目位于某村，征收面积 10 公顷。\n\n"
        "# 2 风险分析\n\n主要风险为施工噪声与交通影响。\n\n"
        "# 3 评估结论\n\n风险等级为低风险。\n", encoding="utf-8")
    a = analyze_template_file(p, domain="stability", name="测试稳评报告")
    assert a["chapters"] == 3
    assert [s["title"] for s in a["sections"]] == ["项目概况", "风险分析", "评估结论"]
    assert a["sections"][0]["kind"].startswith("重写")      # 项目概况
    assert a["sections"][1]["kind"].startswith("重写")      # 风险分析
