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
    opening = next(t for t in d.tables
                   if "内容（大写）" in " ".join(c.text for c in t.rows[0].cells))
    assert opening is not None
    # 投标函 13/14 项必须是表格（人工稿：关联单位表 + 地址/联系信息表）
    related = next(t for t in d.tables
                   if "与我方单位负责人为同一人的其他单位"
                   in " ".join(c.text for c in t.rows[0].cells))
    assert "江苏众拓项目代理咨询有限公司" in related.rows[0].cells[1].text
    contact = next(t for t in d.tables
                   if "传真" in [c.text for c in t.rows[0].cells])
    assert any("开户账号" in c.text for r in contact.rows for c in r.cells)


def test_service_plan_outline_aligns_with_human_bid():
    """服务方案按人工稿的分节粒度组织：5 章 57 节（人工稿 53 节 + 14 子节）。"""
    from app.services.bidding_bid_builder import _PLAN_CHAPTERS, _SECTION_TABLES

    titles = [c[0] for c in _PLAN_CHAPTERS]
    assert titles == ["第一章 对项目的理解", "第二章 项目组织实施方案",
                      "第三章 质量保障措施", "第四章 进度安排", "第五章 安全保密保障措施"]
    subs = [s for _, ss in _PLAN_CHAPTERS for s in ss]
    assert len(subs) == 57
    total = sum(s[2] for s in subs)
    assert 80000 <= total <= 95000
    # 每章都要有表（人工稿 29 张表；分节号必须真的存在于大纲里）
    nos = {s[0].split(" ", 1)[0] for s in subs}
    assert set(_SECTION_TABLES) <= nos
    assert len({k for ks in _SECTION_TABLES.values() for k in ks}) >= 25
    assert {"1.10", "4.7", "5.9"} <= set(_SECTION_TABLES)


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


def test_runaway_subsections_are_trimmed():
    """线上实测：让模型只写 4.1，它把 4.2～4.5 一并写了 —— 同章号出现两遍。

    每块只保留自己的小节，遇到编号不符的 2～3 级标题截断；子标题（4.1.1）保留。
    """
    from app.services.bidding_bid_builder import _drop_runaway_sections

    text = ("## 4.1 总体进度计划与编制依据\n\n" + "进度编制依据。" * 60
            + "\n\n### 4.1.1 合同工期\n\n" + "工期为90日。" * 60
            + "\n\n## 4.2 分阶段进度安排与节点控制\n\n" + "越界续写。" * 60)
    out = _drop_runaway_sections(text, "4.1")
    assert "## 4.1 总体进度计划与编制依据" in out
    assert "### 4.1.1 合同工期" in out
    assert "4.2 分阶段进度安排与节点控制" not in out

    # 模型一上来就写错号（正文太短）→ 原样返回，宁多不空
    tiny = "## 9.9 别的章节\n\n短正文。"
    assert _drop_runaway_sections(tiny, "4.1") == tiny


def test_tender_parses_duration_and_plots(tender):
    """工期与地块清单从招标文件取（表1-1、进度表要用），不写死 90 天/四个地块。"""
    assert tender["duration_days"] == 90
    plots = tender["plots"]
    assert [p["name"] for p in plots] == [
        "蜀浦路东西两侧地块", "湖光中路南侧地块", "航空产业园地块"]
    assert [p["area"] for p in plots] == ["300", "10.03", "131"]


