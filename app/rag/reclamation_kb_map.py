# -*- coding: utf-8 -*-
"""复垦「章 → 内容类型库」映射。

与稳评的 `kb_chapter_map.CHAPTER_KB_TYPES` 同构，但**主要按章建库，个别章共享
类型库**：因 5 份复垦样本的 level-1/level-2 章节结构基本一致（设计文档 §3），
多数章各建一库即可。少数章仍共享同一类型库 —— 如第 8、9 章都落到「计划与效益」，
第 1 章把第 3 章的「项目概况」列为备选 —— 故并非严格的「一章一库」。

库名一律 `kb_reclamation_` 前缀，与稳评的 `knowledge_base`、
投标的 `bidding_knowledge` 物理隔离。
"""

# 语义类 → collection 名（单一事实源）
COLLECTIONS = {
    "前言":          "kb_reclamation_preface",
    "编制总则":      "kb_reclamation_general",
    "项目概况":      "kb_reclamation_overview",
    "方向-损毁分析": "kb_reclamation_damage",
    "方向-生态影响": "kb_reclamation_ecology",
    "方向-适宜性":   "kb_reclamation_suitability",
    "方向-水土平衡": "kb_reclamation_water_soil",
    "措施":          "kb_reclamation_measures",
    "工程设计":      "kb_reclamation_design",
    "投资估算":      "kb_reclamation_investment",
    "计划与效益":    "kb_reclamation_plan_benefit",
    "保障措施":      "kb_reclamation_guarantee",
}

# 章号 → (主类型库, 备选类型库列表)，对齐稳评 CHAPTER_KB_TYPES 的「主 + 备」结构
RECLAMATION_KB_TYPES = {
    1:  ("前言",          ["项目概况"]),
    2:  ("编制总则",      []),
    3:  ("项目概况",      ["前言"]),
    4:  ("方向-损毁分析", ["方向-生态影响", "方向-适宜性", "方向-水土平衡"]),
    5:  ("措施",          ["编制总则"]),
    6:  ("工程设计",      ["措施"]),
    7:  ("投资估算",      ["工程设计"]),
    8:  ("计划与效益",    ["投资估算"]),
    9:  ("计划与效益",    ["方向-水土平衡"]),
    10: ("保障措施",      ["措施"]),
}


def collection_for(section_key: str) -> str:
    """语义类 → collection 名；未知类直接失败，避免静默落到稳评库。"""
    return COLLECTIONS[section_key]
