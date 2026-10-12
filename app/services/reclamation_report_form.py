# -*- coding: utf-8 -*-
"""第11部分「土地复垦方案报告表」—— 样本那种**制式表单**（数据驱动渲染）。

## 长什么样（样本《明远路》p57–p60 逐页核对）

一整张带边框的大表，**最左一列放竖排的分节名**（「项/目/概/况」逐字竖排），
右侧是「标签｜值｜标签｜值」的表单；「主要编制人员」是嵌在里面的子表。
四页分别是：

    ① 项目概况 + 方案编制单位（含主要编制人员子表）
    ② 复垦区土地利用现状 + 复垦责任范围内土地损毁及占用面积 + 复垦土地面积 + 复垦率
    ③ 工作计划及保障措施（一、复垦工作计划；二、实施保障措施）
    ④ 投资估算（测算依据 + 投资估算费用构成）

## 数据从哪来（全部是已有事实源，不编）

| 表单项 | 来源 |
|---|---|
| 项目名称/单位名称/项目位置/项目区面积 | `filled_data`（材料抽取） |
| 图幅号 | `filled_data` 里从勘测定界抽到的 `I50H118163` |
| 投资规模 | `project_investment`（万元） |
| 编制单位名称/法人代表/资质/联系人 | 公司档案（`_company`） |
| 主要编制人员 | 人员库（`personnel_for`） |
| 复垦区土地利用现状 / 复垦土地面积 | `filled_data["land_classification"]`（勘测定界地类，视觉读出） |
| 投资估算费用构成 | `reclamation_cost_model`（样本回验过） |
| 工作计划 / 保障措施 | 第8章、第10章正文（摘录，不另编） |

**抽不到的**一律留空、不印「待填写」这类占位符（业主硬约束）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _txt(v: Any) -> str:
    """取值转字符串；None/空 → 空串（**不印占位符**）。"""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("None", "nan", "—") else s


def _staff_rows(personnel: Sequence[dict], unit_name: str, limit: int = 8) -> List[List[str]]:
    """主要编制人员子表的行：姓名｜职称/职务｜专业｜单位｜签名（签名留给手签）。"""
    out: List[List[str]] = []
    for p in list(personnel or [])[:max(3, limit)]:
        name = _txt(p.get("姓名"))
        if not name:
            continue
        out.append([name, _txt(p.get("职称/职务")), _txt(p.get("专业")), unit_name, ""])
    while len(out) < 3:                      # 样本至少留 3 行（含空行给手填）
        out.append(["", "", "", "", ""])
    return out


def collect_form_data(filled: dict, company: dict, personnel: Sequence[dict]) -> Dict[str, Any]:
    """把各事实源收敛成报告表要用的字段（缺的留空，不猜）。"""
    fd = filled or {}
    co = company or {}
    _area_m2 = 0.0
    try:
        _area_m2 = float(str(fd.get("area_m2") or 0).replace(",", "")) or 0.0
    except Exception:
        _area_m2 = 0.0
    _ha = f"{_area_m2 / 10000.0:.4f}" if _area_m2 else ""

    # 图幅号：材料里有形如 I50H118163 的值
    _sheet = ""
    try:
        import json as _json
        m = re.search(r'\bI?\d{2}[A-Z]\d{6}\b', _json.dumps(fd, ensure_ascii=False))
        if m:
            _sheet = m.group(0)
    except Exception:
        pass

    _invest = _txt(fd.get("project_investment"))
    _invest_u = _txt(fd.get("project_investment_unit")) or "万元"

    lc = fd.get("land_classification") or {}
    return {
        "项目名称": _txt(fd.get("project_name")),
        "单位名称": _txt(fd.get("project_unit")) or _txt(fd.get("land_user_unit")),
        "单位地址": _txt(fd.get("unit_address")),
        "法人代表": "",                    # 材料的「法人代表」属于别的单位，宁缺勿错
        "联系电话": _txt(fd.get("contact_phone")),
        "单位性质": "",
        "项目性质": _txt(fd.get("construction_nature")),
        "项目位置": _txt(fd.get("location")),
        "项目区面积": f"{_ha} 公顷" if _ha else "",
        "投资规模": f"{_invest} {_invest_u}".strip() if _invest else "",
        "图幅号": _sheet,
        "生产年限": _txt(fd.get("construct_period")),
        "服务年限": (f"{_txt(fd['service_years'])} 个月"
                     if _txt(fd.get("service_years")) else ""),
        # 编制单位
        "编制单位名称": _txt(co.get("name")),
        "编制单位法人": _txt(co.get("leader_name")),
        "资质证书名称": _txt(co.get("qualification_text")),
        "资质等级": "",
        "发证机关": "",
        "证书编号": _txt(co.get("business_license_no")),
        "编制单位联系人": _txt(co.get("contact")),
        "编制单位电话": "",
        # 地类
        "地类行": lc.get("rows") or [],
        "地类合计_m2": float(lc.get("合计_m2") or 0),
        "area_m2": _area_m2,
        "单位名称_编制": _txt(co.get("name")),
        "人员行": _staff_rows(personnel, _txt(co.get("name"))),
        "永久建设用地_ha": _txt(fd.get("permanent_build_area")) or "",
    }


# ═══════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════

def _vertical_label(cell, text: str) -> None:
    """段名竖排：逐字一行（样本就是「项/目/概/况」这样排的）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for ch in text:
        r = p.add_run(ch + "\n")
        r.font.size = Pt(11)
        r.bold = False


