"""标准报告标题体系 — 二级标题的**唯一事实源在 chapter_definitions.CHAPTER_SECTION_SPECS**。

本模块不再自持一份标题表（此前与章节结构规范重复定义、易走样），
改为按需从 `CHAPTER_SECTION_SPECS` 派生，保证「标题体系」与「章节结构规范」永远一致。

来源标注:
  [4937] = DB32/T 4937-2024 土地征收前社会稳定风险评估规范 附录D
  [428]  = 发改办投资〔2013〕428号 评估报告编制大纲
  [4013] = DB32/T 4013-2021 第三方社会稳定风险评估规范 附录G
  [案卷] = 江苏省稳评工作案卷（人工成品）标准结构

LLM禁止自行编造标题。所有标题严格按此列表使用。
"""

from typing import Dict, List

from app.agent.chapter_definitions import (
    CHAPTER_DEFINITIONS,
    get_chapter_section_specs,
)


def _derive_headings() -> Dict[int, List[str]]:
    """从章节结构规范派生 {章号: [二级标题, ...]}。"""
    out: Dict[int, List[str]] = {}
    for num in CHAPTER_DEFINITIONS:
        specs = get_chapter_section_specs(num)
        out[num] = [s.get("title", "") for s in specs if s.get("title")]
    return out


# 每章的标准子标题 (章节号 → [标题文本])，由结构规范派生
CHAPTER_HEADINGS: Dict[int, List[str]] = _derive_headings()

# 附件章标准清单（与 chapter_templates.CH12_APPENDIX_LIST 对齐）
APPENDIX_HEADINGS = [
    "拟征地位置示意图",
    "拟征地公告",
    "勘测定界报告",
    "座谈会签到表",
    "稳评问卷调查表",
    "专家评审会照片",
    "专家评审签到表",
    "专家评审意见",
]


def get_chapter_headings(chapter_num: int) -> list:
    return CHAPTER_HEADINGS.get(chapter_num, [])


def format_headings_for_prompt(chapter_num: int) -> str:
    headings = get_chapter_headings(chapter_num)
    if not headings:
        return ""
    lines = [
        "\n## 本章必须使用的子标题（来源见标注，禁止修改或编造）",
    ]
    for h in headings:
        lines.append(f"- {h}")
    lines.append("\n严格按上述标题和顺序撰写，不得跳过、合并或新增标题。")
    return "\n".join(lines)
