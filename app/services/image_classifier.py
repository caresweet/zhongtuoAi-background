"""Image classification for 社会稳定风险评估 reports.

Auto-detects image type from filename, visual keywords, and document context.
Maps images to the correct report chapter/attachment position.
"""

import re
from typing import Dict, List, Optional, Tuple


# ── Image category definitions ──────────────────────────────────────────────

# Each category maps to a report chapter + attachment section
IMAGE_CATEGORIES = {
    # ── 公司资质类 → 第十二章 附件一 ──
    "business_license": {
        "label": "营业执照",
        "chapter": 12,
        "attachment": "附件一：公司资质",
        "section_title": "公司营业执照",
        "keywords": ["营业执照", "统一社会信用代码", "business license", "执照"],
        "filename_patterns": [r"营业", r"执照", r"license"],
        "visual_keywords": ["营业执照", "统一社会信用代码", "注册资本", "法定代表人", "经营范围"],
        "priority": 1,
    },
    "qualification_cert": {
        "label": "资质证书",
        "chapter": 12,
        "attachment": "附件一：公司资质",
        "section_title": "公司资质证书",
        "keywords": ["资质证书", "资质", "工程咨询", "评估资质", "certificate"],
        "filename_patterns": [r"资质", r"证书", r"cert", r"qualif"],
        "visual_keywords": ["资质证书", "资质等级", "证书编号", "有效期"],
        "priority": 2,
    },
    "personnel_cert": {
        "label": "人员证书",
        "chapter": 12,
        "attachment": "附件一：公司资质",
        "section_title": "人员培训合格证书",
        "keywords": ["培训证书", "培训合格", "结业证书", "人员", "姓名"],
        "filename_patterns": [r"培训", r"合格", r"证书.*人", r"personnel", r"train"],
        "visual_keywords": ["培训合格证书", "社会稳定风险评估", "培训", "姓名", "证书编号"],
        "priority": 3,
    },
    "social_security": {
        "label": "社保纳税证明",
        "chapter": 12,
        "attachment": "附件一：公司资质",
        "section_title": "社保及纳税证明",
        "keywords": ["社保", "纳税", "税收", "社会保障", "缴税"],
        "filename_patterns": [r"社保", r"纳税", r"税收", r"social", r"tax"],
        "visual_keywords": ["社会保险", "纳税", "税收完税证明", "缴费"],
        "priority": 4,
    },
    "financial_report": {
        "label": "财务报告",
        "chapter": 12,
        "attachment": "附件一：公司资质",
        "section_title": "财务审计报告",
        "keywords": ["财务", "审计", "报表", "利润", "资产", "负债"],
        "filename_patterns": [r"财务", r"审计", r"报表", r"financial", r"audit"],
        "visual_keywords": ["财务报告", "审计报告", "资产负债表", "利润表"],
        "priority": 5,
    },

    # ── 项目材料类 → 第一章 / 附件二 ──
    "red_line_map": {
        "label": "征地红线图",
        "chapter": 1,
        "attachment": "附件二：决策相关材料",
        "section_title": "拟征收土地位置示意图（红线图）",
        "keywords": ["红线图", "红线", "征收范围图", "勘界", "地块位置"],
        "filename_patterns": [r"红线", r"勘界", r"范围图", r"red.?line"],
        "visual_keywords": ["红线", "征收范围", "地块", "四至", "道路", "河流"],
        "priority": 10,
    },
    "survey_map": {
        "label": "勘测定界图",
        "chapter": 1,
        "attachment": "附件二：决策相关材料",
        "section_title": "土地勘测定界图",
        "keywords": ["勘测定界", "勘界", "定界", "测绘", "地籍"],
        "filename_patterns": [r"勘测", r"定界", r"勘界", r"测绘", r"survey"],
        "visual_keywords": ["勘测定界", "界址点", "地类", "面积", "坐标"],
        "priority": 11,
    },
    "pre_announcement": {
        "label": "征收预公告",
        "chapter": 1,
        "attachment": "附件二：决策相关材料",
        "section_title": "征收土地预公告（盖章版）",
        "keywords": ["预公告", "拟征告", "征收公告", "拟征收", "盖章"],
        "filename_patterns": [r"预公告", r"拟征告", r"公告.*征收", r"announce"],
        "visual_keywords": ["人民政府", "征收土地预公告", "拟征告", "盖章", "公告"],
        "priority": 12,
    },
    "land_use_map": {
        "label": "土地利用现状图",
        "chapter": 1,
        "attachment": "附件二：决策相关材料",
        "section_title": "土地利用现状图",
        "keywords": ["土地利用", "现状图", "规划图", "地类"],
        "filename_patterns": [r"土地.*利用", r"现状", r"规划图", r"land.?use"],
        "visual_keywords": ["土地利用现状", "地类", "农用地", "建设用地"],
        "priority": 13,
    },

    # ── 调查材料类 → 第三章 / 附件三 ──
    "bulletin_photo": {
        "label": "公示照片",
        "chapter": 3,
        "attachment": "附件三：调查相关材料",
        "section_title": "稳评公示张贴照片",
        "keywords": ["公示", "公示栏", "张贴", "公告栏", "公示照片"],
        "filename_patterns": [r"公示", r"张贴", r"公告栏", r"bulletin"],
        "visual_keywords": ["公示栏", "公告", "张贴", "通知", "社区", "村委会"],
        "sub_types": {"near": "近景", "far": "远景"},
        "priority": 20,
    },
    "meeting_photo": {
        "label": "座谈会照片",
        "chapter": 3,
        "attachment": "附件三：调查相关材料",
        "section_title": "群众座谈会现场照片",
        "keywords": ["座谈", "开会", "会议", "会场", "村民"],
        "filename_patterns": [r"座谈", r"会议", r"开会", r"meeting"],
        "visual_keywords": ["会议", "座谈", "群众", "村民", "问卷", "投影", "会议室"],
        "priority": 21,
    },
    "site_photo": {
        "label": "现场勘查照片",
        "chapter": 3,
        "attachment": "附件三：调查相关材料",
        "section_title": "拟征收地块现场勘查照片",
        "keywords": ["现场", "勘查", "地块", "踏勘", "实地"],
        "filename_patterns": [r"现场", r"勘查", r"踏勘", r"site", r"field"],
        "visual_keywords": ["空地", "农田", "水田", "道路", "地块", "现场", "青苗", "附着物"],
        "priority": 22,
    },
    "survey_form": {
        "label": "调查问卷",
        "chapter": 3,
        "attachment": "附件三：调查相关材料",
        "section_title": "稳评问卷调查表（样表）",
        "keywords": ["问卷", "调查表", "征求意见", "意见表", "勾选"],
        "filename_patterns": [r"问卷", r"调查表", r"意见.*表", r"survey", r"form"],
        "visual_keywords": ["问卷调查", "勾选", "支持", "了解", "反对", "意见", "签名"],
        "priority": 23,
    },
    "signin_sheet": {
        "label": "签到表",
        "chapter": 3,
        "attachment": "附件三：调查相关材料",
        "section_title": "座谈会/评审会签到表",
        "keywords": ["签到", "签名", "出席", "登记"],
        "filename_patterns": [r"签到", r"签名", r"sign.?in", r"attend"],
        "visual_keywords": ["签到表", "姓名", "电话", "地址", "签名"],
        "priority": 24,
    },

    # ── 评审材料类 → 附件四 ──
    "expert_review": {
        "label": "专家评审意见",
        "chapter": 12,
        "attachment": "附件四：专家评审相关材料",
        "section_title": "专家评审意见表",
        "keywords": ["专家", "评审", "意见", "论证", "审核"],
        "filename_patterns": [r"专家", r"评审", r"意见", r"expert", r"review"],
        "visual_keywords": ["专家评审", "评审意见", "低风险", "中风险", "专家签名", "评审结论"],
        "priority": 30,
    },
    "expert_meeting_photo": {
        "label": "专家评审会照片",
        "chapter": 12,
        "attachment": "附件四：专家评审相关材料",
        "section_title": "专家评审会现场照片",
        "keywords": ["专家会", "评审会", "专家开会"],
        "filename_patterns": [r"专家.*会", r"评审.*会", r"expert.*meet"],
        "visual_keywords": ["专家组", "评审", "会议室", "专家", "汇报"],
        "priority": 31,
    },
    "evaluation_form": {
        "label": "稳评评审表",
        "chapter": 12,
        "attachment": "附件四：专家评审相关材料",
        "section_title": "社会稳定风险评估评审表",
        "keywords": ["评审表", "评估表", "稳评表"],
        "filename_patterns": [r"评审表", r"评估表", r"稳评.*表"],
        "visual_keywords": ["社会稳定风险评估评审表", "风险等级", "评审意见"],
        "priority": 32,
    },
}


