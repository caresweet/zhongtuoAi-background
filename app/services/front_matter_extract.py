# -*- coding: utf-8 -*-
"""封面 / 扉页「按材料抽取」的字段正则 —— **单一事实源**。

`app/routers/report.py::_analyze_session_attachments` 与
`app/services/deep_material_analyzer.py::_extract_data_from_text` 都 import 本模块。
**绝不各抄一份** —— 本项目已经吃过两次「同一个坏正则抄两份、修了一份漏一份」的亏
（用地面积抽到「总建筑面积」那次，`deep_material_analyzer` 与 `report.py` 各有一份）。

业主口径（2026-10-10）：
    「材料一定要做好，不要有漏的或者分析不出来的还去问用户」
所以这些字段只做**材料抽取**，抽不到就静默降级（扉页那一行不出现），
不为它们新增弹窗提问。
"""

from __future__ import annotations

import re
from typing import Dict, List

def _loose(word: str) -> str:
    """把词拆成「字间允许空白」的正则 —— 材料的 OCR/排版常在词中插空格。

    实测：勘测定界原文写的是「用 地 单位： 中铁建工集团有限公司」
    （「用地单位」四个字被排成了带空格的表格头）。不放宽就漏掉这一份，
    于是项目单位落到后面批复的「项目建设单位」上 —— 而样本扉页取的是勘测定界的
    「用地单位」（中铁建工集团有限公司）。
    """
    return r'\s*'.join(word)


# 「单位名称」类。取「谁在用这块地」——勘测定界的「用地单位」最贴复垦口径。
_UNIT = '(?:' + '|'.join(_loose(w) for w in
                        ("项目建设单位", "项目单位", "用地单位", "建设单位", "复垦义务人")) + ')'
_ADDR = '(?:' + '|'.join(_loose(w) for w in
                        ("单位地址", "单位住所", "注册地址", "单位所在地")) + ')'
_CONTACT = _loose("联系人")
_PHONE = _loose("联系电话") + '|' + r'电\s*话'
_SUBMIT = '(?:' + '|'.join(_loose(w) for w in
                          ("送审时间", "送审日期", "报送日期", "报送时间")) + ')'

_PATTERNS: Dict[str, "re.Pattern"] = {
    "project_unit": re.compile(_UNIT + r'[：:\s]{0,3}([^\s，,；;。\n]{4,40})'),
    # 单位地址：样本里该栏常为空（后面紧跟「电 话」）→ 要求 ≥4 个非空白字符，
    # 「单位地址 电 话 15208624251」这种空值自然不会命中
    "unit_address": re.compile(_ADDR + r'[：:\s]{0,3}([^\s，,；;。\n]{4,60})'),
    # 联系人（排除后面的数字，避免把电话当联系人）
    "contact_name": re.compile(_CONTACT + r'[：:\s]{0,3}([^\s，,；;。、\d]{2,10})'),
    # 联系电话 —— 容忍「电 话」「电　话」里的空白
    "contact_phone": re.compile(r'(?:' + _PHONE + r')[：:\s]{0,3}([\d\-—－]{7,20})'),
    # 送审时间
    "submit_date": re.compile(
        _SUBMIT + r'[：:\s]{0,3}'
        r'(\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?)'),
}


def extract_front_matter_fields(text: str) -> Dict[str, str]:
    """从一段材料文本里抽封面/扉页字段；抽不到的键不出现（**不返回空串占位**）。"""
    out: Dict[str, str] = {}
    s = str(text or "")
    if not s:
        return out
    for key, rx in _PATTERNS.items():
        m = rx.search(s)
        if m and m.group(1).strip():
            out[key] = m.group(1).strip()
    return out


def merge_front_matter(per_material: List[Dict[str, str]]) -> Dict[str, str]:
    """按材料顺序合并各份的抽取结果。

    · `project_unit` / `unit_address` / `submit_date`：**第一个产出者胜**。
    · `contact_name` / `contact_phone`：**优先取同一份材料里成对出现的那一组**。
      实测踩过：勘测定界只给了电话（「电 话 15208624251」），环保批文只给了姓名
      （「联系人 唐卫国」）—— 各取首个拼起来就是「唐卫国 / 15208624251」，
      两个毫不相干的人。成对优先才印得出合理的一组。
    """
    out: Dict[str, str] = {}
    pair: Dict[str, str] = {}
    for d in per_material or []:
        if not isinstance(d, dict):
            continue
        for k in ("project_unit", "unit_address", "submit_date"):
            if d.get(k) and k not in out:
                out[k] = d[k]
        if d.get("contact_name") and d.get("contact_phone") and not pair:
            pair = {"contact_name": d["contact_name"],
                    "contact_phone": d["contact_phone"]}
        for k in ("contact_name", "contact_phone"):
            if d.get(k) and k not in out:
                out[k] = d[k]
    # 成对的那一组覆盖「各取首个」拼出来的组合
    out.update(pair)
    return out
