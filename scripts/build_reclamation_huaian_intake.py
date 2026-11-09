# -*- coding: utf-8 -*-
"""第四份测试数据：淮安区博里镇农民集中居住区（博里花园）建设工程临时用地。

与前三份的区别：
- 淮安区**安置房项目**（块状用地，多地块），模板会自动选淮安区上传版；
- 设计参数**不在 Excel 里**，全部写在施工组织设计摘录里（模拟用户真实上传）；
- 配套一份《投资估算 Excel》（Sheet3 工程表 / Sheet2 汇总），第七、八章以它为准。

产物：
    storage/templates/复垦基础资料_博里花园_测试.xlsx
    storage/templates/投资估算_博里花园.xlsx
    storage/templates/博里花园_材料.txt
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SRC = BACKEND / "storage" / "templates" / "复垦基础资料_模板.xlsx"
DST = BACKEND / "storage" / "templates" / "复垦基础资料_博里花园_测试.xlsx"
COST = BACKEND / "storage" / "templates" / "投资估算_博里花园.xlsx"
MAT = BACKEND / "storage" / "templates" / "博里花园_材料.txt"

PROJECT = "淮安市淮安区博里镇农民集中居住区（博里花园）建设工程临时用地"

BASE = {
    "项目名称": PROJECT,
    "建设单位": "淮安市博里城镇建设投资有限公司",
    "项目性质": "新建",
    "项目类型": "建设项目（安置房）",
    "项目位置": "淮安市淮安区博里镇博里村、南涧村",
    "所属地区": "淮安区",
    "主体工程用地面积(公顷)": 7.8600,
    "项目总投资(万元)": 38600,
    "项目批复文号": "淮发改审〔2026〕87号",
    "用地期限(月)": 24,
    "用地开始": "2027-03",
    "用地结束": "2029-02",
    "复垦期(月)": 6,
    "管护期(月)": 12,
    "单位地址": "淮安市淮安区博里镇博里村府前路18号",
    "法人代表": "王建国",
    "单位性质": "有限责任公司（国有独资）",
    "联系人": "邵建国",
    "联系电话": "0517-85391266",
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "编制人1": "陈春",
    "编制人2": "林叶青",
    "编制人3": "刘利伟",
}

SURVEY = {
    "土地座落": "淮安市淮安区博里镇博里村、南涧村",
    "权属单位": "博里镇博里村村民委员会、南涧村村民委员会",
    "界址点数": 8,
    "勘测单位": "江苏众拓测绘有限公司",
    "勘测日期": "2026-12-08",
    "临时用地面积(公顷)": 3.2600,
}

# 设计参数：Excel 填一份（正式渠道）；材料里同时给文字描述，便于抽不上来时有兜底
DESIGN = {
    "表土剥离厚度(cm)": 30,
    "硬化结构类型": "碎石垫层+钢板",
    "硬化面积(公顷)": 1.6000,
    "面层厚度(cm)": 1.2,
    "垫层厚度(cm)": 15,
    "覆土厚度(cm)": 30,
    "耕层厚度(cm)": 20,
    "翻耕深度(cm)": 30,
    "培肥量(t/ha)": 15,
    "沟渠疏通长度(m)": 480,
    "表土堆放区面积(公顷)": 0.3300,
    "是否占用永久基本农田": "否",
    "是否占用生态保护红线": "否",
    "复垦方向": "水田",
    "土地复垦方案服务年限(月)": 42,
}

LAND = [
    ("耕地", "水田", 1.4200, "43.56", "博里村"),
    ("耕地", "旱地", 0.8600, "26.38", "博里村、南涧村"),
    ("园地", "果园", 0.3100, "9.51", "南涧村"),
    ("水域及水利设施用地", "坑塘水面", 0.2400, "7.36", "南涧村"),
    ("水域及水利设施用地", "沟渠", 0.2700, "8.28", "博里村、南涧村"),
    ("交通运输用地", "农村道路", 0.1600, "4.91", "博里村"),
]

# 同名分区多行 = 该分区下的各个地块（表4-2「分区小计 | 逐个地块」）
ZONE_PLOTS = {
    "施工场站": [0.5200, 0.6600],
    "施工营地": [0.5500],
    "材料堆场": [0.4200],
    "施工便道": [0.4100, 0.3700],
    "表土堆放区": [0.1600, 0.1700],
}

ZONES = [
    ("施工场站", 1.1800, "压占", "重度", "水田、旱地", "水田"),
    ("施工营地", 0.5500, "压占", "重度", "旱地、果园", "旱地"),
    ("材料堆场", 0.4200, "压占", "重度", "旱地、农村道路", "旱地"),
    ("施工便道", 0.7800, "压占", "重度", "水田、旱地、农村道路", "水田"),
    ("表土堆放区", 0.3300, "压占", "中度", "水田、坑塘水面", "水田"),
]

PUBLIC = [
    ("博里村村民", 32, "希望复垦后恢复水田耕作，不降低耕层肥力", "已采纳"),
    ("南涧村村民", 18, "关注施工便道占用农村道路的恢复标准", "已采纳"),
    ("村民代表", 8, "要求表土剥离单独堆存、复垦时全部回覆", "已采纳"),
]

FORM = {
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "单位地址": "淮安市淮安区博里镇博里村府前路18号",
    "联系人": "邵建国",
    "联系电话": "0517-85391266",
    "项目负责人": "陈春",
    "技术负责人": "林叶青",
    "填表人": "刘利伟",
    "送审时间": "2027-02-20",
}

MATERIAL = """淮安市淮安区博里镇农民集中居住区（博里花园）建设工程临时用地土地复垦方案
（施工组织设计摘录）

