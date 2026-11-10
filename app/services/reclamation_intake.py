# -*- coding: utf-8 -*-
"""复垦基础资料 Excel —— 用户每次生成报告前上传的那一张表。

来源
----
结构不是凭空定的，是从业主在《1井神安井组复垦报告11.2.docx》里逐段写的
49 条批注反推出来的取数清单：

    「土地分类面积表中的数据」「excel表格提供」「让用户选择日期」
    「根据功能分区填写」「挖损弹窗询问用户具体信息」「与第七章投资一致」
    「如果 agent 分析不出来就是 15cm」……

每一张 sheet 都对应报告里真正要用的数据，不再让用户在对话里一项项回答。

对外
----
    build_intake_workbook(path)      生成空白模板
    read_intake_workbook(path)       读回成 {fields, zones, land_rows, ...}
    apply_intake_from_state(state)   从会话上传的 xlsx 里找到并写入 filled_data
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SHEET_HOME = "说明"
SHEET_BASE = "项目基本信息"
SHEET_SURVEY = "勘界信息"
SHEET_LAND = "土地分类面积"
SHEET_ZONES = "临时用地功能分区"
SHEET_DESIGN = "设计参数"
SHEET_BUDGET = "投资估算"
SHEET_PUBLIC = "公众参与"
SHEET_ATTACH = "附件清单"
SHEET_FORM = "报告表信息"

SHEETS = [SHEET_HOME, SHEET_BASE, SHEET_SURVEY, SHEET_LAND, SHEET_ZONES,
          SHEET_DESIGN, SHEET_BUDGET, SHEET_PUBLIC, SHEET_ATTACH, SHEET_FORM]

# ── 成对字段（字段｜值｜必填｜说明）────────────────────────────────
_BASE_FIELDS = [
    ("项目名称", "", "是", "与立项批复/公告一致"),
    ("建设单位", "", "是", "甲方单位全称"),
    ("项目性质", "", "是", "新建 / 改扩建 / 改建"),
    ("项目类型", "", "否", "如：建设项目（天然气储备）"),
    ("项目位置", "", "是", "写到区/街道/村，如：淮安市淮安区山阳街道黄土桥村"),
    ("所属地区", "", "是", "区县名，如：淮安区 / 金湖县；系统也会自动识别"),
    ("主体工程用地面积(公顷)", "", "是", "永久性建设用地面积"),
    ("项目总投资(万元)", "", "否", "主体工程总投资"),
    ("项目批复文号", "", "否", "立项/核准/用地批复文号"),
    ("用地期限(月)", "", "是", "临时用地批准使用月数"),
    ("用地开始", "", "是", "YYYY-MM"),
    ("用地结束", "", "是", "YYYY-MM"),
    ("复垦期(月)", "", "是", "施工结束后的复垦施工月数"),
    ("管护期(月)", "", "是", "复垦完成后的管护月数"),
    ("单位地址", "", "否", "报告表/扉页用"),
    ("法人代表", "", "否", "用地单位法人，报告表/扉页用"),
    ("单位性质", "", "否", "报告表用，如：有限责任公司"),
    ("联系人", "", "否", "报告表用"),
    ("联系电话", "", "否", "报告表用"),
    ("编制单位", "", "否", "本公司名称"),
    ("编制人1", "", "否", "报告表人员栏，最多 3 人"),
    ("编制人2", "", "否", ""),
    ("编制人3", "", "否", ""),
]

_SURVEY_FIELDS = [
    ("土地座落", "", "是", "勘测定界报告封面"),
    ("权属单位", "", "是", "村集体/国有"),
    ("界址点数", "", "否", "勘测定界报告"),
    ("图幅号", "", "否", ""),
    ("勘测单位", "", "否", ""),
    ("勘测日期", "", "否", "YYYY-MM-DD"),
    ("临时用地面积(公顷)", "", "是", "勘界报告中的临时用地面积"),
]

_DESIGN_FIELDS = [
    ("表土剥离厚度(cm)", "", "是", "可研/初设；没有就留空，系统会问"),
    ("硬化结构类型", "", "是", "碎石垫层+钢板 / 混凝土面层+垫层"),
    ("硬化面积(公顷)", "", "是", "井场、场站等硬化/压占面积"),
    ("面层厚度(cm)", "", "否", "混凝土面层或钢板厚度"),
    ("垫层厚度(cm)", "", "否", "碎石垫层厚度"),
    ("覆土厚度(cm)", "", "否", "复垦覆土厚度，默认同表土剥离厚度"),
    ("耕层厚度(cm)", "", "否", "复垦后耕作层厚度"),
    ("翻耕深度(cm)", "", "否", ""),
    ("培肥量(t/ha)", "", "否", "有机肥/复合肥施用量"),
    ("沟渠疏通长度(m)", "", "否", "涉及沟渠时填"),
    ("表土堆放区面积(公顷)", "", "否", "没有独立堆放区就填 0"),
    ("是否占用永久基本农田", "", "是", "是 / 否 / 不涉及"),
    ("是否占用生态保护红线", "", "是", "是 / 否 / 不涉及"),
    ("复垦方向", "", "是", "水田 / 旱地 / 林地 / 其他农用地"),
    ("土地复垦方案服务年限(月)", "", "否",
     "含临时用地期限+复垦期+管护期；不填则按用地开始日期推算"),
    # 🔴 土地适宜性评价因子（表4-9）—— 可研/勘测里有的直接填；
    #    不填时系统先从材料抽，仍没有才弹窗问（业主 2026-11-10）。
    ("自然坡度", "", "否", "如 <5° / 5-15° / 15-25° / >25°"),
    ("有效土层厚度(cm)", "", "否", "表4-9 适宜性评价用"),
    ("土壤质地", "", "否", "中壤土/轻壤土/粘土/砂壤土/砂土"),
    ("有机质含量(%)", "", "否", "如 0.8"),
    ("地貌部位", "", "否", "平地/岗坡地/坡地"),
    ("灌溉保证程度", "", "否", "充分满足/一般满足/无灌溉系统"),
    ("灌溉方式", "", "否", "喷灌/滴灌/地面灌溉/天然降水"),
    ("排水条件", "", "否", "健全/一般/无"),
    ("道路通达性", "", "否", "良好/一般/较差"),
    ("田块平整度", "", "否", "非常平坦/比较平坦/高低不平"),
]

_BUDGET_FIELDS = [
    ("工程施工费(万元)", "", "否", "表 7-3 合计"),
    ("设备购置费(万元)", "", "否", ""),
    ("其他费用(万元)", "", "否", "表 7-4 合计"),
    ("监测与管护费(万元)", "", "否", "含监测、管护、验收"),
    ("其中：复垦监测费(万元)", "", "否", ""),
    ("其中：管护费(万元)", "", "否", ""),
    ("其中：复垦验收费(万元)", "", "否", ""),
    ("基本预备费(万元)", "", "否", ""),
    ("价差预备费(万元)", "", "否", ""),
    ("风险金(万元)", "", "否", ""),
    ("静态总投资(万元)", "", "否", ""),
    ("动态总投资(万元)", "", "否", ""),
    ("投资估算Excel文件名", "", "否",
     "第七章管道费用表（.xls/.xlsx）；上传材料里同名文件，表7-3/7-4/7-5 以它为准"),
]

_FORM_FIELDS = [
    ("编制单位", "", "否", "报告表"),
    ("单位地址", "", "否", "报告表"),
    ("联系人", "", "否", "报告表"),
    ("联系电话", "", "否", "报告表"),
    ("项目负责人", "", "否", ""),
    ("技术负责人", "", "否", ""),
    ("填表人", "", "否", ""),
    ("送审时间", "", "否", "YYYY-MM-DD"),
]

_LAND_HEADERS = ["序号", "一级地类", "二级地类", "面积(公顷)", "占比(%)", "权属", "备注"]
_LAND_EXAMPLE = ["示例", "耕地", "水田", "2.2215", "95.56", "黄土桥村", "请删除本行"]

_ZONE_HEADERS = ["序号", "功能区名称", "面积(公顷)", "损毁方式", "损毁程度",
                 "原地类", "复垦方向", "备注"]
_ZONE_EXAMPLE = ["示例", "井场", "0.2310", "挖损和压占", "重度", "水田", "水田", "请删除本行"]

_PUBLIC_HEADERS = ["序号", "调查对象", "人数", "主要意见", "采纳情况", "备注"]
_PUBLIC_EXAMPLE = ["示例", "黄土桥村村民", "30", "希望复垦后恢复水田", "已采纳", "请删除本行"]

_ATTACH_NAMES = [
    "土地复垦方案委托函", "委托合同", "编制单位资质证书", "土地复垦承诺书",
    "临时用地协议书", "临时用地复垦协议", "环境影响报告批复", "不使用客土的情况说明",
    "公众参与调查表", "权利人意见", "公示", "现场照片、影像图",
    "临时用地功能分布图", "临时用地补偿到位说明", "不建设永久性建筑的承诺",
    "勘测报告", "主体项目用地红线图", "主体项目用地范围", "建设材料近期价格",
    "主体工程批复文件", "公司名称更改材料", "其他",
]
_ATTACH_HEADERS = ["序号", "附件名称", "是否有", "文件名", "备注"]


# ══════════════════════════════════════════════════════════════
# 生成
# ══════════════════════════════════════════════════════════════
def build_intake_workbook(path: str | Path) -> Path:
    """生成空白《复垦基础资料》Excel。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    head_fill = PatternFill("solid", fgColor="1F4E78")
    head_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    req_fill = PatternFill("solid", fgColor="FFF2CC")
    ex_fill = PatternFill("solid", fgColor="F2F2F2")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(vertical="center", wrap_text=True)

    def _head(ws, headers, widths):
        ws.append(headers)
        for i, _ in enumerate(headers, 1):
            c = ws.cell(row=1, column=i)
            c.fill = head_fill
            c.font = head_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = border
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 28

    # 说明
    ws = wb.active
    ws.title = SHEET_HOME
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 96
    lines = [
        ("复垦基础资料（每次生成报告前填一份）", ""),
        ("怎么用", "1）先填黄色必填项；资料里没有的可以留空，系统会弹窗问。"),
        ("", "2）《土地分类面积》与《临时用地功能分区》是两张核心表，合计必须等于临时用地面积。"),
        ("", "3）功能分区就是表 1-1、表 4-7、表 4-9、表 4-10 用的同一套单元，不要另造。"),
        ("", "4）设计参数优先从可研/初设摘，摘不到再留空；不要套别的项目。"),
        ("", "5）投资估算留空时，系统按工程量模型和江苏省定额自动算。"),
        ("", "6）所属地区填区县名（如淮安区、金湖县），系统也会从资料里自动识别。"),
        ("面积口径", "临时用地面积 = 各地类面积之和 = 各功能分区面积之和。"),
        ("单位", "面积：公顷；长度：厘米；投资：万元；日期：YYYY-MM-DD 或 YYYY-MM。"),
        ("来源", "本表结构来自《1井神安井组复垦报告11.2》的 49 条批注。"),
    ]
    for i, (a, b) in enumerate(lines, 1):
        ws.cell(row=i, column=1, value=a).font = Font(name="微软雅黑", size=11, bold=(i == 1))
        ws.cell(row=i, column=2, value=b).alignment = Alignment(wrap_text=True, vertical="top")
        if i == 1:
            ws.cell(row=i, column=1).font = Font(name="微软雅黑", size=14, bold=True, color="1F4E78")
        ws.row_dimensions[i].height = 26 if i > 1 else 32

    # 成对字段表
    def _pairs_sheet(name, fields):
        s = wb.create_sheet(name)
        _head(s, ["字段", "值", "必填", "说明 / 来源"], [30, 34, 8, 62])
        for row in fields:
            s.append(list(row))
            r = s.max_row
            for col in range(1, 5):
                c = s.cell(row=r, column=col)
                c.border = border
                c.alignment = wrap
            if row[2] == "是":
                s.cell(row=r, column=2).fill = req_fill
        return s

    _pairs_sheet(SHEET_BASE, _BASE_FIELDS)
    _pairs_sheet(SHEET_SURVEY, _SURVEY_FIELDS)
    _pairs_sheet(SHEET_DESIGN, _DESIGN_FIELDS)
    _pairs_sheet(SHEET_BUDGET, _BUDGET_FIELDS)
    _pairs_sheet(SHEET_FORM, _FORM_FIELDS)

    # 土地分类面积
    s = wb.create_sheet(SHEET_LAND)
    _head(s, _LAND_HEADERS, [8, 18, 18, 14, 12, 16, 30])
    s.append(_LAND_EXAMPLE)
    for r in range(2, 22):
        for c in range(1, 8):
            s.cell(row=r, column=c).border = border
    for c in range(1, 8):
        cell = s.cell(row=2, column=c)
        cell.fill = ex_fill
        cell.font = Font(name="微软雅黑", size=9, italic=True, color="808080")

    # 功能分区
    s = wb.create_sheet(SHEET_ZONES)
    _head(s, _ZONE_HEADERS, [8, 18, 14, 14, 12, 16, 14, 26])
    s.append(_ZONE_EXAMPLE)
    for r in range(2, 22):
        for c in range(1, 9):
            s.cell(row=r, column=c).border = border
    for c in range(1, 9):
        cell = s.cell(row=2, column=c)
        cell.fill = ex_fill
        cell.font = Font(name="微软雅黑", size=9, italic=True, color="808080")
    dv1 = DataValidation(type="list", formula1='"挖损,压占,挖损和压占"', allow_blank=True)
    dv2 = DataValidation(type="list", formula1='"轻度,中度,重度"', allow_blank=True)
    s.add_data_validation(dv1)
    s.add_data_validation(dv2)
    dv1.add("D2:D200")
    dv2.add("E2:E200")

    # 公众参与
    s = wb.create_sheet(SHEET_PUBLIC)
    _head(s, _PUBLIC_HEADERS, [8, 22, 10, 34, 20, 24])
    s.append(_PUBLIC_EXAMPLE)
    for r in range(2, 22):
        for c in range(1, 7):
            s.cell(row=r, column=c).border = border

    # 附件清单
    s = wb.create_sheet(SHEET_ATTACH)
    _head(s, _ATTACH_HEADERS, [8, 34, 10, 38, 26])
    for i, name in enumerate(_ATTACH_NAMES, 1):
        s.append([i, name, "", "", ""])
    dv3 = DataValidation(type="list", formula1='"有,无,待补充"', allow_blank=True)
    s.add_data_validation(dv3)
    dv3.add("C2:C40")
    for r in range(2, s.max_row + 1):
        for c in range(1, 6):
            s.cell(row=r, column=c).border = border

    wb.save(str(path))
    return path


