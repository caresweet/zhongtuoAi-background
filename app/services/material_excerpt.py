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
from typing import Any, Dict, List

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


def excerpt_for_chapter(chapter_no: int, state: Dict[str, Any],
                        limit: int = _EXCERPT_LIMIT) -> str:
    """摘出**上传材料里与本章相关**的原文段落；没有返回空串。

    只扫 `state["_project_materials"]`（用户上传并解析过的材料）——
    绝不碰 `data/reference_reports/`（那是别的项目的参照件）。
    """
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
    if len(out) > limit:
        out = out[:limit] + "…"
    return out


def inject_excerpt(chapter_no: int, state: Dict[str, Any], prompt: str,
                   limit: int = _EXCERPT_LIMIT) -> str:
    """把材料摘录**追加**到 prompt 末尾。取不到就原样返回。"""
    try:
        ex = excerpt_for_chapter(chapter_no, state, limit)
        if not ex:
            return prompt
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
