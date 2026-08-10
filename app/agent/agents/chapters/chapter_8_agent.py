"""Chapter8Agent — 措施后风险等级评估."""

from typing import Dict, List, Any
from .chapter_base import ChapterAgentBase


class Chapter8Agent(ChapterAgentBase):
    name = "Chapter8Agent"
    description = "生成第8章：措施后风险等级评估"
    covered_steps = [8, 13]
    chapter_number = 8
    chapter_title = "措施后风险等级评估"
    rag_query_extra = "措施后风险等级 重新评估 得分对比 风险下降"
    required_data_keys = ["report_title"]
    key_tables = []

    def _build_llm_prompt(self, state, rag_context, user_data):
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})

        scoring_text = ""
        try:
            from app.services.scoring_service import scoring_service
            report = scoring_service.build_scoring_report(filled, {})
            scoring_text = scoring_service.format_for_llm(report)
        except:
            pass

        generated = state.get("generated_sections", {})
        ch6_content = generated.get("chapter_6", {}).get("markdown", "")
        ch7_content = generated.get("chapter_7", {}).get("markdown", "")

        system = (
            f"你是江苏众拓项目代理咨询有限公司的稳评工程师，在淮安做了8年征地稳评。\n"
            f"现在写第八章「措施后风险等级评估」。\n\n"
            f"## 任务\n"
            f"基于第七章的防范化解措施，对措施实施后的风险状况进行重新评估。\n"
            f"写一段1500-2000字的连贯文字，纯文字叙述。\n\n"
            f"## DB32/T4013-2021 评分规则（和第六章一样）\n"
            f"- 总分44分制\n"
            f"- 合法性满分16分 | 合理性满分6分 | 可行性满分10分 | 可控性满分12分\n"
            f"- ≤20分=低风险(A级) | 21-35分=中风险(B级) | ≥36分=高风险(C级)\n"
            f"- 措施后总分应低于措施前（风险下降了）\n\n"
            f"## 写作要求\n"
            f"- 每个维度用自然段落写，不用子标题\n"
            f"- 重点说明措施后每个维度为什么得分改善了\n"
            f"- 引用第七章的具体措施，说清楚哪项措施起了什么效果\n"
            f"- 严格使用「系统评分参考」中的数字\n"
            f"- 不要编造具体数据（宣传场次/入户数量/满意度百分比等）\n"
            f"- 🔴 分析结束后插入：![图8-1 专家评审意见]\n\n"
            f"## ⛔ 禁止\n"
            f"- 禁止生成表格（如措施前后对比表等）\n"
            f"- 禁止创建子标题\n"
            f"- 禁止使用100分制\n"
            f"- 禁止AI套词\n"
        )

        user = (
            f"## 项目\n"
            f"报告标题：{report_title}\n"
            f"位置：{filled.get('location', '')}\n"
            f"涉及村组：{filled.get('villages', '')}\n\n"
            f"## 系统评分参考（唯一数字来源，严格使用）\n{scoring_text}\n\n"
            f"## 第六章措施前评估\n{ch6_content[:1000] if ch6_content else ''}\n\n"
            f"## 第七章措施\n{ch7_content[:1000] if ch7_content else ''}\n\n"
            f"## 知识库参考\n{rag_context.get('chapter_context', '')[:2000]}\n\n"
            f"请撰写第八章正文。用定性方式描述改善效果，数字只用系统评分参考里的。"
        )
        return system, user

    def _fallback_content(self, state: dict) -> str:
        pre_total = post_total = None
        try:
            from app.services.scoring_service import scoring_service
            report = scoring_service.build_scoring_report(state.get("filled_data", {}), {})
            pre_total = report["pre_measures"]["total"]
            post_total = report["post_measures"]["total"]
        except:
            pass
        score_text = ""
        if post_total is not None and pre_total is not None:
            score_text = f"措施后综合得分：{post_total}分（较措施前{pre_total}分降低了{pre_total - post_total}分），"
        return (
            f"## 八、措施后风险等级评估\n\n"
            f"在全面落实第七章各项风险防范化解措施后重新评估。\n\n"
            f"补偿方案优化后群众接受度提升；政策宣传到位；信访渠道畅通；"
            f"舆情监测机制有效运行；应急处置预案已制定并落实保障。\n\n"
            f"各项措施有效降低了风险发生的可能性和影响程度。\n\n"
            f"{score_text}风险等级判定为：**低风险（A级）**。"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["措施后", "风险等级", "重新评估", "风险下降"]
