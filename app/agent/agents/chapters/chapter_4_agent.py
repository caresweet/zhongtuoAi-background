"""Chapter4Agent — 稳评工作方案。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter4Agent(ChapterAgentBase):
    name = "Chapter4Agent"
    description = "生成第4章：稳评工作方案"
    covered_steps = [4]
    chapter_number = 4
    chapter_title = "稳评工作方案"
    rag_query_extra = "稳评工作方案 评估范围 方法 步骤 工作计划"
    required_data_keys = ["project_name"]
    key_tables = []

    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """🔴 按参照件模板，本章（社会稳定风险评估工作报告）要出**措施后**那一套两张表。

        模板第四章 = 表4-1 主要风险因素识别结果表 + 表4-2 …量化指标评价体系（措施后）
        + 表4-3 群众意见分析。第12章另出一套「措施前」的，两章别混。
        """
        parts = ["## 🔒 本章体例约束（按参照件模板）",
                 "**本章二级标题固定为：（一）项目基本情况 /（二）四性分析 /"
                 "（三）不稳定风险因素 /（四）稳评结论及工作建议 /（五）参与人员签字。"
                 "一个都不能少、不能改名，更不能拿表题去顶替小节标题。**",
                 "",
                 "两张新表放在「（三）不稳定风险因素」（**含表4-1**）**之后**，"
                 "即写在「（四）稳评结论及工作建议」这一节里 —— "
                 "表号由系统按出现顺序编，摆在 表4-1 后面才会得到 表4-2 / 表4-3。",
                 "表题**单独一行、不带任何 # 号**，紧跟表格，例如：",
                 "表4-2 稳评风险等级确定量化指标评价体系（措施后）",
                 "紧接着就是：`| 测评指标 | 权重 | 测评项目 | 评分 | 评分标准 | 得分 |` 及其数据行。",
                 "另一张同理：`表4-3 群众意见分析`，列：序号 | 反对率百分比a | 风险发生概率或激烈程度b | 得分。",
                 "- 🔴 表题一律用**普通正文行**，绝不许写成 `### …` 或加粗标题；",
                 "- 🔴 表内数值必须取自下方评分服务给出的**措施后**结果，不得自行推算、不得编造。"]
        try:
            from app.services.scoring_service import scoring_service
            rep = scoring_service.build_scoring_report(state.get("filled_data", {}) or {}, {})
            txt = scoring_service.format_for_llm(rep)
            if txt:
                parts.append("\n## 🧮 量化评分结果（表4-2、表4-3 的唯一数据源）")
                parts.append(txt)
        except Exception:
            pass
        return "\n".join(parts)

