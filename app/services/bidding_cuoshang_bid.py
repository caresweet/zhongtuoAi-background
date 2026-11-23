# -*- coding: utf-8 -*-
"""竞争性磋商响应文件（政府采购）——按《磋商文件》第二章「响应文件格式」出件。

与询比（投选）件、公开招标件的区别：
  · 响应文件结构只有四部分：磋商函 / 资格证明文件 / 人员配备表 / 其它资料；
    「示范格式」是采购人给的填空模板，填好的内容直接进「资格证明文件」，
    不再单独成章。
  · 报价是**单价**（元/亩，分报省/报市），不是总价；磋商函里必须同时写
    大写单价和报省、报市两个小写单价。
  · 评分表把技术方案拆成 6 项（工作方案/技术方案/质量/重难点/进度/安全保密），
    技术方案章按这 6 项成节，评委按表找分。

资料全部取公司档案（营业执照、资质证书、体系认证、业绩合同、设备照片、
人员职称证书、社保名单），不编造。
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.bidding_bid_builder import (
    _ASSET_COMPANY,
    _add_toc,
    _asset_block,
    _asset_images,
    _asset_texts,
    _auth_fields,
    _h,
    _img,
    _p,
    _person_images,
    _plan_p,
    _price_text,
    _set_font,
    _social_insurance_images,
    _stamp_toc,
    _table,
    _upper_yuan,
    company_name,
)
from app.services.bidding_tender import parse_tender, select_bid_personnel
from app.services.bidding_style import load_bidding_style
from app.services.bidding_format_profile import merged as _merged_profile

logger = logging.getLogger(__name__)

_ALIGN = {"center": 1, "start": 0, "left": 0, "end": 2, "right": 2,
          "both": 3, "justify": 3, "distribute": 4}


def _line(p, *, align=None, first_chars=None, first_pt=None, size=12,
          exact=None, multiple=None, before=None, after=None, left_pt=None,
          keep=False, outdent=False):
    """段落级版式：对齐 / 首行缩进 / 行距，数值全部来自招标文件格式章。"""
    from docx.shared import Cm, Pt
    pf = p.paragraph_format
    if align:
        p.alignment = _ALIGN.get(str(align), 0)
    if first_chars:
        pf.first_line_indent = Pt(float(first_chars) * float(size))
    elif first_pt:
        pf.first_line_indent = Pt(float(first_pt))
    if left_pt:
        pf.left_indent = Pt(float(left_pt))
    if exact:
        pf.line_spacing = Pt(float(exact))
    elif multiple:
        pf.line_spacing = float(multiple)
    pf.space_before = Pt(float(before or 0))
    pf.space_after = Pt(float(after or 0))
    if keep:
        pf.keep_with_next = True
    return p


def _fmt_of(profile: Dict[str, Any], role: str, no: str = "") -> Dict[str, Any]:
    """章节大标题的版式（按招标文件里的章节序号取；四/五缩进各不相同）。"""
    from app.services.bidding_format_profile import role_fmt
    if role == "h1":
        # 只取该序号自己量到的值；缺字体/字号时才回落到「一、」或默认，
        # 不能把「一、」的居中/行距带给「四、」「五、」（它们的缩进方式不同）
        base = dict((profile.get("h1_by_no") or {}).get(no) or {})
        first = profile.get("h1") or {}
        for k in ("font", "size", "bold"):
            if base.get(k) is None and first.get(k) is not None:
                base[k] = first[k]
        base.setdefault("font", "宋体")
        base.setdefault("size", 14)
        base.setdefault("bold", True)
        return base
    return role_fmt(profile, "", "plain")


def _styled(doc, profile: Dict[str, Any], role: str = "", *, no: str = "",
            section: str = "", kind: str = "plain", form: str = "",
            style: str = "", text: str = "", size=None, bold=None,
            font=None, align=None, first_chars=None, first_pt=None,
            exact=None, multiple=None, before=None, after=None,
            keep=False) -> Any:
    """按招标文件量到的版式建段；调用方不再写死字号/缩进/行距。"""
    from app.services.bidding_format_profile import role_fmt
    if section or form:
        f = role_fmt(profile, section, kind, form_no=form)
    else:
        f = _fmt_of(profile, role or "body", no)
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _size = float(size or f.get("size") or 12)
    _line(p, align=align or f.get("align"),
          first_chars=(first_chars if first_chars is not None
                       else f.get("first_line_chars")),
          first_pt=(first_pt if first_pt is not None else f.get("first_line_pt")),
          size=_size,
          exact=(exact if exact is not None else f.get("line_exact_pt")),
          multiple=(multiple if multiple is not None else f.get("line_multiple")),
          before=before, after=after, keep=keep)
    if text:
        r = p.add_run(text)
        _set_font(r, name=font or f.get("font") or "宋体", size=_size,
                  bold=(f.get("bold", False) if bold is None else bold))
        from docx.shared import RGBColor
        r.font.color.rgb = RGBColor(0, 0, 0)
    return p


def _add_r(p, text, *, size=12, bold=False, underline=False, font="宋体"):
    r = p.add_run(text)
    _set_font(r, name=font, size=size, bold=bold)
    if underline:
        r.underline = True
    return r



# ═══════════════════════════════════════════════════════════════
# 小工具
# ═══════════════════════════════════════════════════════════════
_CN_YEAR = {"0": "〇", "1": "一", "2": "二", "3": "三", "4": "四",
            "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}


def _cn_year(y: int) -> str:
    return "".join(_CN_YEAR.get(ch, ch) for ch in str(y))


_CN_SECTION_NO = {"一": "1", "二": "2", "三": "3", "四": "4",
                  "五": "5", "六": "6", "七": "7", "八": "8",
                  "九": "9", "十": "10"}


def _no_arabic(no: str) -> str:
    return _CN_SECTION_NO.get(str(no or "").strip(), str(no or ""))


def _cn_month(m: int) -> str:
    if m == 10:
        return "十"
    if m == 11:
        return "十一"
    if m == 12:
        return "十二"
    return _CN_YEAR.get(str(m), str(m))


def _today(en: date) -> str:
    return f"{en.year}年{en.month}月{en.day}日"


def _unit_big(v: float) -> str:
    """330 → 叁佰叁拾元/亩。"""
    return _upper_yuan(int(round(float(v)))).replace("整", "") + "/亩"


def _add_page_footer(doc) -> None:
    """页脚居中页码（封面不显示）；目录页码与页脚同一口径。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    sec = doc.sections[0]
    sec.different_first_page_header_footer = True
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    _set_font(run, name="宋体", size=10.5)
    for tag, attrs, text in (
            ("w:fldChar", {"w:fldCharType": "begin"}, None),
            ("w:instrText", {"xml:space": "preserve"}, " PAGE "),
            ("w:fldChar", {"w:fldCharType": "end"}, None)):
        el = OxmlElement(tag)
        for k, v in attrs.items():
            el.set(qn(k), v)
        if text is not None:
            el.text = text
        run._r.append(el)


def _bump_toc_pages(path: Path, titles: List[str], style: dict) -> int:
    """目录页码 +1：`_stamp_toc` 返回的是 0 起的 PDF 页序，页脚从 1 开始印。"""
    from docx import Document
    toc = (style or {}).get("toc") or {}
    d = Document(str(path))
    n = 0
    for p in d.paragraphs:
        t = p.text or ""
        for title in titles:
            m = re.match(re.escape(title) + r'\t(\d+)$', t)
            if not m:
                continue
            for r in list(p.runs):
                r._element.getparent().remove(r._element)
            r = p.add_run(f"{title}\t{int(m.group(1)) + 1}")
            _set_font(r, name=toc.get("entry_font") or "宋体",
                      size=toc.get("entry_size") or 14, bold=False)
            n += 1
    if n:
        d.save(str(path))
    return n


def _person_rows(people: List[dict]) -> List[List[str]]:
    """默认 9 列（磋商文件第二章人员配备表）。实际列名由格式章 spec 决定。"""
    return _person_rows_for(
        ["序号", "姓名", "性别", "年龄", "学历", "专业", "职称",
         "相关证书", "拟任岗位"], people)


def _person_rows_for(headers: List[str], people: List[dict]) -> List[List[str]]:
    """按招标文件表格的列名逐列取数（不同项目列名/列数不一样）。"""
    def cell(kind: str, i: int, p: dict) -> str:
        if kind == "seq":
            return str(i)
        if kind == "name":
            return str(p.get("name") or "")
        if kind == "gender":
            return str(p.get("gender") or "")
        if kind == "age":
            return str(p.get("age") or "")
        if kind == "education":
            return str(p.get("education") or "")
        if kind == "major":
            return str(p.get("major") or "")
        if kind == "title":
            return str(p.get("title") or "")
        if kind == "certs":
            certs = [c for c in (p.get("certs") or []) if str(c).startswith("注册")]
            return "、".join(dict.fromkeys(certs)) or "—"
        if kind == "role":
            return str(p.get("role") or "")
        if kind == "social":
            return "已缴纳"
        return "—"

    rows = []
    for i, p in enumerate(people, 1):
        row = []
        for h in headers:
            t = str(h or "")
            if "序号" in t:
                row.append(cell("seq", i, p))
            elif "姓名" in t:
                row.append(cell("name", i, p))
            elif "性别" in t:
                row.append(cell("gender", i, p))
            elif "年龄" in t:
                row.append(cell("age", i, p))
            elif "学历" in t:
                row.append(cell("education", i, p))
            elif "专业" in t:
                row.append(cell("major", i, p))
            elif "职称" in t:
                row.append(cell("title", i, p))
            elif "证书" in t:
                row.append(cell("certs", i, p))
            elif any(k in t for k in ("岗位", "职务", "角色", "分工")):
                row.append(cell("role", i, p))
            elif "社保" in t:
                row.append(cell("social", i, p))
            else:
                row.append("—")
        rows.append(row)
    return rows


def _person_col_widths(headers: List[str]) -> List[float]:
    """人员表列宽：按列名给权重，总和固定 15cm。"""
    weights = []
    for h in headers:
        t = str(h or "")
        if "序号" in t:
            weights.append(1.1)
        elif "姓名" in t:
            weights.append(1.7)
        elif "性别" in t:
            weights.append(1.1)
        elif "年龄" in t:
            weights.append(1.1)
        elif "学历" in t:
            weights.append(1.6)
        elif "专业" in t:
            weights.append(1.7)
        elif "职称" in t:
            weights.append(2.2)
        elif "证书" in t:
            weights.append(2.3)
        elif any(k in t for k in ("岗位", "职务", "角色", "分工")):
            weights.append(2.2)
        else:
            weights.append(1.8)
    scale = 15.0 / sum(weights) if sum(weights) else 1.0
    return [round(w * scale, 2) for w in weights]


