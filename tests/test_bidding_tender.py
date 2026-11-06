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
