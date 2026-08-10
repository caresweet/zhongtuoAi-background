"""Learn image placement patterns from templates.

Analyzes template DOCX files to identify:
1. Where images are placed (chapter/section context)
2. What types of images go in each position
3. Standard image counts per report section
4. Image size/dimension expectations

Used by the report generator to auto-place images in the correct positions.
"""

import json, re
from typing import Dict, List, Optional, Any
from pathlib import Path


# ── Standard image position map (learned from template analysis) ────────────

# This is the canonical image-placement reference for 稳评 reports.
# Derived from DB32/T4013-2021 section 6.1 (附件规范) + template inspection.

STANDARD_IMAGE_POSITIONS = {
    1: {  # 第一章 拟征收决策基本概况
        "chapter_title": "拟征收决策基本概况",
        "expected_images": [
            {"type": "red_line_map", "label": "征地红线图", "section": "1.7 决策征地红线图",
             "placement": "决策地理位置之后", "required": True, "count": 1,
             "tips": "标注四至范围、征收地块边界"},
            {"type": "survey_map", "label": "勘测定界图", "section": "1.5 决策用地范围",
             "placement": "地块面积描述之后", "required": True, "count": 1,
             "tips": "标注地类面积、界址点坐标"},
        ],
    },
    3: {  # 第三章 社会稳定风险因素调查
        "chapter_title": "社会稳定风险因素调查",
        "expected_images": [
            {"type": "bulletin_photo", "label": "公示照片(远景+近景)", "section": "3.4 风险调查过程",
             "placement": "公示期描述之后", "required": True, "count": 2,
             "tips": "村委会/社区公示栏张贴公告的照片，远景1张+近景1张"},
            {"type": "site_photo", "label": "现场勘查照片", "section": "3.4 风险调查过程",
             "placement": "现场勘查描述之后", "required": True, "count": "2-4",
             "tips": "展示地块现状、地形地貌、地上附着物"},
            {"type": "meeting_photo", "label": "座谈会照片", "section": "3.4 风险调查过程",
             "placement": "座谈会描述之后", "required": False, "count": "1-2",
             "tips": "群众座谈会现场，包含参会人员和横幅"},
            {"type": "survey_form", "label": "问卷样表", "section": "3.1 问卷调查结果",
             "placement": "问卷调查统计之后", "required": True, "count": 1,
             "tips": "空白问卷模板，展示调查问题"},
            {"type": "signin_sheet", "label": "座谈会签到表", "section": "3.4 风险调查过程",
             "placement": "座谈会描述之后", "required": True, "count": 1,
             "tips": "参会群众签到记录"},
        ],
    },
    12: {  # 第十二章 附件
        "chapter_title": "附件",
        "expected_images": [
            # 附件一
            {"type": "business_license", "label": "营业执照", "section": "附件一：公司资质",
             "placement": "附件一第一部分", "required": True, "count": 1},
            {"type": "qualification_cert", "label": "资质证书", "section": "附件一：公司资质",
             "placement": "营业执照之后", "required": True, "count": 1},
            {"type": "personnel_cert", "label": "人员培训证书", "section": "附件一：公司资质",
             "placement": "资质证书之后", "required": True, "count": "≥3",
             "tips": "至少3名稳评培训合格人员"},
            {"type": "social_security", "label": "社保纳税证明", "section": "附件一：公司资质",
             "placement": "人员证书之后", "required": False, "count": 1},
            {"type": "financial_report", "label": "财务报告", "section": "附件一：公司资质",
             "placement": "社保纳税之后", "required": False, "count": 1},
            # 附件二
            {"type": "pre_announcement", "label": "征收预公告(盖章)", "section": "附件二：决策材料",
             "placement": "附件二开始", "required": True, "count": 1},
            {"type": "red_line_map", "label": "征地红线图", "section": "附件二：决策材料",
             "placement": "预公告之后", "required": True, "count": 1},
            {"type": "land_use_map", "label": "土地利用现状图/规划图", "section": "附件二：决策材料",
             "placement": "红线图之后", "required": False, "count": "1-2"},
            # 附件三
            {"type": "signin_sheet", "label": "座谈会签到表", "section": "附件三：调查材料",
             "placement": "附件三开始", "required": True, "count": 1},
            {"type": "meeting_photo", "label": "座谈会照片", "section": "附件三：调查材料",
             "placement": "签到表之后", "required": True, "count": "1-2"},
            {"type": "survey_form", "label": "问卷样表", "section": "附件三：调查材料",
             "placement": "座谈会照片之后", "required": True, "count": 1},
            # 附件四
            {"type": "expert_review", "label": "专家评审意见", "section": "附件四：专家评审",
             "placement": "附件四开始", "required": True, "count": 1},
            {"type": "signin_sheet", "label": "专家签到表", "section": "附件四：专家评审",
             "placement": "评审意见之后", "required": True, "count": 1},
            {"type": "expert_meeting_photo", "label": "专家评审会照片", "section": "附件四：专家评审",
             "placement": "签到表之后", "required": False, "count": "1-2"},
            {"type": "evaluation_form", "label": "稳评评审表", "section": "附件四：专家评审",
             "placement": "照片之后", "required": True, "count": 1,
             "tips": "江苏省稳评工作规范化运作统一制式评审表"},
        ],
    },
}


