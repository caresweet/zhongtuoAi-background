# -*- coding: utf-8 -*-
"""从报告/资料文本里识别项目所属地区（区/县/县级市）。

用途
----
业主口径（2026-11-04）：用户上传一份已写完的报告当模板时，系统要先认出
「这是哪个地区的报告」，再把这份报告登记成**该地区**的模板；地区不一定是
淮安区，也可能是金湖县、洪泽区、涟水县……

做法
----
1. 先扫江苏省县级行政区名单（精确命中权重最高）；
2. 再扫 `X区 / X县 / X市` 形式的候选，必须满足其一：
   - 在江苏名单里；或
   - 紧跟在「位于 / 地处 / 建设地点」等位置词之后；或
   - 紧跟在市名之后（如「宿州市埇桥区」）；
3. 排除「项目区 / 复垦区 / 表土堆放区 / 井场区」这类普通名词；
4. 按「名单命中、后缀、位置词、出现次数」打分，取最高分；
5. 返回命中词、证据句和候选列表，便于人工核对。

不依赖 LLM，纯确定性，毫秒级。
"""

from __future__ import annotations

import re
from typing import Dict, List

# 江苏省县级行政区（2024 年口径）
_JS_COUNTIES = {
    # 南京
    "玄武区", "秦淮区", "建邺区", "鼓楼区", "浦口区", "栖霞区", "雨花台区",
    "江宁区", "六合区", "溧水区", "高淳区",
    # 无锡
    "锡山区", "惠山区", "滨湖区", "梁溪区", "新吴区", "江阴市", "宜兴市",
    # 徐州
    "云龙区", "贾汪区", "泉山区", "铜山区", "丰县", "沛县", "睢宁县",
    "新沂市", "邳州市",
    # 常州
    "天宁区", "钟楼区", "新北区", "武进区", "金坛区", "溧阳市",
    # 苏州
    "虎丘区", "吴中区", "相城区", "姑苏区", "吴江区", "常熟市",
    "张家港市", "昆山市", "太仓市",
    # 南通
    "通州区", "崇川区", "海门区", "如东县", "启东市", "如皋市", "海安市",
    # 连云港
    "连云区", "海州区", "赣榆区", "东海县", "灌云县", "灌南县",
    # 淮安
    "淮安区", "淮阴区", "清江浦区", "洪泽区", "涟水县", "盱眙县", "金湖县",
    # 盐城
    "亭湖区", "盐都区", "大丰区", "响水县", "滨海县", "阜宁县", "射阳县",
    "建湖县", "东台市",
    # 扬州
    "广陵区", "邗江区", "江都区", "宝应县", "仪征市", "高邮市",
    # 镇江
    "京口区", "润州区", "丹徒区", "丹阳市", "扬中市", "句容市",
    # 泰州
    "海陵区", "高港区", "姜堰区", "兴化市", "靖江市", "泰兴市",
    # 宿迁
    "宿城区", "宿豫区", "沭阳县", "泗阳县", "泗洪县",
}

# 以「区」结尾但不是行政区的普通词（整词排除）
_NON_REGION = {
    "地区", "园区", "城区", "郊区", "市区", "辖区", "社区", "小区", "校区",
    "厂区", "矿区", "库区", "采区", "塌陷区", "复垦区", "项目区", "规划区",
    "管控区", "红线区", "区域", "示范区", "开发区", "工业区", "农业区",
    "保护区", "集中区", "居住区", "作业区", "施工区", "场区", "坝区",
    "淹没区", "影响区", "评价区", "治理区", "复绿区", "建设区", "用地区",
    "行政区", "功能区", "片区", "灾区", "疫区", "牧区", "林区", "垦区",
    "沉陷区", "采空区", "回填区", "堆土区", "表土区", "主城区",
}

