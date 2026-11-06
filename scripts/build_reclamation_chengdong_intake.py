# -*- coding: utf-8 -*-
"""第三份测试数据：淮安区城东污水处理厂扩建工程（虚构，模拟用户新资料）。

与上一份「智能装备产业园」不同：
- 用地类型是**线性管道工程**（开挖带），不是厂区；
- 地类是旱地/果园/沟渠/农村道路；
- **设计参数不填 Excel**，只写在材料文本里，模拟用户上传的可研/施工组织设计。

产物：
    storage/templates/复垦基础资料_城东污水厂_测试.xlsx
    storage/templates/城东污水厂_材料.txt
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SRC = BACKEND / "storage" / "templates" / "复垦基础资料_模板.xlsx"
DST = BACKEND / "storage" / "templates" / "复垦基础资料_城东污水厂_测试.xlsx"
MAT = BACKEND / "storage" / "templates" / "城东污水厂_材料.txt"

PROJECT = "淮安市淮安区城东污水处理厂扩建工程临时用地"

BASE = {
    "项目名称": PROJECT,
    "建设单位": "淮安市淮安区水务投资有限公司",
    "项目性质": "改扩建",
    "项目类型": "建设项目（污水处理）",
    "项目位置": "淮安市淮安区钦工镇宋集村",
    "所属地区": "淮安区",
    "主体工程用地面积(公顷)": 5.4000,
    "项目总投资(万元)": 21500,
    "项目批复文号": "淮发改审〔2026〕52号",
    "用地期限(月)": 18,
    "用地开始": "2027-01",
    "用地结束": "2028-06",
    "复垦期(月)": 6,
    "管护期(月)": 12,
    "单位地址": "淮安市淮安区钦工镇人民路9号",
    "联系人": "张海峰",
    "联系电话": "0517-85661122",
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "编制人1": "陈春",
    "编制人2": "林叶青",
    "编制人3": "刘利伟",
}

SURVEY = {
    "土地座落": "淮安市淮安区钦工镇宋集村",
    "权属单位": "钦工镇宋集村村民委员会",
    "界址点数": 4,
    "勘测日期": "2026-11-26",
    "临时用地面积(公顷)": 2.6800,
}

# 🔴 设计参数**留空**——材料里给（模拟真实上传）
DESIGN = {
    "表土堆放区面积(公顷)": 0.3000,
    "是否占用永久基本农田": "否",
    "是否占用生态保护红线": "否",
    "复垦方向": "旱地",
}

LAND = [
    ("耕地", "旱地", 1.4000, "52.24", "宋集村"),
    ("园地", "果园", 0.6000, "22.39", "宋集村"),
    ("水域及水利设施用地", "沟渠", 0.3800, "14.18", "宋集村"),
    ("交通运输用地", "农村道路", 0.3000, "11.19", "宋集村"),
]

# 逐个地块面积：同名分区写多行 = 该分区下的各个地块。
# 模板 表4-2 的面积列是「分区小计 | 逐个地块面积」两栏，靠这份数据岔开。
ZONE_PLOTS = {
    "施工场站": [0.4200, 0.5600],
    "管道开挖带": [0.3000, 0.3200, 0.3000],
    "施工便道": [0.4800],
    "表土堆放区": [0.1300, 0.1700],
}

ZONES = [
    ("施工场站", 0.9800, "压占", "重度", "旱地、果园", "旱地"),
    ("管道开挖带", 0.9200, "挖损和压占", "重度", "旱地、果园、沟渠", "旱地"),
    ("施工便道", 0.4800, "压占", "重度", "旱地、农村道路", "旱地"),
    ("表土堆放区", 0.3000, "压占", "中度", "旱地", "旱地"),
]

PUBLIC = [
    ("宋集村村民", 26, "希望复垦后恢复旱地耕作", "已采纳"),
    ("村民代表", 9, "关注管道开挖带表土回覆", "已采纳"),
]

FORM = {
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "单位地址": "淮安市淮安区钦工镇人民路9号",
    "联系人": "张海峰",
    "联系电话": "0517-85661122",
    "项目负责人": "陈春",
    "技术负责人": "林叶青",
    "填表人": "刘利伟",
    "送审时间": "2027-01-10",
}

MATERIAL = """淮安市淮安区城东污水处理厂扩建工程临时用地土地复垦方案（施工组织设计摘录）

