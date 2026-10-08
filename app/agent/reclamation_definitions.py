# -*- coding: utf-8 -*-
"""复垦方案章节结构 —— 唯一事实源。

结构由 5 份真实复垦方案交叉验证得出（设计文档 §3）：
level-1 与 level-2 在 5 份中完全一致，差异只在 level-3。
字数区间取自 5 份报告的逐章统计。

与稳评的 `chapter_definitions.py` 并列，**不复用** —— 稳评那套写死了 14 章、
（一）（二）体例、DB32/T4013 等，与复垦全不相干。通用工具
（`cn_num_to_int` / `chapter_no_of_heading`）直接 import 稳评那份，不复制。
"""

RECLAMATION_TOTAL_CHAPTERS = 10
RECLAMATION_ALL_CHAPTERS = list(range(1, RECLAMATION_TOTAL_CHAPTERS + 1))

# 章类型：fact=叙述章；table=以表格为主的章
RECLAMATION_CHAPTER_KIND = {
    1: "fact", 2: "fact", 3: "fact", 4: "fact", 5: "fact",
    6: "table",    # 工程设计及工程量测算
    7: "table",    # 投资估算
    8: "fact", 9: "fact", 10: "fact",
}

RECLAMATION_CHAPTER_DEFINITIONS = {
    1: {
        "title": "前言",
        "description": "编制背景及过程、复垦方案摘要（服务年限、各类土地面积、"
                       "土地损毁情况、复垦目标、投资情况、生态空间管控情况）",
        "key_tables": [],
        "sections": ["1.1 编制背景及过程", "1.2 复垦方案摘要"],
        "min_words": 2100, "max_words": 2800,
    },
    2: {
        "title": "编制总则",
        "description": "编制目的、编制原则、编制依据（法律法规、政策文件、标准规范、其他技术资料）",
        "key_tables": [],
        "sections": ["2.1 编制目的", "2.2 编制原则", "2.3 编制依据"],
        "min_words": 2300, "max_words": 3100,
    },
    3: {
        "title": "项目概况",
        "description": "项目简介、项目区自然概况（地理位置、气候、地貌、土壤、水文、"
                       "地质、植被与生物）、社会经济概况、项目区土地利用状况",
        "key_tables": [],
        "sections": ["3.1 项目简介", "3.2 项目区自然概况",
                     "3.3 社会经济概况", "3.4 项目区土地利用状况"],
        "min_words": 1500, "max_words": 2450,
    },
    4: {
        "title": "土地复垦方向可行性分析",
        "description": "土地损毁分析与评定、复垦区土地利用状况、生态环境影响分析、"
                       "土地复垦适宜性评价、水土资源平衡分析、"
                       "生态保护红线及生态空间管控区分析、复垦的目标任务",
        "key_tables": ["临时用地土地利用现状统计表", "评价单元土地损毁情况表",
                       "评价单元复垦方向表"],
        "sections": ["4.1 土地损毁分析与评定", "4.2 复垦区土地利用状况",
                     "4.3 生态环境影响分析", "4.4 土地复垦适宜性评价",
                     "4.5 水土资源平衡分析",
                     "4.6 生态保护红线及生态空间管控区分析", "4.7 复垦的目标任务"],
        "min_words": 7500, "max_words": 9000,
    },
    5: {
        "title": "土地复垦质量要求与复垦措施",
        "description": "质量要求、预防控制措施、复垦措施（工程、生物化学、"
                       "永久基本农田）、监测措施、管护措施",
        "key_tables": [],
        # 注：5.2 下的「路线方案设计中的预防控制措施」仅线性工程（铁路/道路）有，
        #     点状工程（小区/厂区）没有 —— 该小节为条件小节，见 §3.1 差异表
        "sections": ["5.1 土地复垦质量要求", "5.2 预防控制措施", "5.3 复垦措施",
                     "5.4 监测措施", "5.5 管护措施"],
        "min_words": 4000, "max_words": 4600,
    },
    6: {
        "title": "土地复垦工程设计及工程量测算",
        "description": "工程设计（工程技术措施、生物化学措施、监测管护措施设计）"
                       "及工程量测算",
        "key_tables": ["工程量测算表"],
        "sections": ["6.1 工程设计", "6.2 工程量测算"],
        "min_words": 1200, "max_words": 1600,
    },
    7: {
        "title": "土地复垦投资估算",
        "description": "估算说明（编制依据、费用构成、投资情况）与估算成果"
                       "（工程施工费、设备购置费、其他费用、监测管护费、预备费、技术经济指标）",
        "key_tables": ["措施费取费费率表", "间接费取费费率表", "工程施工费估算表",
                       "其他费用估算表", "土地复垦投资估算表"],
        "sections": ["7.1 估算说明", "7.2 估算成果"],
        "min_words": 3100, "max_words": 4850,
    },
    8: {
        "title": "土地复垦工作计划安排",
        "description": "服务年限、工作计划安排、费用安排",
        "key_tables": ["复垦工作计划与投资安排表"],
        "sections": ["8.1 土地复垦服务年限", "8.2 土地复垦工作计划安排",
                     "8.3 土地复垦费用安排"],
        "min_words": 650, "max_words": 950,
    },
    9: {
        "title": "土地复垦效益分析",
        "description": "社会效益、生态效益、经济效益评价，复垦后耕地质量",
        "key_tables": [],
        "sections": ["9.1 社会效益评价", "9.2 生态效益评价",
                     "9.3 经济效益评价", "9.4 复垦后耕地质量"],
        "min_words": 930, "max_words": 1000,
    },
    10: {
        "title": "保障措施",
        "description": "组织保障、监管保障、技术保证、费用保障、公众参与、"
                       "土地权属调整方案",
        "key_tables": [],
        "sections": ["10.1 组织保障措施", "10.2 监管保障措施", "10.3 技术保证措施",
                     "10.4 费用保障措施", "10.5 公众参与", "10.6 土地权属调整方案"],
        "min_words": 2900, "max_words": 5800,
    },
}


def get_reclamation_chapter_title(chapter_number: int) -> str:
    return RECLAMATION_CHAPTER_DEFINITIONS.get(
        chapter_number, {}).get("title", f"第{chapter_number}章")


def get_reclamation_chapter_kind(chapter_number: int) -> str:
    return RECLAMATION_CHAPTER_KIND.get(chapter_number, "fact")