def _perf_rows(texts: Dict[str, str]) -> List[List[str]]:
    """公司档案「类似项目业绩」→ [序号, 项目名称, 类型, 合同签订时间, 证明材料]。"""
    src = str(texts.get("类似项目业绩") or "")
    rows: List[List[str]] = []
    for line in src.splitlines():
        m = re.match(r'\s*\d+[.、]\s*(.+?)；类型：(.+?)；合同签订时间：(.+?)。?$',
                     line.strip())
        if not m:
            continue
        name, typ, when = (m.group(1).strip(), m.group(2).strip(),
                           m.group(3).strip())
        # 评分只认 2023-01-01 以来的征地报批类业绩
        if "征地" not in typ and "报批" not in typ:
            continue
        rows.append([str(len(rows) + 1), name, typ, when, "合同复印件加盖公章"])
    return rows


def _equip_rows(texts: Dict[str, str]) -> List[List[str]]:
    """设备清单 → 评分口径的 7 类设备覆盖表（有就写，没有写「—」）。"""
    src = str(texts.get("公司主要设备及软件清单") or "")
    got = {k: (k in src) for k in
           ("全站仪", "RTK", "无人机", "CASS", "水位仪", "绘图仪", "测高仪")}
    # RTK 就是 GNSS 接收机，评分项写的是「GNSS 接收机」
    got["GNSS接收机"] = got.pop("RTK")
    names = {
        "全站仪": "全站仪（南方 NTS 系列）",
        "GNSS接收机": "GNSS 接收机（RTK）",
        "无人机": "无人机（大疆精灵 4RTK）",
        "CASS": "南方 CASS 成图软件",
        "水位仪": "水位仪",
        "绘图仪": "大幅面绘图仪",
        "测高仪": "测高仪",
    }
    rows = []
    for k, label in names.items():
        rows.append([str(len(rows) + 1), label,
                     "有" if got.get(k) else "—",
                     "自有（发票/采购合同）" if got.get(k) else "—"])
    return rows


def _plan_facts(tender: dict, prices: Dict[str, float]) -> str:
    items = "；".join(
        f"{w.get('no')} {w.get('name')}" for w in (tender.get("work_items") or []))
    return (
        f"项目名称：{tender.get('project_name')}\n"
        f"项目编号：{tender.get('project_no')}\n"
        f"采购人：{tender.get('purchaser')}\n"
        f"服务地点：淮安市洪泽区（具体由采购人指定）\n"
        f"服务期：12 个月（从合同签订至取得上级部门批复）\n"
        f"预算/最高限价：报省 {prices.get('max_province', '')} 元/亩、"
        f"报市 {prices.get('max_city', '')} 元/亩\n"
        f"本次报价：报省 {prices.get('province', '')} 元/亩、"
        f"报市 {prices.get('city', '')} 元/亩\n"
        f"工作内容：{items}\n"
        "提交成果：建设项目「一书三信息」及用地报批组卷材料、征前批中批后档案、"
        "勘测定界材料、分户测绘成果、征地批次 ArcGIS 图层、电子稿、材料归档。\n"
        "计价方式：合同单价×实际工作量，按批次结算。\n"
    )


# ═══════════════════════════════════════════════════════════════
# 技术方案（50 分）
# ═══════════════════════════════════════════════════════════════
_SECTIONS: List[Tuple[str, List[str]]] = [
    ("（一）工作方案", [
        "政策依据与制度背景：土地管理法及实施条例、江苏省土地管理条例、"
        "苏自然资函〔2020〕442 号，以及省、市建设用地报批的组卷要求",
        "项目概况与工作边界：洪泽区 2026 年批次、11 个报批环节、8 项提交成果、"
        "12 个月服务期、按亩计价与按批次结算",
        "采购人需求分析：时限、成果质量、程序留痕、镇村协调、档案归档五个维度",
        "总体工作思路：一次踏勘摸清底数、一套台账管到底、分批次组卷报批、"
        "批后归档闭环",
        "工作目标与承诺：材料一次受理、不因缺项退回、成果可追溯、按期组卷",
        "项目组织机构与岗位职责：项目负责人、技术负责人、外业组、内业组、"
        "质检组、外联与档案岗的分工和到场安排",
    ]),
    ("（二）技术方案", [
        "征地范围红线制作：用地红线与已征地红线、国土空间规划、永久基本农田、"
        "生态红线、生态管控区、林地湿地等图层的拓扑与核查",
        "控制测量与勘测定界：GNSS 静态/RTK 布点、全站仪界址点测量、精度指标、"
        "CASS 成图与面积汇总",
        "地类权属调查与分户测绘：三调及年度变更调查地类认定、权属调查到组到户、"
        "农户面积表、分户表、组级确认表",
        "现状调查与补偿安置方案测算：房屋、附着物、青苗调查，区片综合地价与"
        "预存款测算，地类变更证明",
        "公告、登记、听证与协议签订：拟征地公告、补偿安置方案公告的张贴、拍照、"
        "送达、记录，补偿登记和协议收集",
        "组卷报批与归档：一书三信息、勘测定界材料、分户成果、ArcGIS 图层、"
        "电子稿、征前批中批后档案归档",
    ]),
    ("（三）质量保障措施", [
        "质量目标与质量管理体系：三级校审、岗位质量责任、质量记录",
        "外业质量控制：控制点复测、界址点检核、地类认定复核、权属签字确认",
        "内业与成果一致性检查：红线、批复、勘测定界、分户表四套数据核对",
        "程序性材料质量控制：公告取证、送达证明、登记表、协议的完整性检查",
        "组卷清单化核对：按省市报批清单逐项勾选、双人复核、项目负责人签批",
        "问题闭环与考核：问题台账、整改销号、周例会通报、成果归档质量检查",
    ]),
    ("（四）重点与难点分析及对策", [
        "地类认定与变更：三调地类与现状不一致、批复地类差异的成因与处理",
        "权属争议与历史遗留问题：组与组、户与户边界不清，历史征地档案缺失",
        "永久基本农田、生态管控区和林地约束：红线压占的调整、论证与补充手续",
        "公告送达与取证规范：外出务工户、村组公开栏张贴、照片和送达证明",
        "多批次时限压力与镇村部门协调：批次滚动、倒排工期、联络机制",
        "补偿标准执行与群众工作：区片综合地价、附着物标准、异议处理",
    ]),
    ("（五）进度安排", [
        "总体进度安排：12 个月服务期、批次滚动、阶段划分与衔接",
        "单批次节点控制：T+3 踏勘、T+10 定界、T+20 方案、T+30 组卷",
        "关键路径与里程碑：红线、定界、公告、组卷、批复五个里程碑",
        "资源投入与赶工能力：驻场人员、两班倒、设备调配、公司后台支持",
        "周报例会与节点考核：周报内容、例会频次、滞后预警与考核",
        "风险与赶工预案：雨季、农忙、审批延迟、镇村协调不畅、人员变动的应对",
    ]),
    ("（六）安全保密保障措施", [
        "安全保密体系与责任：项目负责人第一责任、安全保密员、承诺书与交底",
        "现场作业安全：村组道路、沟渠、危旧房屋、高温雨雪天气的防护",
        "交通与仪器安全：车辆日检、仪器保管领用、RTK 和无人机作业安全",
        "涉密测绘成果与数据管理：专机专盘、加密存储、禁止公网传输",
        "人员保密与成果交付销毁：保密协议、成果交接、留存与销毁记录",
        "文明作业、群众关系与应急处置：尊重风俗、避免冲突、突发事件报告",
    ]),
]


async def _write_cuoshang_plan(tender: dict, prices: Dict[str, float],
                               llm=None) -> List[str]:
    """LLM 按 6 个评分项、每项分两段写技术方案。

    一项一次生成容易写满 2500 字就收尾（50 分的技术标只有十几页，评委看不出
    工作量）；拆成「前半段/后半段」两次生成，每段 2500 字以上，六项合计 3 万字
    左右，每条评分要点都有对应段落。
    """
    try:
        from app.services.llm_service import LLMService
        llm = llm or LLMService()
    except Exception:
        return [""] * len(_SECTIONS)
    facts = _plan_facts(tender, prices)
    system = (
        "你是政府采购土地征收报批技术服务项目的投标文件编写专家，替投标人"
        "（江苏众拓测绘有限公司）写竞争性磋商响应文件的技术方案。文风平实专业，"
        "内容落在本项目事实上，不编造地名、数字、批文号和奖项。"
    )
    sem = asyncio.Semaphore(4)

    async def _one(title: str, subpoints: List[str], part: int) -> str:
        half = len(subpoints) // 2
        subset = subpoints[:half] if part == 1 else subpoints[half:]
        where = "前半部分" if part == 1 else "后半部分"
        user = (
            f"为下面的竞争性磋商项目撰写技术方案的「{title}」{where}。\n\n"
            f"{facts}\n"
            f"本部分重点写：{'；'.join(subset)}。\n"
            "写作要求：本部分不少于 2500 字，分 5～7 个自然段；第一段总述，"
            "其余段落按「做什么—怎么做—达到什么标准—谁负责—什么时间完成」展开，"
            "写具体方法、规范名称、精度指标、表单名称和岗位，不要泛泛而谈；"
            "不用项目符号，步骤用「1）2）3）」；不出现「待补充」「以实际为准」"
            "等占位语；不要输出标题行和 Markdown 表格。"
        )
        async with sem:
            try:
                res = await asyncio.wait_for(
                    llm.chat_with_reasoning(
                        messages=[{"role": "user", "content": user}],
                        system=system, max_tokens=8000, temperature=0.35,
                        disable_thinking=True),
                    timeout=420)
                text = str((res or {}).get("content") or "").strip()
                if text and len(text) > 800 and "待补充" not in text:
                    return text
            except Exception as e:
                logger.warning(f"[磋商文件] 技术方案「{title}」第{part}段失败: {e}")
        return ""

    tasks = []
    for t, subs in _SECTIONS:
        tasks.append(_one(t, subs, 1))
        tasks.append(_one(t, subs, 2))
    pieces = await asyncio.gather(*tasks)
    # 每节两段合并（有一段失败就只用另一段，渲染时不足 300 字再走兜底）
    return ["\n\n".join(p for p in pieces[i * 2:i * 2 + 2] if p)
            for i in range(len(_SECTIONS))]