一、项目概况
本工程为城东污水处理厂扩建工程，临时用地位于淮安市淮安区钦工镇宋集村，
主要用于施工场站、管道开挖带、施工便道和表土堆放，临时用地面积 2.6800 公顷，
共 12 个地块。项目主体工程总投资 21500 万元，临时用地期限 18 个月。

二、表土剥离与回覆
临时用地施工前，对现状耕地和园地剥离表土，剥离厚度 25cm，剥离的表土集中堆存于
表土堆放区，复垦时全部回覆。复垦覆土厚度按 30cm 控制。

三、场地硬化与拆除
施工场站采用 10cm 厚碎石垫层，其上铺设钢板，钢板由建设单位回收。
需清除地面垫层的面积为 1.1000 公顷，垫层清除后外运至指定弃土场。

四、复垦工期
土地复垦方案服务年限为 36 个月，其中复垦施工期 6 个月，管护期 12 个月。

五、界址点坐标表
1 3734000.00 40424000.00
2 3734000.00 40424200.00
3 3734134.00 40424200.00
4 3734134.00 40424000.00
"""


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

    ws = wb["土地分类面积"]
    for i, (l1, l2, ha, pct, own) in enumerate(LAND, start=2):
        for col, val in enumerate((i - 1, l1, l2, ha, pct, own), start=1):
            ws.cell(row=i, column=col).value = val

    ws = wb["临时用地功能分区"]
    _seq = 0
    for name, ha, method, degree, types, direction in ZONES:
        _plots = ZONE_PLOTS.get(name) or [ha]
        # 同名分区一个地块一行；只有一个地块时也写一行，并写明地块数=1
        for _k, _plot_ha in enumerate(_plots):
            _seq += 1
            for col, val in enumerate(
                    (_seq, name, _plot_ha, method, degree, types, direction), start=1):
                ws.cell(row=_seq + 1, column=col).value = val
            if _k == 0 and len(_plots) == 1:
                ws.cell(row=_seq + 1, column=8).value = "地块数=1"

    ws = wb["公众参与"]
    for i, (who, n, opinion, adopted) in enumerate(PUBLIC, start=2):
        for col, val in enumerate((i - 1, who, n, opinion, adopted), start=1):
            ws.cell(row=i, column=col).value = val

    wb.save(str(DST))
    MAT.write_text(MATERIAL, encoding="utf-8")
    print("WROTE", DST)
    print("WROTE", MAT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def build_permanent_summary() -> Path:
    """用制式《土地分类面积汇总表》做一份「主体用地」测试表（公用设施用地 5.4 公顷）。"""
    import xlrd
    import openpyxl
    src = Path.home() / "Downloads" / "4_土地分类面积汇总表空白(1).xls"
    if not src.exists():
        raise SystemExit(f"缺少制式表：{src}")
    sh = xlrd.open_workbook(str(src)).sheet_by_index(0)
    grid = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    col = None
    for c, v in enumerate(grid[5]):
        if "公用设施用地" in str(v).replace("\r", "").replace("\n", "").replace(" ", ""):
            col = c
            break
    if col is None:
        raise SystemExit("制式表里找不到「公用设施用地」列")
    for r in grid[6:]:
        if str(r[1] if len(r) > 1 else "").strip() in ("合计",):
            r[col] = 54000
        if str(r[1] if len(r) > 1 else "").strip() == "集体合计":
            r[col] = 54000
    out = BACKEND / "storage" / "templates" / "土地分类面积汇总表_主体.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "土地分类面积表"
    for r, row in enumerate(grid, start=1):
        for c, v in enumerate(row, start=1):
            ws.cell(row=r, column=c).value = v
    wb.save(str(out))
    return out
