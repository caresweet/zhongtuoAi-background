"""SQLAlchemy ORM models for history_reports.db."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    session_id = Column(String(64), unique=True, nullable=False)

    template_id = Column(Integer, nullable=True)
    template_name = Column(String(255), nullable=True)

    # Status
    status = Column(String(30), default="created")
    # created, analyzing, interviewing, filling, reviewing, completed, failed, cancelled
    error_message = Column(Text, nullable=True)

    # Output file
    report_file_path = Column(String(500), nullable=True)
    review_table_path = Column(String(500), nullable=True)  # Separate 评审表 .docx path

    # Data payloads
    filled_data_json = Column(Text, nullable=True)       # JSON: {placeholder_key: value} (legacy)
    section_progress_json = Column(Text, nullable=True)  # JSON: section progress array
    conversation_json = Column(Text, nullable=True)      # JSON: full chat messages
    markdown_content = Column(Text, nullable=True)       # Full report markdown (new)
    chapter_structure_json = Column(Text, nullable=True) # JSON: chapter-by-chapter structure

    # Metadata
    generation_duration_sec = Column(Integer, nullable=True)
    user_id = Column(String(100), default="default")

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    messages = relationship(
        "ConversationMessage",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )

    __table_args__ = (
        Index("idx_reports_status", "status"),
        Index("idx_reports_created", "created_at"),
        Index("idx_reports_session", "session_id"),
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(64), nullable=False)

    role = Column(String(20), nullable=False)  # user, agent, system
    content = Column(Text, nullable=False)
    message_type = Column(String(30), default="text")
    # text, question, confirmation, system_event, error

    metadata_json = Column(Text, nullable=True)  # JSON

    created_at = Column(DateTime, default=datetime.now)

    # Relationships
    report = relationship("Report", back_populates="messages")

    __table_args__ = (
        Index("idx_messages_report", "report_id"),
        Index("idx_messages_session", "session_id"),
        Index("idx_messages_created", "created_at"),
    )
