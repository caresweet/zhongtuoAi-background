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
# 🔴 参照件《白马湖农场2号风电项目社会稳定风险评估报告》十四章结构：
#   - 位置示意图 / 勘测定界图 → 第6章「项目基本情况」6.1 项目概况
#   - 社会稳定风险评估流程图 → 第7章「稳评工作方案」7.2 评估方法
#   - 公示照片 / 现场勘察照片 / 座谈会照片 → 第8章「稳评方法与过程」8.4 风险调查过程
#   - 网络舆情搜索截图 / 专家评审意见 → 第9章「各利益相关者对项目的反映」9.2 各方意见汇总情况
#   - 其余图片（红线图/公告/勘测定界/预审意见/法人证明/签到表/问卷表/评审照片/评审签到/评审意见）
#     全部进附件（见 APPENDIX_IMAGE_CATEGORIES）
CHAPTER_IMAGE_SPECS = {
    # 第6章 项目基本情况（6.1 项目概况）
    6: [
        {"type": "map", "label": "项目位置示意图", "min": 0, "max": 2,
         "keywords": ["位置", "地理位置", "红线", "地图", "宗地", "示意"]},
        {"type": "survey_map", "label": "项目用地勘测定界图", "min": 0, "max": 2,
         "keywords": ["勘测定界图", "勘界图", "定界图", "勘测定界"]},
        {"type": "planning", "label": "控制性详细规划图", "min": 0, "max": 1,
         "keywords": ["控制性详细规划", "规划图", "工业单元", "详细规划"]},
    ],
    # 第7章 稳评工作方案（7.2 评估方法 → 流程图）
    7: [{"type": "flowchart", "label": "社会稳定风险评估流程图", "min": 0, "max": 1,
         "keywords": ["流程", "评估流程", "流程图", "步骤"]}],
    # 第8章 稳评方法与过程（8.4 风险调查过程）
    8: [
        {"type": "announcement", "label": "公示照片", "min": 0, "max": 4,
         "keywords": ["公示", "公告栏", "张贴"]},
        {"type": "photo", "label": "现场勘察照片", "min": 0, "max": 4,
         "keywords": ["现场", "勘查", "勘察", "踏勘", "地块", "临时用地"]},
        {"type": "meeting", "label": "群众座谈会照片", "min": 0, "max": 3,
         "keywords": ["座谈", "开会", "村民", "会议", "群众"]},
    ],
    # 第9章 各利益相关者对项目的反映（9.2 各方意见汇总情况 → 网络媒体舆论意见）
    # 🔴 只放「网络舆情搜索截图」。专家评审会照片 / 专家评审意见是**附件**材料
    #    （人工成品案卷的附件6、附件8），放进正文明细章就是同一张图出现两次
    #    （业主：注意图片不要重复）。
    9: [
        {"type": "sentiment", "label": "网络舆情搜索截图", "min": 0, "max": 3,
         "keywords": ["舆情", "搜索", "截图", "网络", "媒体", "百度"]},
    ],
}

