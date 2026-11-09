"""把上传材料里的**本项目原文**按章摘出来，注入对应章节的提示词。

🔴 为什么需要它
────────────────────────────────────────────────────────────────────────
实测：用户上传的那份 docx（**本项目报告**）里有 23,412 字正文，全是本项目的
具体事实 —— 而系统只把它当「材料」抽了几个字段，**正文一个字没用**：

    四至      「西邻长深高速，北至维三路，南至致富线，东至经14路」
    地上物    「地块上无建筑物，涉及农村道路及青苗附着物（当季种植水稻）」
    周边      「地块周边主要为农田和厂房」「不占用永久基本农田，不涉及生态红线」
    安置      「和三河镇八里社区负责人沟通了解：按"征谁补谁"原则…村民对此无异议」

于是第6章只能写「位于…范围内，位置详见附图」这种空话 ——
业主：「生成出来的内容还是很虚，不像专家写的」。

🔴 与 RAG 的区别
    RAG（`kb_*` 知识库）里是**别的项目**的范文，只教「怎么写」；
    这里摘的是**用户上传的本项目材料**，是本项目的**事实来源**，可以直接引用。
    两者不能混 —— 混了就是串项目。

🔴 章号对不上怎么办
    材料是**人工成品**，章号与系统不同（材料 12 章、系统 14 章）。
    所以按**章标题关键词**匹配，不按章号。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 材料里的章标题：`第四章 决策事项基本情况` / `第七章 决策事项合法性、合理性、…`
_HEAD_RE = re.compile(r'^第[一二三四五六七八九十]+章\s*[、．.]?\s*(.+)$')

# 系统章号 ← 材料章标题里可能出现的关键词
# 🔴 一个材料块可以命中多章（「各利益相关者对决策事项的反映」同时供第8、9章用）。
_CH_ALIASES: Dict[int, tuple] = {
    1:  ("编制说明",),
    2:  ("编制依据",),
    4:  ("稳评工作报告",),
    5:  ("突发性事件", "应急预案", "不稳定因素处置"),
    6:  ("决策事项基本情况", "项目基本情况"),
    7:  ("稳评工作方案",),
    8:  ("各利益相关者对决策事项的反映", "风险调查"),
    9:  ("各利益相关者对决策事项的反映", "各方意见", "利益相关者诉求"),
    10: ("合法性、合理性、可行性、可控性", "决策事项合法性"),
    11: ("分析预测风险源", "风险预测"),
    12: ("确定风险等级",),
    13: ("提出防范和化解风险", "工作建议"),
}

# 每章最多带多少字 —— 材料原文只是**补充事实**，不能把 prompt 撑爆
_EXCERPT_LIMIT = 2400

# 复垦：用户上传的常常是「施工组织设计 / 可研 / 概况」这类**本项目资料**，
# 不是按「第X章」编排的报告，而且业主明确要求「部分内容可以直接复制到正文」。
# 所以复垦按**材料里的小节标题关键词**匹配，限额放宽（原文引用需要篇幅）。
_RECLAMATION_ALIASES: Dict[int, tuple] = {
    1: ("项目概况", "工程概况", "概况", "项目背景", "编制背景"),
    2: ("编制依据", "编制目的", "编制原则", "依据"),
    3: ("项目概况", "工程概况", "概况", "建设单位", "工程规模", "实施步骤",
        "建设内容", "项目简介", "井场", "井组", "井场情况", "可研"),
    4: ("土地利用", "地类", "损毁", "勘测", "勘界", "现状"),
    5: ("复垦", "剥离", "覆土", "覆土回覆", "培肥", "灌排", "措施"),
    6: ("工程量", "施工组织", "工程设计", "施工工艺"),
    7: ("投资", "估算", "预算", "费用", "单价"),
    8: ("工期", "进度", "计划", "安排"),
    9: ("效益", "经济", "社会", "评价"),
    10: ("保障", "管护", "监测", "组织", "验收"),
}
_RECLAMATION_EXCERPT_LIMIT = 8000
# 第3章兜底：文件名像「概况/施工报告/可研/初设」的材料，整份都是项目概况素材
_RECLAMATION_OVERVIEW_HINTS = ("概况", "施工报告", "可研", "可行性", "初步设计", "钻井")

_ANY_HEAD_RE = re.compile(
    r'^\s*(?:第[一二三四五六七八九十]+[章节]|'
    r'[一二三四五六七八九十]+[、．.]|'
    r'（[一二三四五六七八九十]+）|'
    r'\d+(?:\.\d+){1,3})\s*[、．.]?\s*\S')


def _looks_like_any_heading(line: str) -> bool:
    """复垦材料的小节标题：一、/（一）/1.3.1/第X章 —— 短行、无句读。"""
    s = line.strip()
    if not s or len(s) > 40 or s.endswith(("。", "；", "，", ",", ";")):
        return False
    if any(c in s for c in "，。；"):     # 正文句（「3.26 公顷，共 8 个地块」）不是标题
        return False
    return bool(_ANY_HEAD_RE.match(s))


def split_material_blocks(text: str) -> List[tuple]:
    """按任意级别标题切 `[(标题, 正文)]`；完全没有标题时整份作为一块。"""
    blocks: List[tuple] = []
    cur, buf = None, []
    for line in str(text or "").split("\n"):
        s = line.strip()
        if _looks_like_any_heading(s):
            if cur is not None:
                blocks.append((cur, "\n".join(buf).strip()))
            cur, buf = s, []
            continue
        if cur is None:
            cur = "全文"
        if s:
            buf.append(s)
    if cur is not None:
        blocks.append((cur, "\n".join(buf).strip()))
    return blocks


def split_material_chapters(text: str) -> Dict[str, List[str]]:
    """把材料正文按「第X章 标题」切块。返回 `{章标题: [行]}`。"""
    blocks: Dict[str, List[str]] = {}
    cur, buf = None, []
    for line in str(text or "").split("\n"):
        m = _HEAD_RE.match(line.strip())
        if m:
            if cur:
                blocks[cur] = buf
            cur, buf = m.group(1).strip(), []
            continue
        if cur is not None:
            buf.append(line)
    if cur:
        blocks[cur] = buf
    return blocks


def _domain_of(state: Dict[str, Any]) -> str:
    try:
        from app.domains import domain_of_state
        return domain_of_state(state or {}) or "stability"
    except Exception:
        return "stability"


def _reclamation_excerpt(chapter_no: int, state: Dict[str, Any],
                         limit: int) -> str:
    """复垦：按材料小节标题取本项目原文（施工组织设计/可研/概况可直接引用）。"""
    aliases = _RECLAMATION_ALIASES.get(int(chapter_no))
    if not aliases:
        return ""
    picked: List[str] = []
    for m in ((state or {}).get("_project_materials") or []):
        if not isinstance(m, dict):
            continue
        txt = str(m.get("text_content") or "")
        if not txt.strip():
            continue
        name = str(m.get("source_name") or m.get("title") or "")
        hit = False
        for title, body in split_material_blocks(txt):
            # 只有标题、没有正文的块不要
            if len(body) < 30:
                continue
            if any(a in title for a in aliases):
                picked.append(f"【{title}】\n{body}")
                hit = True
        if hit:
            continue
        # 兜底：第3章「项目概况」—— 文件名就是概况/施工报告/可研 的材料整份可用
        if int(chapter_no) == 3 and any(k in name for k in _RECLAMATION_OVERVIEW_HINTS):
            body = txt.strip()
            if len(body) >= 20:
                picked.append(f"【{name or '项目材料'}】\n{body}")
    if not picked:
        return ""
    out = "\n\n".join(picked)
    if len(out) > limit:
        out = out[:limit] + "…"
    return out


def excerpt_for_chapter(chapter_no: int, state: Dict[str, Any],
                        limit: Optional[int] = None,
                        domain: Optional[str] = None) -> str:
    """摘出**上传材料里与本章相关**的原文段落；没有返回空串。

    只扫 `state["_project_materials"]`（用户上传并解析过的材料）——
    绝不碰 `data/reference_reports/`（那是别的项目的参照件）。
    """
    dom = domain or _domain_of(state)
    if dom == "reclamation":
        return _reclamation_excerpt(
            chapter_no, state,
            int(limit or _RECLAMATION_EXCERPT_LIMIT))
    aliases = _CH_ALIASES.get(int(chapter_no))
    if not aliases:
        return ""
    mats = (state or {}).get("_project_materials") or []
    picked: List[str] = []
    for m in mats:
        if not isinstance(m, dict):
            continue
        txt = str(m.get("text_content") or "")
        if not txt.strip():
            continue
        for title, lines in split_material_chapters(txt).items():
            if not any(a in title for a in aliases):
                continue
            body = "\n".join(x for x in lines if x.strip())
            if len(body) < 30:          # 空壳块（只有标题没有正文）
                continue
            picked.append(f"【{title}】\n{body}")
    if not picked:
        return ""
    out = "\n\n".join(picked)
    if len(out) > (limit or _EXCERPT_LIMIT):
        out = out[:(limit or _EXCERPT_LIMIT)] + "…"
    return out


def inject_excerpt(chapter_no: int, state: Dict[str, Any], prompt: str,
                   limit: Optional[int] = None) -> str:
    """把材料摘录**追加**到 prompt 末尾。取不到就原样返回。"""
    try:
        dom = _domain_of(state)
        ex = excerpt_for_chapter(chapter_no, state, limit, domain=dom)
        if not ex:
            return prompt
        if dom == "reclamation":
            # 业主 2026-11-09：「部分内容是可以直接复制粘贴到正文内容上的」。
            return prompt + (
                "\n\n## 📄 本项目上传材料的相关章节（**本项目事实，可整段直接引用**）\n"
                "下面是从用户上传的材料里摘出的**本项目原文**。写本章时：\n"
                "1) 建设单位概况、工程规模、实施步骤、井场/临时用地情况、参建单位等，"
                "**可以直接复制原文**，数字、名称、单位一字不改；\n"
                "2) 其余内容按本章行文重组，只取事实，不要编造材料里没有的数字；\n"
                "3) 不要用「详见附件」「以现状调查为准」把具体事实盖过去。\n\n"
                + ex
            )
        return prompt + (
            "\n\n## 📄 本项目上传材料的相关章节（**本项目的事实**，可直接引用）\n"
            "下面是从你上传的材料里摘出来的**本项目原文** —— 其中的四至、地上物、"
            "周边情况、沟通结论等都是**本项目已核实的事实**，写本章时**要具体用上**，"
            "不要再用「详见附图」「以现状调查为准」这类话把它们盖过去。\n"
            "⚠️ 但也不要整段照抄 —— 按本章行文重组，只取其中的事实。\n\n"
            + ex
        )
    except Exception as e:
        logger.warning(f"[材料摘录] 第{chapter_no}章注入失败: {e}")
        return prompt
