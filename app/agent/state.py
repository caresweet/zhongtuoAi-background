"""State definitions for the RAG + LangGraph report generation agent.

Defines the AgentState TypedDict that flows through all LangGraph nodes.
"""

from typing import TypedDict, List, Dict, Any, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# Chapter Content
# ═══════════════════════════════════════════════════════════════════════════════

class ChapterContent(TypedDict, total=False):
    """Content and status of a single report chapter (1-10)."""
    number: int                      # 1-10
    title: str                       # e.g., "拟征收决策基本概况"
    status: str                      # pending | generating | review | approved | revised
    markdown: str                    # Current markdown content
    rag_sources: List[Dict[str, Any]]  # Retrieved knowledge used
    tables: List[Dict[str, Any]]     # Tables in this chapter
    images: List[str]                # Image references
    revision_history: List[Dict[str, Any]]  # [{timestamp, request, old_md, new_md}]
    user_customizations: List[str]   # User-specified customization notes
    confirmed_at: Optional[str]      # ISO timestamp when user confirmed this chapter
    generation_attempts: int         # Number of generation attempts for this chapter


# ═══════════════════════════════════════════════════════════════════════════════
# Skill 审核 → 章节级审核任务 / 人工干预状态
# ═══════════════════════════════════════════════════════════════════════════════

class ChapterAuditItem(TypedDict, total=False):
    """单章审核项：Skill/终稿审核原始输出解析后的章节级任务。

    disposition 是关键：决定这条违规走「AI 自动重写」还是「人工介入」。
    """
    chapter: int                       # 章节号 1-10
    type: str                          # 违规类型（expert_skill_violation / fabricated_data / ...）
    severity: str                      # critical | error | warning | info
    message: str                       # 违规描述（喂给前端 + 重写 prompt）
    correction: str                    # 纠正写法（若有，喂给重写 prompt）
    disposition: str                   # ai_rewrite | human | auto_fix | warning
    skill_rule_id: Optional[int]       # 命中的 review_skills.id（溯源）
    rule_pattern: Optional[str]        # 命中的正则/关键词
    match: Optional[str]               # 命中的原文片段（用于 docx 批注定位）


class ChapterHumanState(TypedDict, total=False):
    """单章人工干预状态（每章独立维护）。"""
    chapter: int
    retry_count: int                   # 🔴 本章独立重试计数器（替代全局 _chapter_retry）
    max_retry: int                     # 本章允许的最大 AI 重试次数（默认 = MAX_RETRY）
    in_human_queue: bool               # 是否已推入人工待处理队列
    queued_at: Optional[str]           # 入队时间 ISO
    # —— 前端写入的三个字段 ——
    human_opinion: str                 # 人工修改思路（模式1：交给 AI 重写）
    human_override: bool               # 是否人工直接改写了内容（模式2a）
    human_approved: bool               # 人工审批放行（模式2b：跳过后续 AI 审核重写）
    override_content: Optional[str]    # 人工直接改写的章节全文
    approved_at: Optional[str]
    status: str                        # queued | ai_rewriting | human_reviewed | approved | passed


class ReportState(TypedDict, total=False):
    """报告工作流状态（增量扩展 ReportWorkflowState=dict）。

    与 report_workflow.py 的 dict 兼容：所有字段都是可选、渐进写入，
    不破坏现有 node 的读写。新增字段集中在审核/人工干预闭环。
    """
    # —— 现有字段（沿用，不重复定义）——
    session_id: str
    chapters: Dict[int, Dict[str, Any]]            # {ch_num: {"markdown","title","status",...}}
    filled_data: Dict[str, str]
    outline_list: List[Dict[str, Any]]

    # —— 🔴 新增：Skill 违规解析结果 ——
    chapter_audits: Dict[int, List[ChapterAuditItem]]   # 章节号 -> 该章违规列表（解析后）
    skill_violations: List[Dict[str, Any]]              # 原始 skill 违规（含 rule_id/pattern）

    # —— 🔴 新增：人工介入状态 ——
    human_queue: List[int]                             # 待人工复核章节号（有序）
    human_items: Dict[int, ChapterHumanState]          # 章节号 -> 人工干预状态

    # —— 🔴 新增：全局质量循环 ——
    global_quality_rounds: int                         # 全局重写轮次（替代 _quality_round）
    max_quality_rounds: int                            # 轮次上限（替代 MAX_QUALITY_ROUNDS）

    # —— 🔴 新增：导出元数据（docx 批注用）——
    audit_meta: Dict[str, Any]                         # 完整审核元数据快照（含时间戳、skill 版本）


