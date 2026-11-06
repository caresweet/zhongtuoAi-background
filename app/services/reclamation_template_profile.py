# -*- coding: utf-8 -*-
"""地区复垦模板的「逐节三色」画像 —— 让模板在生成时真正可执行。

为什么
------
只把模板整章丢给模型，模型会两种极端：要么整段照抄（别项目的地名、数字也抄），
要么整章自由发挥（格式和地方依据全丢）。业主的批注已经把规则说清楚了：
固定条款不动、数据位置只换数、项目分析重新写。

本模块把一份复垦模板 DOCX 解析成：

    {
      "source": "templates/user_xxx.docx",
      "domain": "reclamation",
      "chapters": {
        "1": {"title": "前言", "sections": [
          {"no": "1.1", "title": "编制背景及过程", "level": 2,
           "fixed": ["..."], "slot": ["..."], "create": ["..."],
           "keys": ["project_name", "area_m2"], "chars": 860},
          ...
        ]}
      }
    }

写入 `data/template_profiles/reclamation_<文件名>.json`。生成第 N 章时，
`profile_prompt_block()` 把本章每节的「固定/换数/创作 + 数据键」压缩成几行，
直接注入章节 prompt。

分类是**确定性规则**（不调 LLM），可按模板人工校订 JSON；运行时零成本。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
_PROFILE_DIR = _BACKEND / "data" / "template_profiles"

# 画像结构版本：解析规则升级（如新增 slots 数据位明细）后，旧 JSON 自动重建。
# 🔴 不加版本号时，磁盘上的旧画像会被一直命中，新字段永远为空中 —— 实测踩过。
_PROFILE_VERSION = 4

# 这些小节以固定话术为主（政策、原则、程序、质量标准、保障措施）
_FIXED_TITLES = (
    "编制目的", "编制原则", "编制依据", "法律法规", "政策文件", "标准规范",
    "其他技术资料", "总则", "指导思想", "工作原则", "质量要求", "预防控制",
    "监测措施", "管护措施", "保障措施", "组织保障", "监管保障", "技术保证",
    "费用保障", "估算说明", "奖惩", "责任追究", "复垦措施",
    "评价原则", "评价方法", "评价指标", "评价标准", "分级标准", "评价过程",
)

# 这些小节必须结合本项目资料重写
_CREATE_TITLES = (
    "编制背景", "项目简介", "自然概况", "地理位置", "气候", "生物资源",
    "地貌", "土壤", "水文", "地质", "社会经济", "土地利用状况", "土地利用类型",
    "损毁分析", "损毁环节", "已损毁", "拟损毁", "复垦责任范围", "生态环境影响",
    "适宜性评价", "水土资源平衡", "复垦目标", "目标任务", "工程设计",
    "效益", "权属调整", "公众参与",
)

# 这些小节以数据为主（摘要、投资、计划）
_SLOT_TITLES = (
    "复垦方案摘要", "服务年限", "各类土地面积", "土地损毁情况", "投资情况",
    "投资估算", "估算成果", "工作计划", "费用安排", "工程量",
    "复垦目标", "目标任务", "占用永久基本农田", "占用耕地", "生态保护红线",
    "土地权属状况",
)

_KEY_RULES = (
    ("project_name", ("项目名称", "工程名称", "决策名称")),
    ("build_unit", ("建设单位", "项目单位", "实施单位")),
    ("location", ("地理位置", "项目位置", "位于", "坐落")),
    ("area_m2", ("临时用地面积", "总面积", "面积合计", "公顷")),
    ("land_classification", ("地类", "土地利用现状", "土地分类")),
    ("land_ownership", ("权属",)),
    ("project_investment", ("总投资", "投资额")),
    ("_budget_estimate", ("静态总投资", "动态总投资", "工程施工费", "其他费用")),
    ("service_years", ("服务年限", "用地期限", "管护期", "复垦期")),
    ("topsoil_thickness", ("表土剥离", "剥离厚度", "覆土厚度", "耕层厚度")),
    ("hard_surface_type", ("硬化", "砼", "混凝土", "碎石垫层")),
    ("reclaim_direction", ("复垦方向", "复垦为", "恢复为")),
    ("land_use_start", ("用地开始", "起始时间", "从20")),
)

_NUM_UNIT = re.compile(
    r'\d+(?:\.\d+)?\s*(?:公顷|hm2|hm²|亩|平方米|㎡|m2|万元|%|个月|cm|厘米|米|m)')
# 「年」只在服务年限语境里算数据位；法规引用里的 2021 年不算
_YEAR_SLOT = re.compile(
    r'(?:服务年限|用地期限|使用时间|管护期|复垦期)[^。；]{0,12}\d+\s*(?:个?月|年)')
# 年/月的裸数字（「48 个月」「3 年」），取原值用
_YEAR_NUM = re.compile(r'\d+(?:\.\d+)?\s*(?:个?月|年)')
_CITATION = re.compile(r'^(?:依据|根据|按照|参照)?《[^》]{2,60}》')
_HEAD_CH = re.compile(r'^(\d{1,2})\s*(.+)$')
_HEAD_SEC = re.compile(r'^(\d+(?:\.\d+){1,2})\s*(.*)$')
_TOC_STYLE = ("toc",)


def _clean(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def _snippet(s: str, n: int = 90) -> str:
    s = _clean(s)
    return s if len(s) <= n else s[:n] + "…"


def _section_prior(title: str) -> str:
    t = title or ""
    if any(k in t for k in _SLOT_TITLES):
        return "slot"
    if any(k in t for k in _FIXED_TITLES):
        return "fixed"
    if any(k in t for k in _CREATE_TITLES):
        return "create"
    return "mixed"


def _classify_para(text: str, prior: str) -> str:
    """fixed / slot / create。"""
    t = _clean(text)
    if not t:
        return ""
    if _CITATION.match(t):
        return "fixed"
    has_num = bool(_NUM_UNIT.search(t) or _YEAR_SLOT.search(t))
    if prior == "fixed":
        return "slot" if has_num else "fixed"
    if prior == "slot":
        return "slot" if has_num else "mixed"
    if prior == "create":
        return "slot" if has_num else ("create" if len(t) >= 20 else "")
    # mixed：逐段判断
    if has_num:
        return "slot"
    if len(t) >= 30:
        return "create"
    return "fixed"


def _keys_of(text: str) -> List[str]:
    out = []
    for key, kws in _KEY_RULES:
        if key in out:
            continue
        if any(k in text for k in kws):
            out.append(key)
    return out


# 每个数据键「合理的单位」——模板原值取数时按它过滤，避免把邻近的面积当成年限。
# 空元组 = 该键是文字型（地名/方向/单位名），不取数字原值。
_KEY_UNITS: Dict[str, tuple] = {
    "area_m2": ("公顷", "hm", "亩", "平方米", "㎡", "m2"),
    "land_classification": ("公顷", "hm", "亩", "平方米", "㎡", "m2"),
    "project_investment": ("万元", "元"),
    "_budget_estimate": ("万元", "元"),
    "service_years": ("个月", "月", "年"),
    "land_use_start": ("个月", "月", "年"),
    "topsoil_thickness": ("cm", "厘米", "m", "米"),
    "hard_surface_type": ("cm", "厘米", "m", "米"),
    "land_ownership": ("公顷", "hm", "亩", "平方米", "㎡", "m2"),
    "reclaim_direction": (),
    "build_unit": (),
    "location": (),
    "project_name": (),
}


def _num_with_units(segment: str, key: str) -> str:
    """在片段里找「数字+单位」，单位必须与该键相符；文字型键直接不取。"""
    allowed = _KEY_UNITS.get(key)
    if allowed == ():
        return ""
    for m in list(_NUM_UNIT.finditer(segment)) + list(_YEAR_NUM.finditer(segment)):
        s = m.group(0).strip()
        if not allowed or any(u in s for u in allowed):
            return s
    return ""


def _example_near(text: str, key: str) -> str:
    """数据位在模板里的**原值**（例：`0.30 m`、`2.3248 公顷`）。

    按关键词位置取邻近的数字+单位 —— 模板原值是「这个项目要换成什么」的参照，
    也是防止模型把范文数值带进新报告的检查点。取值顺序：关键词之后的第一个数
    （「服务年限为 48 个月」），没有再往前找（「2.3248 公顷临时用地」）。
    """
    kws = dict(_KEY_RULES).get(key, ())
    pos = -1
    for k in kws:
        i = text.find(k)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        return ""
    forward = _num_with_units(text[pos:pos + 25], key)
    if forward:
        return forward
    return _num_with_units(text[max(0, pos - 15):pos], key)


def _slot_examples(body: List[str]) -> List[Dict[str, str]]:
    """本节的数据位清单：`[{key, example}]`，同一 key 只留第一次出现的原值。"""
    out: List[Dict[str, str]] = []
    seen = set()
    for para in body:
        if not (_NUM_UNIT.search(para) or _YEAR_SLOT.search(para)):
            continue
        for key in _keys_of(para):
            if key in seen:
                continue
            seen.add(key)
            # 文字型键（复垦方向/建设单位/位置）没有原值，也照样进清单：
            # 它们是本项目必须给的资料，缺了要报缺口。
            out.append({"key": key, "example": _example_near(para, key)})
    return out


def _iter_headings(doc):
    """按文档顺序产出 (level, no, title, body_paragraphs)，表格文本也收进本节。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    current = None
    for child in doc.element.body.iterchildren():
        tag = str(child.tag)
        if tag.endswith('}p'):
            p = Paragraph(child, doc)
            txt = _clean(p.text)
            style = (p.style.name or "").lower()
            if any(style.startswith(t) for t in _TOC_STYLE):
                continue
            if not txt:
                continue
            if style.startswith(("heading 1", "heading1")):
                m = _HEAD_CH.match(txt)
                if not m:
                    # 封面/扉页的一级标题（无编号）不单独成章
                    continue
                if current:
                    yield current
                current = {"level": 1, "no": int(m.group(1)),
                           "title": _clean(m.group(2)), "body": []}
                continue
            if style.startswith(("heading 2", "heading 3",
                                 "heading2", "heading3")):
                m = _HEAD_SEC.match(txt)
                if not m:
                    # 无编号的小标题（封面项目名、图表标题）不单独成节
                    if current is not None:
                        current["body"].append(txt)
                    continue
                if current:
                    yield current
                title = _clean(m.group(2))
                level = 3 if style.endswith("3") else 2
                current = {"level": level, "no": m.group(1),
                           "title": title, "body": []}
                continue
            if current is not None:
                current["body"].append(txt)
        elif tag.endswith('}tbl') and current is not None:
            try:
                t = Table(child, doc)
                for row in t.rows:
                    cells = [_clean(c.text) for c in row.cells]
                    line = " | ".join(x for x in cells if x)
                    if line:
                        current["body"].append("表格：" + line)
            except Exception:
                continue
    if current:
        yield current