def _fallback_plan(tender: dict, prices: Dict[str, float]) -> List[str]:
    """LLM 不可用时的 6 节框架（事实来自磋商文件，不编数字）。"""
    name = tender.get("project_name") or "本项目"
    prov = prices.get("province", "")
    city = prices.get("city", "")
    return [
        f"{name}是洪泽区 2026 年度土地征收报批的综合技术服务，服务对象是"
        "淮安市自然资源和规划局洪泽分局，工作覆盖征地范围红线制作、实地调查与"
        "勘测定界、拟征地公告、现状调查、补偿安置方案、补偿登记、听证、协议签订"
        "和组卷报批全过程。政策依据分三个层面：法律层面是《土地管理法》《土地管理"
        "法实施条例》《江苏省土地管理条例》；程序层面是《江苏省自然资源厅关于做好"
        "当前土地征收前期工作的通知》（苏自然资函〔2020〕442 号）；操作层面是省、市"
        "建设用地报批的组卷清单和成果标准。\n\n"
        "总体思路是「一次踏勘摸清底数、一套台账管到底、分批次组卷报批、批后归档"
        "闭环」。进场后先核对批次清单和已批用地范围，把每一宗地的批文、红线、地类、"
        "权属、现状一次查清，形成项目台账；此后所有调查表、方案、公告、协议都以台账"
        "为底稿，避免同一宗地多套数据。批次按采购人下达的时序滚动推进，前一批组卷"
        "报批的同时启动下一批外业，不等、不压。\n\n"
        "工作目标是：报批材料一次通过受理，不因材料缺项退回；勘测定界成果与批复"
        "范围一致，面积、地类、权属可追溯；公告、登记、协议等程序性材料齐全可查；"
        f"按采购人要求的时间节点完成组卷。本次报价报省 {prov} 元/亩、报市 {city} 元/亩，"
        "按合同单价和实际工作量结算，报价已包含人员、交通、办公、专家论证、税费和"
        "售后服务等全部费用。",
        "技术路线按「红线—调查—方案—公告—组卷—归档」六段组织。红线制作以用地"
        "红线为底，叠加已征地红线、国土空间规划、永久基本农田、生态保护红线、生态"
        "管控区、林地湿地等图层，逐宗拓扑检查，形成征地范围红线并标注地块编号。\n\n"
        "实地调查与勘测定界同步开展。控制测量采用 GNSS 静态或 RTK 布设图根点，"
        "界址点用全站仪极坐标法施测，界址点相对邻近控制点中误差不超过 ±5 厘米；"
        "地类按第三次国土调查及最新年度变更调查认定，权属调查到组、签字到户；"
        "内业用 CASS 成图，输出勘测定界图、面积汇总表和界址点成果表，并与红线、"
        "批文逐项核对。\n\n"
        "现状调查会同镇（街道）逐户核实房屋、附着物和青苗，制作农户土地面积表、"
        "分户表、组级确认表和现场调查记录表，进村签章、签字并拍照取证。补偿安置"
        "方案按现行区片综合地价和地上附着物标准测算，制作征地预存款测算表；涉及"
        "地类变更的，配合镇（街道）出具变更证明。\n\n"
        "公告环节按「制作—张贴—拍照—送达—记录」五步留痕：拟征地公告和补偿安置"
        "方案公告均由项目组制作，协助张贴，远近视距各拍一张，留存送达证明和张贴"
        "记录表并签章。补偿登记、听证和协议签订按采购人安排配合完成。\n\n"
        "组卷阶段按「一书三信息」和省市报批清单逐项组卷：农用地转用方案、地类认定"
        "基本信息、土地征收信息和报件基本信息，附勘测定界材料、分户测绘成果、征地"
        "批次 ArcGIS 图层和电子稿；报批前由项目负责人按清单自检，报批后跟踪补正，"
        "批复后整理征前、批中、批后全部档案并归档。",
        "质量管理按「谁作业、谁负责，谁校审、谁签字」执行三级校审：作业组自检，"
        "技术负责人复检，项目负责人终审。红线、批复、勘测定界三套数据的一致性由"
        "技术负责人逐宗核对，面积差异超过规定限差的必须查明原因并留记录。\n\n"
        "外业成果实行「当日采集、次日整理、三日成表」：界址点、地类、权属数据当天"
        "回传，内业次日成图，第三天出面积汇总表和分户表，避免数据积压后集中出错。"
        "分户面积表与组级确认表由两名作业人员交叉核对，签字确认后才能用于方案测算。\n\n"
        "程序性材料按清单管理：每一宗地的公告照片、送达证明、张贴记录、登记表、"
        "协议逐项编号入盒，缺一项就在清单上标注并限期补齐。组卷前按省市报批清单"
        "逐条勾选，项目负责人签字后方可报送。\n\n"
        "对采购人反馈和主管部门补正意见建立问题台账，明确责任人和完成时限，整改"
        "完成后由技术负责人复核销号；同类问题在周例会上通报，防止重复发生。成果"
        "归档按批次、宗地、材料类型三级目录整理，纸质与电子稿同步归档。",
        "本项目的重点难点集中在六处。第一是地类认定。临时用地、设施农用地与建设"
        "用地交织，三调地类与现状可能不一致，对策是以最新年度变更调查为准，逐宗"
        "比对批复地类，需变更的提前配合镇（街道）出具证明。\n\n"
        "第二是权属争议和历史遗留问题。部分地块存在组与组、户与户边界不清，对策是"
        "先查历史登记和征地档案，再组织现场指界，争议地块单独建册，会同镇村形成"
        "书面确认后再确权。\n\n"
        "第三是永久基本农田和生态管控区约束。红线压占基本农田或生态红线的，按"
        "规定调整用地范围或补充论证，不擅自变更；涉及林地的同步办理使用林地手续。\n\n"
        "第四是公告送达与取证规范。公告必须张贴到村组公开栏并拍照留痕，外出务工"
        "户要留存送达证明，避免因程序瑕疵被退回。对策是建立公告取证清单，逐宗"
        "核对照片、送达证明和签章。\n\n"
        "第五是多批次时限压力。对策是批次台账和倒排工期，前一批组卷时下一批外业"
        "同步启动，关键节点每周核对一次。\n\n"
        "第六是镇村与部门协调。对策是明确项目负责人为唯一对接人，建立镇村联络"
        "清单和问题清单，重要事项书面确认，避免口头交办失联。",
        "进度按 12 个月服务期、批次滚动编排。合同签订后 3 个工作日内成立项目组并"
        "进场，5 个工作日内完成资料收集和批次清单核对，10 个工作日内完成首宗地"
        "红线制作和现场踏勘。\n\n"
        "每批次按「T+3 踏勘、T+10 定界、T+20 方案、T+30 组卷」控制：进场后 3 日内"
        "完成踏勘，10 日内提交勘测定界初步成果，20 日内完成现状调查和补偿安置方案"
        "测算，30 日内完成组卷报送。报批后按主管部门要求跟踪补正，批复后 10 日内"
        "完成归档。\n\n"
        "保障措施：项目组驻场办公，项目负责人和技术负责人常驻；每周向采购人报送"
        "周报，每两周召开一次例会核对节点；关键节点前 5 日启动预警，滞后 3 日"
        "启动赶工。雨季和农忙影响外业的，提前调整作业顺序，把内业整理和方案编制"
        "前置；审批延迟的，保持与主管部门的日常沟通，材料补正不过夜。",
        "安全保密按「人、机、数、场」四条线管理。人员方面，进场前完成安全交底和"
        "保密承诺，明确项目负责人为第一责任人；外业人员配备反光背心、安全帽和"
        "急救包，禁止单人进入偏僻地块。\n\n"
        "设备方面，全站仪、GNSS 接收机和无人机由专人保管、登记领用，作业前检查"
        "电池和支架，无人机飞行避开高压线、林区和人员密集区，严格遵守空域规定。\n\n"
        "数据方面，征地红线、勘测定界成果和权属资料按涉密测绘成果管理，只在项目"
        "专用电脑和加密移动硬盘存储，不上传公共网盘、不通过个人微信传输；成果"
        "交付后按采购人要求移交或销毁，留存交接记录。\n\n"
        "现场方面，作业车辆停放在安全位置，仪器不离开视线；进入村组尊重当地风俗，"
        "遇到群众阻工不争执，及时报告项目负责人和镇村协调。发生人身伤害、设备"
        "损坏或数据泄露的，1 小时内报告采购人并启动应急处置。",
    ]


async def _write_generic_plan(tender: dict, llm=None) -> List[tuple]:
    """格式不固定时，按招标范围/评分要点让模型自拟 5～6 节技术方案。

    返回 [(小节标题, 正文), ...]；失败返回空列表，由调用方用项目概况兜底。
    """
    try:
        from app.services.llm_service import LLMService
        llm = llm or LLMService()
    except Exception:
        return []
    facts = (
        f"项目名称：{tender.get('project_name')}\n"
        f"采购人：{tender.get('purchaser')}\n"
        f"采购范围：{str(tender.get('scope') or '')[:1500]}\n"
        f"分项工作：{'；'.join(str(w.get('name') or '') for w in (tender.get('work_items') or []))}\n"
        f"评分办法：{str(tender.get('scoring_points') or '')[:1500]}\n"
    )
    prompt = (
        f"为下面的招标项目撰写技术方案。\n\n{facts}\n"
        "请按评标要点自拟 5～6 个小节，输出格式严格为：\n"
        "## 小节标题\n正文……\n"
        "每节正文不少于 1200 字，写做法、标准、责任人和时间；不要输出其它说明文字。"
    )
    try:
        res = await asyncio.wait_for(
            llm.chat_with_reasoning(
                messages=[{"role": "user", "content": prompt}],
                system="你是投标文件技术方案编写专家，内容必须落在项目事实上，不编造数字和批文号。",
                max_tokens=16000, temperature=0.35, disable_thinking=True),
            timeout=600)
        text = str((res or {}).get("content") or "")
    except Exception as e:
        logger.warning(f"[磋商文件] 通用技术方案生成失败: {e}")
        return []
    out = []
    for part in re.split(r'\n(?=##\s+)', text):
        m = re.match(r'##\s*(.+?)\n(.*)', part.strip(), re.S)
        if m and len(m.group(2).strip()) > 300:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out[:8]


