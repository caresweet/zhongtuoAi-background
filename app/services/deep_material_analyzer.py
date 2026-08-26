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

# Image classification categories — 🔴 扩充为8类（survey/announcement/review/map/photo/meeting/cert/other）
IMAGE_CATEGORIES = {
    "survey": {"kw": ["问卷", "调查表", "统计", "签名", "签到", "测评"], "desc": "调查问卷/统计表"},
    "announcement": {"kw": ["公告", "公示", "批文", "通知", "征收", "预公告", "批复"], "desc": "公告/批文"},
    "review": {"kw": ["评审", "意见", "专家", "签字", "评估报告", "综合意见"], "desc": "专家评审"},
    "map": {"kw": ["地图", "红线", "规划图", "位置图", "勘测", "测定", "地形", "宗地", "示意"], "desc": "地图/红线图"},
    "meeting": {"kw": ["座谈", "开会", "会议", "村民", "群众会"], "desc": "座谈会/开会照片"},
    "cert": {"kw": ["执照", "证书", "资质", "备案", "营业执照"], "desc": "公司资质/证书"},
    "photo": {"kw": ["现场", "照片", "地块", "房屋", "附着物", "植被", "道路", "走访", "勘察"], "desc": "现场照片"},
}


async def classify_image_with_vision(image_path: str, llm_service=None) -> Dict[str, str]:
    """Use vision LLM to classify an image and extract any text content.

    🔴 结果按文件 md5 缓存到磁盘，跨 session 复用。
    """
    result = {
        "category": "other",
        "has_text": False,
        "extracted_text": "",
        "description": "",
    }

    # 🔴 磁盘缓存 key（文件 md5）
    cache_file = None
    try:
        import hashlib, pickle
        from app.config import settings
        cache_dir = settings.STORAGE_DIR / "image_classify_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.md5()
        with open(image_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        cache_file = cache_dir / f"{h.hexdigest()}.pkl"
        if cache_file.exists():
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
    except Exception:
        pass

    if not llm_service:
        return result

    try:
        import base64
        # 🔴 修复相对路径：uploaded files 存的是 "images/xxx.jpg"（相对 storage 目录），
        # 但 open() 需要完整路径。先尝试直接打开，失败则解析到 storage 目录。
        if not os.path.isabs(image_path) and not os.path.exists(image_path):
            from app.config import settings
            candidates = [
                settings.STORAGE_DIR / image_path,
                settings.STORAGE_DIR / "images" / os.path.basename(image_path),
            ]
            for cand in candidates:
                if cand.exists():
                    image_path = str(cand)
                    break
        with open(image_path, "rb") as f:
            raw = f.read()
        # 🔴 压缩图片：超过 800px 或 > 1MB 时压缩，避免大型扫描件导致 413
        from PIL import Image as PILImage
        import io
        try:
            pil_img = PILImage.open(io.BytesIO(raw))
            w, h = pil_img.size
            max_dim = 800  # 🔴 视觉分类只需判断类别，800px 足够且 token 更少
            if w > max_dim or h > max_dim or len(raw) > 300_000:
                ratio = min(max_dim / w, max_dim / h)
                pil_img = pil_img.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)
                buf = io.BytesIO()
                pil_img.save(buf, format='JPEG', quality=80, optimize=True)
                raw = buf.getvalue()
        except Exception:
            pass
        img_b64 = base64.b64encode(raw).decode()

        prompt = (
            "你是社会稳定风险评估的图片识别助手。请识别这张图片，并【只输出 JSON，不要输出任何解释文字】。\n"
            "JSON 格式如下：\n"
            '{"category": "现场勘查", "has_text": false, "text": ""}\n'
            "\n要求：\n"
            "- category 取值（严格从以下选一个）：\n"
            "  现场勘查（户外地块、施工现场、临时用地、实地踏勘的场地照片）\n"
            "  座谈会（室内开会、村民/群众围坐讨论、会议室、签到）\n"
            "  调查问卷（问卷、调查表、统计表）\n"
            "  公告公示（公示栏、张贴的公告、批文）\n"
            "  专家评审（专家意见、评审签字、评审会）\n"
            "  地图红线（位置图、红线图、勘测定界图、规划图）\n"
            "  其他（无法归入以上类别）\n"
            "- 只输出 JSON，不要 markdown 代码块，不要解释"
        )

        # 🔴 用 chat_with_image（多模态视觉方法），而不是 chat_with_reasoning（文本模型）。
        # chat_with_reasoning 不支持图片输入，导致视觉 OCR 返回空结果。
        response = await asyncio.wait_for(
            llm_service.chat_with_image(
                text=prompt,
                image_base64=img_b64,
                media_type="image/png",
                max_tokens=200,
            ),
            timeout=60.0,
        )

        content = response if isinstance(response, str) else response.get("content", "")
        # Try to parse JSON from response（去掉可能的 markdown 代码块）
        content_clean = re.sub(r'```(?:json)?', '', content).strip()
        # 🔴 用 start/end 提取完整 JSON（支持嵌套，正则 \{...\} 无法匹配嵌套）
        start = content_clean.find('{')
        end = content_clean.rfind('}')
        parsed = None
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(content_clean[start:end + 1])
            except json.JSONDecodeError:
                parsed = None
        if parsed:
            result["category"] = parsed.get("category", "other")
            result["has_text"] = parsed.get("has_text", False)
            result["extracted_text"] = parsed.get("text", "")
            result["description"] = parsed.get("category", "")
            data = parsed.get("data", {})
            if isinstance(data, dict):
                for k, v in data.items():
                    result[k] = v
            # 🔴 问卷勾选识别结果
            questions = parsed.get("questions", [])
            if isinstance(questions, list):
                result["questions"] = questions
        else:
            # 🔴 fallback：视觉模型返回了自然语言，把整段作为提取文本
            result["has_text"] = bool(content and content.strip())
            result["extracted_text"] = content.strip()[:2000]
            # 从自然语言推断类别
            if any(kw in content for kw in ['问卷', '调查', '勾选', '支持', '反对']):
                result["category"] = "survey"
            elif any(kw in content for kw in ['公告', '公示', '征收']):
                result["category"] = "announcement"
            elif any(kw in content for kw in ['评审', '意见', '专家']):
                result["category"] = "review"

    except asyncio.TimeoutError:
        logger.warning(f"Vision classification timeout for {image_path}")
    except Exception as e:
        logger.warning(f"Vision classification failed for {image_path}: {e}")

    # 🔴 存磁盘缓存（即使失败也存，避免重复尝试）
    try:
        import pickle
        pickle.dump(result, open(cache_file, 'wb'))
    except Exception:
        pass

    return result