def build_profile_from_docx(path: str | Path,
                            domain: str = "reclamation") -> Dict[str, Any]:
    """解析模板 DOCX，产出逐节三色画像。"""
    from docx import Document

    path = Path(path)
    doc = Document(str(path))
    chapters: Dict[str, Dict[str, Any]] = {}
    cur_ch: Optional[str] = None
    parent_prior: Optional[str] = None   # 最近一个二级小节的类别，供三级小节继承

    for head in _iter_headings(doc):
        if head["level"] == 1:
            no = str(head["no"] or len(chapters) + 1)
            cur_ch = no
            parent_prior = None
            chapters.setdefault(no, {"title": head["title"], "sections": []})
            # 第 1 章标题下的前言段落也作为一节
            if head["body"]:
                chapters[no]["sections"].append(
                    _make_section("", head["title"], 1, head["body"]))
            continue
        if cur_ch is None:
            cur_ch = "1"
            chapters.setdefault(cur_ch, {"title": "", "sections": []})
        # 三级小节继承二级小节的类别（如 10.1 保障措施 → 10.1.1/10.1.2 都是固定话术）
        inherited = parent_prior if head["level"] >= 3 else None
        sec = _make_section(head["no"], head["title"], head["level"],
                            head["body"], inherited=inherited)
        chapters[cur_ch]["sections"].append(sec)
        if head["level"] == 2:
            parent_prior = sec["prior"]

    return {
        "version": _PROFILE_VERSION,
        "source": str(path),
        "domain": domain,
        "chapters": chapters,
    }


