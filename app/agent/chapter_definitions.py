"""Unified chapter definitions — single source of truth for all agents.

All chapter metadata lives here:
- CHAPTER_DEFINITIONS: titles, descriptions, key_tables
- CHAPTER_STRUCTURE_SPECS: sections, min/max words, required tables
- CHAPTER_RAG_QUERIES: RAG search queries per chapter
- CHAPTER_DATA_REQUIREMENTS: required/recommended/optional fields
- CHAPTER_FORMAT_RULES: format compliance rules

Previously this data was duplicated across 6 separate files.
"""

from typing import Dict, List, Any

# ═══════════════════════════════════════════════════════════════
# Core Definitions (was in state.py + chapter_base.py)
# ═══════════════════════════════════════════════════════════════

CHAPTER_DEFINITIONS: Dict[int, Dict[str, Any]] = {
    1: {
        "title": "拟征收决策基本概况",
        "description": "决策名称、决策主体、稳评责任单位、稳评实施单位、项目基本情况、涉及利益相关者、征收补偿方案要点",
        "key_tables": [],
        "sections": ["1.1 决策名称", "1.2 决策主体", "1.3 稳评责任单位",
                      "1.4 稳评实施单位", "1.5 项目基本情况", "1.6 涉及利益相关者",
                      "1.7 征收补偿方案要点"],
        "min_words": 300, "max_words": 1000,
    },
    2: {
        "title": "评估过程、方法和依据",
        "description": "评估过程、评估方法（资料收集/问卷/座谈/现场勘查/舆情）、评估依据（法律法规+标准规范）",
        "key_tables": [],
        "sections": ["2.1 评估过程", "2.2 评估方法", "2.3 评估依据"],
        "min_words": 300, "max_words": 1000,
    },
    3: {
        "title": "社会稳定风险因素调查",
        "description": "问卷调查结果（发放/回收/知晓度/支持度/主要关切/主要诉求）、利益相关者诉求",
        "key_tables": [],
        "sections": ["3.1 问卷调查结果", "3.2 利益相关者诉求"],
        "min_words": 300, "max_words": 1500,
    },
    4: {
        "title": "决策综合分析",
        "description": "合法性分析、合理性分析、可行性分析、可控性分析",
        "key_tables": [],
        "sections": ["4.1 合法性分析", "4.2 合理性分析", "4.3 可行性分析", "4.4 可控性分析"],
        "min_words": 400, "max_words": 2000,
    },
    5: {
        "title": "风险因素识别与初始等级表",
        "description": "识别主要风险因素、列出风险表现和风险等级（表格形式，金湖模板格式）",
        "key_tables": [],  # 使用金湖模板的4列表格
        "sections": [],  # 金湖模板第5章无子标题，直接正文+表格
        "min_words": 100, "max_words": 500,
    },
    6: {
        "title": "措施前风险等级研判",
        "description": "量化评分各项指标，计算措施前综合得分，判定风险等级（纯文字叙述，无表格）",
        "key_tables": [],
        "sections": [],  # 金湖模板第6章无子标题，纯文字叙述
        "min_words": 200, "max_words": 800,
    },
    7: {
        "title": "风险防范与化解措施",
        "description": "针对风险因素逐项制定措施（列表形式，每项一条）",
        "key_tables": [],
        "sections": [],  # 金湖模板第7章无子标题，列表形式
        "min_words": 200, "max_words": 800,
    },
    8: {
        "title": "措施后风险等级评估",
        "description": "措施后重新评估，综合得分计算，判定措施后风险等级（纯文字叙述，无表格）",
        "key_tables": [],
        "sections": [],  # 金湖模板第8章无子标题，纯文字叙述
        "min_words": 200, "max_words": 800,
    },
    9: {
        "title": "评估结论与建议",
        "description": "评估结论（综合判定风险等级）、工作建议（4-5条）",
        "key_tables": [],
        "sections": ["9.1 评估结论", "9.2 建议"],
        "min_words": 300, "max_words": 1000,
    },
    10: {
        "title": "应急预案",
        "description": "组织指挥体系/分级响应/处置措施（金湖模板格式）",
        "key_tables": [],
        "sections": ["10.1 组织指挥体系", "10.2 分级响应", "10.3 处置措施"],
        "min_words": 200, "max_words": 500,
    },
}

# ═══════════════════════════════════════════════════════════════
# RAG Query Templates (was in retriever.py + knowledge_agent.py)
# ═══════════════════════════════════════════════════════════════

CHAPTER_RAG_QUERIES: Dict[int, str] = {
    1: "拟征收决策基本概况 项目名称 责任单位 征地位置 征收范围 面积 地类 资金测算 实施周期",
    2: "评估过程 评估方法 评估依据 对照表法 实地考察法 问卷调查法 稳评法规 DB32/T4013-2021",
    3: "社会稳定风险因素调查 公众意见调查 问卷调查统计 利益相关者诉求 网络舆情 公示 座谈",
    4: "决策综合分析 合法性分析 合理性分析 可行性分析 可控性分析 征收主体 规划相符性 程序合规性",
    5: "风险因素识别 初始风险等级 补偿方案风险 资金分配风险 社保名单风险 信访舆情风险 发生概率 影响程度",
    6: "措施前风险等级研判 量化指标体系 合法性打分 合理性打分 可行性打分 可控性打分 DB32/T4013-2021 评分表",
    7: "风险防范化解措施 宣传规范 补偿方案 资金监管 社保落实 信访舆情应对 责任主体 可执行措施",
    8: "措施后风险等级评估 重新计算得分 得分对比 风险下降 低风险判定",
    9: "评估结论 建议 合法性结论 合理性结论 可行性结论 可控性结论 低风险 可实施 工作建议",
    10: "应急预案 编制目的 依据 适用范围 组织领导 职责任务 预警预防 现场处置 舆情处置 保障措施 奖惩机制",
}

