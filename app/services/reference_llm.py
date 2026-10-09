"""照搬章「模型改写」—— 参照件出骨架，正文交给模型写。

业主口径（2026-09-16）：
    「你得让模型能去写部分可以编造的内容」→ 范围定为
    **「除项目事实外全交给模型」**。

于是第 1/2/4/5/7 章不再是「逐字照搬」，而是：
    参照件只保留 ① 章节骨架（二级/三级标题）② 插图 / 系统表标记 ③ 法规白名单；
    正文行文由模型结合 RAG 与事实卡重写成本项目自己的稿子。

🔴 **模型绝对不许编的**（业主逐项确认）：
    ① 数字 —— 面积/亩数/投资额/户数/问卷份数/得分/风险等级；
    ② 人名 / 职务 / 电话；
    ③ 文号 / 日期；
    ④ 法规名称 —— 只准引用白名单里的。
前两类只能取自事实卡与人员库；文号日期只能取自事实卡；法规只准引用白名单。

防线分两层：
    · **提示词**（第一层，见 `PROSE_SYSTEM`）—— 把禁令与可用素材一起给到模型；
    · **落地校验**（第二层，`guard_fabrications`）—— 硬剥手机号与非白名单文号，
      其余可疑项记成 issue 交给审核环节，绝不悄悄放行。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 第一层：提示词
# ═══════════════════════════════════════════════════════════════

PROSE_SYSTEM = """你是社会稳定风险评估报告的撰稿人。下面给你「本章骨架」「项目事实卡」
「可引用法规清单」「知识库素材」，以及一份「参照件范文」。

请按骨架写出本章正文，产出**本项目自己**的章节稿。

【三档处理，逐类对号入座】

▍第一档 · 原样照搬，一个字都不许动
  法规、政策、标准的**名称与条文引用** —— 骨架里已经写好的这类段落
  （如「依据《中华人民共和国土地管理法》…制定本预案」「DB32/T4013-2021」）。
  这些本来就是全国/全省通用，改反而改错。**逐字保留**。

▍第二档 · 必须改，但只能用事实卡里的事实
  凡是与**本项目**有关的：单位名称、地名、村组、文号、日期、面积、户数、
  补偿标准、得分、风险等级 —— 一律换成「项目事实卡」里的值。
  事实卡里没有的，写成**不含该信息**的通用表述，绝不许自己补一个。

▍第三档 · 仿照范文自己写，但必须符合事实逻辑
  通用行文（指导思想、工作原则、职责分工、处置流程、保障措施等）——
  照着范文的结构、篇幅、语气自己组织语言。
  🔴 **必须与项目类型对得上**：本项目是**土地征收**，矛盾诱因就写补偿标准、
  补偿费用分配、土地换社保、安置等征收场景的事，不许出现施工噪声、风机之类
  别的行业的情节。宁可写得平实，也不许编造不合逻辑的情节。

【绝对不许编造】
1. 数字：面积、亩数、投资额、户数、问卷份数、支持率、得分、风险等级等。
2. 人名 / 职务 / 电话：只能用事实卡与人员库里有的姓名；泛称写「分管领导」
   「相关科室负责人」「项目所在地有关负责人」。
3. 文号 / 日期：只能用事实卡里的。
4. 法规名称：只能引用骨架或「可引用法规清单」里已有的，绝不新增、绝不臆造。

【硬性格式要求】
- 所有以 `#` 开头的标题行、所有 `![...](...)` 图片行、所有 `[SYS_TABLE:...]` 标记行
  **逐字保留，不许改、不许增删**。