# ═══════════════════════════════════════════════════════════════════════════════
# Main Agent State
# ═══════════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict, total=False):
    """Full agent state flowing through LangGraph nodes."""

    # ---- Session Identity ----
    session_id: str
    report_title: str

    # ---- Template Binding ----
    template_id: int                  # Knowledge base template ID (0 = built-in)
    template_name: str                # Template display name
    template_path: str                # Path to template .docx file
    preserved_sections: List[Dict[str, str]]  # [{name, description}] — keep as-is
    template_placeholders: List[Dict[str, Any]]  # All placeholders from template analysis

    # ---- Workflow Phase ----
    phase: str  # setup | collecting | generating | reviewing | assembling | complete | chapter_review | review_table | chapter_generation

    # ---- Project Context ----
    project_context: str             # User-provided project summary
    filled_data: Dict[str, str]      # Collected fill values {placeholder_key: value}
    uploaded_files: List[Dict[str, str]]  # [{name, file_path, type}]

    # ---- Chapter Generation ----
    chapters: Dict[int, ChapterContent]   # keyed by chapter number 1-10
    current_chapter: int             # 1-10
    generation_mode: str             # "full" | "single_chapter" | "revision" | "chapter_by_chapter"

    # ---- User Interaction ----
    messages: List[Dict[str, Any]]   # Full conversation history
    pending_user_request: Optional[str]  # Latest user feedback to process
    user_action: str                 # "approve" | "revise" | "skip" | "fill_table" | ""

    # ---- RAG State ----
    rag_collection_id: str           # Session-specific Chroma collection ID
    last_rag_results: Dict[str, Any] # Results from last retrieval

    # ---- Output ----
    final_markdown: str              # Complete assembled markdown
    output_path: Optional[str]       # Path to generated .docx
    report_id: Optional[int]         # Database record ID

    # ---- Control ----
    status: str                      # created | generating | reviewing | completed | failed
    error_message: Optional[str]
    streaming: bool                  # Whether to stream output to client

    # ---- Chapter Orchestrator State ----
    chapter_orchestrator_state: str  # "idle" | "generating" | "reviewing" | "completed"
    review_table_path: Optional[str]  # Path to generated standalone review table .docx
    missing_data_requests: Dict[int, List[str]]  # chapter_number -> list of missing data item descriptions
    chapter_feedback: Optional[str]  # Latest user feedback for current chapter (revision text)

    # ---- Multi-Agent Step Tracking (12-step workflow) ----
    current_step: int                # 1-12, current step in the 12-step workflow
    step_statuses: Dict[int, str]    # {1: "completed", 2: "in_progress", ...}
    structured_data: Dict[str, Any]  # Typed data per step (step_1, step_6, etc.)
    generated_sections: Dict[str, Any]  # AI-generated content (survey_analysis, legality, etc.)
    active_agents: Dict[str, str]    # {agent_name: "thinking"|"acting"|"idle"}
    agent_log: List[Dict[str, Any]]  # [{agent, timestamp, action, issues, elapsed_sec}]
    needs_human_review: bool         # True when paused at a HUMAN_REVIEW_STEPS checkpoint

    # ---- Collecting Phase State (section-by-section Q&A) ----
    collecting_section_order: List[Dict[str, Any]]  # [{section_index, label}, ...]
    collecting_current_group_idx: int
    collecting_question_idx: int
    collecting_skipped_keys: List[str]


# ═══════════════════════════════════════════════════════════════════════════════
# Chapter Definitions (built-in, replaces template-based structure)
# ═══════════════════════════════════════════════════════════════════════════════

