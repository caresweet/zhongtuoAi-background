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
from typing import Optional

from docx.oxml.ns import qn

logger = logging.getLogger(__name__)

# 需要替换文字的表头封面行（模板写法不一，按**去空白后**的前缀匹配 → 数据键）。
# 金湖/淮安市写「事 项  名 称」「项 目 名 称」，洪泽/淮阴区写「决策事项名称」。
_HEAD_PREFIX = [
    ("决策事项名称", "name"),
    ("事项名称", "name"),
    ("项目名称", "name"),
    ("稳评责任单位", "org"),
    ("稳评实施单位", "impl_unit"),
    ("项目实施单位", "impl_unit"),
    ("填表日期", "fill_date"),
]


def _norm_label(s: str) -> str:
    return re.sub(r'\s+', '', s or "")


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

def _set_cell(cell, text: str, *, bold: bool = False) -> None:
    """把单元格内容换成 `text`（支持换行），字体字号沿用它原有的格式。

    🔴 模板里内容单元格的第一个 run 往往是加粗的小标题，照抄 rPr 会让
       **整格正文都变粗**（业主 2026-11-12：「法规内容/降低风险措施字体还是
       加粗了」）。这里复制 rPr 时去掉 w:b/w:bCs，只有标签列（bold=True）再加粗。
    """
    rpr = _strip_fill_marks(_first_rpr(cell))
    if rpr is not None:
        rpr = deepcopy(rpr)
        for _tag in ('w:b', 'w:bCs'):
            for _e in rpr.findall(qn(_tag)):
                rpr.remove(_e)
        if bold:
            from docx.oxml import OxmlElement as _OE
            rpr.append(_OE('w:b'))
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