def classify_block(body: List[str], title: str = "",
                   inherited: Optional[str] = None) -> Dict[str, Any]:
    """把一个小节的正文分成 🟩固定 / 🟨换数 / 🟦创作（画像与范文分析共用）。

    返回 `{prior, fixed, slot, create, keys, slots}`；`slots` 是「数据键 + 模板原值」。
    """
    prior = _section_prior(title)
    if prior == "mixed" and inherited in ("fixed", "slot", "create"):
        prior = inherited
    fixed: List[str] = []
    slot: List[str] = []
    create: List[str] = []
    for para in body:
        kind = _classify_para(para, prior)
        if kind == "fixed":
            fixed.append(_snippet(para))
        elif kind == "slot":
            slot.append(_snippet(para))
        elif kind == "create":
            create.append(_snippet(para))
    text = "\n".join(body)
    return {
        "prior": prior,
        "fixed": fixed[:4],
        "slot": slot[:4],
        "create": create[:4],
        "keys": _keys_of(text),
        "slots": _slot_examples(body),
    }


def _make_section(no: str, title: str, level: int,
                  body: List[str], inherited: Optional[str] = None) -> Dict[str, Any]:
    cls = classify_block(body, title, inherited)
    return {
        "no": no,
        "title": title,
        "level": level,
        "prior": cls["prior"],
        "fixed": cls["fixed"],
        "slot": cls["slot"],
        "create": cls["create"],
        "keys": cls["keys"],
        "slots": cls["slots"],
        "chars": len("\n".join(body)),
    }