# ═══════════════════════════════════════════════════════════════
# Data Requirements per Chapter (single source of truth — was duplicated in data_validator_agent.py)
# Format: "required" → List[Tuple[key, display_name, criticality]]
#         "recommended"/"optional" → List[Tuple[key, display_name]]
#         "depends_on_chapters" → List[int] (optional)
# ═══════════════════════════════════════════════════════════════

CHAPTER_DATA_REQUIREMENTS: Dict[int, Dict[str, Any]] = {
    1: {
        "required": [
            ("report_title", "报告标题（决策名称）", "critical"),
            ("location", "拟征地位置（街道/社区/村组）", "critical"),
        ],
        "recommended": [
            ("org_name", "稳评责任单位名称"),
            ("area_m2", "征收面积（平方米/公顷）"),
            ("area_mu", "征收面积（亩）"),
            ("land_use", "土地用途"),
            ("funding", "资金测算"),
        ],
        "optional": [
            ("household_count", "涉及户数"),
            ("compensation_standard", "补偿标准"),
            ("doc_reference", "公告文号"),
        ],
    },
    2: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "稳评责任单位"),
            ("commission_month", "委托日期"),
        ],
        "optional": [
            ("survey_start", "调查开始日期"),
            ("survey_end", "调查结束日期"),
        ],
    },
    3: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("total_samples", "调查样本总数"),
            ("support_rate", "群众支持率"),
            ("survey_start", "调查开始日期"),
            ("survey_end", "调查结束日期"),
        ],
        "optional": [
            ("awareness_rate", "知晓率"),
            ("grassroots_opinion", "基层组织意见"),
            ("villager_demands", "村民诉求"),
            ("online_opinion", "网络舆情"),
        ],
    },
    4: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位"),
            ("location", "项目位置"),
            ("land_use", "土地用途"),
        ],
        "optional": [
            ("support_rate", "支持率"),
            ("funding", "资金来源"),
        ],
    },
    5: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("support_rate", "群众支持率"),
        ],
        "optional": [
            ("compensation_standard", "补偿标准"),
            ("funding", "资金测算"),
        ],
    },
    6: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("support_rate", "群众支持率（影响打分）"),
        ],
        "optional": [],
    },
    7: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位（措施责任主体）"),
        ],
        "optional": [],
    },
    8: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [],
        "optional": [],
        "depends_on_chapters": [6],
    },
    9: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [],
        "optional": [],
        "depends_on_chapters": [4, 5, 6, 8],
    },
    10: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位（应急指挥部名称）"),
        ],
        "optional": [],
    },
}

# ═══════════════════════════════════════════════════════════════
# Format Compliance Rules per Chapter (was in format_compliance_agent.py)
# ═══════════════════════════════════════════════════════════════

CHAPTER_FORMAT_RULES: Dict[int, Dict[str, Any]] = {
    1: {"must_contain": ["决策名称", "决策主体"], "must_have_table": False},
    2: {"must_contain": ["评估过程", "评估方法", "评估依据"], "must_have_table": False},
    3: {"must_contain": ["问卷调查", "利益相关者"], "must_have_table": False},
    4: {"must_contain": ["合法性", "合理性", "可行性", "可控性"], "must_have_table": False},
    5: {"must_contain": ["风险因素"], "must_have_table": True},  # 金湖模板仅此章有表格
    6: {"must_contain": ["措施前", "得分"], "must_have_table": False},
    7: {"must_contain": ["风险防范", "化解"], "must_have_table": False},
    8: {"must_contain": ["措施后", "得分"], "must_have_table": False},
    9: {"must_contain": ["评估结论", "建议"], "must_have_table": False},
    10: {"must_contain": ["组织指挥", "分级响应", "处置措施"], "must_have_table": False},
}


def get_chapter_rag_query(chapter_number: int) -> str:
    """Get the RAG search query template for a chapter."""
    return CHAPTER_RAG_QUERIES.get(
        chapter_number,
        f"社会稳定风险评估报告 第{chapter_number}章"
    )


def get_chapter_data_requirements(chapter_number: int) -> Dict[str, List[str]]:
    """Get data requirements (required/recommended/optional) for a chapter."""
    return CHAPTER_DATA_REQUIREMENTS.get(chapter_number, {
        "required": ["report_title"],
        "recommended": [],
        "optional": [],
    })


def get_chapter_format_rules(chapter_number: int) -> Dict[str, Any]:
    """Get format compliance rules for a chapter."""
    return CHAPTER_FORMAT_RULES.get(chapter_number, {
        "must_contain": [],
        "must_have_table": False,
    })
