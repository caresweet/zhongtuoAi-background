# -*- coding: utf-8 -*-
"""按井神三井组测试数据填一份《复垦基础资料》Excel（用于端到端测试）。

产物：storage/templates/复垦基础资料_井神三井组_测试.xlsx
用法：python scripts/build_reclamation_test_intake.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SRC = BACKEND / "storage" / "templates" / "复垦基础资料_模板.xlsx"
DST = BACKEND / "storage" / "templates" / "复垦基础资料_井神三井组_测试.xlsx"

BASE = {
    "项目名称": "江苏盐业井神楚州张兴储气库工程三井组临时用地",
    "建设单位": "江苏苏盐井神股份有限公司第一分公司",
    "项目性质": "新建",
    "项目类型": "建设项目（天然气储备）",
    "项目位置": "淮安市淮安区山阳街道黄土桥村",
    "所属地区": "淮安区",
    "主体工程用地面积(公顷)": 8.0762,
    "项目总投资(万元)": 651003.92,
    "项目批复文号": "苏自然资规〔2026〕1号",
    "用地期限(月)": 24,
    "用地开始": "2026-10",
    "用地结束": "2028-09",
    "复垦期(月)": 6,
    "管护期(月)": 6,
    "单位地址": "江苏省淮安市淮安区井神路1号",
    "联系人": "刘利伟",
    "联系电话": "13770380120",
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "编制人1": "陈春",
    "编制人2": "林叶青",
    "编制人3": "刘利伟",
}

SURVEY = {
    "土地座落": "淮安市淮安区山阳街道黄土桥村",
    "权属单位": "黄土桥村村民委员会",
    "界址点数": 36,
    "勘测日期": "2026-09-18",
    "临时用地面积(公顷)": 2.3248,
}

DESIGN = {
    "表土剥离厚度(cm)": 30,
    "硬化结构类型": "碎石垫层+钢板",
    "硬化面积(公顷)": 2.0048,
    "面层厚度(cm)": 15,
    "覆土厚度(cm)": 30,
    "表土堆放区面积(公顷)": 0.3200,
    "是否占用永久基本农田": "否",
    "是否占用生态保护红线": "否",
    "复垦方向": "水田",
}

LAND = [
    ("耕地", "水田", 2.2215, "95.56", "黄土桥村"),
    ("交通运输用地", "农村道路", 0.0417, "1.79", "黄土桥村"),
    ("水域及水利设施用地", "沟渠", 0.0616, "2.65", "黄土桥村"),
]

ZONES = [
    ("井场", 0.2310, "挖损和压占", "重度", "水田", "水田"),
    ("施工场站", 1.1950, "压占", "重度", "水田、农村道路、沟渠", "水田"),
    ("施工便道", 0.5788, "压占", "重度", "水田、农村道路、沟渠", "水田"),
    ("表土堆放区", 0.3200, "压占", "中度", "水田", "水田"),
]

PUBLIC = [
    ("黄土桥村村民", 30, "希望复垦后恢复水田", "已采纳"),
    ("村民代表", 12, "关注表土回覆与耕层质量", "已采纳"),
]

FORM = {
    "编制单位": "江苏众拓项目代理咨询有限公司",
    "单位地址": "江苏省淮安市淮安区井神路1号",
    "联系人": "刘利伟",
    "联系电话": "13770380120",
    "项目负责人": "陈春",
    "技术负责人": "林叶青",
    "填表人": "刘利伟",
    "送审时间": "2026-11-04",
}


def _fill_pairs(ws, values: dict) -> None:
    """「字段｜值」两张：按 A 列字段名写 B 列。"""
    for row in range(2, ws.max_row + 1):
        name = str(ws.cell(row=row, column=1).value or "").strip()
        if name in values:
            ws.cell(row=row, column=2).value = values[name]


def main() -> int:
    from openpyxl import load_workbook

    if not SRC.exists():
        print(f"模板不存在：{SRC}")
        return 1
    shutil.copyfile(SRC, DST)
    wb = load_workbook(str(DST))

    _fill_pairs(wb["项目基本信息"], BASE)
    _fill_pairs(wb["勘界信息"], SURVEY)
    _fill_pairs(wb["设计参数"], DESIGN)
    _fill_pairs(wb["报告表信息"], FORM)

    ws = wb["土地分类面积"]
    for i, (l1, l2, ha, pct, own) in enumerate(LAND, start=2):
        ws.cell(row=i, column=1).value = i - 1
        ws.cell(row=i, column=2).value = l1
        ws.cell(row=i, column=3).value = l2
        ws.cell(row=i, column=4).value = ha
        ws.cell(row=i, column=5).value = pct
        ws.cell(row=i, column=6).value = own

    ws = wb["临时用地功能分区"]
    for i, (name, ha, method, degree, types, direction) in enumerate(ZONES, start=2):
        ws.cell(row=i, column=1).value = i - 1
        ws.cell(row=i, column=2).value = name
        ws.cell(row=i, column=3).value = ha
        ws.cell(row=i, column=4).value = method
        ws.cell(row=i, column=5).value = degree
        ws.cell(row=i, column=6).value = types
        ws.cell(row=i, column=7).value = direction

    ws = wb["公众参与"]
    for i, (who, n, opinion, adopted) in enumerate(PUBLIC, start=2):
        ws.cell(row=i, column=1).value = i - 1
        ws.cell(row=i, column=2).value = who
        ws.cell(row=i, column=3).value = n
        ws.cell(row=i, column=4).value = opinion
        ws.cell(row=i, column=5).value = adopted

    wb.save(str(DST))
    print("WROTE", DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
