"""Image Catalog — dynamic image discovery, classification, and matching for report chapters.

Instead of fixed image positions, each chapter requests images by type/category,
and the catalog provides the best matches from user-uploaded materials.

Image naming priority:
  1. User-provided folder/name (e.g., "专家评审会照片/xxx.jpg")
  2. AI vision analysis classification
  3. Filename keyword fallback
"""

import os, re, logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Chapter image requirements: what each chapter needs
CHAPTER_IMAGE_SPECS = {
    1: [{"type": "map", "label": "位置示意图", "min": 1, "max": 1, "keywords": ["位置图", "红线", "勘测", "地图", "勘测定界"]}],
    2: [{"type": "photo", "label": "现场勘查照片", "min": 1, "max": 3, "keywords": ["现场", "勘察", "地块", "临时用地"]}],
    3: [
        {"type": "announcement", "label": "公示照片", "min": 1, "max": 2, "keywords": ["公示", "公告栏", "张贴"]},
        {"type": "photo", "label": "风险调查现场照片", "min": 1, "max": 2, "keywords": ["调查", "走访", "入户"]},
        {"type": "photo", "label": "座谈会照片", "min": 1, "max": 1, "keywords": ["座谈", "开会", "村民"]},
        {"type": "survey", "label": "问卷调查及签到表", "min": 1, "max": 2, "keywords": ["问卷", "调查表", "签到", "统计"]},
    ],
    5: [{"type": "review", "label": "专家评审会照片", "min": 1, "max": 2, "keywords": ["评审", "专家", "意见"]}],
    6: [{"type": "meeting", "label": "座谈会/走访照片", "min": 1, "max": 2, "keywords": ["座谈", "走访", "群众"]}],
    8: [{"type": "review", "label": "专家评审意见", "min": 1, "max": 2, "keywords": ["评审", "意见", "签字"]}],
    9: [{"type": "review", "label": "稳评专家评审意见表", "min": 1, "max": 2, "keywords": ["评审", "意见表", "签字"]}],
}


