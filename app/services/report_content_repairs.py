# -*- coding: utf-8 -*-
"""装配前的**内容修补**：把专家批注里反复出现的那几类硬伤统一修掉。

来源：2026-11-11 业主对《测试_洪拟征告7号_稳评报告》《测试_城东污水厂_复垦方案》
两份成稿的逐条批注。这里只处理**能确定性判断**的问题（数字加总、条数、日期先后、
伪造法律名、公式写法、胶水句、附件指向），不做改写式润色：

  稳评：
    · 拟征收土地总面积 ≠ 地类构成分项之和（批注：面积合计和表6-1不符）
    · 风险条数前后不一致（批注：4项风险，第11章为6风险）
    · 专家评审日期早于报告编制完成（批注：日期冲突）
    · 法规名编造（批注：《…街道…民委员会组织法》没有这个说法）
    · 「责任单　2026年9月24日，位审阅工作方案」这类错位句（批注：不通顺）
    · 群众意见得分公式缺乘号、得分与正文矛盾（批注：不完整/得分为0矛盾）
    · 营业执照等材料「详见附件1」但附件1是专家评审材料（批注：和附件1不匹配）
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FIXED_LAW = "《中华人民共和国村民委员会组织法》（2018年12月29日修正）"


def _fmt_num(x: float) -> str:
    return f"{x:.1f}" if abs(x - round(x)) > 1e-9 else f"{round(x)}"


# ── ① 面积合计 = 各地类分项之和 ────────────────────────────────
_AREA_PAT = re.compile(
    r'(拟征收土地总面积)(\d+(?:\.\d+)?)(\s*平方米)'
    r'([\s\S]{0,300}?地类构成包括)([^。]+)(。)')


def _area_table(md: str):
    """从表6-1（面积总计 + 各地类列 + 合计行）取权威分项。"""
    lines = str(md or "").splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i].strip())
            i += 1
        if len(block) < 3:
            continue
        cells = [c.strip() for c in block[0].strip("|").split("|")]
        if "面积总计" not in cells or "耕地" not in cells:
            continue
        total_ci = cells.index("面积总计")
        skip = {"面积总计", "备注", "权属类别", "被征（用）地单位", "被征(用)地单位"}
        total_row = None
        for r in block[2:]:
            cs = [c.strip() for c in r.strip("|").split("|")]
            if cs and "合计" in cs[0]:
                total_row = cs
                break
        if not total_row:
            continue
        pairs = []
        for k, name in enumerate(cells):
            if k in (total_ci,) or name in skip or not name:
                continue
            val = total_row[k] if k < len(total_row) else ""
            try:
                v = float(val.replace(",", ""))
            except ValueError:
                continue
            if v > 0:
                pairs.append((name, v))
        try:
            total = float(total_row[total_ci].replace(",", ""))
        except (ValueError, IndexError):
            total = sum(v for _n, v in pairs)
        return total, pairs
    return None, []


def fix_area_sum(md: str) -> Tuple[str, int]:
    """总面积/地类构成以表6-1的合计行为准。

    实测（洪拟征告7号）：正文只列了 8 个地类，漏掉商业服务业用地 1671、
    工矿用地 5、住宅用地 2272，合计比表6-1少 3948 ㎡。正确做法是**补齐分项**，
    而不是把总数改小。
    """
    table_total, pairs = _area_table(md)
    n = 0

    def _repl(m):
        nonlocal n
        if table_total is None:
            # 章内没有表可对（表6-1 往往是系统渲染的）→ 不动，交给
            # 装配后的 `_fix_stability_area` 按 docx 里的表去改
            return m.group(0)
        n += 1
        lst = "、".join(f"{name}{_fmt_num(v)}平方米" for name, v in pairs)
        return (f"{m.group(1)}{_fmt_num(table_total)}{m.group(3)}"
                f"{m.group(4)}{lst}{m.group(6)}")

    out = _AREA_PAT.sub(_repl, md)
    return out, n


# ── ② 风险条数：以风险表行数/等级分布为准 ─────────────────────
def _risk_table_stats(md: str) -> Tuple[int, Dict[str, int]]:
    """在 markdown 表格里找风险等级表，返回 (风险条数, {等级: 个数})。"""
    lines = str(md or "").splitlines()
    best_count, best_dist = 0, {}
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i].strip())
            i += 1
        if len(block) < 3:
            continue
        cells = [c.strip() for c in block[0].strip("|").split("|")]
        if not any("风险" in c for c in cells):
            continue
        # 找出「等级」列
        lvl_ci = next((k for k, c in enumerate(cells) if "等级" in c), None)
        rows = [b for b in block[2:] if not b.startswith("|--")]
        data = []
        for r in rows:
            cs = [c.strip() for c in r.strip("|").split("|")]
            if not cs or not cs[0]:
                continue
            if "合计" in "".join(cs):
                continue
            data.append(cs)
        if len(data) > best_count:
            best_count = len(data)
            dist: Dict[str, int] = {}
            if lvl_ci is not None:
                for cs in data:
                    v = cs[lvl_ci] if lvl_ci < len(cs) else ""
                    key = "一般" if "一般" in v else ("较小" if "较小" in v else
                          ("较大" if "较大" in v else ""))
                    if key:
                        dist[key] = dist.get(key, 0) + 1
            best_dist = dist
    return best_count, best_dist


def fix_risk_count(chapters: Dict[Any, Dict[str, Any]]) -> int:
    count, dist = 0, {}
    # 🔴 第11章的风险条目是**唯一口径**（与报告提示词一致）：表4-1/表12-4 都要跟它走。
    #    拿「章内最大的风险表行数」当口径会取到第4章模板表（写死六类，含征收程序风险），
    #    与第11章实际分析的五类对不上（2026-11-11 实测）。
    md11 = str(((chapters.get(11) or chapters.get("11") or {})
                if isinstance(chapters.get(11) or chapters.get("11"), dict)
                else {}).get("markdown") or "")
    if md11:
        try:
            from app.validation.content_guardrails import _ch11_items
            items = _ch11_items(md11)
            if len(items) >= 3:
                count = len(items)
                for _name, lv in items:
                    if lv:
                        dist[lv] = dist.get(lv, 0) + 1
        except Exception:
            pass
    if count <= 0:
        for ch in chapters.values():
            if isinstance(ch, dict):
                c, d = _risk_table_stats(str(ch.get("markdown") or ""))
                if c > count:
                    count, dist = c, d
    if count <= 0:
        return 0
    n = 0
    for ch in chapters.values():
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        out, k1 = re.subn(r'共\s*(?:识别)?\s*\d+\s*(个方面主要风险因素|个主要风险因素|项主要风险因素|项风险因素|项风险)',
                          lambda m: f"共{count}{m.group(1)}", md)
        out, k2 = re.subn(r'识别出\s*\d+\s*项', f"识别出{count}项", out)
        # 「其中4项为一般风险，2项为较小风险」——量词可能是「个」也可能是「项」
        _lv_pairs = [(k, v) for k, v in dist.items() if v]
        if len(_lv_pairs) >= 2:
            _a, _b = _lv_pairs[0], _lv_pairs[1]
            out, k3 = re.subn(
                r'(\d+)\s*([个项])为' + re.escape(_a[0]) + r'风险[，,]\s*(\d+)\s*[个项]为'
                + re.escape(_b[0]) + r'风险',
                lambda m: (f"{_a[1]}{m.group(2)}为{_a[0]}风险，"
                           f"{_b[1]}{m.group(2)}为{_b[0]}风险"), out)
        else:
            k3 = 0
        # 「…共梳理出征收程序、补偿方案…六项主要风险因素」——把列错的清单整句收口
        out, k4 = re.subn(
            r'共(?:梳理出|识别出|识别|列出|确定|包括|包含)'
            r'[^。]{0,80}?(?:[一二三四五六七八九十]+|\d+)\s*项主要风险因素',
            f"共识别{count}项主要风险因素", out)
        if k1 or k2 or k3 or k4:
            n += k1 + k2 + k3 + k4
            ch["markdown"] = out
    return n


# ── ③ 日期先后：评审不得早于报告编制完成 ─────────────────────
_DATE = r'(\d{4})年(\d{1,2})月(\d{1,2})日'


def _d(y, m, d) -> date:
    return date(int(y), int(m), int(d))


_END_DATE = r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日'


_END = r'(?:(?P<ey>\d{4})年)?(?P<em>\d{1,2})月(?P<ed>\d{1,2})日'


def fix_date_order(md: str) -> Tuple[str, int]:
    # 编制完成时点的写法各家不同：「…至X编制评估报告」「…至X报告编制完成止」
    # 「…至X编制社会稳定风险评估报告」。旧写法只认第一种，实测 2026-11-11
    # 这版写成「至2026年9月30日报告编制完成止」，评审日期（9/22–9/30）没被纠正。
    make = re.search(
        _DATE + r'[^。；]{0,20}?至\s*' + _END +
        r'[\s\S]{0,30}?(?:编制评估报告|编制社会稳定风险评估报告|报告编制完成|编制完成)', md)
    rev = re.search(
        _DATE + r'\s*至\s*' + _END + r'[^。]{0,20}?组织专家评审', md)
    if not (make and rev):
        return md, 0
    make_end = date(int(make.group(4) or make.group(1)),
                    int(make.group(5)), int(make.group(6)))

    def _move(m):
        rs = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        re_ = date(int(m.group(4) or m.group(1)), int(m.group(5)), int(m.group(6)))
        if rs > make_end:
            return m.group(0)
        span = max((re_ - rs).days, 0)
        ns = make_end + timedelta(days=1)
        ne = ns + timedelta(days=span)
        return (f"{ns.year}年{ns.month}月{ns.day}日"
                f"至{ne.month}月{ne.day}日组织专家评审")

    out, n = re.subn(_DATE + r'\s*至\s*' + _END + r'[^。]{0,20}?组织专家评审', _move, md)
    return out, n


# ── ④ 编造的法规名 ───────────────────────────────────────────
_FAKE_LAW = re.compile(r'《[^》]{2,50}?组织法》\s*(?:[（(][^）)]{0,30}[）)])?')


def fix_fake_laws(md: str) -> Tuple[str, int]:
    n = 0

    def _repl(m):
        nonlocal n
        s = m.group(0)
        if "村民委员会组织法" in s:
            n += 1
            return _FIXED_LAW
        # 「XX街道、XX社区…民委员会组织法」这类把地名拼进法律名的写法
        if any(k in s for k in ("街道", "社区", "组", "委员会")) and "中华人民共和国" not in s:
            n += 1
            return _FIXED_LAW
        return s

    return _FAKE_LAW.sub(_repl, md), n


# ── ⑤ 错位/病句 ─────────────────────────────────────────────
_GARBLE = (
    (re.compile(r'责任单\s*(\d{4}年\d{1,2}月\d{1,2}日)[，,]\s*位审阅'),
     r'责任单位于\1审阅'),
    (re.compile(r'情形之一的[，,]\s*渎职的[。；;]'),
     '情形之一的，属于渎职行为。'),
    (re.compile(r'([^。；\n]{0,10})出现下列情形之一的[，,]\s*([^。]{0,20})的[。；;]'),
     r'\1出现下列情形之一的，属于\2行为。'),
)


def fix_garble(md: str) -> Tuple[str, int]:
    n = 0
    for pat, rep in _GARBLE:
        md, k = pat.subn(rep, md)
        n += k
    return md, n


# ── ⑥ 群众意见得分公式 / 正文得分 ───────────────────────────
_FORMULA = re.compile(
    r'群众意见分析得分\s*=\s*[（(]([^）)]*)[）)]\s*(\d+(?:\.\d+)?)?\s*=\s*([\d.]+)\s*分')


def fix_score_formula(md: str) -> Tuple[str, int]:
    changed = 0

    def _repl(m):
        nonlocal changed
        terms = m.group(1)
        nums = re.findall(r'(\d+(?:\.\d+)?)\s*%\s*(\d+(?:\.\d+)?)', terms)
        if not nums:
            # 尝试「0.0%1」这种缺乘号的写法
            nums = re.findall(r'(\d+(?:\.\d+)?)\s*%\s*(\d+(?:\.\d+)?)', terms.replace("%", "% "))
        if not nums:
            return m.group(0)
        base = float(m.group(2) or 35)
        total = sum(float(p) / 100.0 * float(w) for p, w in nums) * base
        expr = " + ".join(f"{p}%×{w}" for p, w in nums)
        changed += 1
        return f"群众意见分析得分 =（{expr}）×{_fmt_num(base)} = {total:.1f} 分"

    return _FORMULA.sub(_repl, md), changed


def fix_score_mentions(chapters: Dict[Any, Dict[str, Any]]) -> int:
    """第12章正文的「得分为X分」与本章评分表合计对齐。"""
    ch12 = chapters.get(12) or chapters.get("12") or {}
    if not isinstance(ch12, dict):
        return 0
    md = str(ch12.get("markdown") or "")
    # 找含「得分」列的表的数值合计；没有合计行就累加数据行
    total = None
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i].strip())
            i += 1
        cells = [c.strip() for c in block[0].strip("|").split("|")]
        if "得分" not in "".join(cells):
            continue
        ci = next((k for k, c in enumerate(cells) if "得分" in c), None)
        if ci is None:
            continue
        vals = []
        for r in block[2:]:
            cs = [c.strip() for c in r.strip("|").split("|")]
            if ci < len(cs):
                try:
                    vals.append(float(cs[ci]))
                except ValueError:
                    pass
        if vals:
            total = sum(vals)
    if total is None:
        return 0
    new_md, k = re.subn(r'(得分为)\s*[\d.]+(\s*分)', rf'\g<1>{total:.1f}\g<2>', md)
    if k:
        ch12["markdown"] = new_md
    return k


# ── ⑦ 附件指向 ───────────────────────────────────────────────
def fix_attachment_ref(md: str) -> Tuple[str, int]:
    """「营业执照、职称及培训证书详见附件1」——附件1其实是专家评审材料。"""
    n = 0
    if "营业执照" not in md:
        return md, 0

    def _repl(m):
        nonlocal n
        n += 1
        return "随报告另行提供营业执照、项目人员职称及稳评培训证书"

    out = re.sub(r'详见附件\d*[。；]?', lambda m: _repl(m), md)
    return out, n


def fix_truncated_paragraphs(md: str) -> Tuple[str, int]:
    """去掉「话说到一半」的残段（2026-11-11 业主：话术不连贯）。

    判据：正文段落结尾既没有句末标点，也不是表格/标题/短标签/公式，就回退到
    该段最后一个 。！？；；回退后不足 20 字的整段删除。
    """
    import re as _re
    out, n = [], 0
    for para in str(md or "").split("\n\n"):
        s = para.strip()
        if not s:
            out.append(para)
            continue
        s = _re.sub(r'[\ue000-\uf8ff]', '', s).rstrip()
        if (s.startswith(("|", "#", "[[", "表", "图", "注", "——", "- ", "* "))
                or len(s) < 12
                or "：" in s[:12]
                or "＝" in s or "=" in s
                or s[-1] in "。！？…”’）)】"):
            out.append(s)
            continue
        cut = max((s.rfind(ch) for ch in "。！？；"), default=-1)
        if s[-1] in "，、":
            # 以逗号/顿号收尾 = 话没说完
            if cut >= 0 and len(s[:cut + 1].strip()) >= 12:
                out.append(s[:cut + 1])
            n += 1
            continue
        if cut >= 0 and len(s[:cut + 1].strip()) >= 20:
            out.append(s[:cut + 1])
            n += 1
        elif len(s) >= 40:
            n += 1                 # 长段且没有完整句 → 整段丢弃
        else:
            out.append(s)          # 短句/标签，可能是表格前后的说明，保留
    if n == 0:
        return md, 0
    return "\n\n".join(out), n


# ── ⑦ 第4章「四性分析」与第10章重复 ───────────────────────
def dedupe_ch4_four_natures(chapters: Dict[Any, Dict[str, Any]]) -> int:
    """第4章（评估工作报告）的（二）四性分析只保留结论，详细分析指向第十章。

    专家批注 2026-11-11：「（二）四性分析 —— 和第10章重复」。
    第10章本身就是「合法性、合理性、可行性、可控性分析」，第4章再展开一遍
    就是同文两处。这里把第4章该节压成一句结论 + 交叉引用。
    """
    import re as _re
    ch4 = chapters.get(4) or chapters.get("4")
    if not isinstance(ch4, dict):
        return 0
    md = str(ch4.get("markdown") or "")
    if not md:
        return 0
    lines = md.split("\n")
    start = next((i for i, ln in enumerate(lines)
                  if _re.match(r'^#{2,4}\s*[（(]二[）)]\s*四性分析\s*$',
                               ln.strip())), None)
    if start is None:
        return 0
    end = next((i for i in range(start + 1, len(lines))
                if _re.match(r'^#{2,4}\s*[（(][一二三四五六七八九十]+[）)]',
                             lines[i].strip())), len(lines))
    body_len = len("\n".join(lines[start + 1:end]).strip())
    if body_len <= 400:
        return 0            # 本来就只是摘要，不动
    summary = ("经分析，本决策在合法性、合理性、可行性、可控性四个方面均符合要求，"
               "社会稳定风险可控。具体分析详见第十章。")
    ch4["markdown"] = "\n".join(lines[:start + 1] + ["", summary, ""] + lines[end:])
    return 1


# ── ⑧ 缺问卷逐题数据时，正文不能引用不存在的表 ──────────────
def drop_missing_survey_refs(chapters: Dict[Any, Dict[str, Any]],
                             state: Optional[Dict[str, Any]] = None) -> int:
    """没有逐题清点数据时，删掉「如下表所示」「从上表可见」和 [TABLE:] 标记。

    业主 2026-11-12：「可选资料没有就不要在正文里提；硬性资料没提供要提示」。
    问卷逐题人数属于**可选**（原件在附件里就能评）；但正文不能指着一张
    渲染不出来的表说「如下表所示」，更不能编造「全部为本地居民」这类结论。
    """
    if not isinstance(state, dict):
        return 0
    ch9 = chapters.get(9) or chapters.get("9")
    if not isinstance(ch9, dict):
        return 0
    md = str(ch9.get("markdown") or "")
    if not md:
        return 0
    try:
        from app.services.table_generation_service import TableGenerationService
        from app.services.report_assembler import ReportAssembler
        _has_pub = ReportAssembler._survey_rows_have_counts(
            (TableGenerationService.generate_table_3_1(state) or {}).get("rows"))
        _has_dept = ReportAssembler._survey_rows_have_counts(
            (TableGenerationService.generate_table_3_3(state) or {}).get("rows"))
    except Exception:
        return 0
    if _has_pub and _has_dept:
        return 0
    lines = md.split("\n")
    out = []
    changed = 0
    for ln in lines:
        s = ln.strip()
        if s in ("[TABLE:ch9_public_survey]", "[TABLE:ch9_dept_survey]"):
            changed += 1
            continue
        _is_pub = ("个人问卷" in s) or ("群众问卷" in s)
        _is_dept = (("单位问卷" in s) or ("部门问卷" in s)
                    or ("单位（部门）" in s) or ("单位(部门)" in s))
        # 「xx问卷统计汇总情况如下表所示：」——对应表出不来就整句删掉
        if "如下表所示" in s and ((_is_pub and not _has_pub) or
                                  (_is_dept and not _has_dept)):
            changed += 1
            continue
        # 「从上表可见…」是对缺失表的解读，删掉（往往还带着编造的构成结论）
        if s.startswith("从上表可见") or s.startswith("由表9-"):
            if _is_pub and not _has_pub:
                changed += 1
                continue
            if _is_dept and not _has_dept:
                changed += 1
                continue
            # 没点名是个人还是单位：两张表都没数据就删
            if not _has_pub and not _has_dept:
                changed += 1
                continue
        out.append(ln)
    if changed:
        ch9["markdown"] = "\n".join(out)
    return changed



# ── ⑨ 表6-1 渲染不出来时，正文不能引用 ──────────────────────
def drop_missing_sys_table_refs(chapters: Dict[Any, Dict[str, Any]],
                                state: Optional[Dict[str, Any]] = None) -> int:
    """没有勘测定界/地类数据时，第6章删掉 [SYS_TABLE:6-1] 与「如下表所示」。

    业主 2026-11-12：「可选资料没有就不要写在正文里」。地类表渲染不出来时，
    正文却写「项目用地基本情况如下表所示」「从上表可见」，读者会找不到表。
    """
    if not isinstance(state, dict):
        return 0
    ch6 = chapters.get(6) or chapters.get("6")
    if not isinstance(ch6, dict):
        return 0
    md = str(ch6.get("markdown") or "")
    if "[SYS_TABLE:6-1]" not in md:
        return 0
    f = state.get("filled_data") or {}
    if (f.get("_extracted_tables") or f.get("land_classification")
            or f.get("_land_by_category")):
        return 0
    out, changed = [], 0
    for line in md.split("\n"):
        s = line.strip()
        if s == "[SYS_TABLE:6-1]":
            changed += 1
            continue
        if "如下表所示" in s:
            line = (line.replace("项目用地基本情况如下表所示。", "项目用地情况以勘测定界成果为准。")
                        .replace("如下表所示。", "以勘测定界成果为准。"))
            changed += 1
        if s.startswith("从上表可见"):
            line = re.sub(r'^(\s*)从上表可见[，,]?', r'\1', line)
            changed += 1
        out.append(line)
    if changed:
        ch6["markdown"] = "\n".join(out)
    return changed


# ── ⑩ 第4章「（五）参与人员签字」不能只有三个空标签 ──────────
def fill_participant_signatures(chapters: Dict[Any, Dict[str, Any]],
                                state: Optional[Dict[str, Any]] = None) -> int:
    """把评估人员姓名与手写签字线补进第4章「参与人员签字」。

    专家批注 2026-11-12：该节只有「评估专业人员/评估助理/资料员」三个标签，
    没有姓名也没有签字线。姓名取用户勾选的报告人员（或事实卡组长），
    其余角色留手写空位。
    """
    if not isinstance(state, dict):
        return 0
    ch4 = chapters.get(4) or chapters.get("4")
    if not isinstance(ch4, dict):
        return 0
    md = str(ch4.get("markdown") or "")
    if "参与人员签字" not in md:
        return 0
    f = state.get("filled_data") or {}
    names = [str(x).strip() for x in (state.get("selected_personnel")
             or f.get("_selected_personnel") or []) if str(x).strip()]
    leader = str(f.get("leader") or "").strip()
    if leader and leader not in names:
        names = [leader] + names
    lines = md.split("\n")
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().lstrip("#").strip().startswith("（五）参与人员签字")), None)
    if start is None:
        return 0
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip().startswith("#")), len(lines))
    body = "\n".join(lines[start + 1:end])
    if "____" in body or any(n in body for n in names):
        return 0
    new_body = [
        "",
        "评估专业人员：" + ("、".join(names) if names
                          else "____________________"),
        "",
        "评估助理：____________________",
        "",
        "资料员：____________________",
        "",
        # 🔴 不能单独一行全是下划线：Markdown 会把它当分隔线（---/___）整行吃掉
        "参与人员签字：____________________　　____________________　　____________________",
        "",
    ]
    ch4["markdown"] = "\n".join(lines[:start + 1] + new_body + lines[end:])
    return 1

# ── 入口 ────────────────────────────────────────────────────
def repair_stability_chapters(chapters: Dict[Any, Dict[str, Any]],
                              state: Optional[Dict[str, Any]] = None) -> List[str]:
    logs: List[str] = []
    for key, ch in list(chapters.items()):
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        orig = md
        md, n_tr = fix_truncated_paragraphs(md)
        md, n_area = fix_area_sum(md)
        md, n_law = fix_fake_laws(md)
        md, n_garble = fix_garble(md)
        md, n_formula = fix_score_formula(md)
        md, n_att = fix_attachment_ref(md)
        if str(key) == "7":
            md, n_date = fix_date_order(md)
            if n_date:
                logs.append(f"第7章 评审日期调整 {n_date} 处")
        if md != orig:
            ch["markdown"] = md
            if n_tr:
                logs.append(f"第{key}章 截断残句 {n_tr} 处")
            if n_area:
                logs.append(f"第{key}章 总面积改为地类分项之和（{n_area} 处）")
            if n_law:
                logs.append(f"第{key}章 修正编造法规名 {n_law} 处")
            if n_garble:
                logs.append(f"第{key}章 修正错位/病句 {n_garble} 处")
            if n_formula:
                logs.append(f"第{key}章 重写群众意见得分公式")
            if n_att:
                logs.append(f"第{key}章 修正附件指向 {n_att} 处")
    n_count = fix_risk_count(chapters)
    if n_count:
        logs.append(f"风险条数统一 {n_count} 处")
    n_score = fix_score_mentions(chapters)
    if n_score:
        logs.append(f"第12章得分与评分表对齐 {n_score} 处")
    n_dup = dedupe_ch4_four_natures(chapters)
    if n_dup:
        logs.append("第4章四性分析改为结论+指向第十章")
    n_sv = drop_missing_survey_refs(chapters, state)
    if n_sv:
        logs.append(f"第9章删除缺失问卷表的引用 {n_sv} 处")
    n_t6 = drop_missing_sys_table_refs(chapters, state)
    if n_t6:
        logs.append(f"第6章删除缺失地类表的引用 {n_t6} 处")
    n_sig = fill_participant_signatures(chapters, state)
    if n_sig:
        logs.append("第4章参与人员签字已补姓名与签字线")
    return logs