# ══════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════
def _norm_label(s: Any) -> str:
    return (str(s or "").strip()
            .replace("（", "(").replace("）", ")")
            .replace("：", "").replace(" ", ""))


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _pairs(ws) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for r in range(2, (ws.max_row or 0) + 1):
        k = _norm_label(ws.cell(row=r, column=1).value)
        v = ws.cell(row=r, column=2).value
        if k and v not in (None, ""):
            out[k] = v
    return out


def _rows(ws, headers: List[str]) -> List[Dict[str, Any]]:
    out = []
    for r in range(2, (ws.max_row or 0) + 1):
        vals = [ws.cell(row=r, column=i + 1).value for i in range(len(headers))]
        if all(v in (None, "") for v in vals):
            continue
        if str(vals[0] or "").strip() == "示例":
            continue
        out.append(dict(zip(headers, vals)))
    return out


_PAIR_MAP = {
    "项目名称": "project_name",
    "建设单位": "build_unit",
    "项目性质": "construction_nature",
    "项目类型": "project_type",
    "项目位置": "location",
    "所属地区": "region",
    "主体工程用地面积(公顷)": "permanent_area_ha",
    "项目总投资(万元)": "project_investment",
    "项目批复文号": "doc_reference",
    "用地期限(月)": "land_use_period_months",
    "用地开始": "land_use_start",
    "用地结束": "land_use_end",
    "复垦期(月)": "reclaim_period_months",
    "管护期(月)": "care_period_months",
    "单位地址": "unit_address",
    "法人代表": "legal_rep",
    "单位性质": "unit_nature",
    "联系人": "contact_name",
    "联系电话": "contact_phone",
    "编制单位": "implement_unit",
    "编制人1": "compiler_1",
    "编制人2": "compiler_2",
    "编制人3": "compiler_3",
    "土地座落": "land_location",
    "权属单位": "land_ownership",
    "界址点数": "boundary_point_count",
    "图幅号": "map_sheet_no",
    "勘测单位": "survey_unit",
    "勘测日期": "survey_date",
    "临时用地面积(公顷)": "temp_area_ha",
    "表土剥离厚度(cm)": "topsoil_thickness",
    "硬化结构类型": "hard_surface_type",
    "硬化面积(公顷)": "hard_area_ha",
    "面层厚度(cm)": "surface_thickness_cm",
    "垫层厚度(cm)": "cushion_thickness_cm",
    "覆土厚度(cm)": "cover_thickness_cm",
    "耕层厚度(cm)": "topsoil_depth_cm",
    "翻耕深度(cm)": "tillage_depth_cm",
    "培肥量(t/ha)": "fertilizer_t_ha",
    "沟渠疏通长度(m)": "ditch_length_m",
    "表土堆放区面积(公顷)": "stockpile_area_ha",
    "是否占用永久基本农田": "occupies_basic_farmland",
    "是否占用生态保护红线": "occupies_eco_redline",
    "复垦方向": "reclaim_direction",
    "土地复垦方案服务年限(月)": "service_years",
    "自然坡度": "slope_grade",
    "有效土层厚度(cm)": "effective_soil_depth_cm",
    "土壤质地": "soil_texture",
    "有机质含量(%)": "organic_matter",
    "地貌部位": "landform",
    "灌溉保证程度": "irrigation_guarantee",
    "灌溉方式": "irrigation_method",
    "排水条件": "drainage_condition",
    "道路通达性": "road_access",
    "田块平整度": "field_flatness",
    "项目负责人": "project_leader",
    "技术负责人": "tech_leader",
    "填表人": "form_filler",
    "送审时间": "submit_date",
}

