"""Chapter10Agent — 应急预案.

Generates Chapter 10: emergency response plan.
Matches the 金湖 reference template exactly: 3 sections, narrative text, no tables.
"""

from typing import Dict, List, Any

from .chapter_base import ChapterAgentBase


class Chapter10Agent(ChapterAgentBase):
    """Generate Chapter 10: 应急预案 — 金湖模板格式."""

    name = "Chapter10Agent"
    description = "生成第10章：应急预案（组织指挥体系+分级响应+处置措施，纯文字无表格）"
    covered_steps = [10, 15]
    chapter_number = 10
    chapter_title = "应急预案"
    rag_query_extra = "应急预案 组织指挥 分级响应 群体性事件处置"
    required_data_keys = [
        "report_title",
    ]
    key_tables = []  # 金湖模板第10章无表格

    def _build_llm_prompt(
        self, state: dict, rag_context: Dict[str, Any], user_data: Dict[str, Any]
    ) -> tuple:
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})
        org_name = filled.get("org_name", "相关单位")
        location = filled.get("location", "项目所在地")

        system = (
            f"你是一位专业的社会稳定风险评估报告撰写专家。\n"
            f"正在撰写报告的第十章「应急预案」。\n\n"
            f"## 模板格式（严格遵循，不要增减章节）\n"
            f"### 10.1 组织指挥体系\n"
            f"一段文字：成立XX项目/地区征地稳评应急工作领导小组，由谁任组长，\n"
            f"哪些部门负责人为成员，办公室设在哪里。\n\n"
            f"### 10.2 分级响应\n"
            f"列表形式：\n"
            f"- 一般事件（Ⅳ级）：由乡镇（街道）负责处置\n"
            f"- 较大事件（Ⅲ级）：由县应急工作领导小组负责处置\n"
            f"- 重大事件（Ⅱ级及以上）：报请市级相关部门协助处置\n\n"
            f"### 10.3 处置措施\n"
            f"列表形式，4-5项：\n"
            f"1. 群体性事件：迅速组织公安力量控制现场，做好群众疏导工作\n"
            f"2. 恶性事件：立即启动公安应急机制，确保人员安全\n"
            f"3. 网络舆情事件：启动舆情应急预案，及时发布权威信息\n"
            f"4. 信访事件：按照信访工作条例及时受理、妥善处理\n\n"
            f"## 核心规则\n"
            f"1. 严格按上述3个小节结构写，不要添加其他小节\n"
            f"2. 禁止生成任何表格\n"
            f"3. 结合项目实际：{report_title}、{location}\n"
            f"4. 字数：300-500字\n"
            f"5. 输出纯Markdown格式\n"
        )

        user = (
            f"## 项目信息\n"
            f"报告标题：{report_title}\n"
            f"责任单位：{org_name}\n"
            f"项目位置：{location}\n"
            f"实施单位：江苏众拓项目代理咨询有限公司\n\n"
            f"## 知识库参考（法规依据）\n"
            f"{rag_context.get('chapter_context', '')[:1500]}\n"
            f"{rag_context.get('example_context', '')[:1000]}\n\n"
            f"请按上述金湖模板格式撰写第十章，三段式结构，不添加其他内容。"
        )

        return system, user

    def _fallback_content(self, state: dict) -> str:
        title = state.get("report_title", "本项目")
        filled = state.get("filled_data", {})
        org = filled.get("org_name", "相关单位")
        loc = filled.get("location", "项目所在地")

        return (
            f"## 十、应急预案\n\n"
            f"### 10.1 组织指挥体系\n\n"
            f"成立{title}项目征地稳评应急工作领导小组，由{org}负责人任组长，"
            f"街道办、公安局、信访局、应急管理局等部门负责人为成员，"
            f"办公室设在{org}。\n\n"
            f"### 10.2 分级响应\n\n"
            f"- 一般事件（Ⅳ级）：由乡镇（街道）负责处置\n"
            f"- 较大事件（Ⅲ级）：由县应急工作领导小组负责处置\n"
            f"- 重大事件（Ⅱ级及以上）：报请市级相关部门协助处置\n\n"
            f"### 10.3 处置措施\n\n"
            f"1. 群体性事件：迅速组织公安力量控制现场，做好群众疏导工作\n"
            f"2. 恶性事件：立即启动公安应急机制，确保人员安全\n"
            f"3. 网络舆情事件：启动舆情应急预案，及时发布权威信息\n"
            f"4. 信访事件：按照信访工作条例及时受理、妥善处理\n"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["应急预案", "组织指挥", "分级响应", "群体性事件", "处置措施"]