def test_plan_tables_follow_human_bid():
    """方案表按人工稿表式：表头与列数一致，内容取项目事实（地块/人员/工期）。"""
    from app.services.bidding_bid_builder import _plan_ctx
    from app.services.bidding_plan_tables import PLAN_TABLES, build_table

    tender = {"project_name": "某地块用地报批服务项目", "purchaser": "某采购人",
              "duration_days": 90,
              "plots": [{"name": "A地块", "type": "批次用地报批", "area": "100",
                         "location": "某村"}]}
    people = [{"name": "张三", "role": "项目负责人", "title": "高级工程师",
               "major": "测绘", "certs": ["注册测绘师"]},
              {"name": "李四", "role": "项目组成员", "title": "工程师",
               "major": "土地", "certs": []}]
    ctx = _plan_ctx(tender, "某公司", people)

    assert len(PLAN_TABLES) == 29
    plots = build_table("plots", ctx)
    assert plots["headers"] == ["地块名称", "报批类型", "用地规模", "重点难点",
                                "特殊关注事项", "预计耗时"]
    assert plots["rows"][0][0].endswith("A地块")

    duties = build_table("duties", ctx)
    assert [r[1] for r in duties["rows"]] == ["张三", "李四"]

    gantt = build_table("gantt", ctx)
    assert len(gantt["headers"]) == 10          # 工作内容 + 9 个时间段
    assert all(len(r) == 10 for r in gantt["rows"])

    for key in PLAN_TABLES:
        spec = build_table(key, ctx)
        assert spec and spec["headers"] and spec["rows"], key
        assert all(len(r) == len(spec["headers"]) for r in spec["rows"]), key


@pytest.mark.asyncio
async def test_deviation_and_scoring_tables_match_human_bid(tmp_path):
    """偏离表逐条 6 行；评分导读表 9 项（4 客观 + 5 方案）。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "偏离评分.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=2026, use_llm=False)
    d = Document(str(out))
    dev = next(t for t in d.tables
               if "正偏离/负偏离" in "".join(c.text for c in t.rows[0].cells))
    assert len(dev.rows) == 7                     # 表头 + 6 条
    texts = [r.cells[2].text for r in dev.rows[1:]]
    assert any("服务期限" in x for x in texts)
    assert any("验收标准" in x for x in texts)
    assert any("人员配备" in x for x in texts)

    sc = next(t for t in d.tables
              if "自评分" in "".join(c.text for c in t.rows[0].cells))
    assert len(sc.rows) == 10                     # 表头 + 9 项
    names = [r.cells[1].text for r in sc.rows[1:]]
    for kw in ("对项目的理解", "项目组织实施方案", "质量保障措施",
               "进度安排", "安全保密保障措施"):
        assert kw in names
    # 客观分项的自评分按公司档案计算，不再写死
    assert sc.rows[2].cells[4].text.isdigit()
    assert int(sc.rows[2].cells[4].text) <= 10


@pytest.mark.asyncio
async def test_default_personnel_is_leader_plus_five(tmp_path):
    """默认投入 1 名负责人 + 5 名组员（对齐人工稿，招标最低 1+3 只是底线）。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "人员六人.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=2026, use_llm=False)
    d = Document(str(out))
    pers = next(t for t in d.tables
                if "资格证书编号" in "".join(c.text for c in t.rows[0].cells))
    assert len(pers.rows) == 7                    # 表头 + 6 人


@pytest.mark.asyncio
async def test_toc_and_headings_follow_human_pdf(tmp_path):
    """目录黑体 20/14 且不加粗；部分标题宋体 15 不加粗（人工稿 PDF 实测）。"""
    from docx import Document
    from app.services.bidding_bid_builder import build_bid_document

    out = tmp_path / "目录格式.docx"
    await build_bid_document(tender_path=TENDER, company_id=4, price_wan=37,
                             out_path=out, seed=2026, use_llm=False)
    ps = [p for p in Document(str(out)).paragraphs if p.text.strip()]
    title = next(p for p in ps if p.text.strip() == "目  录")
    r = title.runs[0]
    assert r.font.name == "黑体" and r.font.size.pt == 20 and not r.bold
    entry = next(p for p in ps if p.text.startswith("（一）投标函\t"))
    r = entry.runs[0]
    assert r.font.name == "黑体" and r.font.size.pt == 14 and not r.bold
    part = next(p for p in ps if p.text.strip() == "（一）投标函")
    r = part.runs[0]
    assert r.font.name == "宋体" and r.font.size.pt == 15 and not r.bold
