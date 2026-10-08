"""章节工厂按 domain 分派；复垦标题不被稳评章节目录覆盖。"""
from app.agent.agents.chapters import get_chapter_agent
from app.agent.chapter_definitions import get_chapter_title


def test_reclamation_dispatches_to_reclamation_agent():
    a = get_chapter_agent(1, domain="reclamation")
    assert type(a).__name__ == "ReclamationChapter1Agent"


def test_reclamation_title_and_rag_query_not_overwritten_by_stability():
    """基类 __init__ 会从稳评 chapter_definitions 覆盖**两个**属性 —— 复垦必须都绕开。

    不是只覆盖标题：`ChapterAgentBase.__init__` 同时写入 `rag_query_extra`，
    而 `get_chapter_rag_query` 的兜底文案是「社会稳定风险评估报告 第N章」。
    只补标题的话，复垦章节会拿稳评的措辞去检索，**把稳评素材拉进来**。

    断言「与稳评同号章的不同」而非具体字符串，这样稳评改了自己的内容
    也不会让本测试假失败。
    """
    from app.agent.chapter_definitions import get_chapter_rag_query

    a = get_chapter_agent(3, domain="reclamation")
    assert a.chapter_title == "项目概况"
    assert a.chapter_title != get_chapter_title(3)
    # 检索词必须来自复垦，不能是稳评那份
    assert a.rag_query_extra != get_chapter_rag_query(3)
    assert "社会稳定风险评估" not in a.rag_query_extra


def test_reclamation_out_of_range_returns_none():
    assert get_chapter_agent(11, domain="reclamation") is None
    assert get_chapter_agent(14, domain="reclamation") is None


def test_stability_default_behaviour_unchanged():
    """不传 domain 时与改动前完全一致 —— 这是稳评的护栏。"""
    a = get_chapter_agent(8)
    assert a.chapter_title == get_chapter_title(8)
    assert type(a).__name__ != "ReclamationChapter1Agent"
