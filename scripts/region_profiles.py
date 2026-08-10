"""region_profiles.py — 多层级法规体系 + 区域配置

四级法规体系（国家→省→市→区县），每级可精确匹配和引用。

设计原则:
  - 国家法律: 全国通用，所有地区复用
  - 省级法规: 按省份匹配（DB32江苏/DB43湖南/DB44广东...）
  - 市级规范: 按城市匹配（淮安/南京/苏州...）
  - 区县级: 按区县匹配（洪泽/金湖/江宁...）

LLM生成时引用匹配的所有层级法规。
"""

from typing import Dict, List, Optional

# ═══════════════════════════════════════════════════════════════════
# 一、国家法律（全国通用，所有项目复用）
# ═══════════════════════════════════════════════════════════════════

NATIONAL_LAWS = [
    {"name": "中华人民共和国民法典", "year": "2021"},
    {"name": "中华人民共和国土地管理法", "year": "2020年修正"},
    {"name": "中华人民共和国城乡规划法", "year": "2019年修正"},
    {"name": "中华人民共和国村民委员会组织法", "year": "2018年修正"},
    {"name": "中华人民共和国行政诉讼法", "year": "2017年修正"},
    {"name": "中华人民共和国行政复议法", "year": "2017年修正"},
    {"name": "中华人民共和国突发事件应对法", "year": "2021年修订"},
    {"name": "中华人民共和国土地管理法实施条例", "year": "2021年9月1日起施行"},
    {"name": "中华人民共和国湿地保护法", "year": "2022年6月1日起施行"},
]

NATIONAL_REGULATIONS = [
    {"name": "重大行政决策程序暂行条例", "ref": "国务院令第713号", "year": "2019年9月1日起施行"},
    {"name": "关于加强新形势下重大决策社会稳定风险评估机制建设的意见", "ref": "中办发〔2021〕11号"},
    {"name": "信访工作条例", "year": "2022年5月1日执行"},
    {"name": "重大固定资产投资项目社会稳定风险评估暂行办法", "ref": "发改投资〔2012〕2492号"},
]

NATIONAL_GUIDELINES = [
    {"name": "第三方社会稳定风险评估规范", "ref": "江苏省地方标准DB32/T4013-2021",
     "note": "江苏标准，其他省份可参考或使用本地等效标准"},
]

# ═══════════════════════════════════════════════════════════════════
# 二、省级法规（按省份索引）
# ═══════════════════════════════════════════════════════════════════

PROVINCE_REGULATIONS: Dict[str, Dict] = {
    "江苏省": {
        "province_short": "苏",
        "standards": [
            {"name": "第三方社会稳定风险评估规范", "ref": "DB32/T4013-2021"},
            {"name": "土地征收示范文本", "ref": "2022年8月1日执行"},
        ],
        "laws": [
            {"name": "江苏省土地管理条例", "year": "2021年修订"},
            {"name": "江苏省信访条例", "year": "2021年7月修订"},
        ],
        "regulations": [
            {"name": "江苏省实施〈中华人民共和国突发事件应对法〉办法", "year": "2012年1月1日起施行"},
            {"name": "江苏省重大行政决策程序实施办法", "ref": "省政府令第134号", "year": "2020年8月1日起施行"},
            {"name": "江苏省重大决策社会稳定风险评估第三方机构管理办法", "year": "2021年9月1日"},
            {"name": "关于加强新形势下重大决策社会稳定风险评估机制建设的实施意见", "ref": "苏办发〔2021〕15号"},
            {"name": "江苏省突发事件预警信息发布管理办法", "ref": "苏政办发〔2022〕32号"},
        ],
        "compensation": [
            {"name": "江苏省被征地农民社会保障办法", "ref": "苏政发〔2021〕87号", "year": "2022年3月1日起实施"},
            {"name": "贯彻落实江苏省被征地农民社会保障办法的通知", "ref": "苏人社函〔2022〕85号"},
            {"name": "关于重新公布江苏省征地区片综合地价最低标准的通知", "ref": "苏政规〔2023〕12号"},
        ],
        "format_specs": [
            {"name": "台帐材料印制格式规范", "ref": "DB3201/T1163-2023（南京）/ 南通规范",
             "fonts": {"正文": "仿宋_GB2312", "标题": "黑体", "三级标题": "楷体", "数字": "Times New Roman"}},
        ],
        "government_chain": [
            "{city}市人民政府",
            "{city}市自然资源和规划局{district}分局",
            "{district}区{district}街道办事处",
        ],
    },
    "湖南省": {
        "province_short": "湘",
        "standards": [
            {"name": "社会稳定风险评估规范", "ref": "DB43/TXXXX-2022（如有）或参考DB32/T4013-2021"},
        ],
        "laws": [],
        "regulations": [
            {"name": "湖南省征地补偿和被征地农民社会保障办法"},
            {"name": "湖南省洞庭湖保护条例"},
            {"name": "湖南省重点水域禁捕退捕补偿指导意见"},
        ],
        "compensation": [
            {"name": "关于调整征地补偿标准的通知", "ref": "湘政发〔2024〕号"},
        ],
        "format_specs": [],
        "government_chain": [
            "{city}市{district}区人民政府",
            "{city}市自然资源和规划局{district}分局",
            "{district}街道办事处",
        ],
    },
    "四川省": {
        "province_short": "川",
        "standards": [],
        "laws": [],
        "regulations": [
            {"name": "四川省征地补偿和被征地农民社会保障办法"},
            {"name": "关于公布全省征地区片综合地价的通知", "ref": "川府发〔2023〕号"},
        ],
        "compensation": [],
        "format_specs": [],
        "government_chain": [
            "{city}市人民政府",
            "{city}市规划和自然资源局",
            "{district}区{district}街道办事处",
        ],
    },
    "广东省": {
        "province_short": "粤",
        "standards": [],
        "laws": [],
        "regulations": [
            {"name": "广东省征地补偿和被征地农民社会保障办法"},
            {"name": "关于公布实施征收农用地区片综合地价的公告"},
            {"name": "广东省城市更新条例"},
            {"name": "广东省土地管理条例"},
        ],
        "compensation": [],
        "format_specs": [],
        "government_chain": [
            "{city}市人民政府",
            "{city}市规划和自然资源局{district}管理局",
            "{district}区{district}街道办事处",
        ],
    },
    "浙江省": {
        "province_short": "浙",
        "standards": [],
        "laws": [{"name": "浙江省土地管理条例"}],
        "regulations": [
            {"name": "浙江省征地补偿和被征地农民社会保障办法"},
        ],
        "compensation": [],
        "format_specs": [],
        "government_chain": [
            "{city}市人民政府",
            "{city}市规划和自然资源局",
            "{district}区{district}街道办事处",
        ],
    },
}

