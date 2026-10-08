"""模板槽位标注表 —— **离线标注一次，运行时零 LLM**。

数据库模板的章节正文是带示例项目数据的成品文字，没有 `{{}}` 占位符。
本模块负责把模板块「标注一次」：

    洞庭湖路（S350-宁连一级路段）工程土地征收决策   → slot:project_name
    稳评责任单位：江苏洪泽经济开发区管理委员会        → slot:org_name
    风险分析：随着《土地管理法》的修正…              → fixed（一字不动）
    图4-1 决策地理位置                              → image:location_map
    土地分类面积表（单位：平方米）                   → table:land_classification

标注结果落盘 `data/template_profiles/<template_id>.json`，**人工校订后冻结**
（校订过的条目 `locked:true`，重新学习不覆盖）。此后每次生成只读标注表做
字面命中替换，不再调用 LLM。

template_id 由「模板源文件名 + chunker 切块口径版本」派生，换模板会自动失效重学。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.template_slot_schema import (
    DETECTORS, is_fixed_phrase, is_regulation_citation, pick_field,
    is_valid_slot_value, context_field,
)

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROFILE_DIR = _BACKEND_DIR / "data" / "template_profiles"

# 语义类 → 事实卡字段名（LLM 标注时给它的选项）
SEMANTIC_CLASSES: Dict[str, str] = {
    "项目名称": "project_name",
    "公告文号": "doc_reference",
    "稳评责任单位": "org_name",
    "稳评实施单位": "implement_unit",
    "项目位置": "location",
    "征收面积（平方米）": "area_m2",
    "征收面积（亩）": "area_mu",
    "涉及户数": "household_count",
    "涉及人口": "population_count",
    "问卷份数": "total_samples",
    "群众支持率": "support_rate",
    "反对率": "oppose_rate",
    "知晓率": "awareness_rate",
    "现场公示时间": "notice_date",
    "实地勘察时间": "survey_date",
    "座谈会时间": "symposium_date",
    "委托时间": "commission_date",
    "资金筹措": "funding",
    "补偿标准": "compensation_standard",
    "土地用途": "land_use",
    "无（固定话术，不替换）": "__fixed__",
}


# ═══════════════════════════════════════════════════════════════
# 路径与身份
# ═══════════════════════════════════════════════════════════════

def profile_path(template_id: str) -> Path:
    return _PROFILE_DIR / f"{template_id}.json"


def current_template_id() -> str:
    """当前模板身份 = 源文件名（去扩展名）+ 切块口径版本。"""
    try:
        from app.services.template_chapter_chunker import build_and_cache, _SHIFT_VERSION
        data = build_and_cache()
        src = Path(str(data.get("source") or "unknown")).stem
        return f"{src}_v{_SHIFT_VERSION}"
    except Exception as e:
        logger.warning(f"无法确定模板身份: {e}")
        return "unknown_v0"


def load_profile(template_id: Optional[str] = None) -> Dict[str, Any]:
    """读取标注表；不存在返回空表（调用方应触发 learn_profile）。"""
    tid = template_id or current_template_id()
    p = profile_path(tid)
    if not p.exists():
        return {"template_id": tid, "reviewed": False, "chapters": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("template_id", tid)
        data.setdefault("reviewed", False)
        data.setdefault("global", [])
        data.setdefault("chapters", {})
        return data
    except Exception as e:
        logger.warning(f"标注表读取失败 {p}: {e}")
        return {"template_id": tid, "reviewed": False, "global": [], "chapters": {}}


def save_profile(profile: Dict[str, Any]) -> Path:
    """写回标注表。"""
    tid = profile.get("template_id") or current_template_id()
    profile["template_id"] = tid
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    p = profile_path(tid)
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"模板标注表已保存: {p}")
    return p


# ═══════════════════════════════════════════════════════════════
# 候选槽位抽取（启发式，不依赖 LLM）
# ═══════════════════════════════════════════════════════════════

# 区划/单位名候选（含这些后缀的短片段，多为示例项目的专属数据）
_REGION_SUFFIXES = ("街道", "镇", "乡", "社区", "居委会", "村", "组")
_ORG_SUFFIXES = ("人民政府", "管理委员会", "管委会", "街道办事处", "镇政府", "乡政府",
                 "自然资源局", "自然资源和规划局", "发展和改革委员会", "公司")
_ALL_SUFFIXES = _REGION_SUFFIXES + _ORG_SUFFIXES

# 按标点/空白切段后，整段必须是「短且以区划/单位后缀结尾」才算候选，
# 避免把「根据和高良涧街道」「不满16周岁的安置人员…」这类长句误收。
_SEG_SPLIT_RE = re.compile(r'[，。；、：:（）()\s"“”]+')

# 通用词/动词开头，说明不是专有名称
_GENERIC_PREFIXES = ("涉及", "根据", "按照", "结合", "目前", "其中", "包括", "例如",
                     "不满", "其本人", "本决策", "该项目", "上述", "有关", "相关", "各",
                     "听取", "成立", "组织", "负责", "分别", "下设", "召开", "张贴",
                     "由", "在", "对", "从", "与", "和")


def _looks_like_name(seg: str) -> bool:
    """整段是否像一个专有区划/单位名。"""
    s = seg.strip()
    if not (3 <= len(s) <= 22):
        return False
    if not s.endswith(_ALL_SUFFIXES):
        return False
    if s[0].isdigit():                          # 「1.成立稳评小组」这类带序号的
        return False
    if any(s.startswith(p) for p in _GENERIC_PREFIXES):
        return False
    # 含动词性/口语性字样的，多半是句子而非名称
    if re.search(r'(的|了|是|有|不|应|可|需|将|把|对|从|被|使|让|请|要)', s):
        return False
    # 🔴 含中缀连接/方位词的是句子片段（如「朱璇在朱坝街道大魏社区」「朱坝街道及大魏社区」）
    if len(s) > 5 and re.search(r'[在及与和到向于]', s):
        return False
    # 纯后缀本身（如「街道」「社区」）不是名称
    if s in _ALL_SUFFIXES:
        return False
    # 🔴 「…组」必须是村民小组（含村/社区/镇/街道），否则是「协调联络组」这类内部小组
    if s.endswith("组") and not any(k in s for k in ("村", "社区", "镇", "街道")):
        return False
    return True


def extract_candidates(chapter_no: int, block_text: str) -> List[Dict[str, Any]]:
    """从模板块里抽出「疑似项目专属」的候选串（供 LLM 归类或人工校订）。

    三类来源：
      1. 整段即专有名称（区划/单位名）——如「江苏洪泽经济开发区管理委员会」
      2. 形状可辨认的（文号/日期/面积/数量/百分比）——如「326342平方米」「100%」
         （**法规文号一律排除**，见 is_regulation_citation）
      3. 项目/决策名称——封面首行、或「决策名称」标记行之后的一行

    注意：`chapter_no=0` 表示模板的**前置部分**（封面 / 公司简介）。
    """
    out: List[Dict[str, Any]] = []
    seen = set()

    def _add(anchor: str, kind: str, ctx: str):
        a = (anchor or "").strip()
        if len(a) < 3 or a in seen:
            return
        if re.match(r'^\d+\s*[.、]', a):     # 「2.决策地理位置」这类带编号的标题，不是数据
            return
        if is_fixed_phrase(a, ctx):
            return
        if is_regulation_citation(a, ctx):
            return                      # 🔴 法规文号绝不能当项目槽位替换
        seen.add(a)
        out.append({"anchor": a, "kind": kind, "context": ctx[:120]})

    lines = [l.strip() for l in str(block_text or "").splitlines()]

    for i, p in enumerate(lines):
        if not p:
            continue
        # 1) 专有名称：按标点切段，整段判定
        for seg in _SEG_SPLIT_RE.split(p):
            if _looks_like_name(seg):
                _add(seg, "region_or_org", p)
        # 3) 项目/决策名称：紧跟「决策名称」「项目名称」标记行之后的一行
        if any(mk in p for mk in ("决策名称", "项目名称", "事项名称")) and len(p) <= 12:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and not nxt.endswith(_ALL_SUFFIXES) and 6 <= len(nxt) <= 50:
                _add(nxt, "project_name", p)
        # 2) 形状类（文号/日期/面积/数量/百分比），表号图号跳过
        for rule in DETECTORS:
            if rule.slot_type in ("table_no", "figure_no"):
                continue
            for m in rule.pattern.finditer(p):
                _add(m.group(0), rule.slot_type, p)

    # 封面：首个非空行通常就是项目名称
    if chapter_no == 0 and lines:
        first = next((l for l in lines if l and "公司" not in l and "报告" not in l
                      and "案卷" not in l and not re.match(r'^[二〇\d]{2,}', l)), "")
        if first and 6 <= len(first) <= 50:
            _add(first, "project_name", first)

    return out


# ═══════════════════════════════════════════════════════════════
# 学习（离线一次）
# ═══════════════════════════════════════════════════════════════

_LLM_PROMPT = """你是稳评报告模板分析专家。下面是从**报告模板**某一章里抽出的候选片段。

