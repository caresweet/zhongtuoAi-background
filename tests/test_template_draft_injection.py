# -*- coding: utf-8 -*-
"""模板底稿注入（复垦/投标）与投标模块写作的测试。

业主口径（2026-10-30）：
  「部分内容不需要动的就不动，需要 AI 去做的就让 agent 根据模板和提供的知识库
   上的内容进行拟人构写；知识库指的是知识，模板才是文档。」

钉住三件事：
  1. 模板 docx 能按 Heading 1 切章，章号 1:1 对应报告章号；
  2. 底稿块带上「不需要动的不动 / 要写的重新拟 / 别的项目事实不得照抄」三条规则；
  3. 投标模块：llm 类真写正文，资料类（asset/personnel）不下发写作。
"""
import pytest

from app.services.template_chapter_chunker import (
    chunk_docx_by_headings, build_template_block_from_path,
)


def _make_template(tmp_path, chapters):
    """造一份「1 前言 / 1.1 小节 / 正文」结构的模板 docx。"""
    from docx import Document

    d = Document()
    for no, title, body in chapters:
        d.add_paragraph(f"{no} {title}", style="Heading 1")
        d.add_paragraph(f"{no}.1 小节", style="Heading 2")
        d.add_paragraph(body)
    p = tmp_path / "tpl.docx"
    d.save(str(p))
    return str(p)


def test_chunk_splits_by_heading1(tmp_path):
    path = _make_template(tmp_path, [
        (1, "前 言", "项目背景材料。"),
        (2, "编制总则", "编制依据与原则。"),
    ])
    ch = chunk_docx_by_headings(path)
    assert sorted(ch) == [1, 2]
    assert ch[2]["title"] == "编制总则"
    assert "编制依据与原则" in ch[2]["text"]
    # 章内小节标题与正文都在
    assert "2.1 小节" in ch[2]["text"]


def test_template_block_carries_rules(tmp_path):
    path = _make_template(tmp_path, [(3, "项目概况", "项目建设地点：某某村。")])
    block = build_template_block_from_path(path, 3)
    assert "本章模板底稿" in block
    assert "不需要动的不动" in block
    assert "要写的地方重新拟" in block
    assert "不得照抄" in block
    assert "项目建设地点" in block


def test_template_block_missing_chapter_or_path():
    assert build_template_block_from_path(None, 3) == ""
    assert build_template_block_from_path("/nope/x.docx", 3) == ""


def test_custom_prompt_path_gets_template_block(tmp_path):
    """UI 走 custom prompt（覆盖 agent 自带提示词）时，也必须带上「本章模板底稿」。

    2026-11-02 核查发现：`_use_custom_prompt` 分支此前只给章节要点 + 事实卡 + RAG，
    **没有模板原文** —— LLM 等于凭空写，「按模板对应小节构写」没落地。
    """
    from app.agent.agents.chapters.reclamation_generic_agent import (
        ReclamationGenericChapterAgent,
    )
    path = _make_template(tmp_path, [(3, "项目概况", "项目建设地点：某某村。")])
    agent = ReclamationGenericChapterAgent(3)
    out = agent._append_template_block("原始提示词", {"template_path": path})
    assert out.startswith("原始提示词")
    assert "本章模板底稿" in out
    assert "项目建设地点" in out
    # 没有选定模板 → 不报错：库里有复垦范文时补「同章多模板参考」，没有就原样返回
    _no_tpl = agent._append_template_block("x", {})
    assert _no_tpl == "x" or ("同章多模板参考" in _no_tpl and _no_tpl.startswith("x"))


class _FakeLLM:
    def __init__(self, text="## 技术方案\n\n正文内容。"):
        self.text = text
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return self.text


@pytest.mark.asyncio
async def test_bidding_agent_writes_llm_modules(monkeypatch):
    from app.agent.agents.bidding_chapter_agent import BiddingChapterAgent

    agent = BiddingChapterAgent(
        _FakeLLM(), "tender_response", 4,
        {"title": "技术方案·项目理解与需求分析", "source": "llm",
         "description": "说明", "key_points": ["要点一"]},
    )
    # 不碰真模板（38MB）与真 RAG
    monkeypatch.setattr(agent, "_template_outline", _noop_outline)
    monkeypatch.setattr(agent, "_rag_text", _noop_rag)

    res = await agent.act({"session_id": "s", "_bidding_data": {"bid_project_name": "某项目"}}, {})
    assert res["status"] == "completed"
    assert res["markdown"].lstrip().startswith("##")


@pytest.mark.asyncio
async def test_bidding_agent_skips_asset_modules(monkeypatch):
    from app.agent.agents.bidding_chapter_agent import BiddingChapterAgent

    llm = _FakeLLM()
    agent = BiddingChapterAgent(
        llm, "tender_response", 2,
        {"title": "资格证明文件", "source": "asset"},
    )
    res = await agent.act({}, {})
    assert res["status"] == "skipped"
    assert llm.calls == 0      # 资料类模块绝不调模型


@pytest.mark.asyncio
async def test_bidding_agent_rejects_placeholder_output(monkeypatch):
    from app.agent.agents.bidding_chapter_agent import BiddingChapterAgent

    agent = BiddingChapterAgent(
        _FakeLLM("【待生成】"), "tender_response", 4,
        {"title": "技术方案", "source": "llm"},
    )
    monkeypatch.setattr(agent, "_template_outline", _noop_outline)
    monkeypatch.setattr(agent, "_rag_text", _noop_rag)

    res = await agent.act({}, {})
    # 模型没写出有效正文 → 标记失败，调用方据此保留模板原文
    assert res["status"] == "failed"


async def _noop_outline(state):
    return ""


async def _noop_rag(state, title):
    return ""
