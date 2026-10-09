# -*- coding: utf-8 -*-
"""默认单章 **user** prompt（`chapter_base._default_user_prompt`）按域装配 —— 回归护栏。

`_default_user_prompt` 把「写作材料」按优先级排开：材料1 = 规范 + 优秀范文。它原先：
  · 抬头**写死 DB32/T4013-2021**（稳评规范）—— 复垦章被要求「优先参考稳评规范」；
  · 往里注入 `data/template_chapters.json` 切出的**稳评模板段落**（写作标杆）。

`act()` 的公共下游另有两处同病（都随域而定）：自定义 prompt 分支的兜底身份（写死
「资深稳评工程师」）、每章前置的「## 要求」写作引导（身份 + 稳评小节示例）。

本测试锁两件事：
  · 稳评（默认域）逐字等于改动前（下列字面量写死，改动即红）；
  · 复垦换复垦口径、且不再出现「稳评 / DB32 / 社会稳定风险评估 / 拟征收土地」。
不触网、不碰真实库：DB 型来源一律打桩。
"""
import pytest

from app.agent import chapter_catalog as cat


# ═══════════════════════════════════════════════════════════════
# 缝隙函数：稳评逐字 / 复垦换口径
# ═══════════════════════════════════════════════════════════════

_MATERIAL1_STABILITY = "### 材料1：DB32/T4013-2021规范 + 优秀范文（最重要，优先参考）\n"
_CUSTOM_PERSONA_STABILITY = "你是一位资深稳评工程师。"
_WRITING_GUIDE_STABILITY = (
    "\n\n## 要求\n"
    "1. 你是一位资深稳评工程师，根据专业知识决定本章每个小节下的内容深度\n"
    "2. 数据用「材料3：本项目数据」中提供的，没有的不编造\n"
    "3. 地名/村名/社区名必须来自项目数据，禁止使用LLM内部知识中的地名\n"
    "4. 写短句，像老工程师写报告，不要AI翻译腔\n"
    "5. 不用「第一/第二/第三」排比，不用「具有重要/切实/有力」等套词\n"
    "6. 🔴 子标题结构：严格用下方「本章结构规范」/「本章必须使用的子标题」给出的编号标题"
    "（如 4.1 合法性分析、6.1 量化评分指标体系）。禁止自创数字编号标题，"
    "禁止用「六、」「八、」中文序号标题；更细的分点用（一）（二）（三）写在正文里，不用编号标题。\n"
    "7. 表格只在必要时使用，不要为填模板而加表\n"
)


def test_material_heading_stability_byte_identical_reclamation_swapped():
    assert cat.material_heading("stability") == _MATERIAL1_STABILITY
    assert cat.material_heading(None) == _MATERIAL1_STABILITY
    r = cat.material_heading("reclamation")
    assert "DB32" not in r and "稳评" not in r
    # 复垦点名其编制依据（对齐 reclamation.py 的身份提示词）
    for token in ("《土地复垦条例》", "《土地复垦条例实施办法》", "TD/T 1031"):
        assert token in r
    assert r.endswith("\n") and r.startswith("### 材料1：")


def test_custom_prompt_persona_stability_byte_identical_reclamation_swapped():
    assert cat.custom_prompt_persona("stability") == _CUSTOM_PERSONA_STABILITY
    assert cat.custom_prompt_persona(None) == _CUSTOM_PERSONA_STABILITY
    r = cat.custom_prompt_persona("reclamation")
    from app.domains.reclamation import RECLAMATION_IDENTITY_PROMPT
    assert r == RECLAMATION_IDENTITY_PROMPT
    assert "稳评" not in r and "社会稳定风险评估" not in r


def test_writing_guide_stability_byte_identical_reclamation_swapped():
    assert cat.writing_guide("stability") == _WRITING_GUIDE_STABILITY
    assert cat.writing_guide(None) == _WRITING_GUIDE_STABILITY
    r = cat.writing_guide("reclamation")
    assert "稳评" not in r
    assert "4.1 合法性分析" not in r and "6.1 量化评分指标体系" not in r
    assert "土地复垦方案编制专家" in r
    assert r.startswith("\n\n## 要求\n") and r.endswith("\n")