def _plan_table_specs(tender: dict) -> Dict[int, tuple]:
    """技术方案每节配一张管理表（评委按表找做法、标准、责任人）。"""
    methods = {
        "制作征地范围红线": ("多图层叠加拓扑、逐宗核对，标注地块编号",
                       "征地范围红线图、图层数据"),
        "实地调查阶段": ("GNSS/RTK 布设图根点，全站仪界址点测量，地类权属调查",
                    "勘测定界图、面积汇总表、界址点成果表"),
        "前期审查阶段": ("制作项目明细表，配合内部会审", "项目明细表、会审意见"),
        "发布拟征地公告": ("制作、张贴、远近景拍照、送达证明、张贴记录签章",
                     "公告及取证材料"),
        "拟征收土地现状调查": ("入户调查房屋和附着物，会同镇村制作面积表、分户表、"
                       "组级确认表", "现状调查表、分户表、确认表"),
        "拟定征收补偿安置方案": ("区片综合地价测算、预存款测算，制作方案及公告",
                        "补偿安置方案、测算表"),
        "发布征地补偿安置方案公告": ("张贴、拍照、送达、记录签章", "公告及取证材料"),
        "征地补偿登记": ("会同镇村填写补偿登记表", "补偿登记表"),
        "征地补偿安置方案听证": ("协助组织听证、记录意见", "听证材料"),
        "签订补偿安置协议": ("会同镇街签订所有权、承包权、附着物补偿协议",
                      "补偿安置协议"),
        "组卷报批阶段": ("一书三信息组卷、清单自检、跟踪补正", "报批组卷、批复文件"),
    }
    items = tender.get("work_items") or []
    tech_rows = []
    for i, w in enumerate(items or [], 1):
        name = str(w.get("name") or "")
        m, out = methods.get(name, ("按规范作业、双人复核", "相应成果材料"))
        for key, (mm, oo) in methods.items():
            if key in name or name in key:
                m, out = mm, oo
                break
        tech_rows.append([str(i), name, m, out])
    if not tech_rows:
        tech_rows = [[str(i), k, v[0], v[1]]
                     for i, (k, v) in enumerate(methods.items(), 1)]
    return {
        0: ("表 4-1　项目组织机构与岗位职责",
            ["岗位", "人数", "主要职责"],
            [["项目负责人", "1", "履约第一责任人，对接采购人，审定方案和成果，签批组卷"],
             ["技术负责人", "1", "技术路线、成果校审、红线与定界一致性、技术问题处置"],
             ["外业组", "3", "踏勘、控制测量、界址点、地类权属调查、公告张贴取证"],
             ["内业组", "3", "CASS 成图、面积汇总、分户表、方案测算、组卷材料编制"],
             ["质检组", "2", "成果自检互检、清单核对、问题台账和整改验证"],
             ["外联与档案", "1", "镇村和部门联络、协议收集、档案整理归档"]],
            [3.0, 1.3, 10.7], ["center", "center", "left"]),
        1: ("表 4-2　报批工作环节、作业方法与成果",
            ["序号", "工作环节", "主要作业方法", "成果形式"],
            tech_rows[:11],
            [1.2, 3.8, 6.4, 3.6],
            ["center", "left", "left", "left"]),
        2: ("表 4-3　质量控制点与检查方式",
            ["控制点", "控制标准", "检查方式", "责任人"],
            [["控制点布设", "图根点密度和精度符合测量规范",
              "外业检查、复测记录", "技术负责人"],
             ["界址点测量", "相对邻近控制点中误差不超过 ±5 厘米",
              "全站仪检核、抽测", "外业组长"],
             ["地类认定", "与三调及最新年度变更调查一致",
              "逐宗比对、会审", "技术负责人"],
             ["权属调查", "到组到户、签字盖章齐全",
              "组级确认表核验", "外业组长"],
             ["分户面积", "分户合计＝组级＝勘界面积",
              "双人交叉核算", "内业组长"],
             ["红线与批复", "范围、面积、地类一致",
              "逐宗套合比对", "技术负责人"],
             ["公告取证", "远近景照片、送达证明、张贴记录齐全",
              "清单勾选检查", "质检员"],
             ["组卷材料", "清单齐全、签章完整",
              "双人复核、负责人签批", "项目负责人"]],
            [2.8, 6.0, 3.6, 2.6],
            ["center", "left", "left", "center"]),
        3: ("表 4-4　重点难点与应对措施",
            ["重点难点", "主要影响", "应对措施", "责任人"],
            [["地类认定与变更", "地类不符导致组卷退回",
              "以最新年度变更调查为准，逐宗比对批复地类，需变更的提前出具证明",
              "技术负责人"],
             ["权属争议", "指界不成、协议签不下来",
              "查历史登记和征地档案，组织现场指界，争议地块单独建册书面确认",
              "外业组长"],
             ["基本农田和生态管控", "红线压占，报批受阻",
              "按规划调整用地范围或补充论证，涉林地块同步办理使用林地手续",
              "项目负责人"],
             ["公告送达与取证", "程序瑕疵被认定无效",
              "建立公告取证清单，逐宗核对照片、送达证明和签章",
              "质检员"],
             ["多批次时限压力", "批次集中到期、组卷滞后",
              "批次台账和倒排工期，前一批组卷时下一批外业同步启动",
              "项目负责人"],
             ["镇村和部门协调", "交办事项失联、进度不可控",
              "项目负责人唯一对接，镇村联络清单和问题清单，重要事项书面确认",
              "外联岗"]],
            [3.4, 3.4, 5.8, 2.4],
            ["left", "left", "left", "center"]),
        4: ("表 4-5　总体进度计划与里程碑",
            ["阶段", "时间安排", "主要工作", "里程碑"],
            [["进场准备", "第 1 个月", "组建项目组、资料收集、批次清单核对",
              "工作大纲报审"],
             ["红线制作", "第 1—2 个月", "图层叠加、逐宗拓扑、红线上图",
              "红线成果确认"],
             ["勘测定界", "第 2—4 个月", "控制测量、界址点、地类权属调查",
              "勘界成果提交"],
             ["现状调查", "第 3—5 个月", "房屋附着物调查、分户表、确认表",
              "调查成果签章"],
             ["补偿方案", "第 4—6 个月", "补偿测算、方案编制和公告",
              "方案批复"],
             ["公告登记协议", "第 5—8 个月", "公告取证、补偿登记、听证、协议签订",
              "程序材料齐全"],
             ["组卷报批", "第 6—11 个月", "一书三信息组卷、报送、跟踪补正",
              "取得批复"],
             ["归档移交", "第 12 个月", "征前批中批后档案整理、电子稿移交",
              "归档清单签收"]],
            [2.8, 3.2, 6.2, 2.8],
            ["center", "center", "left", "center"]),
        5: ("表 4-6　安全保密风险与防控措施",
            ["风险点", "防控措施", "责任人"],
            [["现场人身安全", "安全帽、反光背心，两人同行，进场前风险告知",
              "外业组长"],
             ["交通与仪器", "车辆日检，仪器专人保管领用，无人机按规定空域飞行",
              "设备管理员"],
             ["涉密数据泄露", "专机专盘、加密存储，禁止公网网盘和个人微信传输",
              "安全保密员"],
             ["成果丢失损坏", "双备份、异地存储，交接登记",
              "内业组长"],
             ["群众冲突", "尊重当地风俗，镇村陪同，不单独入户",
              "项目负责人"],
             ["突发事件", "1 小时内报告采购人，启动应急联系表并留存处置记录",
              "项目负责人"]],
            [3.2, 8.8, 3.0],
            ["left", "left", "center"]),
    }


def _add_plan_table(doc, spec: tuple, *, caption_p=None, table=None) -> None:
    caption, headers, rows, widths, aligns = spec
    if caption_p is not None:
        caption_p(caption).paragraph_format.keep_with_next = True
    else:
        _p(doc, caption, size=12, bold=True, align="center", indent=False
           ).paragraph_format.keep_with_next = True
    if table is not None:
        table(headers, rows, widths, aligns)
    else:
        _table(doc, headers, rows, widths=widths, font_size=10.5, aligns=aligns)
    _p(doc, "", size=8, indent=False, space_after=0)


