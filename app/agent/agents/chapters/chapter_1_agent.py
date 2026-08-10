"""Chapter1Agent — 拟征收决策基本概况.

Chapter 1 is PURE FACTS — no legal analysis, no citations.
Legal analysis (合法性/合理性/可行性/可控性) belongs in Chapter 4.
"""

from typing import Dict, List, Any

from .chapter_base import ChapterAgentBase


class Chapter1Agent(ChapterAgentBase):
    """Generate Chapter 1: 拟征收决策基本概况."""

    name = "Chapter1Agent"
    description = "生成第1章：拟征收决策基本概况"
    covered_steps = [1]
    chapter_number = 1
    chapter_title = "拟征收决策基本概况"
    rag_query_extra = "拟征收决策基本概况 征收公告 征地范围 面积 补偿标准"
    required_data_keys = ["report_title", "location"]
    key_tables = []

    def _build_llm_prompt(
        self, state: dict, rag_context: Dict[str, Any], user_data: Dict[str, Any]
    ) -> tuple:
        report_title = state.get("report_title", "")
        filled = state.get("filled_data", {})
        pdf_text = user_data.get("pdf_raw_text", "")

        org_name = filled.get("org_name") or user_data.get("org_name", "")
        implement_unit = filled.get("implement_unit") or "江苏众拓项目代理咨询有限公司"
        location = filled.get("location") or user_data.get("location", "")
        doc_ref = filled.get("doc_reference") or user_data.get("doc_reference", "")
        area_m2 = filled.get("area_m2") or user_data.get("area_m2", "")
        area_mu = filled.get("area_mu") or user_data.get("area_mu", "")
        land_use = filled.get("land_use") or user_data.get("land_use", "")
        funding = filled.get("funding") or user_data.get("funding", "")
        household = filled.get("household_count") or user_data.get("household_count", "")
        compensation = filled.get("compensation_standard") or user_data.get("compensation_standard", "")

        system = (
            f"你是江苏众拓项目代理咨询有限公司的稳评工程师。\n"
            f"现在写第一章「拟征收决策基本概况」。\n\n"
            f"## ⛔ 本章铁律：只写事实，不做法条论证\n"
            f"- 第一章是「基本情况陈述」，不是「合法性分析」\n"
            f"- 禁止引用任何法律条款（《土地管理法》第X条等）——那是第四章的事\n"
            f"- 禁止写「符合XX法规定」「属于XX法第X条情形」等论证性语句\n"
            f"- 用平实的叙述句：谁决策、在哪、多大面积、什么用途、怎么补偿\n\n"
            f"## 小节要求\n\n"
            f"### 1.1 决策名称\n"
            f"写明项目全称和文号，1-2句话。\n\n"
            f"### 1.2 决策主体\n"
            f"写明本次征收的决策主体是谁（如洪泽区人民政府）。30-50字，不要引用法条。\n\n"
            f"### 1.3 稳评责任单位\n"
            f"写明稳评责任单位全称及其职责。20-40字。\n\n"
            f"### 1.4 稳评实施单位\n"
            f"写明实施单位（江苏众拓项目代理咨询有限公司）及委托关系，30-50字。\n\n"
            f"### 1.5 项目基本情况\n"
            f"这是本章核心小节，需要详细写。内容包括：\n"
            f"- 位置：从省写到村组（江苏省淮安市洪泽区朱坝街道三圩社区二组、三组、六组）\n"
            f"- 四至范围（如数据不足可简写或省略）\n"
            f"- 总面积（㎡和亩双单位）、地类构成\n"
            f"- 地上附着物概况\n"
            f"- 土地用途\n"
            f"- 可以提一句「该地块已纳入洪泽区成片开发方案」，但不要引用法条论证\n"
            f"- 插入图片标记 ![图1-1 拟征地位置示意图]\n"
            f"本节150-250字。\n\n"
            f"### 1.6 涉及利益相关者\n"
            f"列出涉及哪些村组、大概多少户、有无企业或其他组织。用叙述式，不用清单式。\n\n"
            f"### 1.7 征收补偿方案要点\n"
            f"说明补偿标准、补偿内容、安置方式、社保安排、资金来源。\n"
            f"只写方案内容，不要引用「根据XX法规定」。\n"
            f"本节150-250字。\n\n"
            f"## ⛔ 全局禁止\n"
            f"- 禁止引用《土地管理法》任何条款\n"
            f"- 禁止创建三级标题（如1.5.1）\n"
            f"- 禁止AI套词\n"
            f"- 禁止出现本项目以外的地名\n"
            f"- 字数800-1200字\n"
        )

        area_m2_str = str(area_m2) if area_m2 else ""
        area_mu_str = str(area_mu) if area_mu else ""
        household_str = str(household) if household else ""

        user = (
            f"## 项目数据（唯一数据来源）\n\n"
            f"| 项目 | 内容 |\n"
            f"|------|------|\n"
            f"| 报告标题 | {report_title} |\n"
            f"| 征收文号 | {doc_ref or '洪拟征告〔2026〕7号'} |\n"
            f"| 责任单位 | {org_name or '洪泽区人民政府'} |\n"
            f"| 实施单位 | {implement_unit} |\n"
            f"| 项目位置 | {location or '淮安市洪泽区朱坝街道三圩社区二组、三组、六组'} |\n"
            f"| 征收面积 | {area_m2_str + '㎡' if area_m2_str else ''}{'（约' + area_mu_str + '亩）' if area_mu_str else ''} |\n"
            f"| 土地用途 | {land_use or '商业服务业设施用地'} |\n"
            f"| 涉及户数 | {household_str + '户' if household_str else '（待补充）'} |\n"
            f"| 补偿标准 | {compensation or '按江苏省区片综合地价执行'} |\n"
            f"| 资金安排 | {funding or '纳入财政预算统筹安排'} |\n\n"
            f"## PDF原文\n{chr(10).join(pdf_text.split(chr(10))[:60]) if pdf_text else '（无）'}\n\n"
            f"## 知识库参考\n{rag_context.get('chapter_context', '')[:2000]}\n\n"
            f"请撰写第一章正文——只写事实，不引用法条。"
        )

        return system, user

    def _fallback_content(self, state: dict) -> str:
        filled = state.get("filled_data", {})
        title = filled.get("report_title", state.get("report_title", ""))
        org = filled.get("org_name", "洪泽区人民政府")
        loc = filled.get("location", "淮安市洪泽区朱坝街道三圩社区二组、三组、六组")
        area_m2 = filled.get("area_m2", "")
        area_mu = filled.get("area_mu", "")
        land_use = filled.get("land_use", "商业服务业设施用地")
        household = filled.get("household_count", "")
        compensation = filled.get("compensation_standard", "江苏省征地区片综合地价标准")
        doc_ref = filled.get("doc_reference", "")

        area_line = ""
        if area_m2:
            area_line = f"拟征收土地总面积{area_m2}平方米"
            if area_mu:
                area_line += f"（约{area_mu}亩）"
        if land_use:
            area_line += f"，土地用途为{land_use}"

        loc_text = f"拟征收土地位于{loc}。" if loc else ""
        household_text = f"共涉及约{household}户。" if household else ""

        return (
            f"## 一、拟征收决策基本概况\n\n"
            f"### 1.1 决策名称\n\n"
            f"{title}项目" + (f"（{doc_ref}）" if doc_ref else "") + "。\n\n"
            f"### 1.2 决策主体\n\n"
            f"本次征收决策主体为{org}。\n\n"
            f"### 1.3 稳评责任单位\n\n"
            f"稳评责任单位为{org}，具体负责组织开展本次征地社会稳定风险评估工作。\n\n"
            f"### 1.4 稳评实施单位\n\n"
            f"稳评实施单位为江苏众拓项目代理咨询有限公司，"
            f"受稳评责任单位委托承担本次评估技术服务工作。\n\n"
            f"### 1.5 项目基本情况\n\n"
            f"{loc_text}"
            f"{area_line}。\n"
            f"该地块已纳入洪泽区国土空间规划和年度成片开发方案。\n"
            f"![图1-1 拟征地位置示意图]\n\n"
            f"### 1.6 涉及利益相关者\n\n"
            f"本次征收涉及{loc or '项目所在地'}相关社区、村组"
            + (f"，{household_text}" if household else "") + "\n"
            f"利益相关者主要包括被征地农村集体经济组织及其成员、"
            f"地上附着物所有权人、土地承包经营权人等。\n\n"
            f"### 1.7 征收补偿方案要点\n\n"
            f"补偿标准按照{compensation}执行。"
            f"补偿内容包括土地补偿费、安置补助费、地上附着物和青苗补偿费。\n"
            f"安置方式以货币补偿为主、社会保障安置为辅，"
            f"符合条件的被征地农民纳入城乡居民基本养老保险体系。\n"
            f"征收补偿资金已纳入财政预算统筹保障。"
        )

    def _get_key_phrases(self) -> List[str]:
        return ["决策名称", "责任单位", "实施单位", "拟征收", "补偿方案"]
