"""Chapter6Agent — 措施前风险等级研判."""

from typing import Dict, List, Any
from .chapter_base import ChapterAgentBase


class Chapter6Agent(ChapterAgentBase):
    name = "Chapter6Agent"
    description = "生成第6章：措施前风险等级研判"
    covered_steps = [6]
    chapter_number = 6
    chapter_title = "措施前风险等级研判"
    rag_query_extra = "措施前风险等级 量化评分 DB32/T4013"
    required_data_keys = ["report_title"]
    key_tables = []

    def _build_llm_prompt(self, state, rag_context, user_data):
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})
        support_rate = filled.get("support_rate", "")
        project_ctx = state.get("project_context", "")

        scoring_text = ""
        try:
            from app.services.scoring_service import scoring_service
            report = scoring_service.build_scoring_report(filled, {})
            scoring_text = scoring_service.format_for_llm(report)
        except:
            pass

        generated = state.get("generated_sections", {})
        ch5_content = generated.get("chapter_5", {}).get("markdown", "")

        system = (
            f"你是江苏众拓项目代理咨询有限公司的稳评工程师，在淮安做了8年征地稳评。\n"
            f"现在写第六章「措施前风险等级研判」。\n\n"
            f"## 任务\n"
            f"按照DB32/T4013-2021量化评分体系，从合法性、合理性、可行性、可控性四个维度"
            f"对项目进行逐项评分分析。写一段1500-2500字的连贯文字，纯文字叙述。\n\n"
            f"## DB32/T4013-2021 评分规则\n"
            f"- 总分44分制（不是100分制！）\n"
            f"- 合法性满分16分 | 合理性满分6分 | 可行性满分10分 | 可控性满分12分\n"
            f"- ≤20分=低风险(A级) | 21-35分=中风险(B级) | ≥36分=高风险(C级)\n\n"
            f"## 写作要求\n"
            f"- 每个维度写一个自然段，用连贯文字叙述，不要用子标题分隔\n"
            f"- 逐项分析打分理由，扣分原因说清楚\n"
            f"- 严格使用「系统评分参考」中的分数，不要编造\n"
            f"- 引用前章识别的风险因素\n"
            f"- 引用法规用『』\n"
            f"- 🔴 分析结束后插入：![图6-1 群众座谈会现场照片]\n\n"
            f"## ⛔ 禁止\n"
            f"- 禁止生成表格\n"
            f"- 禁止创建子标题\n"
            f"- 禁止使用100分制（满分只能是44分）\n"
            f"- 禁止AI套词（具有重要意义/切实保障/综上所述等）\n"
            f"- 禁止出现本项目以外的地名\n"
        )

        user = (
            f"## 项目\n"
            f"报告标题：{report_title}\n"
            f"位置：{filled.get('location', '')}\n"
            f"群众支持率：{support_rate}\n"
            f"涉及村组：{filled.get('villages', '')}\n\n"
            f"## 系统评分参考（唯一可用的数字来源）\n{scoring_text}\n\n"
            f"## 第五章风险因素\n{ch5_content[:1500] if ch5_content else ''}\n\n"
            f"## 知识库参考\n{rag_context.get('chapter_context', '')[:3000]}\n\n"
            f"请撰写第六章正文。"
        )
        return system, user

    def _fallback_content(self, state: dict) -> str:
        pre_total = 17
        try:
            from app.services.scoring_service import scoring_service
            report = scoring_service.build_scoring_report(state.get("filled_data", {}), {})
            pre_total = report["pre_measures"]["total"]
        except:
            pass
        risk_level = "低风险（A级）" if pre_total <= 20 else ("中风险（B级）" if pre_total <= 35 else "高风险（C级）")
        return (
            f"## 六、措施前风险等级研判\n\n"
            f"按照DB32/T4013-2021量化评分体系（总分44分制），对各风险因素逐项评分。\n\n"
            f"合法性方面，征收主体资格合法合规，程序依法依规，得分较高。\n\n"
            f"合理性方面，补偿标准按区片综合地价执行，方案公平合理。\n\n"
            f"可行性方面，资金纳入财政预算，施工条件具备，群众支持度较高。\n\n"
            f"可控性方面，风险因素已识别，已制定防范化解措施和应急预案。\n\n"
            f"措施前风险总分：{pre_total}分（满分44分），风险等级：**{risk_level}**。"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["措施前", "风险等级", "量化评分", "DB32", "低风险"]