# 候选里只要含这些词，就不可能是行政区
_BAD_PARTS = (
    "堆放", "井场", "施工", "复垦", "生态", "保护", "功能", "作业", "项目",
    "建设", "规划", "管控", "红线", "影响", "评价", "治理", "沉陷", "塌陷",
    "采空", "回填", "堆土", "表土", "农田", "基本", "临时", "主体", "场站",
    "便道", "取土", "弃土", "储气", "库区", "工业", "农业", "开发", "示范",
    "矿", "图示", "图中", "蓝色", "矩形", "距", "隶属", "两市", "三市",
    "市县", "各省", "界线", "坐标",
)

_LOC_HINTS = ("位于", "地处", "坐落于", "坐落", "建设地点", "项目位置",
              "用地位置", "项目位于", "工程位于", "场址位于", "地址")

# 候选前面必须是这些边界之一，避免把「目位于金湖县」整段抓进来
_BOUNDARY = r"(?:^|[，。；：、\s（()）于在省市县区])"
_CAND = re.compile(_BOUNDARY + r"([一-龥]{2,4}(?:区|县|市))")
_AFTER_CITY = re.compile(r"([一-龥]{2,5}市)([一-龥]{2,4}(?:区|县))")


def _candidate_ok(token: str) -> bool:
    if not token or token in _NON_REGION:
        return False
    if any(b in token for b in _BAD_PARTS):
        return False
    if token.endswith("地区"):
        return False
    return True


def _near_hint(blob: str, token: str) -> bool:
    for hint in _LOC_HINTS:
        start = 0
        while True:
            idx = blob.find(hint, start)
            if idx < 0:
                break
            if token in blob[idx: idx + 40]:
                return True
            start = idx + len(hint)
    return False


def _score(token: str, blob: str, filename: str,
           after_city: bool) -> int:
    score = 0
    if token in _JS_COUNTIES:
        score += 10
    if token.endswith(("区", "县")):
        score += 3
    elif token.endswith("市"):
        score += 1
    if after_city:
        score += 3
    if filename and token in filename:
        score += 6
    if _near_hint(blob, token):
        score += 4
    score += min(blob.count(token), 5)
    return score


def detect_region(text: str, filename: str = "") -> Dict[str, object]:
    """返回 {region, evidence, candidates}；识别不到时 region 为空串。"""
    text = str(text or "")
    filename = str(filename or "")
    blob = f"{filename}\n{text}"
    if not blob.strip():
        return {"region": "", "evidence": "", "candidates": []}

    after_city_tokens = {m.group(2) for m in _AFTER_CITY.finditer(blob)}
    scores: Dict[str, int] = {}

    # ① 江苏名单精确命中
    for token in _JS_COUNTIES:
        if token in blob and _candidate_ok(token):
            scores[token] = max(scores.get(token, 0),
                                _score(token, blob, filename, token in after_city_tokens))

    # ② 市名之后的区县（省外也收，如「宿州市埇桥区」）
    for token in after_city_tokens:
        if _candidate_ok(token):
            scores[token] = max(scores.get(token, 0),
                                _score(token, blob, filename, True))

    # ③ 通用候选：只在「位置词附近」才收，避免表土堆放区/井场区
    for m in _CAND.finditer(blob):
        token = m.group(1)
        if not _candidate_ok(token) or token in scores:
            continue
        if len(token) > 4 and token not in _JS_COUNTIES:
            continue
        if _near_hint(blob, token):
            scores[token] = _score(token, blob, filename, token in after_city_tokens)

    # 「淮安市淮安区」：市名紧邻区名，区名优先，市名降权
    for m in _AFTER_CITY.finditer(blob):
        city, county = m.group(1), m.group(2)
        if city in scores:
            scores[city] = max(0, scores[city] - 6)
        if county in scores:
            scores[county] += 2

    if not scores:
        return {"region": "", "evidence": "", "candidates": []}

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    best = ranked[0][0]
    idx = blob.find(best)
    evidence = blob[max(0, idx - 30): idx + len(best) + 30].replace("\n", " ").strip()
    return {
        "region": best,
        "evidence": evidence,
        "candidates": [{"region": k, "score": v} for k, v in ranked[:6]],
    }


def region_or_empty(text: str, filename: str = "") -> str:
    return str(detect_region(text, filename).get("region") or "")
