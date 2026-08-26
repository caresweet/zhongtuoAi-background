"""Image Catalog — dynamic image discovery, classification, and matching for report chapters.

Instead of fixed image positions, each chapter requests images by type/category,
and the catalog provides the best matches from user-uploaded materials.

Image naming priority:
  1. User-provided folder/name (e.g., "专家评审会照片/xxx.jpg")
  2. AI vision analysis classification
  3. Filename keyword fallback
"""

import os, re, logging, hashlib
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _image_fingerprint(path: str) -> str:
    """图片内容指纹（md5）：同内容不同文件名/路径判为重复。"""
    try:
        h = hashlib.md5()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


# Chapter image requirements: what each chapter needs
# 🔴 权威规范（DB32/T4013-2021 十章结构，依据官方稳评模板图片位置）：
#   - 位置示意图 → 第1章「决策基本情况」的三级标题「决策地理位置」，图注「决策地理位置」
#   - 风险评估流程图 → 第2章「评估过程」的「评估步骤」，图注「本决策风险评估流程图」
#   - 公示照片 → 第3章「各利益相关者对决策事项的反映」的「风险调查过程」
#   - 现场勘查照片 → 第3章「各利益相关者对决策事项的反映」的「实地勘察」（只取2张作示例）
#   - 现场座谈会照片 → 第3章「各利益相关者对决策事项的反映」的「座谈会介绍」
#   - 网络舆情搜索截图 → 第3章「网络媒体舆论意见」
#   - 工业单元控制性详细规划图 → 第4章「四性分析」
#   - 其余图片（红线图/公告/勘测定界/预审意见/法人证明/签到表/问卷表/评审照片/评审签到/评审意见）
#     全部进附件（见 APPENDIX_IMAGE_CATEGORIES）
CHAPTER_IMAGE_SPECS = {
    1: [{"type": "map", "label": "决策地理位置", "min": 0, "max": 1, "keywords": ["位置", "地理位置", "红线", "地图", "勘测定界", "宗地"]}],
    2: [{"type": "flowchart", "label": "本决策风险评估流程图", "min": 0, "max": 1, "keywords": ["流程", "评估流程", "流程图", "步骤"]}],
    3: [
        {"type": "announcement", "label": "公示照片", "min": 0, "max": 3, "keywords": ["公示", "公告栏", "张贴"]},
        {"type": "photo", "label": "现场勘查照片", "min": 0, "max": 2, "keywords": ["现场", "勘查", "勘察", "踏勘", "地块", "临时用地"]},
        {"type": "meeting", "label": "现场座谈会照片", "min": 0, "max": 3, "keywords": ["座谈", "开会", "村民", "会议", "群众"]},
        {"type": "sentiment", "label": "网络舆情搜索截图", "min": 0, "max": 1, "keywords": ["舆情", "搜索", "截图", "网络", "媒体"]},
    ],
    4: [{"type": "planning", "label": "控制性详细规划图", "min": 0, "max": 1, "keywords": ["控制性详细规划", "规划图", "工业单元", "HZ03", "详细规划"]}],
}

# 🔴 附件图片分类（权威规范：其余图片按此进附件）
APPENDIX_IMAGE_CATEGORIES = [
    ("附件1 征地红线图", ["红线", "红线图"]),
    ("附件2 拟征地公告", ["拟征地公告", "征收公告", "预公告"]),
    ("附件3 勘测定界报告", ["勘测定界", "勘测报告"]),
    ("附件4 建设项目用地预审与选址意见书", ["预审", "选址意见", "用地预审"]),
    ("附件5 法人证明及身份证组织机构代码证", ["法人", "身份证", "组织机构代码", "营业执照"]),
    ("附件6 座谈会签到表", ["座谈会签到", "签到表", "签到"]),
    ("附件7 稳评问卷调查表", ["问卷", "调查表", "调查问卷"]),
    ("附件8 专家评审会照片", ["评审会照片", "专家评审会"]),
    ("附件9 专家评审签到表", ["评审签到", "专家签到"]),
    ("附件10 专家评审意见", ["专家意见", "评审意见", "综合意见", "专家签字"]),
]

# 🔴 文件夹名/文件名 → 正文章节 的确定性绑定（治「图片放错」）。
# 顺序即优先级：先匹配到的生效。
FOLDER_CHAPTER_HINTS = [
    (['位置', '地理位置', '红线', '勘测定界', '宗地', '地图'], 1),
    (['流程', '评估流程', '流程图'], 2),
    (['公示', '公告栏', '张贴'], 3),
    (['现场', '勘查', '勘察', '踏勘', '地块', '临时用地'], 3),
    (['座谈', '开会', '村民', '会议', '群众'], 3),
    (['舆情', '搜索', '截图', '网络'], 3),
    (['控制性详细规划', '规划图', '工业单元', 'HZ03', '详细规划'], 4),
]


def _classify_chapter_hint(name: str, folder_hint: str = "") -> Optional[int]:
    """从文件夹名/文件名识别图片应放的正文章节（确定性绑定）。

    返回章节号；识别不出返回 None（进附件）。
    """
    nl = name.lower()
    fl = (folder_hint or "").lower()
    combined = f"{fl} {nl}"
    for keywords, ch in FOLDER_CHAPTER_HINTS:
        if any(k in combined for k in keywords):
            return ch
    return None





