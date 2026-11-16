# -*- coding: utf-8 -*-
"""行政区划识别/核验 + 复垦批注修补（水田、运距、请示抬头、公式、法规重复）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import admin_division as ad          # noqa: E402
from app.services.report_content_repairs import fix_government_names  # noqa: E402
from app.services.reclamation_content_repairs import (  # noqa: E402
    fix_direction_wording, fix_haul_distance, fix_investment_wording,
    fix_price_formula, fix_regulation_duplicates, fix_request_authority,
)


def test_parse_division_from_location():
    d = ad.parse_division("淮安市淮阴区马头镇运河里一号地块土地征收决策")
    assert d["city"] == "淮安市"
    assert d["county"] == "淮阴区"
    assert d["township"] == "马头镇"

    d2 = ad.parse_division("淮安市淮安区钦工镇宋集村临时用地")
    assert d2["county"] == "淮安区"
    assert d2["township"] == "钦工镇"


def test_government_name_by_type():
    assert ad.government_name("马头镇") == "马头镇人民政府"
    assert ad.government_name("钦工镇") == "钦工镇人民政府"
    assert ad.government_name("宋集乡") == "宋集乡人民政府"
    assert ad.government_name("淮城街道") == "淮城街道办事处"


def test_natural_resources_bureau():
    assert ad.natural_resources_bureau("淮安市", "淮安区") == \
        "淮安市自然资源和规划局淮安分局"


def test_resolve_local_writes_filled():
    filled = {"location": "淮安市淮阴区马头镇运河里一号地块"}
    info = ad.resolve_local(filled)
    assert info["township"] == "马头镇"
    assert filled["town"] == "马头镇"
    assert filled["township_type"] == "镇"
    assert filled["township_gov"] == "马头镇人民政府"


def test_fix_government_names_replaces_wrong_street():
    filled = {"township": "马头镇", "township_type": "镇"}
    md = ("2.街道（淮阴区马头镇）\n"
          "安置人员名单经社区商定、街道办事处审核、在社区公示。")
    out, n = fix_government_names(md, filled)
    assert "镇（淮阴区马头镇）" in out
    assert "马头镇人民政府审核" in out
    assert "街道办事处审核" not in out
    assert n >= 2


def test_verify_async_uses_web_and_cache(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    async def fake_search(query, top_k=10):
        return [{"title": "马头镇人民政府", "link": "https://www.gov.cn/x",
                 "snippet": "淮阴区马头镇人民政府"}]

    import app.services.web_opinion as wo
    monkeypatch.setattr(wo, "search_web", fake_search)

    filled = {"location": "淮安市淮阴区马头镇运河里一号地块"}
    info = asyncio.run(ad.verify_async(filled))
    assert info["township"] == "马头镇"
    assert filled["admin_verified"] is True
    assert filled["admin_sources"]


def test_reclamation_direction_wording():
    filled = {"reclamation_direction": "旱地"}
    md = "以保证水田地面坡度小于3°，复垦为水田。"
    out, n = fix_direction_wording(md, filled)
    assert "水田" not in out
    assert "旱地地面坡度" in out and "复垦为旱地" in out
    assert n == 2


def test_reclamation_haul_distance_unified():
    filled = {"haul_distance_km": "3.5"}
    md = "清理的碎石碎渣将送至0.5km外的农村道路整修使用。"
    out, n = fix_haul_distance(md, filled)
    assert "3.5km" in out and "0.5km" not in out
    assert n == 1


def test_haul_target_prefers_explicit_distance():
    from app.services.reclamation_content_repairs import haul_distance_target
    chapters = {
        5: {"markdown": "清理的碎石碎渣将送至0.5km外的农村道路整修使用。"},
        6: {"markdown": "外运材料用于村内道路垫层，运距约3.5km。"},
    }
    assert haul_distance_target({}, chapters) == "3.5"


def test_request_authority_and_investment_wording():
    filled = {"city": "淮安市", "county": "淮安区",
              "dynamic_total_investment": "57.48 万元",
              "_budget_estimate": {"static_total": 52.14}}
    md = "淮安市自然资源和规划局：\n估算总投资 57.48 万元。"
    out, _ = fix_request_authority(md, filled)
    out, _ = fix_investment_wording(out, filled)
    assert "淮安市自然资源和规划局淮安分局：" in out
    assert "动态总投资 57.48 万元（静态 52.14 万元）" in out


def test_price_formula_and_regulation_duplicate():
    out, n = fix_price_formula("计算公式：\nWi=\nWi——项目开工第 i 年价差预备费")
    assert "Wi = ai × [(1 + i)^ni − 1]" in out and n == 1

    out2, n2 = fix_regulation_duplicates(
        "《土地复垦方案编制规程第6部分：第6部分：建设项目》")
    assert "第6部分：第6部分" not in out2
    assert "第6部分：建设项目" in out2
    assert n2 == 1


def test_appendix_numbers_renumbered_consistently():
    from docx import Document
    from app.services.report_assembler import ReportAssembler

    doc = Document()
    doc.add_paragraph("附件清单")
    doc.add_paragraph("附件1 编制单位资质证书")
    doc.add_page_break()
    doc.add_heading("附件2 编制单位资质证书", level=2)

    ReportAssembler._renumber_appendix_numbers(doc, 0)
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    assert texts.count("附件1 编制单位资质证书") == 2
    assert "附件2 编制单位资质证书" not in texts