- 不要留空、不要写「【待补充】」、不要留 {{占位符}}。
- 只输出正文 markdown 本身，不要任何解释、不要 ``` 代码围栏。"""

# 第一次改写被模型「原样吐回」时的加强提示（追加到 system 末尾）
_RETRY_HINT = """

🔴 上一次你**原样返回了参照件正文**，这不算完成。参照件是**别的项目**的成稿，
   其中的人名、单位名、地名、日期、文号、数字都属于别的项目，出现在本项目报告里就是错的。
   这一遍请改写**第二档与第三档**的内容：结构、篇幅、语气照着范文，但语句要是新的。
   注意：**第一档的法规条文引用照旧逐字保留，不要动**。"""


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r'^```(?:markdown|md)?\s*\n(.*?)\n```\s*$', t, flags=re.S)
    return (m.group(1) if m else t).strip()


# ═══════════════════════════════════════════════════════════════
# 提示词装配
# ═══════════════════════════════════════════════════════════════

def build_rewrite_prompt(chapter_no: int, title: str, skeleton: str, filled: Dict[str, Any],
                         rag_text: str = "", reference_text: str = "",
                         state: Dict[str, Any] = None) -> str:
    """装配改写提示词。

    🔴 「骨架」只给**标题 / 图片 / 系统表标记**，正文一律不给 —— 否则模型会
       原样把参照件正文吐回来（实测：给了整段正文时输出与输入逐字相同，
       改写变成空转）。参照件正文改从「范文」区进，并明确要求**不许照抄原句**。
    """
    parts: List[str] = [f"# 本章：第{chapter_no}章 {title}", ""]
    try:
        from app.services.master_orchestrator import (
            build_project_card, build_regulation_whitelist,
        )
        from app.domains import domain_of_state
        wl = build_regulation_whitelist(filled)
        if wl:
            parts.append(wl)
        card = build_project_card(filled, domain_of_state(state))
        if card:
            parts.append(card)
    except Exception as e:
        logger.warning(f"[照搬改写] 事实卡/法规清单装配失败: {e}")

    if rag_text:
        parts.append("## 📚 知识库素材（可参考的行文与做法，不要照抄其项目数字）\n" + rag_text[:6000])

    skel = _skeleton_only(skeleton) or skeleton
    parts.append("## 📐 本章骨架（**必须逐字保留**所有标题 / 图片 / 系统表标记；"
                 "每个标题下面的正文由你自己写）\n```markdown\n" + skel + "\n```")

    if reference_text.strip():
        parts.append("## 📖 参照件范文（**别的项目**的成稿）\n"
                     "只学它的结构、篇幅与语气。**禁止原样返回其中的句子** ——"
                     "其中的人名、单位名、地名、日期、文号、数字都属于别的项目，"
                     "一律不许出现在你的成稿里。\n\n" + reference_text[:6000])

    parts.append("## ✍️ 任务\n"
                 "按上面的骨架，为**本项目**写出每一节的正文，补回所有标题/图片/系统表标记。"
                 "篇幅与范文相当。只输出 markdown 正文，不要解释、不要代码围栏。")
    # 🔴 **字数上限**（业主：「篇幅也尽量控制下在 25000 左右就行」）。
    #    照搬章的改写走的是**这条**路，不是 `build_chapter_prompt` ——
    #    实测只把约束加在那边时，第5章 5628 字（目标 4000）、第7章 2956（目标 2400），
    #    **一点没压下来**。两条路共用 `target_words()` 这一个来源。
    try:
        from app.services.master_orchestrator import target_words
        _lo, _hi = target_words(chapter_no)
        parts.append(
            f"## 篇幅\n🔴 本章 **{_lo}–{_hi} 字**（同类模板报告本章就是这个篇幅）。\n"
            f"- **超过 {_hi} 字要删**：同一件事不换说法重复说、不堆「依据《XX》」式引经据典、\n"
            f"  不写「有利于…」「为…提供基础/条件」这类没有具体事实的收尾句。\n"
            f"- 参照件的套话（如应急预案里的「要切实加强…」）**要清掉**，不要原样搬过来。\n"
            f"- 写完自己数一下，超了就删到 {_hi} 字以内。"
        )
    except Exception as _we:
        logger.warning(f"[照搬改写] 字数约束注入失败: {_we}")

    # 🔴 上传材料里与本章相关的原文（四至/地上物/周边/沟通结论…）—— 本项目的事实
    if state:
        try:
            from app.services.material_excerpt import inject_excerpt
            return inject_excerpt(chapter_no, state, "\n\n".join(parts))
        except Exception as _me:
            logger.warning(f"[材料摘录] 第{chapter_no}章注入失败: {_me}")

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 骨架保全：标题 / 图片 / 系统表标记一个都不能丢
# ═══════════════════════════════════════════════════════════════

_ANCHOR = re.compile(r'^(?:#{1,6}\s*\S.*|!\[[^\]]*\]\([^)]*\)|\[SYS_[A-Z_]+(?::[^\]]+)?\])$')


_CITE_ANY = re.compile(r'《[^》]{2,60}》')
# 判定「法规条文段」时忽略的字符（标点、编号、标准号里的字母数字）
_LAW_NOISE = re.compile(r'[《》、，,。；;：:!！?？\-—·\s（）()［］\[\]{}“”‘’"\'0-9A-Za-z/.]')


def _is_law_paragraph(text: str) -> bool:
    """是不是「法规/政策/标准」的**名称或条文引用**段 —— 第一档，逐字照搬不许改。

    判据：① 出现《》引用；② 去掉引用与标点后剩余实词很少（即这段基本就是在列法规名）。
    长论述段里偶带一个引用不算 —— 那种属第三档，要仿照重写。
    """
    t = (text or "").strip()
    if not t or len(t) > 300:
        return False
    if not _CITE_ANY.search(t):
        return False
    return len(_LAW_NOISE.sub('', _CITE_ANY.sub('', t))) <= 30


def _protected(md: str) -> List[str]:
    """第一档段落（法规条文）—— 骨架里原样带着进提示词，成稿里必须逐字还在。"""
    return [l.strip() for l in (md or "").split("\n") if _is_law_paragraph(l)]


def _anchors(md: str) -> List[str]:
    return [l.strip() for l in (md or "").split("\n") if _ANCHOR.match(l.strip())]


def _skeleton_only(md: str) -> str:
    """骨架 = 标题 / 图片 / 系统表标记 + **第一档法规条文段**（逐字带上）。

    🔴 正文论述一律抽掉 —— 抽干净模型才会自己写；不抽它会原样吐回来。
       但法规条文段必须留着：那是要**逐字照搬**的第一档，模型改写反而改错。
    """
    out: List[str] = []
    for line in (md or "").split("\n"):
        s = line.strip()
        if _ANCHOR.match(s) or _is_law_paragraph(s):
            if out and out[-1] != "":
                out.append("")
            out.append(s)
    return "\n".join(out).strip()


def _copy_ratio(new_md: str, skeleton: str) -> float:
    """新稿里**原样照抄骨架的句子**占比。→1 说明模型基本没改写。

    🔴 不能用 `SequenceMatcher.quick_ratio()`：它按字符计数估算，两篇**同主题、
    同篇幅**的中文（正是本场景）恒在 0.95 上下，实测把一份确实改写过的稿子
    误判成「原样返回」。按**句子**判才咬得准。

    🔴 第一档的法规条文段**本来就该照抄**，必须排除，否则第2章这种法规章
    永远是 100%「照抄」，永远触发重试。
    """
    ref = _cmp_key(skeleton)
    law = "".join(_cmp_key(p) for p in _protected(skeleton))
    sents = [s for s in re.split(r'[。；\n]', new_md or "") if len(s.strip()) >= 12]
    if not sents:
        return 0.0
    counted = [s for s in sents if _cmp_key(s) not in law]
    if not counted:
        return 0.0
    return sum(1 for s in counted if _cmp_key(s) in ref) / len(counted)


def structure_ok(new_md: str, skeleton: str) -> Tuple[bool, str]:
    """新稿必须 ① 按序含全部标题/图片/系统表标记，② 第一档法规条文段逐字还在。

    模型偶尔会吞掉或改动标题行、把法规条文「顺手润色」了 —— 前者让章节层级错乱、
    图丢失，后者把全国通用的条文改错。宁可判定失败回退骨架，也不放出损坏的章节。
    """
    want, got = _anchors(skeleton), _anchors(new_md)
    if want != got:
        missing = [a for a in want if a not in got]
        if missing:
            return False, f"缺少 {len(missing)} 个标题/图/表标记：{missing[:3]}"
        extra = [a for a in got if a not in want]
        if extra:
            return False, f"多出 {len(extra)} 个标题/图/表标记：{extra[:3]}"
        return False, "标题/图/表标记顺序被打乱"

    key = _cmp_key(new_md)
    lost = [p for p in _protected(skeleton) if _cmp_key(p) not in key]
    if lost:
        return False, f"第一档法规条文段被改动 {len(lost)} 处：{lost[0][:40]}"
    return True, ""


# ═══════════════════════════════════════════════════════════════
# 第二层：落地校验（不许编的东西）
# ═══════════════════════════════════════════════════════════════

_MOBILE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
# 文号内核：`〔2026〕7号` / `[2026]第7号`。
# 🔴 只匹配内核、不把前面的机关代字吃进正则 —— 旧写法 `[一-龥]{0,8}[〔…` 是贪婪的，
#    会把「另参照洪拟征告〔2026〕7号」整串当成文号去比对事实卡，比对必然失败，
#    于是把事实卡里明明有的文号也误剥了。
_DOC_REF_CORE = re.compile(r'[〔\[［]\s*(?:19|20)\d{2}\s*[〕\]］]\s*第?\s*\d+\s*号')
_DATE = re.compile(r'(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?')
_CITE = re.compile(r'《([^》]{2,60})》')


def _facts_text(filled: Dict[str, Any]) -> str:
    return " ".join(str(v) for v in (filled or {}).values()
                    if isinstance(v, (str, int, float)) and not str(v).startswith("_"))


def _compact(s: str) -> str:
    return re.sub(r'\s+', '', s or "")


# 比对用归一化：连标点一起抹掉，避免模型把「，」改成「、」就被判成改写
_CMP_NOISE = re.compile(r'[\s，。；：、（）()《》〈〉“”‘’"\'\[\]【】\-—·,.;:!?！？]')


def _cmp_key(s: str) -> str:
    return _CMP_NOISE.sub('', s or "")


def _allowed_regulations(filled: Dict[str, Any], skeleton: str) -> set:
    """可引用的法规名 = 法规白名单 ∪ 骨架里已出现的（骨架已按项目类型过滤过）。"""
    names = set(_CITE.findall(skeleton or ""))
    try:
        from app.services.master_orchestrator import build_regulation_whitelist
        names |= set(_CITE.findall(build_regulation_whitelist(filled) or ""))
    except Exception:
        pass
    return names


def guard_fabrications(md: str, filled: Dict[str, Any], skeleton: str = "") -> Tuple[str, List[dict]]:
    """扫模型新稿里的「编造痕迹」。

    硬动作（直接改文本）：剥掉手机号、非事实卡文号。
    软动作（记 issue 交审核）：非白名单法规、非事实卡日期、大额数字与百分数。
    """
    filled = filled or {}
    facts = _facts_text(filled)
    issues: List[dict] = []
    out = md or ""

    # ① 手机号 —— 一律剥（隐私 + 禁区）
    hits = _MOBILE.findall(out)
    if hits:
        out = _MOBILE.sub("", out)
        out = re.sub(r'[ \t]{2,}', ' ', out)
        issues.append({"kind": "phone_stripped", "severity": "error",
                       "detail": f"剥除 {len(hits)} 个手机号"})

    # ② 文号 —— 事实卡**或参照件骨架**里核实过的保留，其余剥掉
    #    （第一档「地区政策法规可百分百 copy」——骨架里的文号属于可照搬范围，
    #      只有模型自己新添的才要剥。旧实现只认事实卡，把法规自带的规范文号
    #      也剥了，留下「《…意见》（）」这种空括号。）
    facts_c, skel_c = _compact(facts), _compact(skeleton)
    spans = []
    for m in _DOC_REF_CORE.finditer(out):
        core = _compact(m.group(0))
        if core in facts_c or (skel_c and core in skel_c):
            continue
        s, e = m.start(), m.end()
        if s > 0 and out[s - 1] in '（(' and e < len(out) and out[e] in '）)':
            # 整个括注就是文号（如「《…意见》（中办发〔2021〕11号）」）→ 连括号一起剥
            s -= 1; e += 1
        else:
            # 剥掉紧邻的机关代字（如「苏办发〔2021〕15号」的「苏办发」）
            pm = re.search(r'[一-龥A-Za-z]{2,8}$', out[:s])
            if pm:
                s = pm.start()
        spans.append((s, e, out[s:e].strip()))
    for s, e, raw in sorted(spans, reverse=True):
        out = out[:s] + out[e:]
        issues.append({"kind": "docref_stripped", "severity": "error",
                       "detail": f"剥除非事实卡文号「{raw}」"})
    # 兜底：剥完可能留下空括号/空书名号
    out = re.sub(r'[（(]\s*[）)]|《\s*》|［\s*］', '', out)
    out = re.sub(r'[ \t]{2,}', ' ', out)

    # ③ 法规名 —— 只能引用白名单
    allowed = _allowed_regulations(filled, skeleton)
    for name in set(_CITE.findall(out)):
        if allowed and name not in allowed:
            issues.append({"kind": "regulation_unlisted", "severity": "error",
                           "detail": f"引用了白名单外的法规「{name}」"})

    # ④ 日期 —— 事实卡里找不到同源的，交人工核
    for m in _DATE.finditer(out):
        d = m.group(0)
        if d.replace(" ", "") not in facts.replace(" ", ""):
            issues.append({"kind": "date_unverified", "severity": "warn",
                           "detail": f"日期「{d}」未见于事实卡"})

    # ⑤ 大额数字 / 百分数 —— 事实卡里找不到的，交人工核
    for m in re.finditer(r'(?<![\d.])(\d{3,}(?:\.\d+)?)(?![\d])|(\d{1,3}(?:\.\d+)?)\s*%', out):
        tok = m.group(0).strip()
        if tok not in facts and tok.rstrip('%') not in facts:
            issues.append({"kind": "number_unverified", "severity": "warn",
                           "detail": f"数字「{tok}」未见于事实卡"})
    if issues:
        logger.info(f"[照搬改写] 校验出 {len(issues)} 项可疑："
                    f"{[i['kind'] for i in issues][:8]}")
    return out, issues


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

async def rewrite_chapter_prose(chapter_no: int, title: str, skeleton: str,
                                filled: Dict[str, Any], llm_service,
                                reference_text: str = "", rag_text: str = "",
                                max_tokens: int = 8000,
                                state: Dict[str, Any] = None) -> Dict[str, Any]:
    """把骨架的正文改写成本项目自己的行文。返回 {markdown, issues, fallback}。

    🔴 任一环节出问题（无 LLM、超时、结构损坏）都**回退骨架** ——
       宁可交付一份照搬稿，也不交付一份结构错乱或编造数据的稿子。
    """
    res: Dict[str, Any] = {"markdown": skeleton, "issues": [], "fallback": ""}
    if not skeleton.strip():
        res["fallback"] = "empty_skeleton"
        return res
    if llm_service is None:
        res["fallback"] = "no_llm"
        return res

    prompt = build_rewrite_prompt(chapter_no, title, skeleton, filled,
                                  rag_text=rag_text, reference_text=reference_text,
                                  state=state)
    # 🔴🔴 **照搬章的改写也要带上文风与事实约束**。
    #     `FANWEN_STYLE_GUIDE` 原先只注入在 `chapter_base.act()` —— 而那条路只覆盖
    #     **LLM 直写章**（第4/6/8/9/10/11/12/13章）。照搬章（第2/5/7章）走的是
    #     `ReferenceChapterAgent`，它继承 `BaseAgent` 而**不是** `ChapterAgentBase`，
    #     于是改写的提示词里**一条文风/事实约束都没有**。
    #     实测后果：成品里 10 句「有判断无依据」的空泛句，**5 句集中在第5章**（照搬章）——
    #     那些「要切实加强沟通和交流，为科学决策提供可靠依据」是**白马湖原文的套话**，
    #     改写时没人告诉模型要清掉。LLM 直写章同期只 1–2 句。
    from app.agent.chapter_definitions import FANWEN_STYLE_GUIDE as _STYLE
    if _STYLE.strip() not in prompt:
        prompt = prompt + "\n\n" + _STYLE

    import asyncio
    new_md, last_err = "", ""
    for attempt in range(2):
        try:
            r = await asyncio.wait_for(
                llm_service.chat_with_reasoning(
                    messages=[{"role": "user", "content": prompt}],
                    system=PROSE_SYSTEM if attempt == 0 else PROSE_SYSTEM + _RETRY_HINT,
                    max_tokens=max_tokens,
                    temperature=0.4 + 0.2 * attempt,
                ),
                timeout=300.0,
            )
            new_md = _strip_fences(r.get("content", "") if isinstance(r, dict) else str(r))
        except Exception as e:
            logger.warning(f"[照搬改写] 第{chapter_no}章第{attempt+1}次改写失败: {e}")
            res["fallback"] = f"llm_error: {e}"
            return res

        if len(new_md.strip()) < 200:
            last_err = f"too_short({len(new_md.strip())})"
            continue
        if not structure_ok(new_md, skeleton)[0]:
            last_err = "structure: " + structure_ok(new_md, skeleton)[1]
            continue
        # 🔴 原样吐回骨架 = 改写空转，重试一次；再吐回就认了但记 issue
        ratio = _copy_ratio(new_md, skeleton)
        if ratio > 0.7 and attempt == 0:
            last_err = f"echoed({ratio:.2f})"
            logger.info(f"[照搬改写] 第{chapter_no}章模型原样返回骨架（{ratio:.2f}），重试")
            continue
        last_err = ""
        break

    if last_err:
        logger.warning(f"[照搬改写] 第{chapter_no}章改写未成（{last_err}），回退骨架")
        res["fallback"] = last_err
        return res

    # 两遍都原样吐回 → 认了，但记一笔，别让「改写空转」悄悄过去
    if _copy_ratio(new_md, skeleton) > 0.7:
        logger.warning(f"[照搬改写] 第{chapter_no}章模型两遍均未改写，成稿≈参照件骨架")
        res["issues"].append({"kind": "no_rewrite", "severity": "warn",
                              "detail": "模型未改写，成稿与参照件骨架基本一致"})

    new_md, issues = guard_fabrications(new_md, filled, skeleton)
    res["markdown"] = new_md
    res["issues"] = res["issues"] + issues
    return res


async def retrieve_rag_text(state: dict, chapter_no: int) -> str:
    """取本章 RAG 素材（失败不影响主流程）。"""
    try:
        from app.rag.rag_service import rag_service
        from app.agent.chapter_definitions import get_chapter_rag_query
        r = await rag_service.retrieve_for_chapter(
            chapter_number=chapter_no,
            session_id=(state or {}).get("session_id", ""),
            project_context=(state or {}).get("project_context", "") + " " + get_chapter_rag_query(chapter_no),
            n_results=20,
            domain=(state or {}).get("_domain") or (state or {}).get("_conversation_domain"),
        )
        chunks: List[str] = []
        for k in ("chapter_context", "example_context", "local_regulation_context"):
            v = r.get(k) if isinstance(r, dict) else None
            if v:
                chunks.append(str(v))
        return "\n\n".join(chunks)
    except Exception as e:
        logger.warning(f"[照搬改写] 第{chapter_no}章 RAG 取素材失败: {e}")
        return ""
