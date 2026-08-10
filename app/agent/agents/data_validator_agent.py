"""DataValidatorAgent — 数据完整性校验与缺口分析Agent

职责：
1. 在章节生成前，校验用户提供的数据是否满足当前章节的生成需求
2. 对每个章节列出缺失数据清单，生成「数据缺口报告」
3. 区分「必填缺失」和「可选缺失」，决定生成策略
4. 在多Agent协同中充当「数据守门人」，避免生成低质量内容

调用时机：ChapterOrchestrator 在生成每章前调用，决定是否可以开始生成。
"""

import asyncio
import logging
import re
from typing import Dict, List, Any, Optional, Tuple

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 每章必填/可选字段定义
# ═══════════════════════════════════════════════════════════════

CHAPTER_DATA_REQUIREMENTS: Dict[int, Dict[str, Any]] = {
    1: {
        "required": [
            ("report_title", "报告标题（决策名称）", "critical"),
            ("location", "拟征地位置（街道/社区/村组）", "critical"),
        ],
        "recommended": [
            ("org_name", "稳评责任单位名称"),
            ("area_m2", "征收面积（平方米/公顷）"),
            ("area_mu", "征收面积（亩）"),
            ("land_use", "土地用途"),
            ("funding", "资金测算"),
        ],
        "optional": [
            ("household_count", "涉及户数"),
            ("compensation_standard", "补偿标准"),
            ("doc_reference", "公告文号"),
        ],
    },
    2: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "稳评责任单位"),
            ("commission_month", "委托日期"),
        ],
        "optional": [
            ("survey_start", "调查开始日期"),
            ("survey_end", "调查结束日期"),
        ],
    },
    3: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("total_samples", "调查样本总数"),
            ("support_rate", "群众支持率"),
            ("survey_start", "调查开始日期"),
            ("survey_end", "调查结束日期"),
        ],
        "optional": [
            ("awareness_rate", "知晓率"),
            ("grassroots_opinion", "基层组织意见"),
            ("villager_demands", "村民诉求"),
            ("online_opinion", "网络舆情"),
        ],
    },
    4: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位"),
            ("location", "项目位置"),
            ("land_use", "土地用途"),
        ],
        "optional": [
            ("support_rate", "支持率"),
            ("funding", "资金来源"),
        ],
    },
    5: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("support_rate", "群众支持率"),
        ],
        "optional": [
            ("compensation_standard", "补偿标准"),
            ("funding", "资金测算"),
        ],
    },
    6: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("support_rate", "群众支持率（影响打分）"),
        ],
        "optional": [],
    },
    7: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位（措施责任主体）"),
        ],
        "optional": [],
    },
    8: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [],
        "optional": [],
        "depends_on_chapters": [6],  # 需要第6章措施前得分
    },
    9: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [],
        "optional": [],
        "depends_on_chapters": [4, 5, 6, 8],  # 需要综合分析+风险+评分
    },
    10: {
        "required": [
            ("report_title", "报告标题", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位（应急指挥部名称）"),
        ],
        "optional": [],
    },
}