一、项目概况
博里花园农民集中居住区建设工程位于淮安市淮安区博里镇博里村、南涧村，
主体工程永久占地 7.8600 公顷，项目总投资 38600 万元，立项批复为淮发改审〔2026〕87号。
为解决施工期间的办公、住宿、材料堆放和运输需要，设置施工场站、施工营地、
材料堆场、施工便道和表土堆放区五类临时用地，临时用地面积 3.2600 公顷，
共 8 个地块，临时用地期限 24 个月，自 2027 年 3 月至 2029 年 2 月。

二、表土剥离与堆存
临时用地范围内现状为水田、旱地、果园、坑塘水面、沟渠和农村道路。施工前对
水田、旱地和果园实施表土剥离，剥离厚度 30cm；剥离表土集中堆存于表土堆放区，
堆放区面积 0.3300 公顷，堆高不超过 3m，四周设袋装土围堰并覆盖防尘网。
表土堆放区在堆土前同样先行剥离表土 30cm，与其余表土一并堆存。

三、场地硬化与拆除
施工场站和材料堆场采用硬化处理，硬化面积 1.6000 公顷，结构为 15cm 厚碎石垫层，
其上铺设 1.2cm 厚钢板，钢板由施工单位回收周转使用。复垦时清除碎石垫层，
垫层及建筑垃圾外运至博里镇指定弃土场，运距 8km。

四、复垦工艺
1. 场地清理：清除碎石垫层、建筑垃圾和临时设施基础；
2. 表土回覆：将堆存的表土全部回覆，覆土厚度 30cm；
3. 土地翻耕：翻耕深度 30cm，耕作层厚度不低于 20cm；
4. 土壤培肥：按有机肥 15t/ha、复合肥 0.75t/ha 施用；
5. 灌排恢复：疏通沟渠 480m，恢复原有灌排体系；
6. 道路恢复：修复施工便道占用的农村道路 180m，路面结构与原状一致。

五、复垦工期
土地复垦方案服务年限为 42 个月，其中临时用地期限 24 个月、复垦施工期 6 个月、
管护期 12 个月。复垦工作在主体工程完工后 1 个月内启动，6 个月内完成，
完成后移交博里村、南涧村管护，管护期 12 个月，管护期满申请验收。

六、公众参与
方案编制期间走访博里村、南涧村村民 50 人、村民代表 8 人，发放调查表 58 份，
回收有效调查表 58 份，村民均支持复垦，主要意见为恢复水田耕作、保证耕层肥力、
恢复农村道路通行，均已采纳。