def test_headings_for_prompt_reclamation_empty_stability_delegates():
    from app.agent.template_headings import format_headings_for_prompt
    for n in range(2, 15):
        assert cat.headings_for_prompt("stability", n) == format_headings_for_prompt(n)
    # 复垦不单独给稳评子标题（其小节名已并入「本章结构规范」）
    assert cat.headings_for_prompt("reclamation", 3) == ""


def test_template_reference_for_prompt_reclamation_omits(monkeypatch):
    # 稳评：委托模板内容提供器（打桩，免读库/文件）
    import app.agent.template_content as tc
    monkeypatch.setattr(tc.template_content_provider, "build_template_prompt",
                        lambda ch, data: f"TEMPLATE_SENTINEL{ch}", raising=True)
    assert cat.template_reference_for_prompt("stability", 3, {}) == "TEMPLATE_SENTINEL3"
    assert cat.template_reference_for_prompt(None, 3, {}) == "TEMPLATE_SENTINEL3"
    # 复垦：无对标模板库 → 空，且**不调用**提供器（否则会读稳评模板 json）
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        return "SHOULD_NOT_APPEAR"

    monkeypatch.setattr(tc.template_content_provider, "build_template_prompt", _boom, raising=True)
    assert cat.template_reference_for_prompt("reclamation", 3, {}) == ""
    assert called["n"] == 0


# ═══════════════════════════════════════════════════════════════
# agent 级：`_default_user_prompt` 实际装配出的文本
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def _stub_external(monkeypatch):
    """中和会读真实库/文件的来源，focus 在本测试关心的材料块上。"""
    import app.agent.agents.chapters.chapter_base as cb
    import app.services.few_shot_service as fss
    monkeypatch.setattr(cb, "_get_cached_learning_hints",
                        lambda n, domain="stability": "", raising=True)
    monkeypatch.setattr(fss, "get_cached_few_shot",
                        lambda n, domain="stability": "", raising=True)


def _state(domain):
    return {"_domain": domain, "report_title": "某项目", "project_context": "位置X",
            "filled_data": {"implement_unit": "某某有限公司"}, "messages": []}


_RAG = {"chapter_context": "CHCTX", "example_context": "EXCTX", "local_regulation_context": "LOCTX"}
_UD = {"k1": "v1", "pdf_raw_text": "PDFTXT"}


def _user_prompt(domain, chapter):
    from app.agent.agents.chapters import get_chapter_agent
    a = get_chapter_agent(chapter, domain=domain)
    return a._default_user_prompt(_state(domain), _RAG, _UD)


@pytest.mark.parametrize("chapter", [1, 2, 3])
def test_reclamation_user_prompt_has_no_stability_flavour(chapter, _stub_external):
    p = _user_prompt("reclamation", chapter)
    for bad in ("稳评", "DB32", "社会稳定风险评估", "拟征收土地"):
        assert bad not in p
    # 材料1 抬头是复垦编制规范
    assert "### 材料1：复垦编制规范" in p
    assert "《土地复垦条例》" in p


def test_reclamation_user_prompt_omits_stability_template_paragraph(monkeypatch, _stub_external):
    import app.agent.template_content as tc
    monkeypatch.setattr(tc.template_content_provider, "build_template_prompt",
                        lambda ch, data: "TEMPLATE_SENTINEL_SHOULD_NOT_APPEAR", raising=True)
    p = _user_prompt("reclamation", 3)
    assert "TEMPLATE_SENTINEL_SHOULD_NOT_APPEAR" not in p
    # 稳评仍注入该段落（证明打桩打通、且差异是真实的）
    q = _user_prompt("stability", 3)
    assert "TEMPLATE_SENTINEL_SHOULD_NOT_APPEAR" in q


def test_stability_user_prompt_keeps_db32_material1_and_template(monkeypatch, _stub_external):
    import app.agent.template_content as tc
    monkeypatch.setattr(tc.template_content_provider, "build_template_prompt",
                        lambda ch, data: "TEMPLATE_SENTINEL", raising=True)
    p = _user_prompt("stability", 3)
    assert _MATERIAL1_STABILITY.strip() in p
    assert "TEMPLATE_SENTINEL" in p
    assert "### 材料3：本项目数据" in p
