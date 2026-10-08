"""稳评工作程序时间 —— 把用户在前端填的日期**确定性**落到正文里。

🔴 为什么需要这个模块
────────────────────────────────────────────────────────────────────────
实测（会话 a5b889e9…）：用户在弹窗里填了 17 个日期，成稿里只出现 7 个，
**10 个一次都没出现**（现场走访 9/5、风险预测 9/7–8、报告编制 9/9–10、
专家评审 9/11–12、制定方案 9/2、方案审批 9/13…）。

第7章「工作计划」的 (1)~(5) 分项在参照件里**本来是带日期的**：

    2026 年7 月30 日—2026 年7 月31 日，稳评工作小组走访调查现场…

但那是**参照件项目的日期**。改写时模型把它们剔除了 —— 剔除是对的（留着就是
编造），可**没有把用户填的日期补回去**，于是正文里时间全丢了，只剩
「公示期结束后，稳评工作小组走访调查…」这种没有时间的句子。

🔴 为什么在正文层做确定性替换，而不是再写进提示词
    提示词锁不住 —— 上面那次跑的就是「提示词里给了事实卡」的版本。
    日期是用户给的事实，必须逐一落位、可核对、可回归。
    `data_usage_map.FIELD_USAGE` 是「字段 → 归属章节」的**声明**，本模块是它的
    **执行器**（目前只覆盖第7章工作计划这一段 —— 恰是声明缺失最集中的地方）。

🔴 边界（与全项目一致）
    · 只用 `filled_data` 里的值，**不编造**；
    · 字段缺失 → 原句一字不动，另记一条 gap，绝不写「【待补充】」；
    · 幂等 —— 已经带日期的句子不再改（重复生成不会叠加日期）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 正文里已经出现过的日期形态（判断「这句已经有时间了」）
_HAS_DATE_RE = re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月\s*\d{1,2}\s*日')

# 分项定位关键词 → (开始字段, 结束字段)
# 🔴 关键词取「分项标题里的特征词」，与参照件 7.6 工作计划的分项一一对应：
#    （1）公告公示 （2）现场走访调查 （3）风险因素预测… （4）报告编制与审核 （5）报告评审及报送
#    ⚠️ 每个分项给**一组**关键词：模型不会逐字照抄参照件的分项名
#       （实测它写「6.风险因素分析」，而参照件叫「风险因素预测…」，只留一个词就漏落）。
_SCHEDULE: Tuple[Tuple[Tuple[str, ...], str, Optional[str]], ...] = (
    (("公告公示", "公示"),                     "publicity_start_date", "publicity_end_date"),
    (("现场走访调查", "走访调查", "现场调查"),   "survey_date",          "symposium_date"),
    (("风险因素", "风险分析", "防范措施制定"),   "risk_predict_date",    "risk_predict_end_date"),
    (("报告编制", "编制评估报告"),              "report_draft_date",    "report_draft_end_date"),
    (("报告评审", "评审及报送", "报送"),         "review_date",          "review_end_date"),
)

# 「2.制定评估方案」这类小标题本身就要带日期（参照件：「2026 年7 月26 日，我公司…」）
_HEAD_SCHEDULE: Tuple[Tuple[str, str], ...] = (
    ("制定评估方案", "plan_date"),
    # 方案审核：定稿/审批日期。正文里没有「＿年＿月＿日」占位（参照件才有），
    # 走这里落到分项内容开头；有占位时下面的 ③ 会另处理。
    ("方案审核", "plan_approve_date"),
)

# 分项标题行：`（2）现场走访调查` / `2.制定评估方案` / `**（3）风险因素预测…**`
_ITEM_RE = re.compile(r'^[\*\s]*(?:[（(]\s*\d+\s*[）)]|\d+\s*[.、])\s*(?P<name>[^\n*：:]{2,40})')

# 落款日期占位（第7章「3.方案审核」末尾）
_BLANK_DATE_RE = re.compile(r'^[_\s]*年[_\s]*月[_\s]*日[_\s]*$')


def _cn_date(value: Any) -> str:
    """`2026-09-05` → `2026年9月5日`；已是中文/无法解析则原样返回空串。"""
    s = str(value or "").strip()
    if not s:
        return ""
    m = re.match(r'^(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})', s)
    if not m:
        return ""
    return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日"


def format_span(start: Any, end: Any) -> str:
    """起止日期 → `2026年9月7日—2026年9月8日`；只有一头则只出那一头。"""
    a, b = _cn_date(start), _cn_date(end)
    if a and b:
        return a if a == b else f"{a}—{b}"
    return a or b


def _inline_body(line: str, m) -> bool:
    """分项标题行里是否**自带正文**（形如 `5.现场走访调查：评估工作组赴…`）。

    🔴 实测：模型写第7章工作计划时，标题与正文**在同一行**用「：」分隔。
       落位器旧写法一律去**下一行**找正文 —— 而下一行就是**下一条分项标题**，
       日期于是被插到了「6.风险因素分析」前面（业主看到的错位句）。
    """
    rest = (line[m.end():] or "").strip().strip("*").strip()
    return bool(rest.strip("：: 　"))


def _insert_after_title(line: str, m, prefix: str) -> str:
    """把日期插到标题行的**内容开头**（有「：」就插在它之后）。"""
    head, rest = line[:m.end()], line[m.end():]
    mm = re.match(r'^(\s*[：:]\s*)', rest)
    if mm:
        return f"{head}{mm.group(1)}{prefix}{rest[mm.end():]}"
    return f"{head}　{prefix}{rest.strip()}"


def apply_to_chapter7(md: str, filled: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    """把「工作程序时间」落进第7章正文。

    Returns:
        (新 markdown, gaps)。gaps 里是**用户填了但这次没落位**的字段，
        供审核环节提示，不写进正文。
    """
    if not md:
        return md, []
    filled = filled or {}
    gaps: List[Dict[str, str]] = []
    lines = md.split("\n")
    out: List[str] = []
    used: set = set()

    i = 0
    while i < len(lines):
        ln = lines[i]
        out.append(ln)
        m = _ITEM_RE.match(ln)          # 直接在原行上匹配（正则会吃掉前导空白/星号）
        if not m:
            i += 1
            continue
        name = (m.group("name") or "").strip()

        # ① 分项标题（（1）公告公示 / 5.现场走访调查 …）：日期补在正文句首
        hit = next((x for x in _SCHEDULE if any(k in name for k in x[0])), None)
        if hit:
            kws, f_start, f_end = hit
            kw = kws[0]
            span = format_span(filled.get(f_start), filled.get(f_end))
            for _f in (f_start, f_end):
                if _f and not str(filled.get(_f) or "").strip():
                    gaps.append({"field": _f, "reason": "work_schedule_missing",
                                 "severity": "warn", "detail": f"「{kw}」日期未落位"})
            if span:
                used.add(f_start)
                if f_end:
                    used.add(f_end)
                # 🔴 同行自带正文 → **就地**插到标题冒号之后（不动别人的行）
                if _inline_body(ln, m):
                    # 🔴 幂等：这一行已经有日期了就不再插（否则每跑一遍叠一个日期）
                    if not _HAS_DATE_RE.search(ln[m.end():]):
                        out[-1] = _insert_after_title(ln, m, span + "，")
                    i += 1
                    continue
                # 否则按老办法：往后找第一条非空正文行
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    out.append(lines[j])
                    j += 1
                if j < len(lines):
                    body = lines[j]
                    if _HAS_DATE_RE.search(body):
                        out.append(body)                      # 已有日期 → 不动（幂等）
                    else:
                        out.append(_prepend_date(body, span + "，"))
                    i = j + 1
                    continue
            i += 1
            continue

        # ② 小标题（2.制定评估方案）：日期补在正文句首
        hit2 = next((x for x in _HEAD_SCHEDULE if x[0] in name), None)
        if hit2:
            kw, f = hit2
            d = _cn_date(filled.get(f))
            if d:
                used.add(f)
                if _inline_body(ln, m):
                    if not _HAS_DATE_RE.search(ln[m.end():]):
                        out[-1] = _insert_after_title(ln, m, d + "，")
                    i += 1
                    continue
                # 🔴 循环里不能「先 append 空行、再决定要不要改正文」——
                #    正文已有日期时若不推进 `i`，下一轮会把同一批空行**再 append 一遍**
                #    （实测：第二次跑多出一个空行，破坏幂等）。所以先定位、后统一写出。
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                out.extend(lines[i + 1:j])
                if j < len(lines):
                    body = lines[j]
                    if not _HAS_DATE_RE.search(body):
                        out.append(_prepend_date(body, d + "，"))
                    else:
                        out.append(body)
                    i = j + 1
                    continue
                i = j
                continue
            if f:
                gaps.append({"field": f, "reason": "work_schedule_missing",
                             "severity": "warn", "detail": f"「{kw}」日期未落位"})
            i += 1
            continue

        i += 1

    # ③ 落款「年月日」→ 方案审批日期（用户填了才替，没填就留着让界面弹窗补）
    d_approve = _cn_date(filled.get("plan_approve_date"))
    if d_approve:
        for k, ln in enumerate(out):
            if _BLANK_DATE_RE.match(ln.strip()):
                out[k] = d_approve
                used.add("plan_approve_date")
                break

    text = "\n".join(out)
    # 声明里说「用于第7章」但这次既没用上、用户也没填 → 不报；填了却没用上 → 报
    _all_items = [(kws[0], f_start, f_end) for kws, f_start, f_end in _SCHEDULE]
    _all_items += [(kw, f, None) for kw, f in _HEAD_SCHEDULE]
    _all_items += [("方案审核落款", "plan_approve_date", None)]
    for kw, f_start, f_end in _all_items:
        for f in (f_start, f_end):
            if not f or f in used:
                continue
            if str(filled.get(f) or "").strip() and not any(g["field"] == f for g in gaps):
                gaps.append({"field": f, "reason": "work_schedule_unplaced",
                             "severity": "warn", "detail": f"已填但未在正文落位（{kw}）"})
    return text, gaps


def _prepend_date(body: str, prefix: str) -> str:
    """把日期前缀插到句子最前 —— 保住原有 markdown 缩进/引用符。"""
    m = re.match(r'^(\s*(?:[-*>]\s+)?)(.*)$', body, re.S)
    if not m:
        return prefix + body
    lead, rest = m.group(1), m.group(2)
    if not rest.strip():
        return body
    return f"{lead}{prefix}{rest}"


# ═══════════════════════════════════════════════════════════════
# 正文里的**空日期占位**（`编写时间：2026年　　月　　日`）
# ═══════════════════════════════════════════════════════════════
# 🔴 模型会在正文里留这种空档（第4章「（五）参与人员签字」末尾固定一处）。
#    它是**用户填了却没落位**的典型：`report_month` 在事实卡里、字段也有值，
#    正文却还是「2026年　　月　　日」。
#    判据要容忍全角空格与已有年份：`2026年` + 空白 + `月` + 空白 + `日`。
_BLANK_MONTH_DAY_RE = re.compile(
    r'(编写时间[：:]\s*)(\d{4})\s*年[\s　]*月[\s　]*日')


def apply_blank_dates(md: str, filled: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    """填掉正文里的**空日期占位**（如第4章「编写时间：2026年　　月　　日」）。

    只用 `filled_data` 里的值；取不到就**原样留着**并记一条 gap（绝不写当前日期糊弄）。
    """
    if not md:
        return md, []
    filled = filled or {}
    gaps: List[Dict[str, str]] = []

    # `report_month` 形如 `2026-09` / `2026-9`，也容忍 `2026年9月`
    _rm = re.match(r'(\d{4})\s*[-/年]\s*(\d{1,2})',
                   str(filled.get("report_month") or "").strip())
    if not _rm:
        if _BLANK_MONTH_DAY_RE.search(md):
            gaps.append({"field": "report_month", "reason": "work_schedule_missing",
                         "severity": "warn", "detail": "「报告编写年月」未填写，正文「编写时间」留空"})
        return md, gaps

    month_txt = f"{int(_rm.group(1))}年{int(_rm.group(2))}月"
    out, n = _BLANK_MONTH_DAY_RE.subn(lambda m: m.group(1) + month_txt, md)
    if n:
        logger.info(f"[工作程序时间] 「编写时间」已填 {month_txt}（{n} 处）")
    if not n and _BLANK_MONTH_DAY_RE.search(md):
        gaps.append({"field": "report_month", "reason": "work_schedule_unplaced",
                     "severity": "warn", "detail": "已填但未在正文落位（报告编写年月）"})
    return out, gaps