_BUDGET_MAP = {
    "工程施工费(万元)": "construction",
    "设备购置费(万元)": "equipment",
    "其他费用(万元)": "other",
    "监测与管护费(万元)": "monitor_care",
    "其中：复垦监测费(万元)": "monitor",
    "其中：管护费(万元)": "care",
    "其中：复垦验收费(万元)": "acceptance",
    "基本预备费(万元)": "basic_reserve",
    "价差预备费(万元)": "price_reserve",
    "风险金(万元)": "risk",
    "静态总投资(万元)": "static_total",
    "动态总投资(万元)": "dynamic_total",
}


def read_intake_workbook(path: str | Path) -> Optional[Dict[str, Any]]:
    """读取《复垦基础资料》Excel；不是这张表就返回 None。"""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(str(path), data_only=True, read_only=True)
    except Exception as e:
        logger.info(f"[基础资料] 打开失败 {path}: {e}")
        return None

    names = set(wb.sheetnames)
    # 识别信号：至少同时有「项目基本信息」和「临时用地功能分区」，或「说明」里有标题
    if not ({SHEET_BASE, SHEET_ZONES} & names):
        return None
    if SHEET_HOME in names:
        try:
            title = str(wb[SHEET_HOME].cell(row=1, column=1).value or "")
            if "复垦基础资料" not in title and SHEET_ZONES not in names:
                return None
        except Exception:
            pass

    fields: Dict[str, Any] = {}
    for sheet in (SHEET_BASE, SHEET_SURVEY, SHEET_DESIGN, SHEET_FORM):
        if sheet in names:
            for k, v in _pairs(wb[sheet]).items():
                key = _PAIR_MAP.get(k)
                if key:
                    fields[key] = v

    land_rows: List[Dict[str, Any]] = []
    if SHEET_LAND in names:
        for row in _rows(wb[SHEET_LAND], _LAND_HEADERS):
            ha = _num(row.get("面积(公顷)"))
            if ha <= 0:
                continue
            land_rows.append({
                "一级地类": str(row.get("一级地类") or "").strip(),
                "二级地类": str(row.get("二级地类") or "").strip(),
                "面积_m2": round(ha * 10000.0, 2),
                "占比": str(row.get("占比(%)") or "").strip(),
                "权属": str(row.get("权属") or "").strip(),
            })

    _zone_rows: List[Dict[str, Any]] = []
    if SHEET_ZONES in names:
        for row in _rows(wb[SHEET_ZONES], _ZONE_HEADERS):
            ha = _num(row.get("面积(公顷)"))
            name = str(row.get("功能区名称") or "").strip()
            if not name or ha <= 0:
                continue
            _note = str(row.get("备注") or "")
            _pm = re.search(r'(?:地块数|地块个数)\s*[:：=]?\s*(\d+)', _note)
            _zone_rows.append({
                "name": name,
                "area_m2": round(ha * 10000.0, 4),
                "method": str(row.get("损毁方式") or "").strip(),
                "degree": str(row.get("损毁程度") or "").strip(),
                "land_types": str(row.get("原地类") or "").strip(),
                "reclaim_direction": str(row.get("复垦方向") or "").strip(),
                # 「地块数=2」写在备注列即可；没有就留空（不编 1 个）
                "plot_count": int(_pm.group(1)) if _pm else None,
            })

    # 🔴 同名分区写**多行** = 该分区下的逐个地块（模板 表4-2 的面积列：
    #    上「分区小计」下「逐个地块面积」）。读回时合并成一条分区记录，
    #    地块面积留在 `plot_areas_m2` 里，地块个数默认取行数。
    zones: List[Dict[str, Any]] = []
    _by_name: Dict[str, Dict[str, Any]] = {}
    for z in _zone_rows:
        name = z["name"]
        cur = _by_name.get(name)
        if cur is None:
            cur = dict(z)
            cur["plot_areas_m2"] = [z["area_m2"]]
            cur["_rows"] = 1
            _by_name[name] = cur
            zones.append(cur)
            continue
        cur["plot_areas_m2"].append(z["area_m2"])
        cur["area_m2"] = round(cur["area_m2"] + z["area_m2"], 4)
        cur["_rows"] += 1
        for k in ("method", "degree", "land_types", "reclaim_direction"):
            if not cur.get(k):
                cur[k] = z.get(k) or ""
        if z.get("plot_count") not in (None, ""):
            cur["plot_count"] = z["plot_count"]
    for z in zones:
        _rows_n = z.pop("_rows")
        if z.get("plot_count") in (None, "") and _rows_n > 1:
            z["plot_count"] = _rows_n
        if len(z.get("plot_areas_m2") or []) <= 1:
            z["plot_areas_m2"] = []

    budget = {}
    cost_excel_name = ""
    if SHEET_BUDGET in names:
        for k, v in _pairs(wb[SHEET_BUDGET]).items():
            if k == "投资估算Excel文件名":
                cost_excel_name = str(v or "").strip()
                continue
            key = _BUDGET_MAP.get(k)
            if key:
                budget[key] = _num(v)

    public = []
    if SHEET_PUBLIC in names:
        public = _rows(wb[SHEET_PUBLIC], _PUBLIC_HEADERS)

    attachments = []
    if SHEET_ATTACH in names:
        for row in _rows(wb[SHEET_ATTACH], _ATTACH_HEADERS):
            attachments.append({
                "name": str(row.get("附件名称") or "").strip(),
                "has": str(row.get("是否有") or "").strip(),
                "filename": str(row.get("文件名") or "").strip(),
            })

    # 面积/年限派生
    if fields.get("permanent_area_ha"):
        fields["permanent_area_m2"] = round(_num(fields.pop("permanent_area_ha")) * 10000, 2)
    if fields.get("temp_area_ha"):
        fields["area_m2"] = round(_num(fields.pop("temp_area_ha")) * 10000, 2)
    if not fields.get("area_m2") and zones:
        fields["area_m2"] = round(sum(z["area_m2"] for z in zones), 4)
    if fields.get("hard_area_ha"):
        fields["hard_area_m2"] = round(_num(fields.pop("hard_area_ha")) * 10000, 2)
    if fields.get("stockpile_area_ha"):
        fields["stockpile_area_m2"] = round(_num(fields.pop("stockpile_area_ha")) * 10000, 2)

    months = sum(_num(fields.get(k)) for k in
                 ("land_use_period_months", "reclaim_period_months", "care_period_months"))
    # 🔴 服务年限**用户提供优先**（2026-11-05 业主：后期在基础资料里加这一条）；
    #    没填才按「用地期限+复垦期+管护期」推。
    if months and not str(fields.get("service_years") or "").strip():
        fields["service_years"] = round(months / 12.0, 1)
        fields["service_months"] = int(round(months))

    if fields.get("permanent_area_m2") and fields.get("area_m2"):
        fields["project_area_m2"] = round(
            _num(fields["permanent_area_m2"]) + _num(fields["area_m2"]), 2)

    if land_rows:
        fields["land_classification"] = {
            "rows": land_rows,
            "合计_m2": round(sum(r["面积_m2"] for r in land_rows), 2),
        }

    if public:
        fields["_public_participation"] = public
    if attachments:
        fields["_intake_attachments"] = attachments

    return {
        "fields": fields,
        "zones": zones,
        "land_rows": land_rows,
        "budget": budget,
        "public": public,
        "attachments": attachments,
        "cost_excel_name": cost_excel_name,
    }