# ══════════════════════════════════════════════════════════════
# 存取
# ══════════════════════════════════════════════════════════════
def _profile_path(template_path: str | Path) -> Path:
    stem = Path(str(template_path)).stem
    return _PROFILE_DIR / f"reclamation_{stem}.json"


def save_profile(profile: Dict[str, Any]) -> Path:
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    p = _profile_path(profile.get("source", ""))
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_profile(template_path: str | Path) -> Optional[Dict[str, Any]]:
    if not template_path:
        return None
    p = _profile_path(template_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[模板画像] 读取失败 {p}: {e}")
        return None


def ensure_profile(template_path: str | Path,
                   domain: str = "reclamation") -> Optional[Dict[str, Any]]:
    """有就读，没有就现建（只对 docx）。旧版本的画像自动重建。"""
    prof = load_profile(template_path)
    if prof and prof.get("version") == _PROFILE_VERSION:
        return prof
    p = Path(str(template_path))
    if not p.exists() or p.suffix.lower() != ".docx":
        return None
    try:
        prof = build_profile_from_docx(p, domain=domain)
        save_profile(prof)
        return prof
    except Exception as e:
        logger.warning(f"[模板画像] 构建失败 {p}: {e}")
        return None


# 数据键 → 中文名（与复垦事实卡同源，取不到就显示英文键）
_EXTRA_LABELS = {"_budget_estimate": "投资估算", "reclaim_direction": "复垦方向",
                 "land_use_start": "临时用地起始"}


def _key_label(key: str) -> str:
    if key in _EXTRA_LABELS:
        return _EXTRA_LABELS[key]
    try:
        from app.agent.reclamation_definitions import RECLAMATION_KEY_LABELS
        return RECLAMATION_KEY_LABELS.get(key) or key
    except Exception:
        return key


# 模板数据键 ↔ 本项目 filled_data 的取数来源（任一有值即视为资料能满足）
_FILLED_ALIASES: Dict[str, tuple] = {
    "_budget_estimate": ("construction_cost", "other_cost", "monitor_care_cost",
                         "investment_summary", "project_investment"),
    "area_m2": ("area_m2", "land_classification", "project_area_m2"),
    "service_years": ("service_years", "land_use_months", "land_use_period"),
    "reclaim_direction": ("reclaim_direction", "reclamation_direction"),
    "hard_surface_type": ("hard_surface_type", "hard_area_m2"),
    "location": ("location", "boundary_desc"),
}


def _has_value(v: Any) -> bool:
    return v not in (None, "", [], {}, "nan", "None")


def _project_value(filled: Optional[Dict[str, Any]], key: str) -> str:
    """本项目资料里该键的值；结构型数据（地类表/分区）只说明「已提供」。"""
    f = filled or {}
    for a in _FILLED_ALIASES.get(key, (key,)):
        v = f.get(a)
        if isinstance(v, dict):
            if v:
                rows = v.get("rows")
                return f"已提供（{len(rows)} 行）" if isinstance(rows, list) and rows else "已提供"
        elif isinstance(v, (list, tuple)):
            if v:
                return f"已提供（{len(v)} 项）"
        elif _has_value(v):
            return str(v).strip()[:40]
    return ""


def profile_data_gaps(profile: Optional[Dict[str, Any]],
                      filled: Optional[Dict[str, Any]],
                      chapter_no: Optional[int] = None) -> List[str]:
    """模板要求的数据键里，本项目资料**还没有**的。

    这是「模板要改的数据 vs 用户资料」的对账：缺的键绝不允许沿用模板原值，
    提示词里显式标出来，让模型按缺口处理。
    `chapter_no` 给定时只看该章（避免第1章提示词里出现第4章才用到的剥离厚度）。
    """
    if not profile:
        return []
    f = filled or {}
    wanted: List[str] = []
    chapters = profile.get("chapters") or {}
    keys = [str(chapter_no)] if chapter_no else list(chapters)
    for ch_no in keys:
        ch = chapters.get(ch_no) or {}
        for sec in ch.get("sections") or []:
            for s in sec.get("slots") or []:
                k = str(s.get("key") or "")
                if k and k not in wanted:
                    wanted.append(k)
    missing = []
    for k in wanted:
        if any(_has_value(f.get(a)) for a in _FILLED_ALIASES.get(k, (k,))):
            continue
        missing.append(k)
    return missing


def template_analysis_summary(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """「这份模板要改哪些数据、哪些内容要重写」的汇总（给上传分析结果用）。

    Returns:
        data_slots:   [{key, label, examples:[模板原值], sections:[出现位置]}]
        rewrite_sections: [{chapter, no, title, sample}] —— 需要按本项目资料重写的小节
        section_counts:   {fixed, slot, create} 各类小节数
    """
    if not profile:
        return {"data_slots": [], "rewrite_sections": [],
                "section_counts": {"fixed": 0, "slot": 0, "create": 0}}
    slots: Dict[str, Dict[str, Any]] = {}
    rewrite: List[Dict[str, Any]] = []
    counts = {"fixed": 0, "slot": 0, "create": 0}
    for ch_no, ch in (profile.get("chapters") or {}).items():
        for sec in ch.get("sections") or []:
            where = {"chapter": ch_no, "no": sec.get("no") or "",
                     "title": sec.get("title") or ""}
            if sec.get("fixed"):
                counts["fixed"] += 1
            if sec.get("slot") or sec.get("slots"):
                counts["slot"] += 1
            if sec.get("create"):
                counts["create"] += 1
                rewrite.append({**where, "sample": (sec["create"][0])[:60]})
            for s in sec.get("slots") or []:
                k = str(s.get("key") or "")
                if not k:
                    continue
                d = slots.setdefault(k, {"key": k, "label": _key_label(k),
                                         "examples": [], "sections": []})
                ex = str(s.get("example") or "")
                if ex and ex not in d["examples"] and len(d["examples"]) < 3:
                    d["examples"].append(ex)
                loc = f"{where['no']} {where['title']}".strip()
                if loc and loc not in d["sections"] and len(d["sections"]) < 5:
                    d["sections"].append(loc)
    return {"data_slots": list(slots.values()),
            "rewrite_sections": rewrite,
            "section_counts": counts}


def profile_prompt_block(template_path: str | Path,
                         chapter_no: int,
                         domain: str = "reclamation",
                         filled: Optional[Dict[str, Any]] = None) -> str:
    """生成章节 prompt 里的「逐节三色处理规则」块。

    `filled` 传入时，把「模板有数据位、本项目资料没有」的键显式标成缺口。
    """
    prof = ensure_profile(template_path, domain=domain)
    if not prof:
        return ""
    ch = (prof.get("chapters") or {}).get(str(chapter_no))
    if not ch:
        return ""
    lines = ["## 🎨 本章模板画像（逐节处理规则，必须遵守）",
             "同一节里可能同时有三类：固定句照原文，数据位只换值，项目分析重新写。"]
    for sec in (ch.get("sections") or [])[:30]:
        no = sec.get("no") or ""
        title = sec.get("title") or ""
        parts = []
        if sec.get("fixed"):
            parts.append(f"🟩固定 {len(sec['fixed'])} 段（例：{sec['fixed'][0][:40]}…）")
        # 数据位优先显示「键=模板原值」；给了本项目资料就同时给「→ 本项目值」，
        # 让模型直接做替换，而不是自己找数。
        if sec.get("slots"):
            bits = []
            for s in sec["slots"][:6]:
                k = str(s.get("key") or "")
                ex = str(s.get("example") or "")
                bit = f"{k}（模板 {ex}" if ex else f"{k}（文字表述"
                if filled is not None:
                    pv = _project_value(filled, k)
                    bit += f" → 本项目 {pv}）" if pv else " → 本项目缺）"
                else:
                    bit += "）"
                bits.append(bit)
            parts.append("🟨换数 " + "、".join(bits))
        elif sec.get("slot") or sec.get("keys"):
            keys = "、".join(sec.get("keys") or []) or "本节数字"
            parts.append(f"🟨换数 {keys}")
        if sec.get("create"):
            parts.append(f"🟦创作 {len(sec['create'])} 段")
        if not parts:
            continue
        lines.append(f"- {no} {title}｜" + "｜".join(parts))
    if len(lines) <= 2:
        return ""
    lines.append("规则：🟩 一字不改（只改单位名/地名/项目名）；🟨 数字必须来自事实卡且能勾稽；"
                 "🟦 先检索全部范文同类小节，再用本项目资料写，不许照抄别项目事实。")
    if filled is not None:
        missing = profile_data_gaps(prof, filled, chapter_no=chapter_no)
        if missing:
            names = "、".join(f"{k}（{_key_label(k)}）" for k in missing[:12])
            lines.append(f"⚠ 资料缺口：{names} —— 这些位置**不得沿用模板原值**，"
                         "改用定性表述或整句不写，缺口由系统统一列出。")
    # 项目既定规则（业主批注/线上实测蒸馏，见 content_rules/template_analysis.md）
    try:
        from app.services.template_rules import rules_block
        _rb = rules_block(domain, chapter_no)
        if _rb:
            lines.append("")
            lines.append(_rb)
    except Exception as e:
        logger.info(f"[模板画像] 既定规则注入跳过: {e}")
    return "\n".join(lines)
