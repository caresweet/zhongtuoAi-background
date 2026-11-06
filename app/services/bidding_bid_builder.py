# -*- coding: utf-8 -*-
"""投标（响应）文件生成器 —— 严格按招标文件要求的格式章节组装。

流程（业主 2026-11-05 口径）：
  1. `bidding_tender.parse_tender` 读懂招标文件（项目信息/资格/人员/评分/格式章节）；
  2. 从公司人员库按最低配置**随机抽人**（负责人1+组员3），带证书图片；
  3. 按招标「投标文件格式」那一章的目录逐项出：投标函、报价文件、资格证明、
     偏离表、评分导读表、服务方案、人员配置清单、其他；
  4. 服务方案等**正文由 LLM 按 5 章 23 节逐节写**（约 9.4 万字），资料类从公司档案取；
  5. 报价由调用方传入（UI 在上传招标文件后弹窗询问）。

不支持的格式（PDF 招标文件）会先尝试文本解析；解析不到格式目录时按同域
「投标响应文件」默认结构出，并在文档里标注需人工核对。
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[2]
KB = BACKEND / "data" / "knowledge_base.db"
AUTH = BACKEND / "data" / "auth.db"


# ═══════════════════════════════════════════════════════════════
# 基础排版
# ═══════════════════════════════════════════════════════════════
def _set_font(run, name="宋体", size=12, bold=False):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    run.font.name = name
    run._element.rPr.rFonts.set(qn('w:eastAsia'), name)
    run.font.size = Pt(size)
    run.bold = bold


def _p(doc, text: str, *, size=12, bold=False, align="left", indent=True,
       name="宋体", space_after=4):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                   "right": WD_ALIGN_PARAGRAPH.RIGHT,
                   "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}.get(align, WD_ALIGN_PARAGRAPH.LEFT)
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.74)
    r = p.add_run(text)
    _set_font(r, name=name, size=size, bold=bold)
    return p


def _h(doc, text: str, level=1, *, toc=True, size=None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    # 一级标题挂 Heading 1，供目录域抓取；服务方案里的分章不挂（目录只列 8 个部分）
    style = "Heading 1" if (level == 1 and toc) else None
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if level == 1 else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(10 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    _set_font(r, name="黑体",
              size=size or (16 if level == 1 else 14 if level == 2 else 12),
              bold=True)
    from docx.shared import RGBColor
    r.font.color.rgb = RGBColor(0, 0, 0)
    return p


def _plan_p(doc, text: str):
    """服务方案正文：15pt、固定 28 磅行距、首行缩进 2 字符（对齐人工稿）。"""
    from docx.shared import Cm, Pt
    p = _p(doc, text, size=15, align="justify")
    p.paragraph_format.line_spacing = Pt(28)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.first_line_indent = Cm(1.06)
    return p


def _p_two(doc, left: str, right: str, *, size=12, indent=0.0,
           right_pos_cm=15.0):
    """一行两段：左文 + 右文（制表位右对齐），用于「投标人名称… 单位：元」。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.first_line_indent = Cm(indent) if indent else None
    p.paragraph_format.tab_stops.add_tab_stop(Cm(right_pos_cm),
                                              WD_TAB_ALIGNMENT.RIGHT)
    r = p.add_run(left)
    _set_font(r, size=size)
    r = p.add_run("\t" + right)
    _set_font(r, size=size)
    return p


def _image_row(doc, imgs, *, width_cm=6.8):
    """身份证正反面并排（带细边框，无图题），与人工稿一致。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    if not imgs:
        return None
    t = doc.add_table(rows=1, cols=len(imgs))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (_nm, data) in enumerate(imgs):
        cell = t.cell(0, i)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = 1
        try:
            p.add_run().add_picture(io.BytesIO(data), width=Cm(width_cm))
        except Exception as e:
            logger.warning(f"[投标文件] 身份证图片插入失败: {e}")
        for row in t.rows:
            row.cells[i].width = Cm(width_cm)
    return t


def _landscape_section(doc):
    """切到横向页（偏离表/人员配置清单，照人工稿）。"""
    from docx.enum.section import WD_ORIENT, WD_SECTION
    from docx.shared import Cm
    s = doc.add_section(WD_SECTION.NEW_PAGE)
    s.orientation = WD_ORIENT.LANDSCAPE
    s.page_width, s.page_height = Cm(29.7), Cm(21.0)
    s.left_margin = s.right_margin = Cm(2.3)
    s.top_margin = s.bottom_margin = Cm(2.0)
    return s


def _portrait_section(doc):
    """从横向页切回 A4 纵向。"""
    from docx.enum.section import WD_ORIENT, WD_SECTION
    from docx.shared import Cm
    s = doc.add_section(WD_SECTION.NEW_PAGE)
    s.orientation = WD_ORIENT.PORTRAIT
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.left_margin = s.right_margin = Cm(3.0)
    s.top_margin = s.bottom_margin = Cm(2.5)
    return s


_BID_TOP_TITLES = [
    "（一）投标函", "（二）报价文件", "（三）资格证明文件",
    "（四）技术条款及相关要求偏离表", "（五）技术商务部分评分导读表",
    "（六）服务内容的详细说明及服务方案", "（七）项目人员配置清单", "（八）其他",
]


def _stamp_toc(path) -> bool:
    """把目录域换成**带真实页码的静态条目**（用 LibreOffice 分页）。

    只在机器上有 soffice 时执行；失败就保留目录域，不影响出报告。
    """
    import shutil as _sh
    import subprocess
    import tempfile
    from pathlib import Path as _P
    soffice = _sh.which("soffice")
    if not soffice:
        return False
    path = _P(path)
    tmp = _P(tempfile.mkdtemp(prefix="bidtoc_"))
    try:
        subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(tmp), str(path)],
            check=True, capture_output=True, timeout=240)
        pdf = tmp / f"{path.stem}.pdf"
        if not pdf.exists():
            return False
        import fitz
        src = fitz.open(str(pdf))
        pages = {}
        for title in _BID_TOP_TITLES:
            # 跳过封面/目录页，按**整行等于标题**匹配，避免命中目录条目
            for i in range(2, src.page_count):
                lines = [ln.strip() for ln in src[i].get_text().splitlines()]
                if title in lines:
                    pages[title] = i          # 页码从 0 起，封面隐藏
                    break
        src.close()
        if len(pages) < len(_BID_TOP_TITLES):
            return False

        from docx import Document
        from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
        from docx.shared import Cm
        d = Document(str(path))
        paras = d.paragraphs
        ph = next((p for p in paras
                   if "右键选择" in p.text or 'TOC \\o' in p._p.xml), None)
        if ph is None:
            # 已有静态目录：只刷新页码
            _existing = [p for p in paras
                         if any(p.text.startswith(t + "\t") for t in _BID_TOP_TITLES)]
            if len(_existing) != len(_BID_TOP_TITLES):
                return False
            for q, title in zip(_existing, _BID_TOP_TITLES):
                for r in list(q.runs):
                    r._element.getparent().remove(r._element)
                r = q.add_run(f"{title}\t{pages[title]}")
                _set_font(r, size=12)
            d.save(str(path))
            return True
        nxt = ph._p.getnext()
        from docx.text.paragraph import Paragraph
        anchor = Paragraph(nxt, ph._parent) if nxt is not None else None
        for title in _BID_TOP_TITLES:
            q = (anchor.insert_paragraph_before() if anchor is not None
                 else ph.insert_paragraph_before())
            q.paragraph_format.tab_stops.add_tab_stop(
                Cm(15.0), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
            q.paragraph_format.space_after = 0
            r = q.add_run(f"{title}\t{pages[title]}")
            _set_font(r, size=12)
        ph._p.getparent().remove(ph._p)
        d.save(str(path))
        return True
    finally:
        import shutil as _sh2
        _sh2.rmtree(tmp, ignore_errors=True)


def _add_toc(doc) -> None:
    """第二页目录：标题 + TOC 域（打开文档时自动更新页码）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn as _q
    from docx.shared import Pt
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("目  录")
    _set_font(r, name="黑体", size=18, bold=True)

    q = doc.add_paragraph()
    run = q.add_run()
    _set_font(run, size=12)
    begin = OxmlElement('w:fldChar')
    begin.set(_q('w:fldCharType'), 'begin')
    begin.set(_q('w:dirty'), 'true')      # 强制打开时更新
    instr = OxmlElement('w:instrText')
    instr.set(_q('xml:space'), 'preserve')
    instr.text = 'TOC \\o "1-1" \\h \\z \\u'
    sep = OxmlElement('w:fldChar')
    sep.set(_q('w:fldCharType'), 'separate')
    placeholder = OxmlElement('w:t')
    placeholder.text = "右键选择“更新域”生成目录"
    end = OxmlElement('w:fldChar')
    end.set(_q('w:fldCharType'), 'end')
    for el in (begin, instr, sep, placeholder, end):
        run._r.append(el)

    # Word / WPS 打开时自动刷新目录域
    try:
        from docx.oxml import OxmlElement as _OE
        st = doc.settings.element
        if st.find(_q('w:updateFields')) is None:
            uf = _OE('w:updateFields')
            uf.set(_q('w:val'), 'true')
            st.append(uf)
    except Exception:
        pass


