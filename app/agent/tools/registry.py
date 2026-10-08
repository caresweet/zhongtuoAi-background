"""Agent tool registry & executor — wraps backend capabilities as LLM tools.

Tools let the chapter agent call backend abilities on demand (RAG retrieval,
勘测定界 parsing, risk scoring) during content generation, instead of relying
on everything being pre-injected into the prompt.

Each tool follows the OpenAI-compatible function-calling format:
    {"type": "function", "function": {"name", "description", "parameters"}}
The executor maps a parsed tool_calls entry to a registered handler and returns
OpenAI-format `{"role": "tool", "tool_call_id", "content"}` messages.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """A registered tool definition + async handler."""

    name: str
    description: str
    parameters: Dict[str, Any]        # JSON Schema for parameters
    handler: Callable[..., Awaitable[str]]  # async (ctx, **kwargs) -> str
    needs_context: bool = True        # whether handler needs the project context dict


# Registry mapping tool name -> spec
TOOL_REGISTRY: Dict[str, ToolSpec] = {}


def register_tool(name: str, description: str, parameters: Dict[str, Any]):
    """Decorator to register an async handler as a tool."""

    def decorator(fn: Callable[..., Awaitable[str]]):
        TOOL_REGISTRY[name] = ToolSpec(
            name=name, description=description,
            parameters=parameters, handler=fn,
        )
        return fn

    return decorator


# ═══════════════════════════════════════════════════════════════════
# 工具 1：RAG 检索
# ═══════════════════════════════════════════════════════════════════
@register_tool(
    name="retrieve_rag",
    description=(
        "从知识库检索法规、政策、官方范文、案例的原文片段。"
        "当正文需要引用具体法规依据（如土地管理法第几条）、稳评规范（DB32/T4013-2021）、"
        "或参考范文表述时调用。返回检索到的文本片段，可直接引用或被概括。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词/问题，如'土地征收程序的法律依据'",
            },
            "n_results": {
                "type": "integer",
                "description": "返回结果条数（1-8，默认 5）",
            },
        },
        "required": ["query"],
    },
)
async def _retrieve_rag(ctx: dict, query: str = "", n_results: int = 5) -> str:
    try:
        from app.rag.rag_service import rag_service
        session_id = str(ctx.get("session_id", "default"))
        n = max(1, min(int(n_results or 5), 8))
        # 🔴 LLM 偶发不传 query：用当前小节/章节标题兜底，保持 RAG 小节对齐
        q = (query or "").strip()
        if not q:
            q = (
                ctx.get("current_section") or ctx.get("section_title")
                or ctx.get("chapter_title") or "社会稳定风险评估报告"
            )
        result = await rag_service.retrieve_hybrid(
            query=q, session_id=session_id, n_results=n,
            domain=ctx.get("domain", "stability"),
        )
        if not result:
            return "【检索结果】没有找到相关知识库内容。"
        lines = []
        for i, r in enumerate(result, 1):
            text = r.get("text", "") or r.get("content", "") or str(r)
            source = r.get("source", "知识库")
            lines.append(f"{i}. {text[:800]}")
        return "【知识库检索结果】\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("retrieve_rag tool error: %s", e)
        return f"【检索失败】{type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════════
# 工具 2：勘测定界表解析
# ═══════════════════════════════════════════════════════════════════
@register_tool(
    name="parse_kd_table",
    description=(
        "解析勘测定界报告 PDF 中的表格数据：表1-1 勘测定界地块汇总表（地块号/坐落/用途/面积/界址点数）"
        "和表4-1 土地分类面积表（按权属单位×地类大类）。"
        "当正文需要真实的地块信息或土地分类面积数据时调用。返回表格的结构化文本。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "勘测定界报告 PDF 路径（可省略，缺省自动从本批上传材料查找）",
            },
        },
        "required": [],
    },
)
async def _parse_kd_table(ctx: dict, pdf_path: str = "") -> str:
    """解析勘测定界报告，返回可直接引用的地块与地类数据。"""
    from app.services.land_survey_parser import parse_land_survey, format_for_prompt

    # 找到勘测定界报告 PDF（显式传入 → ctx → 上传列表）
    target = pdf_path or ctx.get("survey_report_path") or ""
    if not target:
        for f in (ctx.get("file_paths") or []):
            if f and ("勘测" in str(f) or "界" in str(f)):
                target = str(f)
                break
    if not target:
        return "【勘测定界】未找到勘测定界报告 PDF。请传入 pdf_path 或检查本批上传材料。"

    parsed = parse_land_survey(target)
    if not parsed:
        return "【勘测定界】未能从该文件中解析出地块表格，请确认是勘测定界报告（含地块情况汇总表/土地分类面积表）。"
    return format_for_prompt(parsed)

@register_tool(
    name="calc_risk_scoring",
    description=(
        "按 DB32/T4013-2021 量化评分体系（扣分制）计算项目措施前/措施后的风险评分。"
        "当正文（第六章/第八章）需要具体的风险评分数据或风险等级判定时调用。"
        "返回扣分制评分明细（含各维度扣分小计与风险等级）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "support_rate": {
                "type": "number",
                "description": "群众支持率百分比（如 95 表示 95%）。缺省用已收集的数据。",
            },
        },
        "required": [],
    },
)
async def _calc_risk_scoring(ctx: dict, support_rate: float = 100) -> str:
    try:
        from app.services.scoring_service import scoring_service
        facts = dict(ctx.get("facts", {}))
        if support_rate is not None and not facts.get("support_rate"):
            facts["support_rate"] = support_rate
        report = scoring_service.build_scoring_report(facts, {})
        return json.dumps({
            "pre_total": report["pre_measures"]["total"],
            "post_total": report["post_measures"]["total"],
            "risk_level": report["meta"]["risk_level"],
            "pre_items": [
                {
                    "category": i["category"], "item": i["item"],
                    "score": i["score"], "max_score": i["max_score"],
                }
                for i in report["pre_measures"]["items"]
            ],
        }, ensure_ascii=False)
    except Exception as e:
        logger.warning("calc_risk_scoring tool error: %s", e)
        return f"【评分计算失败】{type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════════
# 工具 4：公众问卷调查统计
# ═══════════════════════════════════════════════════════════════════
@register_tool(
    name="query_survey",
    description=(
        "获取公众问卷调查统计分析（第3章用）：总样本、支持/反对人数与比例、"
        "并按六维度返回各选项人数（身份/年龄/职业/了解程度/态度/解决诉求方式）。"
        "当正文（第三章）需要调查样本量、支持率、人群结构分布时调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_dimensions": {
                "type": "boolean",
                "description": "是否包含六维度分布（默认 true）",
            },
        },
        "required": [],
    },
)
async def _query_survey(ctx: dict, include_dimensions: bool = True) -> str:
    try:
        sa = ctx.get("survey_analysis") or {}
        if not sa:
            return "【问卷统计】当前无公众问卷调查统计数据。请上传问卷图片后重新生成。"
        lines = []
        total = sa.get("total_samples", 0)
        lines.append(f"总样本：{total} 份")
        lines.append(f"支持：{sa.get('support_count', 0)} 人（{sa.get('support_rate', 0)}%）")
        lines.append(f"反对：{sa.get('oppose_count', 0)} 人（{sa.get('oppose_rate', 0)}%）")
        if sa.get("conditional_support"):
            lines.append(f"有条件支持：{sa['conditional_support']} 人")
        if include_dimensions:
            for label, key in (
                ("身份", "identity_groups"), ("年龄", "age_groups"),
                ("职业", "occupation_groups"), ("了解程度", "awareness_levels"),
                ("态度", "attitude_options"), ("解决诉求方式", "resolution_methods"),
            ):
                groups = sa.get(key) or {}
                if groups:
                    detail = "、".join(f"{opt}:{cnt}" for opt, cnt in groups.items())
                    lines.append(f"{label}：{detail}")
        return "【公众问卷调查统计】\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("query_survey tool error: %s", e)
        return f"【问卷统计失败】{type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════════
# 工具 5：部门/单位问卷调查统计
# ═══════════════════════════════════════════════════════════════════
@register_tool(
    name="query_dept_survey",
    description=(
        "获取部门/单位问卷调查统计结果（第3章用）：各部门对各调查项目的反馈"
        "（了解程度、宣传公示满意度、政策理解、风险研判、支持态度等）。"
        "当正文（第三章）需要部门意见调查数据时调用。"
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def _query_dept_survey(ctx: dict) -> str:
    try:
        from app.services.table_generation_service import TableGenerationService
        # 构造一个最小 state（包含 survey/dept 数据），交给 generate_table_3_3 提取
        generated = ctx.get("generated_sections") or {}
        state = {
            "generated_sections": generated,
            "structured_data": ctx.get("structured_data") or {},
            "filled_data": ctx.get("filled") or {},
        }
        result = TableGenerationService.generate_table_3_3(state)
        rows = result.get("rows") or []
        if not rows:
            # 兜底：尝试从 survey_analysis 的 department 字段
            sa = ctx.get("survey_analysis") or {}
            dept = sa.get("department_survey") or {}
            if not dept:
                return "【部门问卷】当前无部门问卷调查统计数据。"
            lines = [f"{q}：{v}" for q, v in dept.items()]
            return "【部门问卷调查统计】\n" + "\n".join(lines)
        text = []
        for r in rows:
            text.append(" | ".join(str(x) for x in r))
        return "【部门问卷调查统计】\n" + "\n".join(text)
    except Exception as e:
        logger.warning("query_dept_survey tool error: %s", e)
        return f"【部门问卷获取失败】{type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════════
# Schema 导出 + 执行器
# ═══════════════════════════════════════════════════════════════════
def get_tools_schema() -> List[Dict[str, Any]]:
    """返回 OpenAI-format tools 列表，供 LLM 调用。"""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in TOOL_REGISTRY.values()
    ]


async def execute_tool_calls(
    tool_calls: List[Dict[str, Any]],
    context: Optional[dict] = None,
) -> List[Dict[str, str]]:
    """Execute parsed tool_calls and return OpenAI-format tool result messages.

    Each tool_call: {"name": str, "arguments": dict}
    Each result:    {"role": "tool", "content": str, "name": str}

    Handlers are looked up in TOOL_REGISTRY; failures are caught and returned
    as an error message rather than raising (so the calling agent can continue).
    """
    if not tool_calls:
        return []
    ctx = context or {}
    results = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("arguments", {}) or {}
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            results.append({
                "role": "tool",
                "name": name,
                "tool_call_id": tc.get("id", name),
                "content": f"【错误】未注册的工具: {name}",
            })
            continue
        try:
            content = await spec.handler(ctx, **args)
        except Exception as e:
            logger.warning("tool %s handler error: %s", name, e)
            content = f"【工具执行错误】{type(e).__name__}: {e}"
        results.append({
            "role": "tool",
            "name": name,
            "tool_call_id": tc.get("id", name),
            "content": content,
        })
        logger.info("[tool] %s -> %d chars", name, len(content) if content else 0)
    return results


ALL_TOOLS_SCHEMA = get_tools_schema()