def build_image_catalog(uploaded_files: List, ai_classifications: Optional[Dict] = None) -> Dict:
    """Scan all user-uploaded files and build a structured image catalog.

    Args:
        uploaded_files: List of file paths or dicts with path/original_name.
        ai_classifications: Optional AI vision classification results from deep_material_analyzer.
            Format: {"map": [path, ...], "announcement": [...], "photo": [...], ...}

    Returns: {
        "total": N,
        "by_chapter": {ch_num: [{"path": ..., "name": ..., "type": ..., "label": ...}, ...]},
        "missing": [{chapter, type, label}, ...],
        "catalog": [{path, name, category, display_name}, ...]
    }
    """
    # Build AI classification lookup: path → ai_category
    ai_lookup = {}
    if ai_classifications:
        for ai_cat, paths in ai_classifications.items():
            for p in (paths or []):
                if isinstance(p, str):
                    ai_lookup[p] = ai_cat
                elif isinstance(p, dict):
                    ai_lookup[p.get("path", "")] = ai_cat

    catalog = []
    all_paths = []

    for item in (uploaded_files or []):
        if isinstance(item, str):
            all_paths.append(item)
        elif isinstance(item, dict):
            path = item.get("path", "") or item.get("relative_path", "")
            if path:
                all_paths.append({"path": path, "original_name": item.get("original_name", os.path.basename(path))})

    # Classify each image
    for item in all_paths:
        if isinstance(item, str):
            path = item
            name = os.path.basename(path)
        else:
            path = item["path"]
            name = item.get("original_name", os.path.basename(path))

        if not path.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
            continue
        if not os.path.exists(path):
            # Try storage/images/
            alt = os.path.join(os.path.dirname(__file__), '..', '..', 'storage', 'images', name)
            if os.path.exists(alt):
                path = alt
            else:
                continue

        if name.startswith('pdf_') and ('_full' in name or '_page' in name):
            continue  # 整页渲染（OCR用），不是报告图片材料
        if '勘测定界' in name:
            continue

        # Extract folder context from path
        folder_hint = ""
        if '/' in path or '\\' in path:
            parts = path.replace('\\', '/').split('/')
            folder_hint = '/'.join(parts[:-1])  # All folder parts as context

        # ── Classification priority ──
        # 1. AI vision classification (highest confidence)
        ai_cat = ai_lookup.get(path, "")
        # 2. Folder + filename classification
        file_cat = _classify_filename(name, folder_hint)

        # Use AI classification if available and more specific than filename
        if ai_cat and ai_cat != "other":
            cat = ai_cat
        else:
            cat = file_cat

        # 🔴 PDF-embedded images from known doc types → override category
        if name.startswith('pdf_座谈会') and '_img' in name:
            cat = "survey"  # Meeting sign-in sheets, survey forms
        elif name.startswith('pdf_洪拟征告') and '_img' in name:
            cat = "announcement"  # Announcement embedded images
        elif name.startswith('pdf_0-勘测') and '_img' in name:
            cat = "map"  # Survey report embedded images

        display_name = _clean_display_name(name, cat)

        # Check folder structure for better display name
        if '/' in path or '稳评' in path:
            parts = path.replace('\\', '/').split('/')
            for part in parts:
                if any(kw in part for kw in ['公示', '评审', '座谈', '现场', '勘测', '公告', '走访', '问卷']):
                    display_name = part
                    break  # 🔴 Use first meaningful folder name, not last

        catalog.append({
            "path": path,
            "name": name,
            "category": cat,
            "display_name": display_name,
        })

    # 🔴 Match to chapters with global dedup (each image used at most ONCE)
    used_paths = set()
    by_chapter = {}

    # Process chapters with most specific requirements first
    chapter_order = sorted(CHAPTER_IMAGE_SPECS.keys(),
                           key=lambda ch: sum(s["max"] for s in CHAPTER_IMAGE_SPECS[ch]))

    for ch_num in chapter_order:
        specs = CHAPTER_IMAGE_SPECS[ch_num]
        ch_images = []
        for spec in specs:
            # Find matching images NOT already used in another chapter
            available = [img for img in catalog
                         if img["path"] not in used_paths and (
                             img["category"] == spec["type"] or
                             (spec["type"] == "meeting" and img["category"] in ("photo", "review")) or
                             (spec["type"] == "photo" and img["category"] in ("photo", "other"))  # 🔴 Allow "other" as photo fallback
                         )]
            for m in available[:spec["max"]]:
                used_paths.add(m["path"])
                ch_images.append({"path": m["path"], "name": m["display_name"],
                                  "type": spec["type"], "label": spec["label"]})
        by_chapter[ch_num] = ch_images

    # 🔴 Find missing (accounting for dedup)
    missing = []
    for ch_num, specs in CHAPTER_IMAGE_SPECS.items():
        ch_imgs = by_chapter.get(ch_num, [])
        for spec in specs:
            count = sum(1 for img in ch_imgs if img["type"] == spec["type"])
            if count < spec["min"]:
                # Check if there are more images of this type but used elsewhere
                total_of_type = sum(1 for img in catalog if img["category"] == spec["type"])
                if total_of_type > count:
                    missing.append({"chapter": ch_num, "type": spec["type"],
                                    "label": spec["label"], "need": spec["min"] - count,
                                    "note": "该类型图片已被其他章节使用"})
                else:
                    missing.append({"chapter": ch_num, "type": spec["type"],
                                    "label": spec["label"], "need": spec["min"] - count})

    return {
        "total": len(catalog),
        "by_chapter": by_chapter,
        "missing": missing,
        "catalog": catalog,
    }


