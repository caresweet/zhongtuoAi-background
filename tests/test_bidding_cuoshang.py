# -*- coding: utf-8 -*-
"""竞争性磋商文件解析（洪泽报批项目口径）。"""

from __future__ import annotations

from pathlib import Path

from docx import Document

from app.services.bidding_tender import _cn_int, parse_tender
from app.services.bidding_cuoshang_bid import _unit_big, _equip_rows


def _tender_docx(tmp_path: Path) -> Path:
    d = Document()
    for line in (
        "淮安市洪泽区政府采购项目竞争性磋商文件",
        "项目名称:洪泽区2026年土地征收报批综合服务项目",
        "项目编号:JSJH-HZZFCG-CS-2026033",
        "一、项目基本情况",
        "4、预算金额：叁万伍仟元整",
        "5、最高限价：350元/亩（报省）、240元/亩（报市）",
        "二、申请人的资格要求：",
        "1、满足《中华人民共和国政府采购法》第二十二条的规定",
        "（1）供应商有效期内的测绘资质乙级及以上资质证书；",
        "三、获取采购文件",
        "1、采购人信息",
        "名    称：淮安市自然资源和规划局洪泽分局",
        "2、采购代理机构信息",
        "名    称：江苏嘉华工程咨询有限公司",
        "服务时间：从合同签订之日起一年。",
        "一、投标报价：20分",
        "二、商务部分：15分",
        "三、技术方案：50分",
        "1、工作方案（10分）",
        "6、安全保密保障措施（7分）",
        "四、人员配备：15分",
        "第二章   响 应 文 件 格 式",
        "一、磋商函",
        "二、资质审查时供应商必须提交的资格证明文件",
        "1、供应商提供下列材料之一（加盖供应商公章装订在标书中）:",
        "2、供应商须具有在有效期内的营业执照副本。",
        "三、人员配备表",
        "四、供应商认为有必要提供并说明的其它资料",
        "五、示范格式",
        "示范格式一",
    ):
        d.add_paragraph(line)
    p = tmp_path / "tender.docx"
    d.save(str(p))
    return p


def test_parse_cuoshang_fields(tmp_path):
    req = parse_tender(_tender_docx(tmp_path))
    assert req["bid_format"] == "cuoshang"
    assert req["project_name"] == "洪泽区2026年土地征收报批综合服务项目"
    assert req["purchaser"] == "淮安市自然资源和规划局洪泽分局"
    assert req["agency"] == "江苏嘉华工程咨询有限公司"
    # 叁万伍仟元 = 3.5 万元；按亩计价时总价不再取「350 元」
    assert req["budget_wan"] == "3.5"
    assert req["max_price_wan"] == "3.5"
    assert req["unit_prices"] == {"province": 350.0, "city": 240.0}
    assert req["duration_months"] == 12
    cs = req["cuoshang"]
    assert cs["qualifications"]
    assert cs["qualification_docs"] == [
        "1、供应商提供下列材料之一（加盖供应商公章装订在标书中）:",
        "2、供应商须具有在有效期内的营业执照副本。",
    ]
    assert cs["format_sections"] == [
        "一、磋商函", "二、资质审查时供应商必须提交的资格证明文件",
        "三、人员配备表", "四、供应商认为有必要提供并说明的其它资料",
        "五、示范格式",
    ]
    assert {"item": "三、技术方案", "score": 50} in cs["scoring"]
    assert {"item": "6、安全保密保障措施", "score": 7} in cs["scoring_sub"]


def test_cn_int():
    assert _cn_int("叁万伍仟元整") == 35000
    assert _cn_int("壹拾贰万元整") == 120000


def test_unit_price_uppercase():
    assert _unit_big(330) == "叁佰叁拾元/亩"
    assert _unit_big(225) == "贰佰贰拾伍元/亩"


def test_equip_rows_cover_five_scoring_items():
    texts = {"公司主要设备及软件清单":
             "全站仪；RTK（实时动态差分）GPS接收机；无人机；绘图仪；"
             "CASS成图软件；ArcGIS软件。"}
    rows = _equip_rows(texts)
    have = {r[1]: r[2] for r in rows}
    assert have["全站仪（南方 NTS 系列）"] == "有"
    assert have["GNSS 接收机（RTK）"] == "有"
    assert have["无人机（大疆精灵 4RTK）"] == "有"
    assert have["南方 CASS 成图软件"] == "有"
    assert have["大幅面绘图仪"] == "有"


def test_plan_tables_fit_page_width():
    from app.services.bidding_cuoshang_bid import _plan_table_specs
    specs = _plan_table_specs({"work_items": [
        {"no": "1", "name": "制作征地范围红线", "desc": ""}]})
    assert len(specs) == 6
    for spec in specs.values():
        assert sum(spec[3]) <= 15.05, spec[0]


def test_person_rows_follow_tender_nine_columns():
    # 磋商文件第二章人员配备表是 9 列：序号/姓名/性别/年龄/学历/专业/
    # 职称/相关证书/拟任岗位（.doc 转 docx 时表头丢失，按原 .doc 核对）
    from app.services.bidding_cuoshang_bid import _person_rows
    rows = _person_rows([{
        "name": "徐作诚", "gender": "男", "age": 45, "education": "本科",
        "major": "测绘", "title": "高级工程师",
        "certs": ["高级职称", "注册测绘师"], "role": "项目负责人"}])
    assert len(rows[0]) == 9
    assert rows[0][7] == "注册测绘师"      # 「相关证书」只放注册类
    assert rows[0][8] == "项目负责人"


def test_format_spec_reads_tender_chapter(tmp_path):
    """格式章要按招标文件原文抽：章节顺序、人员表列名、示范格式。"""
    from app.services.bidding_format_spec import extract_format_spec
    d = Document()
    for line in (
        "第二章   响 应 文 件 格 式",
        "一、磋商函",
        "致：（采购人）",
        "二、资质审查时供应商必须提交的资格证明文件",
        "1、供应商提供下列材料之一：",
        "2、供应商须具有营业执照；",
        "三、人员配备表",
    ):
        d.add_paragraph(line)
    t = d.add_table(rows=1, cols=9)
    for i, h in enumerate(["序号", "姓名", "性别", "年龄", "学历", "专业",
                           "职称", "相关证书", "拟任岗位"]):
        t.cell(0, i).text = h
    for line in ("四、供应商认为有必要提供并说明的其它资料",
                 "五、示范格式", "示范格式一", "法定代表人资格证明"):
        d.add_paragraph(line)
    p = tmp_path / "tender.docx"
    d.save(str(p))

    spec = extract_format_spec(p)
    kinds = [s["kind"] for s in spec["sections"]]
    assert kinds == ["letter", "qualification", "personnel", "other", "forms"]
    assert spec["sections"][1]["items"][0].startswith("1、")
    assert spec["sections"][2]["headers"] == [
        "序号", "姓名", "性别", "年龄", "学历", "专业", "职称",
        "相关证书", "拟任岗位"]
    assert "法定代表人资格证明" in spec["forms"]
