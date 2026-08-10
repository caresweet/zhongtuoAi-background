"""Chapter5Agent — 风险因素识别与初始等级表."""

from typing import Dict, List, Any
from .chapter_base import ChapterAgentBase


class Chapter5Agent(ChapterAgentBase):
    name = "Chapter5Agent"
    description = "生成第5章：风险因素识别与初始等级表"
    covered_steps = [5]
    chapter_number = 5
    chapter_title = "风险因素识别与初始等级表"
    rag_query_extra = "风险因素识别 初始风险等级"
    required_data_keys = ["report_title"]
    key_tables = []

    def _build_llm_prompt(self, state, rag_context, user_data):
        report_title = state.get("report_title", "")
        project_ctx = state.get("project_context", "")
        filled = state.get("filled_data", {})

        system = (
            f"你是江苏众拓项目代理咨询有限公司的稳评工程师，在淮安做了8年征地稳评。\n"
            f"现在写第五章「风险因素识别与初始等级表」。\n\n"
            f"## 本章结构\n"
            f"1. 先写一段引言（150-200字）：说明用了什么方法识别风险、识别出几个风险因素\n"
            f"2. 然后是一张风险因素初始风险等级表（4列：序号/风险类型/风险因素描述/风险等级）\n"
            f"3. 表格后写一段分析（150-300字）：对每个风险简要说明为什么评这个等级\n"
            f"4. 分析结束后插入：![图5-1 专家评审会照片]\n"
            f"目标字数：1000-1800字（含表格）\n\n"
            f"## 识别6个风险因素（必须结合本项目具体情况写）\n"
            f"1. 征收程序风险 — 程序是否合规、公告是否到位\n"
            f"2. 补偿方案风险 — 群众对补偿标准的接受度\n"
            f"3. 社保安置风险 — 被征地农民社保如何落实\n"
            f"4. 资金保障风险 — 补偿资金能否及时足额到位\n"
            f"5. 信息公开风险 — 征收过程透明度、群众知情权\n"
            f"6. 施工/环境影响风险 — 后期建设中噪音扬尘对周边的影响\n\n"
            f"## ⛔ 铁律\n"
            f"- 表格每行单独一行，用回车换行分隔，4列完整\n"
            f"- 禁止把多行表格合并成一行\n"
            f"- 禁止用 | — | 作为行分隔符\n"
            f"- 禁止出现本项目以外的地名/村名\n"
            f"- 禁止AI套词（具有重要意义/切实保障/综上所述等）\n"
            f"- 风险等级用：较低/一般/较高，不用\"低/中/高\"\n"
        )

        user = (
            f"## 项目信息\n"
            f"报告标题：{report_title}\n"
            f"项目背景：{project_ctx[:500]}\n"
            f"位置：{filled.get('location', '')}\n"
            f"面积：{filled.get('area_mu', '')}亩\n"
            f"用途：{filled.get('land_use', '')}\n"
            f"涉及村组：{filled.get('villages', '')}\n\n"
            f"## 知识库参考\n"
            f"{rag_context.get('chapter_context', '')[:2500]}\n"
            f"{rag_context.get('example_context', '')[:1500]}\n\n"
            f"请撰写第五章正文。"
        )
        return system, user

    def _fallback_content(self, state: dict) -> str:
        return (
            f"## 五、风险因素识别与初始等级表\n\n"
            f"根据现场勘查、问卷调查、座谈走访和资料分析，本次评估共识别出6个主要风险因素：\n\n"
            f"| 序号 | 风险因素 | 风险表现 | 风险等级 |\n"
            f"|------|---------|---------|----------|\n"
            f"| 1 | 补偿标准争议 | 群众认为补偿标准偏低，期望值高于政策标准 | 中 |\n"
            f"| 2 | 安置方式不满 | 部分群众对货币补偿方式存在疑虑 | 低 |\n"
            f"| 3 | 社会保障问题 | 被征地农民对社保政策不了解，担心长远生计 | 低 |\n"
            f"| 4 | 施工环境影响 | 施工噪音、扬尘可能影响周边群众生活 | 低 |\n"
            f"| 5 | 补偿资金发放风险 | 资金不能及时足额发放可能引发集中上访 | 中 |\n"
            f"| 6 | 信息公开不充分 | 程序透明度不足容易引发群众猜疑 | 低 |\n\n"
            f"经综合分析，上述风险因素中补偿标准争议和补偿资金发放风险为中风险，"
            f"需重点关注并制定针对性防范措施；其余四项为低风险，整体风险可控。"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["风险因素", "风险识别", "风险等级", "初始等级"]
