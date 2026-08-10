"""Pipeline module — 8-phase report generation pipeline.

Exports:
- ReportPipelineService: Main orchestrator
- All context data classes
- Phase services for custom workflows
"""

from app.services.pipeline.pipeline_context import (
    PipelineContext, GenerationRecipe, DocumentStructure,
    SectionDef, TableDef, ImageSlot, ParagraphInfo,
    SectionType, Gap, QualityIssue, QualityReport,
)
from app.services.pipeline.pipeline_service import ReportPipelineService
from app.services.pipeline.knowledge_retrieval import KnowledgeRetrievalService
from app.services.pipeline.structure_analyzer import StructureAnalyzer
from app.services.pipeline.image_positioner import ImagePositioner
from app.services.pipeline.content_generator import ContentGenerator
from app.services.pipeline.table_processor import TableProcessor
from app.services.pipeline.gap_analyzer import GapAnalyzer
from app.services.pipeline.quality_validator import QualityValidator

__all__ = [
    # Orchestrator
    "ReportPipelineService",
    # Context
    "PipelineContext",
    "GenerationRecipe",
    "DocumentStructure",
    "SectionDef",
    "TableDef",
    "ImageSlot",
    "ParagraphInfo",
    "SectionType",
    "Gap",
    "QualityIssue",
    "QualityReport",
    # Phase services
    "KnowledgeRetrievalService",
    "StructureAnalyzer",
    "ImagePositioner",
    "ContentGenerator",
    "TableProcessor",
    "GapAnalyzer",
    "QualityValidator",
]
