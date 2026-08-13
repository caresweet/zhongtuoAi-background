"""Stability (社会稳定风险评估) domain configuration.

References the existing prompt and chapter structure rather than duplicating
them, so behavior stays identical to the pre-registry system.
"""

from app.domains.base import DomainConfig


def build_stability_config() -> DomainConfig:
    # Import lazily to avoid circular imports (master imports domains indirectly).
    from app.agent.agents.master import SYSTEM_PROMPT
    from app.agent.chapter_definitions import CHAPTER_DEFINITIONS

    data_categories = {
        "基础项目信息": ["项目名称", "责任单位", "实施单位", "决策名称", "位置", "坐落"],
        "经济指标": ["投资", "补偿", "资金", "亩", "面积", "公顷", "单价", "总价"],
        "现场实测数据": ["问卷", "支持率", "反对", "户数", "人数", "座谈", "比例"],
        "政策文件信息": ["文号", "批复", "规划", "法规", "DB32", "土地管理法"],
        "附图附表参数": ["红线图", "公示", "影像", "附图", "示意图"],
        "单位资质资料": ["营业执照", "资质", "证书", "许可证"]
    }

    return DomainConfig(
        domain_id="stability",
        display_name="社会稳定风险评估报告",
        identity_prompt=SYSTEM_PROMPT,
        rag_domain="stability",
        pipeline="chapter_by_chapter",
        company_name="",
        classify_keywords=[
            "稳评", "社会稳定", "风险评估", "征收", "征地", "拆迁", "补偿",
        ],
        chapter_structure=CHAPTER_DEFINITIONS,
        data_categories=data_categories,
        default_collection="knowledge_base",
        guardrails=[
            "",
            "风险打分严格按 DB32/T4013-2021",
        ],
    )