def _classify_filename(name: str, folder_hint: str = "") -> str:
    """Classify an image by filename + folder context.

    Priority: folder context > specific keywords > general keywords.
    """
    nl = name.lower()
    fl = (folder_hint or "").lower()

    # ── Folder context (strongest signal) ──
    if any(k in fl for k in ['评审', '专家']):
        return "review"
    if any(k in fl for k in ['公示', '公告', '张贴']):
        return "announcement"
    if any(k in fl for k in ['地图', '红线', '规划', '勘测', '位置']):
        return "map"
    if any(k in fl for k in ['座谈', '走访', '入户', '调查', '问卷']):
        return "survey" if any(k in fl for k in ['问卷', '调查']) else "photo"
    if any(k in fl for k in ['现场', '勘察', '地块', '房屋']):
        return "photo"

    # ── Specific keywords (check most specific first) ──
    # Maps / diagrams — very specific patterns
    if any(k in nl for k in ['红线图', '位置图', '规划图', '地形图', '宗地图', '勘测定界', '示意']):
        return "map"
    # Survey/questionnaire — specific document types
    if any(k in nl for k in ['问卷', '调查表', '统计表', '签到表', '测评表', '签到']):
        return "survey"
    # Expert review — specific meeting types
    if any(k in nl for k in ['专家意见', '专家签字', '专家组', '评审意见']):
        return "review"
    # Maps — broader map keywords (after more specific checks)
    if any(k in nl for k in ['地图', '红线', '勘测', '测定', '地形', '宗地']):
        return "map"
    # Announcement/notice — only match if NOT also a map keyword
    if any(k in nl for k in ['公示', '公告栏', '张贴', '批文', '预公告', '批复']):
        return "announcement"
    # 🔴 "公告" alone is too broad — only match if combined with specific patterns
    if '公告' in nl and any(k in nl for k in ['张贴', '公示', '栏', '墙', '村']):
        return "announcement"
    # Review (broader)
    if any(k in nl for k in ['评审', '评审会']):
        return "review"
    # Survey/questionnaire (broader)
    if any(k in nl for k in ['问卷', '调查']):
        return "survey"
    # Site photos — check after specific categories
    if any(k in nl for k in ['座谈', '走访', '入户', '村民代表', '群众']):
        return "photo"
    if any(k in nl for k in ['现场', '勘察', '地块现状', '临时用地', '房屋现状']):
        return "photo"
    # Meeting/WeChat photos (least specific — catch remaining)
    if any(k in nl for k in ['微信图片', '会议', '开会']):
        return "photo"
    return "other"


def _clean_display_name(name: str, category: str = "") -> str:
    """Clean up a filename into a readable display name.

    For WeChat/generic images, use category label instead of raw filename.
    """
    CAT_LABELS = {
        'map': '位置示意图', 'photo': '现场照片', 'announcement': '公示照片',
        'survey': '调查问卷', 'review': '专家评审', 'meeting': '座谈会照片',
        'other': '资料图片'
    }
    nl = name.lower()
    # 🔴 PDF-extracted images → always use category label (filenames are meaningless)
    if nl.startswith('pdf_'):
        return CAT_LABELS.get(category, '资料图片')
    # 🔴 WeChat/generic images → use category name
    if any(kw in nl for kw in ['微信图片', 'img_', 'dsc_', 'photo_', 'image_',
                                 'mmexport', 'qq图片', 'screenshot', '截图',
                                 '无标题', 'untitled', 'capture']):
        return CAT_LABELS.get(category, '资料图片')

    # Remove extension
    clean = re.sub(r'\.(jpg|jpeg|png|gif|bmp|webp)$', '', name, flags=re.I)
    # Remove hash suffixes
    clean = re.sub(r'_[a-f0-9]{8,}$', '', clean)
    # Remove date prefixes like 20260427170224
    clean = re.sub(r'^\d{10,}$', '', clean)
    # Remove WeChat prefixes
    clean = re.sub(r'^微信图片_', '', clean)
    # Replace underscores
    clean = clean.replace('_', ' ').strip()
    return clean if clean else CAT_LABELS.get(category, name)


def get_chapter_image_guide(chapter_num: int, catalog: Dict) -> str:
    """Generate a prompt guide for the chapter agent about available images."""
    ch_images = catalog.get("by_chapter", {}).get(chapter_num, [])
    if not ch_images:
        specs = CHAPTER_IMAGE_SPECS.get(chapter_num, [])
        needed = [f"{s['label']}({s['type']})" for s in specs]
        return f"⚠️ 本章需要以下图片但用户未提供：{', '.join(needed)}。请在正文中标注【需补充：图片类型】"

    lines = ["## 📸 本章可用图片（请在正文对应位置插入标记）"]
    seen = set()
    for img in ch_images:
        label = img["label"]
        name = img["name"]
        path = img.get("path", "")
        # Include path so assembler can resolve the image directly
        marker = f"![{label}：{name}]({path})"
        if marker not in seen:
            lines.append(f"- {marker}")
            seen.add(marker)
    return "\n".join(lines)


def get_missing_images_prompt(catalog: Dict) -> str:
    """Generate a user-facing prompt listing all missing images."""
    missing = catalog.get("missing", [])
    if not missing:
        return ""
    lines = ["## ⚠️ 缺少以下图片资料，请补充上传："]
    for m in missing:
        lines.append(f"- 第{m['chapter']}章需要：{m['label']}（{m['type']}），还需 {m['need']} 张")
    return "\n".join(lines)
