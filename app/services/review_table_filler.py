"""评审表 —— **就地填模板**（不是重建表格）。

人工模板《模版（洪泽征地稳评报告）.docx》里本来就有一张 22 行 × 7 栅格的
江苏省统一制式评审表（含大量跨行/跨列合并、第一列复合大标题单元格）。
本模块的做法是：

1. 从模板里把「评审表区间」的 XML 元素整块搬过来
   （目录 SDT 之后 → 「第二章」之前），原样保留版式；
2. **就地改文字**：表头 4 行 + 22 行主表的各个值单元格；
3. 删掉模板里的「第一章 稳评评审报告表」标题段——评审表是不编号前置部分。

内容全部取自正文与事实卡（`review_table_service._build_review_data` 是唯一装配点）。
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any, Optional

from docx.oxml.ns import qn
from lxml import etree

logger = logging.getLogger(__name__)

# 模板评审表的行号 → (值列下标, 数据键)
# 行号按模板原样固定（表结构是制式的，不随项目变）
_ROW_MAP = {
    3:  (3, "org"),            # 稳评责任单位·单位名称
    4:  (3, "org_leader"),     # 稳评责任单位·负责人
    5:  (3, "org_contact"),    # 稳评责任单位·联系人
    6:  (3, "impl_unit"),      # 稳评实施单位·单位名称
    7:  (3, "impl_leader"),    # 稳评实施单位·负责人
    8:  (3, "impl_contact"),   # 稳评实施单位·联系人
    9:  (2, "participants"),   # 参与评估单位
    10: (2, "overview"),       # 决策概况（7 条）
    11: (2, "legal_basis"),    # 决策法律政策依据及必要性
    12: (2, "stakeholders"),   # 利益相关者情况
    13: (2, "demands"),        # 各方意见·利益相关者诉求
    14: (2, "grassroots"),     # 各方意见·基层组织意见
    15: (2, "gov_opinion"),    # 各方意见·党委政府或相关部门意见
    16: (2, "risk_factors"),   # 主要风险因素
    17: (2, "risk_measures"),  # 降低风险措施
    18: (2, "conclusion"),     # 稳评实施单位结论
    19: (2, "review_opinion"), # 评审意见
    20: (2, "final_conclusion"),  # 稳评结论
    21: (2, "filing"),         # 备案情况
}

# 行 4/5/7/8 的「职务 / 联系电话」在第 5 列
_ROW_MAP_EXTRA = {
    4: (5, "org_leader_title"),
    5: (5, "org_phone"),
    7: (5, "impl_leader_title"),
    8: (5, "impl_phone"),
}

# 适用程序：模板 r0/r1/r2 分别对应 简易 / 一般 / 特殊，勾在第 6 列
_PROGRAM_ROWS = {"简易程序": 0, "一般程序": 1, "特殊程序": 2}

# 需要替换文字的表头段落（模板原文 → 数据键）
_HEAD_PREFIX = [
    ("决策事项名称：", "name"),
    ("稳评责任单位：", "org"),
    ("稳评实施单位：", "impl_unit"),
    ("填 表  日 期：", "fill_date"),
]


# ═══════════════════════════════════════════════════════════════
# 文本写入（保留单元格原格式）
# ═══════════════════════════════════════════════════════════════

def _first_rpr(cell):
    """取单元格里第一个带格式的 run 的 rPr，用于新文字沿用原字体字号。"""
    for p in cell.paragraphs:
        for r in p.runs:
            if r._element.rPr is not None:
                return deepcopy(r._element.rPr)
    return None


def _strip_fill_marks(rpr):
    """去掉 rPr 里的**填充提示记号**（`highlight` / 字符底纹）。

    🔴 制式模板里把「待填」的格子标成黄色底纹当记号（洪泽模板的
       「决策事项名称」与「稳评责任单位·单位名称」值格就是 highlight=yellow）。
       我们填字时沿用了原格的 rPr，结果**填进去的字也是黄的** —— 交付稿不能看。
    """
    if rpr is None:
        return rpr
    for _tag in ('w:highlight', 'w:shd'):
        for _e in rpr.findall(qn(_tag)):
            rpr.remove(_e)
    return rpr

def _set_cell(cell, text: str) -> None:
    """把单元格内容换成 `text`（支持换行），字体字号沿用它原有的格式。"""
    rpr = _strip_fill_marks(_first_rpr(cell))
    paras = cell.paragraphs
    if not paras:
        return
    # 保留第一段，删掉其余段
    for p in paras[1:]:
        p._element.getparent().remove(p._element)
    p0 = cell.paragraphs[0]
    # 清掉第一段的全部 run
    for r in list(p0.runs):
        r._element.getparent().remove(r._element)

    lines = str(text or "").split("\n")
    from docx.oxml import OxmlElement
    for i, line in enumerate(lines):
        para = p0 if i == 0 else cell.add_paragraph()
        run = para.add_run(line)
        if rpr is not None:
            run._element.insert(0, deepcopy(rpr))
        elif i == 0 and p0.style is not None:
            pass


def _replace_para_text(p_el, new_text: str) -> bool:
    """把段落（lxml 元素）的可见文字换成 new_text，保留首个 run 的格式。"""
    runs = p_el.findall(qn('w:r'))
    if not runs:
        return False
    # 找第一个有 w:t 的 run 作为格式模板
    keep = None
    for r in runs:
        if r.find(qn('w:t')) is not None:
            keep = r
            break
    if keep is None:
        return False
    keep_rpr = _strip_fill_marks(keep.find(qn('w:rPr')))
    for r in runs:
        p_el.remove(r)
    from docx.oxml import OxmlElement
    r = OxmlElement('w:r')
    if keep_rpr is not None:
        r.append(deepcopy(keep_rpr))
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = new_text
    r.append(t)
    p_el.append(r)
    return True


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def _template_review_region(template_path: str):
    """从模板 docx 里取出「评审表区间」的元素列表（未拷贝的原树元素）。

    区间 = 目录 SDT 之后  →  以「第二章」开头的段落之前；
    并顺带返回模板 Document（保持引用，避免元素被回收）。
    """
    from docx import Document as _Doc
    tpl = _Doc(template_path)
    body = tpl.element.body
    kids = list(body.iterchildren())

    sdt_idx = None
    for i, ch in enumerate(kids):
        if ch.tag.split('}')[1] != 'sdt':
            continue
        # 🔴 必须是**真正的目录域** `TOC \o`。只查 `'TOC '` 会被投标文件命中 ——
        #    它的内容控件里恰好含「TOC 」字样，于是把整份投标文件当成了「目录之后到第二章」的区间。
        if 'TOC \\o' in etree.tostring(ch, encoding='unicode'):
            sdt_idx = i
            break

    start = (sdt_idx + 1) if sdt_idx is not None else 0
    end = len(kids)
    for i in range(start, len(kids)):
        ch = kids[i]
        if ch.tag.split('}')[1] != 'p':
            continue
        txt = ''.join(t.text or '' for t in ch.iter(qn('w:t'))).strip()
        if re.match(r'^第二章\b', txt):
            end = i
            break

    region = [ch for ch in kids[start:end]]
    # 去掉模板自带的「第一章 稳评评审报告表」章标题（评审表不编号）
    region = [ch for ch in region
              if not (ch.tag.split('}')[1] == 'p' and
                      re.match(r'^第一章\s*稳评评审报告表\s*$',
                               ''.join(t.text or '' for t in ch.iter(qn('w:t'))).strip()))]
    return tpl, region


# 评审表制式：22 行 × 7 栅格（江苏省统一制式）
_REVIEW_ROWS, _REVIEW_COLS = 22, 7
# 区间元素数上限：正常模板 17–40 个；投标文件那种 1125 个一律判错
_REGION_MAX_ELEMENTS = 120


def _is_review_region(region: list) -> bool:
    """判断区间是不是「一张 22×7 的制式评审表」。

    🔴 旧判据只是 `any(tbl)` —— 形同虚设：投标文件（首表 12×9）、金湖稳评
    （9 张表、575 个元素）全都能通过，而字典序第一的投标文件会先中。
    """
    if not region or len(region) > _REGION_MAX_ELEMENTS:
        return False
    tbls = [c for c in region if c.tag.split('}')[1] == 'tbl']
    if len(tbls) != 1:
        return False
    try:
        rows = tbls[0].findall(qn('w:tr'))
        if len(rows) != _REVIEW_ROWS:
            return False
        # 取所有行的最大栅格宽度（制式表含跨列合并，行内 tc 数不总是 7）
        width = 0
        for r in rows:
            w = 0
            for tc in r.findall(qn('w:tc')):
                gs = tc.find(qn('w:tcPr'))
                span = 1
                if gs is not None:
                    _g = gs.find(qn('w:gridSpan'))
                    if _g is not None:
                        try:
                            span = int(_g.get(qn('w:val')) or 1)
                        except (TypeError, ValueError):
                            span = 1
                w += span
            width = max(width, w)
        return width == _REVIEW_COLS
    except Exception:
        return False


def fill_review_table_into(doc, filled_data: dict, chapters: dict = None,
                           extracted: dict = None, template_path: Optional[str] = None) -> bool:
    """把「就地填好的模板评审表」插入到 `doc` 末尾。

    Returns: 成功返回 True；模板不可用/结构不符返回 False（调用方回退旧渲染）。
    """
    import os

    # 🔴 候选按「稳评优先」排序（DB 洪泽 → 评审表 → 稳评 → 磁盘扫描兜底）。
    #    绝不能再裸 `sorted(glob("*.docx"))` —— 那会把投标文件排到第一。
    from app.services.template_chapter_chunker import rank_template_candidates
    _cands = rank_template_candidates(extra_first=template_path)

    tpl = region = None
    for _p in _cands:
        try:
            _t, _r = _template_review_region(_p)
        except Exception as e:
            logger.debug(f"读取模板评审表失败 {_p}: {e}")
            continue
        if _is_review_region(_r):
            tpl, region, _src = _t, _r, _p
            logger.info(f"[评审表] 采用模板 {os.path.basename(_p)}（区间 {len(_r)} 元素）")
            break
        logger.debug(f"[评审表] 跳过 {os.path.basename(_p)}："
                     f"区间 {len(_r or [])} 元素，不符 22×7 制式")
    if not region:
        logger.warning("[评审表] 未找到含 22×7 制式评审表的模板，回退旧渲染")
        return False

    from app.services.review_table_service import _build_review_data
    d = _build_review_data(filled_data or {}, chapters or {}, extracted or {})

    # 补齐 _build_review_data 未覆盖的栏目
    d.setdefault("participants", "/")
    d.setdefault("review_opinion", "\n\n\n组长（签章）：\n　　　　　　　　年　　月　　日")
    d.setdefault("final_conclusion",
                 f"决策社会稳定风险等级为{d.get('risk_level', '低风险')}，"
                 f"在我单位积极落实专家评审意见以及报告中各项风险防范和化解措施后，决策风险可控。"
                 f"本评估结果已经本单位党委集体讨论研究通过，可以报同级政法委备案，决策可以实施。"
                 f"\n\n\n负责人（签章）：\n　　　　　　　　年　　月　　日")
    d.setdefault("filing", "\n\n\n备案单位（签章）：\n　　　　　　　　年　　月　　日")
    # 🔴 这里原先写的是 `d.setdefault("impl_leader_title", "法人")` ——
    #    对着**所有公司**兜一句写死的「法人」。多租户下这是错的：
    #    职务本身按公司不同（法定代表人 / 总经理 / 董事长…），
    #    取不到就该显示占位，让人知道去「公司信息」页补，
    #    而不是印一个可能不实的职务。真值见 companies.leader_title。
    d.setdefault("impl_leader_title", "【待填写】")
    # 🔴 原来这里写的是 `d.get("org_phone", "")` —— 把**责任单位**的电话抄给
    #    **实施单位**（张冠李戴）。`impl_phone` 现在由 `_build_review_data`
    #    统一按「用户填 > 材料 > 【待填写】」给值，这里只兜底一个占位。
    d.setdefault("impl_phone", "【待填写】")

    # ── ① 复制区间元素到目标文档 ──
    _copies = [deepcopy(el) for el in region]

    # 🔴 剥离区间内的 `w:sectPr`：模板里夹带的节属性会**另起一节**并带入它自己的
    #    纸张方向/页边距（实测把报告第 0 节变成了 A4 横向）。评审表不需要分节。
    def _strip_sectpr(node):
        for sp in node.findall('.//' + qn('w:sectPr')):
            sp.getparent().remove(sp)
        # 段落级 sectPr 在 pPr 里，上面已覆盖；再兜一层 body 级
        for sp in node.findall(qn('w:sectPr')):
            sp.getparent().remove(sp)
    for el in _copies:
        _strip_sectpr(el)

    _body = doc.element.body
    _sect = _body.find(qn('w:sectPr'))
    for el in _copies:
        if _sect is not None:
            _sect.addprevious(el)
        else:
            _body.append(el)

    # ── ② 填表头 4 行 ──
    for el in _copies:
        if el.tag.split('}')[1] != 'p':
            continue
        txt = ''.join(t.text or '' for t in el.iter(qn('w:t'))).strip()
        for _pre, _key in _HEAD_PREFIX:
            if txt.startswith(_pre):
                _replace_para_text(el, f"{_pre}{d.get(_key, '') or ''}")
                # 🔴 表头四项（决策事项名称/责任单位/实施单位/填表日期）要与**表格左边缘对齐**：
                #    模板里这几段带 0.99cm 首行缩进 + 两端对齐，实际渲染会跟表格错位，
                #    整块看起来不齐（业主反馈「这几项内容的格式不对」）。
                try:
                    _pPr = el.find(qn('w:pPr'))
                    if _pPr is None:
                        from docx.oxml import OxmlElement as _OE
                        _pPr = _OE('w:pPr'); el.insert(0, _pPr)
                    for _tag in ('w:ind', 'w:jc'):
                        for _old in _pPr.findall(qn(_tag)):
                            _pPr.remove(_old)
                    from docx.oxml import OxmlElement as _OE2
                    _ind = _OE2('w:ind')
                    _ind.set(qn('w:firstLine'), '0'); _ind.set(qn('w:left'), '0')
                    _pPr.append(_ind)
                    _jc = _OE2('w:jc'); _jc.set(qn('w:val'), 'left')
                    _pPr.append(_jc)
                except Exception as _e:
                    logger.debug(f"表头段格式调整失败: {_e}")
                break

    # ── ③ 填 22 行主表 ──
    _tbls = [el for el in _copies if el.tag.split('}')[1] == 'tbl']
    if not _tbls:
        return False
    from docx.table import Table
    tbl = Table(_tbls[0], doc)

    def _put(r: int, c: int, val: Any) -> None:
        try:
            cell = tbl.cell(r, c)
        except Exception:
            return
        v = val
        if isinstance(v, (list, tuple)):
            v = "\n".join(str(x) for x in v)
        _set_cell(cell, str(v or ""))

    for r, (c, key) in _ROW_MAP.items():
        if r < len(tbl.rows):
            _put(r, c, d.get(key, ""))
    # r0/r1/r2 的「决策事项名称」值单元格（与「适用程序」同行）
    for _r in (0, 1, 2):
        if _r < len(tbl.rows):
            _put(_r, 2, d.get("name", ""))
    for r, (c, key) in _ROW_MAP_EXTRA.items():
        if r < len(tbl.rows):
            _put(r, c, d.get(key, ""))

    # 适用程序打勾
    _prog = str(d.get("program") or "一般程序")
    _tick_row = _PROGRAM_ROWS.get(_prog, 1)
    for _name, _ri in _PROGRAM_ROWS.items():
        if _ri < len(tbl.rows):
            _put(_ri, 6, "√" if _ri == _tick_row else "")

    # 🔴 表题「江苏省淮安市社会稳定风险评估评审表」：**黑体 二号(22pt)**，
    #    与下方「决策事项名称：…」之间留**两行**（业主逐条指出）。
    #    模板里这条是仿宋22pt、前后各垫了 5~7 个空段。
    try:
        _title_p = None
        for _el in (doc.element.body).iterchildren():
            if _el.tag.split('}')[1] != 'p':
                continue
            _txt = ''.join(t.text or '' for t in _el.iter(qn('w:t'))).strip()
            if '社会稳定风险评估评审表' in _txt and len(_txt) <= 24:
                _title_p = _el
                break
        if _title_p is not None:
            for _r in _title_p.findall(qn('w:r')):
                _rpr = _r.find(qn('w:rPr'))
                if _rpr is None:
                    from docx.oxml import OxmlElement as _OE6
                    _rpr = _OE6('w:rPr'); _r.insert(0, _rpr)
                for _tag in ('w:rFonts', 'w:sz', 'w:szCs', 'w:b'):
                    for _old in _rpr.findall(qn(_tag)):
                        _rpr.remove(_old)
                from docx.oxml import OxmlElement as _OE7
                _f = _OE7('w:rFonts')
                for _a in ('w:ascii', 'w:hAnsi', 'w:eastAsia'):
                    _f.set(qn(_a), '黑体')
                _rpr.append(_f)
                _b = _OE7('w:b'); _rpr.append(_b)
                _sz = _OE7('w:sz'); _sz.set(qn('w:val'), '44'); _rpr.append(_sz)   # 22pt = 44 半点
                _szc = _OE7('w:szCs'); _szc.set(qn('w:val'), '44'); _rpr.append(_szc)
            # 标题之后：空段收敛到**恰好两行**
            _nxt, _blanks = _title_p.getnext(), []
            while _nxt is not None and _nxt.tag.split('}')[1] == 'p' \
                    and not ''.join(t.text or '' for t in _nxt.iter(qn('w:t'))).strip():
                _blanks.append(_nxt)
                _nxt = _nxt.getnext()
            for _b in _blanks[2:]:
                _b.getparent().remove(_b)
            while len(_blanks) < 2:
                from docx.oxml import OxmlElement as _OE8
                _np = _OE8('w:p')
                _title_p.addnext(_np)
                _blanks.append(_np)
                if len(_blanks) >= 2:
                    break
            logger.info("[评审表] 表题已设黑体二号，其后留两行")
    except Exception as _te:
        logger.debug(f"评审表表题格式调整跳过: {_te}")

    # 🔴 正文栏格式统一（与独立评审表 `generate_review_table` 一致）：
    #      ① 内容段首行缩进 2 字符（条目标题行不缩进 —— 业主：「只针对内容」）；
    #      ② 小标题行之后隔一行；
    #      ③ **第11行「法律政策依据及必要性」不缩进**（业主：那是法规清单，缩进反而乱），
    #         只做两端对齐。
    #    模板自带的格式各栏不一（实测「实施单位结论」栏缩进 0，其余 22pt），
    #    也不会做「小标题隔行」。统一在这里做，两条路径产出一致。
    try:
        from docx.shared import Pt as _Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH as _AL
        _SUB = re.compile(r'^(?:（[一二三四五六七八九十]+）|\d{1,2}[.、]\s*)\S{2,26}$')
        _NO_INDENT_ROWS = {11}          # 法规清单栏：不缩进，只两端对齐
        _fixed = 0
        for _r in range(10, min(22, len(tbl.rows))):
            try:
                _cell = tbl.cell(_r, 2)
            except Exception:
                continue
            _plain = _r in _NO_INDENT_ROWS
            # 🔴 第10行「决策概况」：每条拆成**标题一段 / 内容一段**。
            #    业主口径（最新）：**小标题也要首行缩进 2 字符**，且小标题底下
            #    **不再隔行**（此前要求「小标题后隔一行」，已按最新口径改回）。
            if _r == 10:
                _items = [str(x).strip() for x in (d.get('overview') or []) if str(x).strip()]
                if _items:
                    for _p in list(_cell.paragraphs)[1:]:
                        _p._element.getparent().remove(_p._element)
                    _p0 = _cell.paragraphs[0]
                    for _r0 in list(_p0.runs):
                        _r0._element.getparent().remove(_r0._element)
                    _rpr = _first_rpr(_cell)
                    _firstp = True
                    for _it in _items:
                        _m = re.match(r'^(\d{1,2}[.、]\s*\S{2,24}?)\s+(\S.*)$', _it)
                        _segs = [(_m.group(1), False), (_m.group(2), True)] if _m else [(_it, True)]
                        for _seg, _isbody in _segs:
                            _np = _p0 if _firstp else _cell.add_paragraph()
                            _firstp = False
                            _nr = _np.add_run(_seg)
                            if _rpr is not None:
                                _nr._element.insert(0, deepcopy(_rpr))
                            # 标题与内容**都**首行缩进 2 字符
                            _np.paragraph_format.first_line_indent = _Pt(21)
                            _np.alignment = _AL.JUSTIFY
                    _fixed += 1
                    continue
            _ps = list(_cell.paragraphs)
            for _pp in _ps:
                _t = _pp.text.strip()
                if not _t:
                    continue
                if _plain:
                    _pp.paragraph_format.first_line_indent = _Pt(0)
                    _pp.alignment = _AL.JUSTIFY
                else:
                    _pp.paragraph_format.first_line_indent = _Pt(21)
        if _fixed:
            logger.info(f"[评审表] 正文栏格式统一：补小标题空行 {_fixed} 处")
    except Exception as _fe:
        logger.debug(f"评审表正文栏格式统一跳过: {_fe}")

    # 🔴 全表清理模板的**填充提示记号**：制式模板用黄色底纹标「待填」格，
    #    段落标记的 rPr 里也带 highlight —— 只清 run 上的话，
    #    文字后面还会留一片黄底（业主：「全是黄色背景」）。
    _cleaned = 0
    try:
        for _e in _tbls:
            for _hl in list(_e.iter(qn('w:highlight'))):
                _hl.getparent().remove(_hl)
                _cleaned += 1
            for _shd in list(_e.iter(qn('w:shd'))):
                _fill = str(_shd.get(qn('w:fill')) or '').upper()
                if _fill and _fill not in ('AUTO', 'FFFFFF', 'NONE'):
                    _shd.getparent().remove(_shd)
                    _cleaned += 1
    except Exception as _ce:
        logger.debug(f"评审表底纹清理跳过: {_ce}")
    if _cleaned:
        logger.info(f"[评审表] 清理模板填充记号 {_cleaned} 处")

    logger.info(f"[评审表] 就地填模板完成（{len(tbl.rows)} 行），来源 {os.path.basename(_src)}")
    return True
