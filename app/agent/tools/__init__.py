"""Agent 工具注册表 — 把后端能力包装成 LLM 可调用的 tool-call 工具。

章节 agent 在生成正文前可调用这些工具按需获取数据：

稳评（stability）——
- retrieve_rag:      知识库/法规/范文检索
- parse_kd_table:    勘测定界报告表格解析（表1-1/表4-1）
- calc_risk_scoring: 风险评分（DB32/T4013-2021 扣分制）
- query_survey / query_dept_survey: 公众·部门问卷统计

复垦（reclamation）—— 见 `reclamation_tools`（三件计算工具 + 复用的 retrieve_rag/parse_kd_table）
- calc_earthwork:      工程量测算
- calc_investment:     投资估算
- evaluate_suitability: 适宜性评价

各工具按 `ToolSpec.domains` 归属域；`get_tools_schema(domain)` 按域过滤，
故稳评拿到的工具表逐字不变，复垦只拿到自己那几件。
"""

from .registry import (
    ToolSpec,
    TOOL_REGISTRY,
    get_tools_schema,
    execute_tool_calls,
    ALL_TOOLS_SCHEMA,
)
# 🔴 导入即注册复垦三件工具（装饰器副作用）。必须在上面 from .registry import 之后 ——
#    `ALL_TOOLS_SCHEMA` 是 registry 模块底部按**默认域（稳评）**取的快照，先于本行求值，
#    故复垦工具不进稳评快照。
from . import reclamation_tools  # noqa: F401

__all__ = [
    "ToolSpec",
    "TOOL_REGISTRY",
    "get_tools_schema",
    "execute_tool_calls",
    "ALL_TOOLS_SCHEMA",
]
