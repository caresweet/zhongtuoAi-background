"""Tool definitions for the report generation agent.

Note: Uses plain Python functions for compatibility.
LangChain tool decorator is optional.
"""
from typing import Optional
from pydantic import BaseModel, Field


class ExtractDataInput(BaseModel):
    placeholder_key: str = Field(description="占位符标识符")
    value: str = Field(description="从用户消息中提取的值")
    confidence: str = Field(description="提取置信度: high, medium, low")


class AskClarificationInput(BaseModel):
    placeholder_key: str = Field(description="需要澄清的占位符标识符")
    question: str = Field(description="向用户追问的问题")


def mark_placeholder_filled(placeholder_key: str, value: str) -> str:
    """
    标记一个占位符已填写。将提取的值保存到填充数据中。
    如果用户跳过，value应为'需后期提供'。
    """
    return f"占位符 {placeholder_key} 已填写: {value}"


def get_next_placeholder(current_section_index: int, sections: list) -> dict:
    """
    获取下一个需要填写的占位符信息。
    在同一章节内按顺序遍历，章节完成后进入下一章。
    """
    return {"next_section_index": current_section_index, "has_more": True}


def check_all_complete(filled_count: int, total_count: int) -> bool:
    """
    检查是否所有占位符都已填写完成。
    """
    return filled_count >= total_count