# ── Classifier ──────────────────────────────────────────────────────────────

class ImageClassifier:
    """Auto-classify images for 稳评 reports based on filename + metadata."""

    def __init__(self):
        self.categories = IMAGE_CATEGORIES

    def classify(self, filename: str, parent_doc_type: str = "",
                 image_summary: str = "", metadata: dict = None) -> Dict:
        """Classify a single image. Returns best-match category info + confidence."""

        filename_lower = filename.lower()
        context = f"{filename} {parent_doc_type} {image_summary}".lower()
        results = []

        for cat_key, cat_info in self.categories.items():
            score = 0

            # Filename pattern match (strongest signal)
            for pat in cat_info.get("filename_patterns", []):
                if re.search(pat, filename_lower):
                    score += 3
                    break

            # Keyword in filename
            for kw in cat_info.get("keywords", []):
                if kw.lower() in filename_lower:
                    score += 2
                    break

            # Context match (doc type + summary)
            for kw in cat_info.get("keywords", []):
                if kw.lower() in context:
                    score += 1

            # Visual keyword hints from summary
            for kw in cat_info.get("visual_keywords", []):
                if kw.lower() in image_summary.lower():
                    score += 0.5

            if score > 0:
                results.append((cat_key, score, cat_info))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)

        if not results:
            return {
                "category_key": "other",
                "category_label": "其他",
                "chapter": 12,
                "attachment": "附件一",
                "section_title": "其他材料",
                "confidence": 0.0,
            }

        best = results[0]
        confidence = min(best[1] / 6.0, 1.0)  # Normalize to 0-1

        return {
            "category_key": best[0],
            "category_label": best[2]["label"],
            "chapter": best[2]["chapter"],
            "attachment": best[2]["attachment"],
            "section_title": best[2]["section_title"],
            "confidence": round(confidence, 2),
            "alternatives": [
                {"key": r[0], "label": r[2]["label"], "score": r[1]}
                for r in results[1:4]
            ],
        }

    def classify_batch(self, images: List[Dict]) -> Dict[str, List[Dict]]:
        """Classify a batch of images. Groups by category.

        Each image dict should have: filename, parent_doc_type, image_summary
        """
        grouped = {}
        for img in images:
            result = self.classify(
                filename=img.get("filename", ""),
                parent_doc_type=img.get("parent_doc_type", ""),
                image_summary=img.get("image_summary", ""),
                metadata=img.get("metadata"),
            )
            key = result["category_key"]
            if key not in grouped:
                grouped[key] = []
            grouped[key].append({**img, "classification": result})

        return grouped

    def get_report_image_map(self) -> Dict[int, List[Dict]]:
        """Return a chapter → image-types mapping for report generation.

        Tells the report generator which image types go in which chapter.
        """
        chapter_map = {}
        for cat_key, cat_info in self.categories.items():
            ch = cat_info["chapter"]
            if ch not in chapter_map:
                chapter_map[ch] = []
            chapter_map[ch].append({
                "key": cat_key,
                "label": cat_info["label"],
                "section": cat_info["section_title"],
                "attachment": cat_info["attachment"],
            })
        return chapter_map

    def get_attachment_index(self) -> Dict[str, List[Dict]]:
        """Return attachment → image-list for the 附件 section."""
        attachments = {}
        for cat_key, cat_info in self.categories.items():
            att = cat_info["attachment"]
            if att not in attachments:
                attachments[att] = []
            attachments[att].append({
                "key": cat_key,
                "label": cat_info["label"],
                "section": cat_info["section_title"],
            })
        return attachments


# Module singleton
image_classifier = ImageClassifier()
