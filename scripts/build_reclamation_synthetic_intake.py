# -*- coding: utf-8 -*-
"""生成一份「合成项目」的《复垦基础资料》Excel（不依赖井神数据）。

项目为虚构测试项目（淮安区），地类按业主要求：
    水浇地 10000 ㎡、竹林地 12000 ㎡、工业用地 12000 ㎡（合计 3.4000 公顷）。

产物：storage/templates/复垦基础资料_合成项目_测试.xlsx
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SRC = BACKEND / "storage" / "templates" / "复垦基础资料_模板.xlsx"
DST = BACKEND / "storage" / "templates" / "复垦基础资料_合成项目_测试.xlsx"

PROJECT_NAME = "淮安市淮安区智能装备产业园一期工程临时用地"

BASE = {
    "项目名称": PROJECT_NAME,
    "建设单位": "淮安市淮安区新港建设发展有限公司",
    "项目性质": "新建",
    "项目类型": "建设项目（智能装备产业园）",
    "项目位置": "淮安市淮安区新港街道临港村",
    "所属地区": "淮安区",
    "主体工程用地面积(公顷)": 6.2000,
    "项目总投资(万元)": 38000,
    "项目批复文号": "淮发改备〔2026〕88号",
    "用地期限(月)": 12,
    "用地开始": "2027-03",
    "用地结束": "2028-02",
    "复垦期(月)": 6,
    "管护期(月)": 12,
    "单位地址": "淮安市淮安区新港街道港城路18号",
    "联系人": "王建国",
    "联系电话": "0517-85901234",
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "编制人1": "陈春",
    "编制人2": "林叶青",
    "编制人3": "刘利伟",
}

SURVEY = {
    "土地座落": "淮安市淮安区新港街道临港村",
    "权属单位": "新港街道临港村村民委员会",
    "界址点数": 4,
    "勘测日期": "2026-12-08",
    "临时用地面积(公顷)": 3.4000,
}

DESIGN = {
    "表土剥离厚度(cm)": 20,
    "硬化结构类型": "混凝土面层+碎石垫层",
    "硬化面积(公顷)": 1.5000,
    "面层厚度(cm)": 20,
    "垫层厚度(cm)": 15,
    "覆土厚度(cm)": 30,
    "表土堆放区面积(公顷)": 0.2500,
    "是否占用永久基本农田": "否",
    "是否占用生态保护红线": "否",
    "复垦方向": "水浇地",
}

# 一级地类 / 二级地类 / 公顷
LAND = [
    ("耕地", "水浇地", 1.0000, "29.41", "临港村"),
    ("林地", "竹林地", 1.2000, "35.29", "临港村"),
    ("城镇村及工矿用地", "工业用地", 1.2000, "35.30", "临港村"),
]

ZONES = [
    ("材料堆场", 0.9800, "压占", "重度", "水浇地、竹林地", "水浇地"),
    ("施工场站", 0.8500, "压占", "重度", "水浇地、竹林地", "水浇地"),
    ("生活区", 0.7000, "压占", "中度", "竹林地", "竹林地"),
    ("施工便道", 0.6200, "压占", "重度", "水浇地、竹林地", "水浇地"),
    ("表土堆放区", 0.2500, "压占", "中度", "水浇地", "水浇地"),
]

PUBLIC = [
    ("临港村村民", 28, "希望复垦后恢复水浇地，保留竹林", "已采纳"),
    ("村民代表", 10, "关注工业用地硬化层清理", "已采纳"),
]

FORM = {
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "单位地址": "淮安市淮安区新港街道港城路18号",
    "联系人": "王建国",
    "联系电话": "0517-85901234",
    "项目负责人": "陈春",
    "技术负责人": "林叶青",
    "填表人": "刘利伟",
    "送审时间": "2026-12-20",
}


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
    for i, (name, ha, method, degree, types, direction) in enumerate(ZONES, start=2):
        for col, val in enumerate((i - 1, name, ha, method, degree, types, direction), start=1):
            ws.cell(row=i, column=col).value = val

    ws = wb["公众参与"]
    for i, (who, n, opinion, adopted) in enumerate(PUBLIC, start=2):
        for col, val in enumerate((i - 1, who, n, opinion, adopted), start=1):
            ws.cell(row=i, column=col).value = val

    wb.save(str(DST))
    print("WROTE", DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
