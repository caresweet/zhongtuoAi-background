"""SQLAlchemy ORM models for knowledge_base.db."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Template(Base):
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    category = Column(String(100), nullable=False, default="通用")

    # Report domain this template belongs to (stability, bidding, feasibility, ...)
    domain = Column(String(50), nullable=False, default="stability")

    # File paths (relative to storage/)
    template_file_path = Column(String(500), nullable=False)
    example_file_path = Column(String(500), nullable=True)

    # AI analysis results
    analysis_status = Column(String(20), default="pending")  # pending, analyzing, completed, failed
    placeholders_json = Column(Text, nullable=True)  # JSON: full placeholder tree
    sections_json = Column(Text, nullable=True)      # JSON: section structure

    # Metadata
    file_size = Column(Integer, nullable=True)
    docx_metadata = Column(Text, nullable=True)  # JSON

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_active = Column(Boolean, default=True)

    # Relationships
    placeholders = relationship(
        "PlaceholderDefinition",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="PlaceholderDefinition.sort_order",
    )


class PlaceholderDefinition(Base):
    __tablename__ = "placeholder_definitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False)

    # Identity
    placeholder_key = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)

    # Location in document
    section_index = Column(Integer, nullable=True)
    section_title = Column(String(500), nullable=True)
    paragraph_index = Column(Integer, nullable=True)
    run_index = Column(Integer, nullable=True)

    # Data expectations
    expected_type = Column(String(50), default="text")  # text, number, date, location, table, image, choice
    expected_format = Column(String(255), nullable=True)
    options_json = Column(Text, nullable=True)  # JSON array for choice type
    description = Column(Text, nullable=True)

    # Control
    is_required = Column(Boolean, default=True)
    default_value = Column(String(500), default="需后期提供")
    sort_order = Column(Integer, default=0)

    # Relationships
    template = relationship("Template", back_populates="placeholders")

    __table_args__ = (
        Index("idx_placeholders_template", "template_id"),
        Index("idx_placeholders_section", "template_id", "section_index"),
    )


class KnowledgeDocument(Base):
    """Uploaded regulation/standard/example document for RAG knowledge base."""

    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    document_type = Column(
        String(50), nullable=False, default="regulation"
    )  # regulation, standard, example_report, other

    # Report domain this document belongs to (stability, bidding, feasibility, ...)
    domain = Column(String(50), nullable=False, default="stability")

    # File storage
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    file_type = Column(String(20), nullable=True)  # docx, pdf, txt

    # Indexing status
    indexed_status = Column(
        String(20), default="pending"
    )  # pending, indexing, completed, failed
    chunk_count = Column(Integer, default=0)
    index_error = Column(Text, nullable=True)

    # Persisted extraction artifacts for analysis-first flow
    extraction_status = Column(String(20), default="pending")
    extraction_version = Column(String(50), nullable=True)
    extracted_text = Column(Text, nullable=True)
    retrieval_text = Column(Text, nullable=True)
    structured_data_json = Column(Text, nullable=True)
    image_summary = Column(Text, nullable=True)

    # RAG collection reference
    collection_name = Column(String(100), nullable=True)

    # ── Data Cleaning Workbench ──
    raw_text = Column(Text, nullable=True)              # 原始解析文本（保留底稿，不可覆盖）
    cleaned_text = Column(Text, nullable=True)           # 清洗后文本（供RAG分块使用）
    clean_config_snapshot = Column(Text, nullable=True)  # JSON: 清洗规则配置快照（用于溯源）
    clean_status = Column(String(20), default="raw")     # raw → cleaning → cleaned → confirmed

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        Index("idx_kd_type", "document_type"),
        Index("idx_kd_status", "indexed_status"),
        Index("idx_kd_domain", "domain"),
    )


class CompanyAsset(Base):
    """Reusable company assets extracted from bidding response documents.

    These are the *generic* parts of a tender/bid document (营业执照, 财务报告,
    社保/纳税, 法人证明, 授权委托书, 承诺函, 资质证书, 人员配备, 设备清单) that
    recur across projects. Extracted once from an uploaded 投标文件 sample and
    reused when generating new bid documents, so the model never re-invents them.
    """

    __tablename__ = "company_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company = Column(String(255), nullable=False, default="江苏众拓测绘有限公司")
    # 营业执照/财务/社保/纳税/法人证明/授权委托/承诺函/资质证书/人员/设备/其他
    asset_type = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False, default="")
    content = Column(Text, nullable=False, default="")   # plain text or structured JSON
    is_structured = Column(Boolean, default=False)        # True → content is JSON
    source_file = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        Index("idx_ca_company_type", "company", "asset_type"),
    )


class AssetImage(Base):
    """Images extracted from knowledge-base bidding documents.

    Each image is named by its surrounding caption/paragraph text in the
    source docx, so it can be matched by keyword when inserting images into
    newly generated reports.  Images are stored as BLOB for portability.
    """

    __tablename__ = "asset_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Humane name derived from the caption / nearest heading / paragraph text
    image_name = Column(String(500), nullable=False, index=True)
    # Category derived from content (营业执照/资质证书/人员/业绩/设备/承诺函/...)
    category = Column(String(100), nullable=False, default="其他", index=True)
    # Binary image data (PNG / JPEG)
    image_data = Column(Text, nullable=False)  # base64-encoded for SQLite compat
    # MIME type: image/png, image/jpeg
    mime_type = Column(String(50), nullable=False, default="image/png")
    # Width × height in pixels (optional, helps DOCX layout)
    width_px = Column(Integer, nullable=True)
    height_px = Column(Integer, nullable=True)
    # Which knowledge-base docx this was extracted from
    source_file = Column(String(500), nullable=True)
    # Template or document ID for traceability
    source_template_id = Column(Integer, nullable=True)
    # Search keywords (derived from surrounding text + category)
    search_keywords = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        Index("idx_ai_category", "category"),
        Index("idx_ai_name", "image_name"),
    )

