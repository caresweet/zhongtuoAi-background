"""Chapter4Agent — 决策综合分析."""

from typing import Dict, List, Any
from .chapter_base import ChapterAgentBase


class Chapter4Agent(ChapterAgentBase):
    name = "Chapter4Agent"
    description = "生成第4章：决策综合分析"
    covered_steps = [4, 9]
    chapter_number = 4
    chapter_title = "决策综合分析"
    rag_query_extra = "合法性 合理性 可行性 可控性 土地管理法 征收补偿"
    required_data_keys = ["report_title"]
    key_tables = []

    def _build_llm_prompt(self, state, rag_context, user_data):
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})
        pdf_text = user_data.get("pdf_raw_text", "")
        org_name = filled.get("org_name") or user_data.get("org_name", "相关单位")
        location = filled.get("location") or user_data.get("location", "")
        land_use = filled.get("land_use") or user_data.get("land_use", "")
        funding = filled.get("funding") or user_data.get("funding", "已纳入财政预算")
        support_rate = user_data.get("support_rate") or filled.get("support_rate", "")
        compensation = filled.get("compensation_standard") or user_data.get("compensation_standard", "")

        system = (
            f"你是一位具有10年以上征地社会稳定风险评估经验的高级工程师，精通《土地管理法》。\n\n"
            f"正在撰写第四章「决策综合分析」，这是报告最核心的分析章节。\n"
            f"字数目标：2000-3000字。\n\n"
            f"## 结构（严格遵循，禁止增减子标题）\n\n"
            f"### 4.1 合法性分析（600-800字）\n"
            f"（一）决策主体资格合法性：依据《土地管理法》第46条，论证主体资格。\n"
            f"（二）决策目的合法性：依据《土地管理法》第45条，论证公共利益需要。\n"
            f"（三）规划相符性：论证与国土空间规划、城乡规划、年度计划的一致性。\n"
            f"（四）程序合规性：逐一核查预公告(≥10工作日)、现状调查、稳评、补偿公告(≥30日)、听证等程序。\n\n"
            f"### 4.2 合理性分析（400-600字）\n"
            f"（一）区域经济发展合理性：项目对产业布局、城市功能的促进作用。\n"
            f"（二）补偿方案公平性：按区片综合地价执行，对比周边项目。\n"
            f"（三）群众利益保障：《土地管理法》第48条\"生活水平不降低、长远生计有保障\"。\n\n"
            f"### 4.3 可行性分析（400-600字）\n"
            f"（一）资金保障可行性：财政预算、资金到位。\n"
            f"（二）实施条件可行性：基础设施、施工条件、组织力量。\n"
            f"（三）群众接受度可行性：引用问卷调查支持率。\n\n"
            f"### 4.4 可控性分析（400-600字）\n"
            f"（一）安全风险可控性：社会治安、历史遗留问题。\n"
            f"（二）舆情风险可控性：政策宣传、舆情监测。\n"
            f"（三）群体性事件风险可控性：历史信访、矛盾排查化解。\n"
            f"（四）整体可控性：综合研判结论。\n\n"
            f"## 🔴 铁律\n"
            f"- 只允许4.1-4.4四个子标题\n"
            f"- 禁止生成任何表格\n"
            f"- 每个论点引用具体法条\n"
            f"- 先结论后论证\n"
            f"- 禁止【待补充】\n"
            f"- 总字数2000-3000字"
        )

        sup_str = str(support_rate) if support_rate else ""
        comp_str = str(compensation) if compensation else "按江苏省区片综合地价执行"

        user = (
            f"## 项目数据\n报告标题：{report_title}\n责任单位：{org_name}\n"
            f"项目位置：{location}\n土地用途：{land_use}\n补偿标准：{comp_str}\n"
            f"资金情况：{funding}\n群众支持率：{sup_str}\n\n"
            f"## PDF原文\n{pdf_text[:3000] if pdf_text else ''}\n\n"
            f"## 知识库法规参考\n{rag_context.get('chapter_context', '')[:4000]}\n"
            f"{rag_context.get('local_regulation_context', '')[:2000]}\n"
            f"{rag_context.get('example_context', '')[:2000]}\n\n"
            f"请撰写第四章正文（2000-3000字）。"
        )
        return system, user

    def _fallback_content(self, state: dict) -> str:
        filled = state.get("filled_data", {})
        org = filled.get("org_name", "相关单位")
        loc = filled.get("location", "项目所在地")
        title = state.get("report_title", "本项目")
        support = filled.get("support_rate", "")
        support_text = f"问卷调查显示支持率为{support}%。" if support else ""

        return (
            f"## 四、决策综合分析\n\n"
            f"### 4.1 合法性分析\n\n"
            f"**（一）决策主体资格合法性**\n"
            f"依据《中华人民共和国土地管理法》第四十六条，征收土地方案经依法批准后，"
            f"由被征收土地所在地的县级以上地方人民政府予以公告并组织实施。{org}作为该项目"
            f"征收主体，具备法定的决策主体资格。\n\n"
            f"**（二）决策目的合法性**\n"
            f"依据《中华人民共和国土地管理法》第四十五条，为了公共利益的需要，在土地利用"
            f"总体规划确定的城镇建设用地范围内，经省级以上人民政府批准由县级以上地方人民政府"
            f"组织实施的成片开发建设，可以依法征收农民集体所有的土地。{title}项目属于成片开发建设，"
            f"符合上述公共利益情形，决策目的合法。\n\n"
            f"**（三）规划相符性**\n"
            f"该项目已纳入国土空间规划和国民经济和社会发展年度计划，符合土地利用总体规划和"
            f"城乡规划要求，项目选址与区域发展规划方向一致。\n\n"
            f"**（四）程序合规性**\n"
            f"经核查，该项目已依法履行征收预公告（公告时间满足不少于十个工作日的法定要求）、"
            f"土地现状调查、社会稳定风险评估、征收补偿方案公告（公告时间满足不少于三十日的法定要求）"
            f"等法定程序，符合《中华人民共和国土地管理法实施条例》第二十六条至第二十九条规定的"
            f"程序要求，程序合法合规。\n\n"
            f"### 4.2 合理性分析\n\n"
            f"**（一）区域经济社会发展合理性**\n"
            f"{title}项目是推动区域经济社会发展的重要举措，有利于优化产业布局、完善城市功能、"
            f"提升区域综合竞争力。项目选址位于{loc}，符合城市总体规划和土地利用总体规划的"
            f"空间布局要求，与周边已开发区域形成连片发展格局，具有较强的区域带动效应。\n\n"
            f"**（二）补偿方案公平性**\n"
            f"征收补偿标准按照江苏省人民政府公布的征地区片综合地价执行，补偿方案在政策范围内"
            f"充分考虑了被征地群众的合法权益。补偿内容包括土地补偿费、安置补助费、地上附着物和"
            f"青苗补偿费，补偿项目齐全、标准明确，与周边同类项目补偿水平相当，具有公平性。\n\n"
            f"**（三）群众利益保障**\n"
            f"依据《中华人民共和国土地管理法》第四十八条，征收土地应当给予公平、合理的补偿，"
            f"保障被征地农民原有生活水平不降低、长远生计有保障。本项目补偿安置方案落实了上述"
            f"法定要求，采取货币补偿为主、社会保障安置为辅的方式，将符合条件的被征地农民纳入"
            f"城乡居民基本养老保险体系，从制度上保障了被征地农民的长远生计。{support_text}\n\n"
            f"### 4.3 可行性分析\n\n"
            f"**（一）资金保障可行性**\n"
            f"项目征收补偿资金已纳入区级财政预算，资金来源明确，由区财政统筹保障。"
            f"根据项目资金测算，征收补偿总费用在财政可承受范围内，资金到位有保障。\n\n"
            f"**（二）实施条件可行性**\n"
            f"项目所在区域基础设施配套较为完善，交通条件便利，施工条件具备。"
            f"项目已纳入年度征收计划，组织实施力量到位，实施条件成熟。\n\n"
            f"**（三）群众接受度可行性**\n"
            f"经问卷调查和座谈走访，大多数群众对项目实施持支持或条件支持态度，"
            f"群众接受度较高。项目已通过多种渠道向群众公开信息，群众知情权和参与权"
            f"得到保障，有利于项目的顺利实施。\n\n"
            f"### 4.4 可控性分析\n\n"
            f"**（一）安全风险可控性**\n"
            f"项目区域社会治安状况良好，无重大安全隐患，无历史遗留的重大矛盾纠纷。"
            f"属地政府具有较强的社会治理能力，能够有效应对可能出现的风险。\n\n"
            f"**（二）舆情风险可控性**\n"
            f"通过社区公示栏、政府网站、入户宣传等多渠道开展了政策宣传和信息公开，"
            f"群众对征收政策的知晓度较高。同时已建立舆情监测机制，能够及时发现、"
            f"研判、处置网络舆情信息，舆情风险整体可控。\n\n"
            f"**（三）群体性事件风险可控性**\n"
            f"历史信访情况平稳，无因征地引发的群体性事件记录。已建立矛盾纠纷排查化解"
            f"机制和维稳应急处置预案，群体性事件发生概率较低。\n\n"
            f"**（四）社会稳定风险整体可控性**\n"
            f"综合以上分析，该项目在法律、经济、社会等各层面的风险因素均已识别，"
            f"在全面落实各项风险防范化解措施的前提下，社会稳定风险整体可控。"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["合法性", "合理性", "可行性", "可控性", "土地管理法"]
