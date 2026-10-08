"""章节 agent 工厂 —— 按开关在「新管线」与「旧管线」之间分派。

对外签名保持不变（调用方无需改动）：

    get_chapter_agent(n, llm_service) -> 具备 run(state, queue) 的对象
    get_review_table_agent()          -> 具备 render_into(doc, ...) 的对象

开关：`settings.CHAPTER_PIPELINE`
    "template_fill" → 新管线（模板填空 + 缺口补写 + 规则校验）
    "legacy"        → 旧管线（LLM 自由写 markdown）—— 默认，便于回滚

两条编排路径都只经这两个工厂入口，因此切换开关即可整体换实现。
"""

from typing import Any, Dict, Optional, Type

from .review_table_agent import ReviewTableAgent


def _pipeline() -> str:
    try:
        from app.config import settings
        return str(getattr(settings, "CHAPTER_PIPELINE", "legacy") or "legacy").lower()
    except Exception:
        return "legacy"


# ── 新管线 ────────────────────────────────────────────────────

def _new_agent(chapter_number: int, llm_service=None):
    from ..chapter_writer_agent import ChapterWriterAgent
    return ChapterWriterAgent(chapter_number, llm_service=llm_service)


# ── 参照件照搬（优先级最高）────────────────────────────────────

def reference_verbatim_chapters() -> set:
    """要「原文照搬」的章号集合（配置驱动，将来改章号不动代码）。"""
    try:
        from app.services.reference_corpus import is_enabled, verbatim_chapters
        if not is_enabled():
            return set()
        return verbatim_chapters()
    except Exception:
        return set()


def _reference_agent(chapter_number: int, llm_service=None):
    from .chapter_reference_agent import ReferenceChapterAgent
    return ReferenceChapterAgent(chapter_number, llm_service=llm_service)


# ── 旧管线（惰性导入：新管线下不加载这 2000 行）────────────────

def _legacy_agent(chapter_number: int, llm_service=None):
    from .chapter_1_agent import Chapter1Agent
    from .chapter_2_agent import Chapter2Agent
    from .chapter_3_agent import Chapter3Agent
    from .chapter_4_agent import Chapter4Agent
    from .chapter_5_agent import Chapter5Agent
    from .chapter_6_agent import Chapter6Agent
    from .chapter_7_agent import Chapter7Agent
    from .chapter_8_agent import Chapter8Agent
    from .chapter_9_agent import Chapter9Agent
    from .chapter_10_agent import Chapter10Agent
    from .chapter_11_agent import Chapter11Agent
    from .chapter_12_agent import Chapter12Agent
    from .chapter_13_agent import Chapter13Agent
    from .chapter_14_agent import Chapter14Agent
    legacy_map = {
        1: Chapter1Agent, 2: Chapter2Agent, 3: Chapter3Agent, 4: Chapter4Agent,
        5: Chapter5Agent, 6: Chapter6Agent, 7: Chapter7Agent, 8: Chapter8Agent,
        9: Chapter9Agent, 10: Chapter10Agent, 11: Chapter11Agent,
        12: Chapter12Agent, 13: Chapter13Agent, 14: Chapter14Agent,
    }
    cls = legacy_map.get(chapter_number)
    return cls(llm_service=llm_service) if cls else None


# ── 对外入口 ──────────────────────────────────────────────────

def get_chapter_agent(chapter_number: int, llm_service=None):
    """创建某章 agent（按开关分派）。

    Args:
        chapter_number: 1-11（正文号；评审表见 get_review_table_agent）。
        llm_service: LLM 服务实例。

    Returns:
        具备 `run(state, queue)` 的 agent 对象；章号越界返回 None。
    """
    from app.agent.chapter_definitions import ALL_CHAPTERS
    if int(chapter_number) not in ALL_CHAPTERS:
        return None
    # 🔴 参照件照搬优先级最高：第 1/2/4/5/7 章直接沿用人工成品原文，零 LLM。
    #    编排层靠 status="reference_verbatim" 跳过质量门与重写。
    if int(chapter_number) in reference_verbatim_chapters():
        return _reference_agent(chapter_number, llm_service)
    # 第1章：骨架照搬知识库案卷 + agent 扩写（模型会改写/省略条目名，故结构锁定）
    if int(chapter_number) == 1:
        from .chapter_1_composer_agent import Chapter1ComposerAgent
        return Chapter1ComposerAgent(1, llm_service=llm_service)
    if _pipeline() == "template_fill":
        return _new_agent(chapter_number, llm_service)
    return _legacy_agent(chapter_number, llm_service)


def get_review_table_agent(llm_service=None) -> ReviewTableAgent:
    """评审表 agent（不编号前置部分；与管线开关无关，始终走制式表渲染）。"""
    return ReviewTableAgent(llm_service=llm_service)


def use_template_fill_pipeline() -> bool:
    """当前是否启用新管线（供编排层/前端展示）。"""
    return _pipeline() == "template_fill"


# 兼容既有引用：旧的 MAP 常量（新管线下为空，避免误用）
CHAPTER_AGENT_MAP: Dict[int, Any] = {}
REVIEW_TABLE_AGENT: Type[ReviewTableAgent] = ReviewTableAgent

__all__ = [
    "ReviewTableAgent",
    "get_chapter_agent",
    "get_review_table_agent",
    "use_template_fill_pipeline",
    "CHAPTER_AGENT_MAP",
    "REVIEW_TABLE_AGENT",
]
