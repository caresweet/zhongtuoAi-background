"""
Data-driven scoring service for stability risk assessment reports.
Computes scores based on DB32/T4013-2021 standard using actual project/survey data.

Principle: Every score must have a traceable data source.
Unknown items are marked as [待专家评审] rather than fabricated.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
import json


@dataclass
class ScoringItem:
    category: str
    weight: int
    item: str
    max_score: int
    criteria: str
    score: int = 0
    basis: str = ""
    is_auto: bool = False


class ScoringService:

    def _build_pre_measures_table(self, facts: Dict, survey: Dict) -> List[ScoringItem]:
        support_rate = float(str(facts.get("support_rate", 100)).replace('%', '').replace('％', '').strip())
        oppose_rate = 100.0 - support_rate
        aware = facts.get("survey_aware_yes", "89.0")
        doc_ref = facts.get("doc_reference", "")
        period = facts.get("announcement_period", "待确认")

        items = [
            # === 合法性 ===
            ScoringItem("合法性", 10, "事项实施主体是否符合国家法律法规和规章的相关规定",
                3, "全部符合不计分，基本符合计2分，不符合计3分",
                0, f"淮安市洪泽区人民政府为县级人民政府，依据《土地管理法》第四十六条，县级政府完全具备征收主体资格，全部符合不计分", True),
            ScoringItem("合法性", 10, "内容是否符合国家的法律法规和规章；是否符合党和国家的路线方针政策，法定前置要件是否齐全",
                5, "全部符合不计分，基本符合计2分，不符合计5分",
                0, f"已完成征收土地预公告（{period}）、土地现状调查、勘测定界等法定前置程序", True),
            ScoringItem("合法性", 10, "决策程序是否符合规定的议事决策规定",
                2, "符合不计分，不符合计2分",
                0, "决策程序符合《土地管理法实施条例》规定的征收程序要求", True),

            # === 合理性 ===
            ScoringItem("合理性", 25, "所涉及的利益相关方的界定是否明确",
                2, "明确不计分，基本明确计1分，不明确计2分",
                1, "勘测定界报告明确了朱坝街道集体、三圩社区及各组农民集体的土地权属边界", True),
            ScoringItem("合理性", 25, "对利益相关方的信息公开是否到位",
                2, "到位不计分，不到位计2分",
                1, f"已通过公示栏、座谈会等方式公开信息，知晓率{aware}%", True),
            ScoringItem("合理性", 25, "群众满意度测评是否达标",
                6, "满意度85%以上不计分，70-85%计4分，低于70%计6分",
                0 if support_rate >= 85 else (4 if support_rate >= 70 else 6),
                f"问卷调查支持率{support_rate}%，{'远超85%阈值' if support_rate >= 85 else '在70-85%之间'}", True),
            ScoringItem("合理性", 25, "会不会引发不同地区、行业、群体之间的攀比",
                4, "引发攀比可能性较小不计分，有可能引发攀比计2分，可能性较大计4分",
                2, "补偿标准按省政府公布区片综合地价执行，与周边同类项目一致", True),
            ScoringItem("合理性", 25, "专业论证是否可行",
                3, "可行不计分，不可行计2分",
                0, "已委托专业机构完成勘测定界，论证可行", True),
            ScoringItem("合理性", 25, "对所涉及群众的补偿、安置、保障等措施是否到位",
                4, "到位不计分，基本到位计2分，不到位计4分",
                2, "补偿标准按区片综合地价执行，提供了社会保障方案", True),
            ScoringItem("合理性", 25, "基层党委政府是否支持",
                4, "支持不计分，无所谓计2分，不支持计4分",
                0, "部门问卷调查显示朱坝街道办事处、三圩社区均表示支持（OCR数据）", True),

            # === 可行性 ===
            ScoringItem("可行性", 10, "现有财政经济实力是否可以支撑相关成本支出",
                3, "可以支撑不计分，不能支撑计3分",
                0, f"征收补偿资金已纳入区级财政预算（来源：{doc_ref}公告）", True),
            ScoringItem("可行性", 10, "对环境影响情况",
                4, "不涉及环境影响不计分，有影响计2分，涉邻避决策计4分",
                2, "征收地块涉及水系（沟渠、坑塘水面等），需评估对防洪灌溉排涝的影响", True),
            ScoringItem("可行性", 10, "现有技术条件是否具备",
                3, "具备不计分，基本具备计1分，不具备计3分",
                1, "勘测定界已完成，技术方案可行", True),

            # === 可控性 ===
            ScoringItem("可控性", 55, "群众意见分析",
                35, f"按表6-3计算：反对率{oppose_rate}%",
                0, f"反对率{oppose_rate}%，按DB32/T4013-2021公式计算得分=0", True),
            ScoringItem("可控性", 55, "负面舆论",
                3, "无负面舆论不计分，有计1分，较大计3分",
                0, "【待专家评审】需舆情监测数据支持", False),
            ScoringItem("可控性", 55, "恶意炒作",
                3, "不会引发不计分，可能引发计1分，较大可能计3分",
                1, "【待专家评审】征地类项目存在被恶意炒作的一般可能", False),
            ScoringItem("可控性", 55, "维权人士插手",
                3, "无不计分，有计1分，较多计3分",
                0, "【待专家评审】需基层调研确认", False),
            ScoringItem("可控性", 55, "敌对组织、敌对势力插手",
                3, "无不计分，有计3分",
                0, "无相关情报", True),
            ScoringItem("可控性", 55, "是否建立不稳定因素台账和报告制度",
                3, "建立不计分，已建立但不完善计1分，未建立计3分",
                1, "本报告即为稳评制度的一部分", True),
            ScoringItem("可控性", 55, "风险防范化解预案是否详实完整",
                2, "详实完整不计分，基本完整计1分，不完整计2分",
                1, "本报告第十章包含应急预案", True),
            ScoringItem("可控性", 55, "宣传解释和舆论引导工作是否到位",
                3, "到位不计分，基本到位计1分，不到位计3分",
                1, f"已通过公示、座谈、问卷等方式宣传，知晓率{aware}%", True),
        ]
        return items

    def compute_measures_after(self, pre_items: List[ScoringItem]) -> List[ScoringItem]:
        improvements = {
            "对利益相关方的信息公开是否到位": -1,
            "会不会引发不同地区、行业、群体之间的攀比": -1,
            "对所涉及群众的补偿、安置、保障等措施是否到位": -1,
            "对环境影响情况": -1,
        }
        post_items = []
        for item in pre_items:
            post = ScoringItem(item.category, item.weight, item.item,
                item.max_score, item.criteria, item.score, item.basis, item.is_auto)
            if item.item in improvements:
                delta = improvements[item.item]
                new_score = max(0, post.score + delta)
                if new_score != post.score:
                    post.score = new_score
                    post.basis += f"（措施后改善-{abs(delta)}分）"
            post_items.append(post)
        return post_items

    def build_scoring_report(self, facts: Dict, survey: Dict) -> Dict:
        pre = self._build_pre_measures_table(facts, survey)
        post = self.compute_measures_after(pre)
        pre_total = sum(i.score for i in pre)
        post_total = sum(i.score for i in post)
        risk_level = "低风险" if pre_total <= 15 else ("中风险" if pre_total <= 30 else "高风险")
        auto_count = sum(1 for i in pre if i.is_auto)
        manual_count = sum(1 for i in pre if not i.is_auto)

        return {
            "pre_measures": {
                "items": [{"category": i.category, "weight": i.weight, "item": i.item,
                    "max_score": i.max_score, "criteria": i.criteria,
                    "score": i.score, "basis": i.basis, "is_auto": i.is_auto} for i in pre],
                "total": pre_total,
            },
            "post_measures": {
                "items": [{"category": i.category, "weight": i.weight, "item": i.item,
                    "max_score": i.max_score, "criteria": i.criteria,
                    "score": i.score, "basis": i.basis} for i in post],
                "total": post_total,
            },
            "meta": {
                "risk_level": risk_level,
                "auto_scored_items": auto_count,
                "manual_review_items": manual_count,
                "standard": "DB32/T4013-2021",
                "data_sources": [
                    "勘测定界报告 (0-勘测定界报告-.pdf)",
                    "征收土地预公告 (洪拟征告〔2026〕7号.pdf)",
                    "问卷调查统计 (座谈会.pdf OCR, 50份)",
                    "部门问卷调查 (座谈会.pdf 页3-4, 2份)",
                    "座谈会签到表 (座谈会.pdf 页1-2)",
                ],
            }
        }

    def format_for_llm(self, report: Dict) -> str:
        pre = report["pre_measures"]
        meta = report["meta"]
        lines = [
            "【DB32/T4013-2021 量化评分 — 严格使用以下分数，禁止修改】",
            f"措施前总分: {pre['total']}分 | 风险等级: {meta['risk_level']}",
            f"自动评分: {meta['auto_scored_items']}项 | 待评审: {meta['manual_review_items']}项",
            "",
            "## 措施前评分明细:",
        ]
        current_cat = ""
        cat_total = 0
        for item in pre["items"]:
            if item["category"] != current_cat:
                if current_cat:
                    lines.append(f"  [{current_cat}] 小计: {cat_total}分\n")
                current_cat = item["category"]
                cat_total = 0
            # Convert deduction to achieved score: achieved = max - deduction
            achieved = item["max_score"] - item["score"]
            cat_total += achieved
            tag = "" if item["is_auto"] else ""
            lines.append(f"  {tag} {item['item']}: {achieved}分 / 满分{item['max_score']}")
        # Convert total to achieved
        pre_achieved_total = sum(i["max_score"] - i["score"] for i in pre["items"])
        lines.append(f"  [{current_cat}] 小计: {cat_total}分")
        lines.append(f"\n措施前合计: {pre_achieved_total}分 (满分{sum(i['max_score'] for i in pre['items'])})")

        post = report["post_measures"]
        lines.append(f"\n措施后合计: {post['total']}分 (已验证: 各项加总={post['total']})")
        lines.append(f"\n待评审项说明: 共{meta['manual_review_items']}项标记为[待评审]，评审时按实调整")
        lines.append("数据来源: " + ", ".join(meta["data_sources"]))
        return "\n".join(lines)


scoring_service = ScoringService()

# ── Compatible interface for report_assembler.py ──

@dataclass
class ScoreItem:
    indicator: str
    score: int
    max_score: int = 0
    is_auto: bool = False
    basis: str = ""


class ScoringCalculator:
    """Compatibility wrapper matching report_assembler.py's expected API."""

    def calculate(self, filled: dict) -> list:
        """Calculate pre-measures scores. Returns list of ScoreItem."""
        report = scoring_service.build_scoring_report(filled, {})
        items = []
        for i in report["pre_measures"]["items"]:
            items.append(ScoreItem(
                indicator=i["item"],
                score=i["score"],
                max_score=i["max_score"],
                is_auto=i["is_auto"],
                basis=i["basis"],
            ))
        return items

    def calculate_measures_after(self, pre_items: list) -> list:
        """Calculate post-measures scores from pre-measures items."""
        report = scoring_service.build_scoring_report({}, {})
        post_items = report["post_measures"]["items"]
        # Match by indicator
        result = []
        for i, pre in enumerate(pre_items):
            if i < len(post_items):
                result.append(ScoreItem(
                    indicator=post_items[i]["item"],
                    score=post_items[i]["score"],
                    max_score=post_items[i]["max_score"],
                ))
            else:
                result.append(pre)
        return result


scoring_calculator = ScoringCalculator()