# ═══════════════════════════════════════════════════════════════════
# 三、市级规范（按城市索引）
# ═══════════════════════════════════════════════════════════════════

CITY_REGULATIONS: Dict[str, Dict] = {
    "淮安": {
        "province": "江苏省",
        "documents": [
            {"name": "关于加强新形势下重大决策社会稳定风险评估工作的通知", "ref": "淮办〔2020〕53号"},
            {"name": "关于建立全市风险防控四项机制的实施办法", "ref": "淮办〔2020〕60号"},
            {"name": "关于重新公布淮安市所辖各县区征地区片综合地价执行标准的通知", "ref": "淮政规〔2023〕4号"},
        ],
        "plans": [
            {"name": "淮安市国民经济和社会发展第十四个五年规划和二〇三五年远景目标纲要"},
            {"name": "淮安市城市总体规划（2017-2035）"},
            {"name": "淮安市国土空间总体规划（2021-2035）"},
        ],
    },
    "南京": {
        "province": "江苏省",
        "documents": [
            {"name": "南京市征地补偿和被征地农民社会保障办法"},
        ],
        "format_specs": [
            {"name": "台帐材料印制格式规范", "ref": "DB3201/T1163-2023"},
        ],
    },
    "苏州": {
        "province": "江苏省",
        "documents": [],
    },
    "岳阳": {
        "province": "湖南省",
        "documents": [
            {"name": "岳阳市国土空间总体规划（2021-2035年）"},
            {"name": "岳阳市洞庭湖保护实施细则"},
        ],
    },
    "成都": {
        "province": "四川省",
        "documents": [
            {"name": "成都市征地补偿和被征地农民社会保障实施办法"},
            {"name": "成都市国土空间总体规划（2021-2035年）"},
        ],
    },
    "深圳": {
        "province": "广东省",
        "documents": [
            {"name": "深圳市房屋征收与补偿实施办法"},
            {"name": "深圳市城市更新条例实施细则"},
        ],
    },
}

# ═══════════════════════════════════════════════════════════════════
# 四、区县级规范（按区县索引）
# ═══════════════════════════════════════════════════════════════════

DISTRICT_REGULATIONS: Dict[str, Dict] = {
    "洪泽": {
        "city": "淮安",
        "province": "江苏省",
        "plans": [
            {"name": "洪泽区国民经济和社会发展第十四个五年规划和二〇三五年远景目标纲要"},
            {"name": "洪泽城市总体规划（2014-2030）"},
            {"name": "洪泽中心城区用地规划"},
            {"name": "淮安市洪泽区洪泽工业单元（HZ03）控制性详细规划"},
        ],
        "units": [
            {"name": "江苏洪泽经济开发区管理委员会", "role": "稳评责任单位"},
        ],
    },
    "金湖": {
        "city": "淮安",
        "province": "江苏省",
        "plans": [
            {"name": "金湖县城市总体规划（2021-2035年）"},
        ],
    },
}

