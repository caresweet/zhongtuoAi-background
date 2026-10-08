"""字段取值链：**用户填的 > 材料识别的 > 占位**。

🔴 业主口径（2026-09-22）
────────────────────────────────────────────────────────────────────────
「每次都会给用户出弹窗让填写，用户填了就用用户填的，用户跳过就用**预公告里识别到的**，
  两者都没有才落「待填写」占位。其他内容也都一样。」

这条口径统一了此前**两处打架**的做法：
  · `routers/report.py` 的预公告解析会把材料识别值写进 `filled_data` 并打 `_locked_` 标记；
  · 而 `review_table_service` 只看自己那几个键（`org_phone` 等），材料值在**别的键名**下
    （`contact_phone`）→ 评审表永远显示「【待填写】」，哪怕材料里明明有电话。

🔴 最后一级**按用途分叉**（这是本模块唯一需要调用方交代的事）
  · **表单类**（评审表）→ 落占位符 `【待填写】`，人工一眼看到该补哪儿；
  · **正文** → 落**中性表述**（`default` 传业务话术，如「以预公告载明为准」），
    绝不写占位符 —— 业主硬约束：「成稿中不得出现【待补充】/【待填写】字样」。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Sequence

# 表单里「该填没填」的占位（评审表专用）
PLACEHOLDER = "【待填写】"

# 同一语义在不同来源下的**键名别名**。
# 🔴 只放**确实同义**的：材料识别（预公告/勘测定界解析）与前端弹窗各自用了不同的键，
#    但说的是同一件事。不做「模糊匹配」—— 宁可少接一根线，也不要把 A 单位写成 B 单位。
MATERIAL_ALIASES: Dict[str, Sequence[str]] = {
    # 稳评责任单位：弹窗问 `org_name`；预公告解析出的是 `org_name`，另可能落在 `decision_unit`
    "org_name": ("decision_unit", "announcement_org"),
    # 责任单位联系人 / 电话：弹窗问 `org_*`；预公告落款解析出的是 `contact_*`
    "org_contact": ("contact_name",),
    "org_phone": ("contact_phone",),
    # 决策主体：弹窗问 `decision_unit`；预公告解析出的是 `org_name`
    "decision_unit": ("org_name",),
    # 项目名称：弹窗问 `project_name`；预公告解析出的是 `announcement_title` / 文号
    "project_name": ("announcement_title", "doc_reference"),
    # 公告发布日：弹窗问 `notice_publish_date`；预公告解析出的是 `publish_date`
    "notice_publish_date": ("publish_date",),
}

# 明显不是「真值」的填充物 —— 视为未填
_PSEUDO = ("", "-", "—", "/", "无", "N/A", "n/a", "null", "None", "nan",
           "未检测到", "未提及", "未提供", "未知", "不详")


def _clean(v: Any) -> str:
    s = str(v if v is not None else "").strip()
    if s in _PSEUDO:
        return ""
    # 已带占位符的值不算「有值」，否则会把占位符当用户填的内容传下去
    if s.startswith("【待"):
        return ""
    return s


def resolve(filled: Dict[str, Any], key: str, *,
            default: str = PLACEHOLDER,
            extra_chain: Optional[Iterable[str]] = None) -> str:
    """按「用户填的 > 材料识别的 > 占位」取一个字段值。

    Args:
        filled: `state["filled_data"]`
        key:    目标字段名（前端弹窗用的那个键）
        default: 三级都取不到时的落点。**表单**传 `PLACEHOLDER`（默认），
                 **正文**传中性表述。
        extra_chain: 额外别名（放在内置别名之后）。
    """
    filled = filled or {}
    chain = [key, *(MATERIAL_ALIASES.get(key) or ()), *(extra_chain or ())]
    for k in chain:
        v = _clean(filled.get(k))
        if v:
            return v
    return default


def resolve_many(filled: Dict[str, Any], keys: Iterable[str], *,
                 default: str = PLACEHOLDER) -> Dict[str, str]:
    """批量版（键序即优先级序）。"""
    return {k: resolve(filled, k, default=default) for k in keys}


# 材料侧键名 → 报告字段名。**只放确实同义的**：宁可少接一根线，
# 也不要把 A 单位写成 B 单位（业主口径：不做模糊匹配）。
CANONICAL_ALIASES: Dict[str, Sequence[str]] = {
    # 稳评责任单位：材料里叫 responsible_unit / decision_unit
    "org_name": ("responsible_unit", "decision_unit", "announcement_org"),
    # 公告发布日：预公告解析出的是 publish_date；扫描件 OCR 落的是 announcement_date
    "notice_publish_date": ("publish_date", "announcement_date"),
    # 🔴 位置：PDF 扫描件 OCR 落的是 `land_location`（与弹窗字段 `location` 同义）。
    #    不接这根线 = 公告里识别到的位置白识别，正文还得回头问用户。
    "location": ("land_location",),
    # 🔴 面积：PDF OCR 落的是 `land_area_sqm` / `land_area_mu`（与 area_m2/area_mu 同义）
    "area_m2": ("land_area_sqm",),
    "area_mu": ("land_area_mu",),
    # 🔴 公告期限：扫描件 OCR 落的是 `announcement_period`
    "notice_period_text": ("announcement_period",),
    # 🔴 座谈记录里解析出的群众诉求 → 第9章 spec 依赖的 `villager_demands`
    #    （此前 `public_demands` 解析出来没有任何读取点）
    "villager_demands": ("public_demands",),
    # 🔴 预公告落款的「联系人 / 电话」→ 评审表「稳评责任单位·联系人 / 联系电话」。
    #    `parse_announcement` 一直在抽 `contact_name` / `contact_phone`
    #    （实测洪拟征告〔2026〕10 号抽到「王慧 / 0517-80920828」），
    #    但**全仓库没有任何键叫 org_contact / org_phone** 的去读它 ——
    #    于是评审表弹窗追着用户要一份公告上明明印着的电话。
    "org_contact": ("contact_name",),
    "org_phone": ("contact_phone",),
}


def canonicalize_material(dst: Dict[str, Any]) -> list:
    """把材料别名键**就地**归一到报告字段名，返回新增的规范键列表。

    🔴 为什么必须做：弹窗的缺失判断（`report_workflow._missing_for`）与各章 spec
       只读规范键，材料识别把值写在别名键上 =「识别到了也当没识别」，
       用户上传过的信息仍会被逐项追问。

    规则：
      · 只在规范键**为空**时写入（用户填过的值优先级最高，绝不覆盖）；
      · 面积只在带单位「亩」时归一到 `area_mu` —— 材料里的「面积」可能是平方米，
        盲目当归会造出一个错面积（报告里数字错比空着更糟）；
      · 只新增键、不删除别名键（下游可能仍在读原键名）。
    """
    if not isinstance(dst, dict):
        return []
    added: list = []
    for canonical, aliases in CANONICAL_ALIASES.items():
        if _clean(dst.get(canonical)):
            continue
        for a in aliases:
            v = _clean(dst.get(a))
            if v:
                dst[canonical] = v
                added.append(canonical)
                break
    # 面积：仅「带亩」的值才能当归
    if not _clean(dst.get("area_mu")):
        m = re.match(r'^\s*(\d+(?:\.\d+)?)\s*亩', str(dst.get("area") or ""))
        if m:
            dst["area_mu"] = m.group(1)
            added.append("area_mu")
    # 🔴 归一出来的键**必须打上「材料」来源**。否则下游（如
    #    `report_workflow.node_review_table` 的「只保留 USER/MATERIAL 值」判据）
    #    会把它们当成 AI 抽的值**清空重问** —— 材料里明明有的联系人和电话
    #    又变成弹窗，归一等于白做。
    if added:
        try:
            from app.services.field_sources import MATERIAL, mark
            mark(dst, MATERIAL, *added)
        except Exception as e:
            logger.warning(f"归一字段来源标记失败（不影响取值）: {e}")
    return added