def _fill_cell(cell, text: str, *, bold: bool = False, size: float = 10.5,
               align_center: bool = True) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    from docx.oxml.ns import qn
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if align_center else WD_ALIGN_PARAGRAPH.LEFT
    for r in list(p.runs):
        r.text = ""
    r = p.add_run(text or "")
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = "宋体"
    if r._element.rPr is not None and r._element.rPr.rFonts is not None:
        r._element.rPr.rFonts.set(qn('w:eastAsia'), "宋体")


def _section_table(doc, section: str, rows: List[List[Tuple[str, int]]],
                   widths_cm: Sequence[float], *, left_align_long: bool = True):
    """一个分节：最左列竖排段名（跨该节所有行），右侧是 `(文本, 跨列数)` 的表单行。

    列数固定 5：`[段名, …4 列表单…]`。跨列数不足 4 的行，最后一格自动撑满。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Cm
    n = max(1, len(rows))
    t = doc.add_table(rows=n, cols=5)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, w in enumerate(widths_cm):
        for r in t.rows:
            r.cells[i].width = Cm(w)
    # 段名列（合并后竖排）
    if n > 1:
        t.cell(0, 0).merge(t.cell(n - 1, 0))
    _vertical_label(t.cell(0, 0), section)
    for ri, row in enumerate(rows):
        ci = 1
        for text, span in row:
            span = max(1, min(int(span), 5 - ci))
            cell = t.cell(ri, ci)
            if span > 1:
                cell = cell.merge(t.cell(ri, ci + span - 1))
            _fill_cell(cell, text,
                       align_center=not (left_align_long and len(str(text)) > 40))
            ci += span
        if ci <= 4:                       # 尾格撑满，避免右边的空白格
            t.cell(ri, ci - 1).merge(t.cell(ri, 4))
    return t


def _kv(lab: str, val: str) -> List[Tuple[str, int]]:
    """一行完整的两格（标签跨1、值跨3）。"""
    return [(lab, 1), (val, 3)]


def _kv2(l1: str, v1: str, l2: str, v2: str) -> List[Tuple[str, int]]:
    """一行四个格（标签｜值｜标签｜值）。"""
    return [(l1, 1), (v1, 1), (l2, 1), (v2, 1)]


def _land_rows(data: Dict[str, Any], *, with_damage: bool) -> List[List[Tuple[str, int]]]:
    """复垦区土地利用现状 / 复垦土地面积 —— 按地类逐项（一级｜二级｜面积｜…）。"""
    out: List[List[Tuple[str, int]]] = [[("土地类型", 1), ("面积（公顷）", 3)]]
    out.append([("一级地类", 1), ("二级地类", 1),
                ("小计" if not with_damage else "已复垦", 1),
                ("已损毁" if not with_damage else "拟复垦", 1)])
    _total = 0.0
    for r in (data.get("地类行") or []):
        _ha = float(r.get("面积_m2") or 0) / 10000.0
        _total += _ha
        out.append([(_txt(r.get("一级地类")), 1), (_txt(r.get("二级地类")), 1),
                    (f"{_ha:.4f}", 1), ("—" if with_damage else f"{_ha:.4f}", 1)])
    out.append([("合计", 2), (f"{_total:.4f}", 1), (f"{_total:.4f}", 1)])
    return out


def render_report_form(doc, data: Dict[str, Any], chapters: Dict[str, Any] | None = None,
                       chapter_no: int = 11) -> None:
    """把第11部分「土地复垦方案报告表」渲染进文档。

    四节对应样本四页；节与节之间**不分页**（样本是连续的表，靠表格自身跨页）。
    """
    from docx.shared import Pt
    W = (0.9, 2.2, 4.3, 2.2, 6.0)      # 合计 15.6cm ≈ A4 版心

    # 表头
    _p = doc.add_paragraph()
    _p.alignment = 1                     # CENTER
    _r = _p.add_run("土地复垦方案报告表")
    _r.bold = True
    _r.font.size = Pt(16)
    from docx.oxml.ns import qn as _q
    if _r._element.rPr is not None and _r._element.rPr.rFonts is not None:
        _r._element.rPr.rFonts.set(_q('w:eastAsia'), "黑体")

    d = data
    # ① 项目概况 + 方案编制单位
    _section_table(doc, "项目概况", [
        _kv("项目名称", d["项目名称"]),
        _kv("单位名称", d["单位名称"]),
        _kv("单位地址", d["单位地址"]),
        _kv2("法人代表", d["法人代表"], "联系电话", d["联系电话"]),
        _kv2("单位性质", d["单位性质"], "项目性质", d["项目性质"]),
        _kv("项目位置", d["项目位置"]),
        _kv2("项目区面积", d["项目区面积"], "投资规模", d["投资规模"]),
        _kv("项目位置土地利用现状图编号", d["图幅号"]),
        _kv2("生产年限（或建设期限）", d["生产年限"],
             "土地复垦方案服务年限", d["服务年限"]),
    ], W)

    # 方案编制单位（含主要编制人员子表）
    _t = _section_table(doc, "方案编制单位", [
        _kv("编制单位名称", d["编制单位名称"]),
        _kv("法人代表", d["编制单位法人"]),
        _kv2("资质证书名称", d["资质证书名称"], "资质等级", d["资质等级"]),
        _kv2("发证机关", d["发证机关"], "编号", d["证书编号"]),
        _kv2("联系人", d["编制单位联系人"], "联系电话", d["编制单位电话"]),
        # 🔴 「主要编制人员」要**独占一行**再挂子表：早先挂在最后一行（联系人那行）的
        #    合并格里，等于把「联系人/联系电话」整行盖掉了（实测 2026-10-12）。
        [("主 要 编 制 人 员", 4)],
    ], W)
    try:
        _cell = _t.cell(len(_t.rows) - 1, 1)
        _cell = _cell.merge(_t.cell(len(_t.rows) - 1, 4))
        _fill_cell(_cell, "主 要 编 制 人 员", align_center=True)
        _rows = d.get("人员行") or []
        _inner = _cell.add_table(rows=1 + len(_rows), cols=5)
        _inner.style = 'Table Grid'
        for i, h in enumerate(("姓名", "职称/职务", "专业", "单位", "签名")):
            _fill_cell(_inner.cell(0, i), h, align_center=True)
        for ri, r in enumerate(_rows, 1):
            for ci, v in enumerate(r):
                _fill_cell(_inner.cell(ri, ci), v)
    except Exception as e:                       # pragma: no cover
        logger.warning(f"[报告表] 主要编制人员子表渲染失败: {e}")

    doc.add_page_break()

    # ② 复垦区土地利用现状 / 损毁及占用 / 复垦土地面积
    _section_table(doc, "复垦区土地利用现状", _land_rows(d, with_damage=False), W)
    _section_table(doc, "复垦责任范围内土地损毁及占用面积", [
        [("类型", 1), ("面积（公顷）", 3)],
        [("损毁", 1), ("压占", 1), ("小计", 1), (f"{d['area_m2'] / 10000.0:.4f}", 1)],
    ], W)
    _section_table(doc, "复垦土地面积", _land_rows(d, with_damage=True), W)

    doc.add_page_break()

    # ③ 工作计划及保障措施（摘录第8、10章正文，不另编）
    _section_table(doc, "工作计划及保障措施", [
        [("一、复垦工作计划", 4)],
        [(d.get("工作计划", ""), 4)],
        [("二、实施保障措施", 4)],
        [(d.get("保障措施", ""), 4)],
    ], W)

    doc.add_page_break()

    # ④ 投资估算
    _cost = d.get("费用构成") or []
    _rows: List[List[Tuple[str, int]]] = [[("投资估算测算依据", 4)],
                                          [(d.get("测算依据", ""), 4)]]
    _section_table(doc, "投资估算", _rows, W)
    if _cost:
        _t2 = doc.add_table(rows=1 + len(_cost), cols=3)
        _t2.style = 'Table Grid'
        for i, h in enumerate(("序号", "工程或费用名称", "费用（万元）")):
            _fill_cell(_t2.cell(0, i), h, align_center=True)
        for ri, (no, name, val) in enumerate(_cost, 1):
            _fill_cell(_t2.cell(ri, 0), str(no), align_center=True)
            _fill_cell(_t2.cell(ri, 1), str(name))
            _fill_cell(_t2.cell(ri, 2), str(val), align_center=True)


def build_cost_rows(estimate) -> List[Tuple[str, str, str]]:
    """`CostEstimate` → 报告表「投资估算费用构成」的行（样本表的就是这七项口径）。"""
    def _w(v):
        try:
            return f"{float(v):.2f}"
        except Exception:
            return ""
    return [
        ("一", "工程措施费", _w(estimate.construction)),
        ("二", "设备费", _w(estimate.equipment)),
        ("三", "其他费用", _w(estimate.other)),
        ("四", "监测与管护费", _w(estimate.monitor_care)),
        ("五", "预备费", _w(estimate.reserve)),
        ("六", "静态总投资", _w(estimate.static_total)),
        ("七", "动态总投资", _w(estimate.dynamic_total)),
    ]