def _replace_head_value(p_el, value: str) -> bool:
    """只替换封面行「：」之后的值，保留标签原写法、下划线与尾部填空线长度。"""
    from docx.oxml import OxmlElement
    runs = p_el.findall(qn('w:r'))
    if not runs:
        return False
    colon_idx = None
    for i, r in enumerate(runs):
        t = ''.join(x.text or '' for x in r.findall(qn('w:t')))
        if '：' in t or ':' in t:
            colon_idx = i
            break
    if colon_idx is None:
        # 标签与值在同一个 run：整体重写（洪泽等无下划线模板）
        full = ''.join(''.join(x.text or '' for x in r.findall(qn('w:t')))
                       for r in runs)
        m = re.match(r'^(.*?[：:])(.*)$', full)
        if not m:
            return False
        return _replace_para_text(p_el, m.group(1) + str(value))
    val_runs = runs[colon_idx + 1:]
    if not val_runs:
        from copy import deepcopy
        r = OxmlElement('w:r')
        rpr = runs[colon_idx].find(qn('w:rPr'))
        if rpr is not None:
            r.append(deepcopy(rpr))
        p_el.append(r)
        val_runs = [r]
    keep = val_runs[0]
    old = ''.join(x.text or '' for x in keep.findall(qn('w:t')))
    trail = len(old) - len(old.rstrip(' '))
    for x in keep.findall(qn('w:t')):
        keep.remove(x)
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = str(value) + ' ' * max(trail, 6)
    keep.append(t)
    for r in val_runs[1:]:
        p_el.remove(r)
    return True


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def _template_review_region(template_path: str):
    """从模板 docx 里取出「评审表区间」的元素列表（未拷贝的原树元素）。

    区间按**表格本身**定位（2026-11-23 改）：
      起点 = 评审表标题段（含「评审表」）→ 终点 = 表格之后第一个章标题之前。
    旧实现用「目录 SDT 之后 → 第二章之前」，金湖/淮安等模板没有 `TOC \\o` 或
    不写「第二章」，整个前置区（575 个元素）都被当成评审表而判废，最后六套
    模板全都退回洪泽表 —— 金湖的「事项名称/淮安市委政法委制」版式就丢了。
    """
    from docx import Document as _Doc
    tpl = _Doc(template_path)
    body = tpl.element.body
    kids = list(body.iterchildren())

    def _txt(el):
        return ''.join(t.text or '' for t in el.iter(qn('w:t'))).strip()

    def _grid_width(tbl):
        width = 0
        for r in tbl.findall(qn('w:tr')):
            w = 0
            for tc in r.findall(qn('w:tc')):
                span = 1
                tp = tc.find(qn('w:tcPr'))
                if tp is not None:
                    g = tp.find(qn('w:gridSpan'))
                    if g is not None:
                        try:
                            span = int(g.get(qn('w:val')) or 1)
                        except (TypeError, ValueError):
                            span = 1
                w += span
            width = max(width, w)
        return width

    # ① 找评审表：18~26 行、6/7 栅格、含「评审/事项名称/责任单位」
    #    （洪泽/金湖/淮安市/清江浦区是 7 栅格 21/22 行；淮安区是 6 栅格 20 行、
    #      淮阴区是 7 栅格 24 行 —— 制式相同但行列略有出入）
    tbl_pos = None
    for i, ch in enumerate(kids):
        if ch.tag.split('}')[1] != 'tbl':
            continue
        rows = ch.findall(qn('w:tr'))
        if not (18 <= len(rows) <= 26):
            continue
        if _grid_width(ch) not in (6, 7):
            continue
        all_text = ''.join(t.text or '' for t in ch.iter(qn('w:t')))
        # 🔴 必须是稳评评审表：两个责任单位栏目同时存在才算
        #    （投标文件里偶有「评审」「事项名称」字样，只靠关键词会误判）
        if '稳评责任单位' not in all_text or '稳评实施单位' not in all_text:
            continue
        if '评审' in all_text or '事项名称' in all_text:
            tbl_pos = i
            break
    if tbl_pos is None:
        return tpl, []

    # ② 起点：表格之前最近一个「…评审表」标题段（不含章标题）
    #    🔴 不要遇到分节符就停：淮安区模板在「制表单位」行后插了分节符，
    #       表格在下一节 —— 停了就把标题与四项留在模板里，报告里只剩一张表。
    start = tbl_pos
    for j in range(tbl_pos - 1, max(-1, tbl_pos - 40), -1):
        el = kids[j]
        if el.tag.split('}')[1] != 'p':
            continue
        t = _txt(el)
        if '评审表' in t and len(t) <= 40:
            start = j
        if re.match(r'^第[零一二三四五六七八九十]+章', t):
            break

    # ③ 终点：表格之后第一个章标题（Heading 1 或「第X章」）之前
    #    🔴 模板的 pStyle 存的是样式 id，金湖是「3」、淮安市是「2」，
    #       不能按 id 字面量判断 —— 先按样式名把 Heading 1 的 id 找出来。
    _h1_ids = set()
    try:
        for _st in tpl.styles:
            if getattr(_st, "name", "") == "Heading 1":
                _h1_ids.add((getattr(_st, "style_id", "") or "").lower())
    except Exception:
        pass
    end = len(kids)
    for j in range(tbl_pos + 1, min(len(kids), tbl_pos + 120)):
        el = kids[j]
        if el.tag.split('}')[1] != 'p':
            continue
        t = _txt(el)
        if not t:
            continue
        ppr = el.find(qn('w:pPr'))
        st = ppr.find(qn('w:pStyle')) if ppr is not None else None
        style = ((st.get(qn('w:val')) if st is not None else '') or '').lower()
        if style and (style in _h1_ids or style in ('1', 'heading1')):
            end = j
            break
        if re.match(r'^第[零一二三四五六七八九十]+章', t):
            end = j
            break

    region = [ch for ch in kids[start:end]]
    # 去掉模板自带的「第X章 评审表」章标题（评审表不编号）
    region = [ch for ch in region
              if not (ch.tag.split('}')[1] == 'p' and
                      re.match(r'^(第[零一二三四五六七八九十]+章|\d+)\s*[、.．]?\s*'
                               r'(稳评|社会稳定风险评估)?评审(报告)?表',
                               _txt(ch)))]
    return tpl, region