# ═══════════════════════════════════════════════════════════════════
# 五、匹配引擎
# ═══════════════════════════════════════════════════════════════════

def match_all_levels(description: str) -> Dict:
    """根据项目描述，匹配所有层级的适用法规。

    返回四级法规体系 + 政府机构链。
    """
    result = {
        "national": {
            "laws": NATIONAL_LAWS,
            "regulations": NATIONAL_REGULATIONS,
            "guidelines": NATIONAL_GUIDELINES,
        },
        "province": {},
        "city": {},
        "district": {},
        "government_chain": [],
    }

    # 匹配省份
    for province, config in PROVINCE_REGULATIONS.items():
        if province in description or _city_in_province(description, province):
            result["province"] = {"name": province, **config}
            break

    # 匹配城市
    for city, config in CITY_REGULATIONS.items():
        if city in description:
            result["city"] = {"name": city, **config}
            # 如果省份未匹配，从城市推导省份
            if not result["province"] and config.get("province"):
                pn = config["province"]
                if pn in PROVINCE_REGULATIONS:
                    result["province"] = {"name": pn, **PROVINCE_REGULATIONS[pn]}
            break

    # 匹配区县
    for district, config in DISTRICT_REGULATIONS.items():
        if district in description:
            result["district"] = {"name": district, **config}
            break

    # 构建政府机构链
    if result["province"]:
        chain = result["province"].get("government_chain", [])
        # 尝试提取城市名
        city = result.get("city", {}).get("name", "{city}")
        district = result.get("district", {}).get("name", "{district}")
        result["government_chain"] = [
            tmpl.format(city=city, district=district) for tmpl in chain
        ]

    return result


def _city_in_province(description: str, province: str) -> bool:
    """检查描述中是否有属于该省份的城市。"""
    for city, config in CITY_REGULATIONS.items():
        if city in description and config.get("province") == province:
            return True
    for district, config in DISTRICT_REGULATIONS.items():
        if district in description and config.get("province") == province:
            return True
    return False


def format_all_regulations(matched: Dict) -> str:
    """将四级匹配结果格式化为LLM可读的法规列表。"""
    lines = ["# 适用法规体系\n"]

    # 国家
    lines.append("## 一、国家法律")
    for l in matched["national"]["laws"]:
        lines.append(f"- 《{l['name']}》（{l.get('year', '')}）")
    lines.append("\n## 二、国家法规")
    for r in matched["national"]["regulations"]:
        lines.append(f"- 《{r['name']}》（{r.get('ref', '')}，{r.get('year', '')}）")

    # 省级
    prov = matched.get("province")
    if prov:
        lines.append(f"\n## 三、{prov['name']}法规与标准")
        for cat_name, cat_key in [("技术标准", "standards"), ("地方法规", "laws"),
                                    ("规范性文件", "regulations"), ("补偿安置", "compensation")]:
            items = prov.get(cat_key, [])
            if items:
                lines.append(f"### {cat_name}")
                for item in items:
                    lines.append(f"- 《{item['name']}》（{item.get('ref', '')}{'; ' + item.get('year', '') if item.get('year') else ''}）")

    # 市级
    city = matched.get("city")
    if city:
        lines.append(f"\n## 四、{city['name']}市规范")
        for doc in city.get("documents", []):
            lines.append(f"- 《{doc['name']}》（{doc.get('ref', '')}）")
        for plan in city.get("plans", []):
            lines.append(f"- 《{plan['name']}》")

    # 区县级
    district = matched.get("district")
    if district:
        lines.append(f"\n## 五、{district['name']}区（县）规范")
        for plan in district.get("plans", []):
            lines.append(f"- 《{plan['name']}》")

    # 格式规范
    if prov and prov.get("format_specs"):
        lines.append(f"\n## 六、格式规范")
        for fs in prov["format_specs"]:
            lines.append(f"- {fs['name']}（{fs.get('ref', '')}）")
            if fs.get("fonts"):
                lines.append(f"  字体: {json.dumps(fs['fonts'], ensure_ascii=False)}")

    return "\n".join(lines)


import json


def get_government_chain(matched: Dict) -> List[str]:
    """获取政府机构链。"""
    chain = matched.get("government_chain", [])
    if not chain:
        prov = matched.get("province")
        if prov:
            chain = prov.get("government_chain", [])
    return chain


# ═══════════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    tests = [
        "淮安市洪泽区朱坝街道洞庭湖路工程",
        "淮安市金湖县土地征收项目",
        "湖南省岳阳市洞庭湖区域生态项目",
        "四川省成都市高新区TOD项目",
        "广东省深圳市南山区城市更新项目",
    ]
    for t in tests:
        print(f"\n{'='*60}")
        print(f"  项目: {t}")
        print(f"{'='*60}")
        m = match_all_levels(t)
        print(format_all_regulations(m)[:800])
        print(f"  政府链: {get_government_chain(m)}")
