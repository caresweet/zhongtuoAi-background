"""Chapter3Agent — 社会稳定风险因素调查."""

from typing import Dict, List, Any
from .chapter_base import ChapterAgentBase


class Chapter3Agent(ChapterAgentBase):
    name = "Chapter3Agent"
    description = "生成第3章：社会稳定风险因素调查"
    covered_steps = [3]
    chapter_number = 3
    chapter_title = "社会稳定风险因素调查"
    rag_query_extra = "社会稳定风险因素调查 问卷调查 利益相关者 公众意见"
    required_data_keys = ["report_title"]
    key_tables = []

    def _build_llm_prompt(self, state, rag_context, user_data):
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})
        pdf_text = user_data.get("pdf_raw_text", "")

        total = filled.get("total_samples", "48")
        support = filled.get("support_count", "48")
        support_rate = filled.get("support_rate", "100")

        system = (
            f"你是江苏众拓项目代理咨询有限公司的稳评工程师。\n"
            f"撰写第三章「社会稳定风险因素调查」。\n\n"
            f"## 结构\n"
            f"### 3.1 问卷调查结果\n"
            f"先写200-300字调查概况：发放{total}份、回收{total}份、有效率100%。\n"
            f"然后300-500字数据分析：知晓度、支持度({support_rate}%)、主要关切。\n\n"
            f"🔴 数据分析写完后必须输出表格标记（独占一行）：\n"
            f"[TABLE:ch3_public_survey]\n"
            f"[TABLE:ch3_dept_survey]\n\n"
            f"表格后再写100-200字简要分析。\n\n"
            f"### 3.2 利益相关者诉求\n"
            f"400-600字描述被征地农户、村集体、其他利益相关者的具体诉求。\n\n"
            f"## 铁律\n"
            f"- [TABLE:ch3_public_survey] 和 [TABLE:ch3_dept_survey] 必须出现在3.1节末尾\n"
            f"- 数据用上面提供的真实值\n"
            f"- 插入图片标记：![图3-1 征收土地预公告公示照片] ![图3-2 调查现场照片] ![图3-3 座谈会照片]\n"
            f"- 禁止AI套词\n"
        )

        user = (
            f"## 项目\n报告标题：{report_title}\n\n"
            f"## 调查数据\n"
            f"总样本：{total}份 | 支持：{support}人 | 支持率：{support_rate}%\n\n"
            f"## PDF原文\n{pdf_text[:2000] if pdf_text else ''}\n\n"
            f"## 知识库参考\n{rag_context.get('chapter_context', '')[:2000]}\n\n"
            f"请撰写第三章正文，必须包含 [TABLE:ch3_public_survey] 和 [TABLE:ch3_dept_survey]。"
        )
        return system, user

    def _fallback_content(self, state: dict) -> str:
        filled = state.get("filled_data", {})
        total = filled.get("total_samples", "48")
        support = filled.get("support_count", "48")
        rate = filled.get("support_rate", "100")
        return (
            f"## 第三章 社会稳定风险因素调查\n\n"
            f"### 3.1 问卷调查结果\n\n"
            f"本次调查共发放问卷{total}份，回收有效问卷{total}份，有效回收率100%。"
            f"调查对象覆盖被征地社区全部利益相关者。\n\n"
            f"项目支持率为{rate}%，群众对征收决策表示理解和支持。\n\n"
            f"[TABLE:ch3_public_survey]\n\n"
            f"[TABLE:ch3_dept_survey]\n\n"
            f"### 3.2 利益相关者诉求\n\n"
            f"被征地农户主要诉求为合理补偿和妥善安置。"
            f"村集体希望保障集体经济可持续发展。\n\n"
            f"![图3-1 征收土地预公告公示照片]\n"
            f"![图3-2 调查现场照片]\n"
            f"![图3-3 座谈会照片]\n"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["问卷调查", "利益相关者", "支持率", "公众意见"]
