"""复垦会话「身份/标题/报告名」不再穿稳评装束 —— 渲染侧最后一类泄漏的回归护栏。

本分支要治的类：稳评**章标题** / 稳评**身份串** / 「社会稳定风险评估报告」**报告名**
被**渲染给用户**或**喂给复垦会话会执行的提示词**。三处已修：

  · `chapter_orchestrator._present_for_review` / `_request_and_wait_for_data`
    的章标题（章节审核 UI 与数据请求 UI）—— 见 test_present_for_review_*。
  · `master._handle_chat` 的「打招呼」身份串 —— 见 test_greeting_*。
  · `report.py` WS 聊天兜底的 system prompt —— 见 test_chat_system_prompt_*。

每处都走 `chapter_catalog` 收口，稳评（默认域）逐字不变。
"""
import asyncio

from app.agent import chapter_catalog as cat


# ═══════════════════════════════════════════════════════════════
# 缝隙函数本身：稳评逐字，复垦换口径
# ═══════════════════════════════════════════════════════════════

_GREETING_STABILITY = (
    "您好！我是社会稳定风险评估报告编制专家。请上传项目资料（PDF/图片）或描述"
    "项目基本信息（位置、面积、文号等），我可以帮您生成社会稳定风险评估报告。"
)
_SYSTEM_STABILITY = (
    "你是社会稳定风险评估报告编制助手。\n"
    "你的职责：引导用户提供项目信息，解答稳评相关问题。\n"
    "如果用户发送了文件或图片，请确认已收到并简要说明文件内容。\n"
    "风格：专业但友好，简洁直接，不说废话。\n"
)


def test_chat_greeting_stability_byte_identical_reclamation_swapped():
    # 稳评（含不传域的默认）逐字等于改动前 master.py 里写死的那句
    assert cat.chat_greeting("stability") == _GREETING_STABILITY
    assert cat.chat_greeting(None) == _GREETING_STABILITY
    # 复垦不点名稳评，改称复垦
    r = cat.chat_greeting("reclamation")
    assert "复垦" in r and "社会稳定风险评估" not in r and "稳评" not in r


def test_chat_system_prompt_stability_byte_identical_reclamation_swapped():
    assert cat.chat_system_prompt("stability") == _SYSTEM_STABILITY
    assert cat.chat_system_prompt(None) == _SYSTEM_STABILITY
    r = cat.chat_system_prompt("reclamation")
    assert r.startswith("你是土地复垦方案编制助手。")
    assert "社会稳定风险评估" not in r and "稳评" not in r


# ═══════════════════════════════════════════════════════════════
# Site 2：master 问候
# ═══════════════════════════════════════════════════════════════

def _greeting_for(state):
    from app.agent.agents.master import MasterAgent

    m = MasterAgent(llm_service=None)
    m._add_to_history = lambda *a, **k: None

    async def fake_emit(msg, *a, **k):
        m._last = msg

    m._emit_message = fake_emit
    r = asyncio.run(m._handle_chat(state, "你好"))
    return r["message"]


def test_greeting_reclamation_shows_reclamation_persona():
    msg = _greeting_for({"messages": [], "_domain": "reclamation"})
    assert msg == cat.chat_greeting("reclamation")
    assert "社会稳定风险评估" not in msg


def test_greeting_stability_unchanged_by_default():
    # 显式稳评与「不钉域」两种情况都仍是原稳评问候
    assert _greeting_for({"messages": [], "_domain": "stability"}) == _GREETING_STABILITY
    assert _greeting_for({"messages": []}) == _GREETING_STABILITY


def test_greeting_bidding_unchanged():
    msg = _greeting_for({"messages": [], "_conversation_domain": "bidding"})
    assert msg.startswith("您好！我是招标投标文件编写专家")


# ═══════════════════════════════════════════════════════════════
# Site 1：章节审核 / 数据请求 UI 的章标题
# ═══════════════════════════════════════════════════════════════

def _review_title(domain, chapter=3):
    from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

    orch = ChapterOrchestrator(llm_service=None)
    box = {}

    async def fake_emit(evt, data, *a, **k):
        box[evt] = data

    orch._emit = fake_emit
    state = {"chapters": {chapter: {"markdown": "x" * 100, "tables": []}}}
    if domain:
        state["_domain"] = domain
    asyncio.run(orch._present_for_review(chapter, state))
    return box["chapter_review_prompt"]["title"]


def test_present_for_review_title_by_domain():
    assert _review_title("reclamation") == cat.chapter_title("reclamation", 3) == "项目概况"
    # 稳评（默认）逐字不变：读死稳评目录时的取值就是它
    from app.agent.state import CHAPTER_DEFINITIONS
    assert _review_title("stability") == CHAPTER_DEFINITIONS[3]["title"]
    assert _review_title(None) == CHAPTER_DEFINITIONS[3]["title"]


def test_chapter_title_seam_matches_legacy_expression_for_stability():
    """`catalog.chapter_title('stability', n)` 必须逐字等于旧的 `CHAPTER_DEFINITIONS.get(...)`
    表达式 —— 这是 site 1 稳评不变的依据。"""
    from app.agent.state import CHAPTER_DEFINITIONS
    for n in list(CHAPTER_DEFINITIONS) + [99]:
        legacy = CHAPTER_DEFINITIONS.get(n, {}).get("title", f"第{n}章")
        assert cat.chapter_title("stability", n) == legacy
        assert cat.chapter_title(None, n) == legacy