# 评审表制式：行数 18~26、栅格 6/7（各地模板略有出入，由标签文字定位）
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
        if not (18 <= len(rows) <= 26):
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
        return width in (6, 7)
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
    _head_paras = []          # 四项（决策事项名称/责任单位/实施单位/填表日期）
    for el in _copies:
        if el.tag.split('}')[1] != 'p':
            continue
        txt = ''.join(t.text or '' for t in el.iter(qn('w:t'))).strip()
        _flat = _norm_label(txt)
        for _pre, _key in _HEAD_PREFIX:
            if _flat.startswith(_pre):
                _replace_head_value(el, str(d.get(_key, '') or ''))
                _head_paras.append(el)
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

    # ── ③ 填主表（按**标签文字**定位，兼容 20/21/22/24 行与 6/7 栅格）──
    _tbls = [el for el in _copies if el.tag.split('}')[1] == 'tbl']
    if not _tbls:
        return False
    from docx.table import Table
    tbl = Table(_tbls[0], doc)

    def _put_val(cell, val) -> None:
        v = val
        if isinstance(v, (list, tuple)):
            v = "\n".join(str(x) for x in v)
        _set_cell(cell, str(v or ""), bold=False)

    def _set_after(row, label: str, val) -> bool:
        """在 row 里找到 label 单元格，把值写进它右边第一个**非合并重复**的格。"""
        cells = row.cells
        for ci, c in enumerate(cells):
            if label not in _norm_label(c.text):
                continue
            prev_tc = c._tc
            for cj in range(ci + 1, len(cells)):
                if cells[cj]._tc is prev_tc:
                    continue
                _put_val(cells[cj], val)
                return True
        return False

    # 事项名称（模板写法：事项名称 / 决策事项名称 / 项目名称）
    _name_done = False
    for _lab in ("事项名称", "项目名称"):
        for _r in tbl.rows[:3]:
            if _set_after(_r, _lab, d.get("name", "")):
                _name_done = True
                break
        if _name_done:
            break

    # 适用程序打勾（最后一个是勾选格）
    _prog = str(d.get("program") or "一般程序")
    for _r in tbl.rows[:4]:
        for _c in _r.cells:
            _t = _norm_label(_c.text)
            if _t in ("简易程序", "一般程序", "特殊程序"):
                _put_val(_r.cells[-1], "√" if _t == _prog else "")

    # 稳评责任单位 / 稳评实施单位 两块（单位名称/负责人/职务/联系人/电话）
    _block = None
    for _r in tbl.rows:
        _joined = _norm_label("".join(c.text for c in _r.cells))
        if "稳评责任单位" in _joined and "单位名称" in _joined:
            _block = "org"
            _set_after(_r, "单位名称", d.get("org", ""))
            continue
        if "稳评实施单位" in _joined and "单位名称" in _joined:
            _block = "impl"
            _set_after(_r, "单位名称", d.get("impl_unit", ""))
            continue
        if "参与评估单位" in _joined:
            _block = None
            _set_after(_r, "参与评估单位", d.get("participants", "/"))
            continue
        if _block and "负责人" in _joined:
            _set_after(_r, "负责人", d.get(
                "org_leader" if _block == "org" else "impl_leader", ""))
            if "职务" in _joined:
                _set_after(_r, "职务", d.get(
                    "org_leader_title" if _block == "org" else "impl_leader_title", ""))
            continue
        if _block and "联系人" in _joined:
            _set_after(_r, "联系人", d.get(
                "org_contact" if _block == "org" else "impl_contact", ""))
            for _lab in ("联系电话", "联络方式", "联络电话", "电话"):
                if _norm_label(_lab) in _joined:
                    _set_after(_r, _lab, d.get(
                        "org_phone" if _block == "org" else "impl_phone", ""))
                    break
            continue

    # 「主要风险因素以及降低风险措施」合在一格的模板（清江浦区）先处理
    _combined_rows = set()
    for _r in tbl.rows:
        _joined = _norm_label("".join(c.text for c in _r.cells))
        if "主要风险因素" in _joined and "降低风险措施" in _joined:
            _put_val(_r.cells[-1],
                     f"{d.get('risk_factors', '')}\n\n{d.get('risk_measures', '')}")
            _combined_rows.add(id(_r._tr))

    for _lab, _key in (
        ("决策事项概况", "overview"), ("决策概况", "overview"),
        ("法律政策依据", "legal_basis"),
        ("利益相关者情况", "stakeholders"),
        ("主要风险因素", "risk_factors"),
        ("降低风险措施", "risk_measures"),
        ("稳评实施单位结论", "conclusion"),
        ("评审意见", "review_opinion"), ("审核意见", "review_opinion"),
        ("稳评结论", "final_conclusion"),
        ("备案情况", "filing"),
    ):
        for _r in tbl.rows:
            if id(_r._tr) in _combined_rows:
                continue
            if _set_after(_r, _lab, d.get(_key, "")):
                break

    # 各方意见三小节
    for _lab, _key in (
        ("利益相关者诉求", "demands"),
        ("基层组织意见", "grassroots"),
        ("党委政府或相关部门意见", "gov_opinion"),
    ):
        for _r in tbl.rows:
            if _set_after(_r, _lab, d.get(_key, "")):
                break

    # 🔴 表题「江苏省淮安市社会稳定风险评估评审表」：**黑体 二号(22pt)**。
    #    只在**复制进来的模板区间**里找 —— 装配器在本页之前还有一个章标题页
    #    「淮安市社会稳定风险评估评审表」，按文档顺序取第一条会改错对象。
    #    模板里这条是仿宋22pt、前后各垫了 5~7 个空段。
    try:
        _title_p = None
        for _el in _copies:
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
            # 🔴 标题 → 四项的竖向留白**按页面算**，不再写死「两行」：
            #    删掉模板里夹在中间的空段，把「页面剩余高度」加到第一项的段前距，
            #    形成「标题贴上、四项靠下、制单位贴底」的上下对齐版式
            #    （业主 2026-11-23：评审表那页要上下对齐，不要都用在上方）。
            _first_head = next((p for p in _head_paras
                                if p.getparent() is not None), None)
            if _first_head is not None:
                # 标题前的空段也删掉（模板区间把目录后的空行一起带过来了，
                # 不删会把标题顶到页面中部 —— 只动复制进来的区间元素）
                _copy_ids = {id(x) for x in _copies}
                _prev = _title_p.getprevious()
                while (_prev is not None and id(_prev) in _copy_ids
                       and _prev.tag.split('}')[1] == 'p'
                       and not ''.join(t.text or '' for t in _prev.iter(qn('w:t'))).strip()):
                    _pv = _prev.getprevious()
                    _prev.getparent().remove(_prev)
                    _prev = _pv
                _blanks = []
                _nxt = _title_p.getnext()
                while _nxt is not None and _nxt is not _first_head:
                    if _nxt.tag.split('}')[1] == 'p' and not ''.join(
                            t.text or '' for t in _nxt.iter(qn('w:t'))).strip():
                        _blanks.append(_nxt)
                        _nxt = _nxt.getnext()
                        continue
                    break
                for _b in _blanks:
                    _b.getparent().remove(_b)
                # 制单位行：模板写法有「…主管部门制」「淮安市委政法委制」两种
                _seal_p = None
                for _el in _copies:
                    if _el.tag.split('}')[1] != 'p':
                        continue
                    _t = ''.join(t.text or '' for t in _el.iter(qn('w:t'))).strip()
                    if (len(_t) <= 30 and _t.endswith('制')
                            and any(k in _t for k in ('政法委', '主管部门', '职能'))):
                        _seal_p = _el
                        break
                _seal_blanks = []
                if _seal_p is not None:
                    _spv, _seal_blanks = _seal_p.getprevious(), []
                    while _spv is not None and _spv.tag.split('}')[1] == 'p' \
                            and not ''.join(t.text or '' for t in _spv.iter(qn('w:t'))).strip():
                        _seal_blanks.append(_spv)
                        _spv = _spv.getprevious()
                    for _b in _seal_blanks[2:]:
                        _b.getparent().remove(_b)
                    _seal_blanks = _seal_blanks[:2]

                def _ph(el, default_pt=12.0):
                    """段落高度估算（pt）：段前 + 段后 + 行高。"""
                    _sp = el.find(qn('w:pPr') + '/' + qn('w:spacing'))
                    _bef = _aft = 0.0
                    _line = 0.0
                    _rule = 'auto'
                    if _sp is not None:
                        def _f(k):
                            try:
                                return float(_sp.get(qn(k)) or 0)
                            except (TypeError, ValueError):
                                return 0.0
                        _bef, _aft = _f('w:before') / 20.0, _f('w:after') / 20.0
                        _line = _f('w:line')
                        _rule = _sp.get(qn('w:lineRule')) or 'auto'
                    _fs = default_pt
                    for _r in el.findall('.//' + qn('w:r')):
                        _pr = _r.find(qn('w:rPr'))
                        _sz = _pr.find(qn('w:sz')) if _pr is not None else None
                        if _sz is not None:
                            try:
                                _fs = max(_fs, float(_sz.get(qn('w:val')) or 0) / 2.0)
                            except (TypeError, ValueError):
                                pass
                    if _line:
                        _h = (_fs * 1.2 * (_line / 240.0)
                              if _rule == 'auto' else _line / 20.0)
                    else:
                        _h = _fs * 1.2
                    return _bef + _aft + max(_h, _fs)

                from docx.shared import Cm as _Cm
                _sec = doc.sections[-1]
                _content = float(_sec.page_height.pt - _sec.top_margin.pt
                                 - _sec.bottom_margin.pt)
                _used = (_ph(_title_p)
                         + sum(_ph(x) for x in _head_paras if x.getparent() is not None)
                         + sum(_ph(x) for x in _seal_blanks)
                         + (_ph(_seal_p) if _seal_p is not None else 0.0))
                _spacer = _content - _used - _Cm(1.5).pt
                if _spacer > 0:
                    _pPr = _first_head.find(qn('w:pPr'))
                    if _pPr is None:
                        from docx.oxml import OxmlElement as _OE9
                        _pPr = _OE9('w:pPr'); _first_head.insert(0, _pPr)
                    _sp = _pPr.find(qn('w:spacing'))
                    if _sp is None:
                        from docx.oxml import OxmlElement as _OE10
                        _sp = _OE10('w:spacing'); _pPr.append(_sp)
                    for _a in ('w:beforeLines', 'w:beforeAutospacing'):
                        if _sp.get(qn(_a)) is not None:
                            del _sp.attrib[qn(_a)]
                    _sp.set(qn('w:before'), str(int(_spacer * 20)))
                logger.info(f"[评审表] 表题→四项动态留白 {int(_spacer)}pt"
                            f"（正文区高 {int(_content)}pt，已用 {int(_used)}pt）")
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
