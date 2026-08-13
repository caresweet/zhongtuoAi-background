"""CrossReferenceAgent — 跨章节一致性校验Agent

职责：
1. 在所有章节生成完成后，校验各章节之间的数据一致性
2. 检查关键数据在全文中是否统一（项目名、面积、金额、日期、评分等）
3. 检查逻辑关系（措施后得分 < 措施前得分、支持率与风险等级匹配等）
4. 检查术语一致性（同一概念用词统一）
5. 生成一致性报告，标注矛盾之处

在 ChapterOrchestrator 的 _run_quality_review 阶段调用。
"""

import asyncio
import logging
import re
from typing import Dict, List, Any, Optional, Tuple

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class CrossReferenceAgent(BaseAgent):
    """跨章节一致性校验Agent"""

    name = "CrossReferenceAgent"
    description = "校验全文10章之间的数据一致性、逻辑关系和术语统一性"
    covered_steps = []

    async def think(self, state: dict) -> Dict[str, Any]:
        """收集所有章节的已生成内容"""
        chapters = state.get("chapters", {})
        generated_chapters = {
            ch_num: ch_data.get("markdown", "")
            for ch_num, ch_data in chapters.items()
            if ch_data.get("markdown")
        }

        total_chars = sum(len(md) for md in generated_chapters.values())

        return {
            "summary": f"CrossReferenceAgent: 校验 {len(generated_chapters)} 个章节的一致性（共{total_chars}字）",
            "steps": [
                f"📖 已生成章节: {sorted(generated_chapters.keys())}",
                f"📝 总字数: {total_chars}",
            ],
            "generated_chapters": generated_chapters,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """执行四类一致性校验"""
        chapters = plan.get("generated_chapters", {})
        all_text = "\n\n".join(chapters.values())

        # 1. 数据一致性
        data_issues = self._check_data_consistency(chapters, state)

        # 2. 逻辑关系
        logic_issues = self._check_logic_relations(chapters, state)

        # 3. 术语一致性
        term_issues = self._check_terminology(chapters)

        # 4. 🔴 NEW: 数据源一致性 — 报告数据 vs 用户填报数据
        source_issues = self._check_data_source_consistency(chapters, state)

        all_issues = data_issues + logic_issues + term_issues + source_issues
        consistency_score = max(0, 100 - len(all_issues) * 5)

        return {
            "status": "completed",
            "consistency_score": consistency_score,
            "data_issues": data_issues,
            "logic_issues": logic_issues,
            "term_issues": term_issues,
            "source_issues": source_issues,
            "total_issues": len(all_issues),
        }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        return []

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """将一致性校验结果写入state"""
        state["_cross_reference"] = {
            "consistency_score": result.get("consistency_score", 100),
            "data_issues": result.get("data_issues", []),
            "logic_issues": result.get("logic_issues", []),
            "term_issues": result.get("term_issues", []),
            "total_issues": result.get("total_issues", 0),
        }
        return state

    # ═══════════════════════════════════════════════════════════════
    # 数据一致性校验
    # ═══════════════════════════════════════════════════════════════

    def _check_data_consistency(
        self, chapters: Dict[int, str], state: dict
    ) -> List[Dict[str, Any]]:
        """检查关键数据在全文中的一致性"""
        issues = []

        # 提取各章节中的面积数据
        area_values = {}
        for ch_num, text in chapters.items():
            # 匹配 XX公顷 或 XX亩
            hectares = re.findall(r'(\d+\.?\d*)\s*公顷', text)
            mu = re.findall(r'(\d+\.?\d*)\s*亩', text)
            if hectares or mu:
                area_values[ch_num] = {
                    "hectares": set(hectares),
                    "mu": set(mu),
                }

        # 比较面积数据
        all_hectares = set()
        all_mu = set()
        for vals in area_values.values():
            all_hectares.update(vals.get("hectares", set()))
            all_mu.update(vals.get("mu", set()))

        if len(all_hectares) > 1:
            issues.append({
                "type": "inconsistent_area",
                "severity": "error",
                "detail": f"面积（公顷）在全文中出现多个值: {all_hectares}",
                "chapters": list(area_values.keys()),
            })

        if len(all_mu) > 1:
            # 排除合理的四舍五入差异
            mu_floats = [float(v) for v in all_mu]
            if max(mu_floats) - min(mu_floats) > 1:
                issues.append({
                    "type": "inconsistent_area_mu",
                    "severity": "error",
                    "detail": f"面积（亩）在全文中出现多个值: {all_mu}",
                    "chapters": list(area_values.keys()),
                })

        # 提取项目名称
        title = state.get("report_title", "")
        project_names = set()
        for ch_num, text in chapters.items():
            # 提取「XX项目」
            names = re.findall(r'[「「]([^」」]*?(?:项目|地块|片区)[^」」]*)[」」]', text)
            project_names.update(names)

        if len(project_names) > 3:
            issues.append({
                "type": "inconsistent_project_name",
                "severity": "warning",
                "detail": f"项目名称在全文中有多个表述: {list(project_names)[:5]}",
            })

        # 提取责任单位名称
        org_names = set()
        for ch_num, text in chapters.items():
            orgs = re.findall(r'(\S{4,20}(?:人民政府|街道办事处|管理委员会))', text)
            org_names.update(orgs)

        if len(org_names) > 2:
            issues.append({
                "type": "multiple_orgs",
                "severity": "info",
                "detail": f"全文出现多个责任单位名称: {list(org_names)[:5]}",
            })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # 逻辑关系校验
    # ═══════════════════════════════════════════════════════════════

    def _check_logic_relations(
        self, chapters: Dict[int, str], state: dict
    ) -> List[Dict[str, Any]]:
        """检查章节间的逻辑关系"""
        issues = []

        # 1. 措施前 vs 措施后得分
        ch6_text = chapters.get(6, "")
        ch8_text = chapters.get(8, "")

        pre_score = self._extract_risk_score(ch6_text, "措施前")
        post_score = self._extract_risk_score(ch8_text, "措施后")

        if pre_score and post_score:
            if post_score >= pre_score:
                issues.append({
                    "type": "logic_score",
                    "severity": "error",
                    "detail": f"措施后得分({post_score})应低于措施前得分({pre_score})",
                    "chapters": [6, 8],
                })
            elif pre_score - post_score < 2:
                issues.append({
                    "type": "logic_score_gap",
                    "severity": "warning",
                    "detail": f"措施前后分差过小（{pre_score - post_score}分），建议体现更明显的措施效果",
                    "chapters": [6, 8],
                })

        # 2. 风险等级一致性
        risk_levels = {}
        for ch_num in (6, 8, 9):
            text = chapters.get(ch_num, "")
            level = self._extract_risk_level(text)
            if level:
                risk_levels[ch_num] = level

        unique_levels = set(risk_levels.values())
        if len(unique_levels) > 1:
            issues.append({
                "type": "inconsistent_risk_level",
                "severity": "error",
                "detail": f"风险等级不一致: {risk_levels}",
                "chapters": list(risk_levels.keys()),
            })

        # 3. 支持率与反对率关系
        ch3_text = chapters.get(3, "")
        support = self._extract_percentage(ch3_text, "支持")
        opposition = self._extract_percentage(ch3_text, "反对")
        if support and opposition:
            if support + opposition > 100:
                issues.append({
                    "type": "logic_percentage",
                    "severity": "warning",
                    "detail": f"支持率({support}%) + 反对率({opposition}%) 超过100%",
                    "chapters": [3],
                })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # 术语一致性校验
    # ═══════════════════════════════════════════════════════════════

    def _check_terminology(self, chapters: Dict[int, str]) -> List[Dict[str, Any]]:
        """检查术语用词的统一性"""
        issues = []
        all_text = "\n".join(chapters.values())

        # 检查同义异词
        term_pairs = [
            ("稳评", "社会稳定风险评估"),
            ("拟征收", "预征收"),
            ("征收土地", "征地"),
        ]
        for t1, t2 in term_pairs:
            c1 = all_text.count(t1)
            c2 = all_text.count(t2)
            if c1 > 0 and c2 > 0:
                # 两者都出现不算错，但提示统一
                if min(c1, c2) > 3:
                    issues.append({
                        "type": "terminology",
                        "severity": "info",
                        "detail": f"「{t1}」({c1}次)和「{t2}」({c2}次)混用，建议统一",
                    })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # 🔴 NEW: 数据源一致性校验 — 报告数据 vs 用户填报数据
    # ═══════════════════════════════════════════════════════════════

    def _check_data_source_consistency(
        self, chapters: Dict[int, str], state: dict
    ) -> List[Dict[str, Any]]:
        """Compare report values against user-provided filled_data.

        If filled_data specifies area=489.51亩 but the report says 500亩 in a chapter,
        flag as inconsistent. This catches the LLM making up its own numbers instead
        of using the provided data.
        """
        issues = []
        filled = state.get("filled_data", {}) or {}
        if not filled:
            return issues

        # Map of filled_data keys → extract patterns in report text → display label
        checks = [
            # (filled_key, report_regex, label, tolerance_pct)
            ("area_mu", r'(\d+\.?\d*)\s*亩', "征收面积（亩）", 1.0),
            ("area_m2", r'(\d+\.?\d*)\s*(?:㎡|平方米)', "征收面积（㎡）", 1.0),
            ("household_count", r'(\d+)\s*(?:户|农户)', "涉及户数", 0),
            ("total_samples", r'(?:发放|回收|问卷|调查).*?(\d+)\s*(?:份|人)', "调查样本数", 5.0),
            ("support_rate", r'支持率[：:]?\s*(\d+\.?\d*)\s*%', "支持率", 0),
        ]

        for field_key, pattern, label, tolerance_pct in checks:
            user_val = filled.get(field_key)
            if not user_val:
                continue

            # Extract user's numeric value
            user_nums = re.findall(r'\d+\.?\d*', str(user_val))
            if not user_nums:
                continue
            try:
                user_num = float(user_nums[0])
            except ValueError:
                continue

            # Scan each chapter for this field's value
            for ch_num, text in chapters.items():
                for m in re.finditer(pattern, text):
                    try:
                        report_num = float(m.group(1))
                    except ValueError:
                        continue

                    # Skip if very small (likely not the main value)
                    if field_key in ("area_mu", "area_m2") and report_num < 0.1:
                        continue
                    if field_key == "household_count" and report_num < 1:
                        continue

                    # Check consistency with tolerance
                    if user_num > 0 and tolerance_pct == 0:
                        # Exact match required
                        if report_num != user_num:
                            issues.append({
                                "type": "source_inconsistency",
                                "severity": "critical",
                                "chapter": ch_num,
                                "detail": (
                                    f"第{ch_num}章{label}值({report_num})与用户填报数据"
                                    f"({user_num})不一致，请使用用户提供的真实数据"
                                ),
                            })
                    elif user_num > 0:
                        # Percentage tolerance
                        deviation = abs(report_num - user_num) / user_num * 100
                        if deviation > tolerance_pct:
                            issues.append({
                                "type": "source_inconsistency",
                                "severity": "critical" if deviation > 10 else "error",
                                "chapter": ch_num,
                                "detail": (
                                    f"第{ch_num}章{label}值({report_num})与用户填报数据"
                                    f"({user_num})偏差{deviation:.1f}%，"
                                    f"请使用用户提供的真实数据"
                                ),
                            })

        # Check compensation standard consistency
        comp_val = filled.get("compensation_standard", "")
        if comp_val and len(str(comp_val)) > 3:
            # Extract numbers from compensation standard
            comp_nums = re.findall(r'\d+\.?\d*', str(comp_val))
            if comp_nums:
                try:
                    comp_ref = float(comp_nums[0])
                except ValueError:
                    comp_ref = None

                if comp_ref and comp_ref > 0:
                    for ch_num, text in chapters.items():
                        # Find compensation amounts in chapter text
                        for m in re.finditer(r'(\d+\.?\d*)\s*(?:万元/亩|元/㎡|元/亩)', text):
                            try:
                                report_comp = float(m.group(1))
                            except ValueError:
                                continue
                            # Allow 20% variance for approximate mentions
                            if report_comp > 0 and comp_ref > 0:
                                deviation = abs(report_comp - comp_ref) / comp_ref * 100
                                if deviation > 20:
                                    issues.append({
                                        "type": "source_inconsistency",
                                        "severity": "error",
                                        "chapter": ch_num,
                                        "detail": (
                                            f"第{ch_num}章补偿标准({report_comp})与用户填报"
                                            f"({comp_ref})偏差{deviation:.0f}%，请核实"
                                        ),
                                    })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # 辅助提取方法
    # ═══════════════════════════════════════════════════════════════

    def _extract_risk_score(self, text: str, prefix: str) -> Optional[int]:
        """从文本中提取风险得分"""
        patterns = [
            rf'{prefix}.*?(\d{{1,2}})\s*分',
            rf'(\d{{1,2}})\s*分.*?{prefix}',
            rf'总分.*?(\d{{1,2}})',
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                try:
                    return int(m.group(1))
                except (ValueError, IndexError):
                    continue
        return None

    def _extract_risk_level(self, text: str) -> Optional[str]:
        """从文本中提取风险等级"""
        for level in ("低风险", "中风险", "高风险"):
            if level in text:
                return level
        return None

    def _extract_percentage(self, text: str, keyword: str) -> Optional[float]:
        """从文本中提取百分比"""
        pattern = rf'(\d{{1,3}}\.?\d*)\s*%\s*(?:的)?{keyword}'
        m = re.search(pattern, text)
        if not m:
            pattern = rf'{keyword}.*?(\d{{1,3}}\.?\d*)\s*%'
            m = re.search(pattern, text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                pass
        return None
