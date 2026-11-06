# -*- coding: utf-8 -*-
"""招标文件解析与投标人员抽取。"""

from pathlib import Path

import pytest

TENDER = Path(__file__).resolve().parent.parent / "storage" / "templates" / \
    "招标文件_丁蜀镇_测试.doc"

pytestmark = pytest.mark.skipif(not TENDER.exists(), reason="招标样本不在仓库里")


@pytest.fixture(scope="module")
def tender():
    from app.services.bidding_tender import parse_tender
    return parse_tender(TENDER)


def test_project_info(tender):
    assert "丁蜀镇" in tender["project_name"]
    assert tender["project_no"] == "丁招（2026）281号"
    assert tender["purchaser"] == "宜兴市丁蜀镇建设和生态环境办公室"
    assert tender["budget_wan"] == "37"
    assert tender["max_price_wan"] == "37"


def test_qualification_and_staffing(tender):
    blob = "\n".join(tender["qualifications"])
    assert "测绘资质" in blob
    assert tender["staff_min"].get("项目负责人") == 1
    assert tender["staff_min"].get("项目组成员") == 3
    assert tender["no_consortium"] is True


def test_format_chapter_is_chapter_six(tender):
    assert "第六章" in tender["format_chapter"]
    titles = [s["title"] for s in tender["format_sections"] if s["level"] == 1]
    joined = " ".join(titles)
    for kw in ("投标函", "报价文件", "资格证明文件", "偏离表", "评分导读表",
               "服务方案", "项目人员配置清单", "其他"):
        assert kw in joined, f"格式目录缺少 {kw}"


def test_scoring_contains_personnel_and_certificates(tender):
    blob = "\n".join(tender["scoring_points"])
    assert "注册测绘师" in blob
    assert "体系认证" in blob


def test_select_minimum_personnel_randomly():
    from app.services.bidding_tender import select_bid_personnel
    p = select_bid_personnel(company_id=4, seed=2026)
    assert len(p) == 4
    assert p[0]["role"] == "项目负责人"
    assert all(x["role"] == "项目组成员" for x in p[1:])
    # 负责人来自高级职称
    assert "高级" in p[0]["title"]


@pytest.mark.asyncio
async def test_build_bid_document_follows_tender_format(tmp_path):
    """按招标第六章顺序出 8 个部分，报价与人员表落位。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "投标文件.docx"
    p = await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                                 out_path=out, seed=2026, use_llm=False)
    assert p.exists()
    d = Document(str(p))
    text = "\n".join(x.text for x in d.paragraphs)
    for sec in ("（一）投标函", "（二）报价文件", "（三）资格证明文件",
                "（四）技术条款及相关要求偏离表", "（五）技术商务部分评分导读表",
                "（六）服务内容的详细说明及服务方案", "（七）项目人员配置清单", "（八）其他"):
        assert sec in text, sec
    assert "叁拾柒万元整" in text
    assert "江苏众拓测绘有限公司" in text
    assert len(d.tables) >= 5
    hdr = " ".join(c.text for c in d.tables[0].rows[0].cells)
    assert "内容（大写）" in hdr


def test_service_plan_outline_aligns_with_human_bid():
    """服务方案按人工稿 5 章 23 节组织，目标约 9.4 万字。"""
    from app.services.bidding_bid_builder import _PLAN_CHAPTERS

    titles = [c[0] for c in _PLAN_CHAPTERS]
    assert titles == ["第一章 对项目的理解", "第二章 项目组织实施方案",
                      "第三章 质量保障措施", "第四章 进度安排", "第五章 安全保密保障措施"]
    subs = [s for _, ss in _PLAN_CHAPTERS for s in ss]
    assert len(subs) == 23
    total = sum(s[2] for s in subs)
    assert 90000 <= total <= 100000


@pytest.mark.asyncio
async def test_cover_and_personnel_table_match_human_bid(tmp_path):
    """封面按人工稿：60pt 仿宋标题、无【正/副本】、日期带「日期：」；证书编号不拿证书名顶。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    people = [
        {"id": 0, "name": "张三", "gender": "男", "age": 40, "education": "本科",
         "major": "测绘", "title": "高级工程师", "role": "项目负责人",
         "certs": ["注册测绘师"], "cert_no": "CH-001"},
        {"id": 0, "name": "李四", "gender": "女", "age": 35, "education": "本科",
         "major": "土地", "title": "工程师", "role": "项目组成员", "certs": []},
    ]
    out = tmp_path / "封面测试.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=1, personnel=people, use_llm=False)
    d = Document(str(out))
    ps = [p for p in d.paragraphs if p.text.strip()]
    title = ps[0]
    assert title.text.replace(" ", "") == "投标文件"
    assert title.runs[0].font.size.pt == 60
    assert "【正/副本】" not in "\n".join(p.text for p in ps)
    assert any(p.text.startswith("日期：") for p in ps)
    hdr = next(t for t in d.tables
               if "资格证书编号" in " ".join(c.text for c in t.rows[0].cells))
    vals = [[c.text for c in r.cells] for r in hdr.rows[1:]]
    assert vals[0][-1] == "CH-001"
    assert vals[1][-1] == "—"


