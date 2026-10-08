"""字段清洗 —— 把「不是值的东西」从事实卡里剔除。

背景：
    图片/文档分析会把**模型的自述**当成字段值写进 `filled_data`，
    例如 `project_name = "未检测到"`、`announcement_number = "未提及"`、
    `location_name = "未显示"`。这些字符串非空，所以下游的
    「空值 → 缺失字段」判断全部失灵，最终原样印进报告。

本模块做两件事：
    ① **剔除伪值**：把「未检测到 / 未提及 / 未显示 / 未知 / 无 / N/A …」这类
       自述式回答当作空值清掉（只清完全由这类话术构成的短值，不动正常长句）；
    ② **项目名整形**：先去掉末尾的位置括注（「…项目（朱坝街道、三圩社区）」→「…项目」），
       缺失时再由「文号 + 土地用途」拼出一个可用的项目名，不留空、不写「未检测到」。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 完全由「没有答案」的话术构成的短值 —— 视为空
_JUNK_EXACT = {
    "未检测到", "未提及", "未显示", "未明确", "未识别", "未找到", "未获取", "未说明",
    "未知", "不确定", "不清楚", "不详", "无", "没有", "暂无", "空", "无数据", "无信息",
    "未在图片中识别到", "无法识别", "无法辨认", "不适用", "n/a", "na", "none", "null",
    "未标注", "未提供", "未填写", "待补充", "待确认",
}
# 允许作为前缀的修饰（「（图片中）未提及」这类）
_JUNK_PREFIXES = ("图片中", "图中", "文档中", "报告中", "材料中", "文书中", "扫描件中")

# 开头的「没有答案」话术 —— 以它开头且整值很短，就当作非答案
# （如「未检测到明确文档标题」「未包含任何可阅读的文本信息」）
# 🔴 用完整短语而非单个「未/不」，否则会误伤「未利用地」「不同意」这类真值
_JUNK_LEAD = (
    "未检测到", "未提及", "未显示", "未明确", "未识别", "未找到", "未获取", "未说明",
    "未标注", "未提供", "未填写", "未包含", "未涉及", "未发现", "未见到", "未见",
    "无法识别", "无法辨认", "无法确定", "无法判断", "不能确定", "没有显示", "没有提及",
    "不涉及", "暂无相关",
)

_JUNK_MAX_LEN = 24      # 只有短值才判为话术，长句可能是真内容


def is_junk(v: Any) -> bool:
    """判断一个字段值是不是「没有答案」的自述式回答。"""
    if v is None:
        return True
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    t = str(v).strip()
    if not t:
        return True
    if len(t) > _JUNK_MAX_LEN:
        return False
    core = t.strip("　 \t.。:：,，;；()（）[]【】")
    for p in _JUNK_PREFIXES:
        if core.startswith(p):
            core = core[len(p):]
    core = core.strip("　 \t.。:：,，;；()（）[]【】")
    if core.lower() in _JUNK_EXACT:
        return True
    return any(core.startswith(p) for p in _JUNK_LEAD)


# ═══════════════════════════════════════════════════════════════
# 项目名：**优先从材料正文里取**，取不到才由文号派生
# ═══════════════════════════════════════════════════════════════
# 🔴 业主：「第一页的内容应该是 食品xxx的项目。」
#    实测：材料里**到处都写着**真实项目名 ——
#      案卷首页第一行      「食品科技产业园道路工程（项目1）土地征收决策」
#      座谈会签到表        「决策名称:食品科技产业园道路工程(项目1)土地征收决策」
#      专家评审意见表      「决策事项名称 食品科技产业园道路工程（项目1）土地征收决策」
#    但 `_derive_project_name` 拿**文号**拼了个「洪拟征告〔2026〕10号土地征收决策」，
#    封面就印成了文号 —— 有真名不用，自己拼了一个。
#
#    所以：**先把材料翻一遍**，翻不到再退回文号派生。
_PN_LABELED = (
    re.compile(r'决策事项名称\s*[:：]?\s*([^\n\t]{4,60})'),
    re.compile(r'决策名称\s*[:：]?\s*([^\n\t]{4,60})'),
    re.compile(r'项目名称\s*[:：]?\s*([^\n\t]{4,60})'),
)
# 项目名常见的收尾词 —— 用来判断「这一行是不是项目名」
_PN_TAILS = ("土地征收决策", "征收决策", "开发决策", "项目")


def _looks_like_project_name(t: str) -> bool:
    """这一行像不像项目名：长度合适、以「…决策/…项目」收尾、不含日期数字串。"""
    t = re.sub(r'\s+', '', str(t or "")).strip("　 　\t.。:：,，;；")
    if not (4 <= len(t) <= 60):
        return False
    if not t.endswith(_PN_TAILS):
        return False
    # 排除「社会稳定风险评估工作案卷」这类套话与纯公式行
    if any(k in t for k in ("案卷", "报告", "预公告", "公示", "签到表", "意见表", "评估工作")):
        return False
    return True


def extract_project_name(state: Dict[str, Any]) -> str:
    """从会话材料里提取项目名；找不到返回空串。

    扫描顺序：① 带「决策(事项)名称：」标签的行 → ② 材料正文的第一行。
    """
    mats = (state or {}).get("_project_materials") or []
    texts = [str((m or {}).get("text_content") or "") for m in mats if isinstance(m, dict)]
    # ① 带标签的（签到表、专家意见表最可靠）
    for txt in texts:
        for pat in _PN_LABELED:
            for mm in pat.finditer(txt):
                cand = mm.group(1)
                if _looks_like_project_name(cand):
                    return re.sub(r'\s+', '', cand)
    # ② 材料正文第一行（案卷封面）
    for txt in texts:
        for line in str(txt).split("\n")[:5]:
            if _looks_like_project_name(line):
                return re.sub(r'\s+', '', line)
    return ""


def _safe_project_name(state, filled) -> str:
    """取项目名的统一入口：材料优先 → 文号派生兜底。"""
    try:
        got = extract_project_name(state)
        if got:
            return got
    except Exception as e:
        logger.warning(f"材料项目名提取跳过: {e}")
    return _derive_project_name(filled)


def _derive_project_name(filled: Dict[str, Any]) -> str:
    """由文号 + 土地用途拼出项目名。

    🔴 **不要把坐落拼进去**：业主口径是「洪拟征告〔2026〕7号 商业服务业设施用地项目」，
    带上「（朱坝街道办事处、三圩社区、二组、三组、六组）」这类括注会把标题撑得很长，
    而且位置本来就要在第6章单独写。
    """
    doc_ref = str(filled.get("doc_reference") or "").strip()
    land_use = str(filled.get("land_use") or "").strip()
    if is_junk(land_use):
        land_use = ""
    if not (doc_ref or land_use):
        return ""
    # 「洪拟征告〔2026〕7号 商业服务业设施用地项目」
    head = f"{doc_ref} " if doc_ref else ""
    body = f"{land_use}项目" if land_use else "拟征收土地项目"
    return (head + body).strip()[:80]


# 项目名末尾的「位置括注」——回填时不能带，读到的旧值也要去掉
_TRAILING_LOC_PAREN = re.compile(r'[（(][^（()）]{0,60}?[）)]\s*$')


def strip_trailing_location(name: str, location: str = "") -> str:
    """去掉项目名末尾的位置括注（如「…项目（朱坝街道办事处、三圩社区）」）。

    只在该括注确实像地名时去掉：含「街道/镇/乡/村/社区/组/区/县」任一特征词，
    或与事实卡的 location 有重叠 —— 避免误删「（一期）」「（送审稿）」这类正常括注。
    """
    t = str(name or "").strip()
    if not t:
        return t
    m = _TRAILING_LOC_PAREN.search(t)
    if not m:
        return t
    inner = m.group(0).strip("（）() \t")
    loc = str(location or "").strip()
    _hints = ("街道", "镇", "乡", "村", "社区", "组", "区", "县", "农场", "管理区")
    looks_loc = any(h in inner for h in _hints)
    if not looks_loc and loc:
        looks_loc = bool(inner) and (inner[:4] in loc or loc[:4] in inner)
    return t[:m.start()].strip() if looks_loc else t


def sanitize_fields(filled: Dict[str, Any], *, verbose: bool = False,
                    state: Dict[str, Any] = None) -> List[str]:
    """就地清洗事实卡。返回被清掉的字段名列表。"""
    if not isinstance(filled, dict):
        return []
    cleared: List[str] = []

    # ① 剔除伪值（跳过内部字段与结构化字段）
    for k in list(filled.keys()):
        if k.startswith("_"):
            continue
        v = filled[k]
        if isinstance(v, (list, dict)):
            continue
        if is_junk(v):
            filled[k] = ""
            cleared.append(k)

    # ② 项目名：先去末尾的位置括注，再回填
    _loc = filled.get("location") or filled.get("land_location") or ""
    # 🔴 **材料里有真项目名就用真名** —— 哪怕当前值「看起来不垃圾」。
    #    实测：当前值是文号派生的「洪拟征告〔2026〕10号土地征收决策」，
    #    `is_junk()` 判它不垃圾 → 不替换 → 封面印的是**文号**不是项目名
    #    （业主：「第一页的内容应该是 食品xxx的项目」）。
    #    只在**用户亲手填过**（`_locked_project_name`）时才让位。
    try:
        _from_mat = extract_project_name(state) if state else ""
    except Exception as _ee:
        logger.warning(f"材料项目名提取跳过: {_ee}")
        _from_mat = ""
    for key in ("project_name", "project_title"):
        # ⚠️ 判据用 `_confirmed_`（**用户真的填过**）而不是 `_locked_` ——
        #    `_locked_` 是「别覆盖我」的旧语义，**系统自己也会打**：
        #    `field_fixer` 从文号派生项目名后打了 `_locked_project_name`，
        #    于是材料里的真名永远顶不掉它（实测就卡在这一步）。
        if _from_mat and not filled.get(f"_confirmed_{key}"):
            if _from_mat != str(filled.get(key) or "").strip():
                filled[key] = _from_mat
                cleared.append(f"{key}(改用材料项目名)")
            continue
        cur = str(filled.get(key) or "")
        if cur and not is_junk(cur):
            cleaned = strip_trailing_location(cur, str(_loc))
            if cleaned and cleaned != cur:
                filled[key] = cleaned
                cleared.append(f"{key}(去位置括注)")
        if is_junk(filled.get(key)):
            derived = _safe_project_name(state, filled)
            if derived:
                filled[key] = derived

    if verbose and cleared:
        logger.info(f"[字段清洗] 剔除 {len(cleared)} 个伪值字段: {cleared[:12]}")
    return cleared