模板正文里混着两类内容：
- **项目专属数据**（示例项目的地名/单位名/文号/数字）——生成新报告时要替换成新项目的真实数据；
- **固定话术**（法规名、行业通用表述、风险分析套话）——必须一字不动保留。

请对每个候选判断它属于哪一类。若是项目专属数据，从给定语义类中选一个最匹配的；
若是固定话术，选「无（固定话术，不替换）」。

## 候选片段
{candidates}

## 语义类（只能从这些里选）
{classes}

只输出 JSON 数组，每项形如：
{{"anchor": "原片段原文", "semantic": "语义类名"}}
不要输出任何解释。"""


def _match_semantic(label: str) -> str:
    """把 LLM 返回的语义名模糊匹配到 SEMANTIC_CLASSES 的键。

    LLM 常返回近义说法（如「公示日期」而非「现场公示时间」），严格相等会全部落空。
    """
    s = str(label or "").strip()
    if not s:
        return ""
    if s in SEMANTIC_CLASSES:
        return s
    for k in SEMANTIC_CLASSES:
        if k in s or s in k:
            return k
    # 关键词兜底
    kw_map = [
        (("项目名", "决策名", "工程名"), "项目名称"),
        (("文号", "公告号"), "公告文号"),
        (("责任单位", "主体"), "稳评责任单位"),
        (("实施单位", "评估机构"), "稳评实施单位"),
        (("位置", "坐落", "地址"), "项目位置"),
        (("平方米", "面积"), "征收面积（平方米）"),
        (("亩",), "征收面积（亩）"),
        (("户数", "农户"), "涉及户数"),
        (("人口", "人数"), "涉及人口"),
        (("问卷", "份数"), "问卷份数"),
        (("支持率",), "群众支持率"),
        (("反对率",), "反对率"),
        (("知晓率",), "知晓率"),
        (("公示", "公告期"), "现场公示时间"),
        (("勘察", "勘查", "踏勘"), "实地勘察时间"),
        (("座谈",), "座谈会时间"),
        (("委托", "受托"), "委托时间"),
        (("资金", "筹措"), "资金筹措"),
        (("补偿标准", "地价"), "补偿标准"),
        (("用途", "地类"), "土地用途"),
        (("固定", "不替换", "话术"), "无（固定话术，不替换）"),
    ]
    for kws, cls in kw_map:
        if any(k in s for k in kws):
            return cls
    return ""


def _classify_entry(c: Dict[str, Any], sem_label: str) -> Optional[Dict[str, Any]]:
    """把一个候选归成标注条目（LLM 优先 → 确定性兜底）。判为固定话术则返回 None。"""
    sem = _match_semantic(sem_label)
    field = SEMANTIC_CLASSES.get(sem, "")
    if field and field != "__fixed__":
        return {"anchor": c["anchor"], "type": "slot", "field": field,
                "locked": False, "source": "llm"}

    kind = c.get("kind", "")
    # 文号：项目公告文号 → doc_reference（法规文号已在抽取阶段被 is_regulation_citation 剔除）
    if kind == "doc_ref":
        return {"anchor": c["anchor"], "type": "slot", "field": "doc_reference",
                "locked": False, "source": "heuristic"}
    # 形状类：按「单位优先 + 上下文关键词」定字段
    if kind in ("date", "count", "pct", "area", "money"):
        fb = context_field(c["anchor"], c.get("context", ""), kind)
        return {"anchor": c["anchor"], "type": "slot", "field": fb or "",
                "locked": False, "source": "context" if fb else "heuristic",
                **({} if fb else {"note": f"形状类({kind})，请确认字段"})}
    if kind == "project_name":
        return {"anchor": c["anchor"], "type": "slot", "field": "project_name",
                "locked": False, "source": "heuristic"}
    if kind == "region_or_org":
        f = "org_name" if any(k in c["anchor"] for k in
                              ("政府", "委员会", "公司", "局", "办事处")) else "location"
        return {"anchor": c["anchor"], "type": "slot", "field": f,
                "locked": False, "source": "heuristic"}
    # 判为固定话术 → 不记录
    return None


async def learn_profile(
    template_id: Optional[str] = None,
    llm_service=None,
    *,
    save: bool = True,
) -> Dict[str, Any]:
    """对模板做一次槽位标注（正则抽候选 → LLM 归类 → 落盘待人工校订）。

    Args:
        template_id: 模板身份；缺省用 current_template_id()。
        llm_service: LLMService 实例；为 None 时只做启发式标注（全部标 fixed，待人工）。
        save: 是否落盘。

    Returns:
        标注表 dict。
    """
    tid = template_id or current_template_id()
    old = load_profile(tid)
    locked = {
        (str(ch), e.get("anchor")): e
        for ch, entries in (old.get("chapters") or {}).items()
        for e in (entries or []) if e.get("locked")
    }

    try:
        from app.services.template_chapter_chunker import build_and_cache
        _cached = build_and_cache()
        chunks = (_cached.get("chapters") or {})
        front_text = "\n".join(_cached.get("front") or [])
    except Exception as e:
        logger.warning(f"读取模板块失败: {e}")
        chunks, front_text = {}, ""

    chapters: Dict[str, List[Dict[str, Any]]] = {}
    for ch, blk in chunks.items():
        text = blk.get("text", "") if isinstance(blk, dict) else str(blk)
        cands = extract_candidates(int(ch), text)

        # LLM 分类（可选）
        semantic_of: Dict[str, str] = {}
        if llm_service and cands:
            semantic_of = await _classify_with_llm(cands, llm_service)

        entries: List[Dict[str, Any]] = []
        for c in cands:
            key = (str(ch), c["anchor"])
            if key in locked:                      # 人工校订过的原样保留
                entries.append(locked[key])
                continue
            e = _classify_entry(c, semantic_of.get(c["anchor"], ""))
            if e:
                entries.append(e)
        if entries:
            chapters[str(ch)] = entries

    # ── 前置部分（封面/公司简介）：项目名等必须全文替换，否则封面残留模板项目名 ──
    global_entries: List[Dict[str, Any]] = []
    front_cands = extract_candidates(0, front_text) if front_text else []
    if front_cands:
        semantic_front: Dict[str, str] = {}
        if llm_service:
            semantic_front = await _classify_with_llm(front_cands, llm_service)
        for c in front_cands:
            e = _classify_entry(c, semantic_front.get(c["anchor"], ""))
            if e:
                e["source"] = f"{e.get('source','')}-front".strip("-")
                global_entries.append(e)

    profile = {
        "template_id": tid,
        "reviewed": False,
        "global": global_entries,
        "chapters": chapters,
    }
    if save:
        save_profile(profile)
    return profile


async def _classify_with_llm(cands: List[Dict[str, Any]], llm_service) -> Dict[str, str]:
    """调 LLM 把候选归类到语义类。失败返回空 dict（退回启发式）。"""
    try:
        import json as _json
        listing = "\n".join(
            f'{i+1}. 片段：{c["anchor"]}\n   上下文：{c["context"]}'
            for i, c in enumerate(cands[:60])
        )
        classes = "\n".join(f"- {k}" for k in SEMANTIC_CLASSES)
        prompt = _LLM_PROMPT.format(candidates=listing, classes=classes)
        resp = await llm_service.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000, temperature=0.1,
        )
        content = str(resp or "")
        m = re.search(r'\[.*\]', content, re.S)
        if not m:
            return {}
        arr = _json.loads(m.group(0))
        return {str(x.get("anchor", "")).strip(): str(x.get("semantic", "")).strip()
                for x in arr if isinstance(x, dict)}
    except Exception as e:
        logger.warning(f"LLM 槽位归类失败，退回启发式: {e}")
        return {}


def mark_reviewed(template_id: Optional[str] = None) -> Path:
    """人工校订完成 → 标记 reviewed（未 reviewed 时槽位置信度降档）。"""
    profile = load_profile(template_id)
    profile["reviewed"] = True
    return save_profile(profile)
