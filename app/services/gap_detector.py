"""遁词检测 —— 把「以…为准」这类**推脱**句识别成显式缺口。

🔴 为什么不能见到「以…为准」就标
────────────────────────────────────────────────────────────────────────
稳评报告里有一半「以…为准」是**标准法律表述**，本来就该这么写 ——
稳评是在补偿安置方案**批准之前**做的，「以批准的方案为准」陈述的是事实，不是推脱。
把它们也标成缺口 → 满屏假警报 → 客户看两条就不看了 → **真缺口被淹没**。

真正的遁词只有一个判据：

    **这个数据本来就在我们手上的材料里，却推给了别人 / 别的时点。**

实测样本（会话 a5b889e9…，全篇 28 处命中）：

    ✅ 放行：「具体资金预算和拨付安排以征收实施单位公布的补偿安置方案为准」
             —— 方案还没批准，客观不存在，这是合法援引
    ❌ 遁词：「实际参加人员以**座谈会签到表**为准」
             —— 签到表（`签到表_c3195dfe.jpg`）就躺在上传材料里，从来没被解析过
    ❌ 遁词：「具体征收范围以征收土地预公告所附**红线图及勘测定界成果**为准」
             —— 勘测定界成果我们已经解析成表6-1 了，正文却还在推

🔴 产出去向
    `state["_chapter_gaps"]` → 组装器写成 **Word 批注**（挂在对应章节标题上）。
    客户打开交付稿、点开右侧批注栏，就能看到「这章缺什么、为什么缺」。
    **不往正文塞占位符** —— 业主硬约束：成稿中不得出现【待补充】/【待填写】。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── 「以…为准」式援引 ──
# 🔴 **只认「以…为准」**。实测「按照…执行 / 依据…确定」这类是**正常行文**
#    （「按照土地利用总体规划确定的用途开发」「资金拨付严格按签订协议执行」），
#    它们描述的是**做法**，不是在推脱**数据** —— 一并收进来会造出一堆假警报，
#    客户看两条就不看了，真缺口反而被淹没。
_REFER_RE = re.compile(r'以[^，。；\n]{0,28}为准')

# ── 「待…后补充」式悬置 ──
_HANG_RE = re.compile(r'待[^，。；\n]{0,26}(?:后|完成|确认|明确|出具|批复)[^，。；\n]{0,20}')

# ── 模型自曝截断（「公告原文表述至此」这种）──
_TRUNCATED_RE = re.compile(r'(?:原文|材料)[^，。；\n]{0,10}(?:表述至此|至此为|到此为止|不完整|有截断)')

# ── 「无依据的判断句」 ──────────────────────────────────────────────
# 🔴 业主：「生成出来的内容还是很虚，不像专家写的。」
#    实测根因**不是用词问题，是「下了判断但没给依据」**：
#    抽测 12 章共 33 句评价性断言，其中 22 句句内**没有任何数字/文号/法规名/专名**，
#    纯靠形容词撑着（「有利于完善…配套」「总体实施难度相对可控」）。
#    禁词表治不了这个 —— 它管「语气」，不管「有没有事实」。
_JUDGE_RE = re.compile(
    r'(有利于|有助于|符合[^，。；\n]{0,14}(?:需要|要求|实际|规划)|'
    r'具备[^，。；\n]{0,12}(?:能力|条件)|具有[^，。；\n]{0,10}(?:意义|作用)|'
    r'为[^，。；\n]{0,14}(?:提供|奠定|营造|创造)|'
    r'与[^，。；\n]{0,12}(?:相匹配|相适应)|'
    r'总体[^，。；\n]{0,8}(?:可控|良好|可行|稳定)|'
    r'切实|有力(?:保障|支撑|推动)|有效保障)')

# 「有依据」的判据：数字 / 文号 / **法规或文件的完整名称**
_EVIDENCE_RE = re.compile(r'(\d|〔\d{4}〕|《[^》]+》)')

# 免判：① 法定口径（「以…为准」是必须写的规范表述，不是虚）
#       ② 方法性说明（「评估…是否…」在交代要评估什么，不是在下结论）
_HOLLOW_EXEMPT_RE = re.compile(r'(是否|以[^，。；\n]{0,20}为准)')


def detect_hollow_claims(markdown: str, chapter_no: int = 0) -> List[Dict[str, str]]:
    """找「下了判断却没有依据」的句子。

    判据：句内有评价性谓语（有利于/符合…需要/具备…条件…），
          且句内**没有任何**数字、文号、法规名。
    """
    out: List[Dict[str, str]] = []
    for sent in re.split(r'(?<=[。；])', markdown or ""):
        t = sent.strip()
        if len(t) < 10:
            continue
        if not _JUDGE_RE.search(t):
            continue
        if _EVIDENCE_RE.search(t):        # 句内有数字/文号/法规名 → 有依据
            continue
        if _HOLLOW_EXEMPT_RE.search(t):   # 法定口径 / 方法性说明
            continue
        out.append({
            "field": f"第{chapter_no}章",
            "reason": "hollow_claim",
            "severity": "warn",
            "detail": "这句话下了判断但没给依据（句内无数字/文号/法规名）"
                      "—— 请补依据，或改写成事实陈述",
            "sentence": t[:120],
        })
    return out

# 🔴 **未来法定程序**的产物 —— 现在客观上还不存在，援引它们是合法的。
#    判据是**时点**，不是词义：「批准/审批/备案」都指向「本报告之后才发生」的事。
_FUTURE_STAGE = ("批准", "审批", "备案", "批复", "主管部门", "上级",
                 "党委", "政府公布", "实施单位公布", "依法", "另行")

# 🔴 **我们手上已经有**的材料 —— 正文却把它推出去，才是真遁词。
_WE_HAVE_HINTS: Dict[str, str] = {
    "签到表": "签到表已在上传材料中（尚未解析出参会人员）",
    "座谈会记录": "座谈会记录已在上传材料中",
    "勘测定界": "勘测定界成果已解析（表6-1 土地分类面积表）",
    "红线图": "征收范围红线图已随预公告上传",
    "预公告": "征收土地预公告已上传并解析",
    "公示": "公示照片已在上传材料中",
    "问卷调查": "问卷统计已由逐页视觉解析得出",
}

# 正文里提到的数据 → (事实卡字段, 展示名)。用来把「含糊表述」升级成「其实我们有」。
_HAVE_FIELDS: Dict[str, tuple] = {
    "户数": ("household_count", "涉及户数"),
    "人数": ("household_count", "涉及户数"),
    "面积": ("area_m2", "拟征收面积"),
    "坐落": ("location", "坐落位置"),
    "位置": ("location", "坐落位置"),
    "用途": ("land_use", "土地用途"),
    "参会人员": ("symposium_attendees", "座谈会参会人员"),
}


def _have_assets(state: Dict[str, Any]) -> Dict[str, str]:
    """本项目**事实卡里已经有**的值：关键词 → 「展示名＝值」。

    用来把「以现状调查结果为准」这类含糊句，升级成
    「涉及户数事实卡里已有 120」—— 客户一眼知道**这个能补**，不是要等调查。
    """
    filled = (state or {}).get("filled_data") or {}
    have: Dict[str, str] = {}
    for kw, (field, label) in _HAVE_FIELDS.items():
        v = str(filled.get(field) or "").strip()
        if v:
            have[kw] = f"{label}（事实卡中已有：{v[:40]}）"
    return have


def detect_evasions(markdown: str, chapter_no: int = 0,
                    state: Dict[str, Any] = None) -> List[Dict[str, str]]:
    """扫描一章正文里的遁词，返回缺口清单（不修改正文）。"""
    if not markdown:
        return []
    have = _have_assets(state or {})
    out: List[Dict[str, str]] = []

    def _add(kind: str, sentence: str, detail: str, severity: str = "warn"):
        out.append({
            "field": f"第{chapter_no}章",
            "reason": kind,
            "severity": severity,
            "detail": detail,
            "sentence": sentence.strip()[:120],
        })

    for m in _REFER_RE.finditer(markdown):
        sent = _sentence_of(markdown, m.start())
        body = m.group(0)

        # ① 指向未来法定程序（批准/审批/备案…）→ 合法援引，放行
        if any(k in body for k in _FUTURE_STAGE):
            continue
        # ② 援引**本身**带了数字/日期（如「以2026年7月的公告为准」）→ 是有依据的表述，放行。
        #    🔴 判据只能是**援引片段**，不能是整句：实测「2026年9月6日，评估工作组在
        #       朱坝街道集体组织座谈会，…，实际参加人员以座谈会签到表为准。」整句带日期，
        #       按整句判会把这条真遁词放过去（漏报比误报更糟 —— 客户看不到缺口）。
        if _HAS_DIGIT.search(body):
            continue

        # ③ 推的正是我们手上有的东西 → 真遁词
        hit = next((v for k, v in _WE_HAVE_HINTS.items() if k in body), "")
        if hit:
            _add("evasion_phrase", sent,
                 f"正文以「{body}」回避，但{hit} —— 请补实际内容")
            continue
        # ④ 句中提到的数据，事实卡里其实已经有 → 不是"要等调查"，是"没接上"
        own = next((v for k, v in have.items() if k in sent), "")
        if own:
            _add("evasion_phrase", sent,
                 f"正文以「{body}」回避，但{own} —— 请直接换成实际值")
            continue
        _add("vague_reference", sent,
             f"「{body}」未给出具体依据，请核对是否应换成实际数据", "info")

    for m in _HANG_RE.finditer(markdown):
        sent = _sentence_of(markdown, m.start())
        if _HAS_DIGIT.search(m.group(0)):   # 同上：只看悬置片段本身
            continue
        _add("deferred_data", sent, f"「{m.group(0)}」把数据推给后续程序，请确认现在能否补齐")

    for m in _TRUNCATED_RE.finditer(markdown):
        _add("material_truncated", _sentence_of(markdown, m.start()),
             f"材料原文疑似截断：「{m.group(0)}」—— 请核对原件")

    # 🔴 「下了判断但没给依据」的空泛句（业主：「内容还是很虚，不像专家写的」）。
    #    与遁词并列跑 —— 两者都是「正文里没有实质内容」的表现，只是形态不同：
    #      · 遁词   = 把缺口推给别人/别的时点（「以…为准」）
    #      · 空泛句 = 用形容词把缺口盖住（「有利于完善…配套」）
    out.extend(detect_hollow_claims(markdown, chapter_no))

    return out


def _sentence_of(text: str, pos: int) -> str:
    """取 pos 所在的整句（以中文句读切分）。"""
    start = max((text.rfind(c, 0, pos) for c in "。！？\n"), default=-1) + 1
    end = min((i for i in (text.find(c, pos) for c in "。！？\n") if i != -1),
              default=len(text)) + 1
    return text[start:end].strip()


_HAS_DIGIT = re.compile(r'\d')


def collect_gaps(state: Dict[str, Any], chapters: Dict[int, Any] = None) -> Dict[int, List[dict]]:
    """汇总「遁词检测」+「已有缺口」，按章号归并。

    `chapters` 传 `{章号: markdown}`；已有缺口从 `state["_chapter_gaps"]` 取。

    🔴 **必须按 (章, reason, 原句) 去重**：`_chapter_gaps` 里可能已经有上一次
       遁词检测的结果（组装报告时会写进去并随会话持久化），这里再检测一遍就会
       **同一处缺口出现两条** —— 实测第6章 5 项翻成 10 项、弹窗里一问两遍。
    """
    state = state or {}
    acc: Dict[int, List[dict]] = {}
    seen: set = set()

    def _push(ch: int, items) -> None:
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            sig = (ch, str(it.get("reason") or ""), str(it.get("sentence") or ""),
                   str(it.get("detail") or ""))
            if sig in seen:
                continue
            seen.add(sig)
            acc.setdefault(ch, []).append(it)

    for ch, items in (state.get("_chapter_gaps") or {}).items():
        try:
            _push(int(ch), items)
        except (ValueError, TypeError):
            continue

    for ch, md in (chapters or {}).items():
        try:
            n = int(ch)
        except (ValueError, TypeError):
            continue
        if not isinstance(md, str) or not md.strip():
            continue
        _push(n, detect_evasions(md, n, state))

    return acc