def _put(filled: Dict[str, Any], key: str, value: Any) -> bool:
    if value in (None, ""):
        return False
    if str(filled.get("_locked_" + key) or "").lower() in ("1", "true", "yes"):
        return False
    filled[key] = value
    return True


def apply_intake(filled: Dict[str, Any], intake: Dict[str, Any]) -> List[str]:
    """把 Excel 内容写进事实卡；返回本次写入的键。"""
    if not isinstance(filled, dict) or not isinstance(intake, dict):
        return []
    changed: List[str] = []
    for k, v in (intake.get("fields") or {}).items():
        if k.startswith("_"):
            continue
        if _put(filled, k, v):
            changed.append(k)

    zones = intake.get("zones") or []
    if zones:
        filled["_land_use_zones"] = zones
        filled["land_use_zones"] = "；".join(
            f"{z['name']} {z['area_m2'] / 10000:.4f}公顷 {z['method']} "
            f"{z['degree']} {z['land_types']}".strip()
            for z in zones)
        changed.append("land_use_zones")

    budget = {k: v for k, v in (intake.get("budget") or {}).items() if v}
    if budget:
        filled["_budget_estimate"] = budget
        changed.append("_budget_estimate")

    if intake.get("attachments"):
        filled["_intake_attachments"] = intake["attachments"]
        changed.append("_intake_attachments")

    if intake.get("cost_excel_name"):
        filled["cost_estimate_excel_name"] = intake["cost_excel_name"]
        changed.append("cost_estimate_excel_name")

    return changed