def _page_number_footer(doc) -> None:
    """页脚居中页码（封面页不显示）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn as _q
    from docx.shared import Pt
    sec = doc.sections[0]
    sec.different_first_page_header_footer = True
    # 页码从 0 起：封面不显示，目录页印 1，与人工稿一致
    try:
        sectPr = sec._sectPr
        if sectPr.find(_q('w:pgNumType')) is None:
            pn = OxmlElement('w:pgNumType')
            pn.set(_q('w:start'), '0')
            sectPr.append(pn)
    except Exception:
        pass
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    _set_font(run, size=9)
    fld = OxmlElement('w:fldSimple')
    fld.set(_q('w:instr'), 'PAGE')
    r = OxmlElement('w:r')
    t = OxmlElement('w:t')
    t.text = "1"          # 不刷新域的阅读器至少看到页码 1
    r.append(t)
    fld.append(r)
    p._p.append(fld)


def _apply_body_format(doc) -> None:
    """正文 12pt 段落统一 1.5 倍行距（对齐人工稿；标题/封面不动）。"""
    for p in doc.paragraphs:
        sizes = [r.font.size.pt for r in p.runs if r.font.size]
        if p.text.strip() and sizes and max(sizes) <= 12:
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.space_after = 0


def _quote_detail_table(doc, items, total_yuan: int, big: str):
    """明细报价表：5 列 + 合计行跨列合并（照人工稿/招标模板）。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    n = len(items)
    t = doc.add_table(rows=1 + n + 2, cols=5)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["序号", "项目名称", "数量", "单价", "分项总报价"]
    for ci, h in enumerate(headers):
        cell = t.cell(0, ci)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        r = cell.paragraphs[0].add_run(h)
        _set_font(r, size=10.5)
        cell.paragraphs[0].alignment = 1
    for i, (name, amt) in enumerate(items, 1):
        for ci, val in enumerate((str(i), name, "1", str(amt), str(amt))):
            cell = t.cell(i, ci)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            r = cell.paragraphs[0].add_run(val)
            _set_font(r, size=10.5)
            cell.paragraphs[0].alignment = 1
    # 合计：左侧两列纵向合并，右侧三列拆成「小写/大写」两行
    left = t.cell(1 + n, 0).merge(t.cell(2 + n, 1))
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    left.text = ""
    r = left.paragraphs[0].add_run("合计")
    _set_font(r, size=10.5)
    left.paragraphs[0].alignment = 1
    for ri, txt in ((1 + n, f"（小写）{total_yuan} 元"), (2 + n, f"（大写）{big}")):
        c = t.cell(ri, 2).merge(t.cell(ri, 4))
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        c.text = ""
        r = c.paragraphs[0].add_run(txt)
        _set_font(r, size=10.5)
        c.paragraphs[0].alignment = 1
    for ci, w in enumerate([1.47, 5.4, 3.5, 3.51, 3.52]):
        for row in t.rows:
            row.cells[ci].width = Cm(w)
    t.rows[0].height = Cm(1.38)
    for ri in range(1, n + 1):
        t.rows[ri].height = Cm(1.35)
    t.rows[1 + n].height = Cm(2.7)
    t.rows[2 + n].height = Cm(1.35)
    return t


def _table(doc, headers: Sequence[str], rows: Sequence[Sequence[str]],
           widths: Optional[Sequence[float]] = None, *, font_size=10.5,
           row_heights: Optional[Sequence[float]] = None):
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    t = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ci, h in enumerate(headers):
        cell = t.cell(0, ci)
        cell.text = ""
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        r = cell.paragraphs[0].add_run(str(h))
        _set_font(r, size=font_size, bold=True)
        cell.paragraphs[0].alignment = 1
    for ri, row in enumerate(rows, 1):
        for ci in range(len(headers)):
            cell = t.cell(ri, ci)
            cell.text = ""
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            val = row[ci] if ci < len(row) else ""
            r = cell.paragraphs[0].add_run(str(val))
            _set_font(r, size=font_size)
            cell.paragraphs[0].alignment = 1
    if widths:
        for ci, w in enumerate(widths):
            for r in t.rows:
                r.cells[ci].width = Cm(w)
    if row_heights:
        for i, h in enumerate(row_heights):
            if i < len(t.rows):
                t.rows[i].height = Cm(h)
    return t


def _center_all_cells(doc) -> None:
    """全篇表格单元格上下左右居中（含模板/后续追加的表）。"""
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                for p in cell.paragraphs:
                    p.alignment = 1


def _img(doc, data: bytes, width_cm=15.0, caption=""):
    from docx.shared import Cm
    try:
        doc.add_picture(io.BytesIO(data), width=Cm(width_cm))
    except Exception as e:
        logger.warning(f"[投标文件] 图片插入失败: {e}")
        return
    if caption:
        _p(doc, caption, size=10.5, align="center", indent=False)


