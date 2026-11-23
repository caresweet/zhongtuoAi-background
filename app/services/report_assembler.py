"""ReportAssembler — generates the final DOCX from chapter content + images + data.

Called automatically by ChapterOrchestrator after all chapters are confirmed.
Replaces the old template-fill approach with direct DOCX construction.
"""

import os, re, logging, hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from lxml import etree
from app.agent.chapter_catalog import (
    all_chapters, total_chapters, chapter_title,
    has_review_table, appendix_chapter_no, appendix_kind,
    uses_company_archive_images,
    report_kind_name, cover_fallback_title,
    front_matter_labels,
    front_matter_order,
    cover_org_value,
    cover_date_parts,
    cover_bold,
    front_matter_gap_pt,
    has_questionnaire_tables,
    injects_audit_comments,
    report_form_chapter_no,
    request_letter_chapter_no,
    figures_chapter_no,
    body_image_anchor,
    typography,
    chapter_heading_text, chapter_title_own_page, is_chapter_heading_text,
    chapter_no_of_text,
)
from app.domains.registry import domain_of_state

logger = logging.getLogger(__name__)

CN_NUM = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}


def _set_vertical_alignment(sec, mode: str) -> None:
    """设置节的**页面垂直对齐**（`w:vAlign`）—— 实现章标题页上下居中。"""
    if sec is None:
        return
    try:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn as _qn
        spr = sec._sectPr
        for old in spr.findall(_qn('w:vAlign')):
            spr.remove(old)
        el = OxmlElement('w:vAlign')
        el.set(_qn('w:val'), 'center' if mode == 'center' else 'top')
        spr.append(el)
    except Exception as e:
        logger.warning(f"设置页面垂直对齐失败: {e}")


def _copy_page_setup(doc, sec) -> None:
    """把上一节的纸张/页边距/方向复制到新节，避免新节回到默认值。"""
    if sec is None:
        return
    try:
        prev = doc.sections[-2] if len(doc.sections) >= 2 else None
        if prev is None:
            return
        sec.page_width, sec.page_height = prev.page_width, prev.page_height
        sec.top_margin, sec.bottom_margin = prev.top_margin, prev.bottom_margin
        sec.left_margin, sec.right_margin = prev.left_margin, prev.right_margin
        sec.orientation = prev.orientation
    except Exception as e:
        logger.warning(f"复制节页面设置失败: {e}")


def _has_meaning(text: str) -> bool:
    """这一行是否有实义（含汉字/字母/数字）—— 只有纯标点、装饰符才算噪声。

    🔴 判据**不能是长度**。旧实现是 `len(clean) > 10 or _is_short_structural(clean)`
       （后者认「编号条目/（一）式标签」这类短结构行），两个洞：
         · 「1.客观公正性原则」（9 字）整行被删，正文只剩说明段；
         · 第7章落款「稳评责任单位（盖章）」（正好 10 字）与「年月日」被吞，
           「3.方案审核」后面直接接第八章。
       「不留空」是硬约束：宁可多留一行（人工一眼能看出），也不能悄悄少一行。
       LLM 噪声（「好的，遵照…」「***」等）在 `_add_chapter` 的显式过滤里已拦掉。
    """
    import re as _re
    return bool(_re.search(r'[0-9A-Za-z\u4e00-\u9fff]', text or ''))


def _num_or(v, default: float) -> float:
    """把字段值安全地转成数字；`【待补充】` / 空 / 非数字一律回落默认值。

    🔴 实测崩过：食品项目的 `support_rate` 是 `【待补充】`（问卷没解析出来）时，
       `float('【待补充】')` 抛 ValueError —— **整个组装阶段挂掉、报告一个字都产不出来**：

           ERROR report_workflow | assemble failed: could not convert string to float: '【待补充】'
           Pipeline done: 12 chapters, output=          ← 空

       这一处失守的代价不是「某个数不准」，是**交付物直接没了**，所以必须兜住。
    """
    s = (str(v if v is not None else "").replace("%", "")
         .replace(",", "").replace("，", "").strip())
    try:
        return float(s)
    except (TypeError, ValueError):
        return float(default)


def _int_or(v, default: int = 0) -> int:
    """`_num_or` 的整数版 —— `【待补充】` / 空 / 非数字一律回落默认值。

    与 `_num_or` 同因：字段里的占位符被直接 `int()` 会让组装阶段整个挂掉。
    """
    return int(_num_or(v, default))


def _cn_num(n: int) -> str:
    """阿拉伯数字 → 中文序号（1→一 … 12→十二，支持到 99）。"""
    n = int(n)
    if n <= 10:
        return ['', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'][n]
    if n < 20:
        return '十' + ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'][n - 10]
    return f"{n // 10}十" + (['', '一', '二', '三', '四', '五', '六', '七', '八', '九'][n % 10] if n % 10 else '')

_CN_DIGIT = '〇一二三四五六七八九'


def _add_page_break(doc) -> None:
    """加一个分页符；**紧邻上一个已是分页符时不再加**。

    🔴 前置件各块（封面/资质/简介/扉页）各自结尾都自带一个分页符，装配器在目录前
       又补一个 —— 两个挨在一起就是**一整页空白**。以前这个重复被「空段清理」顺手
       吃掉了（连正文该有的分页一起吃掉，见 `_post_generation_fix` 的注释），
       现在清理不再动分页符，就得在这里挡重复。
    """
    # 🔴 `sdt` 也要算进来：目录就是一个 sdt，不算它的话「目录后分页」会被误判成重复
    _kids = [c for c in doc.element.body.iterchildren()
             if c.tag.split('}')[1] in ('p', 'tbl', 'sdt')]
    if _kids:
        _last = _kids[-1]
        if any((b.get(qn('w:type')) or '') == 'page'
               for b in _last.findall('.//' + qn('w:br'))):
            return
    doc.add_page_break()


def _add_para_bottom_border(p) -> None:
    """给段落加一条**下边框**（＝填表横线，复垦扉页每行下面那条）。

    🔴 `w:pBdr` 在 `w:pPr` 里的位置有 schema 约束：必须排在 `numPr` 之后、
       `shd`/`tabs`/`spacing`/`ind`/`jc` 之前。位置放错，Word 会因校验失败
       **忽略整段 `w:pPr`**（`_fill_toc_cache` 里那条 `w:spacing` 踩过同样的坑）。
       所以这里按「第一个属于 pBdr 之后」的元素插在它前面，找不到才 append。
    """
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '8')          # 8/8 pt = 1pt 细线
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), '000000')
    bdr.append(bottom)
    _ins = None
    for _ch in pPr:
        if _ch.tag in _PPR_AFTER_PBDR:
            _ins = _ch
            break
    if _ins is not None:
        _ins.addprevious(bdr)
    else:
        pPr.append(bdr)


# `w:pPr` 里**排在 `w:pBdr` 之后**的元素（用于定位插入点，见 `_add_para_bottom_border`）
_PPR_AFTER_PBDR = frozenset(qn(_t) for _t in (
    'w:shd', 'w:tabs', 'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap',
    'w:overflowPunct', 'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN',
    'w:bidi', 'w:adjustRightInd', 'w:snapToGrid', 'w:spacing', 'w:ind',
    'w:contextualSpacing', 'w:mirrorIndents', 'w:suppressOverlap', 'w:jc',
    'w:textDirection', 'w:textAlignment', 'w:textboxTightWrap', 'w:outlineLvl',
    'w:divId', 'w:cnfStyle', 'w:rPr', 'w:sectPr', 'w:pPrChange',
))


def _cn_year_month(d) -> str:
    """日期 → 中文年月（2026-09 → 二〇二六年九月）。

    参照件封面落款写的是「二〇二六年八月」，不是「2026年8月」。
    """
    try:
        y = ''.join(_CN_DIGIT[int(c)] for c in str(int(d.year)))
        return f"{y}年{_cn_num(int(d.month))}月"
    except Exception:
        return ''


# ── 版式规范：**对齐参照件《白马湖农场2号风电项目社会稳定风险评估报告（送审稿）》** ──
# 参照件是带文字层的电子版，字号由 PyMuPDF 逐 span 实测得出（全篇一致）：
#   正文    SimSun        12.0pt（小四）      两端对齐
#   一级标题 SimSun        15.9≈16pt 不加粗   居中      （第三章评审表标题为 18pt）
#   二级标题 Times+宋体     15.0pt 加粗        靠左
#   三级标题 Times+宋体     14.1≈14pt 加粗     靠左
#   表名/图注 SimSun       12.0pt             居中
#   表内文字 SimSun/FangSong 10.4pt（五号）
#   图片    单张宽 15cm；两张并排各宽 7cm
# 注意：中西文混排的标题（二/三级）西文为 Times New Roman、中文为宋体，
#       需要分别设 `w:ascii` 与 `w:eastAsia`，故有 _set_run_font()。
FONT_BODY = '宋体'              # 正文
BODY_SIZE = Pt(12)             # 正文 小四 12pt
# 🔴 1级标题（章标题）**黑体 二号(22pt) 加粗 上下左右居中** —— 业主口径。
#    注释里原先写「宋体…业主口径」是按**参照件实测值**改的（参照件 1 号件的一级标题
#    确实是宋体三号），与业主后来的选择冲突，代码没跟上（业主：「为什么是宋体 三号」）。
FONT_H1 = '黑体'
H1_SIZE = Pt(22)                # 二号
H1_BOLD = True
FONT_H2 = '宋体'                # 2级标题 15pt 加粗 靠左
H2_SIZE = Pt(15)
FONT_H3 = '宋体'                # 3级标题 14pt 加粗 靠左
H3_SIZE = Pt(14)
FONT_TABLE = '宋体'             # 表格内容
TABLE_SIZE = Pt(10.4)          # 表格字号 五号 10.4pt
FONT_CAPTION = '宋体'           # 图名/表名 小四 12pt 居中
CAPTION_SIZE = Pt(12)
FONT_COVER_TITLE = '宋体'       # 封面标题（参照件封面同为宋体）
FONT_SONGTI = '宋体'            # 宋体（工作组/编制说明/目录等）
# 🔴 纯 AI 套话：「依据相关法律法规。」可以删；但「依据有关法律法规，制定本预案。」
#    后面接了实际动作/成果，是正文，不能删（实测 2026-11-12 把第5章 1.编制目的
#    整句吃掉，看起来像内容丢了）。
_AI_BOILERPLATE_RE = re.compile(
    r'(?:严格按照|根据|依据|按照)\s*(?:相关|有关)'
    r'(?:法律法规|规定|政策|文件|程序|流程|步骤|要求|标准|规范)'
    r'(?![^。\n]{0,80}(?:制定|编制|开展|建立|实施|签订|办理|执行|落实))'
    r'[^。\n]{0,80}[。]?'
)

FONT_ASCII = 'Times New Roman'  # 西文/数字字体（参照件正文数字用 Times）
FONT_PAGE_NUM = 'Times New Roman'  # 页码
FONT_DIGIT = 'Times New Roman'     # 数字字体
LINE_SPACING_PT = 20            # 行间距（随 12pt 正文，约 1.67 倍）
# 版心宽度 = 21 − 3.17×2 = 14.66cm；正文图片单张上限按参照件取 15cm 会略超，
# 故按版心收口到 14.6cm（参照件 77 张单图实测 15cm，其页边距略窄）。
MAX_IMAGE_W_CM = 14.6
# 🔴 章标题是否独占一整页（业主口径：是）。标题后立刻分页，正文从下一页开始。
CHAPTER_TITLE_OWN_PAGE = True
# 章标题页上下居中所需的空段数（版心 697pt ÷ 行高 20pt ÷ 2 ≈ 17）
CHAPTER_TITLE_LEAD_BLANKS = 16


def _set_run_font(run, ea_font: str, size=None, bold: bool = False,
                  ascii_font: str = None):
    """统一设置 run 的中西文字体/字号/加粗。

    🔴 python-docx 的 `run.font.name` 只写 `w:ascii`+`w:hAnsi`，中文要靠
    `w:eastAsia`。参照件的二/三级标题是「西文 Times + 中文宋体」混排，
    必须分开设，不能像旧代码那样把两者设成同一个字体名。
    """
    from docx.oxml.ns import qn as _qn
    ascii_font = ascii_font or ea_font
    run.font.name = ascii_font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(_qn('w:rFonts'))
    if rfonts is None:
        from docx.oxml import OxmlElement as _OE
        rfonts = _OE('w:rFonts'); rpr.insert(0, rfonts)
    rfonts.set(_qn('w:ascii'), ascii_font)
    rfonts.set(_qn('w:hAnsi'), ascii_font)
    rfonts.set(_qn('w:eastAsia'), ea_font)
    if size is not None:
        run.font.size = size
    run.font.bold = bold


_SIG_BOOKMARK_PREFIX = "_sig_line_"


def _mark_signature_paragraph(paragraph, idx: int) -> None:
    """给签字行打一个不可见书签，供图片去重识别（见 `dedupe_duplicate_images`）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn as _qn
    _id = str(9000 + int(idx))
    try:
        bs = OxmlElement("w:bookmarkStart")
        bs.set(_qn("w:id"), _id)
        bs.set(_qn("w:name"), f"{_SIG_BOOKMARK_PREFIX}{idx}")
        be = OxmlElement("w:bookmarkEnd")
        be.set(_qn("w:id"), _id)
        paragraph._p.insert(0, bs)
        paragraph._p.append(be)
    except Exception:
        pass


def _is_signature_paragraph(el) -> bool:
    """段落是否带签字行书签（签字占位图是同一张图，不能被当成重复插图删掉）。"""
    from docx.oxml.ns import qn as _qn
    for b in el.iter(_qn("w:bookmarkStart")):
        if str(b.get(_qn("w:name")) or "").startswith(_SIG_BOOKMARK_PREFIX):
            return True
    return False


def _match_tally(tallies: dict, label: str, option: str):
    """把 LLM 写的调查题目（label）映射到问卷真实勾选统计（tallies）里的题目。

    Returns 该选项的勾选数，匹配不到返回 None。
    """
    if not isinstance(tallies, dict) or not label or not option:
        return None

    # 关键词映射：LLM 题目 → 问卷真实题目的特征词
    keyword_map = [
        (['知晓', '了解'], ['了解']),
        (['支持', '态度'], ['支持', '态度']),
        (['年龄'], ['年龄']),
        (['职业'], ['职业']),
        (['身份', '您是', '本地', '租住'], ['您是']),
        (['诉求', '解决', '调解', '诉讼'], ['解决诉求', '方式解决']),
        (['反对'], ['反对']),
    ]

    matched_question = None
    for llm_kws, real_kws in keyword_map:
        if any(k in label for k in llm_kws):
            for q in tallies.keys():
                if any(k in q for k in real_kws):
                    matched_question = q
                    break
            if matched_question:
                break

    if not matched_question:
        return None

    opts = tallies.get(matched_question, {})
    if not isinstance(opts, dict):
        return None

    # 精确匹配选项
    if option in opts:
        return opts[option]
    # 模糊匹配选项（如"基本满意" vs "满意"）
    for opt_key, cnt in opts.items():
        if option in opt_key or opt_key in option:
            return cnt
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 附件图片细分子类（按**材料类型**归档，每类一小节 + 图片；图下**不写图号**）
# ═══════════════════════════════════════════════════════════════════════════════
# 🔴 业主口径（2026-09-18）：「附件中的图片不需要你写14-x，直接就是按照类型分类就好，
#    图片名称就使用他们的图片类型就行，比如 现场勘察图片、公示图、群众调查图」。
#    人工成品案卷的附件正是这么排的：按材料类型分节，图片直接排在小节下面，图下不加图号。
_ATTACHMENT_MAIN = [
    "专家评审全套材料（评审意见、签到表）",
    "拟征地公告",
    "勘测定界摘要",
    "现场调查图片",
]

# 每个主附件下的小节展示顺序（与 `_appendix_sub_label` 返回的小节名对应）
_ATTACHMENT_SUBS = [
    ["专家评审意见", "专家评审签到表", "专家评审会照片"],
    ["拟征地位置示意图", "拟征地公告"],
    ["勘测定界报告"],
    ["公示图", "现场勘察图片", "群众调查图", "座谈会照片",
     "座谈会签到表", "稳评问卷调查表"],
]

# 图库标签（正文图注用名）→ 附件小节名（按材料类型）
_APPENDIX_SUB_ALIAS = {
    '公示照片': '公示图',
    '现场勘察照片': '现场勘察图片',
    '现场走访照片': '现场勘察图片',
    '现场调查照片': '现场勘察图片',
    '群众调查图': '群众调查图',
    '群众座谈会照片': '座谈会照片',
    '调查表': '稳评问卷调查表',
    '部门审批表': '稳评问卷调查表',
    '座谈会签到表': '座谈会签到表',
    '签到表': '专家评审签到表',
    '专家评审签到表': '专家评审签到表',
    '拟征地公告': '拟征地公告',
    '拟征地位置示意图': '拟征地位置示意图',
    '勘测定界报告': '勘测定界报告',
    '专家评审会照片': '专家评审会照片',
    '专家评审意见': '专家评审意见',
}

_APPENDIX_SUB_TO_GROUP = {
    '专家评审意见': 0, '专家评审签到表': 0, '专家评审会照片': 0,
    '拟征地位置示意图': 1, '拟征地公告': 1,
    '勘测定界报告': 2,
    '公示图': 3, '现场勘察图片': 3, '群众调查图': 3, '座谈会照片': 3,
    '座谈会签到表': 3, '稳评问卷调查表': 3,
}


def _appendix_sub_label(img: dict) -> Optional[str]:
    """附件图片 → 小节名（按材料类型）；识别不出返回 None（进「其他项目资料」）。

    🔴 优先用图库给出的**确定性标签**（`display_name` 来自文件夹/PDF页规则），
       猜不出来才退回原来的关键词判断。
    """
    lab = str((img or {}).get("display_name") or "").strip()
    if lab in _APPENDIX_SUB_ALIAS:
        return _APPENDIX_SUB_ALIAS[lab]
    sub = _image_subtype((img or {}).get("category") or "",
                         (img or {}).get("name") or "")
    return sub[1] if sub else None


def _image_subtype(category, name):
    """图片 → (主附件序号, 小节名)；识别不出返回 None（进「其他项目资料」）。"""
    c = (category or "").lower()
    n = name or ""
    # ── 附件3 专家评审 ──
    if c == "review":
        if "意见" in n:
            return (0, "专家评审意见")
        if "签到" in n:
            return (0, "专家评审签到表")
        return (0, "专家评审会照片")
    if "评审" in n and "意见" in n:
        return (0, "专家评审意见")
    # ── 附件4 拟征地公告（公告文件本身，不含张贴公示照片）──
    if c == "announcement" and "公示" not in n:
        return (1, "拟征地公告")
    # ── 附件6 现场调查图片（按材料类型，先判断最具体）──
    if "公示" in n or "张贴" in n or "公告栏" in n:
        return (3, "公示图")
    if "勘测定界" in n or "勘测" in n or "测定" in n:
        return (2, "勘测定界报告")
    if c == "map":
        return (1, "拟征地位置示意图")
    if "签到" in n and "问卷" not in n:
        return (3, "座谈会签到表")
    if "问卷" in n or "调查表" in n or c == "survey":
        return (3, "稳评问卷调查表")
    if c == "meeting" or any(k in n for k in ("座谈", "开会", "会议", "村民", "群众会")):
        return (3, "座谈会照片")
    if c == "photo" or any(k in n for k in ("现场", "勘查", "勘察", "踏勘", "走访", "微信图片", "地块", "照片")):
        return (3, "现场勘察图片")
    return None


# 从 PDF 抽取页文件名推断来源文档名（不写死项目文号）
_PDF_DOC_LABELS = [
    (("勘测", "测定界", "定界"), "勘测定界报告"),
    (("公告", "预公告"), "征收土地预公告"),
    (("座谈", "会议", "纪要"), "座谈会记录"),
    (("问卷", "调查"), "问卷调查"),
    (("评审", "专家"), "专家评审意见"),
    (("风险评估", "稳评"), "社会稳定风险评估"),
]


def demote_deep_headings(md: str) -> tuple:
    """把**四级及更深**的编号标题降成正文引导「n）标题」。

    业主口径（2026-11-04）：「最多不要有四级标题，我更倾向于 1）2）3）这种」。
    复垦模板里 4.4.2.1～4.4.2.5 这种四段编号，不再做成标题，改成加粗引导行。
    返回 `(新文本, 处数)`。
    """
    n = 0

    def _rep(m):
        nonlocal n
        n += 1
        num, title = m.group(1), m.group(2).strip()
        return f"{num.rsplit('.', 1)[-1]}）{title}"

    out = re.sub(r'^#{2,6}\s*(\d+(?:\.\d+){3,})\s*(.+)$', _rep, str(md or ""),
                 flags=re.MULTILINE)
    return out, n


def align_headings_to_template(md: str, allowed: dict) -> tuple:
    """把章节标题对齐模板：编号在模板里的**改成模板标题**，不在的降为「n）」引导行。

    模型常自行加小节（实测：模板 3.1 项目简介下没有 3.1.5～3.1.7，
    5.1 下没有 5.1.2～5.1.6），也会把 5.1.1 写成别的名（模板是「复垦质量要求」，
    模型写成「清理工程标准」）。模板有画像时逐条对齐，成稿目录才和人工报告一致。
    `allowed` 为 `{编号: 模板标题}`。返回 `(新文本, 调整处数)`。
    """
    if not allowed:
        return md, 0
    n = 0
    _text = str(md or "")
    existing = set(re.findall(r'^#{2,6}\s*(\d+(?:\.\d+)+)\s', _text, flags=re.MULTILINE))
    title_to_no = {str(v).strip(): k for k, v in allowed.items() if str(v).strip()}

    def _rep(m):
        nonlocal n
        hashes, num, title = m.group(1), m.group(2), m.group(3).strip()
        want = str(allowed.get(num) or "").strip()
        if want:
            new_line = f"{hashes} {num} {want}"
            if new_line != m.group(0):
                n += 1
            return new_line
        # 编号不在模板里、但标题正好是模板某节 → 归位到模板编号
        _t = re.sub(r'^\d+(?:\.\d+)+\s*', '', title).strip()
        _want_no = title_to_no.get(_t)
        if _want_no and _want_no not in existing:
            existing.add(_want_no)
            n += 1
            return f"{hashes} {_want_no} {_t}"
        n += 1
        last = num.rsplit('.', 1)[-1]
        return f"{last}）{title.rstrip('：:').strip()}"

    out = re.sub(r'^(#{2,6})\s*(\d+(?:\.\d+)+)\s*(.+)$', _rep, str(md or ""),
                 flags=re.MULTILINE)
    return out, n


def strip_bare_structure_tables(md: str) -> str:
    """删掉第4章「无表题、表头是地类/复垦前后」的重复结构对比表。

    模型偶尔只写「表4-6」这一行当表题，`strip_md_tables_by_caption` 匹配不到
    标题关键词，最后与系统补渲的结构对比表印成两张（2026-11-05 实测）。
    """
    lines = str(md or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if re.match(r'^\*{0,2}表\s*4\s*[-—]\s*\d+\s*\*{0,2}$', s):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            hdr = lines[j].strip() if j < len(lines) else ""
            end = j
            while end < len(lines) and lines[end].strip().startswith('|'):
                end += 1
            if (hdr.startswith('|') and '地类名称' in hdr
                    and ('复垦前' in hdr or '复垦后' in hdr)):
                i = end
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def strip_md_tables_by_caption(md: str, keywords: tuple) -> str:
    """删掉 markdown 里「表题含 keyword」的整张表（表题行 + 紧随的表格块）。

    🔴 2026-11-02 线上实测：第4章「评价单元土地损毁情况表 / 复垦方向表」模型只写了
       表头和空行 —— 数据明明可以算。这两张改由系统出数，模型那张**必须删掉**，
       否则报告里同一张表出现两次（一张空、一张有数）。
    """
    lines = str(md or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        cap = re.sub(r'^\*{1,2}|\*{1,2}$', '', s).strip()
        if (re.match(r'^表\s*\d+[-—]\d+', cap)
                and any(k in cap for k in keywords)):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    # 🔴 必须保留原末尾换行：装配器的表题捕获依赖「行尾空行」触发 flush，
    #    去掉后最后一张表的 `pending_caption` 会丢（实测表3-1 不再被识别、整张印出来）。
    _tail = "\n" if str(md or "").endswith("\n") else ""
    return "\n".join(out) + _tail


def material_stem_key(name: str) -> str:
    """材料名归一（判断「PDF 与逐页图片是不是同一份材料」用）。

    `调查表_01.jpg` / `调查表_02.jpg` / `调查表.pdf` / `调查表_01_ab12cd34.jpg`
    全部归到 `调查表`；`临时用地合同1.jpg` 归到 `临时用地合同`。
    """
    s = os.path.splitext(os.path.basename(str(name or "")))[0]
    s = re.sub(r'[_\-\s]*[0-9a-f]{8,}$', '', s, flags=re.I)   # 落盘时追加的 hash
    s = re.sub(r'[_\-\s]*\d{1,3}$', '', s)                    # 末尾页号
    return s.strip().lower()


# 用户上传红线的「原始渲染」——与系统卫星位置图同义，正文只留系统图。
_RAW_REDLINE_SUFFIXES = (".shp.png", ".cad.png")


def split_raw_redline(imgs):
    """把一组图分成 (保留, 原始红线渲染)。

    系统卫星图存在时才调用；卫星图不在时用户红线就是唯一的图，不能丢。
    """
    imgs = list(imgs or [])
    raw = [i for i in imgs
           if str((i or {}).get("path", "")).endswith(_RAW_REDLINE_SUFFIXES)]
    return [i for i in imgs if i not in raw], raw


_LOCATION_LABEL_KEYS = ("位置示意图", "工程位置")


def is_location_figure(item) -> bool:
    """这张图是不是「位置示意图」类（系统图与用户/样本图同义，只能留一张）。"""
    if isinstance(item, dict):
        label = (item.get("label") or item.get("name")
                 or item.get("original_name") or item.get("path") or "")
    else:
        label = item
    text = str(label or "")
    return any(k in text for k in _LOCATION_LABEL_KEYS)


def drop_location_dupes(imgs):
    """系统位置图已生成时，把其他「位置示意图」类图摘掉。返回 (保留, 摘掉)。"""
    keep, drop = [], []
    for it in list(imgs or []):
        (drop if is_location_figure(it) else keep).append(it)
    return keep, drop


# 系统按界址点画出来的位置图固定落盘成这个名字（见 `reclamation_figures`）。
SYSTEM_LOCATION_NAME = "图3-1 工程位置示意图.jpg"


def is_system_location_figure(item, storage_dir) -> bool:
    """这张图是不是**系统生成**的位置示意图。

    只认 `storage/images/图3-1 工程位置示意图.jpg` 这个确定路径 —— 用户上传的
    同名文件也会有同样的 `original_name`，但真实路径在 uploads 下，不能靠文件名判。
    """
    p = str((item or {}).get("path") or "") if isinstance(item, dict) else ""
    if not p or os.path.basename(p) != SYSTEM_LOCATION_NAME:
        return False
    base = os.path.abspath(str(storage_dir))
    cand = p if os.path.isabs(p) else os.path.join(base, p)
    return os.path.abspath(cand) == os.path.join(
        base, "images", SYSTEM_LOCATION_NAME)


def prefer_user_location(imgs, storage_dir):
    """用户自己传了位置图 → 就用用户的，系统图和其他同名图一并摘掉。

    业主 2026-11-09：第3章印了两张一模一样的位置图（图3-1、图3-2）。
    材料/用户既然给了图，就以用户的为准，只留一张。
    返回 (保留, 摘掉, 是否采用了用户图)。
    """
    imgs = list(imgs or [])
    user_loc = [i for i in imgs
                if is_location_figure(i) and not is_system_location_figure(i, storage_dir)]
    if not user_loc:
        return imgs, [], False
    pick = user_loc[0]
    keep = [i for i in imgs if (not is_location_figure(i)) or i is pick]
    drop = [i for i in imgs if i not in keep]
    return keep, drop, True


def drop_raw_redline_from_ch3(by_chapter) -> int:
    """系统卫星图存在时，把原始红线渲染从**第3章正文图位**摘掉（附件仍保留）。

    图片目录的正文配额是「每类最多 2 张、按输入顺序取」——用户上传的两张
    原始红线会先把位置图名额吃光，系统卫星图被挤掉，正文只剩白底红线图。
    返回摘掉的张数。
    """
    if not by_chapter:
        return 0
    key = 3 if 3 in by_chapter else ("3" if "3" in by_chapter else None)
    if key is None:
        return 0
    keep, raw = split_raw_redline(by_chapter.get(key))
    if raw:
        by_chapter[key] = keep
    return len(raw)


class ReportAssembler:
    """Assembles generated chapter markdown into a properly formatted DOCX report."""

    # 🔴 报告域缺省值。装配时由 `assemble()` 按 state 覆盖；单章导出
    #    （scripts/export_chapter.py 直接调 `_add_chapter`）不设时回落稳评，
    #    保证「不传域 = 稳评」的旧行为。
    _domain = "stability"

    def __init__(self, storage_dir: Optional[Path] = None):
        from app.config import settings
        self.storage_dir = storage_dir or settings.STORAGE_DIR
        self.images_dir = self.storage_dir / "images"
        self.company_dir = self.storage_dir / "extracted_imgs"
        self.output_dir = self.storage_dir / "generated"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._inserted_images = set()  # 🔴 Global dedup tracker

    # ═══════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════

    def assemble(self, state: dict) -> str:
        """Build the final report DOCX from session state."""
        self._inserted_images = set()  # 🔴 Reset per call — avoid cross-report dedup
        # 人员名单缓存：未勾选时随机取 5 名项目组成员（技术负责人保留），全篇共用
        # （工作组名单 / 第7章名单 / 参与人员签字 / 证书页四处的名单必须一致）
        self._team_people = None
        self._cert_names = None
        self._used_template_certs: set = set()
        self._missing_images = []      # 资料里缺失的图片（只记缺口，不印占位）
        self._chapter_image_counter = {}  # 🔴 图号统一计数器：章 → 已插入图数
        self._session_state = state  # 🔴 供系统标准表（表3-1/表3-3）渲染读取 survey_analysis
        self._system_rendered_tables = set()  # 🔴 系统渲染的标准表，_fill_table_data 不得覆盖
        # 🔴 这些表的空白格**保持空白**（表4-2 的逐个地块/合计行 —— 模板本来就是空格，
        #    填「—」会把「地块面积」栏弄得像缺数）
        self._keep_blank_tables = set()
        # 🔴 系统补渲表的**完整表题**（复垦第4章分析表）——`_dedup_table_titles`
        #    对它们**不重编号**：那几张的表号是按样本定的（表4-8/4-11/4-12/4-13），
        #    重编成 表4-1… 之后与样本对不上（复审的人按样本查表号）。稳评恒为空集。
        self._system_caption_exact: set = set()
        # 🔴 完整表题（去掉「表N-M」前缀）→ 系统定死的表号。模型照题名手写了同一张表、
        #    但编号写错时（表4-11 写成 表4-1），按题名找回系统表号，不再参与顺序重编。
        #    2026-11-03 实测：表土资源平衡被编成 表4-1、复垦前后对比被编成 表4-3。
        self._system_caption_by_title: dict = {}
        # 🔴 已被系统表**占号**的章号→表号（如 4 → {8, 11, 12, 13}）：LLM 手写表
        #    重编号时跳过这些号，免得占掉样本表号。稳评恒为空字典。
        self._system_caption_nums: dict = {}
        self._rendered_extracted_tbls = set()  # 🔴 已就地渲染的 PDF 提取表（表1-1/4-1），防重复
        # 🔴 已印出的表号（`6-1` 这种）—— 同表号第二次出现一律判重，
        #    防止「系统按标记渲染一张 + 模型又手写一张」（实测 2026-10-28 表6-1 重复且错列）
        self._rendered_captions: set = set()
        # 已渲染系统表的表头签名 [(章号, frozenset(去单位表头), 行数)] —— 无表题重复表判重的依据
        self._rendered_sigs: list = []
        # 🔴 这张计数器必须按次清零：界面走的是**全局单例** `report_assembler`，
        #    不清零的话**本进程内第二份报告**的表号会接着上一份往后排
        #    （表6-1 变表6-3），第一份正常、后面全错 —— 脚本里每次新建实例，
        #    所以本地怎么测都测不出来。
        self._ch_table_seq = {}
        self._cur_section_name = ""
        self._pending_section_imgs = {}
        self._defer_imgs = None
        self._defer_empty_section = False
        self._pending_section_trigger = None
        self._ch8_subimg = {}
        self._ch8_cur_sub = None
        self._pending_map_flush_on_next = False
        self._table_issues = []  # 🔴 表格兜底发现的异常（供审核感知）
        session_id = state.get("session_id", "report")
        chapters = state.get("chapters", {})
        # 🔴 章号键统一成 int：工作流内存里是 `{1: {...}}`，但**会话落盘（JSON）再读回来
        #    就成了 `{"1": {...}}`** —— 装配器全程按 int 章号 `chapters.get(4)` 取值，
        #    字符串键一个都取不到，于是出来的 Word **只有标题、封面和配图、正文全空**，
        #    且不抛任何异常（实测 2026-10-28：23MB、106 张图、正文 1600 字）。
        #    归一化放在入口，一次解决所有回调读。
        if isinstance(chapters, dict):
            _norm = {}
            for _k, _v in chapters.items():
                try:
                    _norm[int(_k)] = _v
                except (TypeError, ValueError):
                    _norm[_k] = _v
            chapters = _norm
        filled = state.get("filled_data", {})
        # `_render_md_table` 要按域改渲染（复垦表4-2）时用它取事实卡数据
        self._filled = filled
        # 第7章投资估算要回头读**第6章**的工程量表，所以整份章节也留一份
        self._chapters = chapters
        # 🔴 模板版式画像：标题/正文/表格的字体字号从模板 docx 反读，
        #    读不到才回落到按域常量（业主 2026-11-04：「能不能自己识别」）。
        self._tpl_style = None
        _tp = str(state.get("template_path") or "")
        if _tp:
            try:
                from app.services.template_style_profile import load_style_profile
                _abs_tp = _tp
                if not Path(_abs_tp).exists():
                    from app.services.file_service import file_service
                    _abs_tp = str(file_service.get_absolute_path(_tp))
                self._tpl_style = load_style_profile(_abs_tp)
            except Exception as e:
                logger.info(f"[版式] 模板画像读取跳过: {e}")

        # 🔴 报告域：决定本报告按哪个域的章节目录装配（章号/标题/是否有评审表章/
        #    是否有附件章）。缺省回落稳评 —— 老调用点（脚本）不传 `_domain` 时
        #    行为与改动前完全一致。
        self._domain = domain_of_state(state)
        # 🔴 章标题版式按模板实测：有的模板章标题独占一页，有的标题只是正文页第一行
        #    （2026-11-23：洪泽第四章、淮阴第三/十章、金湖与淮安市全部章都是同页）。
        #    这里先把模板结构解析出来，`_add_chapter_title_page` 按系统章号取用。
        self._tpl_plan = None
        if self._domain == "stability" and _tp:
            try:
                from app.services.template_structure import extract_plan as _extract_plan
                _abs_plan = _tp
                if not Path(_abs_plan).exists():
                    from app.services.file_service import file_service
                    _abs_plan = str(file_service.get_absolute_path(_tp))
                self._tpl_plan = _extract_plan(_abs_plan)
            except Exception as e:
                logger.info(f"[模板结构] 版式解析跳过: {e}")
        # 🔴 复垦第4章的分析表由系统出数（见 `reclamation_ch4_tables`）——
        #    模型写的同题表先把整张删掉，否则会出现一张空表 + 一张系统表。
        #    ⚠️ 必须放在 `self._domain` 赋值**之后**（之前取到的是默认 stability）。
        #    🔴 删除清单按**本次真正能算出来的表**动态生成（2026-11-04 实测：
        #       写死三张时，模型的「表土资源平衡表 / 复垦方向表」没被删，又因表号
        #       撞上系统表被 `_caption_key` 判重丢弃 —— 两边都不出，整张表消失）。
        if self._domain == "reclamation":
            _strip_titles = ("临时用地土地利用现状统计表",
                             "评价单元土地损毁情况表", "评价单元复垦方向表")
            _built4 = []
            try:
                from app.services.reclamation_ch4_tables import build_ch4_tables
                _built4 = build_ch4_tables(
                    filled, title=str(state.get("report_title") or ""),
                    plan=self._template_ch4_plan())
                _extra = tuple(
                    re.sub(r'^表\s*\d+\s*[-—]\s*\d+\s*', '',
                           str((t or {}).get("caption") or "")).strip()
                    for _, t in _built4)
                _strip_titles = tuple(dict.fromkeys(
                    [t for t in _extra if t] + list(_strip_titles)))
            except Exception as e:
                logger.info(f"[表4-x] 动态删除清单生成失败，用默认三张: {e}")
            for _k, _ch in (chapters or {}).items():
                if isinstance(_ch, dict) and _ch.get("markdown"):
                    _ch["markdown"] = strip_md_tables_by_caption(
                        _ch["markdown"], _strip_titles)
                    if str(_k) == "4":
                        _ch["markdown"] = strip_bare_structure_tables(_ch["markdown"])
                    # 🔴 第8章「表8-1 费用安排表」：模型常把**模板的旧数字**（14.35/4.58/
                    #    115.91）整张抄过来。系统能按本次投资估算出表时，先把模型那张删掉
                    #    （业主 2026-11-05：表里的数必须跟着本项目走）。
                    if str(_k) == "8" and (filled or {}).get("_cost_workbook"):
                        _ch["markdown"] = strip_md_tables_by_caption(
                            _ch["markdown"], ("土地复垦费用安排表",))
                    # 🔴 第7章同理：上传了投资估算 Excel 时，表7-3/7-4/7-5 由系统按 Excel 出，
                    #    模型手写的那几张（数字来自范文）整张删掉。
                    if str(_k) == "7" and (filled or {}).get("_cost_workbook"):
                        _ch["markdown"] = strip_md_tables_by_caption(
                            _ch["markdown"],
                            ("工程施工费估算表", "其他费用估算表", "土地复垦投资估算表"))
            # 专家批注修补：单位、断号/重号、重复段、服务年限与管护期、复垦方向与面积、
            # 剥离厚度、管护费/动态投资、缺失的表3-1、表图编号等
            try:
                from app.services.reclamation_content_repairs import (
                    repair_reclamation_chapters)
                # 「土地利用现状」系统表的本次实际编号 —— 第3章「见表3-1」要指到它
                _land_cap = ""
                try:
                    _land_cap = next(
                        (re.sub(r'\s+', '', str((t or {}).get("caption") or ""))
                         for _, t in (_built4 or [])
                         if "土地利用现状" in str((t or {}).get("caption") or "")), "")
                except Exception:
                    _land_cap = ""
                _rlogs = repair_reclamation_chapters(
                    chapters or {}, filled or {}, land_table_caption=_land_cap,
                    known_captions={4: tuple(
                        str((t or {}).get("caption") or "") for _, t in (_built4 or []))})
                if _rlogs:
                    logger.info("[复垦修补] %s", "；".join(_rlogs))
            except Exception as _re:
                logger.warning(f"[复垦修补] 跳过: {_re}")
        _chapters = all_chapters(self._domain)   # 本域章号列表
        _total = total_chapters(self._domain)    # 本域总章数

        # 🔴 稳评总分跨章一致性：第12章是自己算的表（如 W=0 分），第13章却常从参照范文里
        #    抄一个**别的项目**的「综合风险指数 12.5 分」。以第12章结论值为准改写。
        #    放在装配入口而不是工作流节点里 —— 脚本/离线重装配也走同一份代码，不会漏。
        if self._domain == "stability":
            try:
                # 专家批注中的硬伤统一修补（面积分项、风险条数、日期先后、
                # 伪造法规名、错位句、得分公式、附件指向）
                from app.services.report_content_repairs import repair_stability_chapters
                _repairs = repair_stability_chapters(
                    chapters, state=getattr(self, "_session_state", None))
                if _repairs:
                    logger.info("[内容修补] %s", "；".join(_repairs))
                from app.validation.content_guardrails import (
                    fix_risk_score_consistency, strip_chapter_self_reference)
                chapters, _score_fixes = fix_risk_score_consistency(chapters)
                if _score_fixes:
                    logger.info("[风险指数] 统一 %d 处：%s", len(_score_fixes),
                                "；".join(f"第{n}章 {o}→{v}" for n, o, v in _score_fixes[:6]))
                chapters, _selfref = strip_chapter_self_reference(chapters)
                if _selfref:
                    logger.info("[章号自述] 去掉正文里的「第X章…」表述 %d 处", _selfref)
                # 表12-4 必须与第11章的风险条目一一对应（实测只有 5 行、风险名还是自拟的）
                from app.validation.content_guardrails import rebuild_risk_level_table
                if rebuild_risk_level_table(chapters):
                    logger.info("[表12-4] 与第11章风险条目不一致，已按小节重排")
                # 表4-1 同样以第11章风险清单为准（模板表写死六类，常与第11章对不上）
                from app.validation.content_guardrails import rebuild_risk_overview_table
                if rebuild_risk_overview_table(chapters):
                    logger.info("[表4-1] 已按第11章风险清单重建")
                # 表13-1 的「风险类型」列同理，措施/责任主体沿用原行
                from app.validation.content_guardrails import rebuild_risk_measures_table
                if rebuild_risk_measures_table(chapters):
                    logger.info("[表13-1] 已按第11章风险清单重排")
            except Exception as _se:
                logger.warning(f"[风险指数] 一致性修正跳过: {_se}")

        # 🔴 全文跨章一致性（业主 2026-11-16）：面积/户数/问卷/支持率/总投资统一到事实卡；
        #    四至/公告期/运距按多章多数一致；改不了的记入清单。
        try:
            from app.services.consistency_check import check_and_fix
            _c_issues, _c_fixes = check_and_fix(
                chapters or {}, filled or {}, self._domain)
            self._consistency_issues = _c_issues
            if _c_fixes:
                logger.info("[一致性] %s", "；".join(_c_fixes))
            if _c_issues:
                logger.warning("[一致性] 仍有 %d 处需人工确认：%s", len(_c_issues),
                               "；".join(i.get("message", "") for i in _c_issues[:5]))
        except Exception as _cce:
            logger.warning(f"[一致性] 检查跳过: {_cce}")

        # 🔴 复垦：先从上传的 Shapefile 算**项目中心经纬度**，供正文坐标校正用
        #    （模型会把「33°16′33.45″」写成 `33163345` 这种连串）。
        if self._domain == "reclamation" and not filled.get("_centroid_lat"):
            try:
                from app.config import settings as _settings
                from app.services.reclamation_figures import shapefile_polygons
                from app.services.satellite_map import points_to_lonlat
                for _f in (state.get("_uploaded_files") or []):
                    _p = _f.get("path") if isinstance(_f, dict) else _f
                    if not str(_p).lower().endswith(".shp"):
                        continue
                    _polys = shapefile_polygons(str(_p), str(_settings.STORAGE_DIR))
                    if _polys:
                        _ll = points_to_lonlat(_polys[0]["points"])
                        filled["_centroid_lon"] = round(sum(x for x, _ in _ll) / len(_ll), 6)
                        filled["_centroid_lat"] = round(sum(y for _, y in _ll) / len(_ll), 6)
                        logger.info(f"[坐标] 项目中心 {filled['_centroid_lat']}, "
                                    f"{filled['_centroid_lon']}")
                        break
            except Exception as _ce:
                logger.warning(f"[坐标] 计算项目中心失败（跳过坐标校正）: {_ce}")

        # 🔴 正文去重（两域通用）：模型偶尔把同一句/同一段连写两遍
        #    （实测：「…涉及户数50户。50户。补偿方案制定应…」）。只删逐字重复与秃噜嘴的残句。
        # 🔴 顺带断长句：交付体检实测「60 字以上长句」占了不合格段落的绝大多数
        #    （稳评 402 段里 81 段、复垦 432 段里 84 段）。装配前确定性断一次，
        #    断点只取引号/括号外的中文逗号，前后各留 ≥15 字，一句最多断两处。
        try:
            from app.validation.content_guardrails import polish_chapters
            # 🔴 范文残留清理要拿「库内所有范文正文」当比对源：
            #    范文里的数字/地名/文号若没被用户资料覆盖，换成用户值或「待补充」。
            _sample = ""
            try:
                from app.services.template_section_library import sample_corpus
                _sample = sample_corpus(self._domain or "stability")
            except Exception as _se:
                logger.warning(f"[范文残留] 取范文语料失败（跳过清理）: {_se}")
            _user_text = str(state.get("_pdf_raw_text") or "")
            if not _user_text:
                _user_text = "\n".join(
                    str((m or {}).get("text_content") or "")
                    for m in (state.get("_project_materials") or [])
                    if isinstance(m, dict))
            chapters, _polish = polish_chapters(
                chapters, self._domain, filled=filled,
                sample_text=_sample, user_text=_user_text)
            if _polish.get("dedup"):
                logger.info("[去重] 去掉相邻重复句/段 %d 处", _polish["dedup"])
            if _polish.get("split"):
                logger.info("[断句] 拆开超长句 %d 处", _polish["split"])
            if _polish.get("scrub"):
                logger.info("[范文残留] 替换为项目数据/「待补充」%d 处", _polish["scrub"])
        except Exception as _de:
            logger.warning(f"[去重/断句] 跳过: {_de}")

        # 🔴 用户在「选择报告人员」多选框里勾选的人员姓名（决定前置工作组名单 + 培训证书取图）
        #    优先级：会话 state → filled_data 透传（脚本生成走这条）→ 空表示"未选，用全部在职人员"
        try:
            _sel = state.get("selected_personnel") or (
                filled.get("_selected_personnel") if isinstance(filled, dict) else None) or []
            self._selected_personnel = [str(x).strip() for x in _sel if str(x).strip()]
        except Exception:
            self._selected_personnel = []

        # 🔴 「选择证书」是与工作组**分开**的另一个多选框（最多 3 人）：
        #    决定前置「公司资质证书」区（营业执照之后）贴哪几张人员证书图。
        #    未传则回退为「工作组里持证的人」，保持旧行为。
        try:
            _csel = state.get("selected_cert_personnel") or (
                filled.get("_selected_cert_personnel") if isinstance(filled, dict) else None) or []
            self._selected_cert_personnel = [str(x).strip() for x in _csel if str(x).strip()][:3]
        except Exception:
            self._selected_cert_personnel = []

        # 🔴 公司档案（由工作流在异步环节读好传进来）。
        #    空 dict 表示「这个账号没有公司信息」→ 报告里一律显示「待填写」，
        #    **绝不回落成江苏众拓**（那等于把别人的客户抬头印成我们的）。
        self._company = state.get("_company") or {}
        # 🔴 已解析材料（含 source_name / text_content）—— 复垦附件按 `source_name` 把
        #    「勘测定界报告.pdf」这类原件归到对应附件项，再整页渲染进附件区。
        self._project_materials = state.get("_project_materials") or []

        # Debug: log chapter structure to find dict-in-string bugs
        for ch_num, ch_data in chapters.items():
            if isinstance(ch_data, dict):
                md = ch_data.get("markdown", "")
                if not isinstance(md, str):
                    logger.error(f"Chapter {ch_num} markdown is {type(md).__name__}, not str!")
                    ch_data["markdown"] = str(md) if md else ""
        # 🔴 正文为空必须**报警**：2026-10-28 稳评那次的症状是正文一个字都没进来，
        #    装配器照常出稿 —— 一份 23MB、106 张图、正文只有 1600 字的 Word 文件，
        #    没有任何一处日志说「正文没进来」，等于把坏稿当成品交出去。
        #    键可能是 int（工作流内存里）也可能是 str（JSON 落盘再读回来），两种都认。
        _md_by_no = {}
        for _k, _v in (chapters or {}).items():
            try:
                _md_by_no[int(_k)] = str((_v or {}).get("markdown") or "") if isinstance(_v, dict) else ""
            except (TypeError, ValueError):
                continue
        _empty_chs = [n for n in _chapters if not _md_by_no.get(n, "").strip()]
        if chapters and len(_empty_chs) >= max(2, len(_chapters) // 2):
            logger.error(f"[装配] 章节正文为空 {len(_empty_chs)}/{len(_chapters)} 章："
                         f"{_empty_chs} —— 报告将只剩标题与配图，请查上方各节点日志")

        doc_ref = filled.get("doc_reference", "")
        # 责任单位：尝试多个字段名（不同数据源用不同key）
        org_name = (
            filled.get("org_name", "") or
            filled.get("decision_unit", "") or
            filled.get("responsible_unit", "") or
            filled.get("稳评责任单位", "") or
            filled.get("责任单位", "")
        )
        location = filled.get("location", "") or filled.get("land_location", "")
        # 🔴 清洗视觉分析 dict/JSON 混入的 location（{'general_area': ...} 等），防止封面/正文泄漏
        if location and str(location).strip().startswith("{"):
            location = ""
        project_name = filled.get("project_name", "")

        image_files = self._prepare_image_catalog(state)
        survey_stats = self._get_survey_stats(state)

        # Store original names in filled for appendix categorization
        orig_names = state.get("_image_original_names", {})
        if orig_names and isinstance(filled, dict):
            filled["_image_original_names"] = orig_names

        # 🔴 强制修正文号：把错误的"XX征告〔XXXX〕N号"统一为 doc_reference 的文号
        correct_doc = str(filled.get("doc_reference", "") or "")
        if correct_doc:
            doc_num_match = re.search(r'(\d+)\s*号', correct_doc)
            if doc_num_match:
                correct_num = doc_num_match.group(1)
                for ch_num in _chapters:
                    ch_data = chapters.get(ch_num, {})
                    if isinstance(ch_data, dict) and ch_data.get("markdown"):
                        md = ch_data["markdown"]
                        # 只替换同族项目文号，不碰法规文号（苏政发/发改投资等）
                        # 🔴 前缀完全由真实文号派生，不写死任何字（如"拟征告"/"洪"）
                        _prefix = re.sub(r'\s*[〔（(\[]?\d{4}[〕）)\]]?\s*\d+\s*号\s*$', '', str(correct_doc)).strip()
                        _tail = _prefix[-3:] if len(_prefix) >= 3 else _prefix
                        if _tail:
                            md = re.sub(
                                rf'[一-龥]{{0,4}}{re.escape(_tail)}\s*[〔（(\[]\d{{4}}[〕）)\]]\s*'
                                rf'(?!{re.escape(correct_num)}\s*号)\d+\s*号',
                                str(correct_doc),
                                md,
                            )
                        ch_data["markdown"] = md

        # 🔴 第4章预处理（签字表改系统标记 + 删 LLM 写的单位/时间行）
        #    与单章导出共用同一函数，避免两条路径不一致。
        try:
            from app.agent.chapter_definitions import prepare_chapter4_markdown
            _c4 = chapters.get(4, {})
            if isinstance(_c4, dict) and _c4.get("markdown"):
                _c4["markdown"] = prepare_chapter4_markdown(_c4["markdown"])
        except Exception as _e4:
            logger.debug(f"第4章预处理跳过: {_e4}")

        # 🔴 第6章预处理：「（一）项目概况」按**固定骨架**重排 —— 标题由系统写死，
        #    模型写的 `####` 小标题只用来判断内容归属。业主：「能保证骨架和标题
        #    对上号么」→ 方案 A：把标题从模型手里拿走（光靠提示词锁不住，实测模型
        #    照样起「1.项目名称」「2.项目相关单位」）。
        try:
            from app.agent.chapter_definitions import (
                prepare_chapter6_markdown, chapter6_answer_issues,
            )
            _c6 = chapters.get(6, {})
            if isinstance(_c6, dict) and _c6.get("markdown"):
                _c6["markdown"] = prepare_chapter6_markdown(_c6["markdown"])
                _i6 = chapter6_answer_issues(_c6["markdown"])
                if _i6:
                    self._table_issues.extend(_i6)
                    logger.warning(f"[第6章] {len(_i6)} 条概况项疑似写成了段落（超 "
                                   f"{len(_i6)} 处），已记入审核清单")
        except Exception as _e6:
            logger.debug(f"第6章预处理跳过: {_e6}")

        # 🔴 第7章预处理：把用户填的「工作程序时间」落进「工作计划」分项。
        #    参照件里 (1)~(5) 分项本来带日期，改写时被模型当「参照件项目日期」剔除
        #    （剔除是对的）—— 但没把**用户填的**日期补回去，正文里时间全丢。
        #    确定性替换，不靠提示词。见 `app/services/work_schedule.py`。
        try:
            from app.services.work_schedule import apply_blank_dates, apply_to_chapter7
            _c7 = chapters.get(7, {})
            if isinstance(_c7, dict) and _c7.get("markdown"):
                _md7, _g7 = apply_to_chapter7(_c7["markdown"], filled)
                _c7["markdown"] = _md7
                if _g7:
                    self._table_issues.extend(_g7)
                    logger.warning(f"[第7章] 工作程序时间 {len(_g7)} 项未落位")

            # 🔴 正文里的**空日期占位**（第4章「（五）参与人员签字」末尾的
            #    「编写时间：2026年　　月　　日」）。它不属于任何一章的小节结构，
            #    所以逐章扫一遍 —— 用户填了 `report_month` 就该填上，
            #    没填就留空并记一条 gap。
            for _cn, _cd in (chapters or {}).items():
                if not isinstance(_cd, dict) or not _cd.get("markdown"):
                    continue
                _md, _g = apply_blank_dates(_cd["markdown"], filled)
                if _md != _cd["markdown"]:
                    _cd["markdown"] = _md
                if _g:
                    self._table_issues.extend(_g)
        except Exception as _e7:
            logger.debug(f"第7章预处理跳过: {_e7}")

        # Cleanup placeholders
        for ch_num in _chapters:
            ch_data = chapters.get(ch_num, {})
            if isinstance(ch_data, dict) and ch_data.get("markdown"):
                md = ch_data["markdown"]
                md = re.sub(r'【待补充[^】]*】', '', md)
                md = re.sub(r'\[待补充[^\]]*\]', '', md)
                md = re.sub(r'\|[ \t]*\|', '| — |', md)  # horizontal whitespace only!
                md = re.sub(r'：\s*\n', '：\n', md)
                md = re.sub(r'\n\s*\n\s*\n', '\n\n', md)
                chapters[ch_num]["markdown"] = md

        doc = Document()
        # 🔴 默认样式先按模板字体写好，避免未显式设字体的段落落回 MS 明朝等主题字体
        try:
            self._apply_document_fonts(doc)
        except Exception as _dfe:
            logger.warning(f"[版式] 默认字体设置失败: {_dfe}")
        self._setup_page(doc)

        # ── 前置件（**按域取清单**）──
        #    稳评：封面 → 公司资质 → 公司简介(含人员名单) → 目录
        #          —— 四件全在，调用顺序与分页点逐字等于改动前。
        #    复垦：封面 → 扉页 → 目录
        #          —— 两份复垦样本（明远佳苑二期报告书、盐业井神方案）的前置里
        #             **没有**「公司资质证书」页、**没有**「公司简介/人员名单」页：
        #             那两样在复垦里属**附件**（样本附件清单第 3 项「编制单位资质证书」），
        #             不印在正文前面。
        _front = set(front_matter_order(self._domain))

        # Cover Page（_add_cover_page 内部已分页，此处不再重复分页）
        if "cover" in _front:
            implement_unit = filled.get("implement_unit", "") or self._cv("implement_unit")
            self._add_cover_page(doc, doc_ref, project_name,
                                 cover_org_value(self._domain, filled, org_name),
                                 location, implement_unit, filled=filled)

        # 公司资质（无图片则跳过）
        if "qualifications" in _front:
            if self._add_company_qualifications(doc, filled, domain=self._domain):
                doc.add_page_break()

        # 公司简介（_add_company_intro 内部已分页，此处不再重复分页）
        if "intro" in _front:
            self._add_company_intro(doc, domain=self._domain)

        # 扉页（复垦专有）：项目名称/项目单位/单位地址/联系人/电话/送审时间
        if "title_page" in _front:
            self._add_title_page(doc, filled, project_name, doc_ref)

        # 注：原「编制说明」「法律法规依据」独立块已移除——
        #     现分别由第2章「编制说明」、第3章「编制依据」承担（不再重复出现）。

        # ── 目录（正文各章之前）──
        #    参照件结构：封面 → 资质/简介 → 目录 → 第一章…第十四章
        _add_page_break(doc)
        self._add_toc(doc, chapters, template_path=state.get("template_path"))
        # 🔴 目录后**不再补分页符**：下一章的章标题页用「新节（新页）」开头，
        #    再补一个分页符就是「先跳一页、再分节跳一页」→ 目录后多一页空白
        #    （业主 2026-11-02：「第三页没有内容空白的」）。

        # 注：评审表是**第3章**（参照件《白马湖…报告》结构），
        #     在下面的章节循环里于 ch_num == 3 时渲染。

        # 🔴 Collect extracted PDF tables
        extracted_tables = self._get_extracted_tables(state)

        # ── Chapters ──
        # 🔴 图片放置记录：ch_num → [{path, caption, fname}]，供审核 agent 观察图片位置/命名
        self._image_placements = {}
        self._current_chapter = 0
        # 🔴 评审表是**稳评专有**的第3章（表格章，数据驱动渲染，不由 LLM 写正文）。
        #    复垦没有评审表章 —— 其第3章是「项目概况」（叙述章），走下面普通正文分支。
        _has_review_table = has_review_table(self._domain)
        for ch_num in _chapters:
            self._current_chapter = ch_num
            # 🔴 第3章 = 评审表（稳评专有）
            if ch_num == 3 and _has_review_table:
                self._add_chapter_title_page(doc, _cn_num(3), self._get_chapter_title(3))
                # 🔴 评审表那页**不做整体居中**（业主 2026-09-17 改口径）：
                #    「章节的最后一页内容不需要上下左右居中，按照正常的正文从上到下就行」。
                #    章标题页本身仍居中 —— 那是 `_add_chapter_title_page` 内部实现的，
                #    它收尾时会再起一节并恢复顶端对齐，这里不要再改回去。
                _rv_done = False
                try:
                    from app.services.review_table_filler import fill_review_table_into
                    _rv_done = fill_review_table_into(
                        doc, filled, chapters, state.get("_review_extracted"),
                        template_path=state.get("template_path"))
                except Exception as _rve:
                    logger.warning(f"评审表就地填充失败，回退旧渲染: {_rve}")
                if not _rv_done:
                    try:
                        from app.agent.agents.chapters import get_review_table_agent
                        get_review_table_agent().render_into(
                            doc, filled, chapters, state.get("_review_extracted"))
                    except Exception as _rve:
                        logger.warning(f"评审表渲染失败: {_rve}")
                # 🔴 双路都失败时**必须留占位**：章标题页已加（上面），若此处
                #    一个字都不写，标题就是「光标题」，会被 _post_generation_fix
                #    的空标题清理**整条删掉** → 第三章彻底从报告里消失
                #    （目录是 Word 域重算的，跟着也没了。实测 2026-09-28）。
                if not _rv_done:
                    self._add_para(doc, '（评审表生成失败，请重新生成报告或联系管理员）',
                                   indent=True)
                    logger.warning("[第3章] 评审表两条渲染路径均失败，已写入占位段落保住章标题")
                # 🔴 不再加分页：章标题页用「节分隔符(新页)」实现，重复分页会多出空白页
                if False:
                    doc.add_page_break()
                continue
            ch_data = chapters.get(ch_num, {})
            if isinstance(ch_data, dict) and ch_data.get("markdown"):
                markdown = ch_data["markdown"]
                self._add_chapter(doc, ch_num, markdown, image_files, survey_stats,
                                  extracted_tables.get(ch_num, []), filled)
                if ch_num < _total:
                    doc.add_page_break()
        self._current_chapter = 0
        # 把图片放置记录写回 state，供审核 agent 读取
        state["_image_placements"] = self._image_placements
        state["_unmatched_images"] = self._unmatched_image_markers

        # ── 附件块（稳评第14章 / 复垦第14部分；章标题由渲染器内部输出）──
        #    （章标题由渲染器内部输出，避免与「附件清单」重复）
        # 🔴 不再加分页：附件章标题页用「节分隔符(新页)」实现，重复分页会多出空白页
        # 🔴 附件区图号必须按**附件章**编：循环结束时 `_current_chapter` 被重置为 0，
        #    不设回去的话附件里全是「图0-26」这种编号，交付稿不能看。
        # 🔴 两域的附件**内容毫无重叠**，按域取渲染器（不在装配器里写 `domain == …`）：
        #    稳评 = 专家评审/拟征地公告/勘测定界摘要/现场调查图片 四条图片分组；
        #    复垦 = 样本目录里那张 **22 项**清单（委托函/合同/资质证书/协议书…）。
        #    复垦报告里绝不能出现稳评的「附件1机构资质／附件2问卷／附件3评审…」。
        # ── 第11部分「土地复垦方案报告表」（复垦专有；稳评无此部分）──
        _form_no = report_form_chapter_no(self._domain)
        if _form_no is not None:
            try:
                self._add_reclamation_report_form(doc, chapters, filled)
            except Exception as _fe:
                logger.warning(f"[报告表] 渲染失败，跳过（不影响出报告）: {_fe}")

        # ── 第12部分「请示」/ 第13部分「图件」（复垦专有）──
        _req_no = request_letter_chapter_no(self._domain)
        if _req_no is not None:
            self._current_chapter = _req_no
            self._add_chapter_title_page(
                doc, _cn_num(_req_no), self._get_chapter_title(_req_no),
                chapter_number=_req_no)
            try:
                from app.services.reclamation_request_letter import render_request_letter
                render_request_letter(doc, filled)
            except Exception as _le:
                logger.warning(f"[请示] 渲染失败，跳过（不影响出报告）: {_le}")
        _fig_no = figures_chapter_no(self._domain)
        if _fig_no is not None:
            self._current_chapter = _fig_no
            self._add_chapter_title_page(
                doc, _cn_num(_fig_no), self._get_chapter_title(_fig_no),
                chapter_number=_fig_no)
            try:
                self._add_reclamation_figures(doc, filled)
            except Exception as _fge:
                logger.warning(f"[图件] 渲染失败，跳过（不影响出报告）: {_fge}")

        _appendix_no = appendix_chapter_no(self._domain)
        if _appendix_no is not None:
            self._current_chapter = _appendix_no
            self._chapter_image_counter[_appendix_no] = 0
            if appendix_kind(self._domain) == "reclamation":
                self._add_reclamation_appendices(doc, filled)
            else:
                self._add_appendices(doc, survey_stats, image_files, filled)
            self._current_chapter = 0

        # ── Post-generation review: fix common issues ──
        self._pre_assembly_review(doc, filled)
        self._post_generation_fix(doc, filled)
        # 参与人员签字：用签名库里的手写签名图（没有图的留空手签）
        try:
            self._fill_signature_images(doc, filled)
        except Exception as _sge:
            logger.warning(f"[签名] 签字区渲染失败（保留文字）：{_sge}")
        self._drop_duplicate_sections(doc)
        self._dedup_headings(doc)
        self._drop_duplicate_system_tables(doc)
        self._dedup_table_titles(doc)
        self._dedup_duplicate_tables(doc)
        # 表/图/附件编号：所有域按正文出现顺序重排（业主 2026-11-11）
        self._normalize_caption_order(doc)
        # 断面图/大样图/做法图下面不放「图中红线范围…」解读（图上没有红线）
        self._drop_schematic_redline_text(doc)
        self._fix_truncated_paragraphs(doc)
        if self._domain == "reclamation":
            self._normalize_section_numbers(doc)
            self._fix_reclamation_cost_sentences(doc)
            self._fill_missing_area_table(doc)
        if self._domain == "stability":
            self._fix_stability_area(doc)
            self._fix_stability_score_table(doc)
        # ── Fill tables with computed project data ──
        self._fill_table_data(doc, filled)

        # 🔴 全篇表格补实线边框（杜绝 Word 的虚线网格）
        try:
            _n = self.ensure_all_tables_bordered(doc)
            logger.info(f"[TBL] 已为 {_n} 张表格补齐实线边框")
        except Exception as _be:
            logger.warning(f"表格边框兜底失败: {_be}")

        # 🔴 全篇图片去重兜底（模板整块拷贝的插图绕过插入时去重，见方法注释）
        try:
            _d = self.dedupe_duplicate_images(doc)
            if _d:
                logger.info(f"[IMG] 已清除 {_d} 处重复插图")
        except Exception as _de:
            logger.warning(f"图片去重兜底失败: {_de}")

        # 🔴 打开时自动更新域：目录是 `TOC \o "1-2" \h \u` 域，Docx 生成时**没有缓存结果**，
        #    Word 打开就是一片空白，要手动按 F9 才出条目（业主：「目录没写」）。
        #    `w:updateFields` 让 Word 打开文档时自动刷新所有域，目录/页码随即出现。
        try:
            _st = doc.settings.element
            if _st.find(qn('w:updateFields')) is None:
                from docx.oxml import OxmlElement as _OE3
                _uf = _OE3('w:updateFields')
                _uf.set(qn('w:val'), 'true')
                _st.append(_uf)
                logger.info("[TOC] 已置 updateFields=true（Word 打开时自动刷新目录）")
        except Exception as _ue:
            logger.warning(f"目录自动刷新设置失败: {_ue}")

        # 🔴 页眉抬头 + 页脚页码：必须放在**所有节创建完之后**
        #    （章节标题页/附件都会 add_section，早设会被后建的空节覆盖掉）。
        try:
            _kind = report_kind_name(self._domain)   # 按域取：稳评/复垦
            _hdr = (str(filled.get("project_name") or doc_ref or "").strip() + _kind)
            if _hdr == _kind:
                _hdr = ""     # 连项目名都没有时不如不加抬头
            self._setup_header_footer(doc, _hdr)
            logger.info(f"[HDR] 页眉页脚已设置（抬头={'有' if _hdr else '无'}，页码=正文起第1页）")
        except Exception as _hf:
            logger.warning(f"页眉页脚设置失败: {_hf}")

        # ── Save ──
        filename = f"{session_id}_report.docx"
        if doc_ref:
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', doc_ref)
            filename = f"{safe_name}_{report_kind_name(self._domain)}.docx"

        from datetime import datetime
        ts = datetime.now().strftime('%m%d_%H%M')
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, 'docx')
        filename = f"{name}_{ts}.{ext}"
        outpath = self.output_dir / filename
        # 🔴 缺口检测：扫描正文里的**遁词**（「以…为准」回避句）+ 汇总已有缺口。
        #    产出同时进 `state["_chapter_gaps"]`（→ Word 批注）与日志（终端可见）。
        #    必须在 `_inject_audit_comments` **之前**跑，否则批注里没有这批内容。
        #    🔴 只标注、不改正文 —— 正文里留占位符违反业主硬约束。
        try:
            from app.services.gap_detector import collect_gaps
            _ch_md = {n: (c or {}).get("markdown", "")
                      for n, c in (chapters or {}).items() if isinstance(c, dict)}
            _gaps = collect_gaps(state, _ch_md)
            # 🔴 资料里少的图片也进缺口清单（只提示，不写正文占位）
            for _mi in getattr(self, "_missing_images", []):
                _ch = int(_mi.get("chapter") or 0)
                _gaps.setdefault(_ch, []).append({
                    "reason": "missing_image",
                    "detail": f"缺少图片：{_mi.get('caption') or _mi.get('ref')}",
                    "severity": "warning",
                    "sentence": "",
                })
            state["_chapter_gaps"] = _gaps
            _gn = sum(len(v) for v in _gaps.values())
            if _gn:
                logger.warning(f"[缺口] 全篇 {_gn} 项待补充/待核对："
                               f"{ {k: len(v) for k, v in sorted(_gaps.items())} }")
                for _ch in sorted(_gaps):
                    for _g in _gaps[_ch]:
                        logger.warning(f"   · 第{_ch}章 {_g.get('detail', '')}")
        except Exception as _ge:
            logger.warning(f"缺口检测跳过: {_ge}")

        # 🔴 复垦：模型手写的「复垦前后结构对比」与系统表重复时只留系统那张
        if self._domain == "reclamation":
            try:
                self._dedup_structure_tables(doc)
            except Exception as _dse:
                logger.warning(f"结构对比表去重跳过: {_dse}")

        # 🔴 表格内容统一上下左右居中（必须在表格增删都结束之后）
        try:
            self._center_all_tables(doc)
        except Exception as _cte:
            logger.warning(f"表格居中跳过: {_cte}")

        # 🔴 审核元信息写入 Word 批注（必须在 doc.save() 前的最后阶段，防止后续重写 run 导致批注错位）
        try:
            self._inject_audit_comments(doc, state)
        except Exception as _ce:
            logger.warning(f"Audit comment injection skipped: {_ce}")

        # 🔴 政府称谓最后归一：评审表由模板/提取值渲染，不走章节 markdown 修补，
        #    「街道办事处审核」这类模板句只能在这里统一（马头镇就是镇）。
        try:
            self._fix_doc_government_names(doc, filled)
        except Exception as _gve:
            logger.warning(f"政府称谓归一跳过: {_gve}")

        # 🔴 附件编号最终重排：装配后段可能重新加/改过附件标题，
        #    在保存前以最终文档为准再排一次（清单行与逐项标题各自 1..N）。
        if getattr(self, "_domain", "stability") == "reclamation":
            try:
                _idx = [i for i, p in enumerate(doc.paragraphs)
                        if p.text.strip() == "附件清单"]
                if _idx:
                    self._renumber_appendix_numbers(doc, _idx[-1])
            except Exception as _are:
                logger.warning(f"附件编号最终重排跳过: {_are}")

        # 🔴 交付闸门（业主 2026-11-16）：「审查要点是否写全 + 篇幅是否够」，
        #    纯确定性判定，不调模型。结果写回 state（`_coverage`/`_word_budget`/
        #    `_deliverable`/`_deliverable_blockers`），工作流与界面接口直接读。
        #    不阻断出稿 —— 只把「不可交付」和缺口列出来，交不交由人定。
        try:
            from app.services.coverage_matrix import attach as _attach_gate, summarize as _sum_gate
            _gate = _attach_gate(state, domain=self._domain)
            if not _gate.get("deliverable"):
                logger.warning("[闸门] 本轮报告不可交付：%s", _sum_gate(_gate))
        except Exception as _gge:
            logger.warning(f"[闸门] 覆盖/字数检查跳过: {_gge}")
        doc.save(str(outpath))
        # 稳评：**章节结构跟所在地区的模板走**（2026-11-20 业主口径）。
        # 各区模板章数/顺序不同（洪泽 12、淮阴 10、淮安 14…），装配完按模板
        # 的章标题重排/改名/取舍；模板解析不到就保持系统 14 章结构。
        if self._domain == "stability":
            try:
                _tp = str(state.get("template_path") or "")
                if _tp:
                    from app.services.template_structure import (
                        extract_plan, apply_plan)
                    _plan = extract_plan(_tp)
                    _st = apply_plan(outpath, _plan)
                    if _st.get("applied"):
                        logger.info(f"[模板结构] 按模板重排：{_st}")
                        state["_template_structure"] = _st
            except Exception as _se:
                logger.warning(f"[模板结构] 重排跳过: {_se}")
        # 复垦：装配后统一修补专家批注类问题（目录页码、章节号、表题、地类编码），
        # 并跑一遍确定性审计；这些既不在正文 markdown 里，也不该留给人工。
        if self._domain == "reclamation":
            try:
                from app.services.reclamation_expert_fixes import (
                    apply_expert_fixes, audit_reclamation_docx)
                _stats = apply_expert_fixes(outpath)
                _issues = audit_reclamation_docx(outpath)
                if _issues:
                    logger.warning("[复垦审计] %d 项：%s", len(_issues),
                                   "；".join(_issues[:8]))
                else:
                    logger.info("[复垦审计] 通过")
                logger.info("[复垦专家修补] %s", _stats)
                state["_reclamation_expert_fixes"] = _stats
                state["_reclamation_audit"] = _issues
            except Exception as _ef:
                logger.warning(f"[复垦专家修补] 跳过: {_ef}")
        logger.info(f"Report assembled: {outpath} ({len(doc.paragraphs)} paras, {len(doc.tables)} tables)")
        return f"generated/{filename}"

    def _fix_doc_government_names(self, doc, filled) -> None:
        """全文档统一政府称谓（含评审表、表格单元格、页眉页脚不处理）。"""
        from app.services.report_content_repairs import fix_government_names
        _filled = filled or {}

        def _fix_p(p) -> None:
            text = p.text
            if not text or "街" not in text and "镇" not in text and "乡" not in text:
                return
            new, n = fix_government_names(text, _filled)
            if not n or new == text:
                return
            if p.runs:
                p.runs[0].text = new
                for r in p.runs[1:]:
                    r.text = ""
            else:
                p.add_run(new)

        for p in doc.paragraphs:
            _fix_p(p)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        _fix_p(p)

    def _fill_table_data(self, doc, filled):
        """Fill table cells with computed data from project facts + survey data."""
        # ── Gather data from filled dict ──
        # 🔴 用 `_num_or` 兜底 —— 问卷没解析出来时 `support_rate` 可能是 `【待补充】`，
        #    直接 float() 会让整份报告产不出来（实测崩过）。
        support_rate = _num_or(filled.get("support_rate"), 100)
        # 🔴 问卷数只取已核验的 total_samples/survey_total_count，严禁回退到户数（涉及户数≠问卷数）
        survey_total = _int_or(filled.get("survey_total_count") or filled.get("total_samples"), 0)
        support_n = int(survey_total * support_rate / 100)
        oppose_n = survey_total - support_n
        know_rate = _num_or(filled.get("survey_aware_yes"), 89.0)
        know_n = int(survey_total * know_rate / 100)

        # Pre-compute scoring from the scoring service
        from app.services.scoring_service import scoring_calculator
        score_items = scoring_calculator.calculate(dict(filled))
        score_map = {s.indicator[:30]: s.score for s in score_items}
        after_items = scoring_calculator.calculate_measures_after(score_items)
        after_score_map = {s.indicator[:30]: s.score for s in after_items}

        for tbl in doc.tables:
            if not tbl.rows: continue
            # 🔴 系统渲染的标准表（表3-1/3-3）已含正确数据，跳过，防止被覆盖为 0/【待补充】
            try:
                if id(tbl._element) in getattr(self, '_system_rendered_tables', set()):
                    continue
            except Exception:
                pass
            h0 = tbl.rows[0].cells[0].text.strip()
            cells0 = [c.text.strip() for c in tbl.rows[0].cells]

            # Fix survey summary tables: 调查项目 | 人数 | 占比 (template format)
            if h0 == '调查项目' and ('人数' in str(cells0) or '人次' in str(cells0)):
                for row in tbl.rows[1:]:
                    rtext = [c.text.strip() for c in row.cells]
                    label = rtext[0]
                    if '支持' in label and '条件' not in label:
                        self._set_cell(row.cells[1], str(support_n))
                        self._set_cell(row.cells[2], f'{support_rate:.1f}%')
                    elif '反对' in label:
                        self._set_cell(row.cells[1], str(oppose_n))
                        self._set_cell(row.cells[2], f'{100-support_rate:.1f}%')
                    elif '条件' in label:
                        self._set_cell(row.cells[1], '0')
                        self._set_cell(row.cells[2], '0.0%')
                    elif '合计' in label:
                        self._set_cell(row.cells[1], str(survey_total))
                        self._set_cell(row.cells[2], '100%')

            # Fix scoring tables: fill empty 得分 column
            hcells = [c.text.strip() for c in tbl.rows[0].cells]
            if '得分' in str(hcells) and '测评' in str(hcells):
                # Detect if this is a 措施后 table by checking nearby paragraphs
                is_after = False
                prev_texts = []
                for p in doc.paragraphs:
                    if p._element is tbl._element:
                        break
                    prev_texts.append(p.text)
                chapter_text = ' '.join(prev_texts[-10:])  # last 10 paragraphs before table
                if '措施后' in chapter_text or '第八章' in chapter_text:
                    is_after = True

                use_map = after_score_map if is_after else score_map
                score_col = next((ci for ci, h in enumerate(hcells) if '得分' in h), None)
                ind_col = next((ci for ci, h in enumerate(hcells) if '测评项目' in h), 2)
                if score_col is not None:
                    for row in tbl.rows[1:]:
                        cells = [c.text.strip() for c in row.cells]
                        val = cells[score_col] if score_col < len(cells) else ''
                        val = val.strip('【】* ')
                        need_fill = val in ('', '0', '—') or any(p in val for p in ['填入','计算','项目判定'])
                        if need_fill and ind_col < len(cells):
                            key = cells[ind_col][:30]
                            if key in use_map:
                                self._set_cell(row.cells[score_col], str(use_map[key]))
                            elif '合计' in str(cells):
                                self._set_cell(row.cells[score_col], str(sum(use_map.values())))

            # Fix detailed survey tables: 调查内容 | 选项 | 人数 | 比例
            # 🔴 只处理群众调查表（人数列），部门调查表（个数列）单独处理
            if h0 == '调查内容' and '选项' in str(cells0) and '人数' in str(cells0):
                # 🔴 非破坏原则：表格已有数值（系统渲染）→ 不覆盖
                if all((r.cells[2].text.strip() or '').replace('.', '').isdigit() for r in tbl.rows[1:]) and len(tbl.rows) > 1:
                    continue
                # 🔴 优先从 questionnaire_tallies（问卷勾选统计）查真实数据
                tallies = filled.get("questionnaire_tallies", {})
                total = _int_or(filled.get("survey_total_count"), 0)
                for row in tbl.rows[1:]:
                    rtext = [c.text.strip() for c in row.cells]
                    label = rtext[0] if len(rtext) > 0 else ""
                    option = rtext[1] if len(rtext) > 1 else ""

                    # 🔴 模糊匹配：把 LLM 写的题目（如"项目支持度"）映射到问卷真实题目（如"对该决策是否支持"）
                    count = None
                    if isinstance(tallies, dict) and label and option:
                        count = _match_tally(tallies, label, option)

                    if count is not None and total > 0:
                        self._set_cell(row.cells[2], str(count))
                        self._set_cell(row.cells[3], f"{round(count/total*100, 1)}%")
                    elif any(p in str(rtext) for p in ['【填入', '【待补充', '【待统计', 'XX']):
                        # 🔴 没有真实数据 → 填【待补充】，绝不编造 150/100%
                        self._set_cell(row.cells[2], '【待补充】')
                        self._set_cell(row.cells[3], '【待补充】')

            # Fix 部门意见调查分析表（个数列）：用 dept_* 字段填充真实部门数据
            if h0 == '调查内容' and '个数' in str(cells0):
                # 🔴 表3-1 群众调查表也用「个数」列，但它已由系统生成并填好六维数据，
                # 不能被当成部门表覆盖成【待补充】。区分：部门表题目含「贵单位」。
                is_dept_table = any(
                    '贵单位' in (tbl.rows[r].cells[0].text or '')
                    for r in range(1, min(len(tbl.rows), 4))
                )
                if not is_dept_table:
                    continue

                # 🔴 非破坏原则：单元格已有数值（系统已填好）→ 不覆盖
                def _is_filled(row):
                    return (row.cells[2].text.strip() or '').replace('.', '').replace(',', '').isdigit()

                if all(_is_filled(r) for r in tbl.rows[1:]) and len(tbl.rows) > 1:
                    continue

                dept_count = _int_or(filled.get("dept_survey_count"), 0)
                if dept_count <= 0:
                    # 🔴 没有部门调查数据 → 覆盖 LLM 编造的数量，填【待补充】
                    for row in tbl.rows[1:]:
                        self._set_cell(row.cells[2], '【待补充】')
                        self._set_cell(row.cells[3], '【待补充】')
                else:
                    # 🔴 有部门数据 → 用 dept_* 字段的真实勾选数据填充
                    from app.services.deep_material_analyzer import _map_dept_question
                    for row in tbl.rows[1:]:
                        cells_text = [c.text.strip() for c in row.cells]
                        label = cells_text[0] if len(cells_text) > 0 else ""
                        option = cells_text[1] if len(cells_text) > 1 else ""
                        map_key = _map_dept_question(label)
                        if not map_key:
                            continue
                        extracted = filled.get(map_key, {})
                        if isinstance(extracted, dict) and option in extracted:
                            count = int(extracted[option])
                            self._set_cell(row.cells[2], str(count))
                            self._set_cell(row.cells[3], f"{round(count/dept_count*100, 1)}")
                        else:
                            # 该选项无勾选 → 0
                            self._set_cell(row.cells[2], '0')
                            self._set_cell(row.cells[3], '0.0%')

    def _drop_duplicate_sections(self, doc):
        """同一章内**整节重复**的，删后出现的那一整段（含标题与表）。

        2026-11-03 测试稿实测：第 4 章模型先写完 4.6/4.7，又重写了一遍 4.6/4.2/4.7；
        第 5/7 章各有一个紧邻的重复标题。只按「章内标题首次出现」判重：
        重复标题及其到下一个同级/更高级标题之间的内容全部删除。
        """
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        body = doc.element.body
        cur_ch = None
        seen: set = set()
        drop = False
        drop_level = 0
        for child in list(body.iterchildren()):
            if child.tag == qn('w:p'):
                p = Paragraph(child, doc)
                t = p.text.strip()
                m = re.match(r'Heading\s+(\d)', p.style.name) if t else None
                if not m:
                    if drop:
                        body.remove(child)
                    continue
                lvl = int(m.group(1))
                if lvl == 1:
                    cur_ch = t
                    seen = set()
                    drop = False
                    continue
                if drop:
                    if lvl <= drop_level:
                        drop = False          # 这一节结束，当前标题照常处理
                    else:
                        body.remove(child)
                        continue
                key = (cur_ch, t)
                if key in seen:
                    drop, drop_level = True, lvl
                    body.remove(child)
                    continue
                seen.add(key)
            elif drop:
                body.remove(child)

    def _dedup_headings(self, doc):
        """删掉**紧挨着重复**的同一标题（参照件 + 模型各写了一次）。

        2026-11-03 测试稿实测：「5.2 预防控制措施」「7.2 估算成果」各出现两次 ——
        模型在参照件标题后又补了一个同名标题。只处理连续重复，不动正文。
        """
        last = None
        for p in doc.paragraphs:
            t = p.text.strip()
            if not t:
                continue
            if p.style.name.startswith("Heading"):
                if t == last:
                    p.clear()
                    p.text = ""
                else:
                    last = t
            else:
                last = None

    def _drop_duplicate_system_tables(self, doc):
        """同题表：系统表与模型手写表同时存在时，删模型那张。

        2026-11-11 实测：第6章「项目复垦工程量汇总表」系统表（12 行）与模型
        手写表（999.9）同题并存，`_dedup_table_titles` 只清表题不删表体，
        结果稿里先出现模型那张、后出现系统表，看起来表号/数据都不对。
        """
        import re as _re
        from docx.oxml.ns import qn as _qn
        sys_titles = {
            _re.sub(r'^[表图]\s*\d+\s*[-—－]\s*\d+\s*', '', t).strip()
            for t in (getattr(self, "_system_caption_exact", set()) or set())
        }
        if not sys_titles:
            return
        body = doc.element.body
        kids = list(body.iterchildren())
        pairs = {}          # title -> [(cap_el, tbl_el, tbl_id, rows)]
        for i, el in enumerate(kids):
            if el.tag != _qn("w:p"):
                continue
            txt = "".join(n.text or "" for n in el.iter(_qn("w:t"))).strip()
            if not _re.match(r'^[表图]\s*\d+\s*[-—－]\s*\d+', txt):
                continue
            title = _re.sub(r'^[表图]\s*\d+\s*[-—－]\s*\d+\s*', '', txt).strip()
            # 向后找 1～3 个元素内的表格
            for j in range(i + 1, min(i + 4, len(kids))):
                if kids[j].tag == _qn("w:tbl"):
                    trs = kids[j].findall(_qn("w:tr"))
                    pairs.setdefault(title, []).append((el, kids[j], id(kids[j]), len(trs)))
                    break
        for title, items in pairs.items():
            if title not in sys_titles or len(items) < 2:
                continue
            sys_items = [x for x in items if x[2] in (getattr(self, "_system_rendered_tables", set()) or set())]
            keep = sys_items[0] if sys_items else max(items, key=lambda x: x[3])
            for cap, tbl, _tid, _n in items:
                if tbl is keep[1]:
                    continue
                try:
                    body.remove(tbl)
                    body.remove(cap)
                    logger.info(f"[表去重] 同题表保留系统表，删除模型手写：{title}")
                except Exception:
                    pass

    def _dedup_table_titles(self, doc):
        """Remove duplicate table titles, fix numbering, remove strays, and clean tables."""
        import re

        # Fix None/empty cells → '—' in ALL tables
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    ct = cell.text.strip()
                    if ct in ('None', 'none'):
                        for p in cell.paragraphs:
                            for run in p.runs:
                                if run.text.strip() in ('None', 'none'):
                                    run.text = '—'

        current_ch = 0
        ch_table_counters = {}
        seen_titles = set()
        # Collect table element positions for proximity check
        table_positions = set()
        for tbl in doc.tables:
            try:
                # Find the paragraph just before the table
                prev_elem = tbl._element.getprevious()
                if prev_elem is not None:
                    table_positions.add(id(prev_elem))
            except: pass

        for i, p in enumerate(doc.paragraphs):
            t = p.text.strip()

            # Track current chapter
            # 🔴 按域取章号：这里原用稳评的 `chapter_no_of_heading`，复垦章标题
            #    `4 土地复垦方向可行性分析` 解析出来是 0 → `current_ch > 0` 那道门
            #    永远为假 → **表号重编对复垦从未生效**，LLM 写的表号原样留下，
            #    于是出现两个「表4-2」（实测 2026-10-12）。
            _ch = chapter_no_of_text(self._domain, t)
            if _ch:
                current_ch = _ch
                ch_table_counters[current_ch] = 0

            # Fix table titles
            if t.startswith('表') and len(t) < 80 and current_ch > 0:
                # Skip stray paragraphs that aren't really table titles
                # (real table titles are short and don't contain sentence-like text)
                if any(kw in t for kw in ['表彰', '奖励', '处分', '追究', '通报', '给予']):
                    # Not a table title — it's a stray sentence fragment
                    if not t.startswith('表') or len(t) > 30:
                        continue

                if t in seen_titles:
                    p.clear()
                    p.text = ''
                elif (getattr(self, '_system_caption_by_title', {}) or {}).get(
                        re.sub(r'^表\s*\d+\s*[-—]\s*\d+([-—]\d+)?\s*', '', t).strip()):
                    # 🔴 题名与系统表相同、只是编号写错 → 按样本表号改回来，
                    #    不参与顺序重编（否则 表4-11 会被编成 表4-1，与 4-1 冲突）
                    _fixed = self._system_caption_by_title[
                        re.sub(r'^表\s*\d+\s*[-—]\s*\d+([-—]\d+)?\s*', '', t).strip()]
                    _new_title = re.sub(r'^表\s*\d+\s*[-—]\s*\d+([-—]\d+)?',
                                        _fixed, t, count=1)
                    if _new_title != t:
                        p.clear()
                        _run = p.add_run(_new_title)
                        try:
                            if p.runs and p.runs[0].bold:
                                _run.bold = True
                        except: pass
                    seen_titles.add(_new_title)
                elif t in getattr(self, '_system_caption_exact', ()):
                    # 🔴 系统补渲表的表号是**按样本定死**的（复垦表4-8/4-11/4-12/4-13），
                    #    只登记不重编号。稳评该集恒为空 → 本条恒不触发。
                    seen_titles.add(t)
                else:
                    seen_titles.add(t)
                    # Renumber: sequential within chapter
                    ch_table_counters[current_ch] = ch_table_counters.get(current_ch, 0) + 1
                    new_num = ch_table_counters[current_ch]
                    # 🔴 跳过系统表已占的号（复垦表4-8/4-11/4-12/4-13 按样本定死）
                    _reserved = getattr(self, '_system_caption_nums', {}).get(current_ch, set())
                    while new_num in _reserved:
                        new_num += 1
                    ch_table_counters[current_ch] = new_num
                    # Update table number: 表X-Y or 表X-Y-Z → 表X-new_num
                    new_title = re.sub(r'表\d+[-—]\d+([-—]\d+)?', f'表{current_ch}-{new_num}', t)
                    if new_title != t:
                        p.clear()
                        run = p.add_run(new_title)
                        try:
                            if p.runs and p.runs[0].bold:
                                run.bold = True
                        except: pass

        # Remove empty "表X-Y" headings that have no table following within 3 paragraphs
        for i, p in enumerate(doc.paragraphs):
            t = p.text.strip()
            if re.match(r'^表\d+[-—]\d+', t) and len(t) < 60:
                # Check next 3 paragraphs for a table
                has_table_nearby = False
                for j in range(i+1, min(i+5, len(doc.paragraphs))):
                    next_elem = doc.paragraphs[j]._element
                    if id(next_elem) in table_positions:
                        has_table_nearby = True
                        break
                    # Also check if next paragraph is a table title (nested case)
                    nt = doc.paragraphs[j].text.strip()
                    if nt.startswith('表') and len(nt) < 60:
                        break  # Another table title — this one might be empty
                if not has_table_nearby:
                    # Check if the next meaningful text is another heading
                    next_texts = []
                    for j in range(i+1, min(i+5, len(doc.paragraphs))):
                        nt = doc.paragraphs[j].text.strip()
                        if nt: next_texts.append(nt)
                    # If no table nearby and next content is a heading/another table title, clean this
                    if not any('Heading' in (doc.paragraphs[j].style.name or '') for j in range(i+1, min(i+5, len(doc.paragraphs)))):
                        pass  # Keep it
                    else:
                        # This table title has no table — mark as empty
                        if not has_table_nearby:
                            pass  # Keep, might be between heading transitions

    def _normalize_caption_order(self, doc):
        """复垦：表/图号按**正文出现顺序**重排成 1,2,3…，不跳号、不重号，
        并把正文引用改成**它下面那张表**的号。

        业主 2026-11-12（第二次强调）：「所有的表格上文说见表x-x那下文的表格
        就是x-x，不要出现与上文对应不上的情况，也不要出现排序混乱」。

        旧实现按「旧号→新号」映射改引用：旧号重复（两个表4-2）或引用本身写错
        （见表4-6 指着表4-3）时，改出来的号仍然对不上。这里改成**按位置**：
          ① 表题按出现顺序编号（不区分系统表/模型表）；
          ② 「见表X-Y」「如下表所示」一律改成它**后面第一张表**的实际编号。
        非「见…」的提法（如「与表4-2 对比」）仍按旧号→新号映射改（唯一时才改）。
        """
        cap = re.compile(r'^((?:表|图)\s*)(\d+)\s*[-—－]\s*(\d+)(.*)$')
        ref = re.compile(r'见\s*(表|图)\s*(\d+)\s*[-—－]\s*(\d+)(?:\s*[-—－]\s*\d+)?')
        ref_by = re.compile(r'((?:依据|按照|按|根据|如)\s*)(表|图)\s*(\d+)\s*[-—－]\s*(\d+)')
        ref_word = re.compile(r'(如下表所示|详见下表|见下表|如下表|详见下图|见下图)')
        # 句子话题 → 表题关键词：优先落到「讲的是同一件事」的那张表。
        # 只靠「下一张」会把「各评价单元复垦方向见表X」指到后面的表土平衡表上。
        _TOPICS = (
            ("临时用地土地利用现状", "临时用地土地利用现状"),
            ("主体项目用地", "主体项目用地"),
            ("土地利用现状", "土地利用现状"), ("土地权属", "权属"), ("权属", "权属"),
            ("复垦方向", "复垦方向"), ("适宜性分值", "适宜性分值"), ("适宜性", "适宜性"),
            ("土地质量等级", "质量等级"), ("分级标准", "指标体系"),
            ("指标体系", "指标体系"), ("限制因素", "指标体系"),
            ("表土资源", "表土"), ("表土", "表土"),
            ("水资源", "水资源"), ("结构对比", "结构对比"),
            ("拟损毁", "拟损毁"), ("损毁时序", "损毁时序"), ("损毁", "损毁"),
            ("评价单元", "评价单元"), ("面积统计", "面积统计"),
            ("其他费用", "其他费用"), ("工程施工费", "工程施工费"),
            ("投资估算", "投资估算"),
        )

        def _rewrite(p, text):
            if not p.runs:
                p.add_run(text)
                return
            p.runs[0].text = text
            for r in p.runs[1:]:
                r.text = ""

        paras = list(doc.paragraphs)
        par_ch: List[int] = []
        cur_ch = 0
        for p in paras:
            t = p.text.strip()
            c = chapter_no_of_text(self._domain, t) if t else 0
            if c:
                cur_ch = c
            par_ch.append(cur_ch)

        # ① 表题按出现顺序编号（章号取**它在文档里的实际章**，不认模型写的章号）
        counters: Dict[Any, int] = {}
        caps: List[tuple] = []            # (段落序号, kind, ch, 表题剩余部分, 新号)
        cap_idx: set = set()
        for i, p in enumerate(paras):
            t = p.text.strip()
            m = cap.match(t)
            if not m or not par_ch[i]:
                continue
            rest = m.group(4) or ""
            if "。" in rest or len(t) > 80:
                continue          # 「图4-1 反映了…。施工场站…」是正文，不是图题
            ch = par_ch[i]
            kind = m.group(1).strip()
            key = (kind, ch)
            counters[key] = counters.get(key, 0) + 1
            caps.append((i, kind, ch, rest, counters[key]))
            cap_idx.add(i)

        # 旧号→新号（只给非「见…」的提法兜底；同号多张时不改，避免改错）
        old_map: Dict[tuple, List[int]] = {}
        for i, kind, ch, _rest, new in caps:
            m = cap.match(paras[i].text.strip())
            old_map.setdefault((kind, ch, int(m.group(3))), []).append(new)

        def _next_no(idx: int, kind: str, ch: int):
            for ci, _ck, cch, _rest, new in caps:
                if ci > idx and cch == ch and _ck == kind:
                    return new
            return None

        def _norm_title(s: str) -> str:
            return re.sub(r'\s+', '', str(s or ""))

        def _topic_no(idx: int, line: str, kind: str, ch: int, pos: int = 0,
                      whole: bool = False):
            """按句子话题找**最近**的同题表。

            · 只看「见」所在的这一句（前面那句里的「适宜性」不能拿来定表）；
            · 话题词按**离见字最近的词尾**取（「临时用地土地利用现状」胜过
              包含它的「土地利用现状」；「表土资源平衡」胜过句首的「土地利用现状」）；
            · 再按段落距离取最近的同题表。
            """
            _p = pos or len(line)
            _seg_start = max(line.rfind("。", 0, _p), line.rfind("；", 0, _p),
                             line.rfind("！", 0, _p), line.rfind("？", 0, _p)) + 1
            seg = line if whole else line[_seg_start:_p]
            _anchor = pos if whole else len(seg)
            best = None
            for kw, capkw in _TOPICS:
                q = seg.rfind(kw)
                if q < 0:
                    continue
                # 词尾离引用位置越近越好；同词尾取更长的词（更具体）
                score = (abs(_anchor - (q + len(kw))), -len(kw))
                if best is None or score < best[0]:
                    best = (score, capkw)
            if best is None:
                return None
            topic = best[1]
            cand = [(abs(ci - idx), 0 if ci > idx else 1, new)
                    for ci, _k, cch, rest, new in caps
                    if cch == ch and _k == kind and topic in _norm_title(rest)]
            if not cand:
                return None
            return min(cand)[2]

        for i, kind, ch, rest, new in caps:
            t = paras[i].text.strip()
            newt = f"{kind}{ch}-{new}{rest}"
            if newt != t:
                _rewrite(paras[i], newt)

        for i, p in enumerate(paras):
            if i in cap_idx:
                continue
            t = p.text
            if not t.strip():
                continue
            ch = par_ch[i]
            if not ch:
                continue

            def _rep_ref(m):
                kind = m.group(1)
                _ref_ch = int(m.group(2))
                # 跨章引用（第5章「见表6-1」）：不能按「本章下一张表」改，
                # 只按第6章表号的**旧→新映射**改；映射不唯一就原样保留。
                # （稳评大量跨章引表，这条不能省。）
                if _ref_ch != ch:
                    _cand = old_map.get((kind, _ref_ch, int(m.group(3)))) or ()
                    if len(_cand) == 1:
                        return f"见{kind}{_ref_ch}-{_cand[0]}"
                    return m.group(0)
                nxt = _topic_no(i, t, kind, ch, m.start())
                if nxt is None:
                    nxt = _next_no(i, kind, ch)
                if nxt is None:
                    _cand = old_map.get((kind, ch, int(m.group(3)))) or ()
                    if len(_cand) == 1:
                        nxt = _cand[0]
                    else:
                        return m.group(0)
                return f"见{kind}{ch}-{nxt}"

            nt = ref.sub(_rep_ref, t)

            # 「依据表4-9 的分级标准」这类非「见」引用：话题词常在表号**后面**，
            # 按整句话题找同题表（找不到就原样保留）。
            def _rep_by(m):
                if int(m.group(3)) != ch:
                    return m.group(0)
                nxt = _topic_no(i, t, m.group(2), ch, m.start(), whole=True)
                if nxt is None:
                    return m.group(0)
                return f"{m.group(1)}{m.group(2)}{ch}-{nxt}"

            nt = ref_by.sub(_rep_by, nt)

            # 畸形的三级表号（「表4-34-2」）——模型生造的编号，没有任何表能对上，
            # 整句删掉，别让它出现在成稿里（业主：不要出现对不上的表号）。
            nt = re.sub(
                r'[^。；\n]*见\s*表\s*\d+\s*[-—－]\s*\d+\s*[-—－]\s*\d+[^。；\n]*[。；]?',
                '', nt)

            def _rep_word(m):
                word = m.group(1)
                kind = "图" if "图" in word else "表"
                nxt = _topic_no(i, t, kind, ch, m.start())
                if nxt is None:
                    nxt = _next_no(i, kind, ch)
                if nxt is None:
                    return word
                return f"见{kind}{ch}-{nxt}"

            nt = ref_word.sub(_rep_word, nt)
            if nt != t:
                _rewrite(p, nt)

        # 附件号：同样按出现顺序重排（附件1、附件2…）
        att = re.compile(r'^(\s*附件\s*)(\d+)(.*)$')
        att_map = {}
        cnt = 0
        for p in doc.paragraphs:
            t = p.text.strip()
            m = att.match(t)
            if not m:
                continue
            cnt += 1
            att_map.setdefault(m.group(2), str(cnt))
            newt = f"附件{cnt}{m.group(3)}"
            if newt != t:
                _rewrite(p, newt)
        for p in doc.paragraphs:
            t = p.text.strip()
            if not t or att.match(t):
                continue
            nt = t
            for old, new in att_map.items():
                nt = re.sub(rf'附件\s*{old}(?!\d)', f"附件{new}", nt)
            if nt != t:
                _rewrite(p, nt)

    def _normalize_section_numbers(self, doc):
        """复垦：小节号按出现顺序重排（4.1.1、4.1.3 → 4.1.1、4.1.2）。"""
        import re as _re
        cur, c2, c3 = 0, 0, {}

        def _rewrite(p, text):
            if not p.runs:
                p.add_run(text)
                return
            p.runs[0].text = text
            for r in p.runs[1:]:
                r.text = ""

        for p in doc.paragraphs:
            t = p.text.strip()
            m = _re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?\s+(\S.*)$', t)
            if not m:
                continue
            if len(t) > 50 and not p.style.name.startswith("Heading"):
                continue          # 正文里以数字开头的句子，不是小节标题
            ch, a, b, title = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
            if ch != cur:
                cur, c2, c3 = ch, 0, {}
            if b is None:
                c2 += 1
                c3[c2] = 0
                new = f"{ch}.{c2}"
            else:
                k = a or c2
                c3[k] = c3.get(k, 0) + 1
                new = f"{ch}.{k}.{c3[k]}"
            if f"{ch}.{a}" + (f".{b}" if b else "") != new:
                _rewrite(p, f"{new} {title}")

    def _fix_reclamation_cost_sentences(self, doc):
        """管护费/动态投资句里的错数（21500 万元）按预算结果改回来。"""
        import re as _re
        filled = getattr(self, "_filled", {}) or {}
        est = filled.get("_budget_estimate") or {}
        care = float(est.get("care") or 0)
        monitor = float(est.get("monitor") or 0)
        if not care:
            m = _re.search(r'\d+(?:\.\d+)?', str(filled.get("care_period_months") or ""))
            if m:
                care = round(1 * float(m.group(0)) * 1000.0 / 10000.0, 2)
        if not care and not monitor:
            return

        def _rewrite(p, text):
            if not p.runs:
                p.add_run(text)
                return
            p.runs[0].text = text
            for r in p.runs[1:]:
                r.text = ""

        for p in doc.paragraphs:
            t = p.text
            nt = t
            if care and "管护" in nt:
                nt = _re.sub(r'共计\s*[\d.]+\s*万元', f"共计 {care:.2f} 万元", nt)
                nt = _re.sub(r'(管护[^。；\n]{0,12}?费用\s*)[\d.]+\s*万元',
                             rf'\g<1>{care:.2f} 万元', nt)
            if monitor and "动态投资" in nt:
                nt = _re.sub(r'动态投资\s*[\d.]+\s*万元',
                             f"动态投资 {monitor:.2f} 万元", nt)
            if nt != t:
                _rewrite(p, nt)

    def _fill_missing_area_table(self, doc):
        """「表X-X 面积统计表」只有表题、下面没有表 → 按土地分类面积补表。

        批注 2026-11-12「空白」。这张表可能来自模型 markdown、模板或裸表题
        （装配器事后补名），前面的 markdown 层修补覆盖不全，这里在**成稿层**兜底：
        只要表题下有数据就跳过；没有就按「复垦前/复垦后/增减」补一张。
        """
        if getattr(self, "_domain", None) != "reclamation":
            return
        filled = getattr(self, "_filled", None) or {}
        try:
            from app.services.reclamation_content_repairs import _land_targets
            _names, _area, per = _land_targets(filled)
        except Exception:
            return
        if not per:
            return
        from docx.oxml.ns import qn as _qn
        for p in list(doc.paragraphs):
            t = p.text.strip()
            if not re.match(r'^表\s*\d+\s*[-—－]\s*\d+\s*面积统计表', t):
                continue
            nxt = p._element.getnext()
            while (nxt is not None and nxt.tag == _qn('w:p')
                   and not "".join(nxt.itertext()).strip()):
                nxt = nxt.getnext()
            if nxt is not None and nxt.tag == _qn('w:tbl'):
                continue
            rows = [[name, f"{a:.4f}", f"{a:.4f}", "0.0000"] for name, a in per.items()]
            tot = sum(per.values())
            rows.append(["合计", f"{tot:.4f}", f"{tot:.4f}", "0.0000"])
            self._render_table_from_dict(
                doc, {"headers": ["地类", "复垦前面积（公顷）",
                                  "复垦后面积（公顷）", "增减（公顷）"],
                      "rows": rows}, caption="")
            _new = doc.tables[-1]._element
            p._element.addnext(_new)
            _sp = doc.add_paragraph()
            _new.addnext(_sp._element)
            logger.info("[面积统计表] 表题下缺表，已按土地分类面积补一张")

    def _fix_stability_area(self, doc):
        """以表6-1 的「合计」行为准，改写正文「总面积/地类构成」句。

        批注（2026-11-11）：正文只列了 8 个地类，合计比表6-1 少 3948 ㎡。
        表6-1 是勘测定界成果，权威；正文按表的列补齐，而不是把总数改小。
        """
        import re as _re
        total, pairs = None, []
        for tbl in doc.tables:
            if not tbl.rows:
                continue
            hdr = [c.text.strip() for c in tbl.rows[0].cells]
            if "面积总计" not in hdr or "耕地" not in hdr:
                continue
            ti = hdr.index("面积总计")
            skip = {"面积总计", "备注", "权属类别", "被征（用）地单位", "被征(用)地单位"}
            row = None
            for r in tbl.rows:
                if r.cells and "合计" in r.cells[0].text:
                    row = [c.text.strip() for c in r.cells]
                    break
            if not row:
                continue
            for k, name in enumerate(hdr):
                if k == ti or name in skip or not name:
                    continue
                try:
                    v = float(row[k].replace(",", ""))
                except (ValueError, IndexError):
                    continue
                if v > 0:
                    pairs.append((name, v))
            try:
                total = float(row[ti].replace(",", ""))
            except (ValueError, IndexError):
                total = sum(v for _n, v in pairs)
            break
        if total is None or not pairs:
            return

        def _fmt(x):
            return f"{x:.1f}" if abs(x - round(x)) > 1e-9 else f"{round(x)}"

        lst = "、".join(f"{n}{_fmt(v)}平方米" for n, v in pairs)
        # 「地类构成包括…」是旧写法；模型也会写「勘测定界成果显示，地类面积分别为：…」
        # 「地类面积按…分类确认」—— 都要认，否则正文合计仍比表6-1 少分项。
        _list_pat = re.compile(
            r'(地类构成包括|地类面积分别为[：:]?|地类构成为|地类面积按)([^。]+)(。)')

        def _rebuild_list(m):
            body = m.group(2)
            k = body.rfind("平方米")
            tail = body[k + 3:] if k >= 0 else ""
            return f"{m.group(1)}{lst}{tail}{m.group(3)}"

        for para in doc.paragraphs:
            txt = para.text
            if "拟征收土地总面积" not in txt:
                continue
            if not _list_pat.search(txt):
                continue
            new = _re.sub(r'(拟征收土地总面积)\s*[\d.]+\s*平方米',
                          rf'\g<1>{_fmt(total)}平方米', txt)
            new = _list_pat.sub(_rebuild_list, new, count=1)
            if new != txt:
                if not para.runs:
                    para.add_run(new)
                else:
                    para.runs[0].text = new
                    for r in para.runs[1:]:
                        r.text = ""
            break

    def _fix_stability_score_table(self, doc):
        """群众意见评分表按**实际反对率**重算得分，并与正文「得分为X分」对齐。

        批注（2026-11-11）：P60 表里得分为 0，正文却写 12.0 分；且表格第 3 行
        还留着模板的 100% 反对率（实际群众反对率 0%），算出 10.5 分。
        """
        filled = getattr(self, "_filled", {}) or {}
        try:
            oppose = float(str(filled.get("oppose_rate") or 0).replace("%", ""))
        except ValueError:
            oppose = 0.0
        total = None
        for tbl in doc.tables:
            if not tbl.rows:
                continue
            hdr = [c.text.strip() for c in tbl.rows[0].cells]
            if not any("反对率" in h for h in hdr) or not any("得分" in h for h in hdr):
                continue
            ai = next((i for i, h in enumerate(hdr) if "反对率" in h), None)
            si = next((i for i, h in enumerate(hdr) if "得分" in h), None)
            total = 0.0
            for r in tbl.rows[1:]:
                cells = r.cells
                if any("合计" in c.text for c in cells):
                    continue
                desc = " ".join(c.text for c in cells)
                b = 1.0
                if "很小" in desc:
                    b = 0.3
                elif "有可能" in desc:
                    b = 0.7
                score = round(oppose / 100.0 * b * 35.0, 2)
                total += score
                if ai is not None and ai < len(cells):
                    cells[ai].text = f"{oppose:.2f}%"
                if si is not None and si < len(cells):
                    cells[si].text = f"{score:.2f}"
            total = round(total, 2)
            for r in tbl.rows:
                if any("合计" in c.text for c in r.cells) and si is not None and si < len(r.cells):
                    r.cells[si].text = f"{total:.2f}"
            break
        if total is None:
            return
        for p in doc.paragraphs:
            t = p.text
            if "得分" not in t:
                continue
            # 正文的写法有两种：「三项合计得分为0.0分」「以定量分析法计算，得分12.0分」
            # —— 两种都要跟着表里的合计走，否则同一页里 0 分和 12 分并存。
            nt = re.sub(r'(得分)(为)?\s*[\d.]+\s*分', rf'\g<1>\g<2>{total:.1f}分', t)
            if nt != t:
                if not p.runs:
                    p.add_run(nt)
                else:
                    p.runs[0].text = nt
                    for r in p.runs[1:]:
                        r.text = ""

    def _drop_schematic_redline_text(self, doc):
        """断面/大样/做法/详图/设计图/结构图下面不写「图中红线范围…」。

        批注（2026-11-11）：「图6-1 临时拦挡措施断面示意图」下面被塞了一段
        「图中红线范围为本次复垦责任范围…面积 26,800 平方米…」，图上根本没有红线。
        `_add_figure_reading` 已按图名跳过，但模型自己写的解读段落还在正文里 ——
        这里按「示意图图注后紧跟红线解读」的形态统一删掉。
        """
        import re as _re
        _SCHEMA = ("断面", "大样", "做法", "详图", "设计图", "结构图")
        _CAP = _re.compile(r'^\s*图\s*\d+\s*[-—－]\s*\d+\s*(.*)$')
        _REDLINE = ("红线范围为本次复垦责任范围", "图中红线范围",
                    "上述边界即复垦工程、监测与管护的实际作业范围")
        _last_schema = False
        for p in list(doc.paragraphs):
            t = p.text.strip()
            m = _CAP.match(t)
            if m:
                _last_schema = any(k in m.group(1) for k in _SCHEMA)
                continue
            if not t:
                continue
            # 标题/新小节：示意图区间结束
            if p.style.name.startswith("Heading") or _re.match(
                    r'^(第[一二三四五六七八九十]+[章节]|[（(][一二三四五六七八九十]+[）)]|\d+(\.\d+)*\s)',
                    t):
                _last_schema = False
                continue
            if _last_schema and any(k in t for k in _REDLINE):
                p._element.getparent().remove(p._element)

    def _fix_truncated_paragraphs(self, doc):
        """正文里「话说到一半」的残句：回退到最后一个完整句子。"""
        from app.services.report_content_repairs import fix_truncated_paragraphs
        for p in doc.paragraphs:
            t = p.text
            if not t.strip() or len(t.strip()) < 12:
                continue
            if t.strip().startswith(("表", "图", "附件", "注：", "注:")):
                continue
            if p.style.name.startswith("Heading"):
                continue
            fixed, n = fix_truncated_paragraphs(t.strip())
            if n and fixed != t:
                if not p.runs:
                    p.add_run(fixed)
                else:
                    p.runs[0].text = fixed
                    for r in p.runs[1:]:
                        r.text = ""

    def _dedup_duplicate_tables(self, doc):
        """同章内容重复表格去重。

        LLM 偶尔会把同一张表（如"拟征收土地基本情况表"）在章节里写两次
        （1.6 小节一次、章节末尾又出现一次）。按"章"分组，同章内归一化表头
        完全相同的表视为重复：保留第一张（通常位置正确），删除后续。

        🔴 不会误删措施前/措施后评分表：这两张表表头相同但分别在 6、8 章。
        """
        body = doc.element.body
        ch = 0
        table_info = []  # [(chapter, hdr_key, tbl_element)]
        for child in body.iterchildren():
            if child.tag == qn('w:p'):
                txt = ''.join(n.text or '' for n in child.iter(qn('w:t'))).strip()
                # 同上：按域取章号，否则复垦所有表都被归到「第0章」一起判重
                _c = chapter_no_of_text(self._domain, txt)
                if _c:
                    ch = _c
            elif child.tag == qn('w:tbl'):
                trs = child.findall(qn('w:tr'))
                if not trs:
                    continue
                first_cells = trs[0].findall(qn('w:tc'))
                if not first_cells:
                    continue
                hdr = tuple(
                    self._clean_md_cell(''.join(n.text or '' for n in tc.iter(qn('w:t'))))
                    for tc in first_cells
                )
                # 🔴 表头归一化：去空白、去空括号（"总面积()"→"总面积"）、统一单位残留，
                # 避免同一张表因表头微差（如 PDF 版 vs LLM 版）去重失效。
                hdr_key = tuple(
                    h for h in
                    (re.sub(r'[（(）)\s]', '', h) for h in hdr)
                    if h
                )
                # 🔴 内容签名：取前2行数据的首个单元格，避免"表3-1(群众)"与"表3-3(部门)"
                # 表头同为「调查内容|选项|个数|比例」而被误判为重复。
                data_sig = ()
                if len(trs) > 1:
                    sig_cells = []
                    for _ri in range(1, min(3, len(trs))):
                        tc = trs[_ri].findall(qn('w:tc'))
                        if tc:
                            sig_cells.append(
                                re.sub(r'[（(）)\s]', '',
                                       self._clean_md_cell(''.join(n.text or '' for n in tc[0].iter(qn('w:t')))))
                            )
                    data_sig = tuple(c for c in sig_cells if c)
                if hdr_key and data_sig:
                    table_info.append((ch, hdr_key + ("\x00",) + data_sig, child))

        seen = set()
        remove = []
        for ch_i, hk, el in table_info:
            key = (ch_i, hk)
            if key in seen:
                remove.append(el)
            else:
                seen.add(key)
        for el in remove:
            # 🔴 删表要**连它上面的表题一起删**：否则留下一条孤零零的
            #    「表12-4 …」悬在正文里，读者找不到表（实测第12章）。
            prev = el.getprevious()
            if prev is not None and prev.tag == qn('w:p'):
                _pt = ''.join(n.text or '' for n in prev.iter(qn('w:t'))).strip()
                if re.match(r'^表\s*\d+[-—]\d+', _pt) and len(_pt) < 60:
                    prev.getparent().remove(prev)
            el.getparent().remove(el)
        if remove:
            logger.info(f"去重同章重复表格 {len(remove)} 张")

    def _pre_assembly_review(self, doc, filled=None):
        """Pre-assembly content quality review.

        Checks and fixes:
        1. AI meta-text / verbose descriptions in headings
        2. Section numbering consistency
        3. Empty paragraphs that should have content
        4. Stray table-like text that isn't a real table title
        """
        import re

        # 🔴 清理游离的「—」残留（占位符/表格分隔符误入正文，如「…提出以下工作建议：—」）
        for _p in doc.paragraphs:
            for _run in _p.runs:
                if _run.text:
                    _fixed = re.sub(r'[：:]\s*[—\-]{1,}\s*$', '：', _run.text)
                    _fixed = re.sub(r'^\s*[—\-]{2,}\s*$', '', _fixed)
                    if _fixed != _run.text:
                        _run.text = _fixed

        # 🔴 删除「一级标题下直接挂的正文」+「无意义前言」
        #    （正文必须挂在二级标题下；"本章从…不再重复罗列"这类空话一并删）
        try:
            _ps = list(doc.paragraphs)
            _kill = []
            _preamble_kw = ("本章从", "本章将", "不再重复罗列", "不再赘述", "本章主要", "下面从", "综上所述，本章")
            for _i, _p in enumerate(_ps):
                _t = (_p.text or "").strip()
                if not _t:
                    continue
                _st = _p.style.name if _p.style else ""
                # 无意义前言
                if len(_t) < 240 and any(_k in _t for _k in _preamble_kw):
                    _kill.append(_p)
                    continue
                # 一级标题后紧接着的正文段（在遇到任何二级标题之前）
                if _st.startswith("Heading 1"):
                    _j = _i + 1
                    while _j < len(_ps) and not (_ps[_j].text or "").strip():
                        _j += 1
                    if _j < len(_ps):
                        _nx = _ps[_j]
                        _nst = _nx.style.name if _nx.style else ""
                        if (not _nst.startswith("Heading")) and not _has_image(_nx):
                            _kill.append(_nx)  # 删掉一级标题下的游离正文
            for _p in _kill:
                try:
                    _p._element.getparent().remove(_p._element)
                except Exception:
                    pass
        except Exception:
            pass

        # ── Fix: Remove AI meta-text paragraphs ──
        ai_patterns = [
            r'^好的[，,]?\s*作为.*?(?:撰写|生成).*$',
            r'^以下是为您.*$',
            r'^遵照您的指示.*$',
            r'^根据您提供的要求.*$',
            r'^通过系统识别.*$',
            r'^依据指令要求.*$',
            r'^本次评估综合运用.*?(?:方法|手段).*$',
        ]
        for p in doc.paragraphs:
            t = p.text.strip()
            # Skip paragraphs with images (don't clear them)
            has_image = bool(p._element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing'))
            if has_image:
                continue
            for pat in ai_patterns:
                if re.match(pat, t):
                    p.clear()
                    p.text = ''
                    break

        # ── Fix: Remove long descriptive sub-sections in 1.2/1.3 ──
        # These should be concise unit names, not paragraphs
        for p in doc.paragraphs:
            t = p.text.strip()
            # Skip image paragraphs
            if p._element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing'):
                continue
            if any(kw in t for kw in [
                '作为本项目土地征收', '作为土地征收及相关',
                '负责统筹组织项目', '负责统筹推进项目',
                '该单位按照社会稳定', '负责按照社会稳定',
                '负责组织开展', '负责征地补偿',
                '依法履行征地公告', '依法组织开展征地',
                '对本项目社会稳定', '对项目社会稳定',
            ]):
                p.clear()
                p.text = ''

        # ── Fix: Trim verbose "决策名称" section ──
        for p in doc.paragraphs:
            t = p.text.strip()
            if re.match(r'(?:决策)?名称[为：:]\s*', t) and len(t) > 60:
                # Extract just the name portion
                m = re.search(r'(?:决策)?名称[为：:]\s*(.+?)(?:[，。]|本文|拟对)', t)
                if m:
                    name = m.group(1).strip()
                    for run in p.runs:
                        run.text = name

        # ── Fix: Clean stray "表" headers that aren't real ──
        for p in doc.paragraphs:
            t = p.text.strip()
            if t.startswith('表') and len(t) > 50:
                # Long text starting with 表 is probably not a title
                if any(kw in t for kw in ['表彰', '表现', '表示', '表明', '表格']):
                    pass  # Keep real table references
                elif '。' in t or '，' in t:
                    # Has sentence punctuation — not a table title
                    pass  # Keep as regular text

        logger.info(
            f"Pre-assembly review complete: {len(doc.paragraphs)} paras, "
            f"{len(doc.tables)} tables"
        )

    def _fill_signature_images(self, doc, filled=None):
        """第4章「参与人员签字」：在标签下插入签名库的手写签名图。"""
        from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
        from docx.shared import Cm as _Cm, Pt as _Pt
        from app.services.personnel_signature import (
            load_index, signature_or_placeholder)

        filled = filled or {}
        # 🔴 与前置工作组名单同源（人员库 / 勾选人员）；取不到才退回签名库键表。
        names = [str(p.get("姓名") or "").strip()
                 for p in (self._load_company_personnel() or [])
                 if str((p or {}).get("姓名") or "").strip()]
        if not names:
            names = list(getattr(self, "_selected_personnel", None)
                         or filled.get("_selected_personnel") or [])
        if not names:
            names = list(load_index().keys())
        seen = set()
        names = [n for n in names if not (n in seen or seen.add(n))]
        if not names:
            return
        target = None
        for p in doc.paragraphs:
            if p.text.strip().startswith("参与人员签字"):
                target = p
                break
        if target is None:
            return
        # 标签里的下划线去掉，改由签名图/空格位承担
        for r in target.runs:
            r.text = r.text.replace("_", "").replace("　", " ")
        # 每行最多 6 人
        rows = [names[i:i + 4] for i in range(0, len(names), 4)]
        anchor = target._p
        for chunk in rows:
            t = doc.add_table(rows=2, cols=len(chunk))
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            t.autofit = True
            try:
                from docx.oxml.ns import qn as _qn0
                t._tbl.tblPr.set(_qn0("w:tblCaption"), "_signature")
            except Exception:
                pass
            # 🔴 签名区不能画表格线：去掉全部边框与单元格边距，看起来就是手写签名
            try:
                from docx.oxml import OxmlElement as _OE
                from docx.oxml.ns import qn as _qn
                _tblPr = t._tbl.tblPr
                _bd = _OE("w:tblBorders")
                for _edge in ("top", "left", "bottom", "right",
                              "insideH", "insideV"):
                    _e = _OE("w:" + _edge)
                    _e.set(_qn("w:val"), "none")
                    _bd.append(_e)
                _tblPr.append(_bd)
                _mar = _OE("w:tblCellMar")
                for _side in ("top", "left", "bottom", "right"):
                    _m = _OE("w:" + _side)
                    _m.set(_qn("w:w"), "0"); _m.set(_qn("w:type"), "dxa")
                    _mar.append(_m)
                _tblPr.append(_mar)
            except Exception:
                pass
            for ci, nm in enumerate(chunk):
                c0 = t.cell(0, ci); c1 = t.cell(1, ci)
                for c in (c0, c1):
                    c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                p0 = c0.paragraphs[0]; p0.alignment = 1
                r0 = p0.add_run(str(nm))
                r0.font.name = FONT_SONGTI
                r0._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_SONGTI)
                r0.font.size = _Pt(10.5)
                p1 = c1.paragraphs[0]; p1.alignment = 1
                sig = signature_or_placeholder(str(nm))
                if sig:
                    try:
                        p1.add_run().add_picture(sig, height=_Cm(1.0))
                    except Exception as _e:
                        logger.warning(f"[签名] {nm} 插入失败: {_e}")
            anchor.addnext(t._tbl)
            anchor = t._tbl
        # 表格后补一个空段，避免与下一节文字贴在一起
        from docx.oxml import OxmlElement as _OE
        anchor.addnext(_OE("w:p"))

    def _post_generation_fix(self, doc, filled=None):
        """Post-generation: fix formatting, fonts, spacing, and AI artifacts."""
        import re as _re

        def _safe_re_sub(pattern, replacement, text, timeout=2.0):
            """Regex sub with timeout guard — prevents catastrophic backtracking."""
            result = [text]  # mutable container for result
            exception = [None]

            def _worker():
                try:
                    result[0] = pattern.sub(replacement, text)
                except Exception as e:
                    exception[0] = e

            import threading
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                return text  # Timeout — return unchanged
            if exception[0]:
                return text
            return result[0]

        facts = filled or {}

        # 🔴 删重复文本：清除相邻叠词（如「朱坝街道三圩社区朱坝街道三圩社区」→ 保留一次）。
        #   只处理 2-10 字的相邻重复短语，排除纯标点/数字（避免误删「55」「一一」等）。
        #
        # 🔴🔴 必须要求重复片段**以中文开头**：原来的 `(.{2,10})\1` 会匹配任意字符，
        #   于是三级标题编号 `4.2.2 合理性评估` 从第 2 个字符起命中 `.2` + `.2`，
        #   被收敛成 `4.2 合理性评估` —— 整章的 X.Y.Z 标题全部掉一级（实测第4章
        #   4.2.2→4.2、4.4.1→4.1、4.4.2→4.2）。任何 `X.Y.Y` 形式的编号都会被吃。
        _dup_phrase = re.compile(r'([一-龥][一-龥A-Za-z]{1,9})\1')
        def _dedup_adjacent(text):
            def _repl(m):
                frag = m.group(1)
                # 排除纯标点/数字/空白
                if re.fullmatch(r'[\d\s。，,、；;：:！!？?]+', frag):
                    return m.group(0)
                # 排除「等」「了」等单字虚词叠用（长度>=2已排除单字，这里再排虚词叠）
                if frag in ('等', '了', '的', '是', '在'):
                    return m.group(0)
                return frag
            return _dup_phrase.sub(_repl, text)
        for _p in list(doc.paragraphs):
            if _p.text and _dup_phrase.search(_p.text):
                new = _dedup_adjacent(_p.text)
                if new != _p.text:
                    for r in _p.runs: r.text = ''
                    if _p.runs: _p.runs[0].text = new
                    else: _p.add_run(new)
        for _tbl in doc.tables:
            for _row in _tbl.rows:
                for _cell in _row.cells:
                    for _p in _cell.paragraphs:
                        if _p.text and _dup_phrase.search(_p.text):
                            new = _dedup_adjacent(_p.text)
                            if new != _p.text:
                                for r in _p.runs: r.text = ''
                                if _p.runs: _p.runs[0].text = new
                                else: _p.add_run(new)

        # 🔴 应急响应等级归一化：统一为「一级/二级/三级响应」（消除"级响应"缺数字、编号混乱）
        _resp_map = [
            (re.compile(r'(?<![一二三])级响应\s*[（(]\s*一般事件'), '一级响应（一般事件'),
            (re.compile(r'(?<![一二三])级响应\s*[（(]\s*较大事件'), '二级响应（较大事件'),
            (re.compile(r'(?<![一二三])级响应\s*[（(]\s*(?:重大事件|特别重大事件)'), '三级响应（重大事件'),
        ]
        def _fix_resp(text):
            for pat, rep in _resp_map:
                text = pat.sub(rep, text)
            return text
        for _p in list(doc.paragraphs):
            if _p.text and '级响应' in _p.text:
                new = _fix_resp(_p.text)
                if new != _p.text:
                    for r in _p.runs: r.text = ''
                    if _p.runs: _p.runs[0].text = new
                    else: _p.add_run(new)
        for _tbl in doc.tables:
            for _row in _tbl.rows:
                for _cell in _row.cells:
                    for _p in _cell.paragraphs:
                        if _p.text and '级响应' in _p.text:
                            new = _fix_resp(_p.text)
                            if new != _p.text:
                                for r in _p.runs: r.text = ''
                                if _p.runs: _p.runs[0].text = new
                                else: _p.add_run(new)

        # 🔴 Pre-scan: find all caption paragraphs (immediately after image paragraphs)
        # These must NEVER be modified — they contain 图X-X labels
        # 🔴 必须按 **lxml 元素**判身份，不能按 `id(Paragraph)`：
        #    `doc.paragraphs` 每次调用都新建一批 Paragraph 代理对象，代理被回收后
        #    地址会被后面的新建代理复用 —— `id()` 时对时错，实测把「图5-1 社会稳定
        #    风险处理流程图」当普通段落清空了（图注整条消失）。
        _caption_els = set()
        _prev_img = False
        for _p in doc.paragraphs:
            _has_dw = bool(_p._element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing'))
            if _prev_img and _p.text.strip():
                _caption_els.add(_p._element)
            _prev_img = _has_dw

        # ── 1. Fill placeholders with real data ──
        missing_data = []
        loc = facts.get("location", "")
        area = facts.get("area_mu", "") or facts.get("area_m2", "")
        comp = facts.get("compensation_standard", "")
        house = facts.get("household_count", "")
        fills = {}
        if loc: fills['拟征收土地位置'] = f'本次拟征收土地位于{loc}。'
        if area: fills['拟征收土地范围及面积'] = f'拟征收土地总面积约{area}亩。'
        if comp: fills['土地征收资金测算'] = f'征收补偿标准按{comp}执行，资金已纳入财政预算。'
        if house: fills['涉及户数'] = f'本次征收涉及约{house}户被征地群众。'
        placeholder_fills = {
            '责任单位': facts.get("decision_unit", "") or facts.get("org_name", ""),
            '实施单位': self._cv("implement_unit"),
            '位置': facts.get("land_location", "") or facts.get("location", ""),
            '面积': f"{facts.get('area_mu', '')}亩" if facts.get('area_mu') else '',
            '户数': f"{facts.get('household_count', '')}户" if facts.get('household_count') else '',
            '补偿标准': facts.get('compensation_standard', '按照江苏省区片综合地价标准执行'),
            '文号': facts.get('doc_reference', ''),
            '项目名称': facts.get('project_name', ''),
            '公示期': facts.get('announcement_period', ''),
        }

        # Helper: check if paragraph is an image or caption — NEVER modify these
        def _has_image(para):
            if para._element in _caption_els:
                return True
            return bool(para._element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing'))

        for p in doc.paragraphs:
            if _has_image(p):
                continue  # NEVER touch image paragraphs
            for run in p.runs:
                t = run.text
                if not t:
                    continue
                # Replace 【待补充：XXX】 with actual data if available
                m = re.match(r'【待补充[：:]*(.*?)】', t)
                if m:
                    key_hint = m.group(1).strip() if m.group(1) else ''
                    found = False
                    for k, v in placeholder_fills.items():
                        if v and (k in key_hint or k in t):
                            run.text = t.replace(m.group(0), str(v))
                            found = True
                            break
                    if not found:
                        for fk, fv in facts.items():
                            if fv and not fk.startswith('_') and fk in key_hint:
                                run.text = t.replace(m.group(0), str(fv)[:100])
                                found = True
                                break
                    if not found:
                        missing_data.append(key_hint or t[:40])
                        run.text = t.replace(m.group(0), f'【需补充：{key_hint or "相关数据"}】')

        # ── 1.5 模板项目名泄漏清理 ──
        template_fixes = [
            (r'洞庭湖路\s*[（(]\s*S350\s*[-—]\s*宁连一级路段\s*[）)]\s*工程', '本项目'),
            (r'洞庭湖路\s*[（(]\s*S350\s*[-—]\s*宁连一级路段\s*[）)]', '本项目'),
            (r'洞庭湖路', '本项目所在道路'),
            (r'S350\s*[-—]\s*宁连一级路段', '本项目所在路段'),
            (r'G25至G205段', '本项目征收范围'),
            (r'S350至G25段', '本项目征收范围'),
            # 🔴 征收目的段落开头不要"的规定"三个字
            (r'^\s*的规定[，,]?\s*', ''),
            (r'^根据《[^》]+》第[^条]+条规定[，,]?\s*', ''),
        ]
        # 🔴 项目名末尾的位置括注一律去掉：
        #    「…商业服务业设施用地项目（朱坝街道办事处、三圩社区…）」→「…商业服务业设施用地项目」。
        #    业主口径：项目名不带坐落，位置在第6章单独写。
        #    必须**按段落整体**匹配 —— 逐 run 替换在名称被拆成多个 run 时会漏；
        #    也不能用事实卡里的名字推长名（它已被清洗成短名）。
        # 🔴 项目名末尾的位置括注一律去掉：
        #    「…商业服务业设施用地项目（朱坝街道办事处、三圩社区…）」→「…商业服务业设施用地项目」。
        #    业主口径：项目名不带坐落，位置在第6章单独写。
        #    规则做成**通用式**：只要「项目」后面紧跟的括注里含地名特征词就去掉 ——
        #    （不依赖事实卡字段是否齐全，也兼容「7号商业…」这种号后无空格的写法；
        #     括注里没有地名词的「（一期）」「（送审稿）」不会被误删。）
        _loc_paren = re.compile(
            r'(项目)\s*[（(][^（()）]{0,60}[）)]')
        _hints = ("街道", "镇", "乡", "村", "社区", "组", "农场", "管理区", "片区")
        for _p in doc.paragraphs:
            if _has_image(_p):
                continue
            _txt = _p.text
            if not _txt or '项目（' not in _txt and '项目(' not in _txt:
                continue

            def _repl(m):
                return m.group(1) if any(h in m.group(0) for h in _hints) else m.group(0)

            _new_txt = _loc_paren.sub(_repl, _txt)
            if _new_txt == _txt or not _p.runs:
                continue
            _p.runs[0].text = _new_txt
            for _r in _p.runs[1:]:
                _r.text = ""

        for p in doc.paragraphs:
            if _has_image(p):
                continue  # NEVER touch image paragraphs
            for run in p.runs:
                t = run.text
                if not t:
                    continue
                for pattern, replacement in template_fixes:
                    t = re.sub(pattern, replacement, t)
                run.text = t
        # Also check tables for template leakage
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            t = run.text
                            for pattern, replacement in template_fixes:
                                t = re.sub(pattern, replacement, t)
                            run.text = t

        # ── 1.6 模糊表述替换为实际数据 ──
        #    （「项目所在地」「拟征收土地位于」等正常前缀短语不替换——会把完整句改坏成重复/残缺句）
        vague_fills = {}
        area_mu = facts.get("area_mu", "") or facts.get("area_m2", "")
        area_m2 = facts.get("area_m2", "")
        land_use = facts.get("land_use", "") or facts.get("land_purpose", "")
        if area_mu:
            vague_fills['一定规模的土地'] = f'{area_mu}亩'
            vague_fills['涉及一定规模'] = f'约{area_mu}亩'
            # 🔴 这里原来是**字面量** `{area_mu}*666.67` —— 表达式不会被求值，
            #    会原样印成「（合28.3935*666.67㎡）」（实测踩过）。
            #    现在真的算出来：优先用事实卡里的 m²（勘测定界口径），
            #    没有才由亩换算（1 亩 = 666.67 ㎡）。
            _m2 = str(area_m2).strip()
            if not _m2:
                try:
                    _m2 = f"{round(float(area_mu) * 666.67, 1):g}"
                except (TypeError, ValueError):
                    _m2 = ""
            _tail = f'（合{_m2}㎡）' if _m2 else ''
            vague_fills['以正式征收公告为准'] = (
                f'（具体面积以正式征收公告为准，本次评估依据勘测定界报告，'
                f'总面积约{area_mu}亩{_tail}）')
        if land_use:
            vague_fills['主要用于公共基础设施建设'] = f'主要用于{land_use}'
            vague_fills['公共基础设施建设'] = land_use

        for p in doc.paragraphs:
            if _has_image(p):
                continue
            for run in p.runs:
                t = run.text
                if not t:
                    continue
                for vague, concrete in vague_fills.items():
                    if vague in t and concrete:
                        t = t.replace(vague, concrete)
                run.text = t
        # Also in tables
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            t = run.text
                            for vague, concrete in vague_fills.items():
                                if vague in t and concrete:
                                    t = t.replace(vague, concrete)
                            run.text = t

        # ── 2. Transform AI artifacts into professional text ──
        # Map: artifact pattern → professional replacement

        # Build whitelist of verified legal citations from seed data + template
        known_articles = set()
        try:
            from pathlib import Path
            seed_dir = Path(__file__).parent.parent.parent.parent / "seed_data"
            for fname in ['land_management_law.md', 'db32_t4013_2021.md', 'national_guideline_428.md',
                          'stability_assessment_guideline.md', 'emergency_response_law.md']:
                f = seed_dir / fname
                if f.exists():
                    text = f.read_text(encoding='utf-8')
                    import re as _re_art
                    for m in _re_art.finditer(r'第[四五六七八九十\d]+条', text):
                        known_articles.add(m.group(0))
        except Exception:
            pass  # If can't build whitelist, skip verification

        line_transforms = [
            # "[待评审] X: N分" → "X：经初步评估，该项得分N分"
            (re.compile(r'\[待评审\]\s*(.+?):\s*(\d+)\s*分\s*/?\s*满分\s*(\d+)'),
             r'\1：经初步评估，该项得分为\2分（满分\3分），建议在实施过程中进一步核实确认。'),
            # "> 依据: ..." → remove the line
            (re.compile(r'^.*>\s*依据:.*$', re.MULTILINE), ''),
            # "> 自动评分项: N | 待专家评审项: N" → remove
            (re.compile(r'^.*自动评分项.*待专家评审项.*$', re.MULTILINE), ''),
            # "> 综合得分: ..." → remove
            (re.compile(r'^.*>\s*综合得分:.*$', re.MULTILINE), ''),
            # "【待专家评审】需舆情监测数据支持" → proper sentence
            (re.compile(r'【待专家评审】'), ''),
            # "（待确认）" → actual data or remove
            (re.compile(r'（待确认）'), ''),
            # "[自动]" tags → remove
            (re.compile(r'\[自动\]'), ''),
        ]

        # ── Remove LLM filler phrases ──
        # NOTE: avoid .{0,N} alternations that cause catastrophic backtracking.
        # Use targeted keyword matching instead.
        filler_keywords = [
            '待根据具体数据', '待进一步', '待相关', '待项目实施',
            '具体数据有待', '具体数据需', '建议相关',
            '需进一步跟踪', '需后续关注', '需进一步监测',
        ]
        # Build a simple anchored pattern that won't backtrack catastrophically
        _filler_parts = []
        for kw in filler_keywords:
            _filler_parts.append(re.escape(kw) + r'[^。\n]{0,80}[。]?')
        filler_pattern = re.compile('|'.join(_filler_parts))

        # 🔴 只删「纯套话」（规则见模块级 `_AI_BOILERPLATE_RE` 注释）
        ai_boilerplate_pattern = _AI_BOILERPLATE_RE
        report_boilerplate = re.compile(
            r'本(?:章|节|报告)严格[^。\n]{0,60}(?:标准|规范)[^。\n]{0,40}[。]?'
        )

        # ── Verify legal citations: replace unknown article numbers ──
        unknown_article_pattern = re.compile(
            r'(《[^》]+》)\s*第[四五六七八九十\d]+条'
        )

        for p in doc.paragraphs:
            if _has_image(p):
                continue
            # Check for fabricated legal citations
            if known_articles:
                for run in p.runs:
                    m = unknown_article_pattern.search(run.text)
                    if m:
                        full_cite = m.group(0)
                        article_part = re.search(r'第[四五六七八九十\d]+条', full_cite)
                        if article_part and article_part.group(0) not in known_articles:
                            # Replace fabricated article with just the law name
                            run.text = run.text.replace(full_cite, m.group(1))
                            logger.info(f"Removed unverified citation: {full_cite}")

        for p in doc.paragraphs:
            if _has_image(p):
                continue
            # Apply line transforms first
            t = p.text
            modified = False
            for pattern, replacement in line_transforms:
                new_t = pattern.sub(replacement, t)
                if new_t != t:
                    t = new_t
                    modified = True

            # Then remove filler phrases (with timeout guard)
            new_t = _safe_re_sub(filler_pattern, '【待补充】', t)
            if new_t != t:
                t = new_t
                modified = True
            new_t = _safe_re_sub(ai_boilerplate_pattern, '', t)
            if new_t != t:
                t = new_t
                modified = True
            new_t = _safe_re_sub(report_boilerplate, '', t)
            if new_t != t:
                t = new_t
                modified = True
            if modified:
                # If the entire paragraph is just filler, remove it
                if len(t.strip()) < 20:
                    for run in p.runs:
                        run.text = ''
                    continue

            if modified:
                # Clear all runs and set first run to cleaned text
                for run in p.runs:
                    run.text = ''
                cleaned = t.strip()
                # Remove empty lines
                cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
                if cleaned and p.runs:
                    p.runs[0].text = cleaned

        # ── 2.5 Enforce standard headings (strip LLM-invented titles) ──
        try:
            from app.agent.template_headings import CHAPTER_HEADINGS
            # Build set of all approved headings
            approved = set()
            for ch_num, headings in CHAPTER_HEADINGS.items():
                for h in headings:
                    approved.add(h)
                    # Also add without number prefix for fuzzy matching
                    if '.' in h:
                        approved.add(h.split('.', 1)[1].strip())
            # Scan paragraphs: if it looks like a heading but not approved, demote to body
            heading_pattern = re.compile(r'^(\d+\.\d+)\s+(.+)$')
            heading_pattern2 = re.compile(r'^[（(][一二三四五六七八九十]+[）)]\s*')
            # 🔴 Catch LLM-invented score headings like "合法性（得分: 10分）"
            llm_score_heading = re.compile(r'^(合法性|合理性|可行性|可控性)[（(]得分[：:]\s*\d+')
            for p in doc.paragraphs:
                if _has_image(p):
                    continue
                t = p.text.strip()
                if not t or len(t) > 60: continue
                style = p.style.name if p.style else ''
                is_heading_style = 'Heading' in (style or '')
                is_llm_heading = bool(llm_score_heading.match(t))
                if not is_heading_style and not heading_pattern.match(t) and not heading_pattern2.match(t) and not is_llm_heading:
                    continue
                # Check if this heading text is in approved list
                clean = t
                # Normalize: remove numbering prefix
                m = heading_pattern.match(t)
                if m:
                    clean = m.group(2).strip()
                if clean not in approved and t not in approved:
                    # Unapproved heading → change paragraph style to Normal (body text)
                    try:
                        p.style = p._element.getparent().part.styles['Normal']
                    except Exception:
                        pass
                    for run in p.runs:
                        run.font.size = Pt(14)
                        run.font.name = FONT_BODY
                        run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_BODY)
                        run.font.bold = True  # Keep bold for emphasis
        except Exception:
            pass

        # ── 2.6 兜底修正结论处的错误风险等级（与系统权威值对齐）──
        try:
            ra = (getattr(self, '_session_state', None) or {}).get("_risk_assessment") or {}
            risk_level = ra.get("level", "")
            if risk_level:
                risk_ctx = ['结论', '评定为', '等级为', '综合', '最终', '判定为', '措施前', '措施后', '整体']
                for p in doc.paragraphs:
                    for run in p.runs:
                        for wrong in ('高风险', '中风险'):
                            if wrong != risk_level and wrong in run.text:
                                if any(ctx in p.text for ctx in risk_ctx):
                                    run.text = run.text.replace(wrong, risk_level)
        except Exception:
            pass

        # ── 3. 标题字体归一化（对齐参照件实测值）──
        # 参照件：1级 宋体16pt 不加粗 居中 ／ 2级 15pt 加粗 ／ 3级 14pt 加粗
        for p in doc.paragraphs:
            if _has_image(p):
                continue
            style_name = (p.style.name or '') if p.style else ''
            is_h1 = 'Heading 1' in style_name
            is_h2 = 'Heading 2' in style_name
            is_h3 = 'Heading 3' in style_name

            # 🔴 只归一化「标题」字体；正文(Normal)字体已由 _add_para / _add_toc /
            #    _add_compilation_note 设好，统一改会覆盖「目录/编制说明」应使用的宋体，故跳过 Normal。
            if not (is_h1 or is_h2 or is_h3):
                continue
            for run in p.runs:
                if not run.text.strip():
                    continue
                if is_h1:
                    # 🔴 字号只按层级定，不看文本（旧版按文本含「封面」就把字号抬到 24pt）。
                    #    字体/字号**按域取** —— 这里是全文最后一次刷标题字体，
                    #    不跟着域走的话，上面 `_add_heading` 按域设的值会被这里刷回稳评那套。
                    _ea, _pt = self._typo("h1")
                    _set_run_font(run, _ea, size=Pt(_pt), bold=self._heading_bold(1), ascii_font=_ea)
                elif is_h2:
                    _ea, _pt = self._typo("h2")
                    _set_run_font(run, _ea, size=Pt(_pt), bold=self._heading_bold(2), ascii_font=FONT_ASCII)
                else:  # h3
                    _ea, _pt = self._typo("h3")
                    _set_run_font(run, _ea, size=Pt(_pt), bold=self._heading_bold(3), ascii_font=FONT_ASCII)

        # ── 4. Line spacing to 28pt ──
        from docx.shared import Pt as _Pt2
        from docx.enum.text import WD_LINE_SPACING
        # ── 4. 行距（随 12pt 正文；图片段一并处理，旧版跳过导致图段行距失控）──
        from docx.shared import Pt as _Pt2
        from docx.enum.text import WD_LINE_SPACING
        for p in doc.paragraphs:
            pf = p.paragraph_format
            # 🔴 必须用**最小值(atLeast)**而不是固定值(exact)：
            #    固定值会把**大于该值的字号压扁并裁掉字的上缘** ——
            #    实测评审表标题（22pt）被 20pt 固定行高裁掉了上方笔画。
            #    atLeast 表示「行高不低于 20pt」，字号大时自动撑开。
            pf.line_spacing_rule = WD_LINE_SPACING.AT_LEAST
            pf.line_spacing = _Pt2(LINE_SPACING_PT)

        # ── 5. Clean verbose unit/role descriptions ──
        for p in doc.paragraphs:
            if _has_image(p):
                continue
            t = p.text.strip()
            if any(kw in t for kw in [
                '本决策主要涉及', '项目文号为', '该决策对应文号',
                '作为本项目土地征收', '作为土地征收及相关',
                '负责统筹组织项目', '负责统筹推进项目',
                '该单位按照社会稳定', '负责按照社会稳定',
                '负责组织开展', '负责征地补偿',
                '依法履行征地公告', '依法组织开展征地',
                '对本项目社会稳定', '对项目社会稳定',
            ]):
                p.clear()
                p.text = ''

        # ── 6. Normalize empty paragraphs / excessive spacing ──
        empty_count = 0
        for p in doc.paragraphs:
            if _has_image(p):
                continue
            # 🔴 **分页符段落不是空段**：判据只看有没有文字，而「另起一页」用的正是
            #    一个没有文字的段落（`w:br w:type="page"`）—— 于是它被当空段删掉，
            #    该分页的地方不再分页。实测 2026-10-10：复垦扉页与目录之间的分页符
            #    被删 → **扉页和目录挤在同一页**（业主：「第二页又有目录又有信息」）。
            #    分页符是**版式指令**，遇到就当内容，并把连续空段计数清零。
            if any((b.get(qn('w:type')) or '') == 'page'
                   for b in p._element.findall('.//' + qn('w:br'))):
                empty_count = 0
                continue
            if not p.text.strip():
                empty_count += 1
                if empty_count > 2:  # Max 2 consecutive empty paragraphs
                    p._element.getparent().remove(p._element)
            else:
                empty_count = 0

        # ── 6.2 删「光标题」：标题底下既没正文、也没表、也没图、也没有下级标题 ──
        # 🔴 「不留空」是业主硬约束。实测踩过：第6章「5.建设规模及内容」的内容只有
        #    一个 `[SYS_TABLE:6-1]` 标记，而该表当时渲染不出来（勘测定界没解析成功），
        #    于是报告里留下一个**光秃秃的标题** —— 比缺这一条更难看。
        #    这里做全局兜底（不限第6章）：一条标题若在自己的层级范围内什么都统领不到，
        #    就整条删掉。
        # 🔴 必须按**层级**判，不能只看「下一个标题之前有没有内容」：
        #    「第六章 项目基本情况」紧接着「（一）项目概况」是正常层级结构，
        #    按朴素规则会被当成光标题删掉 —— 那就把章标题删了。
        try:
            _kids = list(doc.element.body)

            def _h_level(el):
                """标题级别；不是标题返回 None。风格名 `Heading N` / `标题 N`。"""
                if el.tag != qn('w:p'):
                    return None
                _st = el.find(qn('w:pPr') + '/' + qn('w:pStyle'))
                if _st is None:
                    return None
                _v = str(_st.get(qn('w:val')) or "")
                if not (_v.lower().startswith("heading") or _v.startswith("标题")):
                    return None
                _m = re.search(r'(\d+)', _v)
                return int(_m.group(1)) if _m else 1

            def _has_content(el):
                if el.tag == qn('w:tbl'):
                    return True
                if el.tag != qn('w:p'):
                    return False
                if (el.find('.//' + qn('w:drawing')) is not None
                        or el.find('.//' + qn('w:pict')) is not None
                        or el.find('.//' + qn('w:object')) is not None):
                    return True
                return bool("".join(t.text or "" for t in el.iter(qn('w:t'))).strip())

            _removed = 0
            for _i, _el in enumerate(_kids):
                _lv = _h_level(_el)
                if _lv is None:
                    continue
                _useful = False
                for _nx in _kids[_i + 1:]:
                    _nlv = _h_level(_nx)
                    if _nlv is not None:
                        if _nlv <= _lv:
                            break          # 本层级范围到此为止
                        _useful = True         # 有下级标题 → 不是光标题
                        break
                    if _has_content(_nx):
                        _useful = True
                        break
                if not _useful:
                    _el.getparent().remove(_el)
                    _removed += 1
            if _removed:
                logger.info(f"[后处理] 删除 {_removed} 个空标题")
        except Exception as _e:
            logger.warning(f"[后处理] 空标题清理跳过: {_e}")

        # ── 6.5 Fill placeholder paragraphs with real data ──
        for i, p in enumerate(doc.paragraphs):
            t = p.text.strip()
            for kw, fill_text in fills.items():
                if kw in t and len(t) < 40 and fill_text:
                    # Check if next paragraph is empty or a placeholder
                    nt = ''
                    if i + 1 < len(doc.paragraphs):
                        nt = doc.paragraphs[i + 1].text.strip()
                    # Also consider placeholder text (e.g. "总面积㎡") as empty
                    is_placeholder = (not nt or ('㎡' in nt and not any(c.isdigit() for c in nt))
                                      or ('亩' in nt and not any(c.isdigit() for c in nt))
                                      or len(nt) < 15)
                    if is_placeholder:
                        new_p = doc.add_paragraph(fill_text)
                        p._element.addnext(new_p._element)
                    break

        # Fix table cells too
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            for old, new in fills.items():
                                if old in run.text:
                                    run.text = run.text.replace(old, new)

        # ── Clean up bare "—" cells: replace with context-aware values ──
        # 🔴 报告表（复垦第11部分）里的空白格**保持空白**：那是给人手填的位置，
        #    不是「数据缺失」。业主 2026-11-02 点名要求不要用「-」代替。
        _form_tbls = getattr(self, '_form_tables', []) or []
        _keep_blank = getattr(self, '_keep_blank_tables', set()) or set()
        for tbl in doc.tables:
            if tbl._tbl in _form_tbls or tbl._tbl in _keep_blank:
                continue
            hcells = [c.text.strip() for c in tbl.rows[0].cells]
            for row in tbl.rows[1:]:
                for ci, cell in enumerate(row.cells):
                    ct = cell.text.strip()
                    if ct == '—' or ct == '' or ct == '待统计':
                        # Try to determine context from column header
                        col_hdr = hcells[ci] if ci < len(hcells) else ''
                        col_hdr_lower = col_hdr.lower()
                        # Heuristic fills based on column context
                        if any(kw in col_hdr for kw in ['得分', '评分', '分数']):
                            self._set_cell(cell, '—')  # Legitimate: not yet scored
                        elif any(kw in col_hdr for kw in ['增幅', '变化率', '增长率']):
                            self._set_cell(cell, '基本持平')
                        elif any(kw in col_hdr for kw in ['备注', '说明', '注']):
                            self._set_cell(cell, '—')  # No remarks needed
                        else:
                            # 🔴 不再写「【待补充】」：业主硬约束是**成稿里不许出现占位符**，
                            #    而这里大量命中勘测定界表里本就为空的地类格（表示面积为 0），
                            #    填成「—」既诚实又不污染交付稿。
                            self._set_cell(cell, '—')

        # Remove duplicate table titles (keep only first occurrence)
        seen_titles = set()
        for p in doc.paragraphs:
            if _has_image(p):
                continue
            t = p.text.strip()
            if t.startswith('表') and len(t) < 60:
                if t in seen_titles:
                    p.clear(); p.text = ''
                else:
                    seen_titles.add(t)

        # Remove orphan image markers
        markers_to_remove = [
            r'^图\d+[-—]\d+\s+.+$',
            r'^\[待补充\].*$',
        ]
        for p in doc.paragraphs:
            if _has_image(p):
                continue
            text = p.text.strip()
            for pattern in markers_to_remove:
                if re.match(pattern, text) and len(text) < 40:
                    p.clear()
                    p.text = ''
                    break

    # ═══════════════════════════════════════════════════════════════
    # Document Setup
    # ═══════════════════════════════════════════════════════════════

        # 🔴 去掉文档**末尾的空白页**：最后一个段落只剩一个分页符（或全空）时，
        #    Word 会多渲染一整页空白 —— 打印/装订就多一张（业主：「最后一页空白」）。
        #    来源是模板区间/附件区末尾自带的分页符。
        # 🔴 例外：末尾是「附件清单」时**不清** —— 附件区要留白两页给粘贴扫描件
        #    （业主 2026-11-20 口径）。装配器在 `_add_appendices` 里已插好，
        #    这里再清就会把这两页删掉（实测 2026-11-23 洪泽测试稿）。
        try:
            _body = doc.element.body
            _tail_text = ""
            for _p in reversed(doc.paragraphs):
                if _p.text.strip():
                    _tail_text = _p.text.strip()
                    break
            _rm = 0
            while _tail_text != "附件清单":
                _kids = [c for c in _body.iterchildren()
                         if c.tag.split('}')[1] in ('p', 'tbl')]
                if not _kids or _kids[-1].tag.split('}')[1] != 'p':
                    break
                _last = _kids[-1]
                _txt = ''.join(t.text or '' for t in _last.iter(qn('w:t'))).strip()
                _brs = _last.findall('.//' + qn('w:br'))
                _pb = any((b.get(qn('w:type')) or '') == 'page' for b in _brs)
                # 🔴 **图片段不是空段**：判据只看了 `<w:t>` 文字，而插图段一个 `w:t`
                #    都没有 —— 于是文档**末尾的图会被逐段删掉**（上限 10 段）。
                #    实测 2026-10-10：复垦附件20「主体工程批复文件」的 7 页扫描件被删光，
                #    文档停在标题那一行（且 `word/media` 里留着 7 个没人引用的孤儿图）。
                #    稳评附件章的末尾图同样中招。有 `w:drawing` 一律停手。
                if _last.find('.//' + qn('w:drawing')) is not None:
                    break
                if _txt or (_brs and not _pb):
                    break                      # 有文字 / 非分页符 → 停
                _last.getparent().remove(_last)
                _rm += 1
                if _rm > 10:
                    break
            if _rm:
                logger.info(f"[DOCX] 清理末尾空白页段落 {_rm} 个")
        except Exception as _cle:
            logger.debug(f"末尾空白页清理跳过: {_cle}")

        # 🔴 目录域的缓存条目（不刷新域的阅读器 —— WPS / 转 PDF —— 只看得到缓存）
        try:
            _n = self._fill_toc_cache(doc)
            if _n:
                logger.info(f"[DOCX] 目录缓存条目写入 {_n} 条")
        except Exception as _te:
            logger.warning(f"目录缓存写入失败: {_te}")

    def _setup_page(self, doc: Document):
        """A4 纵向 + 统一页边距（对齐参照件实测值）。

        🔴 必须显式设 `orientation`：`page_width/page_height` 只写 `w:pgSz` 的 w/h，
        **不会清掉 `w:orient="landscape"` 属性**。模板里夹带的节属性会把它带进来，
        Word 于是把整节渲染成横向（实测报告第 0 节就是这样变成 A4 横向的）。
        """
        from docx.enum.section import WD_ORIENT
        for sec in doc.sections:
            sec.orientation = WD_ORIENT.PORTRAIT
            sec.page_width = Cm(21)
            sec.page_height = Cm(29.7)
            # 🔴 边距对齐**同类模板报告**（食品产业园案卷实测：上2.30 下2.30 左3.00 右2.50）。
            #    原来是 2.54/2.54/3.17/3.17（照参照件白马湖量的）——
            #    左右各宽 0.17cm，版心窄了，正文行数偏多。
            sec.top_margin = Cm(2.30)
            sec.bottom_margin = Cm(2.30)
            sec.left_margin = Cm(3.00)
            sec.right_margin = Cm(2.50)
            sec.header_distance = Cm(1.50)
            sec.footer_distance = Cm(1.75)

    def _setup_header_footer(self, doc: Document, header_text: str = ""):
        """给正文各节补**页眉抬头 + 页脚页码**。

        🔴 为什么原来完全没有
            装配用的是 `doc = Document()`（python-docx 自带空白模板），模板里的
            页眉页脚**没有被带过来**；实测生成的 docx 包里既没有 `footer*.xml`
            也没有 `header*.xml`，而 sectPr 里却残留一个 `footerReference`
            其 `r:id="rId3"` 实际指向 `styles.xml`（**悬空错误引用**）。
            结果：报告全篇没有页码，Word 还可能提示文档有问题。

        版式（对齐参照件/人工案卷）：
            · 页脚：居中，`PAGE` 域，Times New Roman 小四（12pt）纯数字
            · 页眉：居中，宋体小五（9pt）报告名
            · 封面 / 资质 / 简介 / **目录**所在的第一节**不显示**页眉页脚
            · 正文第一节页码从 **1** 重新起算（前面的前置页不计入）
        """
        from docx.oxml import OxmlElement

        _secs = list(doc.sections)
        if not _secs:
            return

        for _i, sec in enumerate(_secs):
            if _i == 0:
                # 前置部分（封面→资质→简介→目录）：页眉页脚留空。
                # 🔴 显式建**空部件**，顺带把上面那个指向 styles.xml 的
                #    悬空 footerReference 覆盖成正常引用。
                for _part in (sec.header, sec.footer):
                    _part.is_linked_to_previous = False
                    for _p in list(_part.paragraphs):
                        _p.clear()
                continue

            if _i == 1:
                # 正文首页：建页脚页码 + 页眉抬头
                _fp = sec.footer
                _fp.is_linked_to_previous = False
                _p0 = _fp.paragraphs[0] if _fp.paragraphs else _fp.add_paragraph()
                for _extra in list(_fp.paragraphs)[1:]:
                    _extra.clear()
                _p0.clear()
                _p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _run = _p0.add_run()
                _set_run_font(_run, FONT_PAGE_NUM, size=Pt(12), ascii_font=FONT_PAGE_NUM)
                _fc1 = OxmlElement('w:fldChar')
                _fc1.set(qn('w:fldCharType'), 'begin')
                _it = OxmlElement('w:instrText')
                _it.set(qn('xml:space'), 'preserve')
                _it.text = ' PAGE '
                _fc2 = OxmlElement('w:fldChar')
                _fc2.set(qn('w:fldCharType'), 'end')
                for _el in (_fc1, _it, _fc2):
                    _run._r.append(_el)

                if header_text:
                    _hp = sec.header
                    _hp.is_linked_to_previous = False
                    _h0 = _hp.paragraphs[0] if _hp.paragraphs else _hp.add_paragraph()
                    for _extra in list(_hp.paragraphs)[1:]:
                        _extra.clear()
                    _h0.clear()
                    _h0.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    _hr = _h0.add_run(header_text)
                    _set_run_font(_hr, FONT_SONGTI, size=Pt(9), ascii_font=FONT_ASCII)

                # 页码从 1 重新起算。⚠️ `w:pgNumType` 在 sectPr 里有 schema 顺序约束
                #    （必须排在 `w:pgSz`/`w:pgMar` 之后、`w:cols` 之前），插错位置 Word 会忽略整节属性。
                _sectPr = sec._sectPr
                for _old in _sectPr.findall(qn('w:pgNumType')):
                    _sectPr.remove(_old)
                _pn = OxmlElement('w:pgNumType')
                _pn.set(qn('w:start'), '1')
                _anchor = _sectPr.find(qn('w:cols'))
                if _anchor is None:
                    _anchor = _sectPr.find(qn('w:docGrid'))
                if _anchor is not None:
                    _anchor.addprevious(_pn)
                else:
                    _sectPr.append(_pn)
            else:
                # 其余各节（各章标题页起的节、附件节）**继承**正文节的页眉页脚，
                # 不再各建一份部件（否则 14 章 = 14 套 header/footer，文档白胖一圈）
                sec.header.is_linked_to_previous = True
                sec.footer.is_linked_to_previous = True

    # ═══════════════════════════════════════════════════════════════
    # Cover Page
    # ═══════════════════════════════════════════════════════════════

    def _cover_title_lines(self, filled: dict, project_name: str) -> list:
        """封面/扉页标题行。

        复垦：用项目自己的标题（`project_title`）——
            「江苏盐业井神…工程」+「三井组临时用地土地复垦方案」；
            没有 project_title 时用 project_name + 域显示名（复垦「土地复垦方案」）；
            **绝不**再硬编码任何封面副标题 —— 不同地区模板的封面标题不同。
        稳评：同样优先用 project_title，不再写死「《社会稳定性风险评估》」。
        """
        import re as _re

        title = str((filled or {}).get("project_title")
                    or (filled or {}).get("report_title") or "").strip()
        if self._domain != "reclamation":
            # 稳评同样不写死报告类型：优先用项目自己的完整标题；
            # 标题就是项目名时，才补域显示名（来自域配置，不是封面专用文案）。
            if not title:
                title = project_name
            if title == project_name:
                return [project_name]
            if project_name and title.startswith(project_name):
                rest = title[len(project_name):].strip()
                if rest:
                    return [project_name, rest]
            return [title]
        if not title or title == project_name:
            title = project_name + report_kind_name(self._domain)
        title = title.replace("报告书", "方案")
        title = _re.sub(r"方案方案$", "方案", title)
        # 「…工程」单独一行，后面「三井组临时用地土地复垦方案」一行（与人工稿一致）
        m = _re.match(r"^(.*?工程)\s*(.+?方案)$", title)
        if m and m.group(1).strip() and m.group(2).strip():
            return [m.group(1).strip(), m.group(2).strip()]
        return [title]

    def _add_cover_page(self, doc, doc_ref, project_name, org_name, location="",
                        implement_unit="", filled=None):
        """封面 —— 版式对齐参照件《白马湖农场1号风电项目》。

        🔴 **用 `space_before` 定位，不用空段**：`_post_generation_fix` 第 6 步会把
           「连续超过 2 个」的空段删掉，旧版靠「标题前 6 个空段 + 落款前 4 个空段」
           撑出来的版面全被压回顶部（业主：字体位置不对，标题该上下居中偏上、
           落款该在下方并与标题隔开距离）。`space_before` 不在清理范围内。

        参照件实测（A4 纵向、上边距 2.54cm=72pt）：
            标题两行同字号居中，落在页面约 27% 高度；
            落款三行左对齐、落在约 72% 高度；两者之间大段留白。
        """
        # 🔴 复垦封面比稳评多一行标题且落款三行；原 185/240 pt 在 WPS 里会把
        #    日期那行挤到第 2 页（业主 2026-11-02 实测）。复垦压到 150/200，
        #    稳评保持原值（逐字不变）。
        _title_top, _sign_top = (150, 200) if self._domain == "reclamation" else (185, 240)
        _TITLE_TOP_PT = Pt(_title_top)   # 标题头顶到版心上沿
        _SIGN_TOP_PT = Pt(_sign_top)     # 落款头顶到标题块之下

        pn = (project_name or doc_ref or '').strip()
        if not pn or '示意图' in pn or '位置图' in pn:
            # 无项目名时才用通用标题兜底（按域取：稳评「土地征收决策」/复垦「土地复垦方案」）
            pn = cover_fallback_title(self._domain)

        # ── 标题：复垦用**项目自身标题**（工程名 + 临时用地…方案），
        #    不再硬编码「土地复垦方案报告书」；稳评保持「项目名 + 报告类型」两行。──
        _ct_font, _ct_pt = typography(self._domain, "cover_title")
        _cs_font, _cs_pt = typography(self._domain, "cover_sign")
        _title_lines = self._cover_title_lines(filled or {}, pn)
        for _i, _line in enumerate(_title_lines):
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = _TITLE_TOP_PT if _i == 0 else Pt(0)
            p.paragraph_format.space_after = Pt(6) if _i < len(_title_lines) - 1 else Pt(0)
            _set_run_font(p.add_run(_line), _ct_font, size=Pt(_ct_pt),
                          bold=cover_bold(self._domain), ascii_font=FONT_ASCII)

        # ── 落款：三行左对齐（标签都是 6 字+冒号，天然对齐）──
        # 🔴 前两个抬头按域取（稳评「稳评责任主体/实施单位」→ 复垦「项目单位/编制单位」）。
        _fl = front_matter_labels(self._domain)
        _dl, _dv = cover_date_parts(self._domain)
        for i, (lab, val) in enumerate((
            (_fl["org_label"], org_name or ''),
            (_fl["implement_label"], implement_unit or ''),
            (_dl, _dv),
        )):
            if not val:
                continue
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            # 🔴 落款行间空档按域取：业主口径「项目单位/编制单位中间要隔一行」——
            #    样本落款三行 y=590/646/700，行距 56pt（15pt 字）→ 空档约 36pt。
            p.paragraph_format.space_before = (
                _SIGN_TOP_PT if i == 0 else Pt(front_matter_gap_pt(self._domain, "cover_sign")))
            p.paragraph_format.space_after = Pt(0)
            _set_run_font(p.add_run(lab + val), _cs_font, size=Pt(_cs_pt),
                          ascii_font=FONT_ASCII)

        doc.add_page_break()

    def _add_title_page(self, doc, filled, project_name="", doc_ref=""):
        """扉页 / 项目信息页（**复垦专有**，稳评没有这一页）。

        规格取自两份复垦样本，两样本完全一致：
            明远佳苑二期：项目名称 / 项目单位 / 单位地址 / 联系人 / 电话 / 送审时间
            盐业井神：    项目名称 / 项目单位 / 单位地址 / 联系人 / 电话

        🔴 每个字段**值非空才输出该行** —— 抽不到就整行不出现（不得印出
           「待填写」「【待补充】」这类占位符，也不为它弹窗问用户）。
        """
        pn = (project_name or doc_ref or '').strip()
        _th_font, _th_pt = typography(self._domain, "title_page_head")
        _ti_font, _ti_pt = typography(self._domain, "title_page_info")

        # ── 顶部：与封面同款标题行（复垦用项目自身标题，不再硬编码报告书）──
        _lines = self._cover_title_lines(filled or {}, pn)
        for _i, _line in enumerate(_lines):
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = (
                Pt(front_matter_gap_pt(self._domain, "title_page_head")) if _i == 0 else Pt(0))
            p.paragraph_format.space_after = Pt(24) if _i == len(_lines) - 1 else Pt(4)
            _set_run_font(p.add_run(_line), _th_font, size=Pt(_th_pt),
                          bold=cover_bold(self._domain), ascii_font=FONT_ASCII)

        # ── 信息块：**样本是 6 行填表**（每行值下面一条横线），值缺失也留着横线给手填 ──
        #    样本（明远路 第2页 实测）：
        #        项目名称：…   项目单位：…   单位地址：…   联 系 人：…   电　话：…   送审时间：…
        #    每行下面都有一条横线。**行不能少** —— 以前「值为空则整行略过」，
        #    材料里没有「单位地址/送审时间」时就只剩 4 行，业主一眼就看出「信息缺了几项」。
        #    横线在=留了空位可手填；不印「待填写」这类占位符（业主硬约束）。
        _rows = (
            ("项目名称：", pn),
            ("项目单位：", filled.get("project_unit")
             or filled.get("build_unit") or filled.get("org_name") or ""),
            ("单位地址：", filled.get("unit_address") or ""),
            ("联 系 人：", filled.get("contact_name") or ""),
            ("电　　话：", filled.get("contact_phone") or ""),
            ("送审时间：", filled.get("submit_date") or ""),
        )
        for _i, (lab, val) in enumerate(_rows):
            v = str(val or "").strip()
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            # 🔴 行距按域取：样本扉页六行 y=277…719 **均匀铺满整页**（跨 442pt / 版心 698pt），
            #    以前写死 Pt(72)/Pt(22) 把整块挤在页面上半截（业主：「没有平均的占满一页」）。
            _gap = front_matter_gap_pt(self._domain, "title_page_row")
            p.paragraph_format.space_before = Pt(_gap * 1.6) if _i == 0 else Pt(_gap)
            p.paragraph_format.space_after = Pt(0)
            if v:
                _set_run_font(p.add_run(lab + v), _ti_font, size=Pt(_ti_pt),
                              ascii_font=FONT_ASCII)
            else:
                _set_run_font(p.add_run(lab), _ti_font, size=Pt(_ti_pt),
                              ascii_font=FONT_ASCII)
            _add_para_bottom_border(p)

        doc.add_page_break()

    # ═══════════════════════════════════════════════════════════════
    # Chapter Processing
    # ═══════════════════════════════════════════════════════════════

    def _add_chapter(self, doc, ch_num, markdown, image_files, survey_stats,
                     extracted_tables=None, filled=None):
        if extracted_tables is None:
            extracted_tables = []
        # 🔴 图注/表格编号要按**本章**章号生成（「图5-1」而不是「图0-1」）。
        #    assemble() 是在循环里设的，单章导出（scripts/export_chapter.py）
        #    直接调本函数，不设就会全编号成「图0-N」。
        self._current_chapter = ch_num

        # Ensure chapter starts with proper heading
        ch_cn = _cn_num(ch_num)
        ch_title = self._get_chapter_title(ch_num)
        # Remove any existing chapter heading the LLM generated, use our title
        # 🔴 只删「第X章」这一行（含同行的标题文字），不能把 `\s*` 吃到下一行、
        #   把首个子标题（如 ### 1.6）也吞掉。
        markdown = re.sub(r'^#{1,3}\s*第[一二三四五六七八九十\d]+章[^\n]*\n', '', markdown)
        markdown = re.sub(r'^第[一二三四五六七八九十\d]+章[^\n]*\n', '', markdown)
        # 🔴 复垦的 LLM 常直接把章标题写成 `# 项目概况`（无「第X章」也无编号）——
        #    上面两条都匹配不到，会**残留成正文里的一行**（实测：
        #    章标题页之后又冒出一行「# 项目概况」）。按本章标题精确剥一次。
        if not chapter_title_own_page(self._domain) and ch_title:
            markdown = re.sub(rf'^#\s*{re.escape(str(ch_title))}[^\n]*\n', '', markdown,
                              flags=re.MULTILINE)
        # 🔴 章标题**最后再插**（见本轮末尾）：复垦的章标题本身是「# 1 前言」形态，
        #    会被下面那条「清理 LLM 生成的 # 数字 章标题」正则一并删掉。
        #    稳评章标题是「# 第X章 …」，本来就不匹配那条正则 —— 挪到后面插入
        #    对稳评是等价的（两条路径产出的首行逐字相同）。

        # Fix section numbering: "1 . 1" → "1.1", "1 .1" → "1.1", etc.
        markdown = re.sub(r'(\d+)\s*\.\s*(\d+)', r'\1.\2', markdown)
        # 🔴 标题里重复的序号：「4.4.2.1 （1）评价范围」→「4.4.2.1 评价范围」
        #    （模型常把自动编号和手写「（1）」一起写；业主 2026-11-04 实测）
        markdown = re.sub(
            r'^(#{2,4}\s*\d+(?:\.\d+)+\s*)[（(]\s*\d+\s*[)）]\s*',
            r'\1', markdown, flags=re.MULTILINE)
        # 编号与标题之间的空格补齐（「4.4.2.2评价单元划分」→「4.4.2.2 评价单元划分」）
        markdown = re.sub(
            r'^(#{2,4}\s*\d+(?:\.\d+)+)(?=[^\s\d.])',
            r'\1 ', markdown, flags=re.MULTILINE)
        # 🔴 最多三级标题：X.Y.Z.W 及更深 → 正文引导「n）标题」
        markdown, _deep_n = demote_deep_headings(markdown)
        if _deep_n:
            logger.info(f"[标题] 第{ch_num}章 {_deep_n} 处四级编号降为「n）」引导行")
        # 🔴 模板清单之外的小节标题 → 「n）」引导行（与人工报告目录对齐）
        _allowed = self._template_allowed_sections(ch_num)
        if _allowed:
            markdown, _unlisted = align_headings_to_template(markdown, _allowed)
            if _unlisted:
                logger.info(f"[标题] 第{ch_num}章 {_unlisted} 处模板外小节降为「n）」引导行")
        markdown = re.sub(r'^### (第[一二三四五六七八九十\d]+章\s)', r'## \1', markdown, flags=re.MULTILINE)
        # 🔴 二级标题层级统一：`### 2.1 xxx` → `## 2.1 xxx`（保证所有 X.Y 二级标题都是 Heading 2/宋体四号加黑靠左）
        markdown = re.sub(r'^#{3,4}\s+(\d+\.\d+\s)', r'## \1', markdown, flags=re.MULTILINE)
        # 🔴 清理 LLM 生成的"# 数字 章标题"重复（如 "# 2 评估过程、方法和依据"）
        markdown = re.sub(r'^#{1,3}\s*\d+\s+[^\n]+\n', '', markdown, flags=re.MULTILINE)
        # 🔴 markdown 里的章标题行用**内部形态** `# 第{中文}章 {标题}`（空格分隔）——
        #    与改动前逐字相同。**不能**在这里用 `chapter_heading_text`（那是**显示**形态，
        #    稳评带「、」）：下面的解析正则 `第X章\s*(.+)` 会把「、」吃进标题，
        #    渲染时再拼一次就变成「第一章、、编制说明」。
        markdown = f"# 第{ch_cn}章 {ch_title}\n\n{markdown}"
        # 🔴 清理图片标记残留（如 "[现场勘查照片：图片1]"）——不是实际图片路径的标记
        markdown = re.sub(r'^\[[^\]]*照片[：:][^\]]*\]\s*\n', '', markdown, flags=re.MULTILINE)

        # Safety: ensure markdown is a string
        if not isinstance(markdown, str):
            markdown = str(markdown) if markdown else ""
        if not markdown:
            return

        lines = markdown.split('\n')
        table_buf, in_table = [], False
        skip_md_tables = False  # Set after rendering extracted tables
        injected_tables = set()  # Track tables injected via [TABLE:name] markers
        pending_caption = ""  # 🔴 表格前的「表X-X 表名」行，捕获后放到表格下方

        # 🔴 第9章两张问卷统计表的落位标志（表9-1 个人 / 表9-2 单位）：
        #    标记渲染、LLM 手写表替换、章内兜底三条路径共用，避免重复落表。
        self._ch9_public_rendered = False
        self._ch9_dept_rendered = False

        # 🔴 第6章「6.1 项目概况」的图（位置示意图 + 勘测定界图）：插在该小节正文之后，而非章末。
        #    用**列表**而不是单张——两类图都要挂 1.5，单张会把勘测定界图挤到附件。
        self._pending_map_images = []
        self._pending_map_flush_on_next = False
        if ch_num == 6:
            _cim = getattr(self, '_chapter_image_map', {})
            _c1 = _cim.get(6, [])
            _MAP_KW = ('勘测', '地图', '红线', '规划', '位置', '地形', '宗地', '勘界', '测定', '示意')
            _keep = []
            for _img in _c1:
                if not isinstance(_img, dict):
                    _keep.append(_img); continue
                _meta = f"{_img.get('label', '')} {_img.get('caption', '')} {_img.get('name', '')}"
                if "拦挡" in _meta or _img.get("type") == "measure_section":
                    # 图6-1 拦挡断面图不是位置图，留到 6.1.1 锚点落位
                    _keep.append(_img)
                elif _img.get("type") in ("map", "survey_map") or any(k in _meta for k in _MAP_KW):
                    # 🔴 1.5 只留**一张**位置示意图（用户要求）；
                    #    其余（勘测定界图等）留回本章图位，章末不再自动补插 → 落附件。
                    if not self._pending_map_images:
                        self._pending_map_images.append(_img)
                else:
                    _keep.append(_img)
            _cim[6] = _keep

        # 🔴 第8章 公示/现场/座谈照片：插到「8.4 风险调查过程」正文之后。
        self._pending_section_imgs = {}
        if ch_num == 8:
            _cim6 = getattr(self, '_chapter_image_map', {})
            _c6 = _cim6.get(8, [])
            _PROC_KW = ('公示', '现场', '座谈', '勘查', '勘察', '踏勘', '会议', '开会')
            _OPIN_KW = ('舆情', '评审', '专家', '意见')
            _proc, _opin, _rest = [], [], []
            for _img in _c6:
                if not isinstance(_img, dict):
                    continue
                _meta = f"{_img.get('label', '')} {_img.get('name', '')} {_img.get('type', '')}"
                if any(k in _meta for k in _OPIN_KW):
                    _opin.append(_img)
                elif any(k in _meta for k in _PROC_KW):
                    _proc.append(_img)
                else:
                    _rest.append(_img)
            self._pending_section_imgs = {
                '风险调查过程': _proc + _rest,
            }
            _cim6[8] = []
            # 🔴 再按**子项**分桶：公示/勘察/走访/座谈 各有各的照片，
            #    到各自子项标题处落位（见 `_flush_ch8_subitem`）。
            self._ch8_partition_images(_proc + _rest)

        # 🔴 按域的「落位锚点」章（复垦第3/4章；稳评恒为 None，等于没这段）。
        #    复用第8章那套 `_pending_section_imgs` 通道 —— 到锚点小节标题处落图，
        #    万一本章没有该小节，`_flush_pending_section_images` 会在章末兜底补插，不丢图。
        _anchor = body_image_anchor(self._domain, ch_num)
        if _anchor:
            _cim_a = getattr(self, '_chapter_image_map', {})
            _imgs_a = _cim_a.get(ch_num, [])
            # 🔴 第3章：系统已出「位置示意图」时，跳过用户上传的**原始红线渲染**
            #    （.shp.png / .cad.png）—— 它们与系统图同义，会印成两张同名图。
            _fig_ok = (int(ch_num) == 3
                       and ((self._session_state or {}).get("filled_data") or {}
                            ).get("_fig_location_generated"))
            _reclam_ch3 = int(ch_num) == 3 and self._domain == "reclamation"
            # 用户自己上传了位置图（排除系统落盘那一张）
            _has_user_loc = _reclam_ch3 and any(
                is_location_figure(i) and not is_system_location_figure(i, self.storage_dir)
                for i in _imgs_a)
            if _fig_ok or _has_user_loc:
                if _fig_ok:
                    _imgs_a, _raw = split_raw_redline(_imgs_a)
                    if _raw:
                        logger.info(f"[配图] 第3章已有系统位置图，跳过原始红线渲染 {len(_raw)} 张")
                # 🔴 业主 2026-11-09：「如果用户提供了就用用户的，只保留一个」。
                #    用户自己传了位置图 → 用它，系统图与其它同名图都摘掉。
                _imgs_a, _dup, _user_loc = prefer_user_location(_imgs_a, self.storage_dir)
                if _user_loc:
                    logger.info(f"[配图] 第3章采用用户上传的位置图，系统图及重复图摘掉 "
                                f"{len(_dup)} 张")
                elif _fig_ok:
                    # 🔴 系统图之外，任何「位置示意图」类的图也一并去掉 ——
                    #    样本/用户材料里常自带一张同名位置图，插进来就是
                    #    图3-1、图3-2 两张一样的图（业主 2026-11-04 实测）。
                    _imgs_a, _dup = drop_location_dupes(_imgs_a)
                    if _dup:
                        logger.info(f"[配图] 第3章已有系统位置图，跳过重复位置图 {len(_dup)} 张")
                # 🔴 **无论过滤后还剩什么都要清空本章图位** —— 原来只在 `_imgs_a`
                #    非空时才清；若系统图没进图库（正文每类限 2 张，原始红线先占了名额），
                #    过滤后为空 → 章末兜底又把那两张红线图插回来，图3-1 变成白底红线图
                #    （2026-11-03 线上实测）。
                _cim_a[ch_num] = []
                if _fig_ok and not _imgs_a and not _user_loc:
                    # 系统图确定存在（`_fig_location_generated`）就直接补一条，
                    # 不让它因为图库名额/去重被吃掉。
                    _gp = Path(str(self.storage_dir)) / "images" / "图3-1 工程位置示意图.jpg"
                    if _gp.exists():
                        _imgs_a = [{"path": str(_gp), "label": "项目地理位置示意图"}]
            if _imgs_a:
                # 🔴 复垦第3章的图序按样本：**位置示意图在前**、土地利用现状图在后
                #    （图3-1 位置、图3-2 现状）。用户上传的图排在系统图前面，
                #    不排序的话现状图会抢到图3-1。
                if self._domain == "reclamation":
                    _imgs_a = sorted(
                        _imgs_a,
                        key=lambda it: 0 if "位置示意图" in str(
                            (it or {}).get("label") or (it or {}).get("name")
                            or (it or {}).get("original_name") or "") else 1)
                self._pending_section_imgs = {_anchor: list(_imgs_a)}
                self._section_body_seen = False   # 本节还没有正文段时，图不能顶在标题下
                self._defer_empty_section = True  # 仅复垦锚点章启用「无正文则挂起」
                _cim_a[ch_num] = []

        # 🔴 复垦第1章：表1-1 拟损毁土地情况表（分区名称/面积由用户提供）
        if ch_num == 1 and getattr(self, "_domain", "stability") == "reclamation":
            self._init_ch1_tables(filled)
        # 🔴 复垦第4章：登记待补渲的分析表（表4-8/4-11/4-12/4-13），
        #    在小节边界处依次落下。稳评 / 其他章此调用是空操作。
        if ch_num == 4:
            self._init_ch4_tables(filled, markdown)
        # 🔴 复垦第6章：表6-1 工程量汇总表由系统按地类算（模型写的同题表会被丢掉）
        if ch_num == 6:
            self._init_ch6_tables()
        # 🔴 复垦第7章：由 budget-maker 现算投资估算，补 表7-3/表7-4。
        #    稳评 / 其他章此调用是空操作。
        if ch_num == 7:
            self._init_ch7_tables()
        # 🔴 复垦第8章：表8-1 费用安排表（阶段工程费 + 按比例分摊）
        if ch_num == 8:
            self._init_ch8_tables()

        for line in lines:
            s = line.strip()
            # 🔴 第8章子项到点插照片：认出「N.现场公示/实地勘察/走访/座谈会」时，
            #    先把**上一个**子项的照片落下去（照片跟在各自子项正文之后）。
            if ch_num == 8:
                self._flush_ch8_subitem(doc, s)
            # 🔴 第9章「（一）问卷调查情况」收尾（遇到「（二）…」）时补插系统统计表。
            #    **不依赖模型是否输出 [TABLE:ch9_*] 标记** —— 问卷统计表是法定内容，
            #    模型漏写也不能缺；模型写错则由系统表覆盖。
            if ch_num == 9 and re.match(
                    r'^#{0,4}\s*\**\s*（[二三四五六七八九十]+）', s):
                self._ensure_ch9_survey_tables(doc)
            # Skip AI meta-text and placeholder explanations
            if any(x in s for x in [
                '***', '遵照您的指示', '以下是为您撰写', '为您撰写第',
                '因勘测定界报告', '因PDF原文', 'PDF原文中未提供',
                '> 注：上表中总面积', '> 注：因PDF',
            ]):
                continue
            if s.startswith('好的，遵照'):
                continue

            if s.startswith('|') and s.endswith('|'):
                # 🔴 相邻两张 markdown 表之间若没有正文/空行，会被并成**一张** docx 表
                #    （实测第12章：措施前评分表 + 措施前后得分对比表 → 一张 29 行的怪表，
                #     表中间冒出「序号|评估维度|措施前得分|措施后得分」这行新表头）。
                #    判据：一张 markdown 表只有**一个**分隔行（|---|---|）；
                #    已收过分隔行又遇到分隔行 → 上一张表结束，并把刚粘进上一张表的那行
                #    新表头（buffer 末行）还给新表。
                _is_sep = bool(re.match(r'^\|[\s\-:—|]+\|$', s))
                if in_table and _is_sep and any(
                        re.match(r'^\|[\s\-:—|]+\|$', x) for x in table_buf):
                    _new_hdr = table_buf.pop() if len(table_buf) >= 2 else ""
                    self._flush_md_table(doc, ch_num, table_buf, pending_caption,
                                         survey_stats, extracted_tables)
                    table_buf = [_new_hdr] if _new_hdr else []
                    pending_caption = ""
                    in_table = bool(table_buf)
                # Collect markdown table lines. Handle LLM merging rows on one line.
                if not in_table:
                    table_buf = []
                # Split malformed single-line tables: rows separated by " | — |" or " |---"
                # 🔴 只在「这一行的格子数明显多于表头」时才拆 —— 正常行本身就可能
                #    含 `| — |`（如 `| 合计 | — | — | 3.97 |`，2026-11-12 实测被
                #    拆成「| 合计 |」+「| — | 3.97 |」，合计金额跑到费用名称列）。
                _ncol_hdr = _ncol_cur = 0
                if in_table and table_buf:
                    _ncol_hdr = len([c for c in table_buf[0].strip().strip('|').split('|')])
                _ncol_cur = len([c for c in s.strip().strip('|').split('|')])
                _multiline = (not in_table) or (_ncol_hdr and _ncol_cur > _ncol_hdr)
                if (_multiline and (' | — |' in s or ' |---' in s)):
                    import re as _re_tbl
                    rows = _re_tbl.split(r'\s*\|\s*[-—]+\s*\|', s)
                    for row in rows:
                        row = row.strip()
                        if not row: continue
                        # Skip pure separator fragments (only dashes and pipes)
                        if re.match(r'^[\s\-—\|]+$', row): continue
                        # Reconstruct: if missing leading/trailing |, add them
                        if not row.startswith('|'):
                            row = '| ' + row
                        if not row.endswith('|'):
                            row = row + ' |'
                        table_buf.append(row)
                else:
                    table_buf.append(s)
                in_table = True; continue
            elif in_table:
                # Render collected markdown table（表题放表格下方）
                # 🔴 表1-1/表4-1 由 PDF 提取版渲染：在 LLM 引用处「就地」渲染提取版，
                # 既避免 LLM 手写表双写，又保证表格位于正确小节（如1.6征收范围及面积）。
                self._flush_md_table(doc, ch_num, table_buf, pending_caption,
                                     survey_stats, extracted_tables)
                table_buf, in_table = [], False
                pending_caption = ""

            # 🔴 捕获「表X-X 表名」行（表格前），改放到表格下方
            #    加粗写法 `**表9-1 个人问卷统计汇总表**` 同样是表题（LLM 常这么写）。
            #    不认的话它会被当正文印出来，表格那边又按注册表另起一个表题
            #    → 一张表挂两个表题、措辞还不一致（实测第9章 表9-1/9-2）。
            _cap = re.sub(r'^\*{1,2}|\*{1,2}$', '', s).strip()
            if re.match(r'^表\s*\d+[-—]\d+', _cap) and len(_cap) < 40:
                pending_caption = _cap
                # 🔴 表1-1/表4-1：即使 LLM 只写了表题、没写 markdown 表，
                # 也在此处就地渲染 PDF 提取版（表题下方紧接表格，位置正确不悬空）
                if (re.match(r'^表\s*(1[-—]1|4[-—]1)', _cap)
                        and self._is_extracted_land_table(_cap, ch_num, extracted_tables)):
                    self._render_extracted_tables(doc, ch_num, extracted_tables)
                    pending_caption = ""
                continue

            # 🔴 [TABLE:name] 标记渲染系统标准表（第9章 表9-1 个人问卷 / 表9-2 单位问卷）。
            # 章节 agent 在正文输出此标记时，由系统生成并渲染，保证题面与人数不走样。
            m_tbl = re.match(r'^\[TABLE:(\w+)\]$', s)
            if m_tbl:
                marker = m_tbl.group(1)
                for name, tdata in self._get_system_survey_tables(ch_num):
                    if name == marker and tdata.get("rows"):
                        # 🔴 模型若已写「表9-1 …」表题行（被 pending_caption 捕获），
                        #    优先用它 —— 否则会出现「表题行被吞 + 系统表题再补一次」的一表两题。
                        self._render_table_from_dict(doc, tdata, caption=pending_caption)
                        pending_caption = ""
                        if name.startswith("ch9_"):
                            setattr(self,
                                    "_ch9_dept_rendered" if name == "ch9_dept_survey"
                                    else "_ch9_public_rendered", True)
                        else:
                            self._suppress_ch3_survey = True
                continue

            # 🔴 [SYS_TABLE:1-1|4-1] 标记：正文以「引导语」引出 PDF 提取的勘测定界表
            # （表1-1 地块汇总 / 表4-1 土地分类），由系统就地渲染（含表题），
            # 表后正文再接解读句 → 实现模板「引导语+表格+解读」的承接写法。
            m_sys = re.match(r'^\[SYS_TABLE:(6[-—]1|1[-—]1|4[-—]1)\]$', s)
            if m_sys:
                # 🔴 无论能否渲染都必须 `continue`：没有提取表时若往下走，
                #    标记行会被当普通正文**原样印进报告**（「[SYS_TABLE:6-1]」）。
                if extracted_tables:
                    self._render_extracted_tables(doc, ch_num, extracted_tables)
                else:
                    logger.warning(f"[SYS_TABLE] 第{ch_num}章有 {s} 标记但无提取表可渲染")
                continue
            # 🔴 表4-5 参与人员签字表：**系统渲染**，用用户勾选的人员 + 模板里的签名图。
            #    LLM 写这张表会把模板里的老团队名单抄进来，且签名是手写图片，模型给不出。
            if re.match(r'^\[SYS_TABLE:4[-—]5\]$', s):
                self._render_personnel_signature_table(doc, ch_num)
                continue
            # 🔴 第7章「成立稳评小组」的成员名单：按**界面勾选的人员**渲染
            if re.match(r'^\[SYS_ROSTER\]$', s):
                self._render_personnel_roster(doc)
                continue

            # 🔴 兜底：上面没认领的任何系统标记**一律不印进正文**。
            #    标记是给装配器看的，漏进交付稿就是「[TABLE:ch9_public_survey]」
            #    这种天书（业主 2026-10-28 直接指出「还有这种字段」）。
            #    典型的漏网情形：标记里有多余空白、大小写不一，或被模型写进了句子中间。
            if re.match(r'^\s*\[(?:TABLE|SYS_TABLE|SYS_ROSTER)\s*[:：]', s):
                logger.warning(f"[标记] 第{ch_num}章有未渲染的系统标记，已丢弃：{s[:60]}")
                continue

            if not s:
                continue

            # Handle inline image markers: ![caption] or ![caption](path)
            img_marker = re.match(r'^!\[([^\]]+)\]\s*(?:\(([^)]*)\))?', s)
            if img_marker:
                caption = img_marker.group(1).strip()
                img_ref = img_marker.group(2).strip() if img_marker.group(2) else ""

                # If path is provided and exists, use it directly
                if img_ref:
                    self._add_image(doc, img_ref, caption)
                    continue  # 🔴 Skip image-map fallback — path was explicit
                # 🔴 按图注关键词匹配图片（避免位置错乱）
                matched_path = self._match_image_by_caption(caption)
                if matched_path:
                    self._add_image(doc, matched_path, caption)
                    continue
                # Fall back to catalog chapter image map only
                ch_img_map = getattr(self, '_chapter_image_map', {})
                ch_imgs = ch_img_map.get(ch_num, [])
                if ch_imgs:
                    img_info = ch_imgs[0]
                    img_path = img_info.get("path", img_info) if isinstance(img_info, dict) else img_info
                    if img_path:
                        self._add_image(doc, img_path, caption)
                    ch_img_map[ch_num] = ch_imgs[1:]  # consume
                else:
                    # 🔴 图注匹配不到图片 → 记录问题（供审核 agent 感知）
                    self._unmatched_image_markers.append({
                        "chapter": ch_num,
                        "caption": caption[:40],
                    })
                continue

            # Skip old-style AI-generated image markers
            if re.match(r'^图\d+[-—]\d+\s', s) and len(s) < 30:
                continue

            # Chapter heading — handles #, ##, ###, or bare 第X章
            m = re.match(r'^#+\s*第([一二三四五六七八九十\d]+)章\s*(.+)', s)
            if not m:
                # 🔴 裸行「第X章 …」必须**短且不像句子**才算章标题：
                #    正文里常出现「第9章调查中反映…」这类句子，旧正则太宽会把它们变成一级标题。
                m = re.match(r'^第([一二三四五六七八九十\d]+)章\s+([^。，；：、\s]{2,20})$', s)
            if m:
                cn = m.group(1)
                num = CN_NUM.get(cn, int(cn) if cn.isdigit() else ch_num)
                # 🔴 传章号：复垦的显示形态是「1 前言」（阿拉伯数字），
                #    拼它需要 chapter_number；稳评用 cn 就够，传了也不改变结果。
                self._add_chapter_title_page(doc, cn, m.group(2).strip(),
                                             chapter_number=num)
                continue

            # 裸行「（一）小节名」→ Heading 2（人工模板章的二级标题写法）
            m = re.match(r'^(（[一二三四五六七八九十]+）\s*[^\s。，,；;]{2,30})$', s)
            if m:
                self._maybe_flush_map_image(doc, m.group(1), ch_num)
                self._cur_section_name = re.sub(r'^（[一二三四五六七八九十]+）\s*', '', m.group(1)).strip()
                self._add_heading(doc, m.group(1), 2)
                continue

            # ## → DOCX Heading 2 (二级标题: 黑体 四号 14pt)
            m = re.match(r'^##\s+(.+)', s)
            if m:
                heading_text = re.sub(r'\*\*(.+?)\*\*', r'\1', m.group(1))
                self._maybe_flush_map_image(doc, heading_text, ch_num)  # 🔴 ch1 图1-1 插在 1.5 之后
                self._flush_dyn_tables(doc, ch_num, heading_text)  # 🔴 系统补渲表在小节边界落位
                self._cur_section_name = heading_text.strip()
                llm_invented_patterns = [
                    r'^(合法性|合理性|可行性|可控性)[（(]得分[：:]\s*\d+',
                    r'^表(?!5[-—])',
                ]
                is_llm_invented = any(bool(re.match(p, heading_text)) for p in llm_invented_patterns)
                if not is_llm_invented:
                    self._add_heading(doc, heading_text, 2)
                continue

            # ### （一）xxx → DOCX Heading 2
            # 🔴 人工模板章的二级标题用中文序号「（一）（二）」，层级等同二级标题
            m = re.match(r'^###\s+(（[一二三四五六七八九十]+）\s*.+)', s)
            if m:
                heading_text = re.sub(r'\*\*(.+?)\*\*', r'\1', m.group(1))
                self._maybe_flush_map_image(doc, heading_text, ch_num)
                self._cur_section_name = re.sub(
                    r'^（[一二三四五六七八九十]+）\s*', '', heading_text).strip()
                self._add_heading(doc, heading_text, 2)
                continue

            # ### → DOCX Heading 3 (三级标题: 楷体_GB2312 四号 14pt) — DB32/T4013
            m = re.match(r'^###\s+(.+)', s)
            if m:
                heading_text = re.sub(r'\*\*(.+?)\*\*', r'\1', m.group(1))
                self._maybe_flush_map_image(doc, heading_text, ch_num)  # 🔴 ch1 图1-1 插在 1.5 之后
                self._flush_dyn_tables(doc, ch_num, heading_text)      # 🔴 表7-3 落在 7.2.2 之前
                self._add_heading(doc, heading_text, 3)
                continue

            m = re.match(r'^#### (.+)', s)
            if m:
                # 🔴 四级标题也要过「到点插图」钩子：第6章的小节是
                #    `### （一）项目概况` → `#### 3.项目地理位置`，只挂在三级标题上
                #    会让位置示意图永远等不到落点，一路飘到章末。
                _ht = re.sub(r'\*\*(.+?)\*\*', r'\1', m.group(1))
                self._maybe_flush_map_image(doc, _ht, ch_num)
                self._flush_dyn_tables(doc, ch_num, _ht)   # 🔴 表7-3 落在 7.2.2 之前
                self._add_heading(doc, _ht, 3)
                continue

            # 🔴 先识别整行加粗（**（一）子点标题** / **加粗结论**），
            # 再剥离 **——否则剥离后变短文本会被下方 len>10 守卫误删
            was_bold = s.startswith('**') and s.endswith('**') and len(s) > 6

            clean = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
            clean = re.sub(r'\[待补充\]', '【待补充】', clean)

            if was_bold:
                self._add_para(doc, clean.strip('*').strip(), bold=True)
            elif re.match(r'^\d+\.\s*\*\*', clean):
                self._add_para(doc, clean, bold=True)
            elif clean.startswith('- ') or clean.startswith('* '):
                self._add_para(doc, '    ' + clean[2:])
            elif _has_meaning(clean):
                # 🔴 判据从「**≤10 字就丢**」改成「这一行**有没有实义**」。
                #    旧守卫会静默吞掉短正文行，实测吞掉了第7章落款
                #    「稳评责任单位（盖章）」（10 字）与「年月日」——
                #    于是「3.方案审核」后面直接接第八章，落款整块消失。
                #    「不留空」是业主硬约束：宁可多留一行（人工一眼能看出），
                #    也不能悄悄少一行（没人看得出来）。
                #    LLM 噪声（「好的，遵照…」「***」等）在上面的显式过滤里已拦掉。
                self._add_para(doc, clean, indent=True)

        if in_table and table_buf and not skip_md_tables:
            if not self._is_extracted_land_table(pending_caption, ch_num, extracted_tables):
                self._render_md_table(doc, table_buf, ch_num, survey_stats)

        # 🔴 第9章章末兜底：若「（二）」小节标题没被识别到（生成结构异常），
        #    问卷统计表仍必须落进报告 —— 宁可位置略靠后，也不能整张缺失。
        if ch_num == 9:
            self._ensure_ch9_survey_tables(doc)

        # 🔴 章末兜底：没等到后续小节标题的系统补渲表在此落下（复垦表4-x/表7-x；稳评空操作）
        self._flush_dyn_tables_end(doc, ch_num)

        # 🔴 固定表格 auto-inject 已移除——表格由章节 agent 根据实际数据动态设计
        # （用 markdown 语法写，有数据才写），不再由系统硬编码渲染固定表格。

        # 🔴 Auto-insert chapter images at end of content
        ch_img_map = getattr(self, '_chapter_image_map', {})
        ch_imgs = ch_img_map.get(ch_num, [])
        if ch_imgs:
            # 🔴 正文里的公示/现场图：**两张一行并排**（中间一个字符间距），图注在各自图片下方
            _pairs = []
            for img_info in ch_imgs[:4]:
                if isinstance(img_info, dict):
                    path = img_info.get("path", "")
                    label = img_info.get("label", "") or img_info.get("name", "") or img_info.get("caption", "") or ""
                else:
                    path = str(img_info)
                    label = ""
                if path:
                    _pairs.append((path, label))
            if _pairs:
                self._add_image_row_pair(doc, _pairs)

        # 🔴 兜底：结构异常时（小节标题没匹配上），图仍在章末插入，避免丢图
        self._flush_pending_map_image(doc)
        # 第8章的照片收尾。
        # 🔴 必须**兜底把剩下的全落下去**：`_ch8_partition_images` 已把这些图从
        #    `_pending_section_imgs` 里搬走了，而落位依赖识别到「1.现场公示」这类
        #    子项标题。若该章正文没有这些子项（LLM 各写成什么样都有），
        #    子项一个都认不出 → 图被**静默丢掉**（实测界面生成的报告第8章 0 张图）。
        if ch_num == 8:
            _cur = getattr(self, '_ch8_cur_sub', None)
            if _cur:
                self._flush_ch8_group(doc, _cur)
            for _sub in list((getattr(self, '_ch8_subimg', None) or {}).keys()):
                self._flush_ch8_group(doc, _sub)
            self._ch8_cur_sub = None
            self._ch8_subimg = {}
        self._flush_pending_section_images(doc)

        # 🔴 Render extracted PDF tables at END of chapter (any chapter with mapped tables)
        if extracted_tables:
            self._render_extracted_tables(doc, ch_num, extracted_tables)

        # 🔴 不再生成"【待插入：图X-X】"占位文本（会造成重复占位无图）。
        # 图片由 LLM 正文标记或章节末尾插入决定；该章无图则不显示任何占位。

    # ═══════════════════════════════════════════════════════════════
    # Chapter titles (template-aligned)
    # ═══════════════════════════════════════════════════════════════
    # Chapter titles — 单一事实源：按域取 `app.agent.chapter_catalog.chapter_title`。
    # 稳评（默认域）逐字等于改动前 `get_all_chapter_titles()` 的取值。

    def _get_chapter_title(self, ch_num):
        return chapter_title(self._domain, ch_num)

    # ═══════════════════════════════════════════════════════════════
    # Tables
    # ═══════════════════════════════════════════════════════════════

    # ── Table Reshaping: 2-column KV → multi-column ────────────────
    # Known table structures mapped by first header pair
    _TABLE_SCHEMAS = {
        ("测评指标", "权重"): {
            "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
            "group_size": 6,  # KV pairs per row
        },
        ("序号", "评估维度"): {
            "columns": ["序号", "评估维度", "措施前得分", "措施后得分", "变化幅度"],
            "group_size": 5,
        },
        ("调查内容", "选项"): {
            "columns": ["调查内容", "选项", "人数", "比例"],
            "group_size": 4,
        },
        ("序号", "风险类型"): {
            "columns": ["序号", "风险类型", "风险因素描述", "风险等级"],
            "group_size": 4,
        },
        ("序号", "风险因素"): {
            "columns": ["序号", "风险因素", "触发条件", "影响范围", "发生概率", "影响程度", "风险等级"],
            "group_size": 7,
        },
        ("序号", "法规/标准名称"): {
            # Ch2 regulation list — keep as 2-col or reshape to bullet list
            "columns": ["序号", "法规/标准名称"],
            "group_size": 2,
        },
    }

    def _reshape_table(self, table_lines):
        """Reshape 2-column KV tables into proper multi-column format.

        When the LLM outputs a 2-column key-value table (e.g. | 测评指标 | 权重 |)
        that should be 6 columns, group the KV pairs into proper rows.
        """
        if len(table_lines) < 4:
            return table_lines

        # Parse header
        header_line = table_lines[0].strip()
        cells = [c.strip() for c in header_line.strip('|').split('|')]
        if len(cells) > 2:
            # Already multi-column — nothing to reshape
            return table_lines

        # Look up schema by first two headers
        h0, h1 = (cells[0] if len(cells) > 0 else ""), (cells[1] if len(cells) > 1 else "")
        schema = self._TABLE_SCHEMAS.get((h0, h1))

        if not schema:
            return table_lines
        group_size = schema["group_size"]
        if group_size <= 2 or group_size > 8:
            return table_lines

        # Collect all KV pairs (skip header row itself)
        kv_pairs = []
        seen_header = False
        for line in table_lines:
            s = line.strip()
            if re.match(r'^\|[\s\-:|—]+\|$', s):
                continue
            cols = [c.strip() for c in s.strip('|').split('|')]
            if not seen_header:
                seen_header = True
                continue  # Skip original 2-col header
            if len(cols) >= 2 and cols[0] and cols[1]:
                kv_pairs.append((cols[0], cols[1]))

        # Simple grouping: just take groups of group_size values
        new_columns = schema["columns"]
        group_size = schema["group_size"]
        new_rows = []
        i = 0
        while i + group_size <= len(kv_pairs):
            row_vals = [kv_pairs[i+j][1] for j in range(group_size)]
            new_rows.append(row_vals)
            i += group_size
        # Remainder
        if i < len(kv_pairs):
            row_vals = [kv_pairs[i+j][1] if i+j < len(kv_pairs) else "" for j in range(group_size)]
            new_rows.append(row_vals)

        if not new_rows:
            return table_lines

        # Sanity check: don't create absurdly wide tables
        if len(new_columns) > 10:
            return table_lines

        # Build new markdown table
        result = []
        result.append("| " + " | ".join(new_columns) + " |")
        result.append("| " + " | ".join(["---"] * len(new_columns)) + " |")
        for row in new_rows:
            result.append("| " + " | ".join(row) + " |")

        return result

    @staticmethod
    def _clean_md_cell(text):
        """清理单元格中的 markdown 残留，避免 **序号**、`代码`、[链接](x)、HTML 标签等泄漏进 DOCX。

        表格单元格只保留纯文本：
        - [label](url) → label
        - **粗体** / `代码` / ~~删除~~ / __下划线__ → 去掉标记符
        - 多余的 *（如 LLM 加的脚注星号"法规/标准名称*"）→ 去掉
        """
        t = str(text or '')
        t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)   # markdown 链接 → 保留文字
        t = re.sub(r'</?[^>]+>', '', t)                   # html 标签
        t = t.replace('**', '').replace('__', '').replace('~~', '').replace('`', '')
        t = t.replace('*', '')                            # 残留星号（脚注/粗体残迹）
        return t.strip()

    def _flush_md_table(self, doc, ch_num, table_buf, caption, survey_stats, extracted_tables):
        """把攒下的 markdown 表格行渲染成 docx 表（不足两行视为噪声，忽略）。

        🔴 表1-1/表4-1 走 PDF 提取版就地渲染，不由 LLM 手写表承担。
        """
        if len(table_buf) < 2:
            return
        if self._is_extracted_land_table(caption, ch_num, extracted_tables):
            self._render_extracted_tables(doc, ch_num, extracted_tables)
        elif not self._try_render_system_survey_table(doc, ch_num, table_buf, caption):
            self._render_md_table(doc, table_buf, ch_num, survey_stats, caption=caption)

    def _render_md_table(self, doc, table_lines, ch_num, survey_stats, caption=""):
        """Parse markdown table lines into DOCX table.

        所有章节的 markdown 表格都渲染——表格由章节 agent 根据实际数据动态设计，
        有数据才写，格式参考模板。系统不再强制注入固定表格。

        🔴 表题规范：表题统一放在表格「正下方」（DB32/T4013 排版规范）。
        caption 为空时自动生成「表{章}-{序号}」。
        """
        # 🔴 复垦表4-2「临时用地土地利用现状统计表」**改由系统按样本结构渲染**：
        #    样本是「按地类逐项 + 小计 + 合计」，数据来自勘测定界的土地分类面积表
        #    （`land_class_extract` 视觉读出 → `filled_data["land_classification"]`）。
        #    LLM 手写的那张实测只有一行「以耕地为主」，数据没法看。
        #    ⚠️ 钩子必须挂在这里（`_render_md_table` 入口）：markdown 表的 flush 有**四处**
        #       （表间分隔、遇到正文、章末），只挂章末那处根本不触发（实测）。
        if self._render_reclamation_system_table(doc, caption, ch_num):
            return
        # 🔴 表4-5 土地适宜性评价指标体系：**整张照搬模板**（保留列宽/边框/合并单元格）
        if ch_num == 4 and "土地适宜性评价指标体系" in str(caption or ""):
            _ind_no = self._template_ch4_no("指标体系") or 5
            if self._insert_template_table(
                    doc, re.sub(r'^表\s*\d+\s*[-—]\s*\d+',
                                f'表4-{_ind_no}', str(caption or "")),
                    ("一级影响因子", "三级影响因子")):
                return
        # 🔴 **同一张表号只印一次**：系统已按 `[SYS_TABLE:6-1]` 渲染过表6-1 之后，
        #    模型又手写了一张同名表（实测 2026-10-28：第二张还列错位，
        #    三圩社区六组「面积总计」印成 2481，实际 178769）。表题相同即判重。
        _key = self._caption_key(caption)
        if _key and _key in (getattr(self, "_rendered_captions", None) or set()):
            logger.warning(f"[表去重] 第{ch_num}章重复表题「{caption[:24]}」—— "
                           f"系统已渲染过该表，丢弃模型手写的这张")
            return
        rows = []
        for line in table_lines:
            if re.match(r'^\|[\s\-:|—]+\|$', line):
                continue  # Skip separator
            line = line.strip().strip('|')
            cells = [c.strip() for c in line.split('|')]
            # Remove trailing empty cells from malformed splits
            while cells and cells[-1] == '':
                cells.pop()
            if any(cells):
                rows.append(cells)

        if len(rows) < 2:
            return

        # 🔴 **无表题的 markdown 表**：与本章已渲染的系统表「表头高度重合 + 行数相同」→ 判重。
        #    实测 2026-10-28：表6-1 由 `[SYS_TABLE:6-1]` 渲染后，模型又在「注：…」
        #    之后手写了一张同样数据的表（**自己没写表题**，表头还被改成了
        #    「面积总计(㎡)」这种带单位的写法、列序也乱了：三圩社区六组的
        #    「面积总计」印成 2481，实际 178769）。按表题判重抓不到它，只能比对表头。
        #    ⚠️ 只在**无表题**时启用：有表的（如表12-5 措施后评分表）即使表头与
        #       表12-2 相同也必须各自保留。
        if not caption:
            _sig = self._header_sig(rows[0])
            for _c, _s, _n in (getattr(self, "_rendered_sigs", None) or []):
                if _c == int(ch_num or 0) and _n == len(rows) and _sig and _s:
                    _inter = len(_sig & _s)
                    if _inter / max(1, len(_sig | _s)) >= 0.7:
                        logger.warning(f"[表去重] 第{ch_num}章无表题的 markdown 表与已渲染系统表"
                                       f"重复（表头重合 {_inter}/{len(_sig | _s)}），丢弃")
                        return

        ncols = max(len(r) for r in rows) if rows else 2
        # 🔴 这里原来有一条「第12章一律裁成 4 列」的钳制（旧章次下第12章只有
        #    「序号|风险因素|风险表现|风险等级」一张表）。现行 14 章制下第12章是
        #    「确定风险等级」，主力表是**6 列**的量化指标评价体系
        #    `测评指标|权重|测评项目|评分|评分标准|得分` —— 钳制把「评分标准」「得分」
        #    两列悄悄切掉了，而提示词又要求必须写这 6 列（实测稿第12章的表只剩 4 列）。
        #    列数交给表本身的 markdown 决定，不再按章号硬裁。
        # Limit columns: if absurdly wide, keep as 2-column
        if ncols > 10:
            flat = []
            for r in rows: flat.extend(r)
            rows = [[flat[i], flat[i+1] if i+1 < len(flat) else ''] for i in range(0, len(flat), 2)]
            ncols = 2
        for r in rows:
            while len(r) < ncols:
                r.append('')

        # ═══════════════════════════════════════════════════════════════
        # 表格兜底 + 审核（业主口径 2026-09-21：「确保 markdown 不会出错，
        # 加个审核或者兜底机制」）
        #   模型偶尔把表写坏：整行都是占位符（`| — | — | — | — | — |`，实测表4-2
        #   每个大类后面都跟一行）、列数与表头不齐、整列无信息（疑似列错位）。
        #   渲染前先规整，并把可疑处记进 `_table_issues` 供审核感知 ——
        #   绝不让坏表原样印进交付稿。
        # ═══════════════════════════════════════════════════════════════
        if not hasattr(self, '_table_issues'):
            self._table_issues = []
        _PH = {'', '—', '－', '–', '-', '--', '/', '\\', 'n/a', '无', '略', '…'}

        # ①-0 **「合计」续行并回上一行**（必须在下面「删全占位符行」之前做）。
        #    模型常把合计行**拆成两行 markdown**：
        #        | 合计   | — | — | — | — | — |
        #        | 8070.0 | 100.0 | — | — | — | — |
        #    下面规则②「只有第一格有字、其余全占位符 → 删」会把**第一行删掉**，
        #    于是要么合计整行消失（表4-1/4-3 实测），要么数值行留下来、8070.0 落到
        #    「序号」列（表4-2 实测，业主所指）。正确做法是**并回去**，不是删。
        _TOTALS = {'合计', '小计', '总计', '合 计', '总 计'}
        _num_re = re.compile(r'^-?\d[\d,]*(?:\.\d+)?%?$')
        _merged, _merged_n = [], 0
        for _r in rows:
            if _merged and len(_r) > 1:
                _p = _merged[-1]
                _prev_is_total = (str(_p[0]).strip() in _TOTALS
                                  and all(str(c).strip() in _PH for c in _p[1:]))
                _tail_val = [str(c).strip() for c in _r if str(c).strip() not in _PH]
                _tail_is_values = (bool(_tail_val)
                                   and all(_num_re.match(v) for v in _tail_val))
                if _prev_is_total and _tail_is_values:
                    _new = [str(_p[0]).strip()] + _tail_val
                    while len(_new) < len(_p):
                        _new.append('—')
                    _merged[-1] = _new[:len(_p)]
                    _merged_n += 1
                    continue
            _merged.append(_r)
        rows = _merged
        if _merged_n:
            logger.warning(f"[表格兜底] 第{ch_num}章：把 {_merged_n} 行「合计」续行并回合计行")

        _kept, _dropped = [], 0
        for _r in rows:
            # ① 整行占位符（`| — | — | — |`）→ 无信息，删
            if len(_r) > 1 and all(str(c).strip() in _PH for c in _r):
                _dropped += 1
                continue
            # ② **只有第一格有字、其余全是占位符** → 也是一行空壳。
            #    实测表4-2：模型把「测评指标」单独写成一行 `| 合法性 | — | — | — | — | — |`，
            #    紧跟一行真正的测评项目 —— 印出来就是「每个类型多出一行 —」（业主所指）。
            if len(_r) > 2 and str(_r[0]).strip() and all(str(c).strip() in _PH for c in _r[1:]):
                _dropped += 1
                continue
            _kept.append(_r)
        rows = _kept

        # ②-1 地类表「合计」行少一格：表头前两列同为「地类名称及编码」（等价于合并），
        #      模型常写成 `| 合计 | 5.4000 | 100 |`（3 格），渲染时「—」补到末尾，
        #      看起来合计面积跑到地类列。按制式补成 `| 合计 | 合计 | 5.4000 | 100 |`。
        _h0 = rows[0][0].strip() if rows else ""
        _h1 = rows[0][1].strip() if rows and len(rows[0]) > 1 else ""
        _dup_first = bool("地类" in _h0 and _h0 == _h1)          # 地类名称及编码 ×2
        _two_level = bool("地类" in _h0 and "二级地类" in _h1)  # 一级/二级两列
        if len(rows) > 1 and len(rows[0]) >= 3 and (_dup_first or _two_level):
            _fixed = [rows[0]]
            for _r in rows[1:]:
                _tail = [str(c) for c in _r[1:] if str(c).strip() not in _PH]
                if _r and str(_r[0]).strip() in _TOTALS and _tail \
                        and str(_r[1] if len(_r) > 1 else "").strip() not in _TOTALS:
                    # 补回被省掉的地类格（合并列写「合计」，一级/二级两列写「—」），
                    # 末格空/占位不要（否则又串到下一列）
                    _r = [str(_r[0]).strip(),
                          str(_r[0]).strip() if _dup_first else "—"] + _tail
                    while len(_r) < len(rows[0]):
                        _r.append("—")
                    _r = _r[:len(rows[0])]
                _fixed.append(_r)
            rows = _fixed

        # ③ **列错位修复**：模型把「测评项目」写到了第一列，后面用 `—` 补位。
        #    实测表4-2：`| 内容是否符合… | 0 | 全部符合… | 0 | — | 0 |` ——
        #    正确应是 `| 合法性 | 10分 | 内容是否符合… | 0 | 全部符合… | 0 |`。
        #    签名：第1格长文本 + 第2格数字 + 第3格长文本 + 第4格数字 + 第5格占位符。
        #    按上一行的「测评指标 / 权重」补回前两列。
        _cur_m, _cur_w, _shift = "", "", 0
        _out = [rows[0]] if rows else []
        for _r in rows[1:]:
            _g = lambda i: str(_r[i]).strip() if len(_r) > i else ""
            if (len(_g(0)) > 10 and re.match(r'^\d+(?:\.\d+)?$', _g(1))
                    and _g(4) in _PH and len(_g(2)) > 4
                    and re.match(r'^\d+(?:\.\d+)?$', _g(3))):
                _out.append([_cur_m or '—', _cur_w or '—', _g(0), _g(1), _g(2), _g(3)])
                _shift += 1
                continue
            if _g(0) and _g(1) and _g(1) not in _PH and len(_g(0)) < 12:
                _cur_m, _cur_w = _g(0), _g(1)
            _out.append(_r)
        rows = _out
        if _shift:
            _dropped += 0
            self._table_issues.append({"chapter": ch_num, "table": f"表{ch_num}-?",
                                       "detail": f"修复 {_shift} 行列错位（测评项目写到了第一列）"})
            logger.warning(f"[表格兜底] 第{ch_num}章：修复 {_shift} 行列错位")

        if len(rows) < 2:
            return
        _issues = []
        if _dropped:
            _issues.append(f"删除 {_dropped} 行全占位符行")
        _w = len(rows[0])
        for _c in range(_w):
            _vals = [str(_r[_c]).strip() for _r in rows[1:]]
            if _vals and all(v in _PH for v in _vals):
                _issues.append(f"第 {_c + 1} 列整列为空/占位符（疑似列错位）")
        if _issues:
            self._table_issues.append({"chapter": ch_num,
                                       "table": f"表{ch_num}-?",
                                       "detail": "；".join(_issues)})
            logger.warning(f"[表格兜底] 第{ch_num}章：{'；'.join(_issues)}")

        # 🔴 表题统一放表格「正上方」（公文/GB 排版规范：表题在上、图题在下）
        # 🔴 表号一律由系统按「章-序号」重编，治 LLM 写错表号（如第八章的评分表写成「表7-1」）
        # 🔴 复垦工程量表：单位按样本**确定性归一**（模型常把 `100m³` 写成 `100m`）
        if rows:
            rows = [rows[0]] + self._normalize_qty_units(rows[0], rows[1:])
        header_cells = rows[0] if rows else []

        # 🔴 表4-3 / 表12-3「群众意见分析」：**按系统模板渲染**。
        #    模板定义见 `knowledge_agent` 的「群众意见分析表」：3 行固定结构，
        #    b（风险发生概率描述）为固定文案 1 / 0.7 / 0.3，**得分 = 反对率a × b × 35**。
        #    LLM 手写时常只写 1 行、b 也不照模板，印出来像「没有数据」
        #    （业主：「群众意见分析不是有模板么」）。这里按模板就地重排。
        _hj = "".join(str(c) for c in header_cells)
        if '反对率' in _hj and '得分' in _hj:
            # 🔴 a 列的口径（照 `1cbf7fba` 模板反推，三档合计 100%）：
            #      行1 = 反对率（反对＋强烈反对）
            #      行2 = 无所谓 ＋ 有条件支持
            #      行3 = 支持率                    ← 业主记的「98」
            #    例：某模板「支持77.78%、无所谓11.11%、有条件支持11.11%、反对0%」
            #        → 行1 0% / 行2 22.22%(2+2人) / 行3 77.78%(14/18)，逐项对得上。
            _ss = survey_stats or {}
            _st = getattr(self, '_session_state', None) or {}
            _q = (_st.get('_questionnaire_result') or {}) if isinstance(_st, dict) else {}
            _valid = int(_q.get('valid_samples') or _q.get('total_samples') or 0)
            if _valid:
                _c_sup = int(_q.get('support_count') or 0)
                _c_opp = int(_q.get('oppose_count') or 0)
            else:
                _valid = int(_ss.get('total_surveys') or 0)
                _c_sup = int(_ss.get('support_count') or 0)
                _c_opp = int(_ss.get('oppose_count') or 0)
            if _valid:
                _r_sup = min(1.0, _c_sup / _valid)
                _r_opp = min(1.0, _c_opp / _valid)
                # 🔴 行2（无所谓＋有条件支持）取**余数**，不直接读 neutral_count ——
                #    汇总里那一栏常年是 0（实测 50 份问卷只解析出 49 份，剩下 1 份
                #    没进汇总），三档就只有 0+0+98=98%，业主：「总数要算成 100」。
                #    余数法保证 行1+行2+行3 ≡ 100%，且与表9-1 的各选项人数自洽。
                _r_neu = max(0.0, 1.0 - _r_sup - _r_opp)
            else:
                _r_sup = _r_opp = _r_neu = 0.0
            # 🔴 必须**保留表头行**：`_render_md_table` 把 rows[0] 当表头，
            #    丢掉它会让第一条数据行被当成表头（实测表头整行消失）。
            rows = [header_cells] + [
                ["1", f"{_r_opp * 100:.2f}%",
                 "肯定，有较大可能发生的或表示强烈反对的为1", f"{_r_opp * 1 * 35:.2f}"],
                ["2", f"{_r_neu * 100:.2f}%",
                 "有可能发生的或反对较强烈的为0.7", f"{_r_neu * 0.7 * 35:.2f}"],
                ["3", f"{_r_sup * 100:.2f}%",
                 "发生概率很小的或反对激烈程度一般的0.3", f"{_r_sup * 0.3 * 35:.2f}"],
            ]
            ncols = 4

        if not hasattr(self, '_ch_table_seq'):
            self._ch_table_seq = {}
        _seq = self._ch_table_seq.get(ch_num, 0) + 1
        self._ch_table_seq[ch_num] = _seq
        # 名称：取 LLM 表题去掉原表号后的名称部分；缺则按列结构查注册表。
        # 🔴 注册表查不到时才回退**本节标题**（模型常把表名写成小节标题）。
        #    这里**不做跨章去重**：参照件模板本身就允许不同章出现同名表
        #    （第4章表4-3 与第12章表12-3 都叫「群众意见分析」，一套措施后一套措施前），
        #    强行改名反而偏离模板。真正的重名要靠**章节内容摆对**来消除
        #    （第11章按模板无表，就不该抢第12章的表）。
        _name = re.sub(r'^\s*表\s*\d+\s*[-—]\s*\d+\s*', '', (caption or '').strip())
        if not _name:
            _name = self._lookup_table_name(ch_num, header_cells)
            if not _name:
                _sec = getattr(self, '_cur_section_name', '') or ''
                if _sec.endswith('表'):
                    _name = _sec
        caption = f"表{ch_num}-{_seq} {_name}".strip() if _name else f"表{ch_num}-{_seq}"
        caption = self._ensure_table_caption_name(caption, rows[0] if rows else [])
        self._add_table_caption(doc, caption)

        table = doc.add_table(rows=len(rows), cols=ncols, style='Table Grid')
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._apply_tpl_table_style(table)

        _tf, _ts = self._table_typo_for(ch_num, caption, ncols)
        _hbold = self._table_bold_for(ch_num, caption)
        for ri, row_data in enumerate(rows):
            for ci in range(ncols):
                cell_text = self._clean_md_cell(row_data[ci] if ci < len(row_data) else '')
                cell = table.cell(ri, ci)
                cell.paragraphs[0].clear()
                r = cell.paragraphs[0].add_run(cell_text)
                r.font.name = _tf; r._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
                r.font.size = _ts
                if ri == 0:
                    r.bold = _hbold
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        self._fit_wide_table(table, rows[0] if rows else [], rows[1:])
        self._merge_first_col(table, _tf, _ts)
        doc.add_paragraph()

    # 🔴 「合计/小计」行永不并入上一行
    _NO_MERGE_VALUES = ("合计", "小计", "总计", "合  计")

    @staticmethod
    def _merge_land_group_cells(table) -> None:
        """表4-3/4-4 的分组格式：表头前两列合并、一级地类纵向合并、合计行前两列合并。"""
        def _keep_first(cell) -> None:
            ps = cell.paragraphs
            for p in ps[1:]:
                p._element.getparent().remove(p._element)

        try:
            table.cell(0, 0).merge(table.cell(0, 1))
            _keep_first(table.cell(0, 0))
        except Exception:
            pass
        n = len(table.rows)
        idx = 1
        while idx < n:
            try:
                txt = table.cell(idx, 0).text.strip()
            except Exception:
                break
            if txt == "合计":
                try:
                    table.cell(idx, 0).merge(table.cell(idx, 1))
                    _keep_first(table.cell(idx, 0))
                except Exception:
                    pass
                idx += 1
                continue
            j = idx
            while j + 1 < n:
                try:
                    nxt = table.cell(j + 1, 0).text.strip()
                except Exception:
                    break
                if nxt != txt:
                    break
                j += 1
            if j > idx:
                try:
                    table.cell(idx, 0).merge(table.cell(j, 0))
                    _keep_first(table.cell(idx, 0))
                except Exception:
                    pass
            idx = j + 1

    @staticmethod
    def _merge_plot_group_cells(table, sizes) -> None:
        """表4-2 的模板格式：面积列拆「小计 | 逐个地块」，其余列按分区纵向合并。

        模板（井神 11.2 p18）：序号/地块名称/地块个数/损毁程度 在每个分区上合并成一个
        大格；面积列左栏是分区小计（同样纵向合并），右栏一行一个地块面积；
        表头「面积（hm2）」跨两栏；合计行「合计」跨前两栏。
        `sizes` 是各分区占用的数据行数（与 `table_damage_inventory` 的 `group_sizes` 对应）。
        """
        def _keep_first(cell) -> None:
            ps = cell.paragraphs
            for p in ps[1:]:
                p._element.getparent().remove(p._element)

        def _vmerge(col: int, top: int, bottom: int) -> None:
            if bottom <= top:
                return
            try:
                table.cell(top, col).merge(table.cell(bottom, col))
                _keep_first(table.cell(top, col))
            except Exception:
                pass

        # 表头：面积两栏合并
        try:
            table.cell(0, 2).merge(table.cell(0, 3))
            _keep_first(table.cell(0, 2))
        except Exception:
            pass

        idx = 1
        for n in sizes:
            n = max(int(n or 0), 1)
            for col in (0, 1, 2, 4, 5):
                _vmerge(col, idx, idx + n - 1)
            idx += n

        # 合计行：前两栏合并
        for r in range(idx, len(table.rows)):
            try:
                if table.cell(r, 0).text.strip().startswith("合计"):
                    table.cell(r, 0).merge(table.cell(r, 1))
                    _keep_first(table.cell(r, 0))
            except Exception:
                pass

    @staticmethod
    def _merge_first_col(table, font=None, size=None) -> int:
        """把表格**首列**相邻同名的单元格纵向合并（业主：「部分表格的第一列要合并同类项」）。

        问卷统计表最典型：首列写的是「您的身份 / 您的年龄 / 您的职业 …」，
        每类下面挂 3~4 个选项，不合并就会把同一个词重复印五六遍。

        🔴 只动首列：其余列即使同名也各有各的含义（如「了解/了解一些」），合并会串行。
        🔴 只在**连续 ≥2 行同名**时合并；空单元格与「合计/小计/总计」一律不并。
        返回合并的组数。
        """
        try:
            rows = table.rows
            n = len(rows)
        except Exception:
            return 0
        merged, i = 0, 1                 # 跳过表头
        while i < n:
            try:
                txt = rows[i].cells[0].text.strip()
            except Exception:
                i += 1
                continue
            if not txt or txt in ReportAssembler._NO_MERGE_VALUES:
                i += 1
                continue
            j = i + 1
            while j < n:
                try:
                    nxt = rows[j].cells[0].text.strip()
                except Exception:
                    break
                if nxt != txt or nxt in ReportAssembler._NO_MERGE_VALUES:
                    break
                j += 1
            if j - i >= 2:
                try:
                    a, b = rows[i].cells[0], rows[j - 1].cells[0]
                    a.merge(b)
                    # 🔴 merge() 会把被并各格的文字**堆到一起**（「您的身份」叠成 2~4 行），
                    #    必须压回一行原文，否则合并格里同一句话重复印好几遍。
                    _paras = a.paragraphs
                    for _p in _paras[1:]:
                        _p._element.getparent().remove(_p._element)
                    _keep = a.paragraphs[0]
                    for _r in list(_keep.runs):
                        _r._element.getparent().remove(_r._element)
                    _run = _keep.add_run(txt)
                    _tf, _ts = font or FONT_TABLE, size or TABLE_SIZE
                    _run.font.name = _tf
                    _run._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
                    _run.font.size = _ts
                    _keep.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    # 合并后的单元格垂直居中
                    _tcPr = a._tc.get_or_add_tcPr()
                    from docx.oxml import OxmlElement as _OE
                    for _old in _tcPr.findall(qn('w:vAlign')):
                        _tcPr.remove(_old)
                    _va = _OE('w:vAlign'); _va.set(qn('w:val'), 'center')
                    _tcPr.append(_va)
                    merged += 1
                except Exception:
                    pass
            i = j
        return merged

    def _lookup_table_name(self, ch_num: int, header_cells: list) -> str:
        """按章节 + 表头列结构，从 TABLE_REGISTRY 查找标准表名。

        用于把「表2-1」这类纯编号表题补全为「表2-1 评估依据法规清单」。
        匹配失败返回空串（不强行命名）。
        """
        try:
            from app.services.table_registry import TABLE_REGISTRY
        except Exception:
            return ""

        def _norm(h):
            h = re.sub(r'[（(].*?[)）]', '', str(h or ''))   # 去括号内容（总面积(㎡)→总面积）
            h = re.sub(r'[\s\*_`~]', '', h)                    # 去空白与 markdown 残留
            return h

        hdr = {_norm(self._clean_md_cell(c)) for c in header_cells}
        hdr.discard('序号')

        same, other = [], []
        for entry in TABLE_REGISTRY.values():
            cols = {_norm(c) for c in entry.get("columns", [])}
            cols.discard('序号')
            score = len(hdr & cols) / max(1, len(cols))
            (same if entry.get("chapter") == ch_num else other).append(
                (score, entry.get("description", "") or ""))
        # 🔴 同章命中优先；**跨章要近乎全等**（≥0.9）才敢用 ——
        #    否则宁可不命名，也不把别章的表名安到这张表上。
        for pool, thresh in ((same, 0.5), (other, 0.9)):
            pool = [x for x in pool if x[0] >= thresh and x[1]]
            if pool:
                return max(pool)[1]

        # 🔴 注册表查不到时才用**语义命名**兜底（比死记列名稳：LLM 每次生成的列名会飘）。
        #    必须放在注册表之后 —— 放前面会盖掉注册表给的准确名字
        #    （实测第12章那张被改名成「主要不稳定风险因素及等级一览表」，
        #      与第4章的表4-1 撞名）。
        _hj = "".join(hdr)
        if '风险因素' in _hj and '风险等级' in _hj:
            return "主要不稳定风险因素及等级一览表"
        if '反对率' in _hj and '得分' in _hj:
            return "群众意见分析"
        return ""

    @staticmethod
    def _caption_key(caption: str):
        """「表6-1 土地分类面积表」→ `('6-1')`；不是表题返回 None。"""
        m = re.match(r'^表\s*(\d+)\s*[-－]\s*(\d+)', str(caption or "").strip())
        return f"{m.group(1)}-{m.group(2)}" if m else None

    @staticmethod
    def _header_sig(headers) -> frozenset:
        """表头签名：去掉「（㎡）」这类单位括注再比对（模型手写时常把单位塞进表头）。"""
        out = set()
        for h in headers or []:
            s = re.sub(r'[（(][^）)]*[）)]', '', str(h)).strip()
            if s:
                out.add(s)
        return frozenset(out)

    def _remember_table_sig(self, headers, nrows: int) -> None:
        """记下系统渲染表的表头签名，供「无表题的重复表」判重用。"""
        _sig = self._header_sig(headers)
        _ch = int(getattr(self, "_current_chapter", 0) or 0)
        if not (_ch and _sig):
            return
        if not hasattr(self, "_rendered_sigs") or self._rendered_sigs is None:
            self._rendered_sigs = []
        self._rendered_sigs.append((_ch, _sig, nrows))

    def _insert_template_table(self, doc, caption, keywords) -> bool:
        """把模板里的整张表深拷贝进来（保留格式与合并单元格）；找不到返回 False。"""
        tp = str((getattr(self, "_session_state", {}) or {}).get("template_path") or "")
        if not tp:
            return False
        try:
            from app.services.template_table_copy import find_template_table
            tbl = find_template_table(tp, keywords)
        except Exception as e:
            logger.info(f"[模板表] 取表失败: {e}")
            return False
        if tbl is None:
            return False
        self._add_table_caption(doc, caption)
        body = doc.element.body
        sect = body.find(qn('w:sectPr'))
        if sect is not None:
            sect.addprevious(tbl)
        else:
            body.append(tbl)
        doc.add_paragraph()
        logger.info(f"[模板表] 已按模板原样插入「{caption[:24]}」")
        return True

    def _ensure_table_caption_name(self, caption, header) -> str:
        """裸表题「表3-1」补名称（业主 2026-11-05：表3-1 连名称都没了）。"""
        cap = str(caption or "").strip()
        if not re.match(r'^\**表\s*\d+\s*[-—]\s*\d+\**$', cap):
            return cap
        h = " ".join(str(x) for x in (header or []))
        if "一级地类" in h and "二级地类" in h:
            name = "土地分类面积表"
        elif "权属单位" in h:
            name = "土地权属状况表"
        elif "损毁" in h:
            name = "土地损毁情况表"
        elif "复垦方向" in h:
            name = "复垦方向表"
        elif "面积" in h:
            name = "面积统计表"
        else:
            name = "统计表"
        _m = re.match(r'^(\**表\s*\d+\s*[-—]\s*\d+\**)', cap)
        return f"{_m.group(1)} {name}" if _m else cap

    def _add_table_caption(self, doc, caption):
        """在表格正上方加表题（宋体 小四 12pt 居中）。"""
        _k = self._caption_key(caption)
        if _k:
            if not hasattr(self, "_rendered_captions") or self._rendered_captions is None:
                self._rendered_captions = set()
            self._rendered_captions.add(_k)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(caption)
        _cf, _cs, _cb = self._caption_typo()
        r.font.name = _cf; r._element.rPr.rFonts.set(qn('w:eastAsia'), _cf)
        r.font.size = _cs
        r.bold = _cb

    def _set_cell(self, cell, text, bold=False, ncols: int = 0, typo=None):
        cell.paragraphs[0].clear()
        r = cell.paragraphs[0].add_run(text)
        _tf, _ts = typo or self._table_typo(ncols)
        r.font.name = _tf; r._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
        r.font.size = _ts
        r.bold = bold

    # ── 复垦第4章：系统补渲的分析表（表4-8/4-11/4-12/4-13）────────────────
    #    样本第4章有 13 张表，我们只渲染 3 张 → 这是「134 页 vs 67 页」的一处主因。
    #    其中四张可由「勘测定界地类 + 规范标准值」确定性推导（见
    #    `reclamation_ch4_tables`），落点按小节号（4.4 / 4.5 / 4.7）。
    #    装配器在新小节标题出现时把**归属更早小节**的表先落下去，章末兜底其余。

    # ═══════════════════════════════════════════════════════════
    # 系统补渲表：按章登记、在小节边界落位（ch4 分析表 / ch7 投资估算表共用）
    # ═══════════════════════════════════════════════════════════
    @staticmethod
    def _sec_key(text) -> Optional[tuple]:
        """「7.2.1 工程施工费」→ (7, 2, 1)；「4.5 水土资源平衡」→ (4, 5, 0)。"""
        m = re.match(r'^\s*(\d+)(?:[.．](\d+))?(?:[.．](\d+))?', str(text or ""))
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))

    def _reserve_system_captions(self, items) -> None:
        """把系统表的**表题与表号**登记为受保护（不渲染，只占号）。

        🔴 与 `_register_dyn_tables` 分开：那张表可能因为「模型已写同题表」被跳过不渲染，
        但它仍占着样本表号（表4-11/4-13）；不登记就会被顺序重编成 表4-1/表4-3。
        """
        for _sec, _tbl in (items or []):
            _cap = str((_tbl or {}).get("caption") or "")
            if not _cap:
                continue
            self._system_caption_exact.add(_cap)
            _m = re.match(r'^表\s*(\d+)\s*[-—]\s*(\d+)', _cap)
            if not _m:
                continue
            self._system_caption_nums.setdefault(int(_m.group(1)), set()).add(int(_m.group(2)))
            _title = re.sub(r'^表\s*\d+\s*[-—]\s*\d+\s*', '', _cap).strip()
            _prefix = re.match(r'^(表\s*\d+\s*[-—]\s*\d+)', _cap)
            if _title and _prefix:
                self._system_caption_by_title[_title] = re.sub(r'\s+', '', _prefix.group(1))

    def _register_dyn_tables(self, ch_num: int, items) -> None:
        """登记某章的待落位表：`items = [(小节号, {caption, headers, rows, lead})]`。"""
        store = getattr(self, "_dyn_pending", None)
        if store is None:
            store = self._dyn_pending = {}
        store[ch_num] = list(items or [])
        # 表号按样本定死（表4-8/7-3…），`_dedup_table_titles` 不重编号、且新表跳号
        self._reserve_system_captions(store[ch_num])

    def _flush_dyn_tables(self, doc, ch_num: int, heading_text: str) -> None:
        """遇到新的小节标题时，把**归属更早小节**的待落位表先落下去。"""
        pending = (getattr(self, "_dyn_pending", {}) or {}).get(ch_num) or []
        if not pending:
            return
        cur = self._sec_key(heading_text)
        if not cur:
            return
        keep = []
        for sec, tbl in pending:
            _s = self._sec_key(sec) or (0, 0, 0)
            (self._render_dyn_table(doc, tbl) if _s < cur else keep.append((sec, tbl)))
        self._dyn_pending[ch_num] = keep

    def _flush_dyn_tables_end(self, doc, ch_num: int) -> None:
        """章末兜底：没等到后续小节标题的表（含按 `###` 分节或小节缺失）在此落下。"""
        _store = getattr(self, "_dyn_pending", None)
        if not _store:
            return
        for _sec, tbl in _store.pop(ch_num, []):
            self._render_dyn_table(doc, tbl)

    @staticmethod
    def _survey_rows_have_counts(rows) -> bool:
        """问卷统计表是否**真有人数**（第 3 列）；全 0 / [待分析] 视为没有。"""
        for r in rows or []:
            try:
                if float(str(r[2]).strip()) > 0:
                    return True
            except Exception:
                continue
        return False

    def _render_dyn_table(self, doc, tbl: dict) -> None:
        """引导语 + 表格（表题由 `_render_table_from_dict` 印在表格上方）。"""
        _lead = str(tbl.get("lead") or "")
        if _lead:
            self._add_para(doc, _lead, indent=True)
        _copy = tbl.get("template_copy")
        if _copy:
            if self._insert_template_table(doc, tbl.get("caption", ""), tuple(_copy)):
                return
        self._render_table_from_dict(doc, tbl, caption=tbl.get("caption", ""))

    def _init_ch1_tables(self, filled) -> None:
        """复垦第 1 章：表1-1 临时用地拟损毁土地情况表（分区由用户提供）。

        2026-11-03 业主：「临时用地名称是每次跟甲方沟通有多少地区，让用户提供
        地区名称还有对应的大小。」没有分区数据就整张不出（不编地块名）。
        """
        try:
            from app.services.reclamation_zones import table_1_1
            _t = table_1_1(filled or getattr(self, '_filled', None) or {})
            if _t:
                self._register_dyn_tables(1, [("1.2.3", _t)])
                logger.info(f"[表1-1] 已登记（{len(_t.get('rows') or []) - 1} 个功能分区）")
        except Exception as e:
            logger.warning(f"[表1-1] 登记失败（不影响出报告）: {e}")

    def _init_ch4_tables(self, filled, markdown: str = "") -> None:
        """登记第4章待补渲的分析表。

        模型写的同题表已在装配入口按表题整张删掉（见 `assemble` 里的
        `strip_md_tables_by_caption`），这里**只要算得出就登记**，不再按
        「模型提到过表题」跳过 —— 2026-11-04 实测那种跳过会让表两头落空。
        """
        from app.agent.chapter_catalog import has_analysis_tables_in_body
        if not has_analysis_tables_in_body(self._domain):
            return
        try:
            from app.services.reclamation_ch4_tables import build_ch4_tables
            _title = str((getattr(self, '_session_state', {}) or {}).get("report_title") or "")
            _plan = self._template_ch4_plan()
            _built = build_ch4_tables(filled or getattr(self, '_filled', None) or {},
                                      title=_title, plan=_plan)
            # 🔴 模板的「土地适宜性评价指标体系」整张照搬（排在 4.4 各表最前）
            _ind_no = self._template_ch4_no("指标体系")
            if _ind_no:
                _e = next((x for x in (_plan or [])
                           if int(x.get("no") or 0) == _ind_no), {})
                _t = re.sub(r'\s+', ' ', str(_e.get("title") or "土地适宜性评价指标体系")).strip()
                _built = [("4.4", {
                    "caption": f"表4-{_ind_no} {_t}",
                    "lead": f"本项目土地复垦适宜性评价指标体系见表4-{_ind_no}。",
                    "template_copy": ("一级影响因子", "三级影响因子"),
                    "headers": [], "rows": [],
                })] + list(_built)
            self._register_dyn_tables(4, _built)
            if _built:
                logger.info(f"[表4-x] 第4章分析表 {len(_built)} 张："
                            f"{[t.get('caption','')[:9] for _, t in _built]}")
        except Exception as e:
            logger.warning(f"[表4-x] 第4章分析表准备失败（不影响出报告）: {e}")

    def _budget_estimate(self):
        """复垦投资估算（第7章表与报告表共用，**只算一次**）。

        优先用生成前预计算好的那份（`filled_data["_budget_estimate"]`，见
        `reclamation_quantities.enrich_filled_data`）—— 第6/7章正文读的也是它，
        报告表与附表才对得上。没有预计算（脚本路径 / 老会话）时才现连桥。
        """
        if getattr(self, "_budget_done", False):
            return getattr(self, "_budget_est", None)
        self._budget_done = True
        self._budget_est = (getattr(self, "_filled", None) or {}).get("_budget_estimate")
        if isinstance(self._budget_est, dict):          # 预计算那份落库时是 dict
            try:
                from app.services.reclamation_budget_bridge import BudgetEstimate
                self._budget_est = BudgetEstimate.from_dict(self._budget_est)
            except Exception:
                self._budget_est = None
        if self._budget_est is not None:
            return self._budget_est
        from app.agent.chapter_catalog import uses_budget_bridge
        if not uses_budget_bridge(self._domain):
            return None
        try:
            from app.services.reclamation_budget_bridge import (
                estimate_for_report, parse_quantity_rows,
            )
            from app.services.reclamation_quantities import QUANTITY_ROWS_KEY
            fd = getattr(self, "_filled", None) or {}
            rows = (fd.get(QUANTITY_ROWS_KEY)
                    or parse_quantity_rows(
                        ((getattr(self, "_chapters", {}) or {}).get(6) or {}).get("markdown", "")))
            if not rows:
                logger.info("[表7-x] 没有工程量数据，跳过投资估算")
                return None
            try:
                _ha = float(str(fd.get("area_m2") or 0).replace(",", "")) / 10000.0
            except Exception:
                _ha = 0.0
            try:
                _m = re.search(r'\d+(?:\.\d+)?', str(fd.get("service_years") or ""))
                _months = float(_m.group(0)) if _m else 0.0
            except Exception:
                _months = 0.0
            self._budget_est = estimate_for_report(
                rows, area_ha=_ha, service_months=_months,
                project_name=str(fd.get("project_name") or ""))
        except Exception as e:
            logger.warning(f"[表7-x] 投资估算取数失败（不影响出报告）: {e}")
        return self._budget_est

    def _init_ch6_tables(self) -> None:
        """第6章 表6-1「项目复垦工程量汇总表」—— 系统按勘测定界地类算，不由模型写。

        模型自己写的那张表在 `_render_reclamation_system_table` 里被丢掉
        （否则两张同题表、数字还打架）。
        """
        from app.agent.chapter_catalog import has_analysis_tables_in_body
        if not has_analysis_tables_in_body(self._domain):
            return
        try:
            from app.services.reclamation_quantities import (
                QUANTITY_ROWS_KEY, build_quantity_table,
            )
            fd = getattr(self, "_filled", None) or {}
            if not fd.get(QUANTITY_ROWS_KEY):
                return
            _t = build_quantity_table(fd)
            if not _t:
                return
            _t = dict(_t)
            _t["lead"] = ""
            self._register_dyn_tables(6, [("6.2", _t)])
            logger.info(f"[表6-1] 第6章工程量汇总表由系统渲染（{len(_t['rows'])} 行）")
        except Exception as e:
            logger.warning(f"[表6-1] 工程量表准备失败（不影响出报告）: {e}")

    def _init_ch7_tables(self) -> None:
        """第7章「投资估算」的表7-3/7-4/7-5。

        🔴 两个口径（业主 2026-11-05）：
          ① 用户上传了《投资估算 Excel》（第七章管道费用表）→ **以它为唯一口径**，
             表7-3/7-4/7-5 直接按 Excel 渲染（工程量也与 Excel 同源）；
          ② 没有 Excel → 仍由 budget-maker 现算，补表7-3/表7-5（回落口径）。
        都取不到 → 整块不补，第7章保持模型原文（金额不编）。
        """
        from app.agent.chapter_catalog import uses_budget_bridge
        if not uses_budget_bridge(self._domain):
            return
        _wb = (getattr(self, "_filled", None) or {}).get("_cost_workbook")
        if isinstance(_wb, dict) and _wb.get("engineering"):
            try:
                from app.services.reclamation_cost_excel import (
                    engineering_table, other_cost_table, summary_table,
                )
                _tbls = []
                for _sec, _t in (("7.2.1", engineering_table(_wb)),
                                 ("7.2.3", other_cost_table(_wb)),
                                 ("7.2.6", summary_table(_wb))):
                    if _t:
                        _t = dict(_t)
                        _t["lead"] = ""
                        _tbls.append((_sec, _t))
                if _tbls:
                    self._register_dyn_tables(7, _tbls)
                    logger.info(f"[表7-x] 按用户投资估算 Excel 渲染 {len(_tbls)} 张表")
                    return
            except Exception as e:
                logger.warning(f"[表7-x] 按 Excel 渲染失败，回落 budget-maker: {e}")
        try:
            from app.services.reclamation_budget_bridge import (
                engineering_table, investment_table,
            )
            est = self._budget_estimate()
            if not est:
                return
            _tbls = []
            for _sec, _t in (("7.2.1", engineering_table(est)),
                             ("7.2.6", investment_table(est))):
                if _t:
                    _t = dict(_t)
                    _t["lead"] = ""
                    _tbls.append((_sec, _t))
            self._register_dyn_tables(7, _tbls)
            logger.info(f"[表7-x] 第7章待补渲 {len(_tbls)} 张（工程施工费 "
                        f"{est.construction:.2f} 万元、静态总投资 {est.static_total:.2f} 万元）")
        except Exception as e:
            logger.warning(f"[表7-x] 第7章投资估算表准备失败（不影响出报告）: {e}")

    def _init_ch8_tables(self) -> None:
        """第8章 表8-1「土地复垦费用安排表」—— 阶段工程费 + 按比例分摊。

        业主 2026-11-05 口径：四期金额按**阶段工程费 + 按比例分摊**其他费/预备费，
        合计严格等于动态总投资。没有投资估算 Excel 的数据就**整张不出**（不估）。
        """
        from app.agent.chapter_catalog import uses_budget_bridge
        if not uses_budget_bridge(self._domain):
            return
        fd = getattr(self, "_filled", None) or {}
        _wb = fd.get("_cost_workbook")
        try:
            from app.services.reclamation_cost_excel import phase_schedule_table
            _t = None
            if isinstance(_wb, dict) and _wb.get("engineering"):
                _t = phase_schedule_table(_wb, fd)
            if not _t:
                logger.info("[表8-1] 缺日期或估算数据，本期不出费用安排表")
                return
            _t = dict(_t)
            _t["lead"] = ""
            self._register_dyn_tables(8, [("8.3", _t)])
            logger.info(f"[表8-1] 第8章费用安排表由系统渲染（{len(_t['rows'])} 期）")
        except Exception as e:
            logger.warning(f"[表8-1] 费用安排表准备失败（不影响出报告）: {e}")

    def _render_reclamation_system_table(self, doc, caption, ch_num) -> bool:
        """复垦的**系统渲染表**（按样本逐表核对，不让 LLM 自己编）。

        样本（明远路报告书）里这三张表的归属是**唯一确定**的：

            第1章  表1-1 临时用地拟损毁土地情况表
                   临时用地名称 | 面积 | 地块个数 | 损毁方式 | 损毁程度 | 复垦情况
                   施工用地 | 0.8070 | 1 | 压占 | 重度 | 待复垦
                   合计     | 0.8070 | —— | —— | —— | ——
           第3章  **没有任何表**
           第4章  表4-4 临时用地土地利用现状统计表（按地类逐项 + 小计 + 合计）

        实测我们这一版：LLM 在第1章编了张「土地利用现状地类构成表」、第3章又编了张
        「土地利用现状地类表」——**名不对、归属不对、两张还都重复第4章的数据**，
        而且合计行写串列（`| 合计 | 8070.0 | — |` 把面积塞进了「二级地类」列）。
        业主：「表1-1 跟应该做出来的样式不一样」「表3-1 合计的数据不对」。

        所以按章收口（`表格由系统渲染` 的老办法，与稳评 SYS_TABLE 同一思路）：
          第1章 + 表题含「损毁/土地利用现状/地类」 → 渲染样本的**拟损毁表**
          第3章 + 表题含「土地利用现状/地类」      → **整张不渲染**（样本无此表）
          第4章 + 表题是「临时用地土地利用现状统计表」→ 按地类渲染

        返回 True = 已处理（调用方不要再用 markdown 渲染那张）。
        """
        filled = getattr(self, '_filled', None) or {}
        cap = str(caption or "").strip()
        # 🔴 本函数是**复垦专用**的，稳评一律不进：它按 `land_classification` 是
        #    dict（勘测定界视觉读出的地类表）写的，而稳评同名键是纯文本字符串
        #    （「耕地为主…」）—— 直接进来就是 `'str' object has no attribute 'get'`，
        #    **整份稳评装配失败、一个字都产不出来**（实测 2026-10-28）。
        #    以前只在 `_render_md_table` 入口无条件调用，等于把复垦的取数口径
        #    强加到稳评每一张 markdown 表上。域判断放这里，调用点保持通用。
        if getattr(self, "_domain", "stability") != "reclamation":
            return False
        # 🔴 第1章（前言）**不排任何表**（业主 2026-10-31：「前言只写文字」）：
        #    模型手写的地类表、系统补渲的损毁表，一律丢弃。
        #    返回 True = 调用方认为已处理，不再走默认渲染。
        if int(ch_num) == 1:
            logger.info(f"[表1-x] 前言不排表，已丢弃：{cap}")
            return True

        _is_land = ("土地利用现状" in cap) or ("地类" in cap)

        # ── 第6章：工程量汇总表**由系统算**（同一会话两次生成必须同数）──
        #    模型那张表丢掉，**就地**换成系统表 —— 模型写「见表6-1」时表就在它原位置；
        #    若模型压根没写这张表，则由 `_init_ch6_tables` 登记的那张在小节边界兜底。
        if ch_num == 6 and ("工程量" in cap or "工程数量" in cap):
            if filled.get("_quantity_rows"):
                try:
                    from app.services.reclamation_quantities import build_quantity_table
                    _t = build_quantity_table(filled)
                    if _t:
                        self._render_dyn_table(doc, dict(_t, lead=""))
                        (getattr(self, "_dyn_pending", {}) or {}).pop(6, None)
                        logger.info(f"[表6-1] 丢弃模型写的工程量表，就地换成系统表：{cap}")
                        return True
                except Exception as e:                     # pragma: no cover
                    logger.warning(f"[表6-1] 系统工程量表渲染失败，保留模型表: {e}")

        # ── 第3章：样本没有这张表 —— 整张丢掉（数据归第4章表4-4）──
        if ch_num == 3 and _is_land:
            logger.info(f"[表3-1] 第3章不设土地利用现状表（样本无此表），已丢弃 LLM 表：{cap}")
            return True

        # ── 第4章：按地类逐项渲染（数据来自勘测定界的土地分类面积表）──
        lc = filled.get("land_classification")
        lc = lc if isinstance(lc, dict) else {}
        rows = lc.get("rows") or []

        # 🔴 第4章「评价单元」两张表（表4-7 土地损毁 / 表4-10 复垦方向）**由系统出数**：
        #    走 `reclamation_ch4_tables.build_ch4_tables` 的小节落点机制
        #    （模型写了同题表就跳过、没写就补渲），不在这里按表题兜。

        if not rows or "临时用地土地利用现状统计表" not in cap:
            return False

        total = float(lc.get("合计_m2") or 0) or sum(float(r.get("面积_m2") or 0) for r in rows)
        if total <= 0:
            return False

        groups, order = {}, []
        for r in rows:
            g = str(r.get("一级地类") or "").strip() or "—"
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append(r)

        out_rows = []
        for g in order:
            items = groups[g]
            for r in items:
                _ha = float(r.get("面积_m2") or 0) / 10000.0
                out_rows.append([g, str(r.get("二级地类") or "").strip() or "—",
                                 f"{_ha:.4f}", f"{_ha * 10000.0 / total * 100:.2f}"])
            if len(items) > 1:       # 单子项时小计与上面那行重复，不印
                _sub = sum(float(r.get("面积_m2") or 0) for r in items)
                out_rows.append([f"{g} 小计", "—",
                                 f"{_sub / 10000.0:.4f}", f"{_sub / total * 100:.2f}"])
        out_rows.append(["合计", "—", f"{total / 10000.0:.4f}", "100"])

        self._render_table_from_dict(
            doc,
            {"headers": ["一级地类", "二级地类", "面积（公顷）", "占比（%）"],
             "rows": out_rows},
            caption=cap)
        logger.info(f"[表4-2] 按勘测定界地类渲染 {len(out_rows)} 行（合计 {total / 10000.0:.4f} 公顷）")
        return True

    def _normalize_qty_units(self, headers, rows):
        """复垦工程量表的**单位归一**（确定性，不靠模型自觉）。

        🔴 为什么不能只写进提示词：实测第6章提示词里已明写「单位用 `100m³`」，
           markdown 里**模型还是写成 `100m`**（丢了立方）。单位是公式化的 ——
           哪一项用什么单位在样本里是固定的，直接按工程名归一，比反复叮嘱可靠。

        样本（明远路 6.2 工程量表）的实际单位：
            表土剥离 / 表土回填 / 混凝土面层拆除 / 垫层清除 / 垃圾外运 / 沟渠疏通 → 100m³
            土地翻耕 → 公顷      田面平整 → 100m²      土壤培肥（有机肥/复合肥） → 吨
        """
        _h = "".join(str(x) for x in (headers or []))
        if not ("工程" in _h and "单位" in _h and "数量" in _h):
            return rows
        _UNIT_BY_ITEM = (
            ("田面平整", "100m²"),
            ("土地翻耕", "公顷"),
            ("土壤培肥", "吨"),
            ("表土剥离", "100m³"), ("表土回填", "100m³"), ("表土剥覆", "100m³"),
            ("混凝土", "100m³"), ("垫层", "100m³"), ("垃圾外运", "100m³"),
            ("沟渠", "100m³"), ("疏通", "100m³"), ("拆除", "100m³"), ("清理", "100m³"),
        )
        out = []
        for r in rows:
            r = list(r)
            if len(r) >= 3:
                _name = str(r[1])
                _want = next((u for k, u in _UNIT_BY_ITEM if k in _name), "")
                # 只改「单位格」且只在该行是具体分项时（层级行是 `—`）
                if _want and str(r[2]).strip() not in ("", "—", "-"):
                    if str(r[2]).strip() != _want:
                        r[2] = _want
            out.append(r)
        return out

    def _render_table_from_dict(self, doc, table_data: dict, caption: str = ""):
        """从 {headers, rows} 结构渲染系统生成的标准表（如表3-1六维群众意见表）。

        表题统一放表格正下方（与 _render_md_table 一致）。
        """
        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])
        if not headers:
            return
        ncols = len(headers)
        nrows = len(rows) + 1  # +1 header
        # 🔴 表题放表格正上方
        caption = caption or table_data.get("caption", "")
        if caption:
            self._add_table_caption(doc, caption)
        # 🔴 记下签名：同章后面若冒出一张**无表题**但表头/行数都一样的表（模型手写的
        #    重复表），`_render_md_table` 据此判重丢弃。
        try:
            self._remember_table_sig(headers, nrows)
        except Exception:
            pass
        table = doc.add_table(rows=nrows, cols=ncols, style='Table Grid')
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._apply_tpl_table_style(table)

        _tf, _ts = self._table_typo_for(getattr(self, "_current_chapter", 0), caption, ncols)
        _hbold = self._table_bold_for(getattr(self, "_current_chapter", 0), caption)
        for ci, h in enumerate(headers):
            cell = table.cell(0, ci)
            cell.paragraphs[0].clear()
            r = cell.paragraphs[0].add_run(str(h))
            r.font.name = _tf; r._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
            r.font.size = _ts; r.bold = _hbold
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        for ri, row in enumerate(rows, start=1):
            for ci in range(ncols):
                val = row[ci] if ci < len(row) else ""
                cell = table.cell(ri, ci)
                cell.paragraphs[0].clear()
                r = cell.paragraphs[0].add_run(self._clean_md_cell(str(val)))
                r.font.name = _tf; r._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
                r.font.size = _ts
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        self._fit_wide_table(table, headers, rows)
        if table_data.get("land_groups"):
            self._merge_land_group_cells(table)
        elif table_data.get("plot_groups"):
            self._merge_plot_group_cells(table, table_data.get("group_sizes") or [])
            try:
                self._keep_blank_tables.add(table._tbl)
            except Exception:
                pass
        if table_data.get("keep_blank"):
            # 表7-3/7-4/7-5/8-1：模板里空着的格就是空白，不能填「—」
            try:
                self._keep_blank_tables.add(table._tbl)
            except Exception:
                pass
        if table_data.get("merge_header") and ncols >= 3:
            # 表7-4 的表头「其他费用（万元）」跨前三栏，金额在右端（样本 p59）
            try:
                _m = table.cell(0, 0).merge(table.cell(0, 2))
                for _p in list(_m.paragraphs)[1:]:
                    _p._element.getparent().remove(_p._element)
            except Exception:
                pass

        # 🔴 记录系统渲染的标准表，_fill_table_data 不得覆盖其数据
        try:
            self._system_rendered_tables.add(id(table._element))
        except Exception:
            pass

        # 🔴 垂直合并第一列重复内容（表3-1群众/表3-3部门 的题目列，如「请问您是？」重复4行）
        if table_data.get("merge_first_col"):
            self._merge_first_col(table, *self._table_typo(ncols))

        if caption:
            doc.add_paragraph()

    def _try_render_system_survey_table(self, doc, ch_num: int, table_lines: list, caption: str = "") -> bool:
        """问卷统计表覆盖：把 LLM 手写的问卷统计表替换为系统生成的标准表。

        识别依据：表头含「调查内容」+ 计数列（人数/个数）+ 比例列。
        返回 True 表示已用系统表替换（跳过 LLM 表）。

        🔴 覆盖第9章（表9-1/表9-2）。实测 LLM 手写表的**题面会走样**
        （「您是」→「您的身份」、部门原件题面被概括），且人数存在被改写风险；
        问卷题面与人数是业主评审的核验点，必须由系统从解析结果直出。
        """
        if ch_num not in (3, 9):
            return False

        headers = []
        data_text = ""
        for line in table_lines:
            s = line.strip().strip('|')
            cells = [c.strip() for c in s.split('|') if c.strip()]
            if not cells:
                continue
            if not headers:
                headers = cells
            else:
                data_text += " ".join(cells)

        header_join = "".join(headers)
        if "调查内容" not in header_join or "比例" not in header_join:
            return False
        has_count = any(k in header_join for k in ("个数", "人数", "人次"))
        if not has_count:
            return False

        # 部门/单位问卷 vs 个人问卷
        _is_dept = any(k in (data_text + header_join)
                       for k in ("贵单位", "单位", "部门"))

        if ch_num == 9:
            _flag = "_ch9_dept_rendered" if _is_dept else "_ch9_public_rendered"
            # 同类系统表已渲染过 → 丢弃这张重复的 LLM 表（返回 True = 已处理）
            if getattr(self, _flag, False):
                return True
            _want = "ch9_dept_survey" if _is_dept else "ch9_public_survey"
            for marker, tdata in self._get_system_survey_tables(9):
                if marker == _want:
                    # 🔴 模型自己写的表题优先（如「表9-2 单位（部门）问卷统计汇总表」），
                    #    没有才用系统默认表题 —— 避免一张表两个表题。
                    self._render_table_from_dict(doc, tdata, caption=caption)
                    setattr(self, _flag, True)
                    logger.info(f"[第9章] 已用系统表替换模型手写表：{tdata.get('caption')}")
                    return True
            return False

        # 已通过 [TABLE:] 标记渲染过系统表 → 直接压制 LLM 手写表（第3章旧路径）
        if getattr(self, '_suppress_ch3_survey', False):
            return True

        from app.services.table_generation_service import TableGenerationService
        state = getattr(self, '_session_state', None) or {}
        if _is_dept:
            tdata = TableGenerationService.generate_table_3_3(state)
        else:
            tdata = TableGenerationService.generate_table_3_1(state)
        if not tdata.get("rows"):
            return False
        cap = caption or tdata.get("caption", "")
        self._render_table_from_dict(doc, tdata, caption=cap)
        return True

    # 第9章两张问卷统计表的表题（**照参照件逐字**，不得改写）
    _CH9_TABLES = (
        ("ch9_public_survey", "表9-1 个人问卷统计汇总表",
         ["调查内容", "选项", "人数（人）", "比例（%）"]),
        ("ch9_dept_survey", "表9-2 单位（部门）问卷统计汇总表",
         ["调查内容", "选项", "个数（个）", "比例（%）"]),
    )

    def _get_system_survey_tables(self, ch_num: int) -> list:
        """获取本章系统生成的标准表。

        🔴 归属（2026-09-30 校正）：
          - **第9章「各利益相关者对项目的反映」** 才是问卷统计表的归宿 ——
            表9-1 个人问卷统计汇总表 / 表9-2 单位（部门）问卷统计汇总表。
          - 第3章现行是**评审表章**（`CHAPTER_KIND[3] == "table"`，数据驱动渲染），
            原来的 `ch3_public_survey/ch3_dept_survey` 归属已过时；这里保留旧 marker
            只为兼容历史会话存档，新生成一律走第9章。

        为什么要走系统渲染而不是让模型手写：
          两份问卷的**题面必须与原件逐字一致**（业主评审硬要求）。
          实测 LLM 手写表会把「您是」写成「您的身份」、把部门原件题面
          「贵单位对本决策的了解程度如何？」概括成「对决策的了解程度」，
          且人数容易被模型改写/对调。这里直接复用 `questionnaire_parser`
          的题面常量与逐页清点结果，从源头消除走样。

        返回 [(marker_name, table_data_dict), ...]，供 [TABLE:xxx] 标记与 LLM 表覆盖使用。
        """
        # 🔴 复垦**没有问卷**：不按域关掉的话，第9章（复垦是「效益分析」）会凭空
        #    长出「表9-1 个人问卷统计汇总表 / 表9-2 单位（部门）问卷统计汇总表」，
        #    人数全 0、比例全「[待分析]」——实测 2026-10-10。
        if not has_questionnaire_tables(self._domain):
            return []
        from app.services.table_generation_service import TableGenerationService
        out = []
        state = getattr(self, '_session_state', None) or {}

        if ch_num == 9:
            _gens = (TableGenerationService.generate_table_3_1,
                     TableGenerationService.generate_table_3_3)
            for (marker, caption, headers), _gen in zip(self._CH9_TABLES, _gens):
                try:
                    # 🔴 必须 dict(...) 复制：generate_* 每次返回新 dict，但要改写
                    #    caption/headers 供第9章用，不能污染第3章口径。
                    tdata = dict(_gen(state) or {})
                    # 🔴 **一条实数据都没有就别出表**：实测 2026-10-28 表9-2（单位问卷）
                    #    人数全 0、比例全「[待分析]」，而正文写着「2 个单位均表示了解」——
                    #    印一张全 0 的统计表比不印更糟（评审会当数据造假看）。
                    #    真正的问卷原件在附件里；缺逐题清点时宁可不渲染。
                    if tdata.get("rows") and self._survey_rows_have_counts(tdata["rows"]):
                        tdata["caption"] = caption
                        tdata["headers"] = headers
                        out.append((marker, tdata))
                    elif tdata.get("rows"):
                        logger.warning(f"[第9章] {caption} 没有逐题人数，跳过不渲染（避免全 0 表）")
                except Exception as _e:
                    logger.warning(f"第9章 {caption} 生成失败: {_e}")
            return out

        # ── 兼容路径（第3章旧 marker）──
        try:
            t31 = TableGenerationService.generate_table_3_1(state)
            if t31.get("rows"):
                out.append(("ch3_public_survey", t31))
        except Exception as _e:
            logger.warning(f"generate_table_3_1 failed: {_e}")
        try:
            t33 = TableGenerationService.generate_table_3_3(state)
            if t33.get("rows"):
                out.append(("ch3_dept_survey", t33))
        except Exception as _e:
            logger.warning(f"generate_table_3_3 failed: {_e}")
        return out

    def _ensure_ch9_survey_tables(self, doc) -> None:
        """第9章兜底：把尚未落地的问卷统计表补插进正文。

        触发时机是「（一）问卷调查情况」小节结束（遇到「（二）」）以及章末。
        **不依赖模型是否输出 [TABLE:ch9_*] 标记** —— 表格是法定内容，
        模型漏写也不能缺；模型写错则由系统表覆盖，保证题面与人数不走样。
        """
        for marker, tdata in self._get_system_survey_tables(9):
            _flag = "_ch9_dept_rendered" if marker == "ch9_dept_survey" \
                else "_ch9_public_rendered"
            if getattr(self, _flag, False):
                continue
            self._render_table_from_dict(doc, tdata)
            setattr(self, _flag, True)
            logger.info(f"[第9章] 系统补插 {tdata.get('caption')}"
                        f"（{len(tdata.get('rows') or [])} 行）")


    def _add_survey_table(self, doc, stats, caption):
        headers = ['调查项目', '人数', '占比']
        support_n = int(stats.get('support_count', 0) or 0)
        oppose_n = int(stats.get('oppose_count', 0) or 0)
        neutral_n = int(stats.get('neutral_count', 0) or 0)
        total_surveys = int(stats.get('total_surveys', 0) or 0)
        # 合计 = 各项实际求和；占比 = 合计 / 总样本（总样本为0则【待补充】，不写死100%）
        sum_n = support_n + oppose_n + neutral_n
        if total_surveys > 0:
            total_pct = f"{sum_n / total_surveys * 100:.1f}%"
        elif sum_n > 0:
            total_pct = "【待计算】"
        else:
            total_pct = "【待补充】"
        rows = [
            ['支持', str(support_n), f"{stats.get('support_rate', 0)}%"],
            ['反对', str(oppose_n), f"{stats.get('oppose_rate', 0)}%"],
            ['有条件支持', str(neutral_n), f"{stats.get('neutral_rate', 0)}%"],
            ['合计', str(sum_n), total_pct],
        ]
        # 🔴 表题放表格正上方
        self._add_table_caption(doc, caption)
        table = doc.add_table(rows=len(rows)+1, cols=len(headers), style='Table Grid')
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._apply_tpl_table_style(table)
        _ncol = len(headers)
        _typo = self._table_typo_for(
            getattr(self, "_current_chapter", 0), caption, _ncol)
        _hbold = self._table_bold_for(getattr(self, "_current_chapter", 0), caption)
        for ci, h in enumerate(headers):
            self._set_cell(table.cell(0, ci), h, bold=_hbold, ncols=_ncol, typo=_typo)
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                self._set_cell(table.cell(ri+1, ci), val, ncols=_ncol, typo=_typo)
        self._fit_wide_table(table, headers, rows)

    # ═══════════════════════════════════════════════════════════════
    # Extracted Tables (from PDF)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _get_extracted_tables(state: dict) -> Dict[int, list]:
        """Collect extracted PDF tables and map them to chapters."""
        result: Dict[int, list] = {}

        sources = [
            # 🔴 `filled_data["_extracted_tables"]` 必须在这里 —— 上传分析
            #    （`routers/report.py`）与脚本（`scripts/gen_full_report.py`）
            #    产出的提取表都写在这个位置，而本方法原先只认
            #    `_project_material_facts` / `_project_materials` 两处 →
            #    `[SYS_TABLE:6-1]` 在正文里渲染不出表，正文却印着「上表所列…」。
            (state.get("filled_data") or {}).get("_extracted_tables", []),
            state.get("_extracted_tables", []),
            state.get("_project_material_facts", {}).get("_extracted_tables", []),
        ]
        summary = state.get("_project_material_summary", {})
        if isinstance(summary, dict):
            sources.append(summary.get("facts", {}).get("_extracted_tables", []))
        for m in state.get("_project_materials", []) or []:
            if isinstance(m, dict):
                meta = m.get("metadata", {})
                if isinstance(meta, dict):
                    sources.append(meta.get("extracted_tables", []))

        seen = set()
        all_tables = []
        for src in sources:
            if isinstance(src, list):
                for tbl in src:
                    if isinstance(tbl, dict):
                        # 🔴 去重签名**必须先把表头里的单位括注剥掉**：
                        #    同一个 PDF 表在两条抽取路径上会得到
                        #    「面积总计/耕地/…」与「面积总计(㎡)/耕地(㎡)/…」两种表头，
                        #    按原文比对判不出重复 → 两张都渲染进报告（实测 2026-10-28：
                        #    表6-1 出了两张，第二张列还错位）。剥掉括注 + 带上行数再比。
                        _hs = [re.sub(r'[（(][^）)]*[）)]', '', str(h)).strip()
                               for h in (tbl.get('headers') or [])[:5]]
                        if not any(_hs):
                            continue
                        sig = '|'.join(_hs) + f"#{len(tbl.get('rows') or [])}"
                        if sig not in seen:
                            seen.add(sig)
                            all_tables.append(tbl)

        # Map to chapters based on content
        # 🔴 本报告决策基本信息在第1章 → 勘测定界两张表（地块汇总 + 土地分类面积）实为一张，
        #    统一为第1章「表1-1 土地分类面积表」（含合计行）；地块汇总表不再单独出。
        _land_summary = []
        _land_classification = []
        for tbl in all_tables:
            if not isinstance(tbl, dict):
                continue
            headers = tbl.get("headers", [])
            hstr = " ".join(str(h) for h in headers).lower() if headers else ""

            if all(kw in hstr for kw in ['地块', '坐落', '界址', '用途', '面积']):
                tbl["kind"] = "land_summary"
                tbl.setdefault("_rendered_caption", "表6-1 勘测定界地块情况汇总表")
                _land_summary.append(tbl)
            elif any(kw in hstr for kw in ['耕地', '园地', '林地', '权属', '分类面积', '被征']):
                tbl["kind"] = "land_classification"
                tbl.setdefault("_rendered_caption", "表6-1 土地分类面积表")
                _land_classification.append(tbl)
            elif any(kw in hstr for kw in ['调查', '问卷', '支持', '反对']):
                result.setdefault(9, []).append(tbl)   # 第9章 9.1 问卷调查情况

        # 🔴 勘测定界数据属于**第6章「项目基本情况」6.1 项目概况 → 表6-1**，
        #    两张勘测定界表实为一张：优先出「土地分类面积表」，无则用「地块汇总表」兜底。
        if _land_classification:
            result.setdefault(6, []).extend(_land_classification)
        elif _land_summary:
            result.setdefault(6, []).extend(_land_summary)

        return result

    def _is_extracted_land_table(self, caption, ch_num: int, tables: list) -> bool:
        """判断一个 markdown 表题是否为「由 PDF 提取版渲染的勘测定界表」。

        表3-1（土地分类面积表）由 _render_extracted_tables 从 PDF 渲染到**第3章**；
        若该章存在对应的提取表（kind=land_summary/land_classification），
        则拦截 LLM 手写的重复 markdown 表，避免双写。
        """
        if not tables:
            return False
        _has_land = any(t.get("kind") in ("land_summary", "land_classification") for t in tables)
        cap = str(caption or "").strip()
        # 若表题明确是其他表（表2-1、表3-1 等），绝不因该章有勘测定界提取表而拦截
        if re.match(r'^表\s*\d+[-—]\d+', cap) and not _has_land:
            return False
        # 勘测定界数据只属第6章「项目基本情况」：仅第6章拦截重复表
        return ch_num == 6 and _has_land

    def _fill_extracted_total_row(self, tbl: dict):
        """补全提取表末尾「合计」行的空数值格（按前面数据行逐列求和）。

        表1-1（地块汇总）的合计行常为全空，需算出：总面积326342、界址点数221 等。
        若已有数值则保留；表4-1 的合计行本身已正确，不受影响。
        """
        rows = tbl.get("rows", [])
        if not rows:
            return
        if not str(rows[-1][0]).strip().startswith("合计"):
            return
        total_row = [str(c) for c in rows[-1]]
        data_rows = rows[:-1]
        for ci in range(1, len(total_row)):
            cell = str(total_row[ci]).strip()
            if cell and cell not in ("", "【待补充】"):
                continue  # 已有值（如表4-1 合计）保留
            vals = []
            for r in data_rows:
                v = str(r[ci]).strip() if ci < len(r) else ""
                vc = v.replace(",", "").replace("，", "")
                try:
                    if vc:
                        vals.append(float(vc))
                except ValueError:
                    pass
            if vals:
                s = sum(vals)
                total_row[ci] = str(int(s)) if float(s).is_integer() else f"{round(s, 2):.2f}"
        tbl["total_row"] = total_row
        # 🔴 从 rows 移除该「合计」行，避免与 total_row 重复渲染成两个合计行
        tbl["rows"] = data_rows

    def _render_extracted_tables(self, doc, ch_num: int, tables: list) -> bool:
        """Render PDF-extracted tables directly as DOCX tables. Returns True if any rendered."""
        if not tables:
            return False
        for tbl in tables:
            if not isinstance(tbl, dict):
                continue

            # 🔴 已就地渲染过的提取表跳过（防止 LLM 多处引用表1-1/4-1 导致重复）
            try:
                if id(tbl) in getattr(self, '_rendered_extracted_tbls', set()):
                    continue
            except Exception:
                pass

            # 🔴 补全提取表末尾「合计」行：空数值格按列求和（表1-1 的合计=326342等）
            try:
                self._fill_extracted_total_row(tbl)
            except Exception:
                pass

            # 🔴 勘测定界表（表1-1/表4-1）走统一 ReportTable 渲染底座
            kind = tbl.get("kind", "")
            if kind in ("land_summary", "land_classification"):
                try:
                    from app.services.report_table_model import ReportTable, render_report_table
                    # 🔴 勘测定界表属**第6章「项目基本情况」6.1 项目概况** → 表6-1
                    caption_map = {
                        "land_summary": ("表6-1", "项目用地基本情况表", 6),
                        "land_classification": ("表6-1", "土地分类面积表", 6),
                    }
                    table_no, cap_name, sec = caption_map[kind]
                    rt = ReportTable.from_extracted(
                        table_no, cap_name, sec,
                        {"headers": tbl.get("headers", []), "rows": tbl.get("rows", []),
                         "total_row": tbl.get("total_row")},
                    )
                    render_report_table(doc, rt)
                    self._rendered_extracted_tbls.add(id(tbl))
                    continue
                except Exception as e:
                    print(f"    ⚠️ render_report_table failed for {kind}: {e}")
                    # 降级：继续走下面的通用渲染

            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            if not headers or not rows:
                continue

            # 🔴 补齐表头单位量纲：如「总面积()」→「总面积(㎡)」「面积（）」→ 补 (㎡)
            headers = [
                (re.sub(r'([（(])\s*([)）])$', r'\1㎡\2', str(h))
                 if re.search(r'面积\s*[（(]\s*[)）]', str(h)) else str(h))
                for h in headers
            ]

            # Filter to reasonable column count
            if len(headers) > 15:
                # Too many columns — narrow to first 10 meaningful headers
                meaningful = [h for h in headers if h.strip() and len(h.strip()) > 1]
                headers = meaningful[:10]
                rows = [[r[i] if i < len(r) else '' for i in range(len(headers))] for r in rows]

            ncols = len(headers)
            nrows = len(rows) + 1  # +1 for header

            # 🔴 表题放表格正上方（自定义表题优先，如「表1-1 拟征收土地基本情况表」）
            caption = tbl.get("_rendered_caption", "")
            if caption:
                self._add_table_caption(doc, caption)
            # 记签名：模型若在「注：…」之后又手写一张同表头同数据、**不带表题**的表，
            # `_render_md_table` 会据此判重丢弃（实测 2026-10-28 表6-1 重复且列错位）。
            try:
                self._remember_table_sig(headers, nrows)
            except Exception:
                pass

            table = doc.add_table(rows=nrows, cols=ncols, style='Table Grid')
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            self._apply_tpl_table_style(table)

            # Header row
            _tf, _ts = self._table_typo_for(
                getattr(self, "_current_chapter", 0), caption, ncols)
            _hbold = self._table_bold_for(
                getattr(self, "_current_chapter", 0), caption)
            for ci, h in enumerate(headers):
                cell = table.cell(0, ci)
                cell.paragraphs[0].clear()
                r = cell.paragraphs[0].add_run(str(h).replace('\n', ' '))
                r.font.name = _tf
                r._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
                r.font.size = _ts
                r.bold = _hbold
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

            # Data rows
            for ri, row in enumerate(rows):
                for ci in range(ncols):
                    val = row[ci] if ci < len(row) else ''
                    cell = table.cell(ri + 1, ci)
                    cell.paragraphs[0].clear()
                    r = cell.paragraphs[0].add_run(str(val).replace('\n', ' '))
                    r.font.name = _tf
                    r._element.rPr.rFonts.set(qn('w:eastAsia'), _tf)
                    r.font.size = _ts
                    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

            self._fit_wide_table(table, headers, rows)
            doc.add_paragraph()
        return True

    @staticmethod
    def _remove_markdown_tables(markdown: str) -> str:
        """Strip markdown table blocks (already rendered directly from PDF)."""
        if not isinstance(markdown, str):
            return str(markdown) if markdown else ""
        lines = markdown.split('\n')
        result = []
        skip = False
        for line in lines:
            s = line.strip()
            if s.startswith('|') and s.endswith('|'):
                skip = True
                continue
            if skip and not (s.startswith('|') and s.endswith('|')):
                skip = False
            if not skip:
                result.append(line)
        return '\n'.join(result)

    # ═══════════════════════════════════════════════════════════════
    # Images
    # ═══════════════════════════════════════════════════════════════

    def _resolve_image_path(self, image_ref):
        from pathlib import Path as _Path
        p = _Path(image_ref)
        if p.is_absolute():
            return p
        # Remove leading 'storage/' if present (relative to storage_dir)
        ref_str = str(image_ref)
        if ref_str.startswith('storage/'):
            return self.storage_dir / ref_str[8:]  # strip 'storage/'
        # Try images_dir, then storage_dir
        for d in [self.images_dir, self.storage_dir]:
            candidate = d / _Path(image_ref).name
            if candidate.exists():
                return candidate
        return self.storage_dir / image_ref

    @staticmethod
    def _clean_image_caption(caption: str, img_path) -> str:
        """Clean up image caption. If it's a raw filename, extract a readable label."""
        import re as _re_cap
        fname = str(img_path.name) if hasattr(img_path, 'name') else str(img_path).split('/')[-1]
        # If caption already looks like a proper label (图X-X ...), keep it
        if _re_cap.match(r'图\s*\d+[-—]\d+\s+\S', caption):
            return caption
        if caption.startswith('附图') or caption.startswith('图') and len(caption) > 5:
            return caption
        # If caption is just the filename, extract readable parts
        # If caption is a raw filename, generate a clean label
        if caption == fname or caption.endswith(('.jpg', '.png', '.jpeg', '.JPG', '.PNG', '.JPEG')):
            clean = _re_cap.sub(r'\.(jpg|jpeg|png|PNG|JPG|JPEG)$', '', fname)
            clean = _re_cap.sub(r'^pdf_[^_]+_', '', clean)
            clean = _re_cap.sub(r'_[a-f0-9]{8,}\b', '', clean)
            clean = _re_cap.sub(r'_4\d$', '', clean)
            # Keep meaningful prefixes like "公示照片4_", "专家评审会照片_"
            clean = clean.replace('_', ' ').strip()
            if len(clean) > 3:
                return clean[:50]  # Truncate to reasonable length
        return caption

    # 🔴 按图注关键词匹配图片路径（从已分类的图片目录里找）
    def _match_image_by_caption(self, caption: str):
        """根据图注关键词从图片目录匹配最合适的图片路径。

        图注如"图1-1 拟征收土地位置示意图"→ 匹配 map 类图片
        图注如"图3-1 公示照片"→ 匹配 announcement 类图片
        返回图片路径，无匹配返回 None。
        """
        if not caption:
            return None
        # 图注关键词 → 图片类别
        cat_keywords = {
            'map': ['位置', '示意', '红线', '勘测', '地形', '规划图'],
            'announcement': ['公示', '公告', '张贴', '预公告'],
            'survey': ['问卷', '调查', '签到'],
            'review': ['评审', '意见', '专家'],
            'photo': ['现场', '照片', '走访', '勘察'],
            'meeting': ['座谈', '会议', '开会'],
        }
        target_cat = None
        for cat, kws in cat_keywords.items():
            if any(kw in caption for kw in kws):
                target_cat = cat
                break
        if not target_cat:
            return None

        # 从图片目录里找该类别的第一张未用图片
        catalog_data = getattr(self, '_img_catalog_data', {})
        for img in catalog_data.get("catalog", []):
            if img.get("category") == target_cat:
                p = img.get("path", "")
                if p and str(p) not in self._inserted_images:
                    return p
        return None

    # 🔴 图片尺寸规范（淮安市稳评格式，单位cm）按图注关键词匹配
    IMAGE_SIZE_SPECS = [
        (['决策实施周期'], (13.45, 10.12)),
        (['风险评估流程'], (13.69, 12.45)),
        (['评估公示内容'], (16.48, 11.65)),
        (['决策位置'], (6.73, 11.7)),
        (['位置示意图', '位置图', '征收示意图', '征地范围图', '红线图', '勘测定界图'], (10.91, 15.5)),
        (['决策网络舆情'], (4.6, 4.41)),
        (['单位调查问卷', '公众调查问卷'], (10.39, 7.34)),
        (['签到表', '座谈会', '会议照片', '走访照片'], (5.43, 7.23)),
        (['现场照片', '勘查照片', '踏勘照片'], (5.44, 7.25)),
        (['公示照片', '公告照片', '张贴照片'], (5.39, 7.18)),
        (['会议纪要'], (20.51, 14.95)),
        (['调查问卷', '问卷调查', '问卷'], (21.84, 15.45)),
        (['公告', '预公告', '征收公告'], (21.35, 14.84)),
        (['专家评审', '评审意见'], (10.91, 15.5)),
    ]

    def _match_image_size(self, caption: str):
        """按图注关键词匹配图片尺寸（cm），无匹配返回 None。

        图注格式可能是「位置示意图：位置图11」或「问卷调查及签到表：签到表」，
        取冒号前的关键词部分做匹配（冒号后是文件名/编号，干扰匹配）。

        🔴 结果一律按版心宽度收口：原表里有 21.84cm（问卷）、21.35cm（公告）、
        20.51cm（会议纪要）等**超过版心 14.66cm** 的值，直接使用会溢出页面。
        超宽时按等比缩到 MAX_IMAGE_W_CM，不改变长宽比。
        """
        if not caption:
            return None
        # 🔴 取冒号前的部分（图注类型），去掉冒号后的文件名干扰
        clean = caption.split('：')[0].split(':')[0].strip()
        # 用完整图注 + 冒号前部分 双重匹配，提高命中率
        for keywords, size in self.IMAGE_SIZE_SPECS:
            if any(kw in clean for kw in keywords) or any(kw in caption for kw in keywords):
                w, h = size
                if w > MAX_IMAGE_W_CM and w > 0:
                    h = h * MAX_IMAGE_W_CM / w
                    w = MAX_IMAGE_W_CM
                return (w, h)
        return None

    @staticmethod
    def _image_fingerprint(path) -> str:
        """图片内容指纹：同内容不同路径判为重复。"""
        try:
            h = hashlib.md5()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    def _img_already_used(self, img) -> bool:
        """这张图（图库条目）是不是**已经在文档里插过了**（按内容指纹判）。

        🔴 附件分派前必须先过这一道：同一张图既可能由正文配额选中、又留在附件池里，
           只比路径字符串挡不住（正文用解析后的绝对路径、附件池用的是上传时的原路径）。
        """
        if not isinstance(img, dict):
            return str(img) in self._inserted_images
        p = img.get("path", "")
        if not p:
            return True
        if str(p) in self._inserted_images:
            return True
        fp = self._image_fingerprint(p) or str(img.get("fingerprint") or "")
        return bool(fp) and fp in self._inserted_images

    @staticmethod
    def _flatten_to_rgb(img):
        """把图片压平成 RGB；**透明背景合成到白底**（避免 RGBA→RGB 把透明处变黑）。"""
        from PIL import Image as _PIL
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            bg = _PIL.new('RGB', img.size, (255, 255, 255))
            rgba = img.convert('RGBA')
            bg.paste(rgba, mask=rgba.split()[-1])
            return bg
        if img.mode != 'RGB':
            return img.convert('RGB')
        return img

    def _add_image(self, doc, image_ref, caption, max_width=Inches(5.5), max_height=Inches(7.0),
                   numbered: bool = True):
        """插入单张图片 + 图注。

        🔴 `numbered=False`：图下**不写「图X-N」图号**（附件用）。
           人工成品案卷的附件是按材料类型分节、图下不加图号的；正文才编图号。
        """
        img_path = self._resolve_image_path(image_ref)
        # 🔴 内容指纹去重：同图（不同路径写法）只插一次，根除重复
        fp = self._image_fingerprint(img_path)
        if fp and fp in self._inserted_images:
            return
        img_key = str(img_path)
        if not fp:
            if img_key in self._inserted_images:
                return
        # 🔴 记录图片放置信息（供审核 agent 观察图片位置/命名）
        try:
            if hasattr(self, '_image_placements'):
                ch = getattr(self, '_current_chapter', 0)
                self._image_placements.setdefault(ch, []).append({
                    "path": str(img_path),
                    "caption": (caption or "")[:60],
                    "fname": os.path.basename(str(img_path))[:60],
                })
        except Exception:
            pass
        if fp:
            self._inserted_images.add(fp)
        self._inserted_images.add(img_key)

        if not img_path.exists():
            for d in [self.images_dir, self.storage_dir]:
                fallback = d / Path(image_ref).name
                if fallback.exists():
                    img_path = fallback
                    break
        if not img_path.exists():
            # 🔴 资料里少一张图也要能出报告：正文**不写**「【图片待插入：…】」
            #    这种占位符（业主硬约束：交付稿不许有占位）。缺图记进
            #    `_missing_images`，由缺口清单提示，正文不引用。
            print(f"[IMG_FAIL] _add_image failed: ref={image_ref}, caption={caption}",
                  flush=True, file=__import__('sys').stderr)
            try:
                self._missing_images.append({
                    "ref": str(image_ref), "caption": str(caption or ""),
                    "chapter": int(getattr(self, "_current_chapter", 0) or 0)})
            except Exception:
                pass
            return

        # 🔴 图注规范化 + 图号统一（治「图号跳号/重复」）：
        #   忽略 LLM 手写的图号，按章内出现顺序由系统自动重编号「图X-N」。
        clean_caption = self._clean_image_caption(caption, img_path)
        ch = getattr(self, '_current_chapter', 0)
        counter = self._chapter_image_counter.setdefault(ch, 0) + 1
        self._chapter_image_counter[ch] = counter
        # 去掉 caption 里已有的「图X-N」前缀（可能跳号），只保留描述文字
        desc = re.sub(r'^图\s*\d+[-—]\d+\s*', '', clean_caption).strip()
        # 若描述里还带着「图X-N」但没在开头，也去掉
        desc = re.sub(r'\b图\s*\d+[-—]\d+\b', '', desc).strip()
        # 🔴 前置资质页（封面之后、正文之前，章号 0）**不编图号**：
        #    营业执照 / 资质证书 / 人员证书 不是任何一章的插图，编出来就是
        #    「图0-1 公司营业执照」这种交付稿不能看的编号。这些页只留名称。
        if ch == 0 or not numbered:
            clean_caption = desc
        else:
            clean_caption = f"图{ch}-{counter} {desc}".strip() if desc else f"图{ch}-{counter}"

        # 🔴 Image above caption format: "图 X-X 描述"
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.paragraph_format.space_after = Pt(0)
        p_img.paragraph_format.space_before = Pt(6)
        try:
            from PIL import Image as PILImage
            import tempfile as _tmp
            with PILImage.open(str(img_path)) as img:
                orig_w, orig_h = img.size
                img = self._flatten_to_rgb(img)
                tmp_fd, tmp_path = _tmp.mkstemp(suffix='.jpg')
                os.close(tmp_fd)
                # 🔴 Always compress: max 1600px, JPEG quality 80, strip EXIF
                max_dim = 1600
                if orig_w > max_dim or orig_h > max_dim:
                    ratio = min(max_dim/orig_w, max_dim/orig_h)
                    img = img.resize((int(orig_w*ratio), int(orig_h*ratio)), PILImage.LANCZOS)
                elif img.mode in ('RGBA', 'P', 'CMYK', 'LA') or img_path.suffix.lower() in ('.png','.bmp','.tiff','.tif'):
                    pass  # Still need to convert format
                img.save(tmp_path, 'JPEG', quality=80, optimize=True)
                use_path = tmp_path

            aspect = orig_w / orig_h if orig_h > 0 else 1.0
            # 🔴 复垦**正文**插图一律按「横版小图」收口（宽 ≤12.5cm、高 ≤9.5cm）。
            #    业主 2026-10-26：「3-1 的项目地理位置示意图 尺寸不要太大，而且
            #    还是竖向排列的，放在文章中不合适」。两处病根：
            #      ① `IMAGE_SIZE_SPECS` 是**稳评案卷**的尺寸表（位置示意图 10.91×15.5
            #         竖版），套到复垦正文上就是一大张竖图 —— 复垦不查这张表；
            #      ② 竖版源图按宽度铺满版心后高度超一页。
            #    附件页（`numbered=False`，整页扫描件）不受此限，仍按版心宽度。
            _body_fig = bool(numbered) and self._domain == "reclamation"
            spec_size = None if _body_fig else self._match_image_size(clean_caption)
            if spec_size:
                target_w = Cm(spec_size[0])
                target_h = Cm(spec_size[1])
            else:
                # 参照件：正文/附件单张图宽 15cm（版心 14.66cm，故取 14.6cm 收口）
                _cap_w = Cm(12.5) if _body_fig else min(max_width, Cm(MAX_IMAGE_W_CM))
                _cap_h = Cm(9.5) if _body_fig else max_height
                target_w = _cap_w
                target_h = target_w / aspect
                if target_h > _cap_h:
                    target_h = _cap_h
                    target_w = target_h * aspect

            # 🔴 图片与图注必须同页：图片顶到页底时，图注会被挤到下一页，
            #    读者看到「上一页一张图、下一页一行图名」（2026-11-12 实测）。
            try:
                p_img.paragraph_format.keep_with_next = True
                p_img.paragraph_format.keep_together = True
            except Exception:
                pass
            run_img = p_img.add_run()
            run_img.add_picture(use_path, width=target_w, height=target_h)
            try:
                os.unlink(use_path)
            except:
                pass
        except Exception as e:
            logger.warning(f"Failed to add image {image_ref}: {e}")
            return

        # 🔴 图名: 宋体 小四(12pt) 居中，位于图片下方居中
        #    `numbered=False` 且无图注文字时不写图注段（附件：类型由小节标题给出）
        if clean_caption:
            p_cap = doc.add_paragraph()
            p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_cap.paragraph_format.space_before = Pt(2)
            p_cap.paragraph_format.space_after = Pt(10)
            run_cap = p_cap.add_run(clean_caption)
            run_cap.font.name = FONT_CAPTION
            run_cap._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_CAPTION)
            run_cap.font.size = Pt(12)  # 小四
            run_cap.italic = False      # 不斜体

    # ═══════════════════════════════════════════════════════════════
    # Company Front Matter (开篇)
    # ═══════════════════════════════════════════════════════════════

    def _add_company_qualifications(self, doc, filled, domain="stability"):
        """Page 2: 公司资质证书图片 + 用户勾选人员的培训证书. Returns True if content was added."""
        import hashlib
        cert_images = self._load_company_certificate_images(domain=domain)
        # 🔴 前置「培训证书」页：用户勾选人员的证书扫描件（按人名匹配 + 视觉识别索引）
        person_certs = self._selected_personnel_cert_images()
        if not cert_images and not person_certs:
            # 🔴 外部公司没有任何资质图时**不能静默跳过** ——
            #    业主口径是「没有就留空显示待填写」，好让他知道该去补。
            #    内部公司维持原行为（确实没图就不出这一节）。
            if self._company and not self._company.get("is_internal"):
                self._add_heading(doc, '公司资质证书', 2)
                self._add_para(doc, '（营业执照、资质证书：待填写 —— 请在公司信息页上传）', indent=True)
                return True
            return False

        def _h(p: str) -> str:
            try:
                return hashlib.md5(open(p, "rb").read()).hexdigest()
            except Exception:
                return ""

        # 人员证书已单独成节，公司资质图里同一张（stability_cert_02/03/04 是它们的副本）不再重复插
        _pc_hashes = {_h(p) for p, _ in person_certs}
        rest = [(p, c) for p, c in cert_images if _h(p) not in _pc_hashes]

        self._add_heading(doc, '公司资质证书', 2)
        for img_path, caption in rest[:6]:
            self._add_image(doc, img_path, caption)
        if person_certs:
            self._add_heading(doc, front_matter_labels(domain)["cert_section_heading"], 2)
            for img_path, caption in person_certs[:8]:
                self._add_image(doc, img_path, caption)
        return True

    def _render_personnel_roster(self, doc) -> None:
        """第7章「成立稳评小组」成员名单 —— 由**界面勾选的人员**驱动。

        🔴 参照件里那份名单是**别项目的班组**（还带真实姓名），照搬即张冠李戴。
        🔴 不写分工：人员库的「拟任角色」目前是统一默认值（项目组成员），
           硬套「稳评负责人 / 调研 / 数据汇总」之类就是编造。只列姓名。
        """
        try:
            # 与前置工作组名单/签字区同源（未勾选时同一份随机 5 名项目组成员）
            people = self._load_company_personnel()
            names = []
            for _p in people:
                _n = str(_p.get("姓名") or "").strip()
                if _n and _n not in names:
                    names.append(_n)
            if not names:
                return
            self._add_para(doc, "成员如下：" + "、".join(names) + "。", indent=True)
        except Exception as _e:
            logger.warning(f"稳评小组名单渲染失败: {_e}")

    def _render_personnel_signature_table(self, doc, ch_num: int) -> None:
        """渲染「（五）参与人员签字」块。

        🔴 版式照知识库模板（洪泽案卷）——**不是表格**，是段落行：
              编写单位：×××
              编写时间：××××年××月
              姓名（职务）：[手写签名图]
           无边框、无表头行、统一小四(12pt)。此前做成 4 列表格是错的
           （业主反馈：不要边框、不要「姓名/分工」表头行、字号不对）。

        人员 = **前置「稳评工作组人员及分工情况」同一份名单**
        （`_load_company_personnel()`：人员库按公司过滤，勾选了就按勾选）；
        签名 = 该人的手写签名图，没有就用「(此处签名)」占位图。
        """
        # 🔴 名单必须与前置工作组名单同源：两处各查各的就会出现
        #    「前面写项目组成员是甲、乙，签字却是丙、丁」（业主 2026-11-16 实测）。
        people = self._load_company_personnel()
        if not people:
            try:
                from app.services.personnel_store import personnel_for
                people = personnel_for(getattr(self, "_selected_personnel", None) or None,
                                       self._company_id())
            except Exception as e:
                logger.warning(f"签字块取人员失败: {e}")
                return
        if not people:
            return

        _fd = {}
        try:
            _fd = (getattr(self, "_session_state", None) or {}).get("filled_data") or {}
        except Exception:
            pass

        self._add_para(doc, f"编写单位：{self._cv('implement_unit')}", indent=False, size=Pt(12))
        _ym = str(_fd.get("report_month") or "").strip()
        if not _ym:
            from datetime import datetime as _dt
            _ym = f"{_dt.now().year}-{_dt.now().month:02d}"
        try:
            _y, _m = _ym.split("-")[:2]
            _ym_txt = f"{int(_y)}年{int(_m)}月"
        except Exception:
            _ym_txt = _ym
        self._add_para(doc, f"编写时间：{_ym_txt}", indent=False, size=Pt(12))
        doc.add_paragraph()

        # 🔴 签名：有真签名用真签名，没有就放**占位图**（业主口径）。
        from app.services.personnel_signature import signature_for, signature_or_placeholder
        from docx.shared import Cm as _Cm
        for _i, p in enumerate(people):
            name = str(p.get("姓名") or "").strip()
            if not name:
                continue
            title = str(p.get("职称/职务") or "").strip()
            para = doc.add_paragraph()
            para.paragraph_format.space_after = Pt(4)
            r = para.add_run(f"{name}（{title}）：" if title else f"{name}：")
            _set_run_font(r, FONT_BODY, size=Pt(12), ascii_font=FONT_ASCII)
            real = signature_for(name)
            sig = real or signature_or_placeholder(name)
            if sig:
                try:
                    para.add_run().add_picture(sig, height=_Cm(0.85))
                except Exception as e:
                    logger.warning(f"签名图插入失败 {name}: {e}")
            # 🔴 打上「签名行」书签：全局图片去重（`dedupe_duplicate_images`）会把
            #    相同的「(此处签名)」占位图当成重复插图**整段删掉** —— 实测只留下
            #    第一个占位的人，其余 15 人从签字区消失。带这个标记的段落一律跳过去重。
            _mark_signature_paragraph(para, _i)


    def _load_company_personnel(self) -> list:
        """从**人员库**（company_personnel 表）读取人员信息。

        `_selected_personnel` 非空时只返回用户勾选的人，并保持勾选顺序；
        未勾选任何人则**随机抽 5 人**（业主 2026-11-23：「随机选择五个就行，
        不要所有的都放上面」），技术负责人优先保留。整份报告共用这一份名单
        （工作组名单 / 第7章名单 / 签字区 / 证书页），避免各处人数不一致。
        库为空时返回 []，由调用方回退到内置默认名单。
        """
        cached = getattr(self, "_team_people", None)
        if cached is not None:
            return cached
        sel = getattr(self, "_selected_personnel", None) or []
        try:
            from app.services.personnel_store import personnel_for
            people = personnel_for(sel or None, self._company_id())
        except Exception as e:
            logger.warning(f"读取人员库失败: {e}")
            return []
        if not sel and people:
            people = self._sample_team(people, 5, self._names_with_certs())
            logger.info("[人员] 未勾选，随机取 5 名项目组成员：%s",
                        [str(p.get("姓名") or "") for p in people
                         if isinstance(p, dict)])
        self._team_people = people
        return people

    @classmethod
    def _sample_team(cls, people: list, n: int = 5,
                     certified: set = None) -> list:
        """保留技术负责人，其余**随机取 n 名项目组成员**。

        优先从「有证书扫描件」的人里抽（业主 2026-11-23：成员证书要能对应上），
        不够再补没有扫描件的；返回顺序沿用人员库顺序。
        """
        import random
        people = [p for p in people if isinstance(p, dict)]
        leads = [p for p in people
                 if str(p.get("拟任角色") or "").strip() == "技术负责人"]
        keep = leads[:1]
        keep_ids = {id(p) for p in keep}
        rest = [p for p in people if id(p) not in keep_ids]
        need = max(0, n)
        if len(rest) <= need and not certified:
            chosen = keep_ids | {id(p) for p in rest}
            return [p for p in people if id(p) in chosen]
        pref = [p for p in rest
                if certified and str(p.get("姓名") or "").strip() in certified]
        pref_ids = {id(p) for p in pref}
        others = [p for p in rest if id(p) not in pref_ids]
        picked = random.sample(pref, min(need, len(pref)))
        if len(picked) < need and others:
            picked += random.sample(others, min(need - len(picked), len(others)))
        chosen = keep_ids | {id(p) for p in picked}
        return [p for p in people if id(p) in chosen]

    def _names_with_certs(self) -> set:
        """有证书扫描件的人员姓名：人员库 + 模板提取的证书索引。"""
        cached = getattr(self, "_cert_names", None)
        if cached is not None:
            return cached
        names = set()
        try:
            from app.services.personnel_store import person_images
            for _n, *_rest in person_images(None, self._company_id()):
                if _n:
                    names.add(str(_n).strip())
        except Exception:
            pass
        try:
            from app.services.personnel_certs import load_index
            for _info in (load_index() or {}).values():
                if isinstance(_info, dict) and _info.get("is_person_cert"):
                    _nm = str(_info.get("person_name") or "").strip()
                    if _nm:
                        names.add(_nm)
        except Exception:
            pass
        self._cert_names = names
        return names

    def _selected_personnel_cert_images(self) -> List[tuple]:
        """证书页要贴的人员证书扫描件 → [(文件路径, 图题)]。

        名单来源（业主口径）：
          ① 界面单独勾选的「证书人员」（`_selected_cert_personnel`，最多 3 人）；
          ② 没勾证书人员时 = **工作组同一份 5 人名单**（`_load_company_personnel`）。

        取图顺序：
          ① 人员库 `company_personnel_images`（人名 → 证书图，纯查表）；
          ② 库里没有的，从**模板提取阶段建立的证书索引**
             （`personnel_certs.json`，一次性视觉识别把「手写姓名」绑到图上）里
             按姓名找 —— 这就是「缺少的话从模板中提取」。
        """
        cert_sel = [str(x).strip() for x in
                    (getattr(self, "_selected_cert_personnel", None) or [])
                    if str(x).strip()]
        team = self._load_company_personnel() or []
        team_names = [str(p.get("姓名") or "").strip()
                      for p in team if isinstance(p, dict)]
        sel = cert_sel or [n for n in team_names if n]
        if not sel:
            return []

        triples = []
        try:
            from app.services.personnel_store import person_images
            triples = person_images(sel, self._company_id())
        except Exception as e:
            logger.warning(f"读取人员证书图失败: {e}")

        covered = {n for n, *_ in triples}
        missing = [n for n in sel if n not in covered]
        if missing:
            for _n, _p, _cat in self._template_cert_images(missing):
                triples.append((_n, _p, Path(_p).name, _cat))

        seen, out = set(), []
        for name, path, _img, cat in triples:
            if path in seen:
                continue
            seen.add(path)
            _cap = (f"{name}·{cat}" if cat and cat != "其他" else f"{name} 证书")
            out.append((path, _cap[:60]))

        _still = [n for n in missing if not any(t[0] == n for t in triples)]
        if _still:
            logger.warning(f"[人员证书] 库和模板索引里都没有这些人的证书：{_still}")
        return out

    def _template_cert_images(self, names: List[str]) -> List[tuple]:
        """按姓名从**模板提取的证书索引**里取图 → [(姓名, 路径, 类别)]。

        `storage/personnel_certs.json` 是模板/公司素材做一次视觉识别后的落盘索引
        （手写姓名 → 图片路径）；这里只做纯查表，不再调模型。同一个人只取一张，
        已用过的不重复取。
        """
        out = []
        try:
            from app.services.personnel_certs import load_index
            idx = load_index() or {}
        except Exception as e:
            logger.warning(f"读取模板证书索引失败: {e}")
            return out
        by_name: Dict[str, list] = {}
        for _k, info in idx.items():
            if not isinstance(info, dict) or not info.get("is_person_cert"):
                continue
            nm = str(info.get("person_name") or "").strip()
            fp = str(info.get("path") or _k)
            if not nm or not Path(fp).exists():
                continue
            by_name.setdefault(nm, []).append(
                (fp, str(info.get("cert_type") or "培训证书")))
        used = getattr(self, "_used_template_certs", None)
        if used is None:
            used = self._used_template_certs = set()
        for nm in names:
            for fp, cat in by_name.get(nm, []):
                if fp in used:
                    continue
                used.add(fp)
                out.append((nm, fp, cat))
                break
        return out

    def _add_compilation_note(self, doc, filled):
        """编制说明：标题 宋体小三加黑居中；内容 仿宋四号首行缩进2格。"""
        self._add_heading(doc, '编制说明', 1)
        _fd = filled if isinstance(filled, dict) else {}
        _org = _fd.get('org_name') or '有关责任单位'
        _impl = _fd.get('implement_unit') or self._cv('implement_unit')
        _name = _fd.get('project_name') or _fd.get('doc_reference') or '本项目'
        for _t in [
            f'受{_org}委托，{_impl}承担了{_name}社会稳定风险评估工作。本报告依据国家及江苏省有关土地管理、'
            '征收补偿和社会稳定风险评估的法律法规、规章及技术规范，在收集资料、现场踏勘、问卷调查、'
            '座谈走访和风险分析研判的基础上编制完成。',
            '本报告正文共十四章。报告中的评估结论基于评估时点所掌握的情况作出，仅对本次评估事项负责。'
            '征收实施过程中，如征收范围、补偿安置方案等发生重大调整，应重新组织社会稳定风险评估。',
            '本报告相关机构营业执照、项目人员职称及稳评培训证书等材料详见附件。',
        ]:
            self._add_para(doc, _t, indent=True)

    # 缓存：从人工模板里抠出来的「目录 SDT」结构（title 段 + TOC 域 + 域结束段）
    _TOC_SDT_CACHE: Optional[object] = None
    _TOC_SDT_SRC: str = ""

    @classmethod
    def _load_toc_sdt(cls, template_path: Optional[str] = None):
        """从人工模板 docx 里取出「目录」内容控件（SDT）。

        人工模板的目录是 Word 自己生成的：一个 SDT 包着 `TOC \\o "1-2" \\h \\u` 域，
        域结果 = [TOC Heading 段「目  录」] + [各条目段] + [域结束段]。
        我们要的是**这套结构和版式**（字体/前导点/制表位），条目内容交给 Word 刷新生成。
        """
        import os as _os
        from copy import deepcopy
        from app.config import settings as _st

        cands = []
        if template_path and _os.path.exists(str(template_path)):
            cands.append(str(template_path))
        # 兜底：storage/templates 下的候选，**稳评模板优先**（按名字排序时「投标文件」
        # 这类大文件会排在前面，它的内容控件里恰好含 "TOC " 字样但不是目录域）
        _tdir = _st.STORAGE_DIR / "templates"
        if _tdir.exists():
            _all = [str(p) for p in sorted(_tdir.glob("*.docx"))]

            def _rank(p: str) -> int:
                n = _os.path.basename(p)
                if "洪泽" in n or n.startswith("598da27a"):
                    return 0
                if "金湖" in n or "洞庭" in n:
                    return 1
                return 2
            cands += sorted(_all, key=_rank)

        for _p in cands:
            if _p == cls._TOC_SDT_SRC and cls._TOC_SDT_CACHE is not None:
                return deepcopy(cls._TOC_SDT_CACHE)
            try:
                from docx import Document as _Doc
                _tpl = _Doc(_p)
                for _ch in _tpl.element.body.iterchildren():
                    if _ch.tag.split('}')[1] != 'sdt':
                        continue
                    _xml = etree.tostring(_ch, encoding='unicode')
                    # 🔴 必须是**真正的目录域**（TOC \o "1-2" …），
                    #    不能只匹配 'TOC ' 字样——投标文件等文档里也有该字串
                    if 'TOC \\o' not in _xml:
                        continue
                    cls._TOC_SDT_CACHE = deepcopy(_ch)
                    cls._TOC_SDT_SRC = _p
                    logger.info(f"[TOC] 采用模板目录域结构：{_os.path.basename(_p)}")
                    return deepcopy(_ch)
            except Exception as _e:
                logger.debug(f"读取模板目录失败 {_p}: {_e}")
        return None

    @staticmethod
    def _toc_is_title(p, qn) -> bool:
        t = ''.join(x.text or '' for x in p.iter(qn('w:t')))
        t = re.sub(r'[\s　​]+', '', t)
        return t in ('目录',)

    @staticmethod
    def _toc_is_field_para(p, qn) -> bool:
        return (p.find('.//' + qn('w:fldChar')) is not None
                or p.find('.//' + qn('w:instrText')) is not None)

    @staticmethod
    def _toc_has_fld_end(p, qn) -> bool:
        return any(fc.get(qn('w:fldCharType')) == 'end'
                   for fc in p.iter(qn('w:fldChar')))

    @staticmethod
    def _toc_strip_entry_runs(p, qn) -> None:
        """删掉段落里的**文字 run**，保留域管道（fldChar/instrText）。"""
        for r in list(p.findall(qn('w:r'))):
            if (r.find('.//' + qn('w:fldChar')) is None
                    and r.find('.//' + qn('w:instrText')) is None):
                p.remove(r)

    def _add_toc(self, doc, chapters, template_path: Optional[str] = None):
        """插入**真正的 Word 目录域**——直接用 docx 的目录功能生成，不自己写条目。

        做法（与人工模板一致）：把模板里的「目录 SDT」整块搬过来，
        **清空其缓存条目**，只留 [TOC Heading「目  录」] + [域 begin/instrText/separate] + [域 end]，
        再置 `w:dirty=true` + `settings.updateFields=true`。
        → Word 打开时自动重算，条目与页码全部由 Word 生成，字体/前导点/缩进沿用模板。
        """
        from docx.oxml.ns import qn as _qn
        from docx.oxml import OxmlElement

        _sdt = self._load_toc_sdt(template_path)
        if _sdt is not None:
            _contents = [c for c in _sdt.iter(_qn('w:sdtContent'))
                         if c.findall(_qn('w:p'))]
            _content = _contents[-1] if _contents else None
            if _content is not None:
                # 🔴 去掉模板目录自带的**节属性**：保留它会在目录后多出一个分节，
                #    紧接的「目录后分页」就落在一个新节首段 → 整页空白（实测）。
                for _sp in list(_sdt.iter(_qn('w:sectPr'))):
                    _sp.getparent().remove(_sp)
                _ps = list(_content.findall(_qn('w:p')))
                _title = next((p for p in _ps if self._toc_is_title(p, _qn)), None)
                # 🔴 **重建**目录域：只留「目  录」标题段，其余旧条目/旧域全丢。
                #    模板每个条目段里都夹着 fldChar，照搬会留下几十个空段，
                #    且 `ps[1:-1]` 的删法会把标题一起删掉（业主：「目录俩字没了」）。
                for _p in _ps:
                    _content.remove(_p)
                if _title is not None:
                    _content.append(_title)

                def _fld_para(kind: str):
                    from docx.oxml import OxmlElement as _OE
                    _p = _OE('w:p')
                    _r = _OE('w:r')
                    _fc = _OE('w:fldChar')
                    _fc.set(_qn('w:fldCharType'), kind)
                    if kind == 'begin':
                        _fc.set(_qn('w:dirty'), 'true')
                    _r.append(_fc)
                    _p.append(_r)
                    return _p

                from docx.oxml import OxmlElement as _OE
                _head = _OE('w:p')
                _r1 = _OE('w:r')
                _fc = _OE('w:fldChar')
                _fc.set(_qn('w:fldCharType'), 'begin')
                _fc.set(_qn('w:dirty'), 'true')
                _r1.append(_fc)
                _r2 = _OE('w:r')
                _it = _OE('w:instrText')
                _it.set(_qn('xml:space'), 'preserve')
                _it.text = ' TOC \\o "1-2" \\h \\u '
                _r2.append(_it)
                _r3 = _OE('w:r')
                _fc2 = _OE('w:fldChar')
                _fc2.set(_qn('w:fldCharType'), 'separate')
                _r3.append(_fc2)
                for _r in (_r1, _r2, _r3):
                    _head.append(_r)
                _content.append(_head)
                _content.append(_fld_para('end'))
            _body = doc.element.body
            _sect = _body.find(_qn('w:sectPr'))
            if _sect is not None:
                _sect.addprevious(_sdt)
            else:
                _body.append(_sdt)
            self._mark_update_fields(doc)
            return

        # ── 兜底：模板不可用时，自己拼一个空的 TOC 域（依旧交给 Word 生成）──
        logger.warning("[TOC] 未找到带目录域的模板，回退为自建空目录域")
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run('目  录')
        r.font.name = '仿宋'; r._element.rPr.rFonts.set(qn('w:eastAsia'), '仿宋')
        r.font.size = Pt(16); r.bold = True
        doc.add_paragraph()

        head = doc.add_paragraph()
        _r1 = head.add_run()._r
        _fc = OxmlElement('w:fldChar'); _fc.set(_qn('w:fldCharType'), 'begin'); _fc.set(_qn('w:dirty'), 'true')
        _r1.append(_fc)
        _r2 = head.add_run()._r
        _it = OxmlElement('w:instrText'); _it.set(_qn('xml:space'), 'preserve')
        _it.text = ' TOC \\o "1-2" \\h \\u '
        _r2.append(_it)
        _r3 = head.add_run()._r
        _fc2 = OxmlElement('w:fldChar'); _fc2.set(_qn('w:fldCharType'), 'separate')
        _r3.append(_fc2)
        tail = doc.add_paragraph()
        _r4 = tail.add_run()._r
        _fc3 = OxmlElement('w:fldChar'); _fc3.set(_qn('w:fldCharType'), 'end')
        _r4.append(_fc3)
        self._mark_update_fields(doc)

    @staticmethod
    def _mark_update_fields(doc):
        """settings.xml 置 `updateFields=true` → Word 打开时自动重算目录页码。"""
        from docx.oxml.ns import qn as _qn
        from docx.oxml import OxmlElement
        try:
            _settings = doc.settings.element
            for _old in _settings.findall(_qn('w:updateFields')):
                _settings.remove(_old)
            _uf = OxmlElement('w:updateFields'); _uf.set(_qn('w:val'), 'true')
            _settings.insert(0, _uf)
        except Exception as _se:
            logger.warning(f"设置 updateFields 失败: {_se}")


    def _company_id(self):
        """当前报告所属公司 id；没有（外部用户未填）→ None。

        人员/证书一律按它过滤 —— 传 None 会返回空，**不会**退化成「全库所有人」。
        """
        try:
            # `_company` 由 `assemble()` 按次设置；单章导出等入口会绕过它，
            # 所以这里用 getattr 兜底而不是直接取属性（否则 AttributeError）。
            return (getattr(self, "_company", None) or {}).get("id")
        except Exception:
            return None

    def _cv(self, key: str, default: str = None) -> str:
        """取当前报告所属公司的字段；缺失一律「待填写」。

        🔴 所有公司字段都必须走这里 —— 让「绝不套用中拓信息」成为**结构性保证**，
           而不是散在七八处各写各的 `or "江苏众拓…"`（实测就是这么漏出去的）。
        """
        from app.services.company_service import company_value, PLACEHOLDER
        return company_value(getattr(self, "_company", None) or {}, key, default or PLACEHOLDER)

    def _add_company_intro(self, doc, domain: str = "stability"):
        """Page 3: 公司简介 + 工作组人员."""
        # ── 公司简介 ──
        self._add_heading(doc, '公司简介', 2)
        # 🔴 文案来自**本报告所属公司**的档案（`companies.intro_text`），
        #    不再写死在代码里。原先这里硬编码了江苏众拓的四段 —— 外部用户
        #    出报告就会把自己的公司抬头印成我们的。
        #    内部公司（江苏众拓）的那四段由 auth Db 迁移搬进数据库，内容一致。
        from app.services.company_service import PLACEHOLDER, intro_paragraphs

        _segments = intro_paragraphs(self._company)
        if _segments:
            for _seg in _segments:
                self._add_para(doc, _seg, indent=True)
        else:
            # 这个账号还没填公司信息 → 留空待填，**绝不回落成我们的简介**
            self._add_para(doc, f'公司简介：{PLACEHOLDER}', indent=True)

        doc.add_page_break()

        # ── 工作组人员及分工（宋体小三加黑居中）──
        #    🔴 标题按域取（稳评「稳评工作组…」→ 复垦「复垦方案编制人员…」）。
        _fl = front_matter_labels(domain)
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(_fl["roster_heading"])
        r.font.name = FONT_SONGTI; r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_SONGTI)
        r.font.size = Pt(15); r.bold = True
        doc.add_paragraph()
        # 岗位标题: 宋体四号加黑靠左；内容: 宋体四号靠左
        _personnel = self._load_company_personnel()
        if _personnel:
            # 🔴 按「拟任角色」分组渲染（数据来自 company_assets，更新入库即生效）
            _groups = {}
            for _ps in _personnel:
                if not isinstance(_ps, dict):
                    continue
                _name = str(_ps.get('姓名') or '').strip()
                if not _name:
                    continue
                _role = str(_ps.get('拟任角色') or '项目组成员').strip()
                _title = str(_ps.get('职称/职务') or '').strip()
                _desc = f"{_name}（{_title}）" if _title else _name
                _groups.setdefault(_role, []).append(_desc)
            for _role, _members in _groups.items():
                self._add_para(doc, _role, bold=True, indent=False, font=FONT_SONGTI)
                for _m in _members:
                    self._add_para(doc, _m, indent=True, font=FONT_SONGTI)
        else:
            # 回退：内置默认名单（company_assets 人员数据入库后自动切换为上方数据驱动渲染）
            self._add_para(doc, _fl["roster_lead_role"], bold=True, indent=False, font=FONT_SONGTI)
            self._add_para(doc, '陈  春（总经理、高级工程师、估价师、经济师）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '调研工作', bold=True, indent=False, font=FONT_SONGTI)
            self._add_para(doc, '安如月（评估专业人员）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '张抗洪（评估专业人员）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '朱  璇（评估助理）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '风险研判与评估报告编制工作', bold=True, indent=False, font=FONT_SONGTI)
            self._add_para(doc, '程诗茹（技术负责人）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '刘利伟（评估专业人员）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '数据汇总整理', bold=True, indent=False, font=FONT_SONGTI)
            self._add_para(doc, '张抗洪（评估助理）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '罗  娜（评估助理）', indent=True, font=FONT_SONGTI)
            self._add_para(doc, '资料与档案工作', bold=True, indent=False, font=FONT_SONGTI)
            self._add_para(doc, '朱  璇（评估助理）', indent=True, font=FONT_SONGTI)
        self._add_para(doc, _fl["material_note"], indent=True, font=FONT_SONGTI)

        doc.add_page_break()

    def _add_legal_basis(self, doc):
        """Add standalone 法律法规与评估依据 section to the report."""
        self._add_heading(doc, '法律法规与评估依据', 1)
        doc.add_paragraph()

        laws = [
            ("一、国家法律", [
                "《中华人民共和国土地管理法》（2019年8月26日第三次修正，2020年1月1日起施行）",
                "《中华人民共和国突发事件应对法》（2024年6月28日修订，2024年11月1日起施行）",
                "《中华人民共和国城乡规划法》（2019年4月23日第二次修正）",
                "《中华人民共和国农村土地承包法》（2018年12月29日第二次修正）",
                "《中华人民共和国村民委员会组织法》（2018年12月29日修正）",
                "《中华人民共和国政府信息公开条例》（国务院令第711号，2019年5月15日起施行）",
                "《信访工作条例》（中共中央、国务院发布，2022年5月1日起施行）",
            ]),
            ("二、省级法规与标准", [
                "《中华人民共和国土地管理法实施条例》（2021年修订，国务院令第743号）",
                "《江苏省土地管理条例》（2021年1月15日修订，2021年5月1日起施行）",
                "《江苏省被征地农民社会保障办法》（苏政发〔2021〕87号，2022年3月1日起施行）",
                "DB32/T4013-2021《第三方社会稳定风险评估规范》",
                "DB32/T4937-2024《土地征收前社会稳定风险评估规范》",
                "《江苏省重大决策社会稳定风险评估办法》（苏政办发〔2021〕58号）",
                "《关于加强新形势下重大决策社会稳定风险评估机制建设的实施意见》（苏办发〔2021〕15号）",
            ]),
            ("三、市级规范与文件", [
                "《市政府关于公布淮安市征地区片综合地价执行标准的通知》（淮政规〔2026〕1号，2026年6月9日发布，现行有效）",
                "《淮安市征地补偿和被征地农民社会保障实施细则》",
                "《洪泽区国土空间总体规划（2021—2035年）》",
                "《洪泽区国民经济和社会发展第十四个五年规划和二〇三五年远景目标纲要》",
            ]),
            ("四、项目文件", [
                "洪拟征告〔2026〕7号 征收土地预公告",
                "0-勘测定界报告-",
                "项目用地预审与选址意见书",
                "拟征收土地现状调查确认表",
            ]),
        ]

        for section_title, items in laws:
            self._add_heading(doc, section_title, 2)
            for item in items:
                self._add_para(doc, f'• {item}', indent=True, font='仿宋_GB2312', size=Pt(12))
            doc.add_paragraph()

    # ═══════════════════════════════════════════════════════════════
    # Appendices (项目材料)
    # ═══════════════════════════════════════════════════════════════

    def _add_appendices(self, doc, survey_stats, image_files, filled):
        """Add appendices: 附件目录 + 附件1机构资质 + 附件2问卷 + 附件3评审 + 附件4公告 + 附件5勘测 + 附件6现场照片 + 附件7台账 + 待补充清单."""
        # Collect ALL images from the catalog
        catalog_data = getattr(self, '_img_catalog_data', {})
        all_images = catalog_data.get("catalog", [])
        session_images = []
        for img in all_images:
            p = img.get("path", "")
            if isinstance(p, str) and os.path.exists(p) and os.path.getsize(p) > 5000:
                if os.path.splitext(p)[1].lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}:
                    session_images.append(img)
        domain = filled.get("_domain", "stability") if isinstance(filled, dict) else "stability"

        # ── 第14章 附件 ──
        # 🔴 附件**只放图片**（业主口径 2026-09-21）：原来打头的
        #    「附件1 机构及人员资质材料」「附件2 问卷调查统计分析」正文里已经有了、
        #    而且不是图片，一并删掉。
        # 🔴 附件章章号按域取（稳评=14）。本方法只在「该域有附件章」时被调用
        #    （见 assemble() 的 `appendix_chapter_no` 判断），故此处必不为 None。
        _TOTAL = appendix_chapter_no(domain) or total_chapters(domain)

        cert_images = self._load_company_certificate_images(domain=domain)
        cert_paths = set(p for p, _ in cert_images)

        # 🔴 先把「每张图归到哪个附件」算出来 —— 附件清单要与**实际渲染的章节**一致
        #    （旧版清单取自 `get_ch12_appendix_list()`、章节是另一套硬编码，
        #      两处各写各的，必然对不上）。
        placed = {}
        for img in session_images:
            if self._img_already_used(img) or str(img.get("path", "")) in cert_paths:
                continue
            sub = _appendix_sub_label(img)
            gidx = _APPENDIX_SUB_TO_GROUP.get(sub)
            if sub is None or gidx is None:
                continue
            placed.setdefault(gidx, {}).setdefault(sub, []).append(img)

        other_paths = [img for img in session_images
                       if not self._img_already_used(img)
                       and str(img.get("path", "")) not in cert_paths
                       and _appendix_sub_label(img) is None]

        _sections = [(t, g) for g, t in enumerate(_ATTACHMENT_MAIN) if placed.get(g)]
        if other_paths:
            _sections.append(('其他项目资料', 'other'))
        # 🔴 「不稳定因素动态排查台账样表」「后续补充归档材料」这两节**已删除**
        #    （业主 2026-09-22 口径 B）。它们是**纯文本、不是图片**，与
        #    「附件只放图片」的既定要求不符 —— 与之前删掉的「机构及人员资质材料」
        #    「问卷调查统计分析」是同一类。

        self._add_chapter_title_page(doc, _cn_num(_TOTAL), self._get_chapter_title(_TOTAL))

        # 附件清单 —— **由实际章节生成**，不再另写一份
        _p = doc.add_paragraph(); _p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _r = _p.add_run('附件清单')
        _r.font.name = FONT_SONGTI; _r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_SONGTI)
        _r.font.size = Pt(14); _r.bold = True
        for _i, (_t, _) in enumerate(_sections, 1):
            self._add_para(doc, f'{_i}.{_t}', indent=False, font=FONT_SONGTI, size=Pt(14))

        # 🔴 没有可渲染的附件材料 → 留白两页，供后续粘贴扫描件（业主口径）
        if not _sections:
            for _ in range(2):
                doc.add_page_break()
                self._add_para(doc, "", indent=False)

        # ── 按 `_sections` 的实际顺序渲染，编号动态排 ──
        for _no, (_t, _key) in enumerate(_sections, 1):
            doc.add_page_break()
            self._add_heading(doc, f'附件{_no} {_t}', 1)

            if _key == 'other':
                self._add_para(doc, f'共 {len(other_paths)} 张', indent=True)
                for img in other_paths:
                    p = img.get("path", "")
                    if not p:
                        continue
                    self._inserted_images.add(str(p))
                    self._add_image(doc, p, "", numbered=False)

            else:
                subs = placed.get(_key) or {}
                # 按固定的顺序展示小节（不用 sorted：中文序号按码位排会乱序）
                for slabel in _ATTACHMENT_SUBS[_key]:
                    imgs = subs.get(slabel)
                    if not imgs:
                        continue
                    self._add_heading(doc, slabel, 2)
                    self._add_para(doc, f'共 {len(imgs)} 张', indent=True)
                    # 🔴 附件里的图按正常尺寸单张排列（并排只用于正文里的公示/现场图）；
                    #    图下**不写「图14-N」** —— 材料类型已由上面小节标题给出。
                    for img in imgs:
                        p = img.get("path", "")
                        if not p:
                            continue
                        self._inserted_images.add(str(p))
                        self._add_image(doc, p, "", numbered=False)


    def _reclamation_appendix_pool(self) -> Dict[int, list]:
        """复垦附件：把会话材料归到 22 个附件项 → `{项号: [{path, note}, …]}`。

        三条来源，全部复用既有设施：
          ① **PDF 整页渲染** —— 按 `source_name` 经 `match_reclamation_appendix` 归项；
             命中不了的进附件22「其他」（与稳评的 `other_paths` 同一思路）。
          ② **公司资质图** —— 附件3「编制单位资质证书」。
             🔴 只取与复垦有关的（滤掉「稳评平台备案」那张 —— 那是稳评的东西，
                贴进复垦报告就是拿稳评材料充数）。
          ③ **图片类** —— 现场照片/影像 → 附件12；公示类 → 附件11。
             正文已用过的按 `_img_already_used` 跳过（内容和指纹双重去重）。

        总量受 `REPORT_MAX_APPENDIX_PAGES`（缺省 60）约束，防止整本 PDF 把 docx 撑爆。
        """
        from app.agent.reclamation_definitions import match_reclamation_appendix
        from app.services.pdf_key_images import render_all_pages

        try:
            budget = int(os.environ.get("REPORT_MAX_APPENDIX_PAGES", "60"))
        except Exception:
            budget = 60

        pool: Dict[int, list] = {}

        def _add(item_no: int, path: str, note: str = ""):
            nonlocal budget
            if budget <= 0 or not path:
                return
            pool.setdefault(item_no, []).append({"path": path, "note": note})
            budget -= 1

        # 🔴 材料名 → 附件项（③④ 图片归项与 PDF 的「同文档去重」共用这张表）。
        #    归项口径要和 `RECLAMATION_APPENDIX_RULES`（PDF 走的那张）一致，
        #    否则同一份调查表会一半按 PDF 进门、一半按图片进门，印两遍。
        _NAME_RULES = (
            (16, ("现状图", "勘测定界图", "勘界图", "勘界报告", "勘测定界报告", "勘测报告")),
            (17, ("红线",)),
            (13, ("功能分布图", "分布图")),
            (6, ("复垦协议",)),
            (5, ("临时用地协议", "临时使用土地合同", "使用土地合同", "临时用地合同")),
            (9, ("调查表", "公众调查", "调查问卷")),
            (10, ("权利人意见", "权利人")),
            (14, ("补偿证明", "补偿到位", "补偿款到位")),
        )

        def _stem_key(n: str) -> str:
            """材料名归一（调查表_01 → 调查表），见模块级 `material_stem_key`。"""
            return material_stem_key(n)

        def _pdf_pages(p) -> int:
            try:
                import fitz
                with fitz.open(str(p)) as _d:
                    return int(_d.page_count)
            except Exception:
                return -1

        # 同名逐页图片（`调查表_01.jpg` … `调查表_07.jpg`）：用来判断「PDF 与图片是同一份材料」。
        # 只统计**会进附件**的那些（按名能归项），否则跳过 PDF 会把材料弄丢。
        _img_stems: Dict[str, list] = {}
        for _img in ((getattr(self, '_img_catalog_data', {}) or {}).get("catalog") or []):
            _ip = str(_img.get("path") or "")
            if not _ip or os.path.basename(_ip).startswith("pdf_"):
                continue
            if not next((n for n, kws in _NAME_RULES if any(k in _ip for k in kws)), 0):
                continue
            _img_stems.setdefault(_stem_key(_ip), []).append(_img)

        # ① PDF 原件 → 整页渲染
        for m in (self._project_materials or []):
            if not isinstance(m, dict):
                continue
            name = str(m.get("source_name") or "")
            src = str(m.get("source_path") or "")
            if not src or not src.lower().endswith(".pdf"):
                continue
            _p = self._resolve_image_path(src)
            if not _p.exists():
                continue
            # 🔴 同一份材料既传了 PDF 又传了逐页图片（井神「调查表.pdf」+
            #    「调查表_01..07.jpg」）：两边都进附件 = **同一份调查表印两遍**
            #    （2026-11-02 业主实测）。页数相同时按图片入册（原图分辨率更高），
            #    PDF 整份跳过；页数对不上说明不是同一份，仍按 PDF 收（宁可重复不可丢页）。
            _same = _img_stems.get(_stem_key(name)) or []
            if _same and _pdf_pages(_p) == len(_same):
                logger.info(f"[复垦附件] 《{name}》与 {len(_same)} 张同名图片内容相同，"
                            f"跳过 PDF 渲染（只按图片入册，避免重复）")
                continue
            # 「勘测定界报告」在样本里同时供了附件16（勘测报告）与附件18（主体项目用地范围），
            # 靠 match 表**专名优先**区分，这里不再二次判断。
            _item = match_reclamation_appendix(name) or 22
            for page in render_all_pages(str(_p), max_pages=max(0, budget)):
                _add(_item, page, name)

        # ② 公司资质 → 附件3
        # 🔴 附件3 是「**编制单位**资质证书」= 机构的营业执照/资质，**不含个人的培训证书**。
        #    个人证书的剔除在 `_load_company_certificate_images` 里统一做了（那儿原先
        #    是死代码，已修），这里只再挡掉稳评专有的那张「稳评平台备案」。
        try:
            _certs = self._load_company_certificate_images(domain=self._domain) or []
        except Exception as _ce:
            logger.warning(f"[复垦附件] 公司资质图加载失败: {_ce}")
            _certs = []
        for _cp, _cap in _certs:
            if "稳评" in str(_cap):
                continue                       # 稳评平台备案不进复垦
            _add(3, str(_cp), str(_cap))

        # ③ 图片类 → 附件11 公示 / 附件12 现场照片、影像图
        for img in ((getattr(self, '_img_catalog_data', {}) or {}).get("catalog") or []):
            if self._img_already_used(img):
                continue
            _p = str(img.get("path", ""))
            _cat = str(img.get("category") or "")
            if not _p:
                continue
            # 🔴 影像图（灰度航拍被判成 map 的）没进正文 → 归附件12「现场照片、影像图」。
            #    2026-10-31 实测：两张航拍影像既不进正文也不进附件，材料等于丢了。
            if _cat == "map":
                if not os.path.basename(_p).startswith("pdf_"):
                    _add(12, _p, str(img.get("display_name") or "影像图"))
                continue
            # 🔴 复垦图片分类器产出的是 `site_photo / site_layout / occupancy_map /
            #    section` 这类**复垦专名**（见 RECLAMATION_IMAGE_SPECS），不是稳评的
            #    `photo/announcement`。只认后两个 → 正文用剩的现场照片一张都进不了
            #    附件12（2026-11-04 实测：62 张图，附件区 0 张）。
            if _cat in ("site_photo", "site_layout", "occupancy_map", "section"):
                _add(12, _p, str(img.get("display_name") or "现场照片"))
                continue
            if _cat not in ("photo", "announcement"):
                continue
            # 🔴 **PDF 派生的图不进这一类**（`pdf_*` 是 PDF 内嵌位图 / 整页渲染 / 关键图）：
            #    那些 PDF 的原件已经在 ① 里整份进附件了。不排除的话，同一份「核准批复」
            #    会以扫描页形式出现在附件20，又被 AI 判成 `announcement` 塞进附件11「公示」
            #    —— 实测就是这样（批复的 3 页被贴成「公示照片」，与附件20 重复）。
            if os.path.basename(_p).startswith("pdf_"):
                continue
            _add(11 if _cat == "announcement" else 12, _p,
                 str(img.get("display_name") or ""))

        # ④ **图片材料按名归项** —— 现状图 / 勘测定界图 / CAD 渲染图 / 功能分布图。
        #    🔴 这条路是补的：原先附件池只认 `.pdf` + 公司资质 + `photo/announcement` 两类图，
        #       用户传的图片材料**一份都进不了报告**。更隐蔽的是：上传的图按
        #       `<原名>_<hash>.jpg` 落盘，而图片目录内部的回退找的是 `storage/images/<原名>`
        #       —— 名字对不上就**整张丢掉**（`_prepare_image_catalog` 里已修成用真实路径）。
        #    归属按样本的附件清单（规则表见函数开头 `_NAME_RULES`）：
        #       现状图 / 勘测定界图 → 附件16「勘测报告」的附图
        #         （样本里它属**第13部分图件区**的图件②，图件区还没做，先挂勘测报告名下）
        #       红线图（含 CAD 渲染出的 `<名>.cad.png`）→ 附件17「主体项目用地红线图」
        #       功能分布图 → 附件13「临时用地功能分布图」
        #       合同/协议扫描件 → 附件5/6（分类器已把这类判成 `document`，不进 ③ 的 11/12）
        for img in ((getattr(self, '_img_catalog_data', {}) or {}).get("catalog") or []):
            _p = str(img.get("path", ""))
            _nm = os.path.basename(_p)
            if not _p or _nm.startswith("pdf_"):
                continue
            _it = next((n for n, kws in _NAME_RULES if any(k in _nm for k in kws)), 0)
            if not _it:
                continue
            if any(f["path"] == _p for f in pool.get(_it, [])):
                continue
            _add(_it, _p, _nm)

        return pool

    def _add_reclamation_report_form(self, doc, chapters, filled):
        """第11部分「土地复垦方案报告表」—— 样本那种**制式表单**（`reclamation_report_form`）。

        数据全部取自既有事实源（材料抽取 / 公司档案 / 人员库 / 勘测定界地类 /
        第8章与第10章正文 / budget-maker 现算的投资估算），**不另编**；抽不到的留空。
        """
        from app.services.reclamation_report_form import collect_form_data, render_report_form
        from app.services.reclamation_budget_bridge import empty_cost_rows
        try:
            from app.services.personnel_store import personnel_for
            _people = personnel_for(getattr(self, "_selected_personnel", None) or None,
                                    self._company_id())
        except Exception as _pe:
            logger.warning(f"[报告表] 人员库读取失败: {_pe}")
            _people = []
        # 🔴 没有勾选时（脚本/老会话），默认按范文的三位排前面（陈春/林叶青/刘利伟），
        #    不够再补其他职称/专业齐全的人；报告表最多 3 行。
        if not (getattr(self, "_selected_personnel", None) or []) and _people:
            # 复垦只收职称/专业齐全的人（与前端可选名单一致）
            _people = [p for p in _people
                       if str(p.get("职称/职务") or "").strip()
                       and str(p.get("专业") or "").strip()] or _people
            try:
                from app.agent.reclamation_definitions import (
                    RECLAMATION_DEFAULT_PERSONNEL as _pref_names,
                )
                _by_name = {p.get("姓名"): p for p in _people}
                _pref = [_by_name[n] for n in _pref_names if n in _by_name]
                if _pref:
                    _people = _pref + [p for p in _people if p not in _pref]
            except Exception:
                pass

        data = collect_form_data(filled or {}, self._company or {}, _people)
        # 🔴 报告表的这两栏就是**正文第8章/第10章的原文摘录**，不压缩改写。
        #    早先卡 1400/2800 字，第10章尾段（土地权属调整方案）被切掉并加「……」，
        #    业主 2026-11-05：「报告表中的文本是否都能对应上正文内容？区别少一些」。
        #    现在两栏按整章收（只留一个防极端长章撑爆表格的上限）。
        data["工作计划"] = self._excerpt(chapters, 8, 8000)
        data["保障措施"] = self._excerpt(chapters, 10, 8000)
        # 🔴 测算依据也取**正文 7.1.1 的原文** —— 早先是一段写死的静态清单，
        #    与模型写的第7章不一致（正文有《江苏省土地综合整治工程建设规范》、
        #    2023 年修订版定额等，表里没有）。取不到才回落到静态口径。
        data["测算依据"] = self._section_prose(chapters, 7, "投资估算编制依据") or (
            "（1）《土地复垦方案编制规程——通则》（TD/T 1031.1~1031.7-2011）；"
            "（2）《江苏省土地综合整治项目预算定额标准》；"
            "（3）《江苏省土地综合整治项目施工机械台班费预算定额标准》（苏财建〔2013〕469 号）；"
            "（4）淮安市建设工程材料信息价；"
            "（5）其他有关规定和标准。")
        # 费用构成：第7章的同一份估算（budget-maker 现算）；取不到数就留空，不印 0
        _est = self._budget_estimate()
        data["费用构成"] = _est.cost_rows if _est else empty_cost_rows()

        _no = report_form_chapter_no(self._domain)
        self._add_chapter_title_page(doc, _cn_num(_no), self._get_chapter_title(_no),
                                     chapter_number=_no)
        _t0 = len(doc.tables)
        render_report_form(doc, data, chapters, chapter_no=_no)
        # 🔴 报告表的空白格就是**留白给人手填**（法人代表/发证机关/已损毁…），
        #    不是「没有数据」。记下这些表格，`_post_generation_fix` 的
        #    「空单元格填 —」那一步必须跳过 —— 业主 2026-11-02：
        #    「表格中空白的部分不需要 "-" 代替」。
        self._form_tables = [t._tbl for t in doc.tables[_t0:]]

    def _add_reclamation_figures(self, doc, filled):
        """第13部分「图件」：系统位置图 + 用户上传的现状/规划/勘界图。

        样本这一部分是**整页大图**（土地利用现状图、复垦规划图等），所以这里
        不套正文小图规格，按 15cm 宽单页排。没有图就只写一句说明，不印空章节。
        """
        from docx.shared import Cm as _Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH as _AL

        items = []
        _loc = Path(str(self.storage_dir)) / "images" / "图3-1 工程位置示意图.jpg"
        if _loc.exists():
            items.append((_loc, "项目地理位置示意图"))

        _cats = {"land_use_map", "site_layout", "occupancy_map",
                 "damage_layout", "measure_layout"}
        _seen = {str(_loc)}
        for it in ((getattr(self, "_img_catalog_data", {}) or {}).get("catalog") or []):
            cat = str((it or {}).get("category") or "")
            if cat not in _cats:
                continue
            name = str((it or {}).get("display_name") or (it or {}).get("name") or "")
            if "位置示意图" in name or "工程位置" in name:
                continue
            try:
                p = self._resolve_image_path((it or {}).get("path") or "")
            except Exception:
                continue
            if not p or not p.exists() or str(p) in _seen:
                continue
            _seen.add(str(p))
            items.append((p, name or f"图件{len(items) + 1}"))
            if len(items) >= 10:
                break

        if not items:
            self._add_para(doc, "本次未提供图件。")
            return
        for i, (p, name) in enumerate(items, 1):
            if i > 1:
                doc.add_page_break()
            try:
                para = doc.add_paragraph()
                para.alignment = _AL.CENTER
                para.add_run().add_picture(str(p), width=_Cm(15))
            except Exception as e:
                logger.warning(f"[图件] 插入失败 {p}: {e}")
                continue
            cap = doc.add_paragraph()
            cap.alignment = _AL.CENTER
            r = cap.add_run(f"图件{i} {name}")
            r.font.name = "黑体"
            r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), "黑体")
            r.font.size = Pt(10.5)
            r.bold = True

    @staticmethod
    def _section_prose(chapters, ch_num, title: str) -> str:
        """取某章某个小节的正文纯文本（去标题/表格行）。

        报告表「投资估算测算依据」用它，保证表里写的就是正文 7.1.1 的原话，
        而不是另写一段静态清单（业主 2026-11-05：「报告表中的文本要能对应上正文」）。
        """
        ch = (chapters or {}).get(ch_num) or (chapters or {}).get(str(ch_num)) or {}
        md = ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
        out: List[str] = []
        inside = False
        for ln in str(md).splitlines():
            s = ln.strip()
            _h = re.match(r'^#{1,6}\s*(.+?)\s*#*$', s)
            if _h:
                if inside and title not in _h.group(1):
                    break
                inside = title in _h.group(1)
                continue
            if not inside or not s:
                continue
            if s.startswith('|') or s.startswith('!['):
                continue
            if re.match(r'^\**\s*[表图]\s*\d+[-—]\d+', s):
                continue
            out.append(re.sub(r'\*\*(.+?)\*\*', r'\1', s))
        return "\n".join(out).strip()

    @staticmethod
    def _excerpt(chapters, ch_num, limit: int) -> str:
        """取某章正文的纯文本摘录（去掉标题行与表格行），供报告表的摘要栏用。

        🔴 超限时**在句末断开**并加省略号 —— 硬切会把句子砍半
        （实测截在「…在其实施过程中必须进行工程」，业主看出来了）。
        """
        ch = (chapters or {}).get(ch_num) or (chapters or {}).get(str(ch_num)) or {}
        md = ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
        keep = []
        for ln in str(md).splitlines():
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith("|") or s.startswith("!["):
                continue
            # 🔴 表格/图片题注不是正文：「表8-1 复垦工作计划与投资安排表」被摘进
            #    报告表的工作计划栏，读者看不到表，只看到一句孤零零的题注。
            if re.match(r'^\**\s*[表图]\s*\d+[-—]\d+', s):
                continue
            s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
            keep.append(s)
        text = "\n".join(keep)
        text = re.sub(r'……+$', '', text).strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        _m = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("\n"))
        if _m > limit * 0.4:              # 断点太靠前就不折腾，宁可多带半句
            cut = cut[:_m + 1]
        return cut.rstrip() + "……"

    def _add_reclamation_appendices(self, doc, filled):
        """复垦第 14 部分「附件」—— 样本目录里那张 **22 项**清单。

        与稳评 `_add_appendices` 的差别（两域附件内容毫无重叠，故各写各的）：
          · 清单是**固定 22 项**（样本目录逐项抄下来的），不是「实际渲染了哪几节」；
          · 没材料的项**照列并标「（待补充）」**，缺口显性（业主口径：材料不要有漏的）；
          · 逐项版式 = 一项一行「附件N 名称」标题 + 该项原件整页顺排。
            ⚠️ 样本的附件区**不印逐项标题**（只有扫描页，项名只在目录清单里）——
               我们加标题是刻意的：件是程序按材料生成的，不标项就分不清哪几页属哪件。
        """
        from app.agent.reclamation_definitions import RECLAMATION_APPENDIX_ITEMS

        _TOTAL = appendix_chapter_no(self._domain) or total_chapters(self._domain)
        pool = self._reclamation_appendix_pool()

        # 🔴 必须传 `chapter_number`：复垦的章标题形态是 `14 附件`（阿拉伯数字），
        #    拼它要用章号；稳评那支只用中文序号 `cn`，不传也不变。
        self._add_chapter_title_page(doc, _cn_num(_TOTAL), self._get_chapter_title(_TOTAL),
                                     chapter_number=_TOTAL)

        # ── 附件清单：只列**这次真有材料**的项，并按 1..N 连续编号 ──
        #    业主 2026-11-04：「资料少就跳过这个附件，按数字顺序排列，不要中间隔一个」。
        #    以前固定列 22 项、缺的标「（待补充）」，渲染区就会出现 6 → 9 的跳号。
        _items = [(no, name) for no, name in RECLAMATION_APPENDIX_ITEMS
                  if pool.get(no)]
        _p = doc.add_paragraph(); _p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _r = _p.add_run('附件清单')
        _r.font.name = FONT_SONGTI; _r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_SONGTI)
        _r.font.size = Pt(14); _r.bold = True
        if not _items:
            self._add_para(doc, '本次未提供附件材料', indent=False, size=Pt(12))
        else:
            for _seq, (_no, _name) in enumerate(_items, 1):
                self._add_para(doc, f'附件{_seq} {_name}', indent=False, size=Pt(12))

        # ── 逐项渲染：编号与清单一致，连续不跳号 ──
        for _seq, (_no, _name) in enumerate(_items, 1):
            _files = pool.get(_no) or []
            doc.add_page_break()
            # 🔴 附件项标题用 2 级：样本目录里「14 附件」下挂各项，用 1 级会和
            #    正文各章并排（实测成稿目录里出现 10 个并列的「附件N」章）。
            self._add_heading(doc, f'附件{_seq} {_name}', 2)
            for _f in _files:
                _fp = _f.get("path", "")
                if not _fp:
                    continue
                self._inserted_images.add(str(_fp))
                # 🔴 附件里的原件**不编图号**（`numbered=False`）—— 材料类型已由项标题给出，
                #    再编「图14-1」反而像正文插图。
                self._add_image(doc, _fp, "", numbered=False)

        # 🔴 清单与逐项标题的编号可能不同步（实测清单「附件1」、标题「附件2」，
        #    专家批注读成两件同名附件）。以出现顺序为准重排，两边都从 1 连续。
        try:
            # 🔴 取**最后一个**「附件清单」：目录域里也可能有同名条目，
            #    从目录那条开始找会一路撞到「14 附件」Heading 1 提前 break。
            _idx = [i for i, p in enumerate(doc.paragraphs)
                    if p.text.strip() == "附件清单"]
            if _idx:
                self._renumber_appendix_numbers(doc, _idx[-1])
        except Exception as _re_err:
            logger.warning(f"[复垦附件] 编号重排失败（保持原样）: {_re_err}")

    @staticmethod
    def _renumber_appendix_numbers(doc, start_idx: int) -> None:
        """把附件区「附件N」的清单行与标题行分别按顺序重排为 1..N。"""
        seq_list = seq_head = 0
        for p in doc.paragraphs[start_idx:]:
            text = p.text.strip()
            if not text:
                continue
            if p.style.name == "Heading 1":      # 走出附件章
                break
            m = re.match(r'^附件\s*\d+\s*(.*)$', text)
            if not m:
                continue
            if p.style.name.startswith("Heading"):
                seq_head += 1
                new = f"附件{seq_head} {m.group(1)}".strip()
            else:
                seq_list += 1
                new = f"附件{seq_list} {m.group(1)}".strip()
            if p.runs:
                p.runs[0].text = new
                for r in p.runs[1:]:
                    r.text = ""

    def _fill_toc_cache(self, doc) -> int:
        """把目录域的**缓存结果**填成真实条目（供不刷新域的阅读器）。

        🔴 `w:updateFields=true` 只对 Word 生效。WPS / 转 PDF / 预览器不刷新域，
           目录就只剩「目 录」两个字 —— 参照件（洪泽模板、人工案卷）缓存里
           存了 96 条条目，我们的是**空的**（上面插目录时把中间段全删了，
           指望 Word 重建）。业主要求「目录要全部」。这个函数按文档里标题
           出现的顺序写入条目；Word 打开时仍会重算覆盖，不刷新域的阅读器
           则至少能看到完整目录。
        """
        from docx.oxml.ns import qn as _q
        from docx.oxml import OxmlElement

        def _tag(el):
            return el.tag.split('}')[1]

        sdt = next((el for el in doc.element.body.iterchildren() if _tag(el) == 'sdt'), None)
        if sdt is None:
            return 0
        contents = [c for c in sdt.iter(_q('w:sdtContent')) if c.findall(_q('w:p'))]
        content = contents[-1] if contents else None
        if content is None:
            return 0
        ps = list(content.findall(_q('w:p')))
        if not ps:
            return 0
        # 🔴 保留「目 录」标题与域管道（fldChar/instrText），只清旧条目 ——
        #    以前 `ps[1:-1]` 把标题也删了，目录页只剩条目没有「目 录」二字。
        title_p = next((p for p in ps if self._toc_is_title(p, _q)), None)
        field_ps = [p for p in ps if self._toc_is_field_para(p, _q)]
        for _p in ps:
            if _p is title_p or _p in field_ps:
                continue
            content.remove(_p)
        for _p in field_ps:
            # 🔴 标题段在模板里可能也带域管道（fldChar），会被当成 field_para；
            #    对它清 run 会把「目  录」两个字一起删掉（业主 2026-11-12：
            #    「目录标题俩个字没了」）。标题只保留原样，不清 run。
            if _p is title_p:
                continue
            self._toc_strip_entry_runs(_p, _q)
        end_p = next((p for p in field_ps if self._toc_has_fld_end(p, _q)), None)
        if end_p is not None:
            anchor = end_p
        elif title_p is not None:
            anchor = title_p
        else:
            anchor = field_ps[0] if field_ps else ps[0]

        # 收集正文标题（跳过目录 SDT 内部的段落）
        _inner = {id(x) for x in sdt.iter(_q('w:p'))}
        heads = []
        for para in doc.element.body.iter(_q('w:p')):
            if id(para) in _inner:
                continue
            pPr = para.find(_q('w:pPr'))
            st = pPr.find(_q('w:pStyle')) if pPr is not None else None
            v = (st.get(_q('w:val')) or '') if st is not None else ''
            if v in ('1', 'Heading1', 'heading 1'):
                lvl = 1
            elif v in ('2', 'Heading2', 'heading 2'):
                lvl = 2
            else:
                continue
            txt = "".join(t.text or '' for t in para.iter(_q('w:t'))).strip()
            if txt:
                heads.append((lvl, txt))
        # 🔴 参照件的目录从**第一个正文章标题**开始 —— 封面后的前置页标题不进目录。
        #    判定按域取：稳评 `第三章、…`，复垦 `3 项目概况`（阿拉伯数字）。
        #    写死稳评正则的话复垦一个都匹配不到，`_first` 退化为 0，
        #    前置页若有 Heading 就会漏进目录。
        _first = next((i for i, (_, t) in enumerate(heads)
                       if is_chapter_heading_text(self._domain, t)), 0)
        heads = heads[_first:]
        if not heads:
            return 0

        # 🔴 目录条目要**紧凑行距**（业主：「目录的格式行高紧凑一些」）。
        #    `docDefaults` 给的是 1.15 倍行距 + 段后 10 磅，条目之间空得厉害；
        #    参照件（洪泽模板 / 人工案卷）用的是 `line=350 lineRule=exact`
        #    —— 固定 17.5 磅。这里与参照件对齐。
        #    ⚠️ `w:spacing` 在 `w:pPr` 里的位置有 schema 约束：必须在 `w:tabs`
        #       之后、`w:ind` 之前，放错 Word 会因为校验失败而忽略整段 pPr。
        def _compact_spacing():
            sp = OxmlElement('w:spacing')
            sp.set(_q('w:line'), '350')
            sp.set(_q('w:lineRule'), 'exact')
            sp.set(_q('w:before'), '0')
            sp.set(_q('w:after'), '0')
            return sp

        # ⚠️ 模板自带的旧条目**不用单独压紧** —— 它们在上面清理旧条目时已经全删掉了，
        #    下面会按 `heads` 重新写一份。给已脱离文档树的元素设属性是死代码。
        for lvl, txt in heads:
            np = OxmlElement('w:p')
            pPr = OxmlElement('w:pPr')
            tabs = OxmlElement('w:tabs')
            tab = OxmlElement('w:tab')
            tab.set(_q('w:val'), 'right')
            tab.set(_q('w:leader'), 'dot')
            tab.set(_q('w:pos'), '8300')
            tabs.append(tab)
            pPr.append(tabs)
            pPr.append(_compact_spacing())   # 顺序：tabs → spacing → ind
            if lvl == 2:               # 二级条目缩进 2 字符
                ind = OxmlElement('w:ind')
                ind.set(_q('w:leftChars'), '200')
                pPr.append(ind)
            np.append(pPr)
            r = OxmlElement('w:r')
            rPr = OxmlElement('w:rPr')
            f = OxmlElement('w:rFonts')
            f.set(_q('w:ascii'), 'Times New Roman')
            f.set(_q('w:hAnsi'), 'Times New Roman')
            # 🔴 目录条目中文字体按域取（复垦样本：一级条目 黑体、二级条目 仿宋；
            #    稳评仍是宋体，逐字等于改动前）。字号两边都是 12pt。
            _tf, _ = typography(self._domain, "toc_l1" if lvl == 1 else "toc_l2")
            f.set(_q('w:eastAsia'), _tf)
            rPr.append(f)
            sz = OxmlElement('w:sz'); sz.set(_q('w:val'), '24')   # 12pt
            rPr.append(sz)
            if lvl == 1:
                rPr.append(OxmlElement('w:b'))
            r.append(rPr)
            t = OxmlElement('w:t'); t.text = txt
            r.append(t)
            np.append(r)
            if end_p is not None:
                end_p.addprevious(np)      # 条目插在域结束段之前
            else:
                anchor.addnext(np)         # 无域结束段：接在标题（或首段）之后
                anchor = np
        return len(heads)


    def _flush_pending_map_image(self, doc):
        """把待插入的第1章图（位置示意图 / 勘测定界图）插到当前位置（1.5 小节之后）。

        多张时**两张一行并排**，图注各自在图片正下方。
        """
        _mis = getattr(self, '_pending_map_images', None) or []
        if not _mis:
            return
        self._pending_map_images = []
        _fl = front_matter_labels(self._domain)
        _pairs = []
        for _mi in _mis:
            _mp = _mi.get("path", "") if isinstance(_mi, dict) else str(_mi)
            # 🔴 兜底图名按域取（稳评「拟征收土地位置示意图」→ 复垦「位置示意图」）
            _ml = (_mi.get("label", "") if isinstance(_mi, dict) else "") or _fl["map_caption"]
            if _mp:
                _pairs.append((_mp, _ml))
        if not _pairs:
            return
        # 🔴 一律走 _add_image_row_pair：单张会居中独占一行，两张则并排；
        #    地图类图片的「旋转 90° 横放」也在这个函数里处理（走 _add_image 会漏掉旋转）。
        self._add_image_row_pair(doc, _pairs)

    def _maybe_flush_map_image(self, doc, heading_text, ch_num):
        """章节内图片的「到点插入」钩子（每个二级标题都会调用一次）。

        - 第3章：进入「决策地理位置」小节 → 置位；下一个标题出现时把位置示意图插到该节之后。
        - 第5章：进入「风险调查过程」/「各方意见」小节 → 置位；下一个标题出现时插入对应的
          公示/现场/座谈照片、舆情与专家评审图。
        """
        _h = str(heading_text or "")

        # 🔴 按域锚点落位（复垦第3/4章）。稳评 `body_image_anchor` 恒为 None → 直接落到
        #    下面两处写死分支，行为逐字不变。
        _anchor = body_image_anchor(self._domain, ch_num)
        if _anchor:
            if _anchor in _h:
                if getattr(self, '_pending_section_trigger', None):
                    self._flush_section_images(doc)   # 先落上一批
                self._pending_section_trigger = _anchor
                return
            if getattr(self, '_pending_section_trigger', None):
                self._flush_section_images(doc)
            return

        if ch_num == 6:
            # 🔴 业主口径（2026-09-18）：图6-1 项目位置示意图要插在
            #    「项目概况 → 3.项目地理位置」小节正文之后（人工成品案卷的图4-1 决策地理位置
            #    就落在「3.决策地理位置」）。
            #    所以**只认「地理位置」**置位，并在下一个标题（任意层级，含 `####`）处落图；
            #    万一本章没有「地理位置」小节，章末兜底会强制落图，不会丢图。
            if '地理位置' in _h:
                self._pending_map_flush_on_next = True
                return
            if getattr(self, '_pending_map_flush_on_next', False):
                self._flush_pending_map_image(doc)
                self._pending_map_flush_on_next = False
            return

        if ch_num == 8:
            # 第8章：进入「8.4 风险调查过程」→ 置位；下一个标题出现时插入公示/现场/座谈照片。
            _pend = getattr(self, '_pending_section_imgs', {}) or {}
            _trig = None
            if '风险调查过程' in _h:
                _trig = '风险调查过程'
            if _trig:
                if getattr(self, '_pending_section_trigger', None):
                    self._flush_section_images(doc)   # 🔴 先落上一批
                self._pending_section_trigger = _trig
                return
            if getattr(self, '_pending_section_trigger', None):
                self._flush_section_images(doc)
            return

    def _flush_pending_section_images(self, doc):
        """章末兜底：把仍未插入的章节内图片按顺序补插，避免丢图。"""
        _pend = getattr(self, '_pending_section_imgs', None)
        if not _pend:
            return
        for _key in list(_pend.keys()):
            self._pending_section_trigger = _key
            self._flush_section_images(doc, force=True)
        # 挂起的（本节没正文）也要落，否则章末丢图
        self._flush_deferred_images(doc)

    # 第8章「（四）风险调查过程」的四个子项 → 各自照片的识别关键词（顺序敏感：先专后泛）
    _CH8_SUBITEMS = (
        ("现场公示", ("公示",)),
        ("实地勘察", ("勘察", "勘查", "踏勘", "查勘")),
        ("现场走访调查", ("走访", "问卷", "调查表")),
        ("座谈会", ("座谈", "会议", "开会")),
    )

    # 🔴 图库标签（确定性）→ 子项。按**标签**分桶比按文件名猜准得多：
    #    文件名里「座谈会签到表」也含「座谈」、「临时用地现场照片」也含「现场」，
    #    靠关键词排序的结果是把签到表塞进「座谈会」、把地块照塞进「现场公示」。
    _CH8_LABEL_TO_SUB = {
        '公示照片': '现场公示',
        '现场勘察照片': '实地勘察',
        '现场调查照片': '实地勘察',
        '调查表': '现场走访调查',
        '群众调查图': '现场走访调查',
        '现场走访照片': '现场走访调查',
        '群众座谈会照片': '座谈会',
    }

    # 🔴 同一个标签的图要**分给两个子项**时按这个顺序切（业主口径 2026-09-20：
    #    「现场景色照片就是走访照片」—— 实地勘察与现场走访调查都配地块现场照，
    #    各取各的，不是同一张图用两遍）。
    # 🔴 走访照片要用「有人」的那组（业主：「现场走访照片也是要有人的」），
    #    不再把地块现场照切一半过去 —— 现场景色照只归实地勘察。
    _CH8_SPLIT = {}

    def _ch8_partition_images(self, imgs: list) -> None:
        """把第8章的候选图按**子项**分桶，剩下的留在 `_pending_section_imgs` 兜底。"""
        _subimg, _leftover = {}, []
        _used = {}   # 标签 → 已切走的张数（`_CH8_SPLIT` 按这个顺序往下分）
        for _img in imgs:
            if not isinstance(_img, dict):
                continue
            _lab = str(_img.get('label') or '').strip()

            _split = self._CH8_SPLIT.get(_lab)
            if _split:
                _seen, _acc, _target = _used.get(_lab, 0), 0, None
                for _sub, _n in _split:
                    if _seen < _acc + _n:
                        _target = _sub
                        break
                    _acc += _n
                if _target:
                    _it = dict(_img)
                    # 同一批地块现场照，落在「实地勘察」叫现场勘察照片，
                    # 落在「现场走访调查」就叫现场走访照片 —— 否则图注与所在小节对不上。
                    if _target == '现场走访调查' and _lab == '现场勘察照片':
                        _it['label'] = '现场走访照片'
                    _subimg.setdefault(_target, []).append(_it)
                    _used[_lab] = _seen + 1
                    continue
                _leftover.append(_img)
                continue

            _sub = self._CH8_LABEL_TO_SUB.get(_lab)
            if not _sub:
                _meta = f"{_lab} {_img.get('name', '')} {_img.get('type', '')}"
                for _k, _kws in self._CH8_SUBITEMS:
                    if any(k in _meta for k in _kws):
                        _sub = _k
                        break
            if _sub:
                _subimg.setdefault(_sub, []).append(_img)
            else:
                _leftover.append(_img)
        self._ch8_subimg = _subimg
        self._pending_section_imgs = {'风险调查过程': _leftover}
        self._ch8_cur_sub = None

    def _flush_ch8_group(self, doc, sub: str) -> None:
        _imgs = (getattr(self, '_ch8_subimg', None) or {}).pop(sub, None)
        if not _imgs:
            return
        _pairs = []
        for _i in _imgs:
            if isinstance(_i, dict) and _i.get("path"):
                _pairs.append((_i["path"], _i.get("label", "") or _i.get("name", "") or ""))
        if _pairs:
            self._add_image_row_pair(doc, _pairs)

    def _flush_ch8_subitem(self, doc, line: str) -> None:
        """第8章子项「到点插入」：遇到下一个子项标题时，先把上一批照片落下去。

        🔴 旧实现只在**小节标题**上装了钩子，而（四）风险调查过程是本章最后一节 →
           钩子永不触发，公示/勘察/走访/座谈 11 张照片全堆到章末，与正文脱节、顺序也乱。
        🔴 子项在模型笔下可能是 `#### 1.现场公示`（四级标题）也可能是加粗的裸行
           `1. 现场公示` —— 先剥掉 `#` 再剥编号，两种写法都要认。
        """
        _t = re.sub(r'^#+\s*', '', (line or "").strip())
        _t = re.sub(r'^\s*\d{1,2}[.、]\s*', '', _t)
        if _t not in {k for k, _ in self._CH8_SUBITEMS}:
            return
        _prev = getattr(self, '_ch8_cur_sub', None)
        if _prev:
            self._flush_ch8_group(doc, _prev)
        self._ch8_cur_sub = _t

    def _flush_section_images(self, doc, force: bool = False):
        """把当前待插入的章节内图片（第6章 公示/现场/座谈 等）成对插入。

        🔴 `force=False` 且本节**只有标题、还没有正文段**时，先挂起：
           实测 2026-10-31「4.1 土地损毁分析与评定」下面没有引言、直接进 4.1.1，
           三张图就顶在小节标题下面，正文一个字都没有。挂起后等第一个正文段出现，
           再插到那段后面（见 `_add_para` 里的 `_flush_deferred_images`）。
        """
        _trig = getattr(self, '_pending_section_trigger', None)
        if not _trig:
            return
        _pend = getattr(self, '_pending_section_imgs', {}) or {}
        _imgs = _pend.get(_trig) or []
        if (_imgs and not force and getattr(self, '_defer_empty_section', False)
                and not getattr(self, '_section_body_seen', False)):
            self._pending_section_trigger = None
            self._defer_imgs = (_trig, list(_imgs))
            _pend.pop(_trig, None)
            logger.info(f"[落图] 小节「{_trig}」暂无正文，图片挂起等第一段："
                        + "、".join(str(x.get('path') if isinstance(x, dict) else x) for x in _imgs))
            return
        self._pending_section_trigger = None
        self._section_body_seen = False
        _imgs = _pend.pop(_trig, [])
        if not _imgs:
            return
        _pairs = []
        for info in _imgs:
            if isinstance(info, dict):
                _p = info.get("path", "")
                _l = info.get("label", "") or info.get("name", "") or ""
            else:
                _p, _l = str(info), ""
            if _p:
                _pairs.append((_p, _l))
        if _pairs:
            if self._domain == "reclamation":
                # 🔴 复垦正文是**地图类**（位置图/现状图/勘测定界图），两张并排各 7cm
                #    看不清 —— 逐张独占一行（宽 12.5cm）。照片类才需要并排。
                for _p, _l in _pairs:
                    self._add_image(doc, _p, _l or "", numbered=True)
                    # 🔴 每张图后面紧跟一段带数字的解读（地段 + 占地面积 + 主导地类），
                    #    业主 2026-11-02：「每张插入的图片是否都有分析」。
                    self._add_figure_reading(doc, getattr(self, '_current_chapter', 0),
                                             [(_p, _l)])
            else:
                self._add_image_row_pair(doc, _pairs)

    def _next_caption(self, label):
        """按当前章生成「图X-N 描述」图注（章内序号自动递增）。"""
        ch = getattr(self, '_current_chapter', 0)
        counter = self._chapter_image_counter.setdefault(ch, 0) + 1
        self._chapter_image_counter[ch] = counter
        desc = re.sub(r'^图\s*\d+[-—]\d+\s*', '', (label or '').strip())
        return f"图{ch}-{counter} {desc}".strip() if desc else f"图{ch}-{counter}"

    def _add_image_row_pair(self, doc, pairs, max_dim=1600, max_w_cm=7.0):
        """插入一组图片：**两张一行并排**，图注各自在图片正下方居中；奇数张时末张独占一行。

        🔴 用**制表位对齐**而不是表格：
        - 表格：Word 会给「无边框表格」画**虚线网格**，看起来像给图片套了虚线框；
        - 制表位：图片与图注各用一个居中制表位（1/4 与 3/4 版心处），
          既没有虚线，又能让图注精确对齐到各自图片正下方。
        """
        from PIL import Image as PILImage
        import tempfile as _tmp
        from docx.enum.text import WD_TAB_ALIGNMENT

        def _prep(path, label=""):
            """缩图（地图类**旋转 90° 横放**）后落地为临时 jpg，返回 (临时路径, 宽cm, 高cm)。"""
            from PIL import ImageOps
            with PILImage.open(str(path)) as im:
                im = ImageOps.exif_transpose(im)   # 先按 EXIF 摆正
                im = self._flatten_to_rgb(im)
                # 🔴 地图类（位置示意图 / 勘测定界图）**旋转 90° 横放**（用户要求）；
                #    照片类（公示/座谈/现场）不旋转，否则人会躺着。
                # 🔴 地图类（勘测定界图 / 红线图 / 规划图）**竖版扫描件**才旋转 90° 横放
                #    （用户要求）；本来就是横版的图（如卫星位置示意图）再转一次就成了躺着的。
                #    照片类（公示/座谈/现场）一律不旋转，否则人会躺着。
                # 🔴 **系统生成的图一概不转**：那条启发式是给「竖版**扫描件**」用的 ——
                #    扫描件是横版地图印在竖版纸上，转过来才正；而我们自己画的图3-1
                #    （卫星影像 + 红线）是**真的南北向竖图**，转 90° 之后正北就指向左边了。
                #    实测 2026-10-12：临时用地是南北长条，出图 768×1761，被转躺。
                if (os.path.basename(str(path)) not in getattr(self, '_no_rotate_names', set())
                        and any(k in str(label) for k in ("位置示意图", "勘测定界图", "勘界图", "红线图", "规划图"))
                        and im.height > im.width):
                    im = im.transpose(PILImage.ROTATE_270)   # 顺时针 90°
                ow, oh = im.size
                if ow > max_dim or oh > max_dim:
                    ratio = min(max_dim / ow, max_dim / oh)
                    im = im.resize((int(ow * ratio), int(oh * ratio)), PILImage.LANCZOS)
                _fd, tmp = _tmp.mkstemp(suffix='.jpg'); os.close(_fd)
                im.save(tmp, 'JPEG', quality=80, optimize=True)
            # 旋转后高度可能很大 → 按「可用高度」再约束一次宽
            # 🔴 复垦正文图按小图收口（宽 ≤12.5cm、高 ≤9.5cm）—— 与 `_add_image`
            #    同一口径；附件页不走这里（附件用的是 `_add_image`）。
            _body = self._domain == "reclamation"
            _max_h = 9.5 if _body else 19.0
            w = (max_w_cm if len(pairs) > 1 else (12.5 if _body else 14.0))
            h = (w * oh / ow) if ow else w
            if h > _max_h and oh:
                h = _max_h
                w = h * ow / oh
            return tmp, w, h

        # 版心 15.6cm：单张居中；两张时图心分别落在 1/4、3/4 处
        _TABS = [(Cm(3.9), None), (Cm(11.7), None)]

        # 🔴 先按内容指纹剔除已插过的图，再配对 —— 与 `_add_image` 共用同一张去重表。
        #    本函数直接 `run.add_picture()`，绕过了 `_add_image`，此前既不查也不登记
        #    `_inserted_images`，导致第8章正文的图在附件6 又被插一遍（实测 124 张图被引用 277 次）。
        _keep = []
        for _pair in (pairs or []):
            try:
                _p = _pair[0]
            except Exception:
                continue
            _fp = self._image_fingerprint(_p)
            _key = _fp or str(_p)
            if _key in self._inserted_images:
                continue
            self._inserted_images.add(_key)
            _keep.append(_pair)
        pairs = _keep
        if not pairs:
            return

        for i in range(0, len(pairs), 2):
            row = pairs[i:i + 2]
            # 🔴 同一行的图**同类同注**时只出一行居中图注（业主口径：
            #    「图8-1 公示照片 图8-2 公示照片 合并为统一个名称，居中」）。
            #    人工成品案卷也是这个写法：一组照片下面一行「图6-1 朱坝街道公示照片」。
            #    图注不同（如 位置示意图 + 勘测定界图）才逐图各自标注。
            _labels = [str(l or "").strip() for _, l in row]
            _merged = _labels[0] if _labels and len(set(_labels)) == 1 and _labels[0] else ""
            _imgp = doc.add_paragraph()
            _imgp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            # 🔴 图片与图注不能分页（图上、图名在下一页）
            try:
                _imgp.paragraph_format.keep_with_next = True
                _imgp.paragraph_format.keep_together = True
            except Exception:
                pass
            _capp = doc.add_paragraph()
            _capp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if len(row) > 1 and not _merged:
                for _tp, _ in _TABS:
                    _imgp.paragraph_format.tab_stops.add_tab_stop(_tp, WD_TAB_ALIGNMENT.CENTER)
                    _capp.paragraph_format.tab_stops.add_tab_stop(_tp, WD_TAB_ALIGNMENT.CENTER)
            for j, (path, label) in enumerate(row):
                try:
                    _tmpf, _w, _h = _prep(path, label)
                    _run = _imgp.add_run('\t' if (len(row) > 1 and not _merged) else '')
                    _run.add_picture(_tmpf, width=Cm(_w), height=Cm(_h))
                    try:
                        os.unlink(_tmpf)
                    except OSError:
                        pass
                except Exception as _e:
                    logger.warning(f"图片渲染失败 {path}: {_e}")
                if _merged:
                    continue
                _rc = _capp.add_run(('\t' if len(row) > 1 else '') + self._next_caption(label))
                _rc.font.name = FONT_CAPTION; _rc._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_CAPTION)
                _rc.font.size = Pt(12)
            if _merged:
                _rc = _capp.add_run(self._next_caption(_merged))
                _rc.font.name = FONT_CAPTION; _rc._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_CAPTION)
                _rc.font.size = Pt(12)

    @staticmethod
    def dedupe_duplicate_images(doc) -> int:
        """🔴 全局兜底：同一张图（同一 media）只保留首次出现，重复的整段删除。

        上面两条路径（`_add_image` 的内容指纹、`_add_image_row_pair` 的登记）
        都是在**插入时**去重，只能挡住「同一进程内」的重复。模板整块拷贝
        （评审表区间）走的是原始 XML，绕开全部去重逻辑 —— 实测 124 张图被引用 277 次。
        这里在装配收尾时按 `r:embed` 再扫一遍，把漏网的清掉。

        Returns: 删除的图片数。
        """
        from docx.oxml.ns import qn as _qn
        body = doc.element.body
        rels = doc.part.rels

        def _media_of(el) -> str:
            rids = [b.get(_qn('r:embed')) for b in el.iter(_qn('a:blip'))]
            out = []
            for rid in rids:
                if not rid:
                    continue
                try:
                    out.append(str(rels[rid].target_ref))
                except Exception:
                    out.append(str(rid))
            return out

        def _is_caption(p) -> bool:
            """图注段：短文本且以「图X-X」开头，且自身不含图片。"""
            if list(p.iter(_qn('a:blip'))):
                return False
            txt = ''.join(t.text or '' for t in p.iter(_qn('w:t'))).strip()
            return bool(re.match(r'^图\s*\d+\s*[-－]\s*\d+', txt)) and len(txt) < 80

        seen = set()
        removed = 0
        kids = list(body.iterchildren())
        for i, el in enumerate(kids):
            if el.tag != _qn('w:p'):
                continue
            # 🔴 签字行：占位图是同一张「(此处签名)」，按媒体去重会把整段删掉 ——
            #    实测 22 人的签字区只剩第一个占位的人。这类段落一律跳过。
            if _is_signature_paragraph(el):
                continue
            media = _media_of(el)
            if not media:
                continue
            # 该段所有图片都出现过 → 整段是重复插图，删段（含紧随其后的图注）
            if all(m in seen for m in media):
                nxt = kids[i + 1] if i + 1 < len(kids) else None
                if nxt is not None and nxt.tag == _qn('w:p') and _is_caption(nxt):
                    nxt.getparent().remove(nxt)
                el.getparent().remove(el)
                removed += len(media)
                continue
            for m in media:
                seen.add(m)
        if removed:
            logger.info(f"[图片去重] 删除 {removed} 处重复插图")
        return removed

    @staticmethod
    def ensure_all_tables_bordered(doc) -> int:
        """给文档内**所有**表格补上细黑色实线边框（全局，无例外）。

        Word 对「无边框表格」会画**虚线网格**（编辑视图里的浅色虚线），
        用户明确要求全篇不得出现虚线 → 装配完成后统一兜这一道，
        任何来源的表格（模板拷贝/自建/注入）都保证是实线框。
        """
        from docx.oxml import OxmlElement

        def _bd(tag):
            e = OxmlElement(f'w:{tag}')
            e.set(qn('w:val'), 'single'); e.set(qn('w:sz'), '4')
            e.set(qn('w:space'), '0'); e.set(qn('w:color'), '000000')
            return e

        n = 0
        for t in doc.tables:
            tblPr = t._tbl.tblPr
            if tblPr is None:
                continue
            # 🔴 参与人员签名表**不画边框**（它不是表格，是签名位）
            if tblPr.get(qn('w:tblCaption')) == "_signature":
                continue
            for old in tblPr.findall(qn('w:tblBorders')):
                tblPr.remove(old)
            borders = OxmlElement('w:tblBorders')
            for tag in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                borders.append(_bd(tag))
            # 🔴 必须**按 schema 顺序**插入：`w:tblBorders` 要排在
            #    `shd / tblLayout / tblCellMar / tblLook` **之前**。
            #    早先是 `tblPr.append(borders)` —— 挂到了 tblLook 后面，整个 tblPr
            #    子元素顺序非法。宽松的渲染器照画边框，严格的一看就整块忽略 tblPr：
            #    连表格里设好的**固定列宽（tblW/tblLayout）一起丢掉** → 报告表列宽
            #    被 Word 按内容自动重排（业主：「报告表还是有问题」）。实测 2026-10-12。
            tblPr.insert_element_before(
                borders, 'w:shd', 'w:tblLayout', 'w:tblCellMar', 'w:tblLook',
                'w:tblCaption', 'w:tblDescription')
            n += 1
        return n

    def _prepare_image_catalog(self, state: dict) -> dict:
        """按用户上传图 + 固定模板图，建好**本章用哪张图**的映射，返回类别→路径池。

        🔴 完整报告（`assemble`）与**单章导出**（`scripts/export_chapter.py`）必须走同一条路：
           以前这段逻辑只长在 `assemble()` 里，单章导出拿不到 `_chapter_image_map`，
           于是「现场公示 / 实地勘察 / 座谈会的照片」在单章评审稿里**一张都不出现**，
           正文全是干文字。提到方法里，两边共用。
        """
        # 🔴 Use image catalog for chapter assignments (single source of truth)
        from app.services.image_catalog import build_image_catalog
        # 🔴 图片只用「用户前台上传的图片」+「公司人员/营业执照等固定资质」（后者单独加载）。
        #    不再扫描 PDF 抽取图(storage/images/pdf_*)或 storage 目录散图，避免附件图片泛滥成几百页。
        # 🔴 必须传 **dict（含 original_name）**，不能只传 path——
        #    存储名是 uuid，丢了原始文件名就再也判断不出「公示/现场/座谈」归属，
        #    图片只能全部落到附件（这正是「正文没有图片」的直接原因之一）。
        uploaded_items = []
        for item in (state.get("_uploaded_files", []) or []):
            if isinstance(item, str):
                _p = item
                _alt = Path(str(self.storage_dir)) / _p
                uploaded_items.append(str(_alt) if _alt.exists() else _p)
            elif isinstance(item, dict) and item.get("path"):
                # 🔴 **必须把相对路径解析成真实存在的绝对路径**：上传时文件按
                #    `<原名>_<hash>.jpg` 落盘，而 `original_name` 是没带 hash 的原名 ——
                #    图片目录内部的回退用的是 `storage/images/<原名>`，**永远对不上**，
                #    于是**所有直接上传的图片被整张丢掉**（实测 2026-10-12：上传
                #    「现状图.jpg」「勘测定界图_00.jpg」「主体项…红线_图纸.png」后重跑，
                #    目录里一张都没有，media 数一个没变）。
                #    PDF 抽图（`pdf_*`）恰好「存储名 == 原名」，所以一直没暴露。
                _p = str(item["path"])
                if not os.path.exists(_p):
                    _alt = Path(str(self.storage_dir)) / _p
                    if _alt.exists():
                        _p = str(_alt)
                uploaded_items.append({"path": _p,
                                       "original_name": item.get("original_name")
                                                        or os.path.basename(str(item["path"]))})

        # 🔴 复垦：报告里的位置示意图/损毁预测图等是**系统按材料画出来的**，不是用户上传的
        #    （样本里的图就是画的）。在此之前这段逻辑只活在开发机上的独立脚本里，装配器
        #    完全不知道 —— 于是复垦十章正文一张图都没有（实测 2026-10-10 media=0）。
        #    生成的图带「图名」注册进来，走图片目录既有的**按名绑定**落到对应章。
        if self._domain == "reclamation":
            try:
                from app.config import settings as _settings
                from app.services.reclamation_figures import ensure_reclamation_figures
                # 🔴 红线常以 **Shapefile** 上传：`_project_materials` 只收文本可解析的
                #    材料，`.shp` 不在里面 → 界址点找不到、卫星图退化成白底几何图。
                #    这里把上传清单里的 `.shp` 也并进材料列表（`collect_survey_polygons`
                #    只读 source_path/source_name）。
                _mats = list(state.get("_project_materials") or [])
                _have = {str((m or {}).get("source_path") or "")
                         for m in _mats if isinstance(m, dict)}
                for _f in (state.get("_uploaded_files") or []):
                    _p = _f.get("path") if isinstance(_f, dict) else _f
                    if str(_p).lower().endswith(".shp") and str(_p) not in _have:
                        _mats.append({"source_name": os.path.basename(str(_p)),
                                      "source_path": str(_p)})
                _gen = ensure_reclamation_figures(
                    _mats, _settings.STORAGE_DIR,
                    filled=state.get("filled_data") or {})
                # 🔴 系统生成的图（图3-1 位置示意图，带行政区位/五角星/比例尺/指北针）
                #    排在用户上传图**前面**：同一图题下优先用生成的，避免人工稿里
                #    那张缺图件要素的旧图顶掉新图（2026-11-04 业主实测）。
                uploaded_items = list(_gen) + uploaded_items
                # 🔴 图6-1 临时拦挡措施断面示意图（业主 2026-11-05 批注：
                #    「涉及到表土剥离就放入这个图片」）。内置一张兜底；
                #    用户上传了同名图（名字带「拦挡/断面」）就改用用户那张。
                try:
                    _fd6 = state.get("filled_data") or {}
                    _rows6 = _fd6.get("_quantity_rows") or []
                    _has_strip = any(
                        ("剥离" in str((r or {}).get("name") or ""))
                        and float((r or {}).get("qty") or 0) > 0
                        for r in _rows6) if isinstance(_rows6, list) else False
                    _user_section = any(
                        ("拦挡" in str((u or {}).get("name") or ""))
                        or ("拦挡" in str((u or {}).get("original_name") or ""))
                        for u in uploaded_items if isinstance(u, dict))
                    _builtin = (Path(str(_settings.STORAGE_DIR)) / "images"
                                / "reclamation" / "图6-1_临时拦挡措施断面示意图.png")
                    if _has_strip and not _user_section and _builtin.exists():
                        uploaded_items.append({
                            "path": str(_builtin),
                            "name": _builtin.name,
                            "label": "临时拦挡措施断面示意图",
                        })
                        logger.info("[图6-1] 涉及表土剥离，内置拦挡断面图已加入配图")
                except Exception as _e6:
                    logger.warning(f"[图6-1] 内置图加入失败（不影响出报告）: {_e6}")
                # 🔴 系统生成的图**不参与「竖版地图转 90° 横放」那条启发式**
                #    （那是给竖版扫描件用的；我们的图3-1 是真南北向竖图）——见 `_prep`。
                self._no_rotate_names = {os.path.basename(str(g.get("path") or ""))
                                         for g in _gen if g.get("path")}
            except Exception as _fe:
                logger.warning(f"[复垦图件] 生成失败，跳过（不影响出报告）: {_fe}")

        img_catalog = build_image_catalog(uploaded_items,
                                          ai_classifications=state.get("_classified_images"),
                                          domain=self._domain)        # 🔴 图片**按类别各自限量**，不做「全局优先级截断」。
        #    旧做法把全部图按 map>announcement>photo>… 排序截断到 N 张，
        #    结果公示/现场/座谈照片（photo）被公告与 PDF 内嵌图挤掉，正文一张图都没有。
        #    现在每类独立配额，谁也挤不掉谁；正文图与附件图分别配额。
        try:
            # 🔴 业主口径（2026-09-17）：「每个类型挑两张，剩余的放在附件」。
            #    配额按 **(类型, 标签)** 逐类计 —— 所以「公示照片 / 现场勘察照片 /
            #    调查表 / 群众座谈会照片」各自最多 2 张进正文。
            #    没被选中的**不会丢**：附件目录用的是完整 catalog，与正文配额相互独立。
            _BODY_QUOTA = {"map": 2, "announcement": 2, "photo": 2, "meeting": 2,
                           "survey": 2, "sentiment": 2, "review": 2, "opinion": 2, "planning": 2}
            _DEFAULT_BODY_QUOTA = 2
            _by_ch = img_catalog.get("by_chapter", {}) or {}
            # 🔴 系统生成的图3-1（卫星底图+红线）必须先占住第3章的位置图名额。
            #    图片目录的正文配额是「每类最多 2 张、按输入顺序取」——用户上传的
            #    原始红线渲染排在前面，两张就把名额吃光，卫星图被挤掉，第3章只剩
            #    白底红线图（2026-11-03 线上实测）。这里先把原始渲染从**正文**摘掉，
            #    它们仍留在 `catalog` 里，照常进附件17。
            _fig_ok = bool(((state.get("filled_data") or {})
                            .get("_fig_location_generated")))
            if self._domain == "reclamation" and _fig_ok:
                _dropped = drop_raw_redline_from_ch3(_by_ch)
                if _dropped:
                    logger.info(f"[配图] 位置图名额让给系统卫星图，正文剔除原始红线 "
                                f"{_dropped} 张（仍进附件17）")
            _total_before = sum(len(v) for v in _by_ch.values())
            for _ch in list(_by_ch.keys()):
                _per_label = {}
                _kept = []
                for _it in _by_ch.get(_ch, []):
                    _lb = str(_it.get("label") or _it.get("type") or "other")
                    _q = _BODY_QUOTA.get(str(_it.get("type") or ""), _DEFAULT_BODY_QUOTA)
                    if _per_label.get(_lb, 0) < _q:
                        _per_label[_lb] = _per_label.get(_lb, 0) + 1
                        _kept.append(_it)
                _by_ch[_ch] = _kept
            _total_after = sum(len(v) for v in _by_ch.values())
            if _total_before != _total_after:
                logger.info(f"[IMG] 正文图按类别限量：{_total_before} → {_total_after}")

            # 附件图总量兜底（防止 PDF 抽取图再次把报告撑到几百页）
            _MAX_APPENDIX = int(os.environ.get("REPORT_MAX_APPENDIX_IMAGES", "120"))
            _cat = img_catalog.get("catalog", []) or []
            if len(_cat) > _MAX_APPENDIX:
                _prio = {"map": 0, "announcement": 1, "photo": 2, "meeting": 3, "survey": 4, "other": 9}
                _kept_cat = sorted(_cat, key=lambda x: _prio.get(x.get("category", "other"), 9))[:_MAX_APPENDIX]
                img_catalog["catalog"] = _kept_cat
                logger.info(f"[IMG] 附件图封顶：{len(_cat)} → {len(_kept_cat)}")
        except Exception as _ie:
            logger.warning(f"图片限量失败: {_ie}")
        self._chapter_image_map = img_catalog.get("by_chapter", {})
        # 🔴 原先这里把 asset_images 里的固定流程图注入**第4章**，但：
        #    ① 参照件模板**第四章通篇无图**，流程图在**第七章**（表7-1 那张「社会稳定
        #       风险评估流程图」）；
        #    ② 那张 DB 固定图与第七章的流程图为**同一张**（同样九个框），
        #       注入第4章等于同一张图在报告里出现两次（业主：「图4-1 和 7-1 是不是重复了」）。
        #    故不再注入 —— 流程图只随第七章走。
        self._unmatched_image_markers = []  # 🔴 图注匹配不到图片的标记（供审核感知）
        # 🔴 Full image pool: category → [{path, name, category}, ...] for appendix
        image_files = {}
        for img in img_catalog.get("catalog", []):
            cat = img["category"]
            if cat not in image_files:
                image_files[cat] = []
            image_files[cat].append(img["path"])
        # Also store full catalog for appendix
        self._img_catalog_data = img_catalog
        return image_files

    def _load_fixed_flowchart(self):
        """从 asset_images 读取固定模板图「本决策风险评估流程图」，落地为文件并返回路径。"""
        try:
            import sqlite3
            from app.config import settings
            db_path = settings.DATA_DIR / "knowledge_base.db"
            if not db_path.exists():
                return None
            conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT image_data, mime_type FROM asset_images "
                "WHERE is_active=1 AND (category='决策流程图' OR image_name LIKE '%流程图%') "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            conn.close()
            if not row or row["image_data"] is None:
                return None
            ext = 'png' if 'png' in (row["mime_type"] or '') else 'jpg'
            out = self.images_dir / f"fixed_risk_flowchart.{ext}"
            if not out.exists():
                self.images_dir.mkdir(parents=True, exist_ok=True)
                data = row["image_data"]
                out.write_bytes(bytes(data) if not isinstance(data, (bytes, bytearray)) else data)
            return str(out)
        except Exception as e:
            logger.warning(f"固定流程图读取失败: {e}")
            return None

    def _load_company_certificate_images(self, domain: str = "stability") -> List[tuple]:
        """Load company cert images. Stability: template-extracted. Bidding: DB."""
        import sqlite3
        from app.config import settings

        results = []

        if uses_company_archive_images(domain):
            # 🔴 这些 `stability_cert_*` 是**我们自己**的营业执照 / 稳评平台备案 / 资质图
            #    （固定文件，仓库里没有生产者）。**只有内部公司的报告才能用** ——
            #    否则外部用户出的报告会贴上我们的营业执照（实测就是只差这一步）。
            #    外部用户走「公司信息」页上传自己的资质图（见 routers/company.py）。
            if not (getattr(self, "_company", None) or {}).get("is_internal"):
                return []
            # 🔴 Stability: scan extracted_imgs for stability_* files (from template extraction)
            # 🔴 先把**个人证书**扫描件摘出去：`stability_cert_02/03/04` 其实是
            #    刘利伟 / 安如月 / 罗娜的稳评培训证书，不是机构资质。以前不看索引一并塞进
            #    「公司资质证书」区、还按顺序编成「资质证书 3/4/5」，结果前置页贴的证书
            #    与「稳评工作组人员及分工情况」名单**完全是两拨人**（业主：要一致）。
            #    个人证书一律走 `_selected_personnel_cert_images()`，跟着勾选的人走。
            # 🔴 判据原来**是死代码**：索引里存的是**相对路径**
            #    （`storage/extracted_imgs/stability_cert_02.jpeg`），而这里拿
            #    `str(f)`（绝对路径）去比，永远不相等 —— 于是 02/03/04 一直被当成
            #    机构资质贴进了「公司资质证书」区（复垦附件3 实测也是同样三张）。
            #    同时比「解析后的绝对路径」与「文件名」：前者精确，后者不受 cwd 影响。
            _person_paths, _person_names = set(), set()
            try:
                from app.services.personnel_certs import load_index as _load_cert_index
                for _v in (_load_cert_index() or {}).values():
                    if not _v.get("is_person_cert"):
                        continue
                    _pp = str(_v.get("path") or "")
                    if _pp:
                        _person_paths.add(str(Path(_pp).resolve()))
                        _person_names.add(Path(_pp).name)
            except Exception:
                pass
            for f in sorted(self.company_dir.iterdir()):
                if not f.name.startswith('stability_cert_'): continue
                if f.suffix.lower() not in ('.jpg','.jpeg','.png','.gif','.bmp'): continue
                if str(f.resolve()) in _person_paths or f.name in _person_names: continue
                # 🔴 图注按实际内容给 —— 别再输出「资质证书 3」这种按序号编的名字
                if '00' in f.stem: cap = '公司营业执照'
                elif '01' in f.stem: cap = '稳评平台备案'
                else: cap = f.stem.replace('stability_cert_','').replace('_',' ')
                results.append((str(f), cap))
        else:
            # 🔴 Bidding: DB asset_images
            try:
                db_path = settings.DATA_DIR / "knowledge_base.db"
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute(
                        "SELECT id, image_name, image_data, mime_type FROM asset_images "
                        "WHERE is_active=1 AND category IN ('营业执照','资质证书','人员证书',"
                        "'财务报告','社保纳税','法人证明') ORDER BY id LIMIT 12"
                    )
                    for row in cur.fetchall():
                        img_data = row['image_data']
                        if not img_data: continue
                        mime = row['mime_type'] or 'image/png'
                        ext = 'png' if 'png' in mime else 'jpg'
                        safe_name = f"cert_db_{row['id']}.{ext}"
                        filepath = self.images_dir / safe_name
                        if not filepath.exists():
                            if isinstance(img_data, str):
                                import base64
                                try: filepath.write_bytes(base64.b64decode(img_data))
                                except: filepath.write_bytes(img_data.encode('latin-1'))
                            else: filepath.write_bytes(bytes(img_data))
                        results.append((f"images/{safe_name}", row['image_name'][:60]))
                    conn.close()
            except Exception as e:
                logger.warning(f"DB cert load failed: {e}")

        return results

    # ═══════════════════════════════════════════════════════════════
    # Data Extraction from State
    # ═══════════════════════════════════════════════════════════════

    def _get_session_images(self, state: dict) -> Dict[str, List[str]]:
        images = {"survey": [], "announcement": [], "photo": [], "review": [], "map": [], "other": []}
        all_image_paths = []

        uploaded = state.get("_uploaded_files", []) or []
        # Track original filenames for categorization
        original_names = {}  # path -> original_name
        for item in uploaded:
            if isinstance(item, dict):
                path = item.get("path", "")
                oname = item.get("original_name", "")
                if path:
                    all_image_paths.append(path)
                    original_names[path] = oname
            elif isinstance(item, str):
                all_image_paths.append(item)
        state["_image_original_names"] = original_names

        material_summary = state.get("_project_material_summary", {})
        if isinstance(material_summary, dict):
            facts = material_summary.get("facts", {})
            extracted = facts.get("_extracted_images", [])
            if isinstance(extracted, list):
                for img in extracted:
                    if img not in all_image_paths:
                        all_image_paths.append(img)

        filled = state.get("filled_data", {})
        filled_imgs = filled.get("_extracted_images", [])
        if isinstance(filled_imgs, list):
            for img in filled_imgs:
                if img not in all_image_paths:
                    all_image_paths.append(img)

        # 🔴 Pull in classified chapter images from DataAnalysisAgent
        chapter_images = state.get("_chapter_images", {})
        chapter_image_map = {}  # ch_num → list of image paths
        for ch_str, img_list in (chapter_images or {}).items():
            try:
                ch_num = int(ch_str)
            except (ValueError, TypeError):
                ch_num = 999  # "other" → appendix
            chapter_image_map[ch_num] = []
            for img_info in img_list:
                if isinstance(img_info, dict):
                    path = img_info.get("path", "")
                    if path and path not in all_image_paths:
                        all_image_paths.append(path)
                    chapter_image_map[ch_num].append(path)
                elif isinstance(img_info, str):
                    if img_info not in all_image_paths:
                        all_image_paths.append(img_info)
                    chapter_image_map[ch_num].append(img_info)
        # Store chapter-image mapping for later use
        state["_chapter_image_map"] = chapter_image_map

        # Also pull scene photo placements
        scene_placements = state.get("_scene_photo_placements", [])
        for p in scene_placements:
            if isinstance(p, dict):
                path = p.get("path", "")
                if path and path not in all_image_paths:
                    all_image_paths.append(path)

        # 🔴 Fallback: if session has few images, scan storage for relevant files
        if len(all_image_paths) < 10:
            import glob as _glob
            storage_patterns = [
                (str(self.images_dir / '*.jpg'), 'photo'),
                (str(self.images_dir / '*.png'), 'photo'),
                (str(self.images_dir / '*.jpeg'), 'photo'),
                # Fallback: scan parent storage dir too
                (str(self.storage_dir / '*.jpg'), 'photo'),
                (str(self.storage_dir / '*.png'), 'photo'),
                (str(self.storage_dir / '*.jpeg'), 'photo'),
            ]
            for pattern, default_cat in storage_patterns:
                for f in _glob.glob(pattern):
                    if f not in all_image_paths:
                        fname = f.split('/')[-1]
                        # Skip PDF renders and tiny files
                        if 'pdf_' in fname or os.path.getsize(f) < 5000:
                            continue
                        all_image_paths.append(f)
                        # Use original filename for classification
                        original_names[f] = fname

        for fpath in all_image_paths:
            if not isinstance(fpath, str):
                continue
            fname = fpath.split('/')[-1] if '/' in fpath else fpath
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext not in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'):
                continue

            # Use original filename for classification if available
            classify_name = original_names.get(fpath, fname)
            name_lower = classify_name.lower()
            # 🔴 Exclude raw PDF page renders (pdf_ prefix) — they're for OCR, not report images
            if name_lower.startswith('pdf_'):
                continue
            if any(k in name_lower for k in ['问卷', '调查', '统计', '签到']):
                images['survey'].append(fpath)
            elif any(k in name_lower for k in ['公告', '公示', '批文', '通知', '征收', '预公告']):
                images['announcement'].append(fpath)
            elif any(k in name_lower for k in ['评审', '意见', '专家签字', '专家']):
                images['review'].append(fpath)
            elif any(k in name_lower for k in ['地图', '红线', '规划', '位置图', '勘测', '勘界', '测定', '地形', '宗地', '示意']):
                images['map'].append(fpath)
            elif any(k in name_lower for k in ['照片', '现场', '座谈', '走访', '会议', '开会', '勘察', '村民', '临时用地', '微信图片', '开会现场']):
                images['photo'].append(fpath)
            else:
                images['other'].append(fpath)

        # 🔴 Auto-build chapter image map — each chapter picks only a few representative images
        if not state.get("_chapter_image_map"):
            ch_map = {}
            # Save a copy for appendix before consuming
            all_remaining = []
            for cat in ['map', 'announcement', 'photo', 'survey', 'review', 'other']:
                for p in images.get(cat, []):
                    fname = (original_names.get(p, p) if isinstance(original_names, dict) else p)
                    all_remaining.append({'path': p, 'caption': str(fname).split('/')[-1][:60]})

            # Helper: take up to N images from remaining pool
            def _take_from_pool(n, match_words):
                taken = []
                remaining = []
                for item in all_remaining[:]:
                    fname_lower = str(item.get('caption', '')).lower()
                    if any(kw in fname_lower for kw in match_words):
                        if len(taken) < n:
                            taken.append(item)
                            all_remaining.remove(item)
                return taken

            # Ch1: 1-2 maps only (位置示意图/红线图) — NO announcements
            ch_map[1] = _take_from_pool(2, ['勘测', '地图', '红线', '规划', '位置', '地形', '宗地', '勘界', '测定', '示意'])

            # Ch5: 2 expert review photos for risk factor identification section
            ch_map[5] = _take_from_pool(2, ['专家', '评审', '意见', '评估', '风险', '签字', '盖章'])

            # Ch3: 2-3 site/meeting photos + 1 notice
            ch_map[3] = _take_from_pool(3, ['现场', '勘察', '座谈', '走访', '会议', '照片', '公示', '村民']) + \
                        _take_from_pool(1, ['公告', '公示'])

            # Ch7: 2 photos
            ch_map[7] = _take_from_pool(2, ['现场', '勘察', '照片', '地块'])

            # Ch9: 1 expert review
            ch_map[9] = _take_from_pool(1, ['专家', '评审', '意见', '评估'])

            # Everything else → appendix (999)
            if all_remaining:
                ch_map[999] = all_remaining

            state["_chapter_image_map"] = ch_map

        return images

    def _get_survey_stats(self, state: dict) -> Dict[str, Any]:
        stats = {"total_surveys": 0, "support_count": 0, "oppose_count": 0,
                 "neutral_count": 0, "support_rate": 0}
        filled = state.get("filled_data", {})
        structured = state.get("structured_data", {})

        if filled.get("total_samples"):
            try: stats["total_surveys"] = _int_or(filled.get("total_samples"), 0)
            except: pass
        if filled.get("support_rate"):
            try: stats["support_rate"] = _num_or(filled.get("support_rate"), 0)
            except: pass
        if filled.get("support_count"):
            try: stats["support_count"] = _int_or(filled.get("support_count"), 0)
            except: pass
        if filled.get("oppose_count"):
            try: stats["oppose_count"] = _int_or(filled.get("oppose_count"), 0)
            except: pass

        step_6 = structured.get("step_6", {})
        if isinstance(step_6, dict):
            if not stats["total_surveys"]:
                stats["total_surveys"] = step_6.get("total_samples", 0)
            if not stats["support_rate"]:
                stats["support_rate"] = step_6.get("support_rate", 0)

        if stats["support_count"] and not stats["total_surveys"]:
            stats["total_surveys"] = stats["support_count"] + stats["oppose_count"] + stats["neutral_count"]
        if stats["total_surveys"] and not stats["support_rate"]:
            stats["support_rate"] = round(stats["support_count"] / stats["total_surveys"] * 100, 1)

        return stats

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _add_chapter_title_page(self, doc, cn: str, title: str,
                                chapter_number: int = None,
                                own_page: bool = None,
                                new_page: bool = None) -> None:
        """章标题页：是否独占一整页**按模板实测**决定。

        🔴 用「独立一节 + 节级垂直居中(`w:vAlign=center`)」实现，不用铺空段：
        铺空段会被 `_post_generation_fix` 的「连续空段最多 2 个」清理规则删掉，
        结果标题仍旧贴在页顶（实测铺 16 个空段后被删到只剩 2 个）。

        🔴 章标题版式各地模板不同（2026-11-23 实测）：
           洪泽/淮安区多数章独占页，但洪泽第四章正文紧跟标题；
           淮阴区第三/十章、金湖与淮安市全部章都是标题与正文同页。
        这些差异由 `template_structure.extract_plan` 从模板实测后写进
        `Chapter.own_page`，这里经 `chapter_number` 取用；取不到才按域默认。
        """
        _text = chapter_heading_text(self._domain, chapter_number, cn, title)
        # 模板级版式（按系统章号找模板里的对应章）
        _pc = None
        if chapter_number and getattr(self, "_tpl_plan", None) is not None:
            try:
                _pc = self._tpl_plan.by_sys().get(int(chapter_number))
            except Exception:
                _pc = None
        if _pc is not None:
            if own_page is None:
                own_page = _pc.own_page
            if new_page is None:
                new_page = _pc.new_page
        _default_own = bool(CHAPTER_TITLE_OWN_PAGE and chapter_title_own_page(self._domain))
        _own = _default_own if own_page is None else bool(own_page)
        _new = True if new_page is None else bool(new_page)
        if not _new:
            # 模板里该章不另起新页：标题就接在上一章正文后面
            self._add_heading(doc, _text, 1)
            return
        if not _own:
            # 复垦：章标题**不独占整页、不垂直居中**（样本里它就印在正文页顶端），
            # 但**仍起新页** —— 样本的每章都是从新页开始的。
            # 🔴 这一节还承担结构作用：`_setup_header_footer` 靠「第 2 节 = 正文首页」
            #    挂页眉页脚。不建节的话正文会留在第 1 节、被当「前置部分」清空页眉。
            #    ⚠️ `_post_generation_fix` 有「连续空段超过 2 个就删」的规则，而目录/
            #       上一章末尾常留 2~3 个空段 —— 紧挨其后的**分节段落会被一并删掉**，
            #       分节符一没整节就没了。故插分节段落前先把尾部空段清干净。
            _body = doc.element.body
            while True:
                _kids = [c for c in _body.iterchildren()
                         if c.tag.split('}')[1] in ('p', 'tbl')]
                if not _kids:
                    break
                _last = _kids[-1]
                if ''.join(t.text or '' for t in _last.iter(qn('w:t'))).strip():
                    break                                   # 有字 → 停
                # 🔴 **分页符段落不是空段**（判据只看文字时它算空 —— 第三次踩同一个坑）：
                #    目录前后各有一个 `add_page_break()` 段落，被这里啃掉 →
                #    **扉页与目录挤在同一页**（业主：「第二页又有目录又有信息」）。
                #    分页符是版式指令，遇到就停手。
                if any((b.get(qn('w:type')) or '') == 'page'
                       for b in _last.findall('.//' + qn('w:br'))):
                    break
                if _last.find(qn('w:pPr') + '/' + qn('w:sectPr')) is not None:
                    break                                   # 别动既有分节段落
                _last.getparent().remove(_last)
            _copy_page_setup(doc, self._add_section(doc))
            _set_vertical_alignment(doc.sections[-1], "top")
            self._add_heading(doc, _text, 1)
            return
        _copy_page_setup(doc, self._add_section(doc))
        _set_vertical_alignment(doc.sections[-1], "center")
        self._add_heading(doc, _text, 1)
        # 收尾：再起一节并恢复顶端对齐，正文从新页开始
        _copy_page_setup(doc, self._add_section(doc))
        _set_vertical_alignment(doc.sections[-1], "top")

    @staticmethod
    def _add_section(doc):
        """新增一节（从新页开始），并沿用上一节的纸张/页边距。"""
        from docx.enum.section import WD_SECTION
        try:
            return doc.add_section(WD_SECTION.NEW_PAGE)
        except Exception:
            return None

    def _typo(self, slot: str) -> tuple:
        """版式槽位 → `(中文字体, 字号 pt)`：模板画像优先，读不到按域常量。"""
        _d = (getattr(self, "_tpl_style", None) or {}).get(slot) or {}
        if _d.get("font") and _d.get("size"):
            return str(_d["font"]), float(_d["size"])
        return typography(getattr(self, '_domain', None), slot)

    def _heading_bold(self, level: int) -> bool:
        """该级标题是否加粗：按模板实测（井神样本标题实际**不加粗**）。

        没有模板画像时保持原行为（加粗）。
        """
        _d = (getattr(self, "_tpl_style", None) or {}).get(f"h{level}") or {}
        b = _d.get("bold")
        return True if b is None else bool(b)

    def _tpl_table_styles(self, chapter_no: int):
        """当前模板某章每张表的实际字体/字号（按表题匹配用）。"""
        cache = getattr(self, "_tpl_tbl_styles", None)
        if cache is None:
            cache = self._tpl_tbl_styles = {}
        if chapter_no in cache:
            return cache[chapter_no]
        styles = []
        tp = str((getattr(self, "_session_state", {}) or {}).get("template_path") or "")
        if tp:
            try:
                from app.services.template_table_copy import chapter_table_styles
                styles = chapter_table_styles(tp, chapter_no) or []
            except Exception as e:
                logger.info(f"[表格样式] 读取模板表样式跳过: {e}")
        cache[chapter_no] = styles
        return styles

    def _match_tpl_table(self, chapter_no: int, caption: str):
        """按表题二元组重合度找模板里的同题表。"""
        def _bg(s):
            s = re.sub(r'^表\s*\d+\s*[-—]\s*\d+\s*', '', str(s or ""))
            s = re.sub(r'\s|单位[:：].*$', '', s)
            return {s[i:i + 2] for i in range(max(0, len(s) - 1))}

        _cap = _bg(caption)
        best, score = None, 0
        for e in (self._tpl_table_styles(chapter_no) or []):
            sc = len(_cap & _bg(e.get("title")))
            if sc > score:
                best, score = e, sc
        return best if (best and score >= 4) else None

    def _table_typo_for(self, chapter_no: int, caption: str, ncols: int = 0) -> tuple:
        """按**模板里同题表**取字体字号（每张表可能不同：4-8/4-9/4-10 是 9pt）。"""
        best = self._match_tpl_table(chapter_no, caption)
        if best and best.get("font") and best.get("size"):
            return str(best["font"]), Pt(float(best["size"]))
        return self._table_typo(ncols)

    def _table_bold_for(self, chapter_no: int, caption: str) -> bool:
        """表头是否加粗：模板实际不加粗（run bold=False），没有模板时保持加粗。"""
        best = self._match_tpl_table(chapter_no, caption)
        if best is not None and best.get("bold") is not None:
            return bool(best["bold"])
        return True

    def _table_typo(self, ncols: int = 0) -> tuple:
        """表格正文字体/字号（模板实测：仿宋 五号 10.5pt；读不到用 宋体 10.4）。

        列多的表（7 列以上）自动降到 9pt、10 列以上 8pt —— 否则表头一个字一行、
        整张表被撑到多页（业主 2026-11-04 实测：表4-5 变成竖排）。
        """
        _d = (getattr(self, "_tpl_style", None) or {}).get("table") or {}
        _size = float(_d.get("size") or TABLE_SIZE.pt)
        if ncols >= 10:
            _size = min(_size, 8.0)
        elif ncols >= 7:
            _size = min(_size, 9.0)
        return str(_d.get("font") or FONT_TABLE), Pt(_size)

    def _caption_typo(self) -> tuple:
        """表题/图注字体、字号、是否加粗（模板实测：黑体 10.5pt）。"""
        _d = (getattr(self, "_tpl_style", None) or {}).get("caption") or {}
        return (str(_d.get("font") or FONT_CAPTION),
                Pt(float(_d.get("size") or 12)),
                bool(_d.get("bold")))

    def _template_ch4_plan(self):
        """当前模板第四章的表格清单（表号/表题/顺序）；无模板返回 None。"""
        if hasattr(self, "_tpl_ch4_plan"):
            return self._tpl_ch4_plan
        plan = None
        tp = str((getattr(self, "_session_state", {}) or {}).get("template_path") or "")
        if tp and getattr(self, "_domain", None) == "reclamation":
            try:
                from app.services.template_table_copy import chapter_table_plan
                plan = chapter_table_plan(tp, 4) or None
            except Exception as e:
                logger.info(f"[表4-x] 模板表格清单读取跳过: {e}")
        self._tpl_ch4_plan = plan
        return plan

    def _template_ch4_no(self, *keywords) -> int:
        """模板第四章里标题含全部关键词的表号；找不到返回 0。"""
        for e in (self._template_ch4_plan() or []):
            t = re.sub(r'\s+', '', str(e.get("title") or ""))
            if keywords and all(k in t for k in keywords):
                return int(e.get("no") or 0)
        return 0

    def _template_allowed_sections(self, chapter_no: int):
        """模板画像里本章允许的小节编号集合；没有模板/画像返回 None。"""
        cache = getattr(self, "_tpl_sec_cache", None)
        if cache is None:
            cache = self._tpl_sec_cache = {}
        if chapter_no in cache:
            return cache[chapter_no]
        allowed = None
        tp = str((getattr(self, "_session_state", {}) or {}).get("template_path") or "")
        if tp and getattr(self, "_domain", None) == "reclamation":
            try:
                from app.services.reclamation_template_profile import ensure_profile
                prof = ensure_profile(tp, domain="reclamation")
                ch = ((prof or {}).get("chapters") or {}).get(str(int(chapter_no)))
                if ch:
                    _nums = {str(s.get("no") or "").strip():
                             re.sub(r'^[.．、\s]+', '',
                                    str(s.get("title") or "")).strip()
                             for s in (ch.get("sections") or [])
                             if str(s.get("no") or "").strip()}
                    allowed = _nums or None
            except Exception as e:
                logger.info(f"[章节核对] 模板小节读取跳过: {e}")
        cache[chapter_no] = allowed
        return allowed

    def _dedup_structure_tables(self, doc) -> int:
        """删掉重复的「复垦前后结构对比」表：只保留最后一张（系统补渲那张）。

        模型有时只写「表4-3」当表题、表头是地类/复垦前，`strip_md_tables_by_caption`
        匹配不到标题，最后和系统表印成两张（2026-11-05 实测）。
        """
        body = doc.element.body
        cands = []
        for el in list(body.iterchildren()):
            if el.tag != qn('w:tbl'):
                continue
            trs = el.findall(qn('w:tr'))
            if not trs:
                continue
            hdr = "".join(n.text or "" for n in trs[0].iter(qn('w:t')))
            if '地类名称' in hdr and ('复垦前' in hdr or '复垦后' in hdr):
                cands.append(el)
        if len(cands) <= 1:
            return 0
        removed = 0
        for el in cands[:-1]:
            prev = el.getprevious()
            if prev is not None and prev.tag == qn('w:p'):
                txt = "".join(n.text or "" for n in prev.iter(qn('w:t'))).strip()
                if re.match(r'^\*{0,2}表\s*4\s*[-—]\s*\d+\s*\*{0,2}$', txt):
                    body.remove(prev)
            body.remove(el)
            removed += 1
        if removed:
            logger.info(f"[表去重] 删除重复的复垦前后结构对比表 {removed} 张")
        return removed

    def _center_all_tables(self, doc) -> int:
        """全篇表格内容**上下左右居中**（业主口径：表格里不要有左对齐/顶对齐的格）。

        逐格设两层：
          水平 —— 每个单元格段落 `w:jc=center`；
          垂直 —— 每个单元格 `w:vAlign=center`（Word 里「表格属性→单元格→垂直居中」）。
        **大段文字格例外**：超过 40 字的整段话强制左对齐（复垦方案报告表的
        「复垦工作计划」「实施保障措施」），只做垂直居中，不把整段话居中。
        只处理单元格内部；表题段落在 `_add_table_caption` 里已居中，不受影响。
        放在 save 前统一兜一遍，模板深拷贝进来的表、系统表、附录表都覆盖到。
        """
        n = 0

        def _do(table) -> None:
            nonlocal n
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for row in table.rows:
                for cell in row.cells:
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    # 单元格里若还嵌了表，一并居中
                    for sub in cell.tables:
                        _do(sub)
                    # 🔴 大段文字（如复垦方案报告表的「一、复垦工作计划」「二、实施保障措施」）
                    #    **左对齐**：整段话居中会像诗，业主 2026-11-05 点名不要。
                    #    阈值与 `reclamation_report_form._section_table` 的 left_align_long 一致。
                    if len(cell.text.strip()) > 40:
                        for p in cell.paragraphs:
                            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                        continue
                    for p in cell.paragraphs:
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            n += 1

        for table in doc.tables:
            _do(table)
        if n:
            logger.info(f"[表格居中] 全篇 {n} 张表已上下左右居中")
        return n

    def _apply_document_fonts(self, doc) -> None:
        """把模板的中文字体写进**文档默认样式**。

        🔴 只给 run 设字体不够：空段、域、题注、表格空单元格会落回文档默认
           （python-docx 空白模板的主题字体）。实测成稿在 Word 里显示
           「MS 明朝」—— 不是我们设的仿宋/黑体，就是默认样式没改。
        """
        body_font, body_size = self._typo("body")
        heads = {f"h{i}": self._typo(f"h{i}") for i in (1, 2, 3)}
        tbl_font = self._table_typo()[0]
        tbl_size_pt = self._table_typo()[1].pt

        def _set_rfonts(rpr, east: str) -> None:
            rf = rpr.find(qn('w:rFonts'))
            if rf is None:
                rf = OxmlElement('w:rFonts')
                rpr.insert(0, rf)
            for attr in ('w:asciiTheme', 'w:hAnsiTheme', 'w:eastAsiaTheme', 'w:cstheme'):
                if rf.get(qn(attr)) is not None:
                    del rf.attrib[qn(attr)]
            rf.set(qn('w:ascii'), 'Times New Roman')
            rf.set(qn('w:hAnsi'), 'Times New Roman')
            rf.set(qn('w:cs'), 'Times New Roman')
            rf.set(qn('w:eastAsia'), east)

        # ① 文档默认（docDefaults）
        try:
            dd = doc.styles.element.find(qn('w:docDefaults'))
            if dd is not None:
                rpr_d = dd.find(qn('w:rPrDefault'))
                if rpr_d is not None:
                    rpr = rpr_d.find(qn('w:rPr'))
                    if rpr is None:
                        rpr = OxmlElement('w:rPr'); rpr_d.append(rpr)
                    _set_rfonts(rpr, body_font)
        except Exception as e:
            logger.info(f"[版式] 文档默认字体设置跳过: {e}")

        # ② 命名样式
        def _style(name, font, size, bold=None):
            try:
                st = doc.styles[name]
            except KeyError:
                return
            rpr = st.element.get_or_add_rPr()
            _set_rfonts(rpr, font)
            if size:
                st.font.size = Pt(float(size))
            if bold is not None:
                st.font.bold = bold

        _style('Normal', body_font, body_size)
        for key, name in (("h1", 'Heading 1'), ("h2", 'Heading 2'), ("h3", 'Heading 3')):
            font, size = heads[key]
            _style(name, font, size, self._heading_bold(int(key[1])))
        _style('Table Grid', tbl_font, tbl_size_pt)

    def _fit_wide_table(self, table, header, rows) -> None:
        """宽表按内容长度分列宽（7 列以上），并收紧单元格边距。

        python-docx 新建表是**等宽列**：7 列以上的表里「一级影响因素」这种长表头
        在 1.9cm 的格子里只能一个字一行，整张表被撑成竖排、跨好几页
        （业主 2026-11-04 实测表4-5）。这里按「表头/前几行最长文本」分配列宽。
        """
        n = len(table.columns)
        if n < 7:
            return
        usable_cm = 14.6
        lens = []
        for i in range(n):
            vals = [str(header[i]) if i < len(header) else ""]
            for r in (rows or [])[:8]:
                vals.append(str(r[i]) if i < len(r) else "")
            lens.append(max(4, min(18, max(len(v) for v in vals))))
        total = float(sum(lens)) or 1.0
        widths = [max(1.1, usable_cm * v / total) for v in lens]
        k = usable_cm / sum(widths)
        widths = [w * k for w in widths]

        table.autofit = False
        tblPr = table._tbl.tblPr
        lay = tblPr.find(qn('w:tblLayout'))
        if lay is None:
            lay = OxmlElement('w:tblLayout')
            tblPr.insert_element_before(
                lay, 'w:tblCellMar', 'w:tblLook', 'w:tblCaption', 'w:tblDescription')
        lay.set(qn('w:type'), 'fixed')
        for i, w in enumerate(widths):
            table.columns[i].width = Cm(w)

        old = tblPr.find(qn('w:tblCellMar'))
        if old is not None:
            tblPr.remove(old)
        mar = OxmlElement('w:tblCellMar')
        for side, cm in (("top", 0), ("left", 0.05), ("bottom", 0), ("right", 0.05)):
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:w'), str(int(round(cm / 2.54 * 1440))))
            el.set(qn('w:type'), 'dxa')
            mar.append(el)
        tblPr.insert_element_before(mar, 'w:tblLook', 'w:tblCaption', 'w:tblDescription')

    def _apply_tpl_table_style(self, table) -> None:
        """按模板画像给表格套**边框、对齐、单元格边距、表头底纹、行高**。

        表格不只是字体：模板可能是 1pt 粗框、无内框、带灰色表头，也可能是全无框线。
        没有模板画像时不动（沿用 `Table Grid` 的 0.5pt 单线）。
        """
        st = getattr(self, "_tpl_style", None) or {}
        if not st:
            return
        tblPr = table._tbl.tblPr
        if tblPr is None:
            return

        # ── 边框（外框 + 内框）──
        b = st.get("table_border") or {}
        if b:
            old = tblPr.find(qn('w:tblBorders'))
            if old is not None:
                tblPr.remove(old)
            bd = OxmlElement('w:tblBorders')
            none = bool(b.get("none"))
            val = "nil" if none else str(b.get("val") or "single")
            sz = str(max(2, int(round(float(b.get("size_pt") or 0.5) * 8))))
            color = str(b.get("color") or "000000")
            for side in ("top", "left", "bottom", "right"):
                el = OxmlElement(f'w:{side}')
                el.set(qn('w:val'), val); el.set(qn('w:sz'), sz)
                el.set(qn('w:space'), '0'); el.set(qn('w:color'), color)
                bd.append(el)
            for side in ("insideH", "insideV"):
                el = OxmlElement(f'w:{side}')
                if none or not b.get("inside", True):
                    el.set(qn('w:val'), 'nil')
                else:
                    el.set(qn('w:val'), val); el.set(qn('w:sz'), sz)
                    el.set(qn('w:space'), '0'); el.set(qn('w:color'), color)
                bd.append(el)
            tblPr.insert_element_before(
                bd, 'w:shd', 'w:tblLayout', 'w:tblCellMar', 'w:tblLook',
                'w:tblCaption', 'w:tblDescription')

        lay = st.get("table_layout") or {}
        # ── 表格整体对齐 ──
        a = str(lay.get("align") or "")
        if a in ("center", "left", "right"):
            old = tblPr.find(qn('w:jc'))
            if old is not None:
                tblPr.remove(old)
            jc = OxmlElement('w:jc'); jc.set(qn('w:val'), a)
            tblPr.insert_element_before(
                jc, 'w:tblCellSpacing', 'w:tblInd', 'w:tblBorders', 'w:shd',
                'w:tblLayout', 'w:tblCellMar', 'w:tblLook')

        # ── 单元格边距 ──
        m = lay.get("cell_margin_cm") or {}
        if m:
            old = tblPr.find(qn('w:tblCellMar'))
            if old is not None:
                tblPr.remove(old)
            mar = OxmlElement('w:tblCellMar')
            for side in ("top", "left", "bottom", "right"):
                el = OxmlElement(f'w:{side}')
                el.set(qn('w:w'), str(int(round(float(m.get(side) or 0) / 2.54 * 1440))))
                el.set(qn('w:type'), 'dxa')
                mar.append(el)
            tblPr.insert_element_before(
                mar, 'w:tblLook', 'w:tblCaption', 'w:tblDescription')

        # ── 表头底纹 ──
        shade = str(lay.get("header_shading") or "")
        if shade and table.rows:
            for cell in table.rows[0].cells:
                tcPr = cell._tc.get_or_add_tcPr()
                old = tcPr.find(qn('w:shd'))
                if old is not None:
                    tcPr.remove(old)
                shd = OxmlElement('w:shd')
                shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto')
                shd.set(qn('w:fill'), shade)
                tcPr.append(shd)

        # ── 行高（atLeast：只保证最小高度，内容多时可撑开，不裁字）──
        h = lay.get("row_height_cm")
        if h:
            for row in table.rows:
                trPr = row._tr.get_or_add_trPr()
                old = trPr.find(qn('w:trHeight'))
                if old is not None:
                    trPr.remove(old)
                trh = OxmlElement('w:trHeight')
                trh.set(qn('w:val'), str(int(round(float(h) / 2.54 * 1440))))
                trh.set(qn('w:hRule'), 'atLeast')
                trPr.append(trh)

    def _add_heading(self, doc, text, level):
        h = doc.add_heading(text, level=level)
        # 🔴 版式按域取（见 `chapter_catalog.typography`）：
        #    稳评 1级 黑体 22pt(二号) 加粗 居中（业主选型）/ 2级 宋体 15pt 左 / 3级 宋体 14pt 左
        #     —— 与改动前逐字相同；
        #    复垦 1级 黑体 16pt 居中 / 2级 黑体 15pt 左 / 3级 黑体 14pt 左
        #     —— 四份样本实测一致（一级不是 22pt，二三级不是宋体）。
        _ea, _pt = self._typo(f"h{level}")
        align = WD_ALIGN_PARAGRAPH.CENTER if level == 1 else WD_ALIGN_PARAGRAPH.LEFT
        ea, size, bold = _ea, Pt(_pt), self._heading_bold(level)
        h.alignment = align
        # 标题段不留首行缩进（正文才有）
        try:
            h.paragraph_format.first_line_indent = Cm(0)
        except Exception:
            pass
        for run in h.runs:
            _set_run_font(run, ea, size=size, bold=bold, ascii_font=FONT_ASCII)
            run.font.color.rgb = RGBColor(0, 0, 0)  # 强制黑色，覆盖 Word 默认蓝色

    def _add_para(self, doc, text, bold=False, indent=False, font=None, size=None):
        _txt_len = len(str(text or ""))
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY  # 🔴 正文两端对齐
        p.paragraph_format.space_after = Pt(4)
        if indent:
            p.paragraph_format.first_line_indent = Cm(0.74)
        r = p.add_run(text)
        # 🔴 正文字体按域取：稳评 宋体（逐字等于改动前）/ 复垦 **仿宋**（四份样本一致）。
        #    显式传 `font` 的调用点（目录 / 编制说明用宋体）不受影响。
        _df, _dp = self._typo("body")
        _set_run_font(r, font or _df, size=size if size is not None else Pt(_dp),
                      bold=bold, ascii_font=FONT_ASCII)

        # 🔴 图落位：本节已经有正文了（不再把图顶在小节标题下）；
        #    若有「挂起」的图，插到这段正文之后。
        if getattr(self, '_pending_section_trigger', None) and _txt_len >= 30:
            self._section_body_seen = True
        if getattr(self, '_defer_imgs', None) and _txt_len >= 30:
            self._flush_deferred_images(doc)

    def _flush_deferred_images(self, doc):
        """把「本节暂无正文」而挂起的图，插到第一个正文段之后。"""
        item = getattr(self, '_defer_imgs', None)
        if not item:
            return
        _trig, _imgs = item
        self._defer_imgs = None
        for info in _imgs:
            if isinstance(info, dict):
                _p = info.get("path", "")
                _l = info.get("label", "") or info.get("name", "") or ""
            else:
                _p, _l = str(info), ""
            if _p:
                self._add_image(doc, _p, _l or "", numbered=True)
                self._add_figure_reading(doc, getattr(self, '_current_chapter', 0),
                                         [(_p, _l)])
        logger.info(f"[落图] 小节「{_trig}」的 {len(_imgs)} 张图落在首段之后："
                    + "、".join(str(x.get('path') if isinstance(x, dict) else x) for x in _imgs))

    def _add_figure_reading(self, doc, ch_num, imgs=None):
        """**每张正文配图**后补一段带数字的解读（业主 2026-11-02：
        「每张插入的图片是否都有分析？分析地段还有分析占地面积这类的」）。

        🔴 为什么系统来写：复垦的图由装配器插入，模型写正文时并不知道图会落在哪一段，
           几乎从不主动写图后解读。这里用事实卡里的**真实数字**补：
             · 地图类（位置/红线/现状/勘界/分布）→ 地段 + 占地面积 + 主导地类及占比
             · 照片/影像 → 该范围 + 占地面积（**不编**照片里看到的东西）
           没有的数字就不写，绝不编。
        """
        if self._domain != "reclamation":
            return
        st = getattr(self, '_session_state', {}) or {}
        filled = st.get("filled_data") or {}
        m2 = _num_or(filled.get("area_m2"), 0) or _num_or(filled.get("area_mu"), 0) * 666.67
        lc = filled.get("land_classification") or {}
        rows = lc.get("rows") if isinstance(lc, dict) else []
        loc = str(filled.get("location") or "").strip()

        items = list(imgs or [])
        if not items:
            items = [""]
        # 同一批图重复调用（落图 + 挂起两条路径）只写一次
        def _pth(x):
            if isinstance(x, dict):
                return str(x.get("path") or "")
            if isinstance(x, (tuple, list)) and x:
                return str(x[0] or "")
            return str(x or "")
        _key = (int(ch_num or 0), tuple(_pth(x) for x in items))
        _done = getattr(self, '_figure_reading_done', None)
        if _done is None:
            _done = self._figure_reading_done = set()
        if _key in _done:
            return
        _done.add(_key)

        _MAP_KWS = ("位置", "红线", "现状", "勘界", "分布", "影像图")
        _PHOTO_KWS = ("照片", "影像", "现场", "微信")
        # 🔴 断面图/大样图/做法图是**工程示意**，里面没有红线、也不是现场影像。
        #    实测（2026-11-11）：图6-1 临时拦挡措施断面示意图 下面被塞了
        #    「图中红线范围为本次复垦责任范围…」，图上根本没有红线。
        _SCHEMA_KWS = ("断面", "大样", "做法", "详图", "设计图", "结构图")
        _leads = ["图中", "该图", "上图", "本图", "所附影像"]
        for _i, _it in enumerate(items):
            if isinstance(_it, dict):
                _label = str(_it.get("label") or _it.get("name") or _it.get("path") or "")
            elif isinstance(_it, (tuple, list)) and len(_it) >= 2:
                _label = str(_it[1] or _it[0] or "")
            else:
                _label = str(_it or "")
            _is_photo = any(k in _label for k in _PHOTO_KWS)
            _is_schema = any(k in _label for k in _SCHEMA_KWS)
            _lead = _leads[_i % len(_leads)]
            parts = []
            if _is_schema:
                continue          # 示意图不写红线/面积解读
            if _is_photo:
                if m2 > 0:
                    parts.append(f"{_lead}为临时用地现场影像，拍摄范围为本次复垦责任范围"
                                 + (f"（{loc}）" if loc else "")
                                 + f"，面积 {m2:,.0f} 平方米（约 {m2 / 10000:.2f} 公顷）")
            elif m2 > 0:
                parts.append(f"{_lead}红线范围为本次复垦责任范围"
                             + (f"，位于{loc}" if loc else "")
                             + f"，面积 {m2:,.0f} 平方米（约 {m2 / 10000:.2f} 公顷）")
            if isinstance(rows, list) and rows and m2 > 0:
                _r = rows[0] or {}
                _name = "".join(str(_r.get(k) or "") for k in ("一级地类", "二级地类"))
                _a = _num_or(_r.get("面积_m2"), 0)
                if _name and _a > 0:
                    parts.append(f"范围内以{_name}为主，面积 {_a:,.0f} 平方米，"
                                 f"占 {_a / m2 * 100:.1f}%")
            if not parts:
                continue
            self._add_para(doc, "；".join(parts) +
                           "。上述边界即复垦工程、监测与管护的实际作业范围。")

    # ═══════════════════════════════════════════════════════════════
    # 审核元信息 → Word 批注（python-docx 无原生批注 API，直接操作 OOXML）
    # ═══════════════════════════════════════════════════════════════

    def _inject_audit_comments(self, doc, state):
        """把审核信息 + **缺口清单**写入 Word 批注。

        - 章节级违规/缺口挂在对应章节标题段上（chapter=0 全局项挂在封面标题）
        - 完整审核元数据一次写入文首批注
        - 无审核数据时跳过，不生成空的 comments part

        🔴 缺口为什么要进批注（而不是往正文塞占位符）
            业主硬约束：「成稿中不得出现【待补充】/【待填写】字样」。
            但「不留空、不编造」又要求缺口必须**显式** —— 实测模型会写
            「实际参加人员以座谈会签到表为准」这种**遁词**把缺口圆过去，
            客户在成稿里根本看不出来这里缺东西。
            批注是唯一同时满足两条的落点：正文干净，点开右侧批注栏就能看到
            「这章缺什么、为什么缺、我们手上是不是已经有」。
            检测见 `app/services/gap_detector.py`。
        """
        from docx.oxml.ns import qn as _qn
        # 🔴 复垦**不往交付稿里写批注**（业主 2026-10-10 口径）——
        #    缺口改在生成结束后由前端提示，见 `chapter_catalog.injects_audit_comments`。
        if not injects_audit_comments(self._domain):
            return
        chapter_audits = state.get("chapter_audits", {}) or {}
        human_items = state.get("human_items", {}) or {}
        audit = state.get("_quality_audit", {}) or {}
        chapter_gaps = state.get("_chapter_gaps", {}) or {}
        if not chapter_audits and not human_items and not audit and not chapter_gaps:
            return

        comments_root = None
        # 找到每章标题段作为批注锚点
        # 🔴 按域取章号（`chapter_no_of_text`）：原来这里写死了稳评的 `^第X章`，
        #    复垦的 `1 前言` 一条都匹配不上 → 所有批注回落到 `doc.paragraphs[0]`
        #    （封面标题）→ **交付稿首页挂满批注气球**（实测 2026-10-10 九条全在封面）。
        ch_anchor = {}
        for p in doc.paragraphs:
            num = chapter_no_of_text(self._domain, p.text.strip())
            if num:
                ch_anchor[num] = p
        if not ch_anchor and doc.paragraphs:
            ch_anchor[1] = doc.paragraphs[0]

        # 🔴 章号取并集：**只有缺口、没有审核项**的章也必须出批注
        ch_nums = set()
        for src in (chapter_audits, chapter_gaps):
            for k in src:
                try:
                    ch_nums.add(int(k))
                except (ValueError, TypeError):
                    continue

        for ch_num in sorted(ch_nums):
            items = chapter_audits.get(ch_num) or chapter_audits.get(str(ch_num)) or []
            lines = []
            for it in items:
                lines.append(f"[{it.get('disposition', '?')}][{it.get('severity', '?')}] {it.get('message', '')}")

            gaps = chapter_gaps.get(ch_num) or chapter_gaps.get(str(ch_num)) or []
            if gaps:
                lines.append(f"—— 待补充/待核对 {len(gaps)} 项 ——")
                for g in gaps:
                    mark = "❗" if g.get("severity") == "warn" else "·"
                    lines.append(f"{mark} {g.get('detail', '')}")
                    if g.get("sentence"):
                        lines.append(f"    原句：{g['sentence']}")

            hi = human_items.get(ch_num, {})
            if hi.get("human_approved"):
                lines.append("✅ 人工审批通过（跳过 AI 审核重写）")
            elif hi.get("human_override"):
                lines.append("👤 人工直接改写内容")
            elif hi.get("human_opinion"):
                lines.append(f"👤 人工意见：{hi['human_opinion'][:80]}")
            if lines:
                anchor = ch_anchor.get(ch_num) or doc.paragraphs[0]
                comments_root = self._add_comment(doc, anchor, "\n".join(lines), comments_root)

        # 完整审核元数据
        if audit:
            meta = (f"审核元数据 total_issues={audit.get('total_issues')} "
                    f"critical={audit.get('critical_issues')} auto_fixed={audit.get('auto_fixed')} "
                    f"regenerate={audit.get('regenerate_chapters')} timestamp={audit.get('timestamp', '')}")
            comments_root = self._add_comment(doc, doc.paragraphs[0], meta, comments_root)

    def _ensure_comments_part(self, doc):
        """惰性创建 /word/comments.xml part，返回 w:comments 根元素。"""
        cached = getattr(doc, '_audit_comments_root', None)
        if cached is not None:
            return cached
        from docx.opc.part import XmlPart
        from docx.opc.packuri import PackURI
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from lxml import etree
        W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        root = etree.Element('{%s}comments' % W_NS, nsmap={'w': W_NS})
        part = XmlPart(PackURI('/word/comments.xml'),
                       'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml',
                       root, doc.part.package)
        doc.part.relate_to(part, RT.COMMENTS)
        doc._audit_comments_root = root
        return root

    def _add_comment(self, doc, anchor_para, text, comments_root=None):
        """给指定段落追加一条 Word 批注。

        在锚点段插入 <w:commentRangeStart/>…<w:commentRangeEnd/><w:commentReference/>，
        批注内容写入 comments part。返回 comments 根元素（供下一条复用）。
        """
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn as _qn
        if comments_root is None:
            comments_root = self._ensure_comments_part(doc)
        cid = len(comments_root.findall(_qn('w:comment')))

        # 批注内容
        comment = OxmlElement('w:comment')
        comment.set(_qn('w:id'), str(cid))
        # 🔴 批注作者按域取（稳评「稳评审核Agent」→ 复垦「复垦审核Agent」）——
        #    作者名在 Word 审阅窗格里可见，属报告可见文案。
        comment.set(_qn('w:author'), front_matter_labels(self._domain)["audit_comment_author"])
        cp = OxmlElement('w:p')
        cr = OxmlElement('w:r')
        ct = OxmlElement('w:t')
        ct.set(_qn('xml:space'), 'preserve')
        ct.text = text
        cr.append(ct)
        cp.append(cr)
        comment.append(cp)
        comments_root.append(comment)

        # 锚点段标记
        p_el = anchor_para._p
        rs = OxmlElement('w:commentRangeStart'); rs.set(_qn('w:id'), str(cid))
        re_ = OxmlElement('w:commentRangeEnd'); re_.set(_qn('w:id'), str(cid))
        ref_r = OxmlElement('w:r')
        ref = OxmlElement('w:commentReference'); ref.set(_qn('w:id'), str(cid))
        ref_r.append(ref)

        # 🔴 pPr 必须是 p 的第一个子元素，sectPr 必须是最后一个子元素
        pPr = p_el.find(_qn('w:pPr'))
        if pPr is not None:
            pPr.addnext(rs)
        else:
            p_el.insert(0, rs)
        sectPr = p_el.find(_qn('w:sectPr'))
        if sectPr is not None:
            sectPr.addprevious(re_)
            sectPr.addprevious(ref_r)
        else:
            p_el.append(re_)
            p_el.append(ref_r)
        return comments_root


# Singleton
report_assembler = ReportAssembler()
