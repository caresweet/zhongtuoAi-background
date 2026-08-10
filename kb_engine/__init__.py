"""kb_engine — 知识驱动的顺序化报告生成引擎

双类型知识库（稳评库 + 招标库），每库独立 SQLite 文件。
模板学习 → 固定资料检索 → 资料分析 → 大纲生成 → 逐章写作 → 逐章审核 →
组装 → 终审对比模板 → 退回修改，全程串行、一句一思。
"""
from .db import DualKB, KB_DOMAIN_STABILITY, KB_DOMAIN_BIDDING
from .llm import LLMClient
from .guardrails import ReviewGuard
from .template_learner import TemplateLearner
from .material_reader import MaterialReader
from .sequential_engine import SequentialEngine
from .template_compare import TemplateComparator
from .docx_writer import DocxWriter
from .template_docx_writer import TemplateDocxWriter

__all__ = [
    "DualKB", "KB_DOMAIN_STABILITY", "KB_DOMAIN_BIDDING",
    "LLMClient", "ReviewGuard", "TemplateLearner",
    "MaterialReader", "SequentialEngine", "TemplateComparator",
    "DocxWriter", "TemplateDocxWriter",
]