class TemplateImageLearner:
    """Learn and apply image placement patterns from templates."""

    def __init__(self):
        self.positions = STANDARD_IMAGE_POSITIONS

    def get_chapter_images(self, chapter: int) -> List[Dict]:
        """Get expected images for a specific chapter."""
        return self.positions.get(chapter, {}).get("expected_images", [])

    def get_required_images(self, chapter: int) -> List[Dict]:
        """Get only required images for a chapter."""
        return [img for img in self.get_chapter_images(chapter) if img.get("required")]

    def get_all_required_types(self) -> List[str]:
        """Get all required image types across the entire report."""
        types = []
        for ch, info in self.positions.items():
            for img in info["expected_images"]:
                if img.get("required"):
                    types.append(img["type"])
        return list(set(types))  # dedup

    def validate_image_set(self, available_types: List[str]) -> Dict:
        """Check if available images cover all required types. Returns gaps."""
        required = self.get_all_required_types()
        available_set = set(available_types)
        missing = [t for t in required if t not in available_set]
        extra = [t for t in available_set if t not in required]

        return {
            "complete": len(missing) == 0,
            "required_count": len(required),
            "available_count": len(available_types),
            "missing": missing,
            "extra": extra,
            "missing_labels": [
                self._get_label(t) for t in missing
            ],
        }

    def suggest_placement(self, image_type: str) -> Dict:
        """Suggest where a specific image type should be placed in the report."""
        for ch, info in self.positions.items():
            for img in info["expected_images"]:
                if img["type"] == image_type:
                    return {
                        "chapter": ch,
                        "chapter_title": info["chapter_title"],
                        "section": img["section"],
                        "placement": img["placement"],
                        "required": img.get("required", False),
                        "tips": img.get("tips", ""),
                    }
        return {
            "chapter": 12,
            "chapter_title": "附件",
            "section": "附件一",
            "placement": "其他材料区域",
            "required": False,
            "tips": "未识别类型的图片，建议放在附件最后",
        }

    def generate_report_image_plan(self, available_images: List[Dict]) -> Dict[int, List[Dict]]:
        """Generate a complete image placement plan for report generation.

        Each available_image should have: type (classification key), label, file_path

        Returns: chapter → [image placements]
        """
        plan = {}
        for img in available_images:
            img_type = img.get("type", "other")
            placement = self.suggest_placement(img_type)
            ch = placement["chapter"]
            if ch not in plan:
                plan[ch] = []
            plan[ch].append({**img, "placement": placement})

        # Sort within each chapter by section order
        for ch in plan:
            plan[ch].sort(key=lambda x: x["placement"].get("section", ""))

        return plan

    def get_image_stats(self, classified_images: List[Dict]) -> Dict:
        """Get statistics about a set of classified images."""
        stats = {
            "total": len(classified_images),
            "by_type": {},
            "by_chapter": {},
            "by_attachment": {},
            "required_covered": [],
            "required_missing": [],
        }

        for img in classified_images:
            img_type = img.get("type", "other")
            stats["by_type"][img_type] = stats["by_type"].get(img_type, 0) + 1

            placement = self.suggest_placement(img_type)
            ch = placement["chapter"]
            stats["by_chapter"][ch] = stats["by_chapter"].get(ch, 0) + 1

            att = placement.get("section", "").split("：")[0] if "：" in placement.get("section", "") else ""
            if att:
                stats["by_attachment"][att] = stats["by_attachment"].get(att, 0) + 1

        # Check required coverage
        available_types = list(stats["by_type"].keys())
        validation = self.validate_image_set(available_types)
        stats["required_covered"] = [t for t in validation["missing"] if t not in available_types]
        stats["required_missing"] = validation["missing"]

        return stats

    def _get_label(self, image_type: str) -> str:
        for ch, info in self.positions.items():
            for img in info["expected_images"]:
                if img["type"] == image_type:
                    return img["label"]
        return image_type

    def learn_from_docx(self, docx_path: str) -> Dict:
        """Analyze a DOCX template to extract image positions.

        Scans the document for:
        - Image placeholders ({{...}} markers)
        - Embedded images with surrounding text context
        - Chapter heading context for each image

        Returns learned position map that can be merged with standard positions.
        """
        try:
            from docx import Document
            from docx.opc.constants import RELATIONSHIP_TYPE as RT
        except ImportError:
            return {"error": "python-docx not available"}

        doc = Document(docx_path)
        findings = {
            "embedded_images": [],
            "image_placeholders": [],
            "chapters_with_images": {},
        }

        current_chapter = 0
        chapter_titles = {}

        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()

            # Detect chapter headings
            ch_match = re.match(r'第([一二三四五六七八九十\d]+)章\s*(.+)', text)
            if ch_match:
                current_chapter = _cn_to_int(ch_match.group(1))
                chapter_titles[current_chapter] = ch_match.group(2).strip()

            # Detect image placeholders
            placeholder_patterns = [
                r'\{\{(.+?)\}\}',   # {{placeholder}}
                r'【插入(.+?)】',     # 【插入xxx】
                r'\[插入(.+?)\]',    # [插入xxx]
            ]
            for pat in placeholder_patterns:
                for m in re.finditer(pat, text):
                    findings["image_placeholders"].append({
                        "chapter": current_chapter,
                        "chapter_title": chapter_titles.get(current_chapter, ""),
                        "paragraph_index": i,
                        "placeholder_text": m.group(1).strip(),
                        "surrounding_text": text[:100],
                    })

            # Check for inline images
            for rel in para._element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing'):
                findings["embedded_images"].append({
                    "chapter": current_chapter,
                    "chapter_title": chapter_titles.get(current_chapter, ""),
                    "paragraph_index": i,
                    "surrounding_text": text[:100],
                })

        # Build chapter-image mapping
        for img in findings["embedded_images"]:
            ch = img["chapter"]
            if ch not in findings["chapters_with_images"]:
                findings["chapters_with_images"][ch] = []
            findings["chapters_with_images"][ch].append(img)

        return findings


def _cn_to_int(s: str) -> int:
    """Chinese numeral to int."""
    mapping = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
               '十一':11,'十二':12,'十三':13}
    if s.isdigit(): return int(s)
    return mapping.get(s, 0)


template_image_learner = TemplateImageLearner()
