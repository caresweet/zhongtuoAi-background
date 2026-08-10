"""Chapter9Agent — 评估结论与建议."""

from typing import Dict, List, Any
from .chapter_base import ChapterAgentBase


class Chapter9Agent(ChapterAgentBase):
    name = "Chapter9Agent"
    description = "生成第9章：评估结论与建议"
    covered_steps = [9]
    chapter_number = 9
    chapter_title = "评估结论与建议"
    rag_query_extra = "评估结论 建议 低风险 可实施"
    required_data_keys = ["report_title"]
    key_tables = []

    def _build_llm_prompt(self, state, rag_context, user_data):
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})
        org_name = filled.get("org_name", "相关单位")
        generated = state.get("generated_sections", {})
        ch4 = generated.get("chapter_4", {}).get("markdown", "")
        ch6 = generated.get("chapter_6", {}).get("markdown", "")
        ch8 = generated.get("chapter_8", {}).get("markdown", "")

        system = (
            f"你是一位资深社会稳定风险评估专家。\n\n"
            f"正在撰写第九章「评估结论与建议」。这是报告的收尾章节。\n"
            f"本章字数目标：1500-2000字。\n\n"
            f"## 结构（严格遵循）\n"
            f"### 9.1 评估结论\n"
            f"第一段（100-150字）：总括句——经综合评估，XX项目决策社会稳定风险等级为低风险（A级）。\n"
            f"第二段（150-200字）：合法性结论——引用具体法律条款，说明主体适格、目的合法、程序合规。\n"
            f"第三段（150-200字）：合理性结论——补偿方案公平合理，符合区片综合地价，保障群众权益。\n"
            f"第四段（150-200字）：可行性结论——资金到位、条件具备、群众接受度高。\n"
            f"第五段（150-200字）：可控性结论——风险因素已识别并制定措施，整体可控。\n"
            f"第六段（100字）：综合研判——建议在全面落实措施后予以实施。\n\n"
            f"### 9.2 建议\n"
            f"5条建议，每条150-200字，格式：**序号. 建议名称：**详细内容和操作方法。\n"
            f"1. 严格执行征收补偿政策\n"
            f"2. 加强就业帮扶和社会保障\n"
            f"3. 做好施工期间群众工作\n"
            f"4. 建立风险监测预警和应急处置机制\n"
            f"5. 落实跟踪评估和\"回头看\"制度\n\n"
            f"## 🔴 铁律\n"
            f"- 只允许9.1和9.2两个子标题\n"
            f"- 禁止生成表格\n"
            f"- 结论必须与前面各章一致\n"
            f"- 建议具体可操作\n"
            f"- 禁止【待补充】\n"
            f"- 🔴 9.2节末尾插入：![图9-1 稳评专家评审意见表]"
        )

        user = (
            f"## 项目\n报告标题：{report_title}\n责任单位：{org_name}\n\n"
            f"## 第四章决策分析摘要\n{ch4[:1500] if ch4 else ''}\n\n"
            f"## 第六章措施前评估摘要\n{ch6[:800] if ch6 else ''}\n\n"
            f"## 第八章措施后评估摘要\n{ch8[:800] if ch8 else ''}\n\n"
            f"## 知识库参考\n{rag_context.get('chapter_context', '')[:2000]}\n{rag_context.get('example_context', '')[:1000]}\n\n"
            f"请撰写第九章正文（1500-2000字）。"
        )
        return system, user

    def _fallback_content(self, state: dict) -> str:
        filled = state.get("filled_data", {})
        title = state.get("report_title", "本项目")
        doc_ref = filled.get("doc_reference", "")
        return (
            f"## 九、评估结论与建议\n\n"
            f"### 9.1 评估结论\n\n"
            f"经综合评估，{title}项目"
            + (f"（{doc_ref}）" if doc_ref else "") +
            f"决策社会稳定风险等级为**低风险（A级）**。\n\n"
            f"合法性方面，项目决策主体适格、目的合法、程序合规，符合《土地管理法》相关规定。\n"
            f"合理性方面，补偿方案公平合理，符合区片综合地价要求，能保障群众合法权益。\n"
            f"可行性方面，资金已纳入财政预算，施工条件具备，群众支持度较高，实施条件成熟。\n"
            f"可控性方面，主要风险因素已识别并制定防范化解措施，社会稳定风险整体可控。\n\n"
            f"综合研判：建议在全面落实各项风险防范化解措施后予以实施。\n\n"
            f"### 9.2 建议\n\n"
            f"1. **严格执行征收补偿政策。**按批准的补偿方案及时足额发放补偿资金，"
            f"确保被征地农民原有生活水平不降低、长远生计有保障。\n\n"
            f"2. **加强被征地农民的就业帮扶和社会保障。**将符合条件人员纳入城乡居民基本养老保险，"
            f"提供就业培训和岗位推荐服务。\n\n"
            f"3. **做好施工期间的群众工作。**合理安排施工时间，采取降噪防尘措施，"
            f"最大限度减少施工对周边群众生活的影响。\n\n"
            f"4. **建立风险监测预警和应急处置机制。**定期排查矛盾纠纷，"
            f"及时发现和处置新的风险点，完善维稳应急预案。\n\n"
            f"5. **落实跟踪评估和\"回头看\"制度。**持续跟踪各项措施落实效果，"
            f"对发现的问题及时整改，确保评估结论的持续有效性。"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["评估结论", "低风险", "建议", "风险等级", "可实施"]