def _render_plan(doc, tender: dict, prices: Dict[str, float],
                 texts: Optional[List[str]], *, head=None, para=None,
                 table=None, caption_p=None) -> None:
    """技术方案 6 节：有 LLM 文本按节渲染，缺哪节用兜底文本补哪节。"""
    fallback = _fallback_plan(tender, prices)
    texts = list(texts or [])
    tables = _plan_table_specs(tender)
    head = head or (lambda t: _h(doc, t, 3, size=14, font="黑体"))
    para = para or (lambda t: _plan_p(doc, t))
    for i, (title, _subs) in enumerate(_SECTIONS):
        head(title)
        text = (texts[i] if i < len(texts) else "") or ""
        text = re.sub(r'^\s*#{1,4}\s+.*$', '', text, flags=re.M).strip()
        # 模型偶尔自己加「（四）……（后半部分）」这类小标题：删掉短行标题，
        # 否则正文里会多出一行和节标题重复的字
        paras = [x.strip() for x in text.split("\n") if x.strip()]
        paras = [x for x in paras
                 if not (re.match(r'^[（(][一二三四五六][）)]', x) and len(x) <= 40)]
        text = "\n".join(paras)
        if len(text) < 300:
            text = fallback[i]
        for _p_ in [x.strip() for x in text.split("\n") if x.strip()]:
            para(_p_)
        if i in tables:
            _add_plan_table(doc, tables[i], caption_p=caption_p, table=table)


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
async def build_cuoshang_bid_document(
    *, tender_path: str | Path, company_id: int, out_path: str | Path,
    unit_prices: Optional[Dict[str, float]] = None,
    price_wan: float = 0,
    report_kind: str = "响应文件",
    seed: Optional[int] = None,
    personnel: Optional[List[dict]] = None,
    use_llm: bool = True,
    agent_name: str = "",
) -> Path:
    """生成竞争性磋商响应文件。

    `unit_prices`：`{"province": 330, "city": 225}`（元/亩）。不传时按磋商文件
    最高限价填（等于不降价），并给出提示。
    """
    from docx import Document
    from docx.shared import Cm, Pt
    from app.config import settings as _settings

    style = load_bidding_style(_settings.STORAGE_DIR)
    # 磋商响应文件的目录要「目 录」+ 页码（公司体例里默认是“投标主要文件目录、
    # 不显示页码”，那是招标件版式）
    style = dict(style)
    style["toc"] = {**(style.get("toc") or {}), "title": "目　录",
                    "show_page_numbers": True}
    _ASSET_COMPANY.set(int(company_id))
    tender = parse_tender(tender_path)

    from app.services.company_service import load_company
    company = await load_company(company_id) or {}

    co_name = company_name(company_id) or "供应商"
    project = tender.get("project_name") or ""
    no = tender.get("project_no") or ""
    purchaser = tender.get("purchaser") or "采购人"
    agency = tender.get("agency") or ""
    cs = tender.get("cuoshang") or {}
    max_unit = tender.get("unit_prices") or {}
    prices = {
        "max_province": max_unit.get("province", ""),
        "max_city": max_unit.get("city", ""),
        "province": float((unit_prices or {}).get("province")
                          or max_unit.get("province") or 0),
        "city": float((unit_prices or {}).get("city")
                      or max_unit.get("city") or 0),
    }
    today = _today(date.today())
    cover_date = f"{_cn_year(date.today().year)}年{_cn_month(date.today().month)}月"

    # 人员：负责人取「土地/测绘高级 + 注册测绘师」优先，组员取职称最高的 5 人；
    # 磋商文件要求人员社保证明的，只从参保名单里选（未参保人员上表要扣分）
    _need_si = bool(tender.get("personnel_social_insurance")
                    or cs.get("personnel_insured"))
    people = personnel or select_bid_personnel(
        company_id=company_id, seed=seed, members=5,
        require_insured=_need_si)
    if people:
        from app.services.bidding_tender import _title_rank
        lead = people[0]
        lead["role"] = "项目负责人"
        rest = sorted(people[1:], key=lambda p: (-_title_rank(p.get("title")),
                                                 p.get("name") or ""))
        people = [lead] + rest
    texts = _asset_texts(co_name)
    af = _auth_fields(texts, company=company, agent_name=agent_name)
    agent = af.get("agent") or ""
    legal = af.get("legal") or ""
    address = af.get("address") or (company.get("address") or "")
    phone = company.get("office_phone") or af.get("office") or ""
    mobile = company.get("phone") or af.get("mobile") or ""

    doc = Document()
    sec = doc.sections[0]
    profile = _merged_profile((tender.get("format_spec") or {}).get("style"))
    page = profile.get("page") or {}
    sec.page_width, sec.page_height = (Cm(page.get("w_cm") or 21.0),
                                       Cm(page.get("h_cm") or 29.7))
    sec.top_margin = Cm(page.get("top_cm") or 2.2)
    sec.bottom_margin = Cm(page.get("bottom_cm") or 2.0)
    sec.left_margin = Cm(page.get("left_cm") or 2.5)
    sec.right_margin = Cm(page.get("right_cm") or 2.3)

    def _blank():
        _p(doc, "", size=12, indent=False, space_after=0)

    def _id_pair(name: str):
        imgs = _asset_images(f"{name} 身份证", limit=4)
        if not imgs:
            _p(doc, "（身份证复印件见公司档案原件）", size=10.5, indent=False)
            return
        from docx.enum.table import WD_TABLE_ALIGNMENT
        t = doc.add_table(rows=1, cols=2)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        w = (style.get("images") or {}).get("id_card_cm", 7.0)
        for i, (_nm, _data) in enumerate(imgs[:2]):
            cell = t.cell(0, i)
            cell.width = Cm(w)
            cp = cell.paragraphs[0]
            cp.alignment = 1
            cp.paragraph_format.space_after = 0
            try:
                cp.add_run().add_picture(io.BytesIO(_data), width=Cm(w))
            except Exception as e:
                logger.warning(f"[磋商文件] 身份证图插入失败: {e}")

    # ── 封面：字号/缩进/对齐取招标文件第二章封面的量测值 ──
    _cv = profile.get("cover") or {}

    def _cvf(key, default):
        return {**default, **{k: v for k, v in (_cv.get(key) or {}).items()
                              if v is not None}}

    for _ in range(3):
        _blank()
    _f = _cvf("copy", {"align": "right", "size": 22})
    _cp = doc.add_paragraph()
    _line(_cp, align=_f.get("align"), size=_f.get("size"),
          exact=_f.get("line_exact_pt"))
    _add_r(_cp, "【正本】", size=_f.get("size"), bold=_f.get("bold", False))
    _blank()
    _blank()
    for _key, _label, _val in (("name", "项目名称：", project),
                               ("no", "项目编号：", no),
                               ("supplier", "供应商：", co_name)):
        _f = _cvf(_key, {"size": 18, "bold": True, "first_line_chars": 5})
        _size = _f.get("size") or 18
        _chars = _f.get("first_line_chars") or 5
        _cp = doc.add_paragraph()
        # 值比模板留空长时会折行：用左缩进（而不是首行缩进），折行才对得齐
        _line(_cp, left_pt=_chars * _size, size=_size,
              exact=_f.get("line_exact_pt"))
        _add_r(_cp, _label, size=_size, bold=_f.get("bold", True))
        _add_r(_cp, _val, size=_size, bold=_f.get("bold", True),
               underline=True)
    for _ in range(5):
        _blank()
    _f = _cvf("date", {"size": 20, "first_line_chars": 6.82})
    _cp = doc.add_paragraph()
    _line(_cp, first_chars=_f.get("first_line_chars") or 6.82,
          size=_f.get("size") or 20, exact=_f.get("line_exact_pt"))
    _add_r(_cp, f"{_cn_year(date.today().year)}年", size=_f.get("size") or 20,
           bold=_f.get("bold", True))
    _add_r(_cp, "    月    日", size=_f.get("size") or 18,
           bold=_f.get("bold", True))
    doc.add_page_break()

    # ── 目录 ──
    _add_toc(doc, style)
    doc.add_page_break()

    # ── 章节结构：以招标文件自己写的「响应文件格式」为准 ──
    # 不同项目的章节名/顺序/人员表列名/要不要单独放示范格式都不一样，
    # `format_spec` 是 parse_tender 从格式章抽出来的（见 bidding_format_spec）。
    spec = tender.get("format_spec") or {}
    spec_secs = list(spec.get("sections") or [])
    if not spec_secs:
        # 解析不到格式章时退回常见四段式（仍尽量按磋商文件正文结构）
        spec_secs = [
            {"no": "一", "title": "磋商函", "kind": "letter"},
            {"no": "二", "title": "资格证明文件", "kind": "qualification"},
            {"no": "三", "title": "人员配备表", "kind": "personnel"},
            {"no": "四", "title": "其它资料", "kind": "other"},
        ]
    by_kind: Dict[str, Any] = {}
    for _s in spec_secs:
        by_kind.setdefault(_s.get("kind"), _s)
    qual_items = list((by_kind.get("qualification") or {}).get("items") or [])
    spec_forms = [f for f in (spec.get("forms") or []) if f]
    _forms_sec = by_kind.get("forms")
    forms_title = (f"{_forms_sec.get('no')}、{_forms_sec.get('title')}"
                   if _forms_sec else "示范格式")

    def _sec_title(_s: Dict[str, Any]) -> str:
        return f"{_s.get('no')}、{_s.get('title')}" if _s else ""

    def _head1(_s) -> Any:
        """章节标题：字号/对齐/行距照招标文件同一序号那一行（四、缩进 5 字，
        五、缩进 11.46 字），仍挂 Heading 1 供目录抓取。"""
        _no = str((_s or {}).get("no") or "")
        return _styled(doc, profile, "h1", no=_no, style="Heading 1",
                       text=_sec_title(_s), keep=True)

    def _para(text="", *, role="body", section="", kind="plain", form="",
              **kw):
        return _styled(doc, profile, role, section=section, kind=kind,
                       form=form, text=text, **kw)

    def _head2(text: str, *, section="四") -> Any:
        """四、下面的（1）（2）小节：字号/行距取该章正文，只加粗、不缩进。"""
        return _styled(doc, profile, section=section, kind="plain", text=text,
                       bold=True, first_chars=0, first_pt=0, keep=True)

    def render_letter(_s) -> Any:
        hp = _head1(_s)
        _unit_mode = prices["province"] > 0 or prices["city"] > 0
        _label = "磋商" if _unit_mode else ("投标" if "投标" in report_kind else "投选")

        def _fill(kind, parts):
            p = _styled(doc, profile, section="一", kind=kind)
            f = ((profile.get("section") or {}).get("一") or {}).get(kind) or {}
            _size = float(f.get("size") or 12)
            for text, bold, underline in parts:
                r = p.add_run(str(text))
                _set_font(r, name=f.get("font") or "宋体", size=_size, bold=bold)
                r.underline = underline or None
            return p

        _fill("plain", [(f"致：{purchaser}", False, False)])
        _fill("plain", [
            ("关于", False, False), (project, False, True),
            ("（项目编号:", False, False), (no, True, False),
            ("）磋商文件（包括更正公告，如果有的话）收悉，我们经详细审阅和研究，"
             f"现决定参加本项目{_label}。同时，正式授权下述签字人", False, False),
            (agent or "（授权代表）", False, True),
            (f"（{af.get('position') or '项目经理'}）代表", False, False),
            (co_name, False, True),
            (f"（供应商单位名称），全权处理本次项目{_label}的有关事宜。", False, False)])
        if _unit_mode:
            _fill("item", [
                ("1、按照磋商文件中规定各项要求，我方愿以（大写、人民币）", False, False),
                (_unit_big(prices["province"]), False, True), ("、", False, False),
                (_unit_big(prices["city"]), False, True),
                (f"（RMB￥{prices['province']:g} 元/亩、"
                 f"￥{prices['city']:g} 元/亩）【其中：报省：￥{prices['province']:g}"
                 f" 元/亩；报市：￥{prices['city']:g} 元/亩】的磋商单价并按磋商文件"
                 "的规定要求提供所需的服务。", False, False)])
            _days = 60
        else:
            _small, _big = _price_text(price_wan)
            _fill("item", [
                ("1、按照磋商文件中规定各项要求，我方愿以（大写、人民币）", False, False),
                (_big, False, True), (f"（RMB￥{_small} 元）的投标总价并按招标文件"
                                     "的规定要求提供所需的服务。", False, False)])
            _days = 90
        _fill("item", [
            (f"2、我们同意按照磋商文件第一章“供应商须知”的规定，本响应文件的有效期为"
             f"从{_label}之日起计算{_days}天，在此期间，本响应文件将始终对我们具有"
             "约束力，并可随时被接受。如果我们成交，本响应文件在此期间之后将继续保持"
             "有效，直到合同生效。", False, False)])
        _fill("item", [
            ("3、我们同意向贵方提供贵方可能要求的与磋商有关的任何证据或资料，并保证"
             "所提供的都是真实的、准确的。", False, False)])
        _fill("item", [
            ("4、一旦我方成交，我方将按照磋商文件的规定，严格履行合同的责任和义务，"
             "并保证于“合同主要条款”中规定的时间进场提供服务。", False, False)])
        _blank()
        for parts in (
            [("供应商名称：", False, False), (co_name, False, True),
             ("（加盖公章）", False, True)],
            [("地址：", False, False), (address, False, True),
             ("　邮编：", False, False), ("223100", False, True)],
            [("电话：", False, False), (phone or mobile, False, True),
             ("　传真：", False, False), (phone or "", False, True)],
            [("法定代表人（或受托人）：", False, False),
             (agent or legal, False, True), ("（签章）", False, False)],
            [("职务：", False, False),
             (af.get("position") or "项目经理", False, True)],
            [("日期：", False, False), (today, False, True)],
        ):
            _fill("sign", parts)
        _styled(doc, profile, section="一", kind="note",
                text="注:请供应商另准备二份空白磋商函，以备最终报价用。")
        return hp

    def _form_p(text, *, form="", section="", kind="plain", size=None,
                bold=False, align=None, **kw):
        return _styled(doc, profile, section=section, form=form, kind=kind,
                       text=text, size=size, bold=bold, align=align, **kw)

    def _form_line(parts, *, form="", section="", kind="field", size=None,
                   bold_default=False):
        f = (((profile.get("form") or {}).get(form) if form else
              (profile.get("section") or {}).get(section)) or {}).get(kind) or {}
        _size = float(size or f.get("size") or 12)
        _fc = f.get("first_line_chars")
        # 招标文件那行留的是空格（短），填上单位名会超出版心：超了就不缩进、
        # 改为右对齐，避免「（加盖公章）」被折到下一行顶格（业主 2026-11-20）
        _text_w = sum(len(str(t)) for t, _b, _u in parts) * _size
        _avail = ((profile.get("page") or {}).get("w_cm") or 21.0) * 28.35 \
            - ((profile.get("page") or {}).get("left_cm") or 2.5) * 28.35 \
            - ((profile.get("page") or {}).get("right_cm") or 2.3) * 28.35
        if (kind == "sign" and _fc
                and (_fc * _size + _text_w) > _avail - 8):
            p = _styled(doc, profile, section=section, form=form, kind=kind,
                        align="right", first_chars=0)
        else:
            p = _styled(doc, profile, section=section, form=form, kind=kind)
        for text, bold, underline in parts:
            if underline and not str(text or "").strip():
                text = "　　　　"      # 空字段也要有下划线可手填
            _add_r(p, str(text), size=_size,
                   bold=(bold if bold is not None else bold_default),
                   font=f.get("font") or "宋体", underline=underline)
        return p

    def _declaration_22(form):
        # 逐字照「示范格式三」：前四条原文没有序号，第五条才是「五、」
        _form_p(f"致：{purchaser}", form=form, kind="plain")
        _form_p(f"我方在{project}（项目编号：{no}）的投标过程中，做如下承诺：",
                form=form, kind="plain")
        for _ln in ("我单位具有独立承担民事责任的能力；",
                    "我单位具有良好的商业信誉和健全的财务会计制度；",
                    "我单位具有履行合同所必需的设备和专业技术能力；",
                    "我单位有依法缴纳税收和社会保障资金的良好记录；",
                    "五、我单位参加政府采购活动前三年内，在经营活动中没有重大违法记录；"):
            _form_p(_ln, form=form, kind="plain")
        _form_p("我方保证上述信息的真实和准确，并愿意承担因我方就此弄虚作假所"
                "引起的一切法律后果，并自愿接受相关监管部门作出的任何处罚。",
                form=form, kind="plain")
        _form_p("特此声明！", form=form, kind="plain")
        _sign_lines(form)

    def _commitment(form):
        _form_p(f"致：{purchaser}", form=form, kind="plain")
        _form_p(f"我方在{project}（项目编号：{no}）的投标过程中，做如下承诺：",
                form=form, kind="plain")
        _form_p("一、我单位负责人与其他参与本项目投标的单位负责人不为同一人、"
                "不存在直接控股、管理关系；", form=form, kind="item")
        _form_p("二、我单位没有为本次采购项目提供整体设计、规范编制或者项目管理、"
                "监理、检测等服务；", form=form, kind="item")
        _form_p("我方保证上述信息的真实和准确，并愿意承担因我方就此弄虚作假所"
                "引起的一切法律后果，并自愿接受相关监管部门作出的任何处罚。",
                form=form, kind="plain")
        _form_p("特此承诺！", form=form, kind="plain")
        _sign_lines(form)

    def _sign_lines(form):
        _form_line([("供应商：", False, False), (co_name, False, True),
                    ("（加盖公章）", False, False)], form=form, kind="sign")
        _form_line([("日期：", False, False), (today, False, True)],
                   form=form, kind="sign")

    def _sme(form):
        emp = company.get("employee_count") or "—"
        rev = company.get("annual_revenue_wan") or "—"
        ast = company.get("total_assets_wan") or "—"
        etype = company.get("enterprise_type") or "小型企业"

        def _fill(kind, parts):
            p = _styled(doc, profile, form=form, kind=kind)
            f = ((profile.get("form") or {}).get(form) or {}).get(kind) or {}
            _size = float(f.get("size") or 12)
            for text, underline in parts:
                _add_r(p, str(text), size=_size, font=f.get("font") or "宋体",
                       underline=underline)
            return p

        _fill("plain", [
            ("公司郑重声明，根据《政府采购促进中小企业发展管理办法》"
             "（财库﹝2020﹞46 号）的规定，本公司参加", False),
            (purchaser, True), ("的", False), (project, True),
            ("采购活动，服务全部由符合政策要求的中小企业承接。相关企业的具体"
             "情况如下：", False)])
        _fill("plain", [
            (project, True), ("，属于", False), ("其他未列明行业", True),
            ("；承建（承接）企业为", False), (co_name, True),
            ("，从业人员", False), (f" {emp} ", True), ("人，营业收入为", False),
            (f" {rev} ", True), ("万元，资产总额为", False), (f" {ast} ", True),
            ("万元，属于", False), (etype, True), ("；", False)])
        _form_p("以上企业，不属于大企业的分支机构，不存在控股股东为大企业的情形，"
                "也不存在与大企业的负责人为同一人的情形。", form=form, kind="plain")
        _form_p("本企业对上述声明内容的真实性负责。如有虚假，将依法承担相应责任。",
                form=form, kind="plain")
        _form_p("企业名称（盖章）：" + co_name, form=form, kind="sign",
                align="center")
        _form_p("日 期：" + today, form=form, kind="sign")

    def _agent_auth_body(form):
        # 逐字照磋商文件「示范格式二 授权委托书」的栏目、下划线和措辞
        _birth = ""
        _m = re.search(r'出生日期[：:]\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日)',
                       str(texts.get("授权委托书") or ""))
        if _m:
            _birth = _m.group(1)
        _form_line([("致：", False, False), (purchaser, False, False)],
                   form=form, kind="plain")
        _form_line([("本授权书宣告：", False, False)], form=form, kind="plain")
        _form_line([("委托方：", False, False), (co_name, False, True),
                    ("　地址：", False, False), (address, False, True)],
                   form=form, kind="field")
        _form_line([("法定代表人：", False, False), (legal, False, True)],
                   form=form, kind="field")
        _form_line([("受托人：姓名", False, False), (agent or legal, False, True),
                    (" 性别：", False, False),
                    (af.get("gender") or "", False, True),
                    (" 出生日期：", False, False), (_birth, False, True)],
                   form=form, kind="field")
        _form_line([("所在单位：", False, False), (co_name, False, True),
                    (" 职务：", False, False),
                    (af.get("position") or "项目经理", False, True)],
                   form=form, kind="field")
        _form_line([("身 份 证：", False, False), (af.get("id_no") or "", False, True),
                    (" 联系方式：", False, False),
                    (af.get("mobile") or mobile, False, True)],
                   form=form, kind="field")
        _form_p(f"兹委托受托人{agent or legal}代表我公司参加"
                f"{agency or '采购代理机构'}组织的（项目编号:{no}）采购项目的"
                "政府采购活动，受托人有权在该磋商活动中，以我单位的名义签署磋商函"
                "和响应文件，与采购人针对本项目咨询、投标报名、澄清、解释、质疑、"
                "投诉、签订合同书并执行一切与此有关的事项。", form=form,
                kind="plain")
        _form_p("    针对上述事宜，我单位只授权其一个人为本项目的受托人，另行授权"
                "无效。受托人在办理上述事宜过程中以其自己的名义所签署的所有文件"
                "我公司均予以承认。受托人无转委托权。", form=form, kind="plain")
        _form_p("委托期限：至上述事宜处理完毕止。", form=form, kind="plain")
        _form_line([("委托单位：", False, False), (co_name, False, True),
                    ("（加盖公章）", False, False)], form=form, kind="sign")
        _form_line([("日    期：", False, False), (today, False, True)],
                   form=form, kind="sign")

    def render_qualification(_s) -> Any:
        hp = _head1(_s)
        items = qual_items or [
            "1、供应商提供下列材料之一（加盖供应商公章装订在标书中）:",
            "2、供应商须具有在有效期内的营业执照副本、税务登记证副本、组织机构"
            "代码证副本或三证合一的营业执照；（复印件加盖供应商公章装订在标书中）",
            "3、供应商符合《中华人民共和国政府采购法》第二十二条规定条件的声明函；"
            "（示范格式三要求）",
            "4、供应商针对本项目所作出的书面承诺；（示范格式四要求）",
            "5、供应商必须为中小微企业，提供中小企业声明函；（示范格式五要求）",
            "6、供应商具有有效期内的测绘资质乙级及以上资质证书。（复印件加盖供应商"
            "公章装订在标书中）",
            "7、供应商必须具有良好的信用记录：投标截止时间之前，未被“信用中国”"
            "网站列入失信执行人、重大税收违法失信主体、政府采购严重违法失信行为"
            "记录名单，未被“中国政府采购网”网站列入政府采购严重违法失信行为记录"
            "名单。（网页截图打印或审查小组现场审查）",
            "8、本次项目不接受联合体供应商参加磋商。（未见联合体字样即可）",
        ]
        for item in items:
            t = str(item)
            _para(t, section="二", kind="item")      # 原条目照抄
            if ("提供下列材料之一" in t or "法定代表人资格证明" in t
                    or "授权委托" in t):
                _para("（1）供应商提供法定代表人资格证明和法定代表人身份证；"
                      "(提供资格证明原件)", section="二", kind="item")
                _para("（2）供应商提供授权委托书和受托人身份证；（提供授权委托书"
                      "原件及被授权人提供开标前六个月任意一个月由人社部门出具的"
                      "由本单位缴纳的养老保险证明）", section="二", kind="item")
                # 二.1 是「二选一」：受托人参加 → 授权委托书 + 受托人身份证 +
                # 养老保险，原件形式见「五、示范格式」，二里不重复贴身份证
                _para(f"本次磋商由受托人{agent or legal}参加，按磋商文件"
                      f"「{forms_title}」示范格式二提供授权委托书原件、受托人身份"
                      "证复印件及开标前六个月任意一个月由人社部门出具、由本单位"
                      "缴纳的养老保险证明，原件装订在第五部分对应格式之后。",
                      section="二", kind="plain")
            elif "营业执照" in t:
                _para(f"统一社会信用代码：{company.get('business_license_no') or '—'}；"
                      f"企业名称：{co_name}；住所：{address}；法定代表人：{legal}。"
                      "营业执照副本复印件附后。")
                _asset_block(doc, "营业执照", [("营业执照",)], caption="营业执照副本",
                             width_cm=(style.get("images") or {}).get("license_cm", 15.0),
                             break_after=False, break_before_first=True)
            elif "第二十二" in t:
                _para(f"按磋商文件「{forms_title}」示范格式三填写声明函，原件见第五部分。")
            elif "承诺" in t:
                _para(f"按磋商文件「{forms_title}」示范格式四填写承诺书，原件见第五部分。")
            elif "中小企业" in t:
                _para(f"按磋商文件「{forms_title}」示范格式五填写中小企业声明函，"
                      "原件见第五部分。")
            elif "测绘资质" in t:
                _para("我公司持有有效期内的测绘资质证书，业务范围覆盖工程测量、"
                      "不动产测绘、地理信息系统工程，满足磋商文件「测绘资质乙级及"
                      "以上」的要求。证书复印件附后。")
                _asset_block(doc, "测绘资质", [("测绘资质",)],
                             caption="测绘资质证书",
                             width_cm=(style.get("images") or {}).get("cert_cm", 15.0),
                             break_after=False, break_before_first=True)
            elif "信用" in t:
                _para("截至投标截止时间，我公司未被“信用中国”网站列入失信被执"
                      "行人、重大税收违法失信主体、政府采购严重违法失信行为记录"
                      "名单，未被“中国政府采购网”列入政府采购严重违法失信行为"
                      "记录名单。查询截图如下。")
                _asset_block(doc, "", [("失信",)], caption="信用记录查询截图",
                             width_cm=15.0, break_after=False,
                             break_before_first=True)
            elif "联合体" in t:
                _para(f"我公司{co_name}独立参加本项目磋商，未组成联合体，响应文件"
                      "中无联合体字样及联合体协议。")
                _form_line([("供应商：", False, False), (co_name, False, True),
                            ("（加盖公章）", False, False)], section="二",
                           kind="sign")
                _form_line([("日期：", False, False), (today, False, True)],
                           section="二", kind="sign")
            else:
                _para("我公司已按磋商文件要求提供：" +
                      re.sub(r'^\d+\s*[、.．]\s*', '', t) + "（材料见附件）。")
        return hp

    def _tbl(headers, rows, widths, *, aligns=None, header_size=None,
             body_size=None, keep_header=True):
        """响应文件里的表：宋体，表头字号按招标文件人员表（洪泽是 14 磅不加粗）。"""
        from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
        from docx.shared import Cm
        _hs = float(header_size or (profile.get("table_header") or {}).get("size")
                    or 14)
        _bs = float(body_size or (profile.get("body") or {}).get("size") or 12)
        aligns = list(aligns or [])
        t = doc.add_table(rows=len(rows) + 1, cols=len(headers))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        for ci, h in enumerate(headers):
            cell = t.cell(0, ci)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell.text = ""
            p = cell.paragraphs[0]
            _line(p, align=aligns[ci] if ci < len(aligns) else "center", size=_hs)
            _add_r(p, str(h), size=_hs, bold=False)
        for ri, row in enumerate(rows, 1):
            for ci in range(len(headers)):
                cell = t.cell(ri, ci)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                cell.text = ""
                p = cell.paragraphs[0]
                _line(p, align=aligns[ci] if ci < len(aligns) else "center",
                      size=_bs)
                _add_r(p, str(row[ci] if ci < len(row) else ""), size=_bs)
        if widths:
            for ci, w in enumerate(widths):
                t.columns[ci].width = Cm(float(w))
                for r in t.rows:
                    r.cells[ci].width = Cm(float(w))
        if keep_header:
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            trPr = t.rows[0]._tr.get_or_add_trPr()
            el = OxmlElement("w:tblHeader")
            el.set(qn("w:val"), "true")
            trPr.append(el)
        return t

    def render_personnel(_s) -> Any:
        hp = _head1(_s)
        headers = list(_s.get("headers") or [
            "序号", "姓名", "性别", "年龄", "学历", "专业", "职称",
            "相关证书", "拟任岗位"])
        widths = ([1.53, 1.80, 1.80, 1.54, 1.74, 1.74, 1.60, 2.14, 2.34]
                  if len(headers) == 9 else _person_col_widths(headers))
        _tbl(headers, _person_rows_for(headers, people), widths,
             aligns=["center"] * len(headers))
        _form_line([("供应商：", False, False), (co_name, False, True),
                    ("（加盖公章）", False, False)], section="三", kind="sign")
        _form_line([("日  期：", False, False), (today, False, True)],
                   section="三", kind="sign")
        _para(f"注：以上人员均为我公司员工，社保缴纳证明附后；项目负责人"
              f"{people[0].get('name') if people else '—'}具有"
              f"{(people[0].get('title') if people else '') or '相应'}"
              "职称，职称证书和注册执业资格证书复印件附后。")
        _asset_block(doc, "", [("注册测绘师",), ("高级工程师",)],
                     caption="项目组人员职称及执业资格证书",
                     width_cm=15.0, break_after=False, break_before_first=True)
        for p in people[:5]:
            imgs = _person_images(int(p.get("id") or 0))
            if not imgs:
                continue
            _para(f"{p.get('name')} 职称/执业资格证书", bold=True).paragraph_format.keep_with_next = True
            for _nm, _data in imgs[:3]:
                _img(doc, _data, 14.0, page_break_before=False)
        _imgs = _social_insurance_images(company_id, limit=6)
        if _imgs:
            _para("项目组人员社保缴纳证明", bold=True).paragraph_format.keep_with_next = True
            for _nm, _data in _imgs:
                _img(doc, _data, 15.0)
        return hp

    async def render_other(_s) -> Any:
        hp = _head1(_s)
        prefix = _no_arabic(_s.get("no") or "四")
        letter_t = _sec_title(by_kind.get("letter")) or "磋商函"
        person_t = _sec_title(by_kind.get("personnel")) or "人员配备表"
        _para("（格式由供应商自定，加盖供应商公章。）", section="四")
        _para(f"本部分按磋商文件评标标准组织：{prefix}.1 为商务部分（体系认证、"
              "类似业绩、设备），"
              f"{prefix}.2 为技术方案（6 个评分项）。评审因素与所在位置对照如下。")
        _tbl(["评审因素", "分值", "本文件对应内容", "所在位置"],
             [["投标报价", "20 分", "磋商函中的报省、报市单价", letter_t],
              ["商务部分", "15 分", "体系认证 4 分、类似业绩 6 分、设备 5 分",
               f"{prefix}.1 商务部分"],
              ["技术方案", "50 分", "工作方案、技术方案、质量保障、重点难点、"
               "进度安排、安全保密", f"{prefix}.2 技术方案"],
              ["人员配备", "15 分", "项目负责人及项目组人员职称、执业资格",
               person_t]],
             [3.0, 1.6, 7.4, 3.0], body_size=10.5,
             aligns=["center", "center", "left", "center"])

        _head2(f"{prefix}.1 商务部分")
        _head2(f"{prefix}.1.1 管理体系认证（4 分）")
        _para("我公司持有有效期内的质量管理体系认证、环境管理体系认证、职业健康"
              "安全管理体系认证和测量管理体系认证证书，认证范围覆盖测绘与土地技术"
              "服务，四项齐全，本项可得满分 4 分。证书复印件附后。")
        _asset_block(doc, "", [("质量管理体系认证",), ("环境管理体系认证",),
                               ("职业健康安全管理体系认证",),
                               ("测量管理体系认证",)],
                     caption="管理体系认证证书",
                     width_cm=(style.get("images") or {}).get("cert_cm", 15.0),
                     break_after=False, break_before_first=True)

        _head2(f"{prefix}.1.2 类似项目业绩（6 分）")
        perf = _perf_rows(texts)
        _para("2023 年 1 月 1 日以来承担的土地征收报批类项目如下（合同签订时间"
              "均在评分有效期内），每个项目附合同复印件，本项按 3 个项目计满分 "
              "6 分。")
        _tbl(["序号", "项目名称", "项目类型", "合同签订时间", "证明材料"],
             perf[:5] or [["—", "（业绩材料见公司档案）", "—", "—", "—"]],
             [1.0, 6.4, 2.6, 2.2, 2.8], body_size=9.5,
             aligns=["center", "left", "center", "center", "center"])
        for kw in ("1.2024年食品科技产业园", "2.井神绿化通道", "3.张兴矿区采输卤"):
            _asset_block(doc, kw, [(kw,)], caption="",
                         width_cm=15.0, break_after=False,
                         break_before_first=False)

        _head2(f"{prefix}.1.3 拟投入设备（5 分）")
        _para("磋商文件点名的 7 类设备中，我公司自有全站仪、GNSS 接收机、无人机、"
              "CASS 成图软件和绘图仪 5 类，同类设备不重复计分，本项可得满分 5 分。"
              "设备发票、采购合同和照片附后。")
        _tbl(["序号", "设备名称", "是否具备", "证明材料"],
             _equip_rows(texts), [1.2, 5.6, 2.4, 5.8],
             body_size=10, aligns=["center", "left", "center", "left"])
        _asset_block(doc, "仪器设备", [("仪器设备",)], caption="主要仪器设备照片",
                     width_cm=13.0, break_after=False, break_before_first=True)

        thp = _head2(f"{prefix}.2 技术方案")
        thp.paragraph_format.page_break_before = True
        _para("本节按评标标准的技术方案 6 个评分项逐项编写：工作方案（10 分）、"
              "技术方案（10 分）、质量保障措施（8 分）、重点与难点分析（8 分）、"
              "进度安排（7 分）、安全保密保障措施（7 分）。")
        md = ""
        if use_llm:
            try:
                md = await _write_cuoshang_plan(tender, prices)
            except Exception as e:
                logger.warning(f"[磋商文件] 技术方案生成失败，用内置框架: {e}")
                md = ""
        _render_plan(doc, tender, prices, md, head=_head2,
                     para=lambda t: _para(t),
                     caption_p=lambda t: _para(t, bold=True, align="center"),
                     table=lambda h, r, w, a: _tbl(h, r, w,
                                                   body_size=10.5, aligns=a))
        return hp

    def render_quote(_s) -> Any:
        hp = _head1(_s)
        _unit_mode = prices["province"] > 0 or prices["city"] > 0
        _para("1、开标一览表", bold=True, align="center", first_chars=0)
        if _unit_mode:
            _tbl(["项目", "磋商单价", "备注"],
                 [["报省", f"{prices['province']:g} 元/亩", "按实际工作量结算"],
                  ["报市", f"{prices['city']:g} 元/亩", "按实际工作量结算"]],
                 [4.0, 5.5, 5.5], body_size=11,
                 aligns=["center", "center", "left"])
        else:
            _small, _big = _price_text(price_wan)
            _tbl(["项目名称", "投标人", "投标总价（元）", "服务期"],
                 [[project, co_name, _small,
                   f"{tender.get('duration_months')} 个月"
                   if tender.get("duration_months") else "按合同约定"]],
                 [5.2, 4.0, 3.2, 2.6], body_size=10,
                 aligns=["left", "left", "center", "center"])
            _para(f"投标总价大写：{_big}", first_chars=0)
        _para("2、投标明细报价表", bold=True, align="center", first_chars=0)
        _tbl(["序号", "服务内容", "金额（元）", "备注"],
             [["1", project, "详见开标一览表", "含税，包含完成本项目的全部费用"]],
             [1.2, 8.0, 3.2, 2.6], body_size=10,
             aligns=["center", "left", "center", "left"])
        return hp

    def render_deviation(_s) -> Any:
        hp = _head1(_s)
        reqs = [q for q in (tender.get("qualifications") or []) if q]
        if not reqs:
            reqs = [w.get("name") or "" for w in (tender.get("work_items") or [])]
        rows = [[str(i), str(q)[:100], "完全响应", "无偏离"]
                for i, q in enumerate(reqs[:15], 1)]
        _tbl(["序号", "招标文件要求", "投标响应", "偏离说明"],
             rows or [["—", "招标文件全部实质性要求", "完全响应", "无偏离"]],
             [1.2, 7.8, 3.2, 2.8], body_size=10,
             aligns=["center", "left", "center", "center"])
        _para("我公司对招标文件全部实质性条款均作出响应，无负偏离。")
        return hp

    def render_scoring(_s) -> Any:
        hp = _head1(_s)
        items = list((tender.get("cuoshang") or {}).get("scoring") or [])
        if not items:
            for ln in (tender.get("scoring_points") or []):
                m = re.match(r'(.+?)[：:]\s*(\d+)\s*分', str(ln))
                if m:
                    items.append({"item": m.group(1), "score": int(m.group(2))})
        rows = [[it.get("item", ""), f"{it.get('score', '')} 分",
                 "见本文件对应章节"] for it in items]
        _tbl(["评审因素", "分值", "本文件对应内容"],
             rows or [["—", "—", "见本文件对应章节"]],
             [5.0, 2.2, 7.8], body_size=10.5,
             aligns=["left", "center", "left"])
        return hp

    async def render_plan(_s) -> Any:
        hp = _head1(_s)
        secs = await _write_generic_plan(tender) if use_llm else []
        if not secs:
            scope = str(tender.get("scope") or "").strip()
            secs = [("总体工作方案", scope or
                     "按招标文件要求组织实施，配备项目负责人和技术负责人，"
                     "按阶段提交成果并接受采购人检查。")]
        for i, (title, body) in enumerate(secs, 1):
            _h(doc, f"（{i}）{title}", 3, size=14, font="黑体")
            for para in [x.strip() for x in body.split("\n") if x.strip()]:
                _plan_p(doc, para)
        return hp

    def render_forms(_s) -> Any:
        # 必须是 Heading 1（目录要抓），字号/字重再压成磋商件的 14 磅宋体加粗
        hp = _head1(_s)
        _names = spec_forms or [
            "法定代表人资格证明", "授权委托书",
            "关于《中华人民共和国政府采购法》第二十二条规定的声明函",
            "承诺书", "中小企业声明函", "残疾人福利性单位声明函"]

        def _is(n: str, key: str) -> bool:
            return key in re.sub(r'\s+', '', n)

        def _label(text):
            # 示范格式N 这行是招标文件的原文，版式取全部标签行的众数
            f = profile.get("form_label") or {}
            p = doc.add_paragraph()
            _size = float(f.get("size") or 14)
            _line(p, align=f.get("align"),
                  first_chars=f.get("first_line_chars"),
                  first_pt=f.get("first_line_pt"), size=_size,
                  exact=f.get("line_exact_pt"),
                  multiple=f.get("line_multiple"), keep=True)
            _add_r(p, text, size=_size, font=f.get("font") or "宋体",
                   bold=f.get("bold", True))
            return p

        def _title(text, key):
            # 表名字号/缩进/行距取招标文件同名示范格式的量测值
            return _styled(doc, profile, form=key, kind="title", text=text)

        def _key(t: str) -> str:
            return re.sub(r'\s+', '', t)

        _CN = "一二三四五六"
        for _i, form in enumerate(_names, 1):
            label = f"示范格式{_CN[_i - 1]}" if _i <= len(_CN) else ""
            key = _key(form)
            n = re.sub(r'\s+', '', form)
            if _is(n, "法定代表人资格证明"):
                _label(label)
                _title("法定代表人资格证明", key)
                _form_line([("单位名称：", False, False), (co_name, False, True)],
                           form=key, kind="field")
                _form_line([("地址：", False, False), (address, False, True)],
                           form=key, kind="field")
                _form_line([("姓名：", False, False), (legal, False, True),
                            ("       性别：", False, False), ("男", False, True),
                            ("      年龄：", False, False), ("", False, True),
                            ("      职务：", False, False),
                            (company.get("leader_title") or "法定代表人",
                             False, True)], form=key, kind="field")
                _form_line([("系", False, False), (co_name, False, True),
                            ("（供应商名称）的法定代表人。为参与", False, False),
                            (project, False, True),
                            ("项目，签署上述项目的响应文件、进行磋商、签署合同和"
                             "处理与之有关的一切事务。", False, False)],
                           form=key, kind="plain")
                _form_line([("特此证明。", False, False)], form=key, kind="plain")
                _form_line([("供应商：", False, False), (co_name, False, True),
                            ("（加盖公章）", False, False)], form=key, kind="sign")
                _form_line([("日  期：", False, False), (today, False, True)],
                           form=key, kind="sign")
                _form_p("注:供应商法定代表人参加磋商的，必须附法定代表人身份证"
                        "复印件并加盖委托单位公章，参加磋商时法定代表人将身份证"
                        "原件带至磋商现场备查。法定代表人授权其他人参加磋商的，"
                        "仅需填写“授权委托书”。", form=key, kind="note")
                # 业主 2026-11-20：五里也要放法人身份证
                _form_p("附：法定代表人身份证复印件", form=key, kind="plain")
                _id_pair(legal)
            elif _is(n, "授权委托书"):
                _label(label)
                _title("授权委托书", key)
                _agent_auth_body(key)
                _form_p("注:供应商委托受托人参加磋商的，必须附受托人身份证"
                        "复印件并加盖委托单位公章，参加磋商时受托人将身份证原件"
                        "带至磋商现场备查。法定代表人直接参加磋商的，仅需填写"
                        "“法定代表人资格证明”。", form=key, kind="note")
                if agent and agent != legal:
                    _form_p("附：受托人身份证复印件", form=key, kind="plain")
                    _id_pair(agent)
                    _imgs = _social_insurance_images(company_id, limit=4)
                    if _imgs:
                        _form_p("受托人开标前六个月养老保险缴纳证明：",
                                form=key, kind="plain")
                        for _nm, _data in _imgs:
                            _img(doc, _data, 15.0)
            elif _is(n, "声明函") and "中小企业" not in n and "残疾人" not in n:
                _label(label)
                _title("关于《中华人民共和国政府采购法》第二十二条规定的"
                       "声明函", key)
                _declaration_22(key)
            elif _is(n, "承诺"):
                _label(label)
                _title("承   诺  书", key)
                _commitment(key)
            elif _is(n, "中小企业"):
                _label(label)
                _title("中小企业声明函（服务）", key)
                _sme(key)
            elif _is(n, "残疾人"):
                _title("残疾人福利性单位声明函", key)
                _form_p("我单位不属于残疾人福利性单位，按磋商文件要求不需提供"
                        "此函。", form=key, kind="plain")
        return hp

    def render_unknown(_s) -> Any:
        hp = _head1(_s)
        _txt = str(_s.get("text") or "").strip()
        if _txt:
            _para(_txt)
        _para("我公司已按磋商文件要求响应本部分内容，相关材料附后。")
        return hp

    _renderers = {"letter": render_letter, "qualification": render_qualification,
                  "personnel": render_personnel, "other": render_other,
                  "forms": render_forms, "quote": render_quote,
                  "deviation": render_deviation, "scoring": render_scoring,
                  "plan": render_plan}
    _TITLES: List[str] = []
    _first = True
    for _s in spec_secs:
        _t = _sec_title(_s)
        if _t and _t not in _TITLES:
            _TITLES.append(_t)
        _res = _renderers.get(_s.get("kind"), render_unknown)(_s)
        _hp = await _res if asyncio.iscoroutine(_res) else _res
        if not _first and _hp is not None:
            _hp.paragraph_format.page_break_before = True
        _first = False

    # ── 保存 + 目录页码 ──
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _add_page_footer(doc)
    doc.save(str(out))
    try:
        # anchors 传空：四个标题在正文里只出现一次，按**首次**出现取页码
        # （评分对照表里也印着「一、磋商函」，按最后一次会把页码指到后面去）
        # 章节标题在正文里可能被引用（如「按五、示范格式填写」），示范格式按
        # **最后一次**出现取页码；其它章节按首次出现，避免评分表里的引用抢先
        _anchors = (forms_title,) if _forms_sec else ()
        if _stamp_toc(out, titles=_TITLES, anchors=_anchors, style=style):
            _bump_toc_pages(out, _TITLES, style)
    except Exception as e:
        logger.warning(f"[磋商文件] 目录页码写入失败，保留目录域: {e}")
    logger.info(f"[磋商文件] 已生成 {out}（{len(people)} 人，"
                f"报省 {prices['province']:g} 元/亩、报市 {prices['city']:g} 元/亩）")
    return out