class DataValidatorAgent(BaseAgent):
    """数据完整性校验与缺口分析Agent"""

    name = "DataValidatorAgent"
    description = "校验每章所需数据的完整性，区分必填/可选缺失，生成数据缺口报告"
    covered_steps = []

    async def think(self, state: dict) -> Dict[str, Any]:
        """分析当前章节的数据完整性状态"""
        current_chapter = state.get("current_chapter", 1)
        requirements = CHAPTER_DATA_REQUIREMENTS.get(current_chapter, {})

        user_data = self._collect_all_data(state)
        missing_required = self._check_fields(user_data, requirements.get("required", []))
        missing_recommended = self._check_fields(user_data, requirements.get("recommended", []))
        missing_optional = self._check_fields(user_data, requirements.get("optional", []))

        # 检查依赖章节是否已生成
        depends = requirements.get("depends_on_chapters", [])
        missing_deps = [
            ch for ch in depends
            if state.get("chapters", {}).get(ch, {}).get("status") not in ("approved", "review", "generated")
        ]

        can_generate = len(missing_required) == 0
        quality_score = self._calc_quality_score(
            requirements, missing_required, missing_recommended, missing_optional
        )

        steps = [
            f"📊 第{current_chapter}章数据校验",
            f"✅ 必填字段: {len(requirements.get('required', [])) - len(missing_required)}/{len(requirements.get('required', []))} 已提供",
        ]
        if missing_recommended:
            steps.append(f"⚠️ 推荐字段缺失: {len(missing_recommended)} 项（可用【待补充】）")
        if missing_deps:
            steps.append(f"🔗 依赖章节未就绪: {missing_deps}")

        return {
            "summary": f"DataValidatorAgent: 第{current_chapter}章数据{'完整' if can_generate else '不完整'}（{quality_score}分）",
            "steps": steps,
            "can_generate": can_generate,
            "quality_score": quality_score,
            "missing_required": missing_required,
            "missing_recommended": missing_recommended,
            "missing_optional": missing_optional,
            "missing_deps": missing_deps,
            "user_data_keys": list(user_data.keys()),
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """生成数据缺口报告，决定生成策略"""
        current_chapter = state.get("current_chapter", 1)

        if not plan.get("can_generate", False):
            # 必填数据缺失，生成缺失清单
            return {
                "status": "missing_critical",
                "chapter": current_chapter,
                "missing_items": plan.get("missing_required", []),
                "recommended_missing": plan.get("missing_recommended", []),
                "quality_score": plan.get("quality_score", 0),
                "recommendation": "请补充以下必填数据后再生成",
            }

        # 数据充足，可以生成
        return {
            "status": "ready",
            "chapter": current_chapter,
            "quality_score": plan.get("quality_score", 0),
            "missing_recommended": plan.get("missing_recommended", []),
            "missing_optional": plan.get("missing_optional", []),
            "recommendation": "数据充足，可以生成（部分字段将标注【待补充】）",
        }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        issues = []
        if result.get("status") == "missing_critical" and not result.get("missing_items"):
            issues.append("标记为缺失但未列出具体缺失项")
        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """将数据校验结果写入state"""
        chapter = result.get("chapter", state.get("current_chapter", 1))
        validation_state = state.setdefault("_data_validation", {})
        validation_state[chapter] = {
            "status": result.get("status"),
            "quality_score": result.get("quality_score", 0),
            "missing_recommended": result.get("missing_recommended", []),
            "missing_optional": result.get("missing_optional", []),
        }

        # 如果数据缺失，记录到 missing_data_requests
        if result.get("status") == "missing_critical":
            missing_req = state.setdefault("missing_data_requests", {})
            missing_req[chapter] = [
                item.get("display", item) if isinstance(item, dict) else str(item)
                for item in result.get("missing_items", [])
            ]

        state["_data_validation"] = validation_state
        return state

    # ═══════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════

    def _collect_all_data(self, state: dict) -> Dict[str, Any]:
        """从state的所有数据源汇总用户数据"""
        data = {}

        # filled_data
        for k, v in state.get("filled_data", {}).items():
            if v and not str(v).startswith(("待", "【", "需")):
                data[k] = v

        # structured_data
        for step_key, step_data in state.get("structured_data", {}).items():
            if isinstance(step_data, dict):
                for k, v in step_data.items():
                    if v and k not in ("images", "attachments", "_files_processed"):
                        data[k] = v

        # report_title
        title = state.get("report_title", "")
        if title and title != "社会稳定风险评估报告":
            data["report_title"] = title

        # generated_sections (for dependency checks)
        generated = state.get("generated_sections", {})
        if generated:
            data["_generated_chapters"] = list(generated.keys())

        return data

    def _check_fields(
        self, user_data: Dict, field_defs: List
    ) -> List[Dict[str, str]]:
        """检查字段是否已提供，返回缺失列表"""
        missing = []
        for field_def in field_defs:
            if isinstance(field_def, tuple):
                key = field_def[0]
                display = field_def[1] if len(field_def) > 1 else key
                level = field_def[2] if len(field_def) > 2 else "optional"
            else:
                key = str(field_def)
                display = key
                level = "optional"

            value = user_data.get(key, "")
            if not value or (isinstance(value, str) and not value.strip()):
                missing.append({"key": key, "display": display, "level": level})

        return missing

    def _calc_quality_score(
        self,
        requirements: Dict,
        missing_required: List,
        missing_recommended: List,
        missing_optional: List,
    ) -> int:
        """计算数据完整度得分（0-100）"""
        total_required = len(requirements.get("required", []))
        total_recommended = len(requirements.get("recommended", []))
        total_optional = len(requirements.get("optional", []))

        score = 100
        if total_required > 0:
            score -= int((len(missing_required) / total_required) * 50)
        if total_recommended > 0:
            score -= int((len(missing_recommended) / total_recommended) * 30)
        if total_optional > 0:
            score -= int((len(missing_optional) / total_optional) * 20)

        return max(0, min(100, score))
