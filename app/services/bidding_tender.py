# -*- coding: utf-8 -*-
"""招标文件解析 + 投标人员抽取（投标文件生成的前半段）。

业主 2026-11-05 的写法口径：
  1. 先熟读招标文件：项目信息、资格条件、人员要求、评分办法、**投标文件格式在哪一章**；
  2. 按招标要求的格式逐项出投标文件（不同项目格式章节不同，不能写死）；
  3. 人员从公司人员库里按招标要求**随机抽**（负责人1 + 组员3 的最低配置），
     并带上职称证书/社保证明等图片；
  4. 人工案例里比招标格式多出来的内容（业绩、体系认证、人员证书、技术方案等）
     按用户确认的清单加入。

本模块只做「读懂招标文件 + 选人」，生成 docx 见 `bidding_bid_builder.py`。
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 文本抽取
# ═══════════════════════════════════════════════════════════════
def extract_text(path: str | Path) -> str:
    p = Path(str(path))
    suffix = p.suffix.lower()
    try:
        if suffix == ".docx":
            from docx import Document
            d = Document(str(p))
            parts = [x.text.strip() for x in d.paragraphs if x.text.strip()]
            for tb in d.tables:
                for row in tb.rows:
                    parts.append("\t".join(c.text.strip() for c in row.cells))
            return "\n".join(parts)
        if suffix == ".pdf":
            import fitz
            with fitz.open(str(p)) as d:
                return "\n".join(pg.get_text() for pg in d)
        if suffix == ".doc":
            import subprocess
            for cmd in (["textutil", "-convert", "txt", "-stdout", str(p)],
                        ["antiword", str(p)],
                        ["libreoffice", "--headless", "--convert-to", "txt",
                         "--outdir", "/tmp", str(p)]):
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=40)
                    out = (r.stdout or b"").decode("utf-8", "ignore")
                    if out.strip():
                        return out
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[招标解析] 文本抽取失败 {p}: {e}")
    return ""


def _clean(s: str) -> str:
    return re.sub(r'[ \t　]+', " ", str(s or "")).strip()


# ═══════════════════════════════════════════════════════════════
# 招标文件解析
# ═══════════════════════════════════════════════════════════════
_UNIT = r'([0-9]+(?:\.[0-9]+)?)\s*万元'


def parse_tender(path: str | Path) -> Dict[str, Any]:
    """招标文件 → 结构化要求（项目信息/资格/人员/评分/投标文件格式）。"""
    text = extract_text(path)
    lines = [_clean(x) for x in text.splitlines()]
    blob = "\n".join(lines)

    def grab(pat: str, default: str = "") -> str:
        m = re.search(pat, blob)
        return _clean(m.group(1)) if m else default

    # ── 项目信息 ──
    project_name = grab(r'项目名称[:：]\s*(.+)')
    if not project_name:
        # 封面式：第一行就是项目名
        for ln in lines[:20]:
            if ln and ("项目" in ln and len(ln) > 8 and "编号" not in ln):
                project_name = ln
                break
    project_no = (grab(r'项目编号[:：]\s*([^\s，,]+)')
                  or grab(r'询比编号[:：]\s*([^\s，,]+)'))
    # 采购人/代理的标签各家不同（采购人：/采购人名称：/采购单位：/询比人：/招标代理机构：）
    purchaser = (grab(r'采\s*购\s*人(?:名称)?[:：]\s*(.+)')
                 or grab(r'询\s*比\s*人[:：]\s*(.+)')
                 or grab(r'采购单位[:：]\s*(.+)'))
    agency = (grab(r'采购代理机构[:：]\s*(.+)')
              or grab(r'招标代理(?:机构)?[:：]\s*(.+)'))
    # 金额单位：万元 或 元（大冈镇这份写的是 62000.00 元）
    budget = grab(r'本项目预算为[:：]?\s*' + _UNIT)
    budget_yuan = grab(r'预算金额[为:：]?\s*([0-9,]+(?:\.\d+)?)\s*元')
    max_yuan = grab(r'最高限价[为:：]?\s*([0-9,]+(?:\.\d+)?)\s*元')
    if budget_yuan and not budget:
        try:
            budget = f"{float(budget_yuan.replace(',', '')) / 10000:g}"
        except ValueError:
            budget = budget_yuan
    max_price = grab(r'最高限价[为:：]?\s*' + _UNIT) or budget
    if max_yuan and not grab(r'最高限价[为:：]?\s*' + _UNIT):
        try:
            max_price = f"{float(max_yuan.replace(',', '')) / 10000:g}"
        except ValueError:
            max_price = max_yuan

    # ── 资格条件（2.1 段，①~⑦）──
    qualifications: List[str] = []
    q0 = blob.find("应具备下列资格条件")
    if q0 >= 0:
        seg = blob[q0:q0 + 1200]
        for m in re.finditer(r'([①-⑦])\s*([^\n]{2,120})', seg):
            s = _clean(m.group(2))
            if s and s not in qualifications:
                qualifications.append(s)
    if not qualifications:
        for m in re.finditer(r'([①-⑦])\s*([^\n]{4,80}?(?:资质|证书|信用|联合体)[^\n]{0,40})',
                             blob):
            s = _clean(m.group(2))
            if s and s not in qualifications:
                qualifications.append(s)

    # ── 人员配置最低要求 ──
    staff_min: Dict[str, int] = {}
    m = re.search(r'人员不得少于[:：]?\s*(.{0,80})', blob)
    if m:
        seg = m.group(1)
        for role, n in re.findall(r'(项目负责人|项目组成员|技术负责人|组员)\s*([0-9]+)\s*名', seg):
            staff_min[role] = int(n)
    if not staff_min:
        staff_min = {"项目负责人": 1, "项目组成员": 3}

    # ── 评分办法里的关键口径（只在评审办法那一章里找）──
    scoring: List[str] = []
    sc0 = -1
    for i, ln in enumerate(lines):
        if re.match(r'^第四章\s*[（(【]?\s*(评审|评标)方法', ln) and not re.search(r'-\s*\d+\s*-$', ln):
            sc0 = i
            break
    if sc0 >= 0:
        sc_lines = lines[sc0:sc0 + 250]
        for ln in sc_lines:
            if re.match(r'^第五章', ln):
                break
            if 6 <= len(ln) <= 200 and ln not in scoring:
                scoring.append(ln)

    # ── 投标/投选文件格式：定位格式所在的那一章（跳过目录行）──
    fmt_chapter, fmt_start = "", -1
    for i, ln in enumerate(lines):
        if re.search(r'投标文件格式|响应文件格式|投选文件格式', ln):
            # 目录行带页码（「- 49 -」或「… 91」）的不是正文位置
            if (re.search(r'-\s*\d+\s*-$', ln) or re.search(r'[ \t]\d+$', ln)
                    or "目\s*录" in ln):
                continue
            _nxt = " ".join(lines[i + 1:i + 4])
            # 正文里「应符合第七章“投选文件格式”要求」这类引用句不是章标题
            if ln.endswith("。") or len(ln) > 40:
                continue
            if ("投标文件" in _nxt or "响应文件" in _nxt or "投选文件" in _nxt
                    or "（一）" in _nxt
                    or ln.startswith("第六章") or ln.startswith("第")):
                fmt_chapter, fmt_start = ln, i
                break
    if fmt_start < 0:
        for i, ln in enumerate(lines):
            if ln.startswith("第六章") and re.search(r'-\s*\d+\s*-$', ln) is None:
                fmt_chapter, fmt_start = ln, i
                break

    fmt_sections: List[Dict[str, Any]] = []
    if fmt_start >= 0:
        seen = set()
        for ln in lines[fmt_start:fmt_start + 500]:
            if re.match(r'^第[一二三四五六七八九十]+章', ln) and ln != fmt_chapter:
                break
            m = re.match(r'^[（(]\s*([一二三四五六七八九十]+)\s*[)）]\s*([^\n]{2,40})', ln)
            if m:
                no, title = m.group(1), _clean(m.group(2))
                key = (no, title)
                if key not in seen:
                    seen.add(key)
                    fmt_sections.append({"no": no, "title": title, "level": 1})
                continue
            m2 = re.match(r'^([0-9]{1,2})[、.．]\s*([^\n]{2,40})$', ln)
            m3 = re.match(r'^[（(]\s*([0-9]{1,2})\s*[)）]\s*([^\n]{2,40}?)[（(]?格式', ln)
            if m3:
                fmt_sections.append({"no": m3.group(1), "title": _clean(m3.group(2)),
                                     "level": 2})
                continue
            if (m2 and fmt_sections
                    and re.search(r'(表|函|书|清单|说明)$', m2.group(2))
                    and not re.search(r'(我方|贵方|如果|同意|愿意|承诺)', m2.group(2))):
                fmt_sections.append({"no": m2.group(1), "title": _clean(m2.group(2)),
                                     "level": 2})

    # ── 采购内容（第三章 项目技术要求）──
    scope = ""
    for i, ln in enumerate(lines):
        if re.match(r'^第三章\s*(项目技术要求|采购需求|服务要求)', ln) and not re.search(r'-\s*\d+\s*-$', ln):
            scope = _clean(" ".join(x for x in lines[i + 1:i + 25] if x))[:1200]
            break

    # ── 其他硬约束 ──
    no_consortium = "不接受联合体" in blob
    sme_required = "中小企业" in blob and ("专门面向" in blob or "中小企业声明" in blob)

    # ── 资格条件（部分招标文件用「（1）（2）」编号）──
    if not qualifications:
        q0 = blob.find("主要资格条件")
        if q0 >= 0:
            seg = blob[q0:q0 + 2000]
            _items = re.split(r'（\d+）', seg)[1:]
            for it in _items:
                piece = _clean(it.split("\n")[0])[:160]
                if piece and piece not in qualifications:
                    qualifications.append(piece)
    # 询比公告写法：「3.1企业资质要求：…」「3.2企业业绩要求：…」
    if not qualifications:
        for m in re.finditer(r'^3\.(\d)\s*([^\n]{4,200})', blob, re.M):
            piece = _clean(m.group(2))[:200]
            if piece and piece not in qualifications:
                qualifications.append(piece)

    # ── 投标文件格式目录（有的写「投标主要文件目录」+ 一、二、三…）──
    format_titles: List[str] = []
    # 优先「投标主要文件目录」——它后面紧跟真正的一、二、三…条目；
    # 只找「投标文件格式」会命中前面的 7.1.6 目录行，抓到别的章标题。
    f0 = blob.find("投标主要文件目录")
    if f0 < 0:
        f0 = blob.find("投标文件格式")
    if f0 >= 0:
        seg = blob[f0:f0 + 1200]
        _seen_no = set()
        for m in re.finditer(r'^([一二三四五六七八九十]+)、\s*([^\n]{2,30})$',
                             seg, re.M):
            no, t = m.group(1), _clean(m.group(2))
            if no in _seen_no or not t:
                continue          # 后面的同名章标题（二、…要求 / 三、…格式）不再重复收
            _seen_no.add(no)
            format_titles.append(t)
    # 只有「最低评标价法」的招标文件走简式八件套；询比项目（「投选人」「投选文件
    # 格式」「附件1：封面」）走询比件；其余沿用综合评分法的全量版
    if "最低评标价法" in blob or "最低投标价法" in blob:
        bid_format = "compact"
    elif "投选文件格式" in blob or ("询比文件" in blob and "投选人" in blob):
        bid_format = "xunbi"
    else:
        bid_format = "full"

    # ── 询比件的工期（「合同签订之日起至复垦验收完成（12个月）」）──
    duration_months = 0
    _dm = re.search(r'[（(]\s*(\d{1,2})\s*个月\s*[）)]', blob)
    if _dm:
        duration_months = int(_dm.group(1))

    # ── 询比件的服务内容清单（第五章 服务内容表：编号/名称/工作描述）──
    #    从**未清洗**的原文里按制表符切列（`lines` 已把 \t 折成空格）
    work_items: List[Dict[str, str]] = []
    for ln in text.splitlines():
        _wm = re.match(r'^(\d+(?:\.\d+)?)\t([^\t]{2,40})\t(.*)$', ln.strip())
        if not _wm:
            continue
        no, name, desc = (_wm.group(1), _clean(_wm.group(2)),
                          _clean(_wm.group(3)))
        if desc == name or not desc:
            continue          # 分组行（1  XXX  1）不是末级工作项
        work_items.append({"no": no, "name": name, "desc": desc})

    # ── 户数 / 单价（村庄测绘这类按户计价）──
    households = ""
    mh = re.search(r'约?\s*(\d{2,5})\s*户', blob)
    if mh:
        households = mh.group(1)
    unit_price = ""
    mu = re.search(r'(\d+(?:\.\d+)?)\s*元\s*/\s*户', blob)
    if mu:
        unit_price = mu.group(1)

    # ── 工期（服务期限：签订合同后 90 日内完成）──
    duration_days = 0
    m = re.search(r'(\d{2,4})\s*日内(?:完成|提交|交付)', blob)
    if not m:
        m = re.search(r'(\d{2,4})\s*日历天', blob)
    if not m:
        m = re.search(r'服务期限[^\n]{0,40}?(\d{2,4})\s*[天日]', blob)
    if m:
        try:
            duration_days = int(m.group(1))
        except ValueError:
            duration_days = 0

    # ── 地块清单（服务方案表1-1 用）：名称 / 规模 / 位置 ──
    plots: List[Dict[str, Any]] = []
    for idx, ln in enumerate(lines):
        mm = re.match(r'^(\d{1,2})\s*[、.．]\s*([^\s，,。：:]{2,20}?地块)\s*(?:项目)?', ln)
        if not mm:
            continue
        name = _clean(mm.group(2))
        # 采购需求里也有「2、付款方式：项目各地块…」这类带编号的条款，不是地块
        if any(k in name for k in ("付款", "方式", "时间", "地点", "标准", "要求")):
            continue
        # 本段只取到下一个编号条目前，避免把后一个地块的面积串进来
        seg_lines = [ln]
        for nxt in lines[idx + 1: idx + 6]:
            if re.match(r'^\d{1,2}\s*[、.．]', nxt):
                break
            seg_lines.append(nxt)
        seg = " ".join(seg_lines)
        area = ""
        # 优先「本次报批/先导区」的面积，避免把园区总规划面积当成报批规模
        am = re.search(r'(?:本次报批|先导区)[^。]{0,40}?(\d+(?:\.\d+)?)\s*亩', seg)
        if not am:
            am = re.search(r'(?:总占地面积|新增用地|规划用地|用地)[^。]{0,12}?约?\s*'
                           r'(\d+(?:\.\d+)?)\s*亩', seg)
        if am:
            area = am.group(1)
        loc = ""
        lm = re.search(r'落地(?:于)?([^，,。]{2,30})', seg)
        if lm:
            loc = _clean(lm.group(1))
        kind = "单独选址" if "单独选址" in seg else "批次用地报批"
        plots.append({"name": _clean(mm.group(2)), "type": kind,
                      "area": area, "location": loc})

    # ── 商务条款（偏离表逐条响应用，不能只写服务内容）──
    # 🔴 人工稿的「商务条款响应及偏离表」覆盖：投标有效期 / 服务期限 / 报价要求 /
    #    履约保证金 / 招标代理费 / 投标保证金 / 投标文件份数 / 付款方式 / 质量要求 /
    #    其他实质性要求。旧实现只写了服务内容类 8 行，商业条款整批缺失
    #    （2026-11-11 业主：「跟之前模板缺少那么多内容」）。
    commerce: Dict[str, str] = {}

    def _clause(label: str, pat: str, repl=None) -> str:
        m = re.search(pat, blob)
        if m:
            txt = (repl(m) if repl else m.group(0))
            commerce[label] = _clean(txt)
        return commerce.get(label, "")

    _clause("投标有效期", r'投标有效期为\s*(\d+)\s*天',
            lambda m: f"自开标之日起 {m.group(1)} 天")
    if not commerce.get("投标有效期"):
        # 询比件写法：「投选有效期 | 为（从投选截止之日算起）：90日历天」
        _clause("投标有效期", r'投选有效期为?\s*[（(]?[^）)\n]{0,20}[）)]?\s*[:：]?\s*(\d+)\s*日历天',
                lambda m: f"自投选截止之日起 {m.group(1)} 日历天")
    _clause("履约保证金", r'履约保证金[（(]合同金额的[^）)]{1,8}[）)][^。]{0,30}')
    if not commerce.get("履约保证金"):
        _clause("履约保证金", r'履约保证金[\t ]+不(?:提交|收取)[^\n]{0,20}')
    _clause("招标代理费", r'招标代理费合计\s*([\d,]+)\s*元[^。]{0,40}',
            lambda m: f"招标代理费合计 {m.group(1)} 元，由中标人支付")
    _clause("投标保证金", r'本项目不收取投标保证金')
    if not commerce.get("投标保证金"):
        _clause("投标保证金", r'投(?:标|选)保证金[^\n]{0,40}')
    if not commerce.get("投标保证金"):
        _clause("投标保证金", r'投标保证金[^。]{0,30}')
    _clause("投标文件份数",
            r'投标文件要求正本\s*[一二三四五六七八九十\d]+\s*份[，,]\s*副本\s*[一二三四五六七八九十\d]+\s*份')
    _clause("付款方式", r'付款方式[\s\S]{0,200}?7\.\d[^\n]{0,120}',
            lambda m: re.sub(r'^\s*\d+(?:\.\d+)*\s*', '',
                             re.sub(r'^付款方式[^\n]*\n?', '', m.group(0)).strip()))
    _clause("质量要求", r'符合甲方质量要求[^。]{0,20}',
            lambda m: "成果" + m.group(0))
    _clause("其他实质性要求", r'其他实质性要求[^\n。]{0,50}',
            lambda m: "满足招标文件列明的其他实质性要求")

    # ── 资格证明文件清单（文件1..N）——从招标文件正文取，不写死 8 件 ──
    #    各家招标的件数/名称不同（有的 7 件、有的 9 件），按文件号去重取最长文本。
    qual_files: List[Dict[str, str]] = []
    _seen_file = {}
    for m in re.finditer(r'^文件\s*(\d{1,2})\s*([^\n]{4,120})', blob, re.M):
        try:
            no = int(m.group(1))
        except ValueError:
            continue
        txt = _clean(m.group(2)).strip("。；; ")
        _prev = None
        while _prev != txt:
            _prev = txt
            txt = re.sub(r'\s*[（(][^）)]*[）)]\s*$', '', txt).strip("。；; ")
        if not txt or no > 15:
            continue
        old = _seen_file.get(no)
        if old is None or len(txt) > len(old):
            _seen_file[no] = txt
    for no in sorted(_seen_file):
        qual_files.append({"no": str(no), "text": _seen_file[no]})

    # 中小企业声明函「所属行业」：取招标文件原话，取不到用「其他未列明行业」
    _sme_industry = ""
    _m = re.search(r'属于([^，。；\n（(]{2,12}?)行业', blob)
    if _m:
        _sme_industry = _m.group(1).strip()

    # 信用承诺书正文（从招标文件最后一处「信用承诺书」起，到落款前）
    credit_pledge: List[str] = []
    _ci = [m.start() for m in re.finditer("信用承诺书", blob)]
    if _ci:
        _seg = blob[_ci[-1]: _ci[-1] + 2000]
        for _ln in _seg.splitlines()[1:]:
            _t = _clean(_ln)
            if not _t:
                continue
            if _t.startswith(("法定代表人签名", "单位名称", "序号", "年    月")):
                break
            if _t == "信用承诺书":
                continue
            credit_pledge.append(_t)
            if len(credit_pledge) >= 15:
                break

    # ── 拟派人员的社保要求 ──
    # 有的招标文件要求「项目负责人/技术人员」提供社保缴纳证明，有的只要求公司
    # 整体的税收社保材料（资格文件）。两者不能混：前者要挑**参保的人**，
    # 后者只要公司证明。业主 2026-11-11：「根据招标文件中的人员要求……交社保」。
    _si_kw = r'(?:社保|社会保险|社会保障)'
    _person_kw = (r'(?:项目负责人|技术负责人|项目组|技术人员|拟派人员|'
                  r'配置人员|投入人员|项目管理人员)')
    personnel_si = bool(
        re.search(_person_kw + r'[^。；\n]{0,60}' + _si_kw, blob)
        or re.search(_si_kw + r'[^。；\n]{0,60}' + _person_kw, blob)
    )
    si_months = grab(r'(?:近|最近)\s*([一二三四五六七八九十\d]{1,3})\s*个月'
                     r'[^。；\n]{0,12}' + _si_kw)

    # 服务实施地点（前附表「服务实施地点」行）
    service_address = grab(r'服务实施地点[\t ]+([^\t\n]{2,40})')

    # 临时用地批文号（询比：淮资规临地复【2025】0043号 这类）
    batch_numbers = list(dict.fromkeys(re.findall(
        r'[一-龥]{2,6}[〔\[【(（]\d{4}[〕\]】)）]\d{2,4}号', blob)))

    # 报价评分口径（建议报价用：基准价满分 / 最低价法 / 其他综合评分）
    price_rule = ""
    if "低于等于评标基准价" in blob or "低于等于基准价" in blob:
        price_rule = "benchmark_full"
    elif "最低评标价法" in blob or "最低投标价法" in blob:
        price_rule = "lowest"

    return {
        "source": str(path),
        "project_name": project_name,
        "project_no": project_no,
        "purchaser": purchaser,
        "agency": agency,
        "service_address": service_address,
        "batch_numbers": batch_numbers,
        "budget_wan": budget,
        "max_price_wan": max_price,
        "qualifications": qualifications,
        "staff_min": staff_min,
        "scoring_points": scoring,
        "scope": scope,
        "format_chapter": fmt_chapter,
        "format_sections": fmt_sections,
        "no_consortium": no_consortium,
        "sme_required": sme_required,
        "duration_days": duration_days,
        "duration_months": duration_months,
        "work_items": work_items,
        "plots": plots,
        "budget_yuan": budget_yuan,
        "max_price_yuan": max_yuan,
        "bid_format": bid_format,
        "price_rule": price_rule,
        "format_titles": format_titles,
        "households": households,
        "unit_price": unit_price,
        "commerce": commerce,
        "qual_files": qual_files,
        "sme_industry": _sme_industry,
        "credit_pledge": credit_pledge,
        "personnel_social_insurance": personnel_si,
        "social_insurance_months": si_months,
        "text_chars": len(text),
    }


# ═══════════════════════════════════════════════════════════════
# 人员抽取（满足招标最低配置，随机）
# ═══════════════════════════════════════════════════════════════
_SENIOR = ("正高级", "高级")
_MIDDLE = ("中级", "工程师")


def _title_rank(title: str) -> int:
    t = str(title or "")
    if "正高级" in t:
        return 3
    if "高级" in t:
        return 2
    if "中级" in t or t.strip() == "工程师":
        return 1
    return 0


def _major_ok(major: str, fields=("土地", "测绘", "规划", "国土", "地理")) -> bool:
    return any(k in str(major or "") for k in fields)


def select_bid_personnel(*, company_id: int = 1, leader: int = 1, members: int = 3,
                         seed: Optional[int] = None,
                         require_insured: bool = False) -> List[Dict[str, Any]]:
    """按招标最低配置从公司人员库**随机**抽人。

    负责人：土地类/测绘类高级及以上优先，其次任意高级；
    组员：土地/测绘/规划/国土相关专业，职称不限（优先中级及以上）。

    🔴 `require_insured`：招标文件要求拟派人员提供社保时，只从**最近一个月社保
       参保名单**里的人里抽（外部人员/未参保的人就不能上）；招标文件没要求时
       外部人员偶尔可用。
    """
    import json
    import sqlite3
    from pathlib import Path as _P

    db = _P(__file__).resolve().parents[2] / "data" / "knowledge_base.db"
    conn = sqlite3.connect(str(db))
    # 老库可能还没跑人员库迁移（没有 cert_no 列）：按列存在与否取，别让投标停在这
    _cols = {r[1] for r in conn.execute("PRAGMA table_info(company_personnel)")}
    _cert_col = "cert_no" if "cert_no" in _cols else "''"
    _id_col = "id_no" if "id_no" in _cols else "''"
    _tel_col = "phone" if "phone" in _cols else "''"
    # 离职人员（is_active=0）一律不进投标人员表（业主 2026-11-11）
    _active_sql = " and coalesce(is_active,1)=1" if "is_active" in _cols else ""
    rows = conn.execute(
        f"select id,name,gender,age,education,major,title,certs,{_cert_col},"
        f"{_id_col},{_tel_col} "
        "from company_personnel "
        f"where company_id=?{_active_sql} order by sort_order,id", (company_id,)).fetchall()
    try:
        # 只有「职称/注册测绘师/执业资格」证书图片才算投标可用材料；
        # 培训证书不能拿来凑投标人员（业主/专家 2026-11-11）。
        _img_ids = {r[0] for r in conn.execute(
            "select distinct personnel_id from company_personnel_images "
            "where category in ('注册测绘师','职称证书','执业资格证书')")}
    except Exception:
        _img_ids = set()
    # 最近一个月的参保名单（招标要求人员社保时用来筛人）
    _insured: set = set()
    _latest_period = ""
    if require_insured:
        try:
            _r = conn.execute(
                "select max(period) from company_social_insurance "
                "where company_id=?", (company_id,)).fetchone()
            _latest_period = str((_r[0] if _r else "") or "")
            if _latest_period:
                _insured = {str(x[0]) for x in conn.execute(
                    "select distinct person_name from company_social_insurance "
                    "where company_id=? and period=? and person_name<>''",
                    (company_id, _latest_period))}
        except Exception:
            pass
    conn.close()
    people = [{"id": r[0], "name": r[1], "gender": r[2], "age": r[3], "education": r[4],
               "major": r[5], "title": r[6],
               "certs": json.loads(r[7] or "[]") if r[7] else [],
               "cert_no": r[8] or "", "id_no": r[9] or "", "phone": r[10] or "",
               "has_img": r[0] in _img_ids}
              for r in rows if r[1]]
    # 人员库有同名重复（导入过两次）：保留有证书编号/证书图片的那条
    _best = {}
    for p in people:
        k = p["name"]
        score = (bool(p.get("has_img")), bool(p.get("cert_no")))
        if k not in _best or score > _best[k][0]:
            _best[k] = (score, p)
    people = [v for _sc, v in _best.values()]
    if require_insured and _insured:
        people = [p for p in people if p["name"] in _insured]
    rng = random.Random(seed)

    # 负责人评分只认「土地类或测绘类」高级职称（规划类只算组员分）
    seniors = [p for p in people if _title_rank(p["title"]) >= 2
               and any(k in str(p["major"] or "") for k in ("土地", "测绘"))]
    if not seniors:
        seniors = [p for p in people if _title_rank(p["title"]) >= 2]
    leaders = seniors or [p for p in people
                          if any(k in str(p["major"] or "") for k in ("土地", "测绘"))]
    if not leaders:
        return []
    # 负责人优先带注册测绘师（评审加分/资格要求里常点名）；同等条件下优先
    # **证书图片齐全**的（投标文件要贴职称/注册测绘师证书，缺图就得空着）。
    _cert_leaders = [p for p in leaders
                     if any("注册测绘师" in str(c) for c in (p.get("certs") or []))]
    _cert_leaders = ([p for p in _cert_leaders if p.get("has_img")]
                     or _cert_leaders)
    chosen = [rng.choice(_cert_leaders or leaders)]
    chosen[0]["role"] = "项目负责人"

    pool = [p for p in people if p not in chosen]
    mid = [p for p in pool if _title_rank(p["title"]) >= 1 and _major_ok(p["major"])]
    # 组员优先「本专业 + 有证书图片/身份证号」的（投标文件要贴证书、社保材料）；
    # 🔴 先只要**有证书图片**的人：只补了身份证号的人（法人、行政）不能被抽成组员
    #    去顶替持证人员（实测 2026-11-11：陈春补了身份证号后把刘倩挤掉了）。
    _rich = [p for p in pool if _major_ok(p["major"]) and p.get("has_img")]
    if len(_rich) < members:
        _rich = [p for p in pool if _major_ok(p["major"])
                 and (p.get("has_img") or p.get("id_no"))]
    pool = (_rich if len(_rich) >= members
            else mid or [p for p in pool if _major_ok(p["major"])] or pool)
    pool = sorted(pool, key=lambda x: (not x.get("has_img"),
                                       not bool(x.get("id_no")),
                                       -_title_rank(x["title"])))
    _top = pool[:members + 1]
    for p in rng.sample(_top, min(members, len(_top))):
        p = dict(p)
        p["role"] = "项目组成员"
        chosen.append(p)
    # 组员按“高级→中级→其他”排序，便于阅读（不改变抽取结果）
    tail = sorted(chosen[1:], key=lambda p: -_title_rank(p["title"]))
    return [chosen[0]] + tail