CHAPTER_DEFINITIONS: Dict[int, Dict[str, str]] = {
    1: {
        "number": 1,
        "title": "拟征收决策基本概况",
        "description": "涵盖决策名称、责任单位、征地位置、征收范围/面积/地类/附着物、资金测算、实施周期",
        "key_tables": ["拟征收土地基本情况表"],
    },
    2: {
        "number": 2,
        "title": "评估过程、方法和依据",
        "description": "评估流程还原、法规依据（DB32/T4013-2021等）、三类核心方法",
        "key_tables": ["评估依据法规清单"],
    },
    3: {
        "number": 3,
        "title": "社会稳定风险因素调查",
        "description": "问卷调查统计、利益相关者诉求、公示/座谈/现场照片、网络舆情排查",
        "key_tables": ["公众意见调查分析表", "部门意见调查分析表"],
    },
    4: {
        "number": 4,
        "title": "决策综合分析",
        "description": "合法性分析（主体/目的/规划/程序）、合理性分析（经济/投资/群众）、可行性分析（资金/政府/群众）、可控性分析（安全/宣传/群体/治安）",
        "key_tables": [],
    },
    5: {
        "number": 5,
        "title": "风险因素识别与初始等级表",
        "description": "识别4类核心风险：补偿方案、资金分配、社保名单、信访舆情；明确发生概率、影响程度、初始风险等级",
        "key_tables": ["风险因素初始风险等级表"],
    },
    6: {
        "number": 6,
        "title": "措施前风险等级研判",
        "description": "按DB32/T4013-2021量化指标体系打分，合法性/合理性/可行性/可控性四大类，措施前总分15-20分",
        "key_tables": ["措施前风险等级量化评分表"],
    },
    7: {
        "number": 7,
        "title": "风险防范与化解措施",
        "description": "对应4类风险制定可落地措施，覆盖宣传/补偿/资金/社保/信访五大方向，明确责任主体",
        "key_tables": ["风险防范与化解措施汇总表"],
    },
    8: {
        "number": 8,
        "title": "措施后风险等级评估",
        "description": "重新计算量化得分，措施后总分10-15分，判定低风险，附措施前后得分对比表",
        "key_tables": ["措施后风险等级量化评分表", "措施前后得分对比表"],
    },
    9: {
        "number": 9,
        "title": "评估结论与建议",
        "description": "总结合法性/合理性/可行性/可控性论证结果，明确低风险可实施，给出4-5条工作建议",
        "key_tables": [],
    },
    10: {
        "number": 10,
        "title": "应急预案",
        "description": "组织指挥体系/分级响应/处置措施（金湖模板：3节纯文字无表格）",
        "key_tables": [],
    },
}


def create_initial_state(
    session_id: str,
    report_title: str = "",
    project_context: str = "",
) -> AgentState:
    """Create a fresh agent state for a new report generation session.

    Args:
        session_id: Unique session identifier.
        report_title: User-provided report title.
        project_context: User-provided project context/information.

    Returns:
        Initialized AgentState dict.
    """
    # Initialize all 10 chapters as pending
    chapters: Dict[int, ChapterContent] = {}
    for num, defn in CHAPTER_DEFINITIONS.items():
        chapters[num] = ChapterContent(
            number=num,
            title=defn["title"],
            status="pending",
            markdown="",
            rag_sources=[],
            tables=[],
            images=[],
            revision_history=[],
            user_customizations=[],
        )

    return AgentState(
        session_id=session_id,
        report_title=report_title or "社会稳定风险评估报告",
        template_id=0,
        template_name="内置10章标准结构",
        template_path="",
        preserved_sections=[],
        template_placeholders=[],
        phase="setup",
        filled_data={},
        project_context=project_context,
        uploaded_files=[],
        chapters=chapters,
        current_chapter=1,
        generation_mode="full",
        messages=[],
        pending_user_request=None,
        user_action="",
        rag_collection_id="",
        last_rag_results={},
        final_markdown="",
        output_path=None,
        report_id=None,
        status="created",
        error_message=None,
        streaming=True,
        chapter_orchestrator_state="idle",
        review_table_path=None,
        missing_data_requests={},
        chapter_feedback=None,
    )
