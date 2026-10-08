"""Agent 工具注册表 — 把后端能力包装成 LLM 可调用的 tool-call 工具。

章节 agent 在生成正文前可调用这些工具按需获取数据：
- retrieve_rag:      知识库/法规/范文检索
- parse_kd_table:    勘测定界报告表格解析（表1-1/表4-1）
- calc_risk_scoring: 风险评分（DB32/T4013-2021 扣分制）
"""

from .registry import (
    ToolSpec,
    TOOL_REGISTRY,
    get_tools_schema,
    execute_tool_calls,
    ALL_TOOLS_SCHEMA,
)

__all__ = [
    "ToolSpec",
    "TOOL_REGISTRY",
    "get_tools_schema",
    "execute_tool_calls",
    "ALL_TOOLS_SCHEMA",
]
