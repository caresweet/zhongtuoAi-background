# -*- coding: utf-8 -*-
"""版式审核 Agent：DOCX / PDF / 图片三类输入的区块识别与异常汇总。"""

from __future__ import annotations

import asyncio
import json

from docx import Document
from docx.shared import Pt

from app.services.layout_audit import audit_document


def _make_docx(path):
    d = Document()
    d.sections[0].header.paragraphs[0].text = "测试报告页眉"
    h1 = d.add_heading("第一章 编制说明", 1)
    # 故意跳级：h1 → h3，应报 heading_level_skip
    d.add_paragraph("跳级的三级标题", style="Heading 3")
    d.add_paragraph("（一）稳评理由")
    d.add_paragraph("1.决策名称")
    d.add_paragraph("（1）第一项内容")
    d.add_paragraph("表1-1 项目基本情况")
    d.add_paragraph("图1-1 位置示意图")
    fake = d.add_paragraph("这是一段很长的正文内容，用来验证大号正文伪装标题的识别能力，句末还有标点。")
    fake.runs[0].font.size = Pt(20)
    for _ in range(3):
        d.add_paragraph("这是一段正常的正文内容，字号与正文一致，用于统计正文基准字号。")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "项目名称"
    t.cell(0, 1).text = "测试项目"
    d.save(str(path))


def test_docx_block_types_and_anomalies(tmp_path):
    path = tmp_path / "layout.docx"
    _make_docx(path)
    res = asyncio.run(audit_document(path))

    assert res["mode"] == "structured_docx"
    types = {b["block_type"] for b in res["blocks"]}
    assert {"h1", "h2", "h3", "body", "table_caption", "figure_caption",
            "list_item", "table_cell", "header"} <= types
    # 原生样式 → 高置信度
    h1 = next(b for b in res["blocks"] if b["block_type"] == "h1")
    assert h1["confidence"] >= 0.9
    # 异常：伪装标题 + 标题跳级
    atypes = {a["type"] for a in res["summary"]["anomalies"]}
    assert "fake_heading" in atypes
    assert "heading_level_skip" in atypes
    assert res["summary"]["chapter_tree"]
    assert "章节树" in res["text_summary"]


def test_pdf_heading_inference(tmp_path):
    import fitz

    path = tmp_path / "layout.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Chapter One", fontsize=22)
    page.insert_text((72, 140), "Section A", fontsize=16)
    page.insert_text((72, 200),
                     "This is the body paragraph text for testing layout audit.",
                     fontsize=11)
    page.insert_text((72, 820), "1", fontsize=9)
    doc.save(str(path))
    doc.close()

    res = asyncio.run(audit_document(path))
    assert res["mode"] == "structured_pdf"
    types = {b["block_type"] for b in res["blocks"]}
    assert "h1" in types and "h2" in types and "body" in types
    assert "footer" in types
    # PDF 推断标题：置信度 0.6~0.8
    h1 = next(b for b in res["blocks"] if b["block_type"] == "h1")
    assert 0.6 <= h1["confidence"] <= 0.8
    assert h1["layout"]["bbox"]


def test_visual_is_marked_as_estimate(tmp_path, monkeypatch):
    from app.services import llm_service as mod

    async def fake_chat(text, image_base64, media_type="image/png",
                        system=None, max_tokens=2048):
        return json.dumps({"blocks": [{
            "block_id": "x", "block_type": "h1", "content": "第一章 测试",
            "style": {"size_hint": "large", "bold_hint": "yes"},
            "layout": {"bbox": [0.1, 0.1, 0.9, 0.2], "align": "center",
                       "column": None},
            "confidence": 0.95,
        }]})

    monkeypatch.setattr(mod.llm_service, "chat_with_image", fake_chat)
    path = tmp_path / "page.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = asyncio.run(audit_document(path))
    assert res["mode"] == "visual_estimate"
    b = res["blocks"][0]
    assert b["style"]["source"] == "visual_estimate"
    assert b["style"]["font_name"] is None
    assert b["style"]["font_size"] is None
    assert b["confidence"] <= 0.7
