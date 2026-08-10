"""Bidding (招标投标) domain configuration.

References the existing bidding prompt and report-type structure rather than
duplicating them, so behavior stays identical to the pre-registry system.
"""

from app.domains.base import DomainConfig


def build_bidding_config() -> DomainConfig:
    from app.agent.agents.master import BIDDING_SYSTEM_PROMPT
    from app.agent.agents.bidding_report_agent import REPORT_TYPES, REPORT_CHAPTERS

    # Adapt bidding's report-type map into the generic chapter_structure shape.
    chapter_structure = {}
    for i, (rtype, title) in enumerate(REPORT_TYPES.items(), start=1):
        chapters = REPORT_CHAPTERS.get(rtype, [])
        chapter_structure[i] = {
            "report_type": rtype,
            "title": title,
            "chapter_count": len(chapters),
            "chapters": chapters,
        }

    data_categories = {
        "项目基础信息": ["项目名称", "项目编号", "预算金额", "招标人", "代理机构"],
        "资格要求": ["资质要求", "业绩要求", "信誉要求"],
        "评分标准": ["评标办法", "评分项", "权重"],
        "时间节点": ["公告时间", "开标时间", "评标时间", "公示期"],
    }

    return DomainConfig(
        domain_id="bidding",
        display_name="招标投标文件",
        identity_prompt=BIDDING_SYSTEM_PROMPT,
        rag_domain="bidding",
        pipeline="chapter_by_chapter",
        company_name="江苏众拓测绘有限公司",
        classify_keywords=[
            "招标", "投标", "评标", "中标", "标书", "采购", "竞争性", "bid", "tender",
        ],
        chapter_structure=chapter_structure,
        data_categories=data_categories,
        default_collection="bidding_knowledge",
        guardrails=[
            "禁止引用社会稳定风险评估规范（DB32/T4013）",
            "禁止在文档中标注数据来源",
            "招标代理机构固定为江苏众拓项目代理咨询有限公司",
        ],
    )