def classify_image_by_filename(filepath: str) -> Optional[str]:
    """Fallback: classify by filename keywords when vision AI is unavailable."""
    fname = os.path.basename(filepath).lower()
    for cat, info in IMAGE_CATEGORIES.items():
        for kw in info["kw"]:
            if kw in fname:
                return cat
    return None


def _map_vision_category(category: str) -> str:
    """把 vision AI 返回的中文类别映射到英文 key。

    vision prompt 返回：调查问卷/公告公示/专家评审/地图红线/现场照片/其他/座谈会/证书
    """
    if not category:
        return "other"
    mapping = [
        ('调查问卷', 'survey'), ('问卷', 'survey'), ('调查表', 'survey'),
        ('公告', 'announcement'), ('公示', 'announcement'), ('预公告', 'announcement'),
        ('评审', 'review'), ('专家', 'review'), ('意见', 'review'),
        ('地图', 'map'), ('红线', 'map'), ('位置', 'map'), ('勘测', 'map'),
        ('座谈', 'meeting'), ('开会', 'meeting'), ('会议', 'meeting'), ('签到', 'meeting'),
        ('证书', 'cert'), ('执照', 'cert'), ('资质', 'cert'),
        ('现场勘查', 'photo'), ('勘查', 'photo'), ('现场', 'photo'), ('照片', 'photo'),
    ]
    for cn, en in mapping:
        if cn in category:
            return en
    return "other"


