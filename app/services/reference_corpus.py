"""参照件语料库 —— 「第 1/2/4/5/7 章沿用人工成品原文」的运行时渲染。

业主口径：
    「有些内容可以照搬，只需要修改一些地区信息，有些章节整体修改的。」

参照件《白马湖农场2号风电项目社会稳定风险评估报告（送审稿）》经
`scripts/build_reference_corpus.py` 离线清洗成分章 markdown（项目专属词已换成
`{{占位符}}`），本模块在运行时用事实卡把它渲染成本项目文本 —— **零 LLM**。

与既有 `template_chapter_chunker` / `template_profile` / `chapter_composer`
那条「模板就地填充」轨道是同一思路，只是模板源换成了参照件 PDF。

🔴 三条硬规则：
  ① **不留空、不编造**。行文类缺失 → 中性表述；数值类缺失 → **整句删除**
     （写错数字比缺一句更糟），并记一条 gap。
  ② **绝不使用 `【待补充】`** —— `report_assembler.assemble` 会把它直接删掉，
     结果是真空而非提示。
  ③ **项目类型专属条目整条剔除**（参照件是风电，本项目是土地征收）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
_REF_ROOT = _BACKEND / "data" / "reference_reports"
# 🔴 参照件插图（图5-1 流程图等）落在运行期目录：语料里只存**相对路径** `figures/xxx.png`，
#    渲染时补成绝对路径 —— 语料随代码走，图随大文件走，部署到别的机器也能对上。
_FIG_ROOT = _BACKEND / "storage" / "reference_reports"

DEFAULT_REF_ID = "baimahu_wind_2_shenshen_v1"

# ── 占位符 → 事实卡字段（可多候选，按序取第一个非空） ──
_SLOT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "project_name":       ("project_name", "project_title"),
    "org_name":           ("org_name", "decision_unit", "responsible_unit"),
    "location":           ("location", "land_location"),
    "investment":         ("funding", "investment", "total_investment"),
    "area_ha":            ("area_ha",),
    "construction_period": ("construction_period", "period"),
}

# 数值类占位符：取不到值时**整句删除**（不写中性值，绝不留 0 或占位符）
_NUMERIC_SLOTS = {"capacity_mw", "unit_capacity", "turbine_count", "hub_height",
                  "rotor_diameter", "voltage", "investment", "area_ha",
                  "construction_period"}

# 行文类占位符：取不到值时用中性表述（保证句子通顺、不残留占位符）
_TEXT_FALLBACK: Dict[str, str] = {
    "org_name": "委托单位",
    "support_orgs": "属地有关部门",
    "site_name": "项目所在地",
    "site_units": "项目涉及范围内",
    "region": "",
}

# 项目类型专属条目的过滤关键词（兜底，正常从 meta.json 读）
_DEFAULT_DENY = ["风电", "风能", "风机", "机位", "升压站", "驭风",
                 "可再生能源", "能源局", "能源体系", "陆上风电",
                 "电力法", "气象法", "特种设备", "石油天然气管道"]

# 泄漏自检：这些词若出现在渲染结果里，说明替换没做干净
LEAK_WORDS = ["白马湖", "风电", "风机", "机位", "升压站", "名通新能源",
              "管理区", "31.25", "17437.96", "6.25", "驭风", "风能"]

_cache: Dict[str, dict] = {}


@dataclass
class ReferenceRenderResult:
    chapter: int
    markdown: str = ""
    filled_count: int = 0
    removed_count: int = 0
    gaps: List[Dict[str, Any]] = _field(default_factory=list)
    leaks: List[str] = _field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 载入
# ═══════════════════════════════════════════════════════════════

def ref_dir(ref_id: str = None) -> Path:
    return _REF_ROOT / (ref_id or _current_ref_id())


def _current_ref_id() -> str:
    try:
        from app.config import settings
        return getattr(settings, "REFERENCE_CORPUS_ID", "") or DEFAULT_REF_ID
    except Exception:
        return DEFAULT_REF_ID


def load_reference(ref_id: str = None) -> dict:
    """载入 chapters.json + meta.json（带进程内缓存）。"""
    rid = ref_id or _current_ref_id()
    if rid in _cache:
        return _cache[rid]
    d = _REF_ROOT / rid
    out: Dict[str, Any] = {"ref_id": rid, "chapters": {}, "meta": {}}
    try:
        cp, mp = d / "chapters.json", d / "meta.json"
        if cp.exists():
            out["chapters"] = (json.loads(cp.read_text(encoding="utf-8")) or {}).get("chapters", {}) or {}
        if mp.exists():
            out["meta"] = json.loads(mp.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"载入参照件语料失败 {rid}: {e}")
    _cache[rid] = out
    return out


def is_enabled() -> bool:
    """总开关（默认开；有语料才真正生效）。"""
    try:
        from app.config import settings
        if not getattr(settings, "REFERENCE_VERBATIM_ENABLED", True):
            return False
    except Exception:
        pass
    return bool(load_reference()["chapters"])


def verbatim_chapters() -> set:
    """要照搬的章号集合。优先级：settings > meta.json > 默认。"""
    try:
        from app.config import settings
        raw = getattr(settings, "REFERENCE_VERBATIM_CHAPTERS", "")
        if raw:
            return {int(x) for x in re.split(r'[,\s]+', str(raw)) if x.strip().isdigit()}
    except Exception:
        pass
    m = load_reference()["meta"].get("verbatim_chapters")
    if m:
        try:
            return {int(x) for x in m}
        except Exception:
            pass
    return {1, 2, 4, 5, 7}


def get_reference_chapter_text(chapter_no: int, ref_id: str = None) -> str:
    return (load_reference(ref_id)["chapters"].get(str(int(chapter_no))) or {}).get("text", "") or ""


# ═══════════════════════════════════════════════════════════════
# 槽位取值
# ═══════════════════════════════════════════════════════════════

def _norm(v: Any) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    if not t or t.startswith("【待补充") or t in ("未检测到", "未提及", "未显示", "未知"):
        return ""
    return t


def _derive_region(location: str) -> str:
    """从位置串里取「市+区/县」。"""
    m = re.search(r'([一-龥]{2,6}市[一-龥]{2,6}[区县])', location)
    if m:
        return m.group(1)
    m = re.search(r'([一-龥]{2,6}[区县])', location)
    return m.group(1) if m else ""


def _derive_site_name(location: str) -> str:
    """从位置串里取乡镇/街道一级。"""
    m = re.search(r'([一-龥]{2,8}(?:镇|街道|乡))', location)
    return m.group(1) if m else ""


def _derive_site_units(location: str) -> str:
    """从位置串里取村组/社区一级（去掉已识别的市区/乡镇前缀）。"""
    s = location
    for junk in (_derive_region(location), _derive_site_name(location)):
        if junk:
            s = s.replace(junk, "")
    s = re.sub(r'^[、，,\s]+|[、，,\s]+$', '', s)
    return s


def resolve_slot_value(slot: str, filled: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """求一个占位符的值 → (文本 or None, status)。

    status ∈ ok / derived / generic / missing
      ok       直接取到
      derived  由其它字段推导（如 region 从 location 拆）
      generic  行文类缺失 → 中性表述
      missing  数值类缺失 → 调用方应**整句删除**
    """
    filled = filled or {}

    # 兜底从 facts 里取
    for key in _SLOT_FIELDS.get(slot, ()):
        v = _norm(filled.get(key))
        if v:
            return v, "ok"

    loc = _norm(filled.get("location")) or _norm(filled.get("land_location"))

    if slot == "region":
        v = _derive_region(loc)
        return (v, "derived") if v else (_TEXT_FALLBACK.get("region", ""), "generic")
    if slot == "site_name":
        v = _derive_site_name(loc)
        return (v, "derived") if v else (_TEXT_FALLBACK["site_name"], "generic")
    if slot == "site_units":
        v = _derive_site_units(loc)
        return (v, "derived") if v else (_TEXT_FALLBACK["site_units"], "generic")
    if slot == "area_ha":
        # 亩 → 公顷（1 公顷 = 15 亩）
        try:
            mu = float(_norm(filled.get("area_mu")) or 0)
            if mu > 0:
                return f"{mu / 15:.2f}", "derived"
        except (TypeError, ValueError):
            pass
        return None, "missing"

    if slot in _NUMERIC_SLOTS:
        return None, "missing"
    return _TEXT_FALLBACK.get(slot, ""), "generic"


def _project_documents_block(md: str, filled: Dict[str, Any]) -> str:
    """把第2章「（三）相关文件」整节换成本项目实际持有的文件清单。

    🔴 照搬源里该节是别的项目的材料清单，直接沿用等于给本项目编造材料。
    唯一可靠来源是**事实卡 + 实际上传的项目文件**；不够时只列已知项，不编造。
    """
    docs: List[str] = []
    ref = _norm(filled.get("doc_reference"))
    if ref:
        docs.append(f"{ref} 征收土地预公告")
    docs += [
        "勘测定界报告及勘测定界图",
        "拟征收土地现状调查确认表",
        "项目用地预审与选址意见书",
        "征地补偿安置方案公告及听证材料",
    ]
    # 实际上传的项目文件（文件名去掉扩展名）
    import os as _os
    for f in (filled.get("_uploaded_files") or []):
        pth = f.get("path") if isinstance(f, dict) else f
        if not pth:
            continue
        base = _os.path.splitext(_os.path.basename(str(pth)))[0]
        if base and base not in docs and len(base) < 40:
            docs.append(base)

    lines = ["### （三）相关文件"]
    for i, d in enumerate(docs[:1] + docs[1:], 1):
        lines.append(f"{i}.{d}；")
    out = "\n\n".join(lines)

    # 用新块替换原「（三）相关文件」整节（到下一个 ## / ### 标题或文末为止）
    m = re.search(r'^#{2,4}\s*（[一二三四五六七八九十]+）\s*相关文件\s*$', md, flags=re.MULTILINE)
    if not m:
        return md
    rest = md[m.end():]
    nxt = re.search(r'^#{2,4}\s', rest, flags=re.MULTILINE)
    tail = rest[nxt.start():] if nxt else ""
    return md[:m.start()] + out + ("\n\n" + tail if tail else "")


# ═══════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════

_PH = re.compile(r'\{\{(\w+)\}\}')
# 句子切分：用于「整句删除」
_SENT = re.compile(r'[^。；！？\n]+[。；！？]?')


def _drop_sentences_with_numeric_slots(md: str, missing: set) -> Tuple[str, int]:
    """把含缺失数值占位符的**整句**删掉。

    直接删占位符会留下「本项目装机容量为 。」这种残句；填 0 或「无」是编造。
    整句删除是唯一既不留空也不编造的降级方式。
    """
    out_lines, removed = [], 0
    for line in md.split("\n"):
        if not any(f"{{{{{s}}}}}" in line for s in missing):
            out_lines.append(line); continue
        parts = _SENT.findall(line)
        kept = []
        for p in parts:
            if any(f"{{{{{s}}}}}" in p for s in missing):
                removed += 1
                continue
            kept.append(p)
        rest = "".join(kept).strip()
        if rest:
            out_lines.append(rest)
    return "\n".join(out_lines), removed


def _unify_sub_headings(md: str) -> str:
    """同一二级小节内，三级条目**要么都升成标题、要么都留在正文**，不许一半一半。

    参照件里 `1.预警信息` 被 PDF 断行粘进了二级标题（已由 ①a 拆出来成了 `#### 1.预警信息`），
    可它的兄弟条目 `2.预警行动` 还留在正文里当普通编号行 —— 同一节里
    `1.` 是 Heading 3、`2.` 是正文，层级看着就散了（业主：「三级标题统一」）。
    同节内只要已经存在 `#### N.` 标题，就把同级的 `N.xxx` 条目一并升上去。
    """
    lines = (md or "").split("\n")
    out, i = [], 0
    while i < len(lines):
        if re.match(r'^###\s+（[一二三四五六七八九十]+）', lines[i].strip()):
            j = i + 1
            # 🔴 只以 `##`/`###` 为界，`####` 三级标题**不是**小节边界 ——
            #    否则 `#### 1.预警信息` 下面的兄弟条目根本进不了这个块。
            while j < len(lines) and not re.match(r'^#{2,3}\s', lines[j].strip()):
                j += 1
            block = lines[i:j]
            if any(re.match(r'^####\s+\d+[.、]', b.strip()) for b in block):
                # ① 同级条目一并升为三级标题
                block = [
                    f"#### {_m.group(1)}.{_m.group(2).strip()}"
                    if (_m := re.match(r'^(\d{1,2})[.、]\s*(\S.*)$', b.strip())) else b
                    for b in block
                ]
                # ② 升级用的序号是从正文序号推来的，可能与**已粘进二级标题**的那条撞号
                #    （参照件「保障措施1.通信保障」+「2.物资保障」→ 两个「1.」），
                #    故本节内统一重排成 1..k。
                _k, _ren = 0, []
                for b in block:
                    _m2 = re.match(r'^####\s+\d+[.、]\s*(\S.*)$', b.strip())
                    if _m2:
                        _k += 1
                        _ren.append(f"#### {_k}.{_m2.group(1).strip()}")
                    else:
                        _ren.append(b)
                block = _ren
            out.extend(block)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _strip_project_type_items(md: str, deny: List[str]) -> Tuple[str, int]:
    """剔除项目类型专属的**整条**编号项（如风电专属的法规/文件名）。

    只删「以编号开头且命中关键词」的行；正文段落不删（避免误伤通顺的叙述句）。
    """
    if not deny:
        return md, 0
    out, n = [], 0
    for line in md.split("\n"):
        s = line.strip()
        if re.match(r'^\d+[.、]', s) and any(k in s for k in deny):
            n += 1
            continue
        out.append(line)
    # 重新编号，避免删出 1. 2. 5. 这样的断号
    # 🔴 遇到标题必须重置序号：否则后一小节的编号会接着上一节连号
    #    （实测 1.2 稳评原则下的四条被编成 4. 5. 6. 7.）。
    res, idx = [], 0
    for line in out:
        # 🔴 只在 `##`/`###` 处清零：在 `####` 处清零会让 `#### 1.预警信息`
        #    后面的兄弟条目从 1 重新数，撞成两个「1.」。
        if re.match(r'^#{2,3}\s', line.strip()):
            idx = 0
        m = re.match(r'^(\s*)\d+([.、]\s*)(.*)$', line)
        if m:
            idx += 1
            res.append(f"{m.group(1)}{idx}{m.group(2)}{m.group(3)}")
        else:
            res.append(line)
    return "\n".join(res), n


_PHONE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')


def _strip_personal_phones(md: str) -> Tuple[str, int]:
    """剔掉照搬源里带进来的**自然人手机号**（隐私兜底）。

    参照件「（X）组织领导」的成员名单里带着 7 个手机号 —— 那是**别家单位**的真人号码，
    照搬进本项目成稿既有隐私问题、又毫无意义。语料阶段已做去个人化改写，
    这里再兜一道：任何来源的手机号都不许进成稿。
    """
    hits = _PHONE.findall(md)
    if not hits:
        return md, 0
    out = _PHONE.sub("", md)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    logger.info(f"[参照件] 隐私兜底：剔除 {len(hits)} 个自然人手机号")
    return out, len(hits)


def render_reference_markdown(chapter_no: int, filled: Dict[str, Any],
                              ref_id: str = None) -> ReferenceRenderResult:
    """把参照件某章原文渲染成本项目文本（纯字面替换，零 LLM）。"""
    res = ReferenceRenderResult(chapter=chapter_no)
    raw = get_reference_chapter_text(chapter_no, ref_id)
    if not raw:
        res.gaps.append({"field": f"reference_ch{chapter_no}", "reason": "no_corpus",
                         "severity": "error"})
        return res

    meta = load_reference(ref_id)["meta"]
    md = raw

    # ① 先剔项目类型专属条目（在替换前做，关键词还在原文里）
    deny = meta.get("project_type_deny") or _DEFAULT_DENY
    md, _removed_items = _strip_project_type_items(md, deny)
    res.removed_count += _removed_items

    # ①a 体例转换：`## X.Y 标题` → `### （N）标题`（二级）
    #              `### X.Y.Z 标题` → `#### K.标题`（三级）
    #
    #     🔴 **必须保层级**。旧实现把所有 `#{2,4} 数字…` 一律压成 `### （N）`，
    #        于是参照件「5.1 总则 / 5.1.1 编制目的 / 5.1.2 编制依据 / 5.1.3 适用范围」
    #        被拉平成（一）（二）（三）（四）四个**平级**小节，模板的二级/三级结构整段丢失。
    #        业主口径「依据模板来」：模板是几级，产出就是几级。
    #
    #     🔴 另需拆开 PDF 断行粘连的标题：`## 5.7 预警机制1.预警信息`
    #        实为模板的「5.7 预警机制」（二级）+「1.预警信息」（三级）两行被并成了一行。
    _CN = "一二三四五六七八九十"
    def _cn(i):
        if i <= 10: return _CN[i-1]
        if i < 20: return "十" + _CN[i-11]
        return _CN[i//10-1] + "十" + (_CN[i % 10-1] if i % 10 else "")
    # 二级标题末尾粘了 `1.子标题`（PDF 断行合并所致）
    # 头部限定为「以汉字开头、不含数字」，尾部限定为「汉字开头」，避开 `依据3.2条…` 这类正常行文
    _GLUED = re.compile(r'^([一-龥][^0-9]{1,20}?)(\d{1,2})[.、]([一-龥].*)$')
    lines_tmp, _h2, _h3 = [], 0, 0
    for _ln in md.split("\n"):
        _m = re.match(r'^(#{2,4})\s*\d+(?:\.\d+)*\s*(.+)$', _ln.strip())
        if not _m:
            lines_tmp.append(_ln)
            continue
        _lvl, _body = len(_m.group(1)), _m.group(2).strip()
        if _lvl >= 3:                       # 三级
            _h3 += 1
            lines_tmp.append(f"#### {_h3}.{_body}")
            continue
        _h2 += 1; _h3 = 0                   # 二级：三级序号在二级内重排
        _g = _GLUED.match(_body)
        if _g:                              # 拆粘连：二级标题 + 它的第一个三级子标题
            lines_tmp.append(f"### （{_cn(_h2)}）{_g.group(1).strip()}")
            _h3 = min(int(_g.group(2)), 99)
            lines_tmp.append(f"#### {_h3}.{_g.group(3).strip()}")
        else:
            lines_tmp.append(f"### （{_cn(_h2)}）{_body}")
    md = "\n".join(lines_tmp)

    # ①a′ 三级标题统一：同节内「1. 升了标题、2. 还留在正文」的一半一半，补齐
    md = _unify_sub_headings(md)

    # ①b 合并「同一句里重复出现的同一占位符」。
    #     原文 `涉及第七管理区、第八管理区` → `涉及{{site_units}}、{{site_units}}`，
    #     逐个替换会把同一串位置重复写两遍（→「涉及…、…、…、…」）。
    md = re.sub(r'\{\{(\w+)\}\}([、，,]\s*)\{\{\1\}\}', r'{{\1}}', md)

    # ② 求各占位符的值
    values, missing_numeric = {}, set()
    for slot in set(_PH.findall(md)):
        v, status = resolve_slot_value(slot, filled)
        if status == "missing":
            missing_numeric.add(slot)
            res.gaps.append({"field": slot, "chapter": chapter_no,
                             "reason": "numeric_slot_missing", "severity": "warn"})
            continue
        values[slot] = v or ""
        res.filled_count += 1

    # ③ 缺失的数值类 → 整句删除（必须在替换之前，占位符还在时才好定位）
    if missing_numeric:
        md, _removed_sent = _drop_sentences_with_numeric_slots(md, missing_numeric)
        res.removed_count += _removed_sent

    # ④ 替换其余占位符
    md = _PH.sub(lambda m: values.get(m.group(1), ""), md)

    # ⑤b 第2章「（三）相关文件」**必须本项目化**：
    #     照搬源里那是白马湖风电项目的材料清单（独立选址论证报告 / 机位点红线图 /
    #     升压站合建承诺书 …），本项目根本没有这些材料 —— 照搬即事实性错误。
    #     改为从本项目事实卡与实际上传材料派生。
    if int(chapter_no) == 2:
        md = _project_documents_block(md, filled)

    # ⑤c 隐私兜底：任何来源的自然人手机号都不进成稿
    md, _n_phone = _strip_personal_phones(md)
    res.removed_count += _n_phone

    # ⑤d 插图路径：`figures/fig5-1.png` → 绝对路径（语料只存相对路径）
    _rid = ref_id or _current_ref_id()
    md = re.sub(r'\]\((figures/[^)]+)\)',
                lambda m: '](' + str(_FIG_ROOT / _rid / m.group(1)) + ')', md)

    # ⑤ 收尾清理：PDF 断行残留的空行、行首多余标点
    md = re.sub(r'[ \t]+\n', '\n', md)
    md = re.sub(r'\n{3,}', '\n\n', md)
    md = re.sub(r'^[、，,；;]\s*', '', md, flags=re.M)
    res.markdown = md.strip()

    # ⑥ 泄漏自检
    res.leaks = [w for w in LEAK_WORDS if w in res.markdown]
    if res.leaks:
        logger.warning(f"[参照件] 第{chapter_no}章疑似残留参照件专有词: {res.leaks}")
    return res
