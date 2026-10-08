"""Reclamation (土地复垦方案) domain configuration.

参照 stability.py 的写法：结构与提示词都引用既有实现，不复制。
"""

from app.domains.base import DomainConfig

# 复垦的身份提示词（与稳评不同的专业口吻；后续可按需细化）
RECLAMATION_IDENTITY_PROMPT = (
    "你是土地复垦方案编制专家，依据《土地复垦条例》《土地复垦条例实施办法》"
    "与 TD/T 1031 系列编制规程，编制临时用地土地复垦方案。"
    "文风平实、用编制单位的口吻，不写营销话术。"
)


def build_reclamation_config() -> DomainConfig:
    # 惰性导入，避免与 chapter_definitions 的导入链形成环
    from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS

    data_categories = {
        "项目基础信息": ["项目名称", "建设单位", "临时用地位置", "服务年限"],
        "勘测定界数据": ["临时用地面积", "地类构成", "权属", "地块坐落"],
        "复垦方向与评价": ["复垦方向", "适宜性评价", "损毁程度", "复垦目标"],
        "工程量与投资": ["表土剥离", "工程量", "工程施工费", "动态总投资"],
        "图件与附件": ["地理位置示意图", "现状图", "规划图", "权属人意见"],
    }

    return DomainConfig(
        domain_id="reclamation",
        display_name="土地复垦方案",
        identity_prompt=RECLAMATION_IDENTITY_PROMPT,
        rag_domain="reclamation",
        pipeline="chapter_by_chapter",
        company_name="",          # 出具单位由生成时用户选择，见 Task 10
        classify_keywords=[
            # 只收「复垦专有」的表述 —— `detect_domain` 里非默认域的关键词会压过
            # 稳评（默认域），所以宽泛词（如「临时用地」「复垦」）会把稳评对话误判成复垦。
            # 实测「涉及临时用地补偿」「复垦保障措施」这类稳评句子必须仍归 stability。
            "土地复垦", "复垦方案", "复垦报告", "复垦方向", "复垦工程",
        ],
        chapter_structure=RECLAMATION_CHAPTER_DEFINITIONS,
        data_categories=data_categories,
        default_collection="kb_reclamation_preface",   # 由各章映射覆盖，此处给主入口
        guardrails=[
            "禁止引用社会稳定风险评估规范（DB32/T4013）",
            "数字必须来自数据层，禁止自行推算或编造",
        ],
    )