def _map_dept_question(question: str) -> Optional[str]:
    """把部门调查题目（贵单位开头）映射到 dept_data_maps 的 key。

    Returns dept_* key，匹配不到返回 None。
    """
    if '了解程度' in question:
        return 'dept_decision_know'
    if '宣传' in question or '公示' in question or '满意' in question:
        return 'dept_publicity_satisfy'
    if '补偿安置政策' in question or '政策了解' in question:
        return 'dept_policy_know'
    if '关心' in question or '主要事项' in question:
        return 'dept_main_concern'
    if '风险等级' in question:
        return 'dept_risk_opinion'
    if '信心' in question:
        return 'dept_stability_confidence'
    if '基本态度' in question or '态度' in question:
        return 'dept_basic_attitude'
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

    # 🔴 主动扫描 storage/images 里的 PDF 提取图片（座谈会问卷扫描页等）。
    # 解决时序问题：PDF 图片在后台异步提取，deep analysis 可能跑在提取完成之前，
    # 导致 _extracted_images 为空。这里直接扫描磁盘补上。
    try:
        from app.config import settings
        images_dir = settings.STORAGE_DIR / "images"
        if images_dir.exists():
            for f in images_dir.iterdir():
                fn = f.name
                # 只扫描 PDF 嵌入图（_img），跳过整页渲染（_page/_full，用于 OCR 不是问卷材料）
                if not fn.startswith('pdf_'):
                    continue
                if '_page' in fn or '_full' in fn:
                    continue
                if f.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
                    continue
                fp = str(f)
                if fp not in all_files:
                    all_files.append(fp)
    except Exception as e:
        logger.warning(f"Scan storage/images for PDF images failed: {e}")

    if not all_files:
        return result

    # Separate images from PDFs
    images = [f for f in all_files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'))]
    pdfs = [f for f in all_files if f.lower().endswith('.pdf')]

    logger.info(f"Deep analysis: {len(images)} images, {len(pdfs)} PDFs")

    # 1. 图片筛选分类：文字文档全量 OCR，场景照片直接分类备用
    # 文字文档（扫描件/表单/证书）：需要 OCR 提取勾选、数据
    TEXT_DOC_KEYWORDS = ['扫描', '签字', '问卷', '调查表', '意见', '评审', '签到',
                         'pdf_page', 'pdf_', '评估', '备案', '执照', '证书', '预公告', '公告']
    # 文件名明确能判断类别的关键词（可直接分类，省 vision）
    CLEAR_FILENAME_KEYWORDS = ['位置图', '红线', '勘测', '公示栏', '问卷', '签到表', '专家意见',
                               '评审', '意见', '执照', '证书', '资质', '公告', '预公告', '地图', '百度']
    # 模糊命名（无法从文件名判断）→ 调用 vision 分类
    AMBIGUOUS_PREFIXES = ['微信图片', '图片', 'dsc', 'img', 'photo', 'image', 'mmexport', 'screenshot']

    classified = {cat: [] for cat in IMAGE_CATEGORIES}
    classified["other"] = []

    # 🔴 问卷勾选统计：question -> {option -> count}
    survey_tallies = {}
    # 🔴 部门调查统计：dept_map_key -> {option -> count}
    dept_tallies = {}

    # 分离：文字文档（需 vision OCR）/ 模糊图片（需 vision 分类）/ 文件名明确（直接分类）
    text_doc_images = []
    ambiguous_images = []
    for img_path in images:
        fname = os.path.basename(img_path).lower()
        fdir = os.path.dirname(img_path).lower() if os.path.dirname(img_path) else ""
        is_text_doc = any(kw in fname or kw in fdir for kw in TEXT_DOC_KEYWORDS)
        is_clear_name = any(kw in fname for kw in CLEAR_FILENAME_KEYWORDS)
        is_ambiguous = any(fname.startswith(p) for p in AMBIGUOUS_PREFIXES) or \
                       re.match(r'^\d+\.(jpg|png|jpeg)$', fname)
        if is_text_doc and llm_service:
            text_doc_images.append(img_path)  # 文字文档 → vision OCR
        elif is_ambiguous and llm_service:
            ambiguous_images.append(img_path)  # 模糊命名 → vision 分类
        else:
            # 文件名明确 → 直接按文件名/文件夹分类
            cat = classify_image_by_filename(img_path) or "other"
            classified[cat].append(img_path)

    # 🔴 文字文档 + 模糊图片 并发 vision 分类（限并发数，避免 API 限流）
    vision_images = text_doc_images + ambiguous_images
    if vision_images and llm_service:
        sem = asyncio.Semaphore(10)  # 🔴 提高并发加速图片分类

        async def _ocr_one(img_path):
            async with sem:
                try:
                    vr = await classify_image_with_vision(img_path, llm_service)
                except Exception as e:
                    logger.warning(f"Vision OCR error for {img_path}: {e}")
                    vr = {}
                return img_path, vr

        vision_results = await asyncio.gather(*[_ocr_one(p) for p in vision_images])

        for img_path, vision_result in vision_results:
            cat = vision_result.get("category", "other")
            # 🔴 vision 返回的是中文类别（调查问卷/公告公示/专家评审/地图红线/现场照片等），映射到英文 key
            cat_key = _map_vision_category(cat)
            if cat_key in classified:
                classified[cat_key].append(img_path)
            else:
                classified["other"].append(img_path)
            if vision_result.get("has_text"):
                text = vision_result.get("extracted_text", "")
                if text:
                    result["extracted_pdf_text"] += f"\n[{os.path.basename(img_path)}]\n{text}\n"
            # 🔴 收集问卷勾选结果（questions: [{question, options, selected}]）
            questions = vision_result.get("questions", [])
            if isinstance(questions, list):
                for q in questions:
                    if not isinstance(q, dict):
                        continue
                    question = q.get("question", "")
                    selected = q.get("selected", "")
                    # 🔴 类型保护：question/selected 可能是 list 或其他类型
                    if not isinstance(question, str):
                        continue
                    if isinstance(selected, list):
                        selected = selected[0] if selected else ""
                    if not isinstance(selected, str):
                        selected = str(selected) if selected else ""
                    question = question.strip()
                    selected = selected.strip()
                    # 🔴 归一化：全角标点统一为半角，避免「36~55」和「36～55」被拆成两个选项
                    for full, half in [('～', '~'), ('（', '('), ('）', ')'), ('，', ','), ('：', ':')]:
                        selected = selected.replace(full, half)
                    if not (question and selected):
                        continue
                    # 🔴 区分部门调查（"贵单位"/"部门"开头）和群众问卷
                    if '贵单位' in question or question.startswith('部门'):
                        map_key = _map_dept_question(question)
                        if map_key:
                            dept_tallies.setdefault(map_key, {})
                            dept_tallies[map_key][selected] = dept_tallies[map_key].get(selected, 0) + 1
                    else:
                        survey_tallies.setdefault(question, {})
                        survey_tallies[question][selected] = survey_tallies[question].get(selected, 0) + 1

    result["classified_images"] = {k: v for k, v in classified.items() if v}

    # 🔴 聚合问卷勾选统计，得出每题各选项的人数和百分比
    if survey_tallies:
        total_sheets = max(
            (sum(option_counts.values()) for option_counts in survey_tallies.values()),
            default=0
        )
        result["extracted_survey_data"]["questionnaire_tallies"] = survey_tallies
        result["extracted_survey_data"]["questionnaire_total_sheets"] = total_sheets

    # 🔴 聚合部门调查统计（贵单位开头的题目），存到 dept_* 字段
    if dept_tallies:
        dept_total = max(
            (sum(option_counts.values()) for option_counts in dept_tallies.values()),
            default=0
        )
        result["extracted_survey_data"]["dept_survey_count"] = dept_total
        # 把 dept_tallies 的每个 map_key 存为独立字段（供 _fill_table_data 使用）
        for map_key, option_counts in dept_tallies.items():
            result["filled_data_updates"][map_key] = option_counts

    # 2. Extract PDF text — 从 state 已有的 _project_materials 读取，不重复 OCR
    pdf_text = ""
    pdf_survey_updates = {}
    existing_materials = state.get("_project_materials", []) or []
    for item in existing_materials:
        if not isinstance(item, dict):
            continue
        fp = item.get("source_path", "") or ""
        if not fp.lower().endswith('.pdf'):
            continue
        txt = str(item.get('text_content', '') or '')
        if len(txt) > 10:
            pdf_text += f"\n[{os.path.basename(fp)}]\n{txt}\n"
        # 从 PDF 聚合的 key_data 读取问卷统计
        sd = item.get("structured_data", None) or {} if isinstance(item.get("structured_data"), dict) else {}
        if isinstance(sd, dict):
            for k in ("total_samples", "support_count", "oppose_count",
                      "support_rate", "oppose_rate", "awareness_rate",
                      "symposium_attendees", "symposium_date", "symposium_location", "survey_data_source"):
                v = sd.get(k)
                if v not in (None, "", "0", "0.0", "0.0%"):
                    pdf_survey_updates.setdefault(k, v)

    result["extracted_pdf_text"] = pdf_text

    # 3. Extract structured data from PDF text
    if pdf_text:
        updates = _extract_data_from_text(pdf_text)
        result["filled_data_updates"].update(updates)
    # 🔴 PDF 聚合问卷统计：问卷图/用户数据优先，缺失字段才用 PDF 聚合结果补
    for k, v in pdf_survey_updates.items():
        if k not in result["filled_data_updates"] or not result["filled_data_updates"].get(k):
            result["filled_data_updates"][k] = v

    # 4. Extract survey data from vision results
    survey_data = result["extracted_survey_data"]
    if survey_data:
        for k, v in survey_data.items():
            if v:
                result["filled_data_updates"][k] = str(v)

    # 🔴 问卷勾选统计：以结构化形式传给 agent（dict 不转字符串）
    tallies = result["extracted_survey_data"].get("questionnaire_tallies", {})
    if tallies:
        result["filled_data_updates"]["questionnaire_tallies"] = tallies
        total = result["extracted_survey_data"].get("questionnaire_total_sheets", 0)
        result["filled_data_updates"]["survey_total_count"] = total
        # 生成易读的统计摘要，供 agent 直接引用
        summary_lines = []
        for question, option_counts in tallies.items():
            opt_parts = []
            for opt, cnt in option_counts.items():
                pct = round(cnt / total * 100, 1) if total else 0
                opt_parts.append(f"{opt} {cnt}人({pct}%)")
            summary_lines.append(f"「{question}」：{'、'.join(opt_parts)}")
        result["filled_data_updates"]["questionnaire_summary"] = "；".join(summary_lines)

        # 🔴 从问卷统计推导支持率/反对率（真实数据，不编造）
        # 找到"支持/反对/了解"类题目，计算支持率
        support_q = None
        for q in tallies.keys():
            if any(kw in q for kw in ['支持', '态度']):
                support_q = q
                break
        if support_q and isinstance(tallies[support_q], dict):
            opts = tallies[support_q]
            support_n = 0
            oppose_n = 0
            for opt, cnt in opts.items():
                if '支持' in opt and '条件' not in opt:
                    support_n += cnt
                elif '反对' in opt or '不满意' in opt:
                    oppose_n += cnt
            total_votes = sum(opts.values())
            if total_votes > 0:
                rate = round(support_n / total_votes * 100, 1)
                result["filled_data_updates"]["support_rate"] = f"{rate}"
                result["filled_data_updates"]["support_count"] = str(support_n)
                result["filled_data_updates"]["oppose_count"] = str(oppose_n)
                logger.info(f"从问卷统计推导支持率: {support_n}/{total_votes} = {rate}%")

        # 🔴 从"您是/身份"题推导涉及户数（调查覆盖的户数）
        identity_q = None
        for q in tallies.keys():
            if any(kw in q for kw in ['您是', '身份', '居民']):
                identity_q = q
                break
        if identity_q and isinstance(tallies[identity_q], dict):
            identity_total = sum(tallies[identity_q].values())
            if identity_total > 0:
                result["filled_data_updates"]["household_count"] = str(identity_total)
                logger.info(f"从问卷身份题推导涉及户数: {identity_total}")

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
            if isinstance(v, dict):
                filled[k] = v  # 🔴 dict 保留结构（如 questionnaire_tallies），不转字符串
            else:
                filled[k] = str(v)

    # 3. PDF text for RAG
    pdf_text = analysis_result.get("extracted_pdf_text", "")
    if pdf_text:
        state.setdefault("_pdf_texts", {})["deep_analysis"] = pdf_text

    # 4. 🔴 只用真实提取的问卷总数，不编造支持率/反对率
    if filled.get("survey_total_count"):
        filled.setdefault("total_samples", str(filled["survey_total_count"]))

    logger.info(f"Deep analysis applied: {len(classified)} image categories, "
                f"{len(updates)} data fields extracted")
