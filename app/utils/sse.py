"""SSE (Server-Sent Events) formatting utilities."""
import json
from typing import Any, Dict


def sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """Format data as an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_message(content: str, message_type: str = "text", **kwargs) -> str:
    """Format a message as an SSE event."""
    data = {"role": "agent", "content": content, "message_type": message_type, **kwargs}
    return sse_event("message", data)


def sse_progress(current: int, total: int, section_title: str = "", **kwargs) -> str:
    """Format a progress update as an SSE event."""
    data = {
        "current_section": current,
        "total_sections": total,
        "section_title": section_title,
        **kwargs,
    }
    return sse_event("progress", data)


def sse_status(status: str, previous_status: str = "") -> str:
    """Format a status change as an SSE event."""
    return sse_event("status_change", {"status": status, "previous_status": previous_status})


def sse_data_filled(placeholder_key: str, value: str) -> str:
    """Format a data_filled notification as an SSE event."""
    return sse_event("data_filled", {"placeholder_key": placeholder_key, "value": value})


def sse_error(message: str, recoverable: bool = True) -> str:
    """Format an error as an SSE event."""
    return sse_event("error", {"message": message, "recoverable": recoverable})


def sse_complete(report_id: int, download_url: str, message: str = "报告生成成功！", review_table_url: str = "") -> str:
    """Format a completion notification as an SSE event."""
    data = {
        "report_id": report_id,
        "download_url": download_url,
        "message": message,
    }
    if review_table_url:
        data["review_table_url"] = review_table_url
    return sse_event("complete", data)


# ═══════════════════════════════════════════════════════════════════════════════
# New Chapter Streaming Events (for RAG + LangGraph workflow)
# ═══════════════════════════════════════════════════════════════════════════════

def sse_chapter_start(chapter: int, title: str) -> str:
    """Signal the start of chapter generation."""
    return sse_event("chapter_start", {"chapter": chapter, "title": title})


def sse_chapter_stream(chapter: int, delta: str) -> str:
    """Stream a chunk of chapter content."""
    return sse_event("chapter_stream", {"chapter": chapter, "delta": delta})


def sse_chapter_complete(
    chapter: int,
    title: str,
    markdown: str = "",
    tables: list = None,
    sources: list = None,
) -> str:
    """Signal the completion of a chapter."""
    return sse_event("chapter_complete", {
        "chapter": chapter,
        "title": title,
        "markdown": markdown,
        "tables": tables or [],
        "sources": sources or [],
    })


def sse_thinking(content: str) -> str:
    """Send AI thinking/processing status."""
    return sse_event("thinking", {"content": content})


def sse_thinking_stream(delta: str, source: str = "agent") -> str:
    """Stream AI thinking process.

    Args:
        delta: Text chunk of thinking content.
        source: "agent" for agent step thinking, "llm" for LLM chain-of-thought reasoning.
    """
    return sse_event("thinking_stream", {"delta": delta, "source": source})


def sse_reasoning_start() -> str:
    """Signal the start of LLM chain-of-thought reasoning block (DeepSeek-style)."""
    return sse_event("reasoning_start", {})


def sse_reasoning_end() -> str:
    """Signal the end of LLM chain-of-thought reasoning block."""
    return sse_event("reasoning_end", {})


def sse_revision_diff(chapter: int, old_text: str, new_text: str, description: str = "") -> str:
    """Send a revision diff for comparison."""
    return sse_event("revision_diff", {
        "chapter": chapter,
        "old": old_text,
        "new": new_text,
        "change_description": description,
    })


def sse_rag_sources(chapter: int, sources: list) -> str:
    """Send RAG retrieval sources."""
    return sse_event("rag_results", {
        "chapter": chapter,
        "sources": sources,
    })


def sse_phase_change(phase: str, metadata: dict = None) -> str:
    """Notify frontend of a phase transition."""
    data = {"phase": phase}
    if metadata:
        data.update(metadata)
    return sse_event("phase_change", data)


def sse_placeholder_filled(key: str, location: str, old_value: str = "", new_value: str = "") -> str:
    """Notify that a placeholder has been filled, with before/after values."""
    return sse_event("placeholder_filled", {
        "key": key,
        "location": location,
        "old_value": old_value,
        "new_value": new_value,
    })


def sse_collecting_question(data: dict) -> str:
    """Send a natural-language collecting question to the frontend.

    data should contain: question, section_title, progress, placeholder_type,
                         image_required, placeholder_key, description
    """
    return sse_event("collecting_question", data)


def sse_section_questions(data: dict) -> str:
    """Send ALL questions for the current section as a batch.

    data should contain: section_title, section_index, progress,
                         total_in_section, questions[]
    Each question: {placeholder_key, question, placeholder_type,
                    display_name, description, image_required}
    """
    return sse_event("section_questions", data)


def sse_section_answers_confirmed(filled_keys: list, next_section: dict = None) -> str:
    """Confirm batch section answers were processed and provide next section.

    Args:
        filled_keys: List of placeholder_keys that were filled.
        next_section: build_section_questions() result for next section,
                      or None if all sections are done.
    """
    return sse_event("section_answers_confirmed", {
        "filled_keys": filled_keys,
        "filled_count": len(filled_keys),
        "next_section": next_section,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-Agent + Step Wizard Events
# ═══════════════════════════════════════════════════════════════════════════════

def sse_agent_status(agent: str, status: str, message: str = "", **kwargs) -> str:
    """Send multi-agent status update.

    status: 'thinking' | 'acting' | 'completed' | 'waiting_review' | 'idle'
    """
    data = {"agent": agent, "status": status, "message": message, **kwargs}
    return sse_event("agent_status", data)


def sse_step_transition(step: int, total: int = 12, label: str = "",
                        needs_review: bool = False) -> str:
    """Send step wizard transition event."""
    return sse_event("step_transition", {
        "step": step,
        "total": total,
        "label": label,
        "needs_review": needs_review,
    })


def sse_step_progress_sync(step_statuses: dict, current_step: int = 1,
                           total_placeholders: int = 0, filled_placeholders: int = 0,
                           placeholders: list = None) -> str:
    """Send full step progress sync to frontend sidebar.

    Args:
        step_statuses: {1: 'completed', 2: 'in_progress', ...} mapping.
        current_step: Current active step number.
        total_placeholders: Total meaningful placeholders in template.
        filled_placeholders: Number of filled placeholders.
        placeholders: Optional list of placeholder dicts for category display.
    """
    data = {
        "step_statuses": step_statuses,
        "current_step": current_step,
        "total_placeholders": total_placeholders,
        "filled_placeholders": filled_placeholders,
    }
    if placeholders is not None:
        data["placeholders"] = placeholders
    return sse_event("step_progress_sync", data)


def sse_data_preview(step: int, data: dict) -> str:
    """Send live data preview to frontend (e.g. extracted survey table)."""
    return sse_event("data_preview", {"step": step, "data": data})


def sse_analysis_result(section: str, content: str) -> str:
    """Send AI-generated analysis text for a specific section."""
    return sse_event("analysis_result", {"section": section, "content": content})


def sse_validation_result(summary: str, details: str = "",
                          passed: bool = False) -> str:
    """Send format validation results."""
    return sse_event("validation_result", {
        "summary": summary,
        "details": details,
        "passed": passed,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Chapter-by-Chapter Generation Events
# ═══════════════════════════════════════════════════════════════════════════════

def sse_chapter_review_prompt(chapter: int, title: str, summary: str = "",
                               tables_count: int = 0, word_count: int = 0) -> str:
    """Prompt user to review and confirm a generated chapter."""
    return sse_event("chapter_review_prompt", {
        "chapter": chapter,
        "title": title,
        "summary": summary,
        "tables_count": tables_count,
        "word_count": word_count,
        "actions": ["approve", "revise", "skip"],
    })


def sse_chapter_confirmed(chapter: int, title: str = "") -> str:
    """Signal that a chapter has been confirmed by the user."""
    return sse_event("chapter_confirmed", {
        "chapter": chapter,
        "title": title,
    })


def sse_missing_data_prompt(chapter: int, title: str,
                             data_items: list) -> str:
    """Prompt user to provide missing data for a chapter.

    data_items: List of {key, display_name, description, example}
    """
    return sse_event("missing_data_prompt", {
        "chapter": chapter,
        "title": title,
        "data_items": data_items,
        "total_missing": len(data_items),
    })


def sse_chapter_data_request(chapter: int, title: str,
                              missing_items: list) -> str:
    """Ask user for specific data items needed before generating a chapter."""
    return sse_event("chapter_data_request", {
        "chapter": chapter,
        "title": title,
        "missing_items": missing_items,
    })


def sse_review_table_start() -> str:
    """Signal that review table generation is starting."""
    return sse_event("review_table_start", {
        "message": "正在从已确认的10章内容中提取数据，生成评审表...",
    })


def sse_review_table_complete(download_url: str) -> str:
    """Signal that the review table has been generated."""
    return sse_event("review_table_complete", {
        "download_url": download_url,
        "message": "评审表生成完成",
    })


def sse_chapter_orchestrator_progress(current: int, total: int = 10,
                                       status: str = "generating") -> str:
    """Send chapter-by-chapter overall progress."""
    return sse_event("chapter_progress", {
        "current": current,
        "total": total,
        "status": status,  # "generating" | "reviewing" | "confirmed"
    })
