# -*- coding: utf-8 -*-
"""要求—章节覆盖矩阵 + 字数预算闸门（交付前的确定性卡口）。

业主口径（2026-11-16）：
    「生成结束后要能看出这份报告缺没缺东西、够不够篇幅，缺了就不要算可交付。」

两类检查都是**纯确定性**的：不调模型、不读数据库，装配完直接算。

1. 覆盖矩阵（`check_coverage`）
   每个报告类型有一张「审查要点 / 规范要求」清单（`REQUIREMENTS`），
   逐条在章节正文里找关键词。**critical 项缺一条 = 不可交付**；
   warn 项只提示。清单里的依据写成可核对的规范/文件，方便业主自己改。

2. 字数预算（`check_word_budget`）
   每章的 (下限, 上限) 来自 `chapter_catalog.target_words`（单一事实源，
   与生成提示词、续写补足用的是同一份）。判三条：
     · 该写的章有没有正文（fact 章缺正文 = 阻断）
     · 单章是否低于下限的 60%（明显偏薄 = 阻断）
     · 全文合计是否低于下限合计的 80%（整体偏薄 = 阻断）

入口：`attach(state)` —— 由 `report_assembler.assemble()` 在保存前调用，
把 `_coverage` / `_word_budget` / `_deliverable` / `_deliverable_blockers`
写回 state；工作流与会话接口直接读这几个键。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 全文字数 / 全文字数下限合计，低于这个比例判「整体偏薄」
WORD_TOTAL_MIN_RATIO = 0.80
# 单章字数 / 该章下限，低于这个比例判「明显偏薄」
WORD_CHAPTER_MIN_RATIO = 0.60


@dataclass(frozen=True)
class Requirement:
    """一条审查要求。`patterns` 任一命中即算覆盖。"""

    id: str
    name: str
    basis: str
    patterns: Tuple[str, ...]
    severity: str = "critical"          # critical = 缺了不可交付
    expected: Tuple[int, ...] = ()      # 建议出现在哪些章（只用于提示）


# ═══════════════════════════════════════════════════════════════════
# 稳评（DB32/T4013-2021 + 中办发〔2021〕11号 + 江苏省案卷口径）
# ═══════════════════════════════════════════════════════════════════

_STABILITY_REQUIREMENTS: Tuple[Requirement, ...] = (
    Requirement("stab-project-basic", "项目基本情况（名称/位置/实施单位）",
                "DB32/T4013-2021 6.1 项目概况", (r"(?:项目名称|工程名称)",),
                "critical", (1, 4, 6)),
    Requirement("stab-area", "征收（用地）范围与面积",
                "《土地管理法》第四十七条；勘测定界报告", (r"(?:征收面积|征地面积|用地面积|拟征收面积)",),
                "critical", (4, 6)),
    Requirement("stab-boundary", "四至范围",
                "勘测定界报告；江苏省稳评案卷要求", (r"东至[^。\n]{1,60}?北至",),
                "critical", (6,)),
    Requirement("stab-landuse", "土地用途（规划用途）",
                "《土地管理法》第四十五条", (r"(?:土地用途|规划用途|用途为)",),
                "warn", (4, 6)),
    Requirement("stab-compensation", "征地补偿标准",
                "《土地管理法》第四十八条；区片综合地价", (r"(?:区片综合地价|征地补偿标准|补偿标准|安置补助费)",),
                "critical", (4, 6, 9)),
    Requirement("stab-social-security", "被征地农民社会保障",
                "《土地管理法》第四十八条；江苏省被征地农民社会保障办法",
                (r"(?:社会保障|社保|被征地农民)",), "warn", (9,)),
    Requirement("stab-legality", "征收合法性依据",
                "《土地管理法》第四十五条、第四十七条；中办发〔2021〕11号",
                (r"(?:土地管理法|符合(?:国民经济|国土空间|土地)|公共利益)",),
                "critical", (10,)),
    Requirement("stab-questionnaire", "问卷调查",
                "DB32/T4013-2021 7.1 调查方法", (r"(?:调查问卷|问卷)",),
                "critical", (8, 9)),
    Requirement("stab-symposium", "座谈会 / 听证 / 民意调查",
                "DB32/T4013-2021 7.2 公众参与", (r"(?:座谈会|听证会|民意调查)",),
                "warn", (8, 9)),
    Requirement("stab-publicity", "公示公告",
                "《土地管理法》第四十七条；征收土地公告办法", (r"(?:公示|公告)",),
                "warn", (8, 9)),
    Requirement("stab-stakeholders", "利益相关者与群众意见",
                "DB32/T4013-2021 7.3 利益相关者分析", (r"(?:利益相关者|群众意见|受访(?:群众|村民)|村民代表)",),
                "critical", (8, 9)),
    Requirement("stab-risk-identify", "风险因素识别",
                "DB32/T4013-2021 8.1 风险识别", (r"(?:风险因素|风险源|风险点)",),
                "critical", (5, 11)),
    Requirement("stab-risk-level", "风险等级判定",
                "DB32/T4013-2021 8.3 风险等级", (r"(?:风险等级|低风险|中风险|高风险)",),
                "critical", (12,)),
    Requirement("stab-score", "评分分值",
                "DB32/T4013-2021 8.2 风险估计", (r"(?:得分|分值|评分)",),
                "warn", (12,)),
    Requirement("stab-measures", "风险防范化解措施",
                "中办发〔2021〕11号；DB32/T4013-2021 9 风险防范", (r"(?:化解措施|防范措施|风险防范|化解方案)",),
                "critical", (13,)),
    Requirement("stab-emergency", "应急处置预案",
                "中办发〔2021〕11号", (r"(?:应急预案|处置预案|突发性事件|群体性事件)",),
                "warn", (5, 13)),
    Requirement("stab-conclusion", "稳评结论与工作建议",
                "DB32/T4013-2021 10 评估结论", (r"(?:稳评结论|评估结论|结论与建议)",),
                "critical", (4, 12, 13)),
    Requirement("stab-org", "组织保障 / 工作小组",
                "中办发〔2021〕11号", (r"(?:领导小组|稳评小组|工作组|工作专班)",),
                "warn", (7,)),
    Requirement("stab-basis", "编制依据（法规 / 规范）",
                "DB32/T4013-2021 5 编制依据", (r"(?:DB32/T\s*4013|中办发|国务院令|土地管理法实施条例)",),
                "warn", (2,)),
)


# ═══════════════════════════════════════════════════════════════════
# 复垦（《土地复垦条例》+ TD/T 1031.1-2011 编制规程）
# ═══════════════════════════════════════════════════════════════════

_RECLAMATION_REQUIREMENTS: Tuple[Requirement, ...] = (
    Requirement("rec-service-years", "复垦方案服务年限",
                "TD/T 1031.1-2011 4.2.1", (r"(?:服务年限|复垦年限)",),
                "critical", (1,)),
    Requirement("rec-area", "复垦区 / 损毁面积",
                "TD/T 1031.1-2011 4.3 复垦区确定", (r"(?:复垦区面积|损毁面积|复垦面积)",),
                "critical", (1, 4)),
    Requirement("rec-landuse", "项目区土地利用现状",
                "TD/T 1031.1-2011 4.4 土地利用现状", (r"(?:土地利用现状|地类|耕地)",),
                "critical", (3, 4)),
    Requirement("rec-natural", "项目区自然概况",
                "TD/T 1031.1-2011 4.4.2", (r"(?:气候|地貌|土壤|水文)",),
                "warn", (3,)),
    Requirement("rec-social", "社会经济概况",
                "TD/T 1031.1-2011 4.4.3", (r"(?:社会经济|人口|人均|生产总值|劳动力)",),
                "warn", (3,)),
    Requirement("rec-damage", "土地损毁分析与评定",
                "TD/T 1031.1-2011 5 土地损毁分析与评定", (r"(?:损毁|压占|挖损|塌陷)",),
                "critical", (4,)),
    Requirement("rec-suitability", "土地复垦适宜性评价",
                "TD/T 1031.1-2011 6 适宜性评价", (r"(?:适宜性评价|评价单元)",),
                "critical", (4,)),
    Requirement("rec-direction", "复垦方向与标准",
                "TD/T 1031.1-2011 7 复垦方向", (r"(?:复垦方向|复垦为|恢复为)",),
                "critical", (4, 5)),
    Requirement("rec-water-soil", "水土资源平衡分析",
                "TD/T 1031.1-2011 8", (r"(?:水土资源平衡|水资源平衡|土源平衡|表土)",),
                "warn", (4, 5)),
    Requirement("rec-eco", "生态空间管控 / 生态保护红线",
                "自然资源部 自然资发〔2022〕130号", (r"(?:生态保护红线|生态空间管控)",),
                "warn", (4,)),
    Requirement("rec-quantity", "工程量测算",
                "TD/T 1031.1-2011 9 复垦工程量", (r"(?:工程量|土方|清淤|回填)",),
                "critical", (6,)),
    Requirement("rec-investment", "投资估算",
                "TD/T 1031.1-2011 10 投资估算", (r"(?:静态总投资|动态总投资|投资估算|亩均投资)",),
                "critical", (7,)),
    Requirement("rec-rate", "土地复垦率",
                "TD/T 1031.1-2011 11 复垦率", (r"复垦率",),
                "warn", (1, 5)),
    Requirement("rec-schedule", "复垦工作计划与进度",
                "TD/T 1031.1-2011 12 工作计划", (r"(?:工作计划|进度安排|年度计划|实施计划)",),
                "warn", (9,)),
    Requirement("rec-guarantee", "保障措施",
                "TD/T 1031.1-2011 13 保障措施", (r"(?:保障措施|组织保障|资金保障|技术保障)",),
                "critical", (10,)),
    Requirement("rec-ownership", "土地权属",
                "《土地复垦条例》第十条", (r"(?:土地权属|权属|集体土地所有权)",),
                "warn", (3, 8)),
    Requirement("rec-public", "公众参与",
                "《土地复垦条例》第十四条", (r"(?:公众参与|公示|座谈|问卷调查)",),
                "warn", (8,)),
    Requirement("rec-standards", "技术标准依据",
                "TD/T 1031.1、TD/T 1036、GB/T 21010",
                (r"(?:TD/T\s*1031|TD/T\s*1036|GB/T\s*21010)",), "warn", (2,)),
    Requirement("rec-topsoil", "表土剥离与利用",
                "TD/T 1031.1-2011 9.2；自然资发〔2022〕130号", (r"(?:表土剥离|剥离表土|表土利用)",),
                "warn", (6,)),
    Requirement("rec-quality", "复垦质量要求",
                "TD/T 1036-2013；TD/T 1031.1-2011 7.3", (r"(?:覆土厚度|复垦质量|有效土层|耕作层)",),
                "warn", (5,)),
)


REQUIREMENTS: Dict[str, Tuple[Requirement, ...]] = {
    "stability": _STABILITY_REQUIREMENTS,
    "reclamation": _RECLAMATION_REQUIREMENTS,
}


# ═══════════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════════

_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$", re.MULTILINE)
_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_NOISE_RE = re.compile(r"[#*`>|~\s]")


def plain_char_count(md: str) -> int:
    """去掉 markdown 记法后的正文字数（与 `chapter_base._plain_char_count` 同口径）。"""
    s = _IMG_MD_RE.sub("", md or "")
    s = _TABLE_SEP_RE.sub("", s)
    s = _MD_NOISE_RE.sub("", s)
    return len(s)


def _chapter_md(chapters: Dict[Any, Any], no: int) -> str:
    """取某章正文；键可能是 int 或 str（会话落盘后是字符串键）。"""
    data = None
    for key in (no, str(no)):
        if key in (chapters or {}):
            data = chapters.get(key)
            break
    if isinstance(data, dict):
        return str(data.get("markdown") or "")
    return str(data or "")


def _iter_chapters(chapters: Dict[Any, Any]) -> Iterable[Tuple[Any, str]]:
    for key in sorted(chapters or {}, key=lambda x: str(x)):
        data = chapters.get(key)
        if isinstance(data, dict):
            md = str(data.get("markdown") or "")
        else:
            md = str(data or "")
        if md:
            yield key, md


def _snippet(text: str, start: int, end: int, pad: int = 12) -> str:
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return re.sub(r"\s+", " ", text[a:b]).strip()


# ═══════════════════════════════════════════════════════════════════
# 覆盖矩阵
# ═══════════════════════════════════════════════════════════════════

def check_coverage(chapters: Dict[Any, Any], domain: str = "stability",
                   requirements: Optional[Tuple[Requirement, ...]] = None) -> Dict[str, Any]:
    """逐条检查审查要求是否在正文里出现。

    Returns:
        {
          "domain", "total", "covered", "ratio",
          "missing": [{"id","name","basis","severity","expected"}],
          "missing_critical": [同上仅 critical],
          "rows": [{"id","name","basis","severity","expected","chapters","evidence","covered"}],
        }
    """
    reqs = requirements if requirements is not None else REQUIREMENTS.get(domain, ())
    texts = list(_iter_chapters(chapters))
    rows: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []

    for req in reqs:
        hit_chs: List[Any] = []
        evidence = ""
        for key, md in texts:
            for pat in req.patterns:
                try:
                    m = re.search(pat, md)
                except re.error as e:            # 规则写错也不能把整份报告卡死
                    logger.warning(f"[覆盖] 规则 {req.id} 正则非法: {e}")
                    continue
                if not m:
                    continue
                hit_chs.append(key)
                if not evidence:
                    evidence = _snippet(md, m.start(), m.end())
                break
        row = {
            "id": req.id, "name": req.name, "basis": req.basis,
            "severity": req.severity, "expected": list(req.expected),
            "chapters": hit_chs, "evidence": evidence,
            "covered": bool(hit_chs),
        }
        rows.append(row)
        if not hit_chs:
            missing.append({k: row[k] for k in
                            ("id", "name", "basis", "severity", "expected")})

    covered = sum(1 for r in rows if r["covered"])
    return {
        "domain": domain,
        "total": len(rows),
        "covered": covered,
        "ratio": round(covered / len(rows), 4) if rows else 1.0,
        "missing": missing,
        "missing_critical": [m for m in missing if m.get("severity") == "critical"],
        "rows": rows,
    }


# ═══════════════════════════════════════════════════════════════════
# 字数预算
# ═══════════════════════════════════════════════════════════════════

def check_word_budget(chapters: Dict[Any, Any], domain: str = "stability") -> Dict[str, Any]:
    """按章核对字数：缺正文 / 明显偏薄 / 全文偏薄。

    Returns:
        {
          "passed", "total_words", "total_min_words", "total_ratio",
          "chapters": [{"chapter","title","words","min_words","max_words","ratio","status"}],
          "missing_chapters", "thin_chapters",
        }
    """
    from app.agent import chapter_catalog

    if domain not in ("stability", "reclamation"):
        # 投标等其它域没有这套字数目标，直接判通过（不给装配器报假阻断）
        return {"passed": True, "total_words": 0, "total_min_words": 0,
                "total_ratio": 1.0, "chapters": [], "missing_chapters": [],
                "thin_chapters": [], "skipped": True}
    try:
        titles = chapter_catalog.chapter_titles(domain)
    except Exception as e:
        logger.warning(f"[字数] 取章标题失败: {e}")
        titles = {}

    rows: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    thin: List[Dict[str, Any]] = []
    total_words = 0
    total_min = 0

    for no in sorted(titles):
        kind = chapter_catalog.chapter_kind(domain, no)
        if kind != "fact":                 # 评审表/附件等由装配器渲染，不吃字数
            continue
        lo, hi = chapter_catalog.target_words(domain, no)
        lo = int(lo or 0)
        if lo <= 0:
            continue
        md = _chapter_md(chapters, no)
        words = plain_char_count(md)
        total_words += words
        total_min += lo
        ratio = (words / lo) if lo else 1.0
        if not md.strip():
            status = "missing"
        elif ratio < WORD_CHAPTER_MIN_RATIO:
            status = "thin"
        elif words < lo:
            status = "short"
        else:
            status = "ok"
        row = {"chapter": no, "title": titles.get(no, f"第{no}章"),
               "words": words, "min_words": lo, "max_words": int(hi or 0),
               "ratio": round(ratio, 4), "status": status}
        rows.append(row)
        if status == "missing":
            missing.append(row)
        elif status == "thin":
            thin.append(row)

    total_ratio = (total_words / total_min) if total_min else 1.0
    passed = (not missing and not thin
              and total_ratio >= WORD_TOTAL_MIN_RATIO)
    return {
        "passed": passed,
        "total_words": total_words,
        "total_min_words": total_min,
        "total_ratio": round(total_ratio, 4),
        "chapters": rows,
        "missing_chapters": missing,
        "thin_chapters": thin,
    }


# ═══════════════════════════════════════════════════════════════════
# 闸门
# ═══════════════════════════════════════════════════════════════════

def evaluate(chapters: Dict[Any, Any], filled: Optional[Dict[str, Any]] = None,
             domain: str = "stability") -> Dict[str, Any]:
    """覆盖矩阵 + 字数预算 → 是否可交付 + 阻断项清单。

    `filled` 暂时只用于日志（后续可加「必填数据是否进了正文」的检查），
    保留参数是为了调用方不用等到那时再改签名。
    """
    domain = domain or "stability"
    coverage = check_coverage(chapters, domain)
    budget = check_word_budget(chapters, domain)

    blockers: List[Dict[str, str]] = []
    for item in coverage.get("missing_critical") or []:
        where = ("（应在第%s章）" % "、".join(str(x) for x in item.get("expected") or [])
                 if item.get("expected") else "")
        blockers.append({
            "kind": "coverage",
            "message": f"缺必需要求「{item['name']}」{where}｜依据：{item['basis']}",
        })
    for row in budget.get("missing_chapters") or []:
        blockers.append({
            "kind": "words",
            "message": f"第{row['chapter']}章「{row['title']}」没有正文",
        })
    for row in budget.get("thin_chapters") or []:
        blockers.append({
            "kind": "words",
            "message": (f"第{row['chapter']}章「{row['title']}」{row['words']} 字，"
                        f"不足下限 {row['min_words']} 字的 "
                        f"{int(WORD_CHAPTER_MIN_RATIO * 100)}%"),
        })
    if (budget.get("total_min_words") and budget.get("total_ratio", 1) < WORD_TOTAL_MIN_RATIO):
        blockers.append({
            "kind": "words",
            "message": (f"全文字数 {budget['total_words']} 字，仅为下限合计 "
                        f"{budget['total_min_words']} 字的 "
                        f"{round(budget['total_ratio'] * 100)}%"),
        })

    deliverable = not blockers
    logs = [summarize({"coverage": coverage, "word_budget": budget,
                       "deliverable": deliverable, "blockers": blockers})]
    if blockers:
        logs.append("⛔ 覆盖/字数闸门未通过，标记为**不可交付**："
                    + "；".join(b["message"] for b in blockers[:6])
                    + (f"（共 {len(blockers)} 项）" if len(blockers) > 6 else ""))
    else:
        logs.append("✅ 覆盖矩阵与字数预算通过，可交付")

    return {
        "domain": domain,
        "deliverable": deliverable,
        "blockers": blockers,
        "coverage": coverage,
        "word_budget": budget,
        "logs": logs,
    }


def summarize(gate: Dict[str, Any]) -> str:
    """一行摘要，给日志/前端用。"""
    cov = (gate or {}).get("coverage") or {}
    bud = (gate or {}).get("word_budget") or {}
    miss = cov.get("missing") or []
    crit = cov.get("missing_critical") or []
    cov_txt = (f"要求覆盖 {cov.get('covered', 0)}/{cov.get('total', 0)} 项"
               + (f"（缺 {len(miss)} 项，其中必需 {len(crit)} 项）" if miss else ""))
    if bud.get("total_min_words"):
        bud_txt = (f"字数 {bud.get('total_words', 0)}/{bud.get('total_min_words', 0)}"
                   f"（{round(float(bud.get('total_ratio') or 0) * 100)}%）")
    else:
        bud_txt = "字数：无正文可核"
    return f"覆盖矩阵：{cov_txt}；字数预算：{bud_txt}"


STATE_KEYS = ("_coverage", "_word_budget", "_deliverable", "_deliverable_blockers")


def attach(state: Dict[str, Any], domain: Optional[str] = None) -> Dict[str, Any]:
    """算好闸门并写进 state；返回完整结果。

    `report_assembler.assemble()` 在保存前调用。工作流走的是白名单式
    `asm_state`，所以那边还要把 `STATE_KEYS` 回写到会话 state（见
    `report_workflow.py` 的 `_chapter_gaps` 回写处）。
    """
    domain = str(domain or state.get("_domain") or "stability")
    gate = evaluate(state.get("chapters") or {}, state.get("filled_data") or {},
                    domain)
    state["_coverage"] = gate["coverage"]
    state["_word_budget"] = gate["word_budget"]
    state["_deliverable"] = gate["deliverable"]
    state["_deliverable_blockers"] = gate["blockers"]
    for line in gate["logs"]:
        if gate["deliverable"]:
            logger.info("[闸门] %s", line)
        else:
            logger.warning("[闸门] %s", line)
    return gate
