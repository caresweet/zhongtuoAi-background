"""Product Skill Integration — Requirement Gathering & Field Classification.

Maps the zhongtuo-report-product skill into the backend workflow.
Classifies template fields into A/B/C/D categories and generates
interactive questions to collect missing data from users.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
import json


class FieldCategory(str, Enum):
    """A/B/C/D field classification from the product skill."""
    A = "A"  # 必填基础信息（可自动填充）
    B = "B"  # 必填项目数据（用户提供）
    C = "C"  # 需AI生成的分析内容
    D = "D"  # 保留模板原文（不修改）
    E = "E"  # 辅助文件（图片/附件）


@dataclass
class FieldDefinition:
    """A single field that needs to be collected."""
    key: str
    category: FieldCategory
    display_name: str
    description: str
    expected_type: str  # "text", "number", "choice", "image", "ai"
    section_title: str
    examples: List[str] = field(default_factory=list)
    is_filled: bool = False
    filled_value: str = ""


@dataclass
class CollectionProgress:
    """Tracks the progress of field collection."""
    total: int = 0
    filled: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    current_question_index: int = 0


class ProductIntegration:
    """Integrates the product skill's requirement-gathering logic.

    Analyzes a template, classifies fields into A/B/C/D categories,
    and generates an interactive Q&A flow to collect missing data.
    """

    # ── Field classification rules ──

    CATEGORY_RULES = {
        FieldCategory.A: {
            "patterns": ["decision_name", "report_title", "project_name",
                        "评估单位", "委托单位", "稳评责任单位"],
            "description": "A类-必填基础信息：可从标题自动派生的基本信息",
        },
        FieldCategory.B: {
            "patterns": ["地块", "面积", "公顷", "征地用途", "土地权属",
                        "补偿标准", "征收", "安置", "土地性质",
                        "num_plots", "area_hectares", "area_mu",
                        "land_use", "land_type", "responsibility_unit"],
            "description": "B类-必填项目数据：用户需要提供的具体项目参数",
        },
        FieldCategory.C: {
            "patterns": ["合法性", "合理性", "可行性", "可控性",
                        "风险识别", "风险等级", "防范措施",
                        "调查分析", "评估结论", "应急预案",
                        "问卷调查", "座谈会", "公示",
                        "legality", "rationality", "feasibility", "controllability"],
            "description": "C类-AI生成内容：需要LLM推理生成的分析章节",
        },
        FieldCategory.D: {
            "patterns": ["营业执照", "稳评平台备案", "人员证书",
                        "工作组人员", "审核", "机构资质", "签章"],
            "description": "D类-保留原文：模板中不应被修改的内容",
        },
        FieldCategory.E: {
            "patterns": ["照片", "图片", "位置图", "勘测定界", "公示栏",
                        "photo", "image", "attachment"],
            "description": "E类-辅助文件：需要用户上传的图片或附件",
        },
    }

    # ── Pre-built question flow for the 稳评报告 ──

    FIELDS: List[FieldDefinition] = []

    @classmethod
    def classify_field(cls, key: str, content: str = "") -> FieldCategory:
        """Classify a single field into A/B/C/D/E category based on key name."""
        key_lower = key.lower()
        full_text = f"{key} {content}".lower()

        for cat, rule in cls.CATEGORY_RULES.items():
            for pattern in rule["patterns"]:
                if pattern.lower() in key_lower or pattern.lower() in full_text:
                    return cat
        return FieldCategory.B  # Default: user-provided data

    @classmethod
    def analyze_template(cls, template_path: str) -> Dict[str, Any]:
        """Analyze a .docx template and return classified fields.

        Returns:
            {
                "fields": [...classified field dicts...],
                "summary": {
                    "A": count, "B": count, "C": count, "D": count, "E": count,
                },
                "questions": [...interactive questions...],
            }
        """
        try:
            from app.integration.template_parser import analyze_template
            parsed = analyze_template(template_path)
            fields_info = parsed.get("fields", parsed)
        except Exception:
            fields_info = {"basic_info": [], "survey_data": [], "ai_sections": [], "attachments": []}

        fields = []
        # Map the structured fields to the flat classified format
        for cat_key, cat_name, category in [
            ("basic_info", "基本信息", FieldCategory.A),
            ("survey_data", "调查数据", FieldCategory.B),
            ("ai_sections", "AI生成章节", FieldCategory.C),
            ("attachments", "附件", FieldCategory.E),
        ]:
            items = fields_info.get(cat_key, [])
            if isinstance(items, dict):
                items = list(items.values())
            if isinstance(items, str):
                items = [items]
            for item in items:
                if isinstance(item, dict):
                    key = item.get("key", item.get("name", ""))
                    text = item.get("text", item.get("content", item.get("description", "")))
                elif isinstance(item, str):
                    key = item
                    text = ""
                else:
                    continue

                fields.append({
                    "key": key,
                    "category": category.value,
                    "display_name": key,
                    "description": text[:100] if text else "",
                    "expected_type": cls._guess_type(key, category),
                    "section_title": cat_name,
                    "examples": cls._get_examples(key, category),
                })

        # Category summary
        summary = {c.value: 0 for c in FieldCategory}
        for f in fields:
            summary[f["category"]] += 1

        questions = cls._build_question_flow(fields)

        return {
            "fields": fields,
            "summary": summary,
            "questions": questions,
            "total_fields": len(fields),
            "auto_fillable": summary.get("A", 0),
            "ai_generated": summary.get("C", 0),
            "needs_user_input": summary.get("B", 0) + summary.get("E", 0),
        }

    @classmethod
    def build_next_question(cls, fields: List[dict], filled: Dict[str, str]) -> Optional[dict]:
        """Get the next unfilled B/E field as a question."""
        for f in fields:
            if f["category"] in ("B", "E") and f["key"] not in filled:
                return {
                    "key": f["key"],
                    "display_name": f["display_name"],
                    "expected_type": f["expected_type"],
                    "section_title": f["section_title"],
                    "description": f["description"],
                    "examples": f.get("examples", []),
                    "remaining": sum(1 for x in fields
                                    if x["category"] in ("B", "E")
                                    and x["key"] not in filled),
                }
        return None

    @classmethod
    def get_progress(cls, fields: List[dict], filled: Dict[str, str]) -> CollectionProgress:
        """Calculate collection progress."""
        b_e = [f for f in fields if f["category"] in ("B", "E")]
        total = len(b_e)
        _filled = sum(1 for f in b_e if f["key"] in filled)

        by_cat = {}
        for cat in FieldCategory:
            cat_fields = [f for f in fields if f["category"] == cat.value]
            by_cat[cat.value] = {
                "total": len(cat_fields),
                "filled": sum(1 for f in cat_fields if f["key"] in filled),
            }

        return CollectionProgress(
            total=total,
            filled=_filled,
            by_category=by_cat,
            current_question_index=_filled,
        )

    # ── Internal helpers ──

    @staticmethod
    def _guess_type(key: str, category: FieldCategory) -> str:
        """Guess field type from key name."""
        if category == FieldCategory.E:
            return "image"
        if any(k in key for k in ["面积", "亩", "公顷", "金额"]):
            return "number"
        if any(k in key for k in ["用途", "性质", "类型"]):
            return "choice"
        if category == FieldCategory.C:
            return "ai"
        return "text"

    @staticmethod
    def _get_examples(key: str, category: FieldCategory) -> List[str]:
        """Provide example values for common fields."""
        examples = {
            "decision_name": ["金征预告〔2026〕XX号（XX项目）土地征收决策"],
            "responsibility_unit": ["金湖县XX街道办事处", "金湖县自然资源和规划局"],
            "area_mu": ["150亩", "200.5亩"],
            "area_hectares": ["10.0公顷", "13.37公顷"],
            "land_use": ["成片开发建设", "交通基础设施建设"],
            "land_type": ["农用地（含耕地、林地）", "建设用地"],
            "location_community": ["XX镇XX村农民集体", "XX街道XX社区"],
            "num_plots": ["1个", "3个"],
        }
        return examples.get(key, [])

    @staticmethod
    def _build_question_flow(fields: List[dict]) -> List[dict]:
        """Build an interactive question flow from classified fields."""
        questions = []
        for f in fields:
            if f["category"] in ("B", "E"):
                q = {
                    "key": f["key"],
                    "display_name": f["display_name"],
                    "question": f"请提供「{f['display_name']}」（{f['section_title']}）",
                    "hint": f["description"] if f["description"] else "",
                    "expected_type": f["expected_type"],
                    "examples": f.get("examples", []),
                }
                questions.append(q)
        return questions


# Singleton
product_integration = ProductIntegration()
