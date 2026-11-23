# -*- coding: utf-8 -*-
"""按六个地区的测试数据生成《稳评数据资料表》xlsx（供线上生成测试报告）。

数据来源：app/services/test_doc_makeup.py 的 TEST_DATA（与测试成稿同一套虚构值）。

用法：
    cd backend
    python scripts/build_region_test_intakes.py
产物：
    storage/templates/稳评数据资料表_<地区>_测试.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.stability_intake import (  # noqa: E402
    SHEET_BASE, SHEET_TIME, SHEET_SURVEY, build_intake_workbook)
from app.services.test_doc_makeup import TEST_DATA  # noqa: E402

# 地区 → 行政区划与用途（测试数据里没有的补充项）
META = {
    "淮阴区": dict(city="淮安市", district="淮阴区", town="渔沟镇",
                   land_use="工业用地",
                   boundary="东至渔沟镇现状村道，南至农田，西至排水渠，北至规划支路"),
    "洪泽": dict(city="淮安市", district="洪泽区", town="三河镇",
                 land_use="工业用地",
                 boundary="东至三河镇农田，南至现状村道，西至灌溉渠，北至规划纬三路"),
    "金湖": dict(city="淮安市", district="金湖县", town="塔集镇",
                 land_use="工业用地",
                 boundary="东至塔集镇农田，南至现状沟渠，西至乡道，北至灌溉渠"),
    "清江浦区": dict(city="淮安市", district="清江浦区", town="和平镇",
                     land_use="工业用地",
                     boundary="东至和平镇现状道路，南至农田，西至排水沟，北至规划路"),
    "淮安区": dict(city="淮安市", district="淮安区", town="范集镇",
                   land_use="工业用地",
                   boundary="东至现状道路，南至农田，西至农田，北至灌溉渠"),
    "淮安市": dict(city="淮安市", district="淮安市", town="",
                   land_use="输变电工程用地",
                   boundary="东至现状村道，南至农田，西至河道，北至规划道路"),
}


def _fill_sheet(ws, values: dict) -> int:
    n = 0
    for row in ws.iter_rows(min_row=2):
        label = str(row[0].value or "").strip()
        if label in values and values[label] not in (None, ""):
            row[1].value = values[label]
            n += 1
    return n


def build_one(region: str, out_dir: Path) -> Path:
    d = TEST_DATA[region]
    meta = META[region]
    out = out_dir / f"稳评数据资料表_{region}_测试.xlsx"
    build_intake_workbook(out)
    from openpyxl import load_workbook
    wb = load_workbook(out)
    base = {
        "项目名称": d["project"],
        "决策主体": d["unit"],
        "稳评责任单位": d["unit"],
        "项目位置": d["location"],
        "所属地级市": meta["city"],
        "所属区县": meta["district"],
        "所属乡镇/街道": meta["town"],
        "征收面积（亩）": d["area_mu"],
        "征收面积（平方米）": d["area_m2"],
        "土地用途": meta["land_use"],
        "涉及户数": d["households"],
        "补偿安置方案文号": f"{meta['district']}政征补〔2027〕{d['households'] % 20 + 1}号",
        "公告/批复文号": d["doc_ref"],
        "资金筹措单位": f"{meta['district']}财政统筹",
        "界址范围描述": meta["boundary"],
    }
    time_vals = {
        "委托日期": "2027-08-01",
        "公告发布日期": "2027-08-06",
        "稳评公示开始日期": "2027-08-10",
        "现场公示开始日期": "2027-08-12",
        "现场公示结束日期": "2027-08-18",
        "问卷日期": "2027-08-20",
        "实地勘察日期": "2027-08-22",
        "座谈会日期": "2027-08-25",
        "座谈会地点": f"{meta['town'] or meta['district']}人民政府会议室",
        "风险预测开始": "2027-09-01",
        "风险预测结束": "2027-09-10",
        "报告编制开始": "2027-09-12",
        "报告编制结束": "2027-10-20",
        "专家评审开始": "2027-10-25",
        "专家评审结束": "2027-10-26",
        "报告编写年月": "2027-11",
    }
    survey = {
        "问卷份数": d["survey"],
        "群众支持率（%）": 100,
        "群众反对率（%）": 0,
        "群众知晓率（%）": 100,
        "部门问卷单位数": 3,
        "群众诉求": "希望补偿标准公开透明、安置方案尽快落实",
        "部门意见": "同意项目实施，按程序办理用地手续",
        "基层组织意见": "支持征收，配合做好群众工作",
        "网络舆情情况": "未发现负面舆情",
    }
    n = 0
    n += _fill_sheet(wb[SHEET_BASE], base)
    n += _fill_sheet(wb[SHEET_TIME], time_vals)
    n += _fill_sheet(wb[SHEET_SURVEY], survey)
    wb.save(out)
    print(f"{region}: {out.name}（{n} 项）")
    return out


def main() -> None:
    out_dir = BACKEND / "storage" / "templates"
    out_dir.mkdir(parents=True, exist_ok=True)
    for region in TEST_DATA:
        build_one(region, out_dir)


if __name__ == "__main__":
    main()