def _resolve(p: Any) -> Optional[Path]:
    from app.config import settings
    s = str(p or "")
    if not s:
        return None
    cands = [Path(s), Path(settings.STORAGE_DIR) / s,
             Path(settings.STORAGE_DIR) / "images" / Path(s).name]
    for c in cands:
        if c.exists() and c.is_file():
            return c
    return None


def apply_intake_from_state(state: Dict[str, Any]) -> int:
    """从会话已上传文件里找《复垦基础资料》xlsx，读入 filled_data。返回写入字段数。"""
    from app.agent.chapter_catalog import domain_of_state
    if domain_of_state(state) != "reclamation":
        return 0

    paths: List[Path] = []
    seen = set()
    for item in (state.get("_uploaded_files") or []) + (state.get("_project_materials") or []):
        p = item.get("path") if isinstance(item, dict) else item
        if isinstance(item, dict) and not p:
            p = item.get("source_path")
        if not str(p or "").lower().endswith((".xlsx", ".xlsm")):
            continue
        rp = _resolve(p)
        if rp and str(rp) not in seen:
            seen.add(str(rp))
            paths.append(rp)

    filled = state.setdefault("filled_data", {})
    changed = 0
    for p in paths:
        data = read_intake_workbook(p)
        if data:
            _n = apply_intake(filled, data)
            logger.info(f"[基础资料] 读取《{p.name}》：写入 {_n} 项")
            changed += _n
            continue
        # 🔴 不是《复垦基础资料》模板 → 当**可研/项目资料 Excel** 扫标签取数
        #    （业主 2026-11-10：「是可研 excel 然后最后才是弹窗」）。
        try:
            from app.services.reclamation_fs_excel import scan_workbook
            _got = scan_workbook(p, filled)
            if _got:
                logger.info(f"[可研Excel] 《{p.name}》：写入 {len(_got)} 项")
                changed += len(_got)
        except Exception as _fe:
            logger.warning(f"[可研Excel] {p.name} 扫描跳过: {_fe}")
    return changed