七、界址点坐标表（2000 国家大地坐标系，X 为北坐标、Y 为东坐标）
序号 X坐标 Y坐标
1 3702000.000 40418800.000
2 3702100.000 40418800.000
3 3702200.000 40418800.000
4 3702200.000 40418600.000
5 3702100.000 40418600.000
6 3702100.000 40418674.000
7 3702050.000 40418674.000
8 3702000.000 40418674.000
"""

# ── 投资估算 Excel ──────────────────────────────────────────────
# 单价沿用江苏省土地复垦定额样本单价；数量按 3.26 公顷规模重算。
ENG_ROWS = [
    # (序号, 名称, 单位, 数量, 单价)
    ("", "土壤重构工程", None, None, None),
    ("（一）", "土壤剥离工程", None, None, None),
    ("1", "表土剥离", "100m³", 97.80, 1593.03),
    ("2", "播撒草籽", "公顷", 0.24, 508.21),
    ("3", "袋装土加固编织袋土", "100m³", 2.80, 5815.36),
    ("（二）", "清理工程", None, None, None),
    ("2", "垫层清理", "100m³", 24.00, 7425.64),
    ("3", "垃圾外运工程", "100m³", 24.00, 6981.16),
    ("（三）", "平整工程", None, None, None),
    ("1", "土地翻耕", "公顷", 3.26, 2902.21),
    ("2", "表土回填", "100m³", 97.80, 1954.40),
    ("3", "田面平整", "100m³", 32.60, 273.20),
    ("（四）", "生物化学工程", None, None, None),
    ("1", "土壤培肥（有机肥）", "吨", 48.90, 2500.00),
    ("2", "土壤培肥（复合肥）", "吨", 2.445, 3000.00),
    ("二", "道路工程", None, None, None),
    ("", "农村道路修复", "m", 180, 312.00),
    ("三", "配套工程", None, None, None),
    ("（一）", "恢复水利工程", None, None, None),
    ("1", "沟渠疏通", "㎡", 2700, 45.00),
]


def _amount(qty, price):
    return round(float(qty) * float(price) / 10000.0, 4)


def _build_cost() -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wb = Workbook()

    # Sheet3 工程施工费估算表
    ws = wb.active
    ws.title = "Sheet3"
    ws.append(["工程施工费：", "序号", "工程或费用名称", "单位", "数量",
               "综合单价（元）", "工程施工费（万元）"])
    eng_total = 0.0
    cur_section_start = None  # 段小计行
    # A 列是表名，序号从 B 列起（与解析器/人工资表口径一致）
    for no, name, unit, qty, price in ENG_ROWS:
        if unit is None and qty is None and price is None:
            ws.append(["", no, name])
            cur_section_start = ws.max_row
            continue
        amt = _amount(qty, price)
        eng_total += amt
        ws.append(["", no, name, unit, qty, price, amt])
    ws.append(["", "合计", "", "", "", "", round(eng_total, 4)])
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for c in row:
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=True)
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 26
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 18
    for c in ws[1]:
        c.font = Font(bold=True)

    # 其余费用按江苏省定额费率
    pre = round(eng_total * 0.068, 4)      # 前期工作费 6.8%
    supervise = round(eng_total * 0.02, 4)  # 工程监理费 2%
    finish = round(eng_total * 0.042, 4)   # 竣工验收费 4.2%
    owner = round((eng_total + pre + supervise + finish) * 0.028, 4)  # 业主管理费 2.8%
    other = round(pre + supervise + finish + owner, 4)

    monitor = 6.00      # 土地污染监测 2.4 + 耕地质量评定 3.6
    care = 2.40
    acceptance = 5.50
    monitor_care = round(monitor + care + acceptance, 4)  # 13.90
    basic = round((eng_total + other + monitor_care) * 0.06, 4)  # 6%
    escalation = 7.20
    risk = 2.00
    reserve = round(basic + escalation + risk, 4)
    static = round(eng_total + other + monitor_care + basic + risk, 4)
    dynamic = round(static + escalation, 4)

    # Sheet2 汇总
    ws2 = wb.create_sheet("Sheet2")
    rows = [
        ["一", "工程施工费：", round(eng_total, 4)],
        ["二", "设备购置费：", 0],
        ["三", "其他费用（万元）："],
        ["其他费用（万元）", other],
        ["（一）", "前期工作费", pre],
        ["1", "土地清查费", "工程施工费×0.20%", round(eng_total * 0.002, 4)],
        ["2", "项目可行性研究费", "工程施工费×1.00%", round(eng_total * 0.01, 4)],
        ["3", "项目勘测费", "工程施工费×0.5%", round(eng_total * 0.005, 4)],
        ["4", "项目规划设计与预算编制费", "工程施工费×2.40%", round(eng_total * 0.024, 4)],
        ["5", "施工图设计费", "工程施工费×1.20%", round(eng_total * 0.012, 4)],
        ["6", "项目立项和规划设计及预算审查费", "工程施工费×0.7%", round(eng_total * 0.007, 4)],
        ["7", "项目招标费", "工程施工费×0.80%", round(eng_total * 0.008, 4)],
        ["（二）", "工程监理费", "工程施工费×2.00%", supervise],
        ["（三）", "拆迁补偿费", 0],
        ["（四）", "竣工验收费", finish],
        ["1", "项目竣工测量与工程复核费", "工程施工费×1.00%", round(eng_total * 0.01, 4)],
        ["2", "工程验收费", "工程施工费×1.20%", round(eng_total * 0.012, 4)],
        ["3", "项目决算编制与审计费", "工程施工费×1.20%", round(eng_total * 0.012, 4)],
        ["4", "土地重估与登记费", "工程施工费×0.60%", round(eng_total * 0.006, 4)],
        ["5", "标牌与标识设定费", "工程施工费×0.20%", round(eng_total * 0.002, 4)],
        ["（五）", "业主管理费", "（工程+前期+监理+拆迁+竣工）×2.80%", owner],
        ["四", "监测与管护费", monitor_care],
        ["（一）", "复垦监测费", monitor],
        ["（二）", "管护费", care],
        ["土地污染监测", 2.40, "估计值"],
        ["耕地质量评定", 3.60, "估计值"],
        ["复垦验收报告编制", acceptance, "估计值"],
        ["五", "预备费", reserve],
        ["（一）", "基本预备费", basic],
        ["（二）", "价差预备费", escalation, "估计值"],
        ["（三）", "风险金", risk],
        ["六", "静态总投资", static],
        ["七", "动态总投资", dynamic],
    ]
    for r in rows:
        ws2.append(list(r))
    for row in ws2.iter_rows(min_row=1, max_row=ws2.max_row):
        for c in row:
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=True)
    ws2.column_dimensions["A"].width = 8
    ws2.column_dimensions["B"].width = 32
    ws2.column_dimensions["C"].width = 42
    ws2.column_dimensions["D"].width = 14

    wb.save(str(COST))
    return {"engineering": round(eng_total, 4), "other": other,
            "monitor_care": monitor_care, "basic": basic,
            "static": static, "dynamic": dynamic}


def _fill(ws, values):
    for row in range(2, ws.max_row + 1):
        name = str(ws.cell(row=row, column=1).value or "").strip()
        if name in values:
            ws.cell(row=row, column=2).value = values[name]


def main() -> int:
    from openpyxl import load_workbook

    shutil.copyfile(SRC, DST)
    wb = load_workbook(str(DST))
    _fill(wb["项目基本信息"], BASE)
    _fill(wb["勘界信息"], SURVEY)
    _fill(wb["设计参数"], DESIGN)
    _fill(wb["报告表信息"], FORM)
    _fill(wb["投资估算"], {"投资估算Excel文件名": COST.name})

    ws = wb["土地分类面积"]
    for i, (l1, l2, ha, pct, own) in enumerate(LAND, start=2):
        for col, val in enumerate((i - 1, l1, l2, ha, pct, own, None), start=1):
            ws.cell(row=i, column=col).value = val

    ws = wb["临时用地功能分区"]
    _seq = 0
    for name, ha, method, degree, types, direction in ZONES:
        _plots = ZONE_PLOTS.get(name) or [ha]
        for _k, _plot_ha in enumerate(_plots):
            _seq += 1
            for col, val in enumerate(
                    (_seq, name, _plot_ha, method, degree, types, direction), start=1):
                ws.cell(row=_seq + 1, column=col).value = val
            ws.cell(row=_seq + 1, column=8).value = (
                "地块数=1" if (_k == 0 and len(_plots) == 1) else None)

    ws = wb["公众参与"]
    for i, (who, n, opinion, adopted) in enumerate(PUBLIC, start=2):
        for col, val in enumerate((i - 1, who, n, opinion, adopted, None), start=1):
            ws.cell(row=i, column=col).value = val

    wb.save(str(DST))
    sums = _build_cost()
    MAT.write_text(MATERIAL, encoding="utf-8")
    print("WROTE", DST)
    print("WROTE", COST, sums)
    print("WROTE", MAT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