# ═══════════════════════════════════════════════════════════════
# 数据读取
# ═══════════════════════════════════════════════════════════════
def company_name(company_id: int) -> str:
    try:
        c = sqlite3.connect(str(AUTH))
        row = c.execute("select name from companies where id=?", (company_id,)).fetchone()
        c.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _asset_texts(company: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        c = sqlite3.connect(str(KB))
        for title, content in c.execute(
                "select title,content from company_assets where company=? and is_active=1",
                (company,)):
            out[str(title or "")] = str(content or "")
        c.close()
    except Exception:
        pass
    return out


def _asset_images(*keywords: str, limit: int = 12) -> List[Tuple[str, bytes]]:
    out = []
    try:
        c = sqlite3.connect(str(KB))
        rows = c.execute(
            "select image_name,image_data from asset_images where is_active=1 "
            "order by id").fetchall()
        c.close()
    except Exception:
        return out
    for name, b64 in rows:
        nm = str(name or "")
        if not any(k in nm for k in keywords):
            continue
        try:
            out.append((nm, base64.b64decode(b64)))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _person_images(personnel_id: int) -> List[Tuple[str, bytes]]:
    out = []
    try:
        c = sqlite3.connect(str(KB))
        rows = c.execute(
            "select image_name,image_path from company_personnel_images "
            "where personnel_id=? order by id", (personnel_id,)).fetchall()
        c.close()
    except Exception:
        return out
    for name, path in rows:
        p = Path(str(path or ""))
        if not p.is_absolute():
            p = BACKEND / p
        if p.exists():
            out.append((str(name), p.read_bytes()))
    return out


# ═══════════════════════════════════════════════════════════════
# 服务方案（agent 写）
# ═══════════════════════════════════════════════════════════════
# 服务方案章节结构（对齐人工稿：5 章 24 节，目标约 9.4 万字）
_PLAN_CHAPTERS: List[Tuple[str, List[Tuple[str, List[str], int]]]] = [
    ("第一章 对项目的理解", [
        ("1.1 项目背景与区域概况",
         ["项目来源、采购人、四个地块基本情况", "丁蜀镇区位、自然地理、社会经济概况",
          "四个地块产业定位与报批需求"], 4000),
        ("1.2 采购需求与工作内容理解",
         ["招标文件第三章采购内容逐项解读", "服务边界、成果形式与配合要求",
          "从采购人视角理解核心诉求"], 4000),
        ("1.3 项目重点与难点分析",
         ["多地块并行、村域广、权属复杂", "报批类型差异（批次/单独选址）",
          "政策要件、耕地占补、社保审核等难点", "应对思路"], 4000),
        ("1.4 政策依据与成果深度理解",
         ["土地管理法及实施条例、江苏省报批政策", "电子报件与审查标准",
          "成果深度、时效与一次通过率要求"], 3000),
    ]),
    ("第二章 项目组织实施方案", [
        ("2.1 项目组织架构与人员配备",
         ["公司资质与同类业绩支撑", "项目负责人负责制、技术负责人/质检/外业/内业分工",
          "人员到岗与更换管理"], 4500),
        ("2.2 总体工作思路与技术路线",
         ["前期准备—资料收集—勘测定界—组卷—报批—跟踪全流程",
          "技术方法、软件、坐标与数据标准", "与采购人及主管部门的衔接"], 4500),
        ("2.3 前期准备与资料收集",
         ["项目启动、政策研读、资料清单", "立项/规划/资金/社保等要件收集",
          "资料交接与缺件催办"], 4000),
        ("2.4 地类权属核查与勘测定界",
         ["权属界线确认、指界签字", "地类认定与变更调查衔接",
          "勘测定界成果与面积汇总"], 4500),
        ("2.5 征前程序与补偿安置配合",
         ["拟征收土地公告、现状调查", "补偿安置方案与公告、听证",
          "补偿登记、社保审核配合"], 4500),
        ("2.6 报批材料组卷与电子报件",
         ["一方案三信息及附件编制", "电子报件系统录入与校验",
          "纸质材料装订与归档"], 4500),
        ("2.7 审批跟踪与协调保障",
         ["县级、市级、省级审查跟踪", "补正材料响应", "取得建设用地批复"], 3500),
    ]),
    ("第三章 质量保障措施", [
        ("3.1 质量管理体系与质量目标",
         ["ISO9001 体系与公司质量方针", "本项目质量目标与责任体系"], 4000),
        ("3.2 三级校审与过程质量控制",
         ["作业组自检、项目组复核、公司质检", "关键节点检查与记录"], 4000),
        ("3.3 成果质量指标与检查",
         ["面积、权属、地类、图件、文本质量指标", "检查方法与抽检比例"], 4000),
        ("3.4 质量问题整改与持续改进",
         ["问题清单、整改闭环", "质量例会与经验固化"], 3000),
    ]),
    ("第四章 进度安排", [
        ("4.1 总体进度计划与编制依据",
         ["合同工期 90 日约束", "法律法规与审批时限",
          "同类项目工期经验"], 4500),
        ("4.2 分阶段进度安排",
         ["准备、调查、组卷、报批、跟踪各阶段工期", "四个地块并行安排"], 4500),
        ("4.3 关键路径与里程碑",
         ["关键路径识别", "里程碑节点与交付物"], 4000),
        ("4.4 进度保障与纠偏措施",
         ["组织、人员、资源保障", "周计划/周报/纠偏", "外部要件催办"], 4000),
    ]),
    ("第五章 安全保密保障措施", [
        ("5.1 安全生产管理体系",
         ["安全方针、组织机构、责任制", "安全制度与教育培训"], 4000),
        ("5.2 外业与交通安全措施",
         ["外业作业安全、仪器设备安全", "车辆与交通安全"], 4000),
        ("5.3 数据安全与保密管理",
         ["涉密测绘成果管理", "人员保密、设备与介质管理",
          "数据传输、备份与销毁"], 5000),
        ("5.4 应急处置与监督检查",
         ["应急预案与演练", "安全检查与考核"], 4000),
    ]),
]


def _plan_chunks(chapter) -> List[List[Tuple[str, List[str], int]]]:
    """一小节一次调用 —— 单次目标 3000～5000 字，23 节并行拼出约 9 万字。"""
    return [[sub] for sub in chapter[1]]


async def _gen_chunk(llm, system: str, facts: str, chapter_title: str, chunk,
                     prior: str) -> str:
    outline, target = [], 0
    for sub in chunk:
        full, pts, chars = sub[0], sub[1], sub[2]
        no, title = (full.split(" ", 1) + [""])[:2]
        outline.append(f"## {no} {title}".strip())
        outline.append("要点：" + "；".join(pts))
        _ask = int(chars * 1.6)
        outline.append(f"目标字数：不少于 {_ask} 字")
        target += _ask
    user = (
        f"为下面的政府采购项目撰写投标文件「服务内容的详细说明及服务方案」的"
        f"「{chapter_title}」部分。\n\n{facts}\n"
        f"本次只写以下小节（严格按编号和标题，用 ## 输出）：\n"
        + "\n".join(outline)
        + f"\n\n前文已写小节：{prior}。不要重复前文内容，做好承接。\n"
        f"写作要求：合计不少于 {target} 字；每节 5～8 个完整段落，开头一句总述，"
        "再展开做法、标准、责任人和时间；不使用项目符号，步骤用「1）2）3）」；"
        "结合招标文件采购内容写具体做法，不写空话；"
        "不得出现「待补充」「以实际为准」等占位语。"
    )
    try:
        # 🔴 关闭思考链：deepseek 推理模型默认把预算耗在 reasoning 上
        #    （实测 12000 tokens 只出 500 字正文），关掉后直接写正文、快得多。
        _res = await asyncio.wait_for(
            llm.chat_with_reasoning(
                messages=[{"role": "user", "content": user}], system=system,
                max_tokens=12000, temperature=0.35, disable_thinking=True),
            timeout=240)
        text = str((_res or {}).get("content") or "").strip()
        if text and len(text) > 500 and "待补充" not in text:
            return text
        return ""
    except Exception as e:
        logger.warning(f"[投标文件] 章节「{chapter_title}」一块生成失败: {e}")
        return ""


async def _write_plan(tender: dict, *, llm=None) -> str:
    """按人工稿 5 章 23 节生成服务方案（约 9.4 万字），各块并行调用 LLM。"""
    try:
        from app.services.llm_service import LLMService
        llm = llm or LLMService()
    except Exception:
        return ""
    scope = (tender.get("scope") or "")[:1800]
    facts = (
        f"项目名称：{tender.get('project_name')}\n"
        f"项目编号：{tender.get('project_no')}\n"
        f"采购人：{tender.get('purchaser')}\n"
        f"代理机构：{tender.get('agency')}\n"
        f"预算/最高限价：{tender.get('budget_wan')} 万元\n"
        f"采购内容：{scope}\n"
    )
    system = ("你是招标投标文件编写专家，替供应商（江苏众拓测绘有限公司）写投标响应文件。"
              "文风平实专业，所有内容必须落在本项目事实上，不编造地名、数字和批文号。")

    sem = asyncio.Semaphore(4)      # 控制并发，避免触发接口限流

    async def _guarded(ch_t, ch_chunk, prior_txt):
        async with sem:
            return await _gen_chunk(llm, system, facts, ch_t, ch_chunk, prior_txt)

    jobs = []          # (chapter_title, chunk, task)
    prior = ""
    for chapter_title, subs in _PLAN_CHAPTERS:
        for chunk in _plan_chunks((chapter_title, subs)):
            jobs.append((chapter_title, chunk,
                         asyncio.create_task(
                             _guarded(chapter_title, chunk, prior))))
            prior = "、".join(str(x[0]) for x in chunk)
    results = list(await asyncio.gather(*(j[2] for j in jobs), return_exceptions=True))
    # 并发失败的块**串行重试一次**（限流/超时多为瞬时问题）
    for i, ((ch_t, chunk, _), text) in enumerate(zip(jobs, results)):
        if not (isinstance(text, str) and text):
            results[i] = await _gen_chunk(llm, system, facts, ch_t, chunk, "")

    parts: List[str] = []
    cur_ch = ""
    for (chapter_title, chunk, _), text in zip(jobs, results):
        if chapter_title != cur_ch:
            parts.append(f"# {chapter_title}\n")
            cur_ch = chapter_title
        if isinstance(text, str) and text:
            parts.append(text + "\n")
        else:
            logger.warning(f"[投标文件] 「{chapter_title}」有一块没有生成内容")
    return "\n".join(parts)


def _fallback_plan(tender: dict) -> str:
    name = tender.get("project_name") or "本项目"
    return (
        f"## 一、项目理解\n\n{name}为用地报批服务项目，工作内容包含征地报批全流程服务、"
        "勘测定界与报批材料组卷、社会稳定风险评估配合等。服务期限按招标文件要求执行。\n\n"
        "## 二、总体工作方案\n\n按“前期准备—资料收集—勘测定界—组卷报批—成果移交”"
        "五个阶段推进，实行项目负责人负责制，关键节点向采购人书面汇报。\n\n"
        "## 三、质量保障措施\n\n执行三级校审（作业组自检、项目组复核、公司质检），"
        "成果资料按档案管理要求整理移交。\n\n"
        "## 四、进度安排\n\n进场后按采购人要求编制实施计划，周报进度，确保在合同工期内完成。\n\n"
        "## 五、安全保密措施\n\n涉密测绘成果按保密规定管理，作业人员签订保密承诺，"
        "数据不外传、不留存于个人设备。\n"
    )


def _md_to_docx(doc, md: str):
    """简易 markdown → docx（标题/段落/列表）。"""
    _seen_scope = False
    for line in str(md or "").splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if s.startswith("###"):
            _h(doc, s.lstrip("#").strip(), 3, size=15)
        elif s.startswith("##"):
            _h(doc, s.lstrip("#").strip(), 2, size=16)
        elif s.startswith("#"):
            _h(doc, s.lstrip("#").strip(), 1, toc=False, size=18)
        elif re.match(r'^\s*[-*]\s+', s):
            body = re.sub(r'^\s*[-*]\s+', '', s)
            # 模型常在每个小节重复一遍「采购内容：…」，只保留第一次
            if body.startswith("采购内容："):
                if _seen_scope:
                    continue
                _seen_scope = True
            _plan_p(doc, "• " + body)
        else:
            _plan_p(doc, re.sub(r'\*\*(.+?)\*\*', r'\1', s))


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ("", "拾", "佰", "仟")
_BIG_UNITS = ("", "万", "亿")


def _upper_yuan(amount_yuan: int) -> str:
    """整数元 → 人民币大写（如 370000 → 叁拾柒万元整）。"""
    if amount_yuan <= 0:
        return "零元整"
    groups = []
    n = amount_yuan
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            if parts and not parts[-1].endswith("零"):
                parts.append("零")
            continue
        s = ""
        zero = False
        for i in range(3, -1, -1):
            d = (g // (10 ** i)) % 10
            if d == 0:
                zero = bool(s)
            else:
                if zero:
                    s += "零"
                    zero = False
                s += _DIGITS[d] + _UNITS[i]
        parts.append(s + _BIG_UNITS[gi])
    text = "".join(parts).rstrip("零")
    return text + "元整"


def _price_text(price_wan: float) -> Tuple[str, str]:
    """(小写, 大写)。"""
    return f"{price_wan:.2f}", _upper_yuan(int(round(float(price_wan) * 10000)))


def _quote_items(project: str, total_yuan: int) -> List[Tuple[str, int]]:
    """按项目名里的地块拆「投标明细报价表」细项；拆不出就一行总价。

    人工稿按地块分项（蜀浦路东西两侧 / 湖光中路南侧 / 航空产业园），
    合计与开标一览表一致。金额按百元取整，尾差放最后一项。
    """
    name = str(project or "").strip()
    name = re.sub(r'项目$', '', name)
    name = re.sub(r'(土地征收|用地)?报批(综合)?服务$', '', name).strip()
    parts = [p.strip() for p in re.split(r'[、及]', name) if p.strip()]
    if not parts:
        parts = ["用地报批服务"]
    items = [f"{p}用地报批服务" for p in parts]
    n = len(items)
    if n == 1:
        return [(items[0], total_yuan)]
    each = (total_yuan // n) // 100 * 100
    amounts = [each] * (n - 1) + [total_yuan - each * (n - 1)]
    return list(zip(items, amounts))


def _auth_fields(texts: Dict[str, str]) -> Dict[str, str]:
    """从公司档案的《授权委托书》里拆出被授权代表信息（模板逐栏填写用）。"""
    import datetime as _dt
    src = texts.get("授权委托书") or ""

    def grab(pat: str, default: str = "") -> str:
        m = re.search(pat, src)
        return (m.group(1) if m else default).strip()

    legal = grab(r'法定代表人[：:]\s*([^\n；]+)') or "陈春"
    agent = grab(r'受托人：.*?姓名\s*([^\s；，]+)') or "张红艳"
    gender = grab(r'性别[：:]\s*([男女])') or "女"
    birth = grab(r'出生日期[：:]\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)')
    age = ""
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', birth)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        t = _dt.date.today()
        age = str(t.year - y - ((t.month, t.day) < (mo, d)))
    return {
        "legal": legal,
        "agent": agent,
        "gender": gender,
        "age": age,
        "position": grab(r'职务[：:]\s*([^；\n]+)') or "项目经理",
        "id_no": grab(r'身份证(?:号码)?[：:]\s*([0-9Xx]+)'),
        "mobile": grab(r'(?:联系方式|手机号码)[：:]\s*(\d{5,})'),
        "office": grab(r'办公电话[：:]\s*([0-9\-]+)') or "0517-83663008",
        "address": grab(r'地址[：:]\s*([^\n]+)') or "淮安经济技术开发区海口路9号1幢1211室",
    }


async def build_bid_document(*, tender_path: str | Path, company_id: int,
                             price_wan: float, out_path: str | Path,
                             seed: Optional[int] = None,
                             personnel: Optional[List[dict]] = None,
                             use_llm: bool = True) -> Path:
    from docx import Document
    from docx.shared import Cm, Pt
    from app.services.bidding_tender import parse_tender, select_bid_personnel

    tender = parse_tender(tender_path)
    co_name = company_name(company_id) or "投标人"
    people = personnel or select_bid_personnel(company_id=company_id, seed=seed)
    texts = _asset_texts(co_name)
    small, big = _price_text(price_wan)
    _total_yuan = int(round(float(price_wan) * 10000))
    project = tender.get("project_name") or ""
    no = tender.get("project_no") or ""
    purchaser = tender.get("purchaser") or ""
    agency = tender.get("agency") or ""
    from datetime import date as _date

    # 人工稿日期用阿拉伯数字（「日期：2026 年 11 月 6 日」），不用汉字年号
    _d = _date.today()
    today = f"{_d.year}年{_d.month}月{_d.day}日"

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(3.0)
    sec.top_margin = sec.bottom_margin = Cm(2.5)

    # ── 封面（照人工案例逐项对版：60pt 仿宋标题、16pt 仿宋信息、3cm 页边距）──
    _p(doc, "", indent=False, space_after=0)
    _p(doc, "", indent=False, space_after=0)
    _t = _p(doc, "投 标 文 件", size=60, bold=False, align="center",
            indent=False, name="仿宋", space_after=0)
    _t.paragraph_format.space_before = Pt(40)
    _p(doc, "", indent=False, space_after=0)
    _p(doc, "", indent=False, space_after=0)
    _p(doc, f"项目编号：{no}", size=16, align="center", indent=False,
       name="仿宋", space_after=0)
    _p(doc, "", indent=False, space_after=0)
    _p(doc, "", indent=False, space_after=0)
    _p(doc, f"项目名称：{project}", size=16, indent=False, name="仿宋",
       space_after=0)
    _p(doc, "", indent=False, space_after=0)
    _p(doc, f"投标人名称：{co_name}", size=16, indent=False, name="仿宋",
       space_after=0)
    _p(doc, "（盖公章）", size=16, indent=False, name="仿宋", space_after=0)
    _p(doc, "", indent=False, space_after=0)
    _p(doc, "法定代表人或其被授权代表人：", size=16, indent=False,
       name="仿宋", space_after=0)
    _p(doc, "（签名）", size=16, indent=False, name="仿宋", space_after=0)
    for _ in range(4):
        _p(doc, "", indent=False, space_after=0)
    _p(doc, f"日期：{today}", size=16, align="center", indent=False,
       name="仿宋", space_after=0)
    doc.add_page_break()

    # ── 第二页：目录（照人工稿）──
    _add_toc(doc)
    doc.add_page_break()

    # ── （一）投标函 ──
    _h(doc, "（一）投标函", 1)
    lines = [
        f"致{purchaser}、{agency}：",
        "我方收到你们招标文件，经仔细阅读和研究，我方决定参加投标。",
        f"1. 我方愿意按照招标文件的一切要求提供本项目服务，投标总价详见《开标一览表》，"
        f"投标报价为人民币（大写）{big}（小写：￥{_total_yuan}.00元）。",
        "2. 我方已对照招标要求，填写了投标服务偏离情况，详见《技术条款及相关要求偏离表》。",
        "3. 我方承诺本次采购项目清单中，若涉及强制采购节能产品或信息安全产品的，"
        "将按照招标文件及法律法规要求提供相关产品。",
        "4. 我方同意按招标文件中的规定，本投标文件的有效期为投标截止之日起的60天（含提交当日）。"
        "在特殊情况下，可与贵方协商延长投标文件的有效期。",
        "5. 如果我方的投标文件被接受，我方将履行招标文件中规定的每一项要求，按期、按质、按量完成服务。",
        "6. 我方愿意根据贵方及评标委员会的要求，提供投标文件中所有材料的原件及相关证明性资料。",
        "7. 我方认为采购人有权确定中标人。除因不可抗力或招标文件认可的情形以外，我方绝不放弃中标资格。",
        "8. 我方愿意按《中华人民共和国民法典》和合同条款履行自己的全部责任。",
        "9. 如果我方中标，我方愿意遵守招标文件和中标通知书的规定，在指定时间和地点与采购人签订合同，"
        "并在签订合同前按招标文件的规定交纳履约保证金。",
        "10. 我方决不提供虚假材料谋取中标，决不采取不正当手段诋毁、排挤其他投标人，"
        "决不与采购人、其它投标人或者采购代理机构恶意串通，决不向采购人、采购代理机构工作人员和评委"
        "进行商业贿赂，决不在采购过程中与采购人进行协商谈判，决不拒绝有关部门监督检查，"
        "如有违反，无条件接受相关管理部门的处罚。",
        "11. 我方承诺一旦中标，提供的服务质量不低于招标文件要求，符合国家相关标准，"
        "并接受采购人的检查与验收要求。",
        "12. 我方不是为本项目提供整体设计、规范编制或者项目管理、监理、检测等服务的投标人。",
        "13. 关联单位情况说明：与我方单位负责人为同一人的其他单位：无；"
        "与我方存在直接控股、管理关系的其他单位：无。",
        "14. 与本投标有关的一切正式往来函电，请联系：",
        f"地址：{_company_addr(co_name)}    邮编：223005",
        f"联系人：{_company_contact(co_name)}    办公电话：0517-83663008",
        "开户名称：江苏众拓测绘有限公司    开户银行：/    开户账号：/",
    ]
    for ln in lines:
        _p(doc, ln, align="justify")
    doc.add_paragraph()
    _p(doc, f"投标人（盖章）：{co_name}", indent=False)
    _p(doc, "法定代表人或被授权代表人（签名）：", indent=False)
    _p(doc, f"日期：{today}", indent=False)
    doc.add_page_break()

    # ── （二）报价文件（照招标第六章模板逐项对版）──
    _h(doc, "（二）报价文件", 1)
    _h(doc, "1、开标一览表：", 2)
    _p(doc, "开标一览表", size=14, align="center", indent=False)
    _p_two(doc, f"投标人名称（盖章）：{co_name}", "单位：元", size=14,
           indent=0.0)
    _table(doc, ["标题", "内容（小写）", "内容（大写）"],
           [["报价", f"{_total_yuan} 元", big]],
           widths=[3.43, 5.33, 8.18], font_size=14,
           row_heights=[1.12, 2.89])
    _sig = _p_two(doc, "投标人签名：", f"日期：{today}", size=14, indent=0.75)
    _sig.paragraph_format.space_before = Pt(18)
    _p(doc, "注：", size=10.5, indent=False)
    _p(doc, "①  投标报价应包含完成本服务项目发生的所有含税费用、支付给员工的工资和"
            "国家强制缴纳的各种社会保障资金，以及投标人认为需要的其他费用等；",
       size=10.5, indent=False)
    _p(doc, "②  每一项服务仅接受一个价格，投标文件应对《开标一览表》中的全部服务进行投标，"
            "只投其中部分服务者，投标无效。", size=10.5, indent=False)

    doc.add_page_break()
    _h(doc, "2、投标明细报价表：", 2)
    _p(doc, "2.1 明细报价表", size=14, align="center", indent=False)
    _p(doc, f"项目名称：{project}", indent=False)
    _p(doc, f"项目编号：{no}", indent=False)
    _p(doc, "单位：元", indent=False, align="center")
    _items = _quote_items(project, _total_yuan)
    _quote_detail_table(doc, _items, _total_yuan, big)
    _sig = _p(doc, f"投 标 人（签章）：{co_name}", indent=False)
    _sig.paragraph_format.left_indent = Cm(4.5)
    _sig.paragraph_format.space_before = Pt(18)
    _sig2 = _p(doc, "投 标 人（签名）：", indent=False)
    _sig2.paragraph_format.left_indent = Cm(4.5)
    _p(doc, "注：", size=10.5, indent=False)
    _p(doc, "（1）投标人应当根据“第三章 项目技术要求和有关说明”的内容详细填写明细报价表，"
            "须包括本次采购的所有细项；“明细报价表”合计应与“报价一览表”报价一致。",
       size=10.5, indent=False)
    doc.add_page_break()

    # ── （三）资格证明文件 ──
    _h(doc, "（三）资格证明文件", 1)
    af = _auth_fields(texts)
    _h(doc, "1、关于资格的声明函：", 2)
    _p(doc, "关于资格的声明函", size=14, align="center", indent=False)
    _p(doc, f"{purchaser}：", indent=False)
    _p(doc, f"我公司参加贵单位{project}项目采购活动前三年内在经营活动中没有重大违法记录；"
            "同时，投标时不存在有效期限内的采购严重违法失信行为的处罚记录，未被列入失信名单"
            "或重大税收违法案件当事人名单；且具备履行合同所必需的设备和专业技术能力。"
            "我公司（单位）愿针对本次项目进行投标，投标文件中所有关于投标资格的文件、证明、"
            "陈述均是真实的、准确的。若有违背，我公司（单位）同意监管部门做出的相关决定并"
            "承担由此而产生的一切后果。", align="justify")
    _p(doc, "投标人签名：", indent=False)
    _p(doc, f"投标人公章：{co_name}", indent=False)

    # 法定代表人身份证随《关于资格的声明函》一起提交（模板位置）
    _legal_imgs = _asset_images(f"{af['legal']} 身份证", limit=2)
    if _legal_imgs:
        _p(doc, "法定代表人身份证复印件：", bold=True, indent=False)
        _image_row(doc, _legal_imgs)
    doc.add_page_break()

    _h(doc, "2、法定代表人授权委托书：", 2)
    _p(doc, "法定代表人授权书", size=14, bold=True, align="center",
       indent=False, name="宋体")
    _p(doc, f"日期：{today}", indent=False, align="right")
    _p(doc, f"{purchaser}、{agency}：", indent=False)
    _p(doc, f"{co_name}系中华人民共和国合法企业。法定地址：{af['address']}",
       align="justify")
    _p(doc, f"特授权{af['agent']}代表我公司（单位）全权办理针对{project}项目的投标、"
            "参与开标、谈判、签约等具体工作，并签署全部有关的文件、协议及合同。",
       align="justify")
    _p(doc, "我公司（单位）对被授权代表人的签名负全部责任。", align="justify")
    _p(doc, "在撤销授权的书面通知送达你处以前，本授权书一直有效，被授权代表签署的所有"
            "文件（在授权书有效期内签署的）不因授权的撤销而失效。", align="justify")
    _p(doc, "被授权代表人情况：", indent=False)
    _p(doc, f"姓名：{af['agent']}        性别：{af['gender']}        "
            f"年龄：{af['age']}        职务：{af['position']}", indent=False)
    _p(doc, f"身份证号码：{af['id_no']}        手机号码：{af['mobile']}",
       indent=False)
    _p(doc, f"办公电话：{af['office']}        传真：无", indent=False)
    _p(doc, f"通讯地址：{af['address']}", indent=False)
    _p(doc, "被授权代表人签名：", indent=False)
    _p(doc, f"单位名称（公章）：{co_name}", indent=False)
    _p(doc, f"法定代表人签名：{af['legal']}", indent=False)

    # 授权书后贴被授权代表身份证正反面（并排，带细边框，无图题）
    _agent_imgs = _asset_images(f"{af['agent']} 身份证", limit=2)
    if _agent_imgs and af["agent"] != af["legal"]:
        _p(doc, "被授权代表身份证复印件：", bold=True, indent=False)
        _image_row(doc, _agent_imgs)
    doc.add_page_break()

    _h(doc, "3、资格性证明材料复印件：", 2)
    _p(doc, "资格性证明材料", size=14, align="center", indent=False)
    _p(doc, f"日期：{today}", indent=False, align="right")
    _p(doc, "投标人应按以下顺序提供相关材料复印件：", indent=False)
    for _item in (
        "①　具有独立承担民事责任的能力：投标人为法人或其他组织的提供营业执照或法人"
        "（登记）证书复印件，投标人为自然人的提供自然人身份证复印件；",
        "②　具有良好的商业信誉和健全的财务会计制度：银行出具的资信证明（若投标人为"
        "法人的须提供其基本开户银行出具的资信证明）或投标人近十二个月中任意一个月份的"
        "财务状况报告（资产负债表和利润表）或由会计师事务所出具的近两年中任意一个年度"
        "的审计报告和所附已审财务报告复印件；",
        "③　有依法缴纳税收的良好记录：投标人依法缴纳近十二个月中任意一个月份税收的"
        "相关材料（提供相关主管部门证明或银行代扣证明）复印件；",
        "④　有依法缴纳社会保障资金的良好记录：投标人依法缴纳近十二个月中任意一个月份"
        "社会保障资金的相关材料（提供相关主管部门证明或银行代扣证明）复印件；",
        "⑤　良好的信用记录：未被“信用中国”网站（www.creditchina.gov.cn）列入失信执行人、"
        "重大税收违法案件当事人名单、政府采购严重违法失信名单，或未在“中国政府采购网”"
        "网站（www.ccgp.gov.cn）被列入政府采购严重违法失信行为记录名单；（若投标人企业"
        "信息已录入以上网站的，无需提供相关证明材料；若投标人企业信息未录入以上网站的，"
        "须在投标文件中提供无不良信用记录承诺书）",
        "⑥　采购人特殊要求：投标单位具有乙级及以上测绘资质。",
    ):
        _p(doc, _item, indent=False)
    _p(doc, "投标人签名：", indent=False)
    _p(doc, f"投标人公章：{co_name}", indent=False)
    doc.add_page_break()

    _asset_block(doc, "营业执照", [("1.营业执照",)], caption="营业执照")
    _asset_block(doc, "财务", [("2024年度财务报告",), ("2025年度审计报告",),
                              ("2025年10—12月资产负债表",),
                              ("2025年10—12月利润表",)], caption="财务状况报告")
    _asset_block(doc, "税收", [("依法缴纳税收证明（2025年10月）",)], caption="依法缴纳税收证明")
    _asset_block(doc, "社保", [("依法社会保障资金证明（2025年11月）",)], caption="社会保障资金证明")
    _asset_block(doc, "社会保险权益记录单", [("江苏省社会保险权益记录单",)],
                 caption="社会保险权益记录单")
    _asset_block(doc, "信用", [("未被列入失信被执行人网站截图",),
                              ("未被列入重大税收违法失信主体",),
                              ("未被列入政府采购严重违法失信行为记录名单",)],
                 caption="信用记录查询截图")
    _asset_block(doc, "测绘资质", [("9.供应商具有有效期内的测绘资质乙级及以上资质证书",)],
                 caption="测绘资质证书", break_after=False)

    # ── （四）偏离表（横向页，照人工稿）──
    _landscape_section(doc)
    _h(doc, "（四）技术条款及相关要求偏离表", 1)
    _p(doc, "技术条款及相关要求偏离表", size=14, align="center", indent=False)
    _p(doc, f"投标人名称（盖章）：{co_name}", indent=False)
    _qual = (
        "投标人参加本次采购活动应具备下列资格条件：\n"
        "①具有独立承担民事责任的能力；\n"
        "②具有良好的商业信誉和健全的财务会计制度；\n"
        "③有依法缴纳税收和社会保障资金的良好记录；\n"
        "④具有履行合同所必需的设备和专业技术能力；\n"
        "⑤不接受联合体，不接受中标后分包；\n"
        "⑥良好的信用记录：报价截止时间之前，未被“信用中国”网站列入失信执行人、"
        "重大税收违法案件当事人名单、政府采购严重违法失信行为记录名单，未被"
        "“中国政府采购网”网站列入政府采购严重违法失信行为记录名单。"
    )
    _resp = _qual.replace("投标人参加", "我单位参加", 1)
    _table(doc, ["序号", "项目名称", "招标内容", "投标内容", "正偏离/负偏离"],
           [["1", project, _qual, _resp, "完全响应"]],
           widths=[1.11, 2.66, 9.16, 10.08, 2.09], font_size=10.5,
           row_heights=[1.67, 9.09])

    # ── （五）评分导读表（回纵向）──
    _portrait_section(doc)
    _h(doc, "（五）技术商务部分评分导读表", 1)
    _p(doc, "技术商务部分评分导读表", size=14, align="center", indent=False)
    _p(doc, f"投标人名称（盖章）：{co_name}", indent=False)
    _p(doc, f"项目编号：{no}", indent=False)
    _table(doc, ["序号", "评审项目", "响应情况", "投标文件对应页码", "自评分"],
           [["1", "投标人自2023年1月1日（含）以来承担过类似征地报批项目的，"
                  "每提供一个得2分，本项最高得6分，未提供不得分。",
             "已提供类似业绩", "见投标文件", "6"],
            ["2", "1、项目负责人具有土地类或测绘类高级及以上职称的得2分，同时具有注册"
                  "测绘师的得2分，本项最高得4分。\n2、项目组人员（除项目负责人外）中具有"
                  "国土规划类、土地类或测绘类高级及以上职称的每有一个得2分，具有中级及以上"
                  "职称的每有一个得1分，本项最多得6分。人员不重复计分，每人按最高职称计分。"
                  "【提供人员证书复印件及社保证明并加盖公章，未提供不得分】",
             "1.项目负责人具有测绘类高级职称并具有注册测绘师；\n"
             "2.项目组成员具有高级职称；\n3.提供项目组成员社保证明材料",
             "见投标文件", "9"],
            ["3", "具有有效期内的质量管理体系、职业健康安全管理体系、环境管理体系证书、"
                  "测量管理体系认证证书的，每提供1个得1分，本项最高得4分。",
             "提供质量管理体系、职业健康安全管理体系、环境管理体系证书",
             "见投标文件", "3"],
            ["4", "投标人拟投入本项目具备全站仪、GNSS接收机、无人机、CASS成图软件、"
                  "水位仪、绘图仪、测高仪，每类得1分，同类设备不重复计分，最高5分。",
             "提供全站仪、GNSS接收机、无人机、CASS成图软件、绘图仪等设备购买发票和照片",
             "见投标文件", "5"]],
           widths=[1.27, 6.56, 4.58, 2.49, 2.48], font_size=10.5,
           row_heights=[1.81, 2.22, 9.92, 2.77, 2.77])
    doc.add_page_break()

    # ── （六）服务方案 ──
    _h(doc, "（六）服务内容的详细说明及服务方案", 1)
    md = ""
    if use_llm:
        try:
            md = await _write_plan(tender)
        except Exception as e:
            logger.warning(f"[投标文件] 方案生成失败，用内置框架: {e}")
            md = ""
    _md_to_docx(doc, md or _fallback_plan(tender))
    doc.add_page_break()

    # ── （七）人员配置清单（横向页，照人工稿）──
    _landscape_section(doc)
    _h(doc, "（七）项目人员配置清单", 1)
    _p(doc, "项目人员配置清单", size=14, align="center", indent=False)
    _p(doc, f"投标人名称（盖章）：{co_name}", indent=False)
    _p(doc, f"项目编号：{no}", indent=False)
    rows = []
    for i, p in enumerate(people, 1):
        # 证书编号只认人员库的 cert_no；没有就印「—」，**不拿证书名称顶编号**。
        cert_no = str(p.get("cert_no") or "").strip() or "—"
        rows.append([str(i), p.get("name") or "", p.get("gender") or "",
                     str(p.get("age") or ""), p.get("role") or "",
                     p.get("education") or "", p.get("title") or "", cert_no])
    _table(doc, ["序号", "姓名", "性别", "年龄", "岗位名称", "学历", "职称", "资格证书编号"],
           rows, widths=[1.45, 2.75, 2.9, 2.76, 3.14, 2.36, 2.8, 6.92],
           font_size=10.5, row_heights=[0.96] + [0.94] * len(rows))
    _p(doc, "注：上述人员在项目执行过程中如需更换，须经采购人同意；"
            "擅自更换或不到位属违约行为。", size=10.5, indent=False)
    _p(doc, "拟投入人员简历：", bold=True, indent=False)
    for p in people:
        certs = "、".join(p.get("certs") or []) or "—"
        _p(doc, f"{p.get('name')}，{p.get('gender') or ''}，{p.get('age') or ''}岁，"
                f"{p.get('education') or ''}学历，{p.get('major') or ''}专业，"
                f"{p.get('title') or ''}，拟任{p.get('role') or ''}；持有证书：{certs}。",
           align="justify")

    # ── （八）其他（回纵向）──
    _portrait_section(doc)
    _h(doc, "（八）其他", 1)
    _h(doc, "1. 体系认证证书", 2)
    _asset_block(doc, "", [("质量管理体系认证",), ("环境管理体系认证",),
                           ("职业健康安全管理体系认证",),
                           ("测量管理体系认证证书",)], caption="体系认证证书")

    _h(doc, "2. 仪器设备", 2)
    _p(doc, "我方拟投入本项目的主要设备包括：全站仪、GNSS 接收机（RTK）、无人机、"
            "绘图仪、测高仪、水位仪、CASS 成图软件及图形工作站等，满足本项目勘测定界、"
            "外业调查与成果编制需要；自有设备附发票/采购合同及照片，软件附合法授权材料。",
       align="justify")
    _asset_block(doc, "", [("仪器设备-全站仪",), ("仪器设备-无人机",),
                           ("仪器设备-CASS",), ("仪器设备-绘图仪",),
                           ("仪器设备-GNSS",)], caption="仪器设备发票及照片")

    _h(doc, "3. 类似项目业绩", 2)
    _p(doc, "近年承担的类似征地报批项目如下（合同/批复复印件附后）：", align="justify")
    perf = _performance_rows()
    if perf:
        _table(doc, ["序号", "项目名称", "类型", "合同签订时间"],
               [[str(i), a, b, c] for i, (a, b, c) in enumerate(perf, 1)],
               widths=[1.5, 9.0, 3.5, 3.0])
    _asset_block(doc, "", [("1.2024年食品科技产业园",), ("2.井神绿化通道",),
                           ("3.张兴矿区",), ("4.世坤迪地块",),
                           ("5.大王庄路网",), ("6.淮阴区烈士",)], caption="业绩证明")

    _h(doc, "4. 拟投入人员证书及社保证明", 2)
    for p in people:
        pid = p.get("id")
        if pid:
            imgs = _person_images(int(pid))
            if imgs:
                _p(doc, f"{p.get('name')}（{p.get('role')}）", bold=True, indent=False)
                for nm, data in imgs[:6]:
                    _img(doc, data, 14.0, nm)

    _h(doc, "5. 声明与承诺", 2)
    for key in ("具备履行合同所必需设备和专业技术能力声明", "无重大违法记录声明",
                "中小企业声明函", "供应商独立性承诺书", "信用记录承诺书"):
        if texts.get(key):
            _p(doc, key, bold=True, indent=False)
            for para in texts[key].splitlines():
                if para.strip():
                    # 素材里的旧落款日期换成本次投标日期（「出生日期」不动）
                    para = re.sub(
                        r'(?<!出生)日期：\s*\d{4}\s*[年.\-]\s*\d{1,2}\s*[月.\-]\s*\d{1,2}\s*日',
                        f"日期：{today}", para.strip())
                    _p(doc, para, align="justify")

    # 🔴 招标文件明确「不接受联合体」时，补一份非联合体声明（人工稿有这份）
    if tender.get("no_consortium"):
        _p(doc, "非联合体投标声明", bold=True, indent=False)
        _p(doc, f"我单位郑重声明：本项目由{co_name}独立投标，未与其他单位组成联合体，"
                "未以任何形式借用或挂靠其他单位资质；中标后由我单位独立履行合同义务，"
                "不进行转包和违法分包。如有不实，愿承担相应法律责任。", align="justify")

    _h(doc, "6. 单位名称变更材料", 2)
    _p(doc, "淮安纵横测绘有限公司于 2022 年 12 月 1 日名称变更为江苏众拓测绘有限公司，"
            "原公司签订的项目合同、资质证书及相关业绩均由变更后的公司承继。"
            "现将名称变更材料附后。", align="justify")
    _asset_block(doc, "单位名称变更", [("单位名称变更材料",)],
                 caption="单位名称变更材料")

    # 身份证复印件已按招标模板贴在「（三）2. 法定代表人授权委托书」之后

    # 正文 12pt 统一 1.5 倍行距（对齐人工稿）；表格上下左右居中；页脚页码
    _apply_body_format(doc)
    _center_all_cells(doc)
    _page_number_footer(doc)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    try:
        if _stamp_toc(out):
            logger.info("[投标文件] 目录已写入真实页码")
    except Exception as e:
        logger.warning(f"[投标文件] 目录页码写入失败，保留目录域: {e}")
    logger.info(f"[投标文件] 已生成 {out}（{len(people)} 人，报价 {small} 万元）")
    return out


def _company_addr(name: str) -> str:
    return {"江苏众拓测绘有限公司": "淮安经济技术开发区海口路9号1幢1211室"}.get(name, "")


def _company_contact(name: str) -> str:
    return {"江苏众拓测绘有限公司": "张红艳 15949194424"}.get(name, "")


def _performance_rows() -> List[Tuple[str, str, str]]:
    """从 company_assets / asset_images 里取业绩（案例中 6 项）。"""
    names = [
        ("2024年食品科技产业园部分地块征地报批", "征地报批", "2024.6"),
        ("井神绿化通道项目征地服务", "征地报批", "2025.5"),
        ("张兴矿区采输卤扩建项目征地服务（含稳评）", "征地报批/稳评", "2025.6"),
        ("世坤迪地块征地报批（含稳评及林地报批）服务", "征地报批", "2025.8"),
        ("大王庄路网接线提升改造工程用地报批咨询服务", "用地报批", "2025.9"),
        ("淮阴区烈士集中安葬区项目社会稳定风险评估服务", "稳评", "2025.10"),
    ]
    return names


def _asset_block(doc, category_kw: str, names: Sequence[Tuple[str, ...]], caption="",
                 *, break_after=True):
    """按图片名插入公司档案图片（不一定全有，有几张插几张）。"""
    count = 0
    for name_tuple in names:
        kw = name_tuple[0]
        for nm, data in _asset_images(kw, limit=20):
            if count == 0 and caption:
                _p(doc, caption, bold=True, indent=False)
            _img(doc, data, 15.0)
            if break_after:
                doc.add_page_break()
            count += 1
    if count == 0 and caption:
        _p(doc, f"（{caption}：见公司档案原件）", indent=False)
