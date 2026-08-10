"""Deep Material Analyzer — AI-powered material classification, OCR, and data extraction.

Replaces simple filename-based categorization with vision LLM analysis.
Runs after material ingestion, before report generation.

Pipeline:
  1. Classify every uploaded image using vision AI → photo/survey/announcement/review/map
  2. OCR text-containing images → extract survey numbers, names, dates
  3. Extract full text from PDFs → parse structured data (area, location, households, etc.)
  4. Populate filled_data with user-provided values (takes priority over KB defaults)
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Image classification categories
IMAGE_CATEGORIES = {
    "survey": {"kw": ["问卷", "调查表", "统计", "签名", "签到", "测评"], "desc": "调查问卷/统计表"},
    "announcement": {"kw": ["公告", "公示", "批文", "通知", "征收", "预公告", "批复"], "desc": "公告/批文"},
    "review": {"kw": ["评审", "意见", "专家", "签字", "评估报告"], "desc": "专家评审"},
    "map": {"kw": ["地图", "红线", "规划图", "位置图", "勘测", "测定", "地形", "宗地", "示意"], "desc": "地图/红线图"},
    "photo": {"kw": ["现场", "照片", "地块", "房屋", "附着物", "植被", "道路", "村民", "开会", "走访", "座谈"], "desc": "现场照片"},
}


async def classify_image_with_vision(image_path: str, llm_service=None) -> Dict[str, str]:
    """Use vision LLM to classify an image and extract any text content."""
    result = {
        "category": "other",
        "has_text": False,
        "extracted_text": "",
        "description": "",
    }

    if not llm_service:
        return result

    try:
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        prompt = (
            "请分析这张图片：\n"
            "1. 图片类型（单选）：调查问卷/公告公示/专家评审/地图红线/现场照片/其他\n"
            "2. 图片中是否有文字？如有，请提取所有可见文字\n"
            "3. 如果图片中有表格数据（人数、百分比、面积等），请以JSON格式输出\n"
            "请用JSON格式回复：{\"category\": \"...\", \"has_text\": true/false, \"text\": \"...\", \"data\": {...}}"
        )

        response = await asyncio.wait_for(
            llm_service.chat_with_reasoning(
                messages=[
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt},
                    ]}
                ],
                max_tokens=1000, temperature=0.1,
            ),
            timeout=60.0,
        )

        content = response.get("content", "")
        # Try to parse JSON from response
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            result["category"] = parsed.get("category", "other")
            result["has_text"] = parsed.get("has_text", False)
            result["extracted_text"] = parsed.get("text", "")
            result["description"] = parsed.get("category", "")
            data = parsed.get("data", {})
            if isinstance(data, dict):
                for k, v in data.items():
                    result[k] = v

    except asyncio.TimeoutError:
        logger.warning(f"Vision classification timeout for {image_path}")
    except Exception as e:
        logger.warning(f"Vision classification failed for {image_path}: {e}")

    return result


def classify_image_by_filename(filepath: str) -> Optional[str]:
    """Fallback: classify by filename keywords when vision AI is unavailable."""
    fname = os.path.basename(filepath).lower()
    for cat, info in IMAGE_CATEGORIES.items():
        for kw in info["kw"]:
            if kw in fname:
                return cat
    return None


async def analyze_all_materials(
    state: dict,
    llm_service=None,
    max_vision_images: int = 10,
) -> Dict[str, Any]:
    """Deep analysis of all uploaded materials.

    Returns:
        Dict with:
        - classified_images: {category: [path, ...]}
        - extracted_survey_data: {total_samples, support_count, ...}
        - extracted_pdf_text: str
        - filled_data_updates: dict for merging into state["filled_data"]
    """
    result = {
        "classified_images": {},
        "extracted_survey_data": {},
        "extracted_pdf_text": "",
        "filled_data_updates": {},
    }

    # Collect all files
    all_files = []
    uploaded = state.get("_uploaded_files", []) or []
    for item in uploaded:
        if isinstance(item, str):
            all_files.append(item)
        elif isinstance(item, dict):
            all_files.append(item.get("path", ""))

    material_facts = state.get("_project_material_facts", {}) or {}
    extracted_imgs = material_facts.get("_extracted_images", []) or []
    for img in extracted_imgs:
        if img not in all_files:
            all_files.append(img)

    if not all_files:
        return result

    # Separate images from PDFs
    images = [f for f in all_files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'))]
    pdfs = [f for f in all_files if f.lower().endswith('.pdf')]

    logger.info(f"Deep analysis: {len(images)} images, {len(pdfs)} PDFs")

    # 1. Classify images — only OCR scanned documents, skip scenery/notice photos
    TEXT_DOC_KEYWORDS = ['扫描', '签字', '问卷', '调查表', '意见', '评审', '签到',
                         'pdf_page', 'pdf_', '评估', '备案', '执照', '证书']
    classified = {cat: [] for cat in IMAGE_CATEGORIES}
    classified["other"] = []

    for img_path in images:
        fname = os.path.basename(img_path).lower()
        fdir = os.path.dirname(img_path).lower() if os.path.dirname(img_path) else ""

        # 🔴 Only OCR images that likely contain text (scanned docs, forms, certificates)
        needs_ocr = any(kw in fname or kw in fdir for kw in TEXT_DOC_KEYWORDS)

        if needs_ocr and llm_service and len(classified.get("map",[])) + len(classified.get("survey",[])) + len(classified.get("review",[])) < max_vision_images:
            vision_result = await classify_image_with_vision(img_path, llm_service)
            cat = vision_result.get("category", "other")
            if cat in classified:
                classified[cat].append(img_path)
            else:
                classified["other"].append(img_path)
            if vision_result.get("has_text"):
                text = vision_result.get("extracted_text", "")
                if text:
                    result["extracted_pdf_text"] += f"\n[{os.path.basename(img_path)}]\n{text}\n"
        else:
            # 🔴 Photos — just classify by filename/folder, no expensive vision API call
            cat = classify_image_by_filename(img_path) or "other"
            classified[cat].append(img_path)

    result["classified_images"] = {k: v for k, v in classified.items() if v}

    # 2. Extract PDF text
    pdf_text = ""
    for pdf_path in pdfs:
        try:
            from app.services.material_ingestion_service import MaterialIngestionService
            service = MaterialIngestionService()
            artifact = await service.ingest_material(
                pdf_path, scope="deep_analysis", document_type="pdf", domain="stability"
            )
            txt = getattr(artifact, 'text_content', '') or ''
            if isinstance(txt, str) and len(txt) > 10:
                pdf_text += f"\n[{os.path.basename(pdf_path)}]\n{txt}\n"
        except Exception as e:
            logger.warning(f"PDF text extraction failed for {pdf_path}: {e}")

    result["extracted_pdf_text"] = pdf_text

    # 3. Extract structured data from PDF text
    if pdf_text:
        updates = _extract_data_from_text(pdf_text)
        result["filled_data_updates"].update(updates)

    # 4. Extract survey data from vision results
    survey_data = result["extracted_survey_data"]
    if survey_data:
        for k, v in survey_data.items():
            if v:
                result["filled_data_updates"][k] = str(v)

    return result


def _extract_data_from_text(text: str) -> Dict[str, str]:
    """Extract structured project data from PDF text using regex patterns."""
    updates = {}

    patterns = [
        (r'(\d{5,7})\s*(?:平方米|㎡)', 'area_m2'),
        (r'(\d+\.?\d*)\s*亩', 'area_mu'),
        (r'(?:位于|坐落|位置)[：:]?\s*(.{5,60}?)(?:。|\n)', 'location'),
        (r'(?:用途|地类)[：:]?\s*(\S{2,30})', 'land_use'),
        (r'(?:涉及|共|共计)\s*(\d+)\s*(?:户|农户)', 'household_count'),
        (r'(\d+)\s*(?:人|人口)\s*(?:被征地|安置)', 'population_count'),
        (r'(?:补偿标准|综合地价)[^\d]*(\d[\d,.]*)\s*(?:元|万元)', 'compensation_standard'),
        (r'(?:总费用|总补偿|资金)[^\d]*(\d[\d,.]*)\s*(?:万元|元)', 'funding'),
        (r'(\d+)\s*(?:份|张)\s*(?:问卷|调查)', 'total_samples'),
        (r'([^\s]{2,10}[告发字]\s*〔?\s*\d{4}\s*〕?\s*\d+\s*号)', 'doc_reference'),
        (r'(?:责任单位|征收主体)[：:]\s*(\S{2,30})', 'org_name'),
    ]

    for pattern, key in patterns:
        match = re.search(pattern, text)
        if match and key not in updates:
            try:
                val = match.group(1) if match.lastindex else match.group(0)
                updates[key] = val.strip()
            except (IndexError, AttributeError):
                pass

    # Extract survey statistics if present
    survey_matches = re.findall(r'(?:支持|赞成|同意)\s*(\d+)\s*(?:人|份)', text)
    if survey_matches:
        updates['support_count'] = survey_matches[0]
    oppose_matches = re.findall(r'(?:反对|不同意)\s*(\d+)\s*(?:人|份)', text)
    if oppose_matches:
        updates['oppose_count'] = oppose_matches[0]

    return updates


def apply_analysis_to_state(state: dict, analysis_result: Dict[str, Any]) -> None:
    """Apply deep analysis results to the session state.

    Priority: user-provided data > extracted data > KB defaults
    """
    # 1. Classified images → merge into state for assembler
    classified = analysis_result.get("classified_images", {})
    if classified:
        # Store classified images so _get_session_images can use them
        state["_classified_images"] = classified
        # Also update _uploaded_files with category info
        for cat, paths in classified.items():
            if cat != "other" and paths:
                existing = state.get("_uploaded_files", []) or []
                for p in paths:
                    if p not in existing:
                        existing.append(p)
                state["_uploaded_files"] = existing

    # 2. Filled data updates
    updates = analysis_result.get("filled_data_updates", {})
    filled = state.setdefault("filled_data", {})
    for k, v in updates.items():
        if v and (k not in filled or not filled[k]):
            filled[k] = str(v)

    # 3. PDF text for RAG
    pdf_text = analysis_result.get("extracted_pdf_text", "")
    if pdf_text:
        state.setdefault("_pdf_texts", {})["deep_analysis"] = pdf_text

    # 4. Compute survey defaults from extracted data if missing
    total = int(filled.get("total_samples") or filled.get("household_count") or 0)
    if total > 0:
        support = int(filled.get("support_count") or total * 0.61)
        oppose = int(filled.get("oppose_count") or total * 0.10)
        filled.setdefault("total_samples", str(total))
        filled.setdefault("survey_total_count", str(total))
        filled.setdefault("support_count", str(support))
        filled.setdefault("oppose_count", str(oppose))
        filled.setdefault("conditional_support_count", str(total - support - oppose))
        if total > 0:
            filled.setdefault("support_rate", f"{support/total*100:.1f}")

    logger.info(f"Deep analysis applied: {len(classified)} image categories, "
                f"{len(updates)} data fields extracted")