def _classify_appendix(name: str, folder_hint: str = "") -> str:
    """把「其余图片」归到附件分类（附件1-10，按权威规范）。

    返回附件名（如「附件3 勘测定界报告」）；识别不出返回「其他资料图片」。
    """
    nl = name.lower()
    fl = (folder_hint or "").lower()
    combined = f"{fl} {nl}"
    for label, kws in APPENDIX_IMAGE_CATEGORIES:
        if any(k in combined for k in kws):
            return label
    return "其他资料图片"


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

        # 🔴 过滤噪音：模板提取产物/公司资质，不是项目图片材料
        #   - doc_*_page_*：文档整页渲染（从 docx/PDF 拆出的每页截图）
        #   - company_P*：公司模板内嵌图
        #   - stability_cert_*：公司资质（由 _load_company_certificate_images 单独处理）
        if re.match(r'^doc_\d+_page_\d+', name, re.I):
            continue
        if name.startswith('company_'):
            continue
        if name.startswith('stability_cert_'):
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

        # 🔴 内容指纹：同一张图（不同路径/副本）只保留一份，根除重复
        fp = _image_fingerprint(path)
        if fp and any(c.get('fingerprint') == fp for c in catalog):
            continue

        # 🔴 文件夹名 → 章节 确定性绑定（治「放错」）
        chapter_hint = _classify_chapter_hint(name, folder_hint)

        catalog.append({
            "path": path,
            "name": name,
            "category": cat,
            "display_name": display_name,
            "fingerprint": fp,
            "chapter_hint": chapter_hint,
        })

    # 🔴 Match to chapters with global dedup (each image used at most ONCE)
    used_paths = set()
    by_chapter = {}

    # 🔴 第一遍：有「文件夹→章节」确定性提示的图，直接绑定到对应章节，不参与竞争
    for img in catalog:
        hint = img.get("chapter_hint")
        if not hint or hint not in CHAPTER_IMAGE_SPECS:
            continue
        ch_images = by_chapter.setdefault(hint, [])
        # 找到该章节里 type 匹配的 spec，用于取 label
        specs = CHAPTER_IMAGE_SPECS[hint]
        # 优先挂到能匹配 category 的 spec；匹配不上就用第一个 spec 的 label
        matched = next((s for s in specs if s["type"] == img["category"]), None)
        label = matched["label"] if matched else specs[0]["label"]
        # 不超过该 spec 的 max
        existing_of_label = sum(1 for x in ch_images if x["label"] == label)
        max_of_label = (matched or specs[0]).get("max", 3)
        if existing_of_label >= max_of_label:
            continue
        used_paths.add(img["path"])
        ch_images.append({"path": img["path"], "name": img["display_name"],
                          "type": img["category"], "label": label})

    # 🔴 第二遍：无提示的图走类别竞争兜底（维持原逻辑）
    # Process chapters with most specific requirements first
    chapter_order = sorted(CHAPTER_IMAGE_SPECS.keys(),
                           key=lambda ch: sum(s["max"] for s in CHAPTER_IMAGE_SPECS[ch]))

    for ch_num in chapter_order:
        specs = CHAPTER_IMAGE_SPECS[ch_num]
        ch_images = by_chapter.setdefault(ch_num, [])
        for spec in specs:
            # 🔴 先算当前该 label 已绑定数（第一遍 hint 绑定 + 本遍已填），不超过 max
            already = sum(1 for x in ch_images if x["label"] == spec["label"])
            remaining = spec["max"] - already
            if remaining <= 0:
                continue
            # Find matching images NOT already used in another chapter
            available = [img for img in catalog
                         if img["path"] not in used_paths and not img.get("chapter_hint") and (
                             img["category"] == spec["type"] or
                             (spec["type"] == "meeting" and img["category"] in ("photo", "review")) or
                             (spec["type"] == "photo" and img["category"] in ("photo", "other"))  # 🔴 Allow "other" as photo fallback
                         )]
            for m in available[:remaining]:
                used_paths.add(m["path"])
                ch_images.append({"path": m["path"], "name": m["display_name"],
                                  "type": spec["type"], "label": spec["label"]})

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

    # 🔴 附件图片：没进正文的图，归到附件1-10分类
    appendix = {}
    for img in catalog:
        if img["path"] in used_paths:
            continue
        label = _classify_appendix(img["name"], folder_hint="")
        appendix.setdefault(label, []).append(img)

    return {
        "total": len(catalog),
        "by_chapter": by_chapter,
        "missing": missing,
        "catalog": catalog,
        "appendix": appendix,
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
    if any(k in nl for k in ['红线图', '位置图', '规划图', '地形图', '宗地图', '勘测定界', '示意', '百度', '高德', '卫星图', '谷歌']):
        return "map"
    # Survey/questionnaire — specific document types
    if any(k in nl for k in ['问卷', '调查表', '统计表', '签到表', '测评表', '签到']):
        return "survey"
    # Expert review — specific meeting types
    if any(k in nl for k in ['专家意见', '专家签字', '专家组', '评审意见', '综合意见']):
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
    # 🔴 Meeting/座谈会照片（座谈/开会/会议/村民）
    if any(k in nl for k in ['座谈', '开会', '会议', '村民会', '群众会', '村民代表']):
        return "meeting"
    # 🔴 公司资质/证书
    if any(k in nl for k in ['执照', '证书', '资质', '备案']):
        return "cert"
    # Site photos — check after specific categories
    if any(k in nl for k in ['现场', '勘察', '地块现状', '临时用地', '房屋现状', '走访', '照片']):
        return "photo"
    # WeChat photos (least specific — catch remaining, vision 分类会进一步细分)
    if any(k in nl for k in ['微信图片', '图片']):
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