@pytest.mark.asyncio
async def test_quotation_tables_follow_tender_template(tmp_path):
    """开标一览表单位是元、标题行是「报价」；明细报价表按地块拆项且合计一致。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "报价测试.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=2026, use_llm=False)
    d = Document(str(out))

    opening = next(t for t in d.tables
                   if "内容（小写）" in " ".join(c.text for c in t.rows[0].cells))
    row = [c.text.strip() for c in opening.rows[1].cells]
    assert row == ["报价", "370000 元", "叁拾柒万元整"]

    detail = next(t for t in d.tables
                  if "分项总报价" in " ".join(c.text for c in t.rows[0].cells))
    items = [[c.text.strip() for c in r.cells] for r in detail.rows[1:4]]
    assert len(items) == 3
    assert all("地块" in x[1] for x in items)
    assert sum(int(x[4]) for x in items) == 370000
    total_txt = " ".join(c.text for r in detail.rows[4:] for c in r.cells)
    assert "（小写）370000 元" in total_txt
    assert "叁拾柒万元整" in total_txt

    # 表格数据上下左右居中
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    for t in d.tables:
        for r in t.rows:
            for c in r.cells:
                assert c.vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER
                assert c.paragraphs[0].alignment in (None, 1)

    text = "\n".join(p.text for p in d.paragraphs)
    assert "2026年2月3日" not in text


@pytest.mark.asyncio
async def test_authorization_follows_tender_template(tmp_path):
    """授权委托书按招标第六章模板逐栏出，不用素材里的旧“本授权书宣告”版式。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "授权书测试.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=2026, use_llm=False)
    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    for kw in ("2、法定代表人授权委托书：", "法定代表人授权书",
               "系中华人民共和国合法企业。法定地址：",
               "特授权张红艳代表我公司（单位）全权办理针对",
               "被授权代表人情况：",
               "姓名：张红艳", "身份证号码：320829198709070629",
               "办公电话：", "通讯地址：", "被授权代表人签名：",
               "单位名称（公章）：江苏众拓测绘有限公司", "法定代表人签名：陈春"):
        assert kw in text, kw
    assert "本授权书宣告" not in text
    assert "法定代表人身份证复印件：" in text
    assert "被授权代表身份证复印件：" in text


@pytest.mark.asyncio
async def test_second_page_is_toc(tmp_path):
    """第二页是目录：封面后插「目  录」+ TOC 域，8 个部分挂 Heading 1。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "目录测试.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=2026, use_llm=False)
    d = Document(str(out))
    assert any(p.text.strip() == "目  录" for p in d.paragraphs)
    # 有 soffice 时目录已写成静态条目（带页码）；没有则保留 TOC 域
    texts = [p.text for p in d.paragraphs]
    has_static = any(t.startswith("（一）投标函\t") for t in texts)
    assert has_static or 'TOC \\o "1-1"' in d.element.xml
    assert d.settings.element.xml.find("updateFields") >= 0 or has_static
    heads = [p.text for p in d.paragraphs if p.style.name == "Heading 1"]
    assert len(heads) == 8
    assert heads[0] == "（一）投标函" and heads[-1] == "（八）其他"