# 🔴 稳评正文配图的**按类别兜底图注**（当某章没有匹配到规格、也无材料规则标签时用）。
#    这些名字全是稳评口径（「拟征收土地」「位置示意图（公告附图）」…），
#    **只对稳评有效** —— 按域取用统一走 `app.agent.chapter_catalog.image_caption`，
#    复垦域不会读到这张表（否则复垦图片会被盖上稳评图注）。
IMAGE_CAPTION_FALLBACK = {
    "announcement": "拟征收土地位置示意图（公告附图）",
    "photo": "现场调查照片", "meeting": "群众座谈会照片",
    "review": "专家评审会照片", "opinion": "专家评审意见",
    "survey": "调查表", "map": "拟征收地块位置示意图",
    "survey_map": "拟征收地块勘测定界图",
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
    # 第6章 项目基本情况（6.1 项目概况）
    (['位置', '地理位置', '红线', '勘测定界', '宗地', '地图', '勘界'], 6),
    (['控制性详细规划', '规划图', '工业单元', '详细规划'], 6),
    # 第7章 稳评工作方案（7.2 评估方法 → 流程图）
    (['流程', '评估流程', '流程图'], 7),
    # 第8章 稳评方法与过程（8.4 风险调查过程）
    (['公示', '公告栏', '张贴'], 8),
    (['现场', '勘查', '勘察', '踏勘', '地块', '临时用地'], 8),
    (['座谈', '开会', '村民', '会议', '群众'], 8),
    # 第9章 各利益相关者对项目的反映（9.2 各方意见汇总情况）
    (['舆情', '搜索', '截图', '网络'], 9),
    (['专家评审', '评审会', '专家意见', '综合意见', '评审签到'], 9),
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


# ═══════════════════════════════════════════════════════════════════════════════
# 🔴 项目材料图片 → (正文章节, 类别, 图注标签) 的**确定性**绑定
# ═══════════════════════════════════════════════════════════════════════════════
# 人工成品案卷（`稳评报告222/…案卷--最终.docx`）是版式与归属的唯一基准：
#   正文（四）风险调查过程 = 1.现场公示 / 2.实地勘察 / 3.座谈会 / （五）网络舆情搜索截图
#   其余材料（公告、勘测定界报告、签到表、问卷、专家评审件）**只进附件**。
#
# 🔴 为什么不用「关键词打分 + 配额竞争」：材料文件夹名本身就是最可靠的语义标签
#    （`公示照片4/`、`临时用地现场照片/`、`村民开会现场/`…），按文件夹判定是确定性的，
#    而按文件名猜测会把「座谈会签到表」当成「调查表」塞进正文——实测就是这么错的。
#
# 顺序即优先级：先专后泛，先匹配到的生效。
# (匹配词, 章节号或 None, category, 图注标签)
MATERIAL_IMAGE_RULES = [
    # ── 专家评审签到表（案卷附件7）：文件夹是「稳评专家意见扫描」，但内容不是意见 ──
    (['专家意见扫描_签到', '评审签到表', '专家签到'],  None, 'cert',        '专家评审签到表'),
    # ── 签到表：只进附件，绝不进正文 ──
    (['签到表'],                                    None, 'cert',         '签到表'),
    # ── 第8章 风险调查过程 ──
    (['公示照片', '公示栏', '张贴'],                  8,    'announcement', '公示照片'),
    (['临时用地现场照片', '地块现状', '勘察照片'],      8,    'photo',        '现场勘察照片'),
    (['村民开会现场', '开会现场'],                    8,    'meeting',      '群众座谈会照片'),
    # 🔴 业主口径（2026-09-20）：「现场景色照片就是走访照片」——
    #   🔴 「现场走访照片」必须**有人**（业主 2026-09-21：「现场走访照片也是要有人的」）。
    #      材料里 6 张有人像的照片全是会议室，只能按来源文件夹分成两组、互不重样：
    #        「群众开会照片」→ 现场走访调查；「村民开会现场」→ 座谈会。
    (['群众开会照片'],                              8,    'meeting',      '现场走访照片'),
    (['座谈会照片'],                                8,    'meeting',      '群众座谈会照片'),
    (['稳评问卷调查表', '问卷调查', '调查问卷', '问卷'], 8,    'survey',       '调查表'),
    # ── 第6章 项目基本情况（3.项目地理位置）──
    (['位置图11', '位置示意图', '拟征地位置'],        6,    'map',          '项目位置示意图'),
    (['勘测定界图', '勘界图', '定界图'],               None, 'survey_map',   '勘测定界报告'),
    # ── 第9章 网络媒体舆论意见 ──
    (['百度', '舆情', '搜索', '信息公开', '政府网站'],  9,    'sentiment',    '网络舆情搜索截图'),
    # 🔴 素材文件夹「图片-稳评（报告中」里除位置图外都是搜索截图（百度搜索、政府网站），
    #    文件名叫 `22.jpg` 这类纯数字，只能靠文件夹兜底。必须排在位置图规则**之后**。
    (['图片-稳评（报告中'],                          9,    'sentiment',    '网络舆情搜索截图'),
    # ── 专家评审件：只进附件（案卷附件6/7/8）──
    (['专家评审会照片', '评审会照片'],                 None, 'review',       '专家评审会照片'),
    (['综合意见', '专家意见', '评审意见'],             None, 'opinion',      '专家评审意见'),
]


def _basename_text(name: str, folder_hint: str = "") -> str:
    """用于材料规则匹配的文本：**最后一级文件夹名 + 文件名**。

    🔴 不能用整条路径：`/Users/mac/Downloads/稳评资料/公示照片4/xx.png` 里的
       `稳评资料` 会把所有图都带上「稳评」，反而干扰判定。
    """
    last = ""
    if folder_hint:
        parts = [p for p in str(folder_hint).replace('\\', '/').split('/') if p]
        if parts:
            last = parts[-1]
    return f"{last} {name or ''}".lower()


def _match_material_rule(name: str, folder_hint: str = ""):
    """材料图片 → (chapter_hint, category, label)；识别不出返回 (None, "", "")。"""
    t = _basename_text(name, folder_hint)
    for kws, ch, cat, label in MATERIAL_IMAGE_RULES:
        if any(k in t for k in kws):
            return (ch, cat, label)
    return (None, "", "")


# 🔴 PDF 抽取页命名：
#   `pdf_<文档名>_page<N>.png`      —— 整页渲染（含印章、版式，最完整）
#   `pdf_<文档名>_p<N>_img<K>.<ext>` —— 该页内嵌位图
# 同一页的两者是**同一内容的两版**，同时进附件就是「第1页附图 / 第1页」并排重复。
_PDF_PAGE_RE = re.compile(r'^pdf_(.+?)_page(\d+)\.\w+$', re.I)
_PDF_EMBED_RE = re.compile(r'^pdf_(.+?)_p(\d+)_img\d*\.\w+$', re.I)


def _pdf_page_of(name: str):
    """→ (文档名, 页码, 是否整页渲染) 或 None。"""
    n = str(name or '')
    m = _PDF_PAGE_RE.match(n)
    if m:
        return (m.group(1), int(m.group(2)), True)
    m = _PDF_EMBED_RE.match(n)
    if m:
        return (m.group(1), int(m.group(2)), False)
    return None


def _pdf_stem_key(stem: str) -> str:
    """PDF 文档名归一化 —— 同一份 PDF 可能被抽过多次，文件名只差一个 hash 后缀
    或把 〔〕 换成了下划线（`洪拟征告〔2026〕7号` vs `洪拟征告_2026_7号`），
    不归一化就会把同一页判成两页，附件里出现「第1页 / 第1页」两份。"""
    s = re.sub(r'_[0-9a-f]{6,}$', '', str(stem or ''), flags=re.I)
    return re.sub(r'[^0-9a-z一-鿿]+', '', s.lower())


def _pdf_page_rule(stem: str, page: int):
    """PDF 页 → (章节号, category, 图注标签)。

    🔴 「群众座谈会扫描.pdf」前 2 页是**座谈会签到表**，第 3 页起才是**稳评问卷调查表**
       —— 人工成品案卷附件「4.座谈会签到表 2 张 / 5.稳评问卷调查表 52 张」正好是这个切分。
       旧实现按文件名把整本都判成「调查表」，正文「现场走访调查」小节于是贴了两张签到表。
    """
    s = str(stem or '')
    if '位置示意图' in s:
        return (None, 'map', '拟征地位置示意图')
    if any(k in s for k in ('座谈', '会议', '纪要')):
        # 群众座谈会扫描件的固定装订顺序（江苏稳评案卷）：
        #   第 1–2 页 座谈会签到表 → 第 3–4 页 部门审批表 → 第 5 页起 稳评问卷调查表
        # 🔴 正文「现场走访调查」小节要的「调查表」是**问卷**那一档，不能拿签到表或
        #    审批表充数（实测就是把签到表当调查表贴了两张）。
        if page <= 2:
            return (None, 'cert', '座谈会签到表')
        if page <= 4:
            return (None, 'survey', '部门审批表')
        return (8, 'survey', '调查表')
    if any(k in s for k in ('问卷', '调查')):
        return (8, 'survey', '调查表')
    if any(k in s for k in ('公告', '预公告', '征告')):
        return (None, 'announcement', '拟征地公告')
    if any(k in s for k in ('勘测', '测定界', '定界')):
        return (None, 'survey_map', '勘测定界报告')
    if any(k in s for k in ('评审', '专家', '意见')):
        return (None, 'opinion', '专家评审意见')
    return (None, '', '')





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


def build_image_catalog(uploaded_files: List, ai_classifications: Optional[Dict] = None,
                        domain: str = "stability") -> Dict:
    """Scan all user-uploaded files and build a structured image catalog.

    Args:
        uploaded_files: List of file paths or dicts with path/original_name.
        ai_classifications: Optional AI vision classification results from deep_material_analyzer.
            Format: {"map": [path, ...], "announcement": [...], "photo": [...], ...}
        domain: 报告域。`章→图规格` 与 `图注` 均**按域取**（见
            `app.agent.chapter_catalog.chapter_image_specs` / `.image_caption`）——
            稳评(默认)逐字不变；复垦不复用稳评的图注。

    Returns: {
        "total": N,
        "by_chapter": {ch_num: [{"path": ..., "name": ..., "type": ..., "label": ...}, ...]},
        "missing": [{chapter, type, label}, ...],
        "catalog": [{path, name, category, display_name}, ...]
    }
    """
    # 🔴 章→图规格、图注、图片→章绑定一律按域取（收口在 chapter_catalog，
    #    不在此处写 `if domain==...`）。
    from app.agent import chapter_catalog as _catalog
    _specs = _catalog.chapter_image_specs(domain)

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

    # 🔴 先扫一遍**全部**文件名，记下「哪些 PDF 页已有整页渲染」。
    #    同一页的整页渲染与内嵌位图是同一内容的两个版本，同时进附件就是重复图；
    #    这里只保留整页渲染（含印章与版式，是完整原件）。
    _rendered_pages = set()
    for _it in all_paths:
        _nm = _it if isinstance(_it, str) else _it.get("original_name") or os.path.basename(_it.get("path", ""))
        _pp = _pdf_page_of(os.path.basename(str(_nm)))
        if _pp and _pp[2]:
            _rendered_pages.add((_pdf_stem_key(_pp[0]), _pp[1]))
    _seen_pages = set()   # 已收过的 (文档, 页码)：同一份 PDF 被抽多次只留一份

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

        # OCR 整页渲染排除（_full）；保留 公告/勘测定界/座谈会/问卷 的 _page 渲染
        # 作为附件扫描图（这些页本身即材料原件，如勘测定界页、盖章公告页）。
        if name.startswith('pdf_') and '_full' in name:
            continue
        if name.startswith('pdf_') and '_page' in name and not any(
                k in name for k in ('勘测定界', '预公告', '座谈会', '问卷', '调查')):
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

        # 🔴 同一 PDF 页已有整页渲染 → 丢掉内嵌位图版（否则附件出现「第N页附图 / 第N页」重复）；
        #    整页渲染与内嵌位图同时存在时**优先整页渲染**，所以这里连渲染件也要排在前面登记。
        _pp = _pdf_page_of(name)
        if _pp:
            _pkey = (_pdf_stem_key(_pp[0]), _pp[1])
            if _pkey in _seen_pages:
                continue
            if not _pp[2] and _pkey in _rendered_pages:
                # 🔴 这一页已有整页渲染 —— 丢掉内嵌位图版，**但不能登记 seen**：
                #    登记了后面那张整页渲染也会被当成重复一并丢掉，附件直接少一页。
                continue
            _seen_pages.add(_pkey)
        # 🔴 抽取器另存的「关键图」（pdf_X_<说明>.png）与整页渲染同源重复，一并丢掉
        if name.startswith('pdf_') and _pp is None:
            continue

        # Extract folder context from path
        folder_hint = ""
        if '/' in path or '\\' in path:
            parts = path.replace('\\', '/').split('/')
            folder_hint = '/'.join(parts[:-1])  # All folder parts as context

        # ── Classification priority ──
        # ① 材料规则 / PDF 页规则 —— 确定性判定，优先级最高（按域取，见 chapter_catalog）
        _rule_label = ""
        chapter_hint, cat, _rule_label = _catalog.material_image_rule(
            domain, name, folder_hint, pdf_page=_pp)

        # ② 规则没识别出来，才退回原来的「AI 视觉分类 + 文件名关键词」
        if not cat:
            ai_cat = ai_lookup.get(path, "")
            file_cat = _classify_filename(name, folder_hint)
            cat = ai_cat if (ai_cat and ai_cat != "other") else file_cat

            # 🔴 文件名给出了**更具体**的类型时，不让 AI 的粗分类覆盖它。
            #    （视觉模型一律把地图类判成 "map"，会把「勘测定界图」和「位置示意图」
            #      混成一类，两类图互相挤配额，勘测定界图就被挤去附件了。）
            _nl = name.lower()
            if any(k in _nl for k in ('勘测定界图', '勘界图', '定界图', '界址点图')):
                cat = "survey_map"
            elif any(k in _nl for k in ('流程图', '评估流程')):
                cat = "flowchart"

            # 🔴 图片→章绑定也按域取：复垦不借稳评那套写死 6/7/8/9 章的文件夹提示。
            chapter_hint = _catalog.image_chapter_hint(domain, name, folder_hint)

        display_name = _rule_label or _clean_display_name(name, cat)

        # Check folder structure for better display name
        if not _rule_label and ('/' in path or '稳评' in path):
            parts = path.replace('\\', '/').split('/')
            for part in parts:
                if any(kw in part for kw in ['公示', '评审', '座谈', '现场', '勘测', '公告', '走访', '问卷']):
                    display_name = part
                    break  # 🔴 Use first meaningful folder name, not last

        # 🔴 内容指纹：同一张图（不同路径/副本）只保留一份，根除重复
        fp = _image_fingerprint(path)
        if fp and any(c.get('fingerprint') == fp for c in catalog):
            continue

        catalog.append({
            "path": path,
            "name": name,
            "category": cat,
            "display_name": display_name,
            "rule_label": _rule_label,
            "fingerprint": fp,
            "chapter_hint": chapter_hint,
            # 🔴 材料规则命中却判定「只进附件」的图（公告、勘测定界报告、签到表…）
            #    必须**钉死**，不许被第二遍的「类别竞争」再抢进正文 ——
            #    实测勘测定界报告的前两页被判成第6章的「勘测定界图」塞进了正文。
            "pinned": bool(_rule_label),
        })

    # 🔴 Match to chapters with global dedup (each image used at most ONCE)
    used_paths = set()
    by_chapter = {}

    # 🔴 第一遍：有「文件夹→章节」确定性提示的图，直接绑定到对应章节，不参与竞争
    for img in catalog:
        hint = img.get("chapter_hint")
        if not hint or hint not in _specs:
            continue
        ch_images = by_chapter.setdefault(hint, [])
        # 找到该章节里 type 匹配的 spec，用于取 label
        specs = _specs[hint]
        # 优先挂到能匹配 category 的 spec；匹配不上就用第一个 spec 的 label
        matched = next((s for s in specs if s["type"] == img["category"]), None)
        # 🔴 图注**按域取**（稳评=材料规则标签 > 命中规格标签 > 按类别兜底表 > 首规格标签；
        #    复垦=命中规格图名，未命中则「章标题+示意图」）。见 chapter_catalog.image_caption。
        label = _catalog.image_caption(
            domain, hint, img["category"],
            rule_label=img.get("rule_label", ""),
            spec_label=(matched or {}).get("label", ""),
            first_spec_label=(specs[0]["label"] if specs else ""),
        )
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
    chapter_order = sorted(_specs.keys(),
                           key=lambda ch: sum(s["max"] for s in _specs[ch]))

    for ch_num in chapter_order:
        specs = _specs[ch_num]
        ch_images = by_chapter.setdefault(ch_num, [])
        for spec in specs:
            # 🔴 先算当前该 label 已绑定数（第一遍 hint 绑定 + 本遍已填），不超过 max
            already = sum(1 for x in ch_images if x["label"] == spec["label"])
            remaining = spec["max"] - already
            if remaining <= 0:
                continue
            # Find matching images NOT already used in another chapter
            available = [img for img in catalog
                         if img["path"] not in used_paths and not img.get("chapter_hint")
                         and not img.get("pinned") and (
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
    for ch_num, specs in _specs.items():
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
    # 🔴 勘测定界图单列一类：它是「勘测定界报告的附图」，
    #    与「位置示意图/红线图」是两种图，都要挂第1章 1.5，不能互相挤配额。
    if any(k in nl for k in ['勘测定界图', '勘界图', '定界图', '界址点图']):
        return "survey_map"
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




def get_missing_images_prompt(catalog: Dict) -> str:
    """Generate a user-facing prompt listing all missing images."""
    missing = catalog.get("missing", [])
    if not missing:
        return ""
    lines = ["## ⚠️ 缺少以下图片资料，请补充上传："]
    for m in missing:
        lines.append(f"- 第{m['chapter']}章需要：{m['label']}（{m['type']}），还需 {m['need']} 张")
    return "\n".join(lines)
