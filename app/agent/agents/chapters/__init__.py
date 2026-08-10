"""Chapter agents — one agent per report chapter (1-10).

Each agent handles:
- Chapter-specific RAG retrieval from knowledge base
- LLM generation with project data + RAG context
- Content validation and state updates

Factory:
    CHAPTER_AGENT_MAP: Dict[int, Type[ChapterAgentBase]] — chapter number → agent class
    get_chapter_agent(chapter_number, llm_service) → ChapterAgentBase instance
"""

from typing import Dict, Type, Optional

from .chapter_base import ChapterAgentBase
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


CHAPTER_AGENT_MAP: Dict[int, Type[ChapterAgentBase]] = {
    1: Chapter1Agent,
    2: Chapter2Agent,
    3: Chapter3Agent,
    4: Chapter4Agent,
    5: Chapter5Agent,
    6: Chapter6Agent,
    7: Chapter7Agent,
    8: Chapter8Agent,
    9: Chapter9Agent,
    10: Chapter10Agent,
}


def get_chapter_agent(
    chapter_number: int,
    llm_service=None,
) -> Optional[ChapterAgentBase]:
    """Factory: create a chapter agent instance for the given chapter number.

    Args:
        chapter_number: 1-10.
        llm_service: Optional LLM service for AI-powered generation.

    Returns:
        ChapterAgentBase instance or None if chapter_number is invalid.
    """
    agent_cls = CHAPTER_AGENT_MAP.get(chapter_number)
    if agent_cls is None:
        return None
    return agent_cls(llm_service=llm_service)


__all__ = [
    "ChapterAgentBase",
    "Chapter1Agent", "Chapter2Agent", "Chapter3Agent", "Chapter4Agent",
    "Chapter5Agent", "Chapter6Agent", "Chapter7Agent", "Chapter8Agent",
    "Chapter9Agent", "Chapter10Agent",
    "CHAPTER_AGENT_MAP",
    "get_chapter_agent",
]
