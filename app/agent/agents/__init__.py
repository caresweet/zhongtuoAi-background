"""Agent system for intelligent report generation.

Multi-Agent Collaboration Architecture:
- MasterAgent: conversation + intent recognition → delegates to ChapterOrchestrator
- DataAnalysisAgent: analyzes PDFs/images, extracts per-chapter data
- ChapterOrchestrator: outline → chapter-by-chapter generation → quality review → review table
- ChapterAgents (1-10): one agent per chapter, RAG + user data driven

Collaboration Agents (NEW):
- KnowledgeAgent: 知识库模板/规范/格式检索专家，为ChapterAgent提供知识库上下文
- DataValidatorAgent: 数据完整性校验与缺口分析，生成前数据守门人
- FormatComplianceAgent: 输出格式合规性审核，确保符合企业规范
- CrossReferenceAgent: 跨章节一致性校验，检查数据/逻辑/术语统一性
- IntentClarificationAgent: LLM语义意图识别与澄清，反幻觉守卫，替代关键词匹配

Supporting Agents:
- ImageAnalysisAgent: vision API analysis of images
- QualityReviewAgent: cross-chapter consistency + colloquial detection + auto-fix
- ReviewTableAgent: generates review table from confirmed chapters

Generation Pipeline per chapter:
  DataValidator → KnowledgeAgent → ChapterAgent → FormatCompliance

Post-Generation Pipeline:
  CrossReferenceAgent + FormatComplianceAgent + QualityReviewAgent
"""

from .base_agent import BaseAgent
from .master import MasterAgent, create_master_agent
from .image_analyzer_agent import ImageAnalysisAgent
from .chapter_orchestrator import ChapterOrchestrator
from .data_analysis_agent import DataAnalysisAgent
from .quality_review_agent import QualityReviewAgent
from .review_table_agent import ReviewTableAgent

# Multi-Agent Collaboration (NEW)
from .knowledge_agent import KnowledgeAgent, get_knowledge_context_for_chapter
from .data_validator_agent import DataValidatorAgent
from .format_compliance_agent import FormatComplianceAgent
from .cross_reference_agent import CrossReferenceAgent
from .intent_clarification_agent import IntentClarificationAgent, INTENT_DEFINITIONS

from .chapters import (
    ChapterAgentBase,
    Chapter1Agent, Chapter2Agent, Chapter3Agent, Chapter4Agent,
    Chapter5Agent, Chapter6Agent, Chapter7Agent, Chapter8Agent,
    Chapter9Agent, Chapter10Agent,
    CHAPTER_AGENT_MAP, get_chapter_agent,
)

__all__ = [
    "BaseAgent",
    "MasterAgent", "create_master_agent",
    "ImageAnalysisAgent",
    "ChapterOrchestrator",
    "DataAnalysisAgent",
    "QualityReviewAgent",
    "ReviewTableAgent",
    # Multi-Agent Collaboration
    "KnowledgeAgent", "get_knowledge_context_for_chapter",
    "DataValidatorAgent",
    "FormatComplianceAgent",
    "CrossReferenceAgent",
    "IntentClarificationAgent", "INTENT_DEFINITIONS",
    # Chapter Agents
    "ChapterAgentBase",
    "Chapter1Agent", "Chapter2Agent", "Chapter3Agent", "Chapter4Agent",
    "Chapter5Agent", "Chapter6Agent", "Chapter7Agent", "Chapter8Agent",
    "Chapter9Agent", "Chapter10Agent",
    "CHAPTER_AGENT_MAP", "get_chapter_agent",
]
