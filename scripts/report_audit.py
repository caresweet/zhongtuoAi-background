#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告审核 CLI —— 把七层校验串成一次可复现的审核，输出标准化报告。

七层（与 `.claude/skills/report-audit/SKILL.md` 一一对应）：

  1 结构     章节是否齐全、各章字数是否达标
  2 内容规则 占位符残留 / 模糊话术 / 表格缺单位 / 编造法规（content_rule_checker）
  3 数据真实 编造数据 / 数值越界 / 编造文号 / 必要材料（content_guardrails）
  4 指标阈值 问卷份数、支持率、户均人口等规则库（rule_checker，需 --filled）
  5 格式规范 字体/字号/对齐/缩进（spec_checker，需 --spec 或库里有「格式规范」模板）
  6 交付体检 「读一遍就过」可用率、AI 套话、配图无解读（delivery_audit）
  7 覆盖闸门 审查要点是否写全 + 字数预算（coverage_matrix，**硬闸门**）

用法：
  cd backend
  .venv/bin/python scripts/report_audit.py 报告.docx
  .venv/bin/python scripts/report_audit.py 报告.docx --domain reclamation --json
  .venv/bin/python scripts/report_audit.py 报告.docx --filled filled.json --spec 格式规范.docx

退出码：0 = 通过（≥70 且无 critical）；1 = 不通过；2 = 输入错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ═══════════════════════════════════════════════════════════════════
# 文本提取 + 分章
# ═══════════════════════════════════════════════════════════════════

def _docx_blocks(path: Path) -> List[Tuple[str, str]]:
    """按文档顺序产出 ("p", 段落) / ("tbl", markdown 表格)。

    不用 `FileService._docx_full_text`：它把全部表格排在段落之后，
    分章时表格会全挂到最后一章。
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    blocks: List[Tuple[str, str]] = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, doc).text.strip()
            if text:
                blocks.append(("p", text))
        elif child.tag.endswith("}tbl"):
            rows: List[str] = []
            for row in Table(child, doc).rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                uniq: List[str] = []
                for c in cells:
                    if not uniq or uniq[-1] != c:       # 合并单元格会重复
                        uniq.append(c)
                if any(uniq):
                    rows.append("| " + " | ".join(uniq) + " |")
            if rows:
                blocks.append(("tbl", "\n".join(rows)))
    return blocks


def _pdf_blocks(path: Path) -> List[Tuple[str, str]]:
    import pdfplumber
    out: List[Tuple[str, str]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                if line.strip():
                    out.append(("p", line.strip()))
    return out


def _extract_blocks(path: Path) -> List[Tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_blocks(path)
    return _docx_blocks(path)


_RE_CL_CHAPTER = re.compile(r"^(\d{1,2})\s+(\S.{1,29})$")


def _chapter_no_of(text: str, domain: str, defs: Dict[int, Dict[str, Any]]) -> int:
    """章标题识别：稳评用「第X章」；复垦用「N 标题」（与复垦定义表对得上才算）。"""
    from app.agent.chapter_definitions import chapter_no_of_heading

    n = chapter_no_of_heading(text)
    if n:
        return n
    if domain == "reclamation":
        m = _RE_CL_CHAPTER.match(str(text or "").strip())
        if m:
            no, title = int(m.group(1)), m.group(2).strip()
            expect = str((defs.get(no) or {}).get("title") or "")
            if expect and (title.startswith(expect[:4]) or expect.startswith(title[:4])):
                return no
    return 0


def _split_chapters(blocks: Iterable[Tuple[str, str]],
                    domain: str = "stability") -> Dict[int, Dict[str, str]]:
    """按章标题切章；0 号收封面/目录等章前内容。"""
    defs = _chapter_defs(domain)
    chapters: Dict[int, List[str]] = {}
    cur = 0
    for kind, text in blocks:
        if kind == "p":
            n = _chapter_no_of(text, domain, defs)
            if n:
                cur = n
                chapters.setdefault(cur, []).append(text)
                continue
        chapters.setdefault(cur, []).append(text)
    return {k: {"markdown": "\n\n".join(v)} for k, v in chapters.items()}


def _cn_chars(text: str) -> int:
    return len(re.findall(r"[一-龥]", text or ""))


# ═══════════════════════════════════════════════════════════════════
# 六层
# ═══════════════════════════════════════════════════════════════════

def _chapter_defs(domain: str) -> Dict[int, Dict[str, Any]]:
    if domain == "reclamation":
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        return RECLAMATION_CHAPTER_DEFINITIONS
    if domain == "stability":
        from app.agent.chapter_definitions import CHAPTER_DEFINITIONS
        return CHAPTER_DEFINITIONS
    return {}


def layer_structure(chapters: Dict[int, Dict[str, str]], domain: str) -> Dict[str, Any]:
    defs = _chapter_defs(domain)
    present = {k for k in chapters if k > 0}
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    below: List[str] = []

    for no in sorted(defs):
        d = defs[no] or {}
        title = d.get("title", "")
        md = (chapters.get(no) or {}).get("markdown", "")
        words = _cn_chars(md)
        min_words = int(d.get("min_words") or 0)
        ok = no in present
        row = {"chapter": no, "title": title, "present": ok,
               "words": words, "min_words": min_words}
        if not ok:
            missing.append(f"第{no}章 {title}")
        elif min_words and words < min_words * 0.6:
            # 0.6 是「明显偏薄」线；不是硬性字数考核
            below.append(f"第{no}章 {title}：{words} 字（目标 ≥{min_words}）")
        rows.append(row)

    return {
        "expected": len(defs), "present": len(present & set(defs)),
        "missing": missing, "below_words": below, "chapters": rows,
        "has_headings": bool(present),
    }


def layer_content_rules(chapters: Dict[int, Dict[str, str]]) -> List[Dict[str, Any]]:
    from app.validation.content_rule_checker import check_chapters
    return check_chapters(chapters)


def layer_guardrails(full_text: str, filled: Optional[dict], domain: str) -> Dict[str, Any]:
    from app.validation.content_guardrails import full_guardrail_check
    return full_guardrail_check(markdown=full_text, filled_data=filled or None,
                                domain=domain)


def layer_thresholds(filled: Optional[dict]) -> Dict[str, Any]:
    if not filled:
        return {"skipped": True}
    from app.validation.rule_checker import missing_checklist, run_checks
    return {"skipped": False, "issues": run_checks(filled, include_missing=True),
            "missing": missing_checklist(filled)}


def layer_spec(report_path: Path, spec_path: Optional[str],
               domain: str = "stability") -> Dict[str, Any]:
    from app.validation.spec_checker import SpecChecker, load_spec_from_db
    # 库里的「格式规范」是稳评制式；复垦/投标不套用，必须显式 --spec
    spec = spec_path or (load_spec_from_db() if domain == "stability" else None)
    if not spec:
        return {"skipped": True}
    rep = SpecChecker.check(str(report_path), str(spec))
    failed = [{"rule": c.rule.target, "issues": c.issues[:5]}
              for c in rep.checks if not c.passed]
    return {"skipped": False, "spec": rep.spec_name,
            "total": rep.total_rules, "passed": rep.passed_count,
            "failed": failed}


def layer_delivery(chapters: Dict[int, Dict[str, str]]) -> Dict[str, Any]:
    from app.services.delivery_audit import audit_chapters
    # 0 号是封面/目录/公司简介，不属于正文；只有它一份时才退而求其次
    body = {k: v for k, v in chapters.items() if isinstance(k, int) and k > 0}
    return audit_chapters(body or chapters)


def layer_gate(chapters: Dict[int, Dict[str, str]], domain: str) -> Dict[str, Any]:
    """第 7 层：要求覆盖矩阵 + 字数预算（与生成时的交付闸门同一套判定）。"""
    from app.services.coverage_matrix import check_coverage, check_word_budget
    body = {k: v for k, v in chapters.items() if isinstance(k, int) and k > 0}
    body = body or chapters
    return {"coverage": check_coverage(body, domain),
            "word_budget": check_word_budget(body, domain)}


# ═══════════════════════════════════════════════════════════════════
# 打分（与 stability-report-review 口径一致）
# ═══════════════════════════════════════════════════════════════════

def _sev(issue: Dict[str, Any]) -> str:
    return str(issue.get("severity") or "warning").lower()


def _deduct_for(issues: List[Dict[str, Any]]) -> float:
    """critical -8 / warning -3 / info -1，单类封顶 50。"""
    cost = 0.0
    for it in issues or []:
        s = _sev(it)
        cost += 8 if s == "critical" else (1 if s == "info" else 3)
    return min(50.0, cost)


def score_all(struct: dict, content: list, guard: dict,
              threshold: dict, spec: dict, delivery: dict,
              gate: Optional[dict] = None) -> Dict[str, Any]:
    guard_all = []
    for key in ("blocking", "data_validity", "fabricated",
                "numeric_ranges", "regulations", "materials"):
        guard_all.extend(guard.get(key) or [])

    data = 100 - (
        _deduct_for([i for i in (guard.get("fabricated") or [])]) +
        _deduct_for([i for i in (guard.get("data_validity") or [])]) * 0.8 +
        _deduct_for([i for i in (guard.get("regulations") or [])]) +
        _deduct_for([i for i in (guard.get("numeric_ranges") or [])]) * 0.4 +
        _deduct_for([i for i in (guard.get("materials") or [])]) +
        (_deduct_for(threshold.get("issues") or []) if not threshold.get("skipped") else 0) * 0.6
    )

    fmt = 100.0
    if not spec.get("skipped"):
        fmt -= min(60.0, len(spec.get("failed") or []) * 5)

    complete = 100 - (
        len(struct.get("missing") or []) * 10 +
        len(struct.get("below_words") or []) * 5 +
        min(30.0, (1 - float(delivery.get("rate") or 0)) * 40)
    )
    # 第 7 层：缺必需要求 / 明显偏薄的章都算内容完整性问题
    if gate:
        _critical_missing = len((gate.get("coverage") or {}).get("missing_critical") or [])
        _thin = len((gate.get("word_budget") or {}).get("thin_chapters") or [])
        complete -= _critical_missing * 8 + _thin * 3

    compliance = 100 - (
        _deduct_for(guard.get("blocking") or []) +
        _deduct_for(content or []) * 0.5
    )

    dims = {
        "数据质量": max(0.0, data),
        "格式规范": max(0.0, fmt),
        "内容完整性": max(0.0, float(complete)),
        "合规性": max(0.0, compliance),
    }
    weights = {"数据质量": 0.5, "格式规范": 0.2, "内容完整性": 0.2, "合规性": 0.1}
    total = sum(dims[k] * weights[k] for k in dims)
    grade = "优秀" if total >= 85 else ("良好" if total >= 70 else "不合格")
    critical_n = sum(1 for i in guard_all if _sev(i) == "critical")
    critical_n += sum(1 for i in (content or []) if _sev(i) == "critical")
    return {"total": round(total, 1), "grade": grade, "dims": dims,
            "weights": weights, "critical": critical_n}


# ═══════════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════════

def _fmt_issue(it: Dict[str, Any]) -> str:
    msg = (it.get("message") or it.get("detail") or it.get("text")
           or it.get("description") or it.get("match") or "")
    if not msg:
        msg = "；".join(str(v) for v in it.values()
                        if isinstance(v, str) and v.strip())[:120]
    sev = _sev(it)
    tag = {"critical": "🔴", "warning": "⚠️", "info": "ℹ️"}.get(sev, "⚠️")
    ch = it.get("chapter")
    prefix = f"[第{ch}章] " if ch not in (None, "", 0, "0") else ""
    return f"- {tag} {prefix}{msg}"


def render_markdown(name: str, domain: str, path: Path, struct: dict, content: list,
                    guard: dict, threshold: dict, spec: dict, delivery: dict,
                    score: dict, gate: Optional[dict] = None) -> str:
    lines: List[str] = []
    w = lines.append
    w(f"# 📋 报告审核报告\n")
    w(f"- **报告文件**：{path.name}")
    w(f"- **报告域**：{domain}")
    w(f"- **审核时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(f"- **综合评分**：**{score['total']} / 100（{score['grade']}）**\n")

    w("## 一、各维度得分\n")
    w("| 维度 | 得分 | 权重 | 加权 |")
    w("|---|---|---|---|")
    for k, v in score["dims"].items():
        wt = score["weights"][k]
        w(f"| {k} | {v:.1f} | {int(wt * 100)}% | {v * wt:.1f} |")
    w("")

    w("## 二、第 1 层：结构完整性\n")
    if not struct.get("has_headings"):
        w("- ℹ️ 未识别到「第X章」标题（该文件可能不是分章报告）")
    else:
        w(f"- 应有 {struct['expected']} 章，识别到 {struct['present']} 章")
        for m in struct.get("missing") or []:
            w(f"- 🔴 缺章：{m}")
        for b in struct.get("below_words") or []:
            w(f"- ⚠️ 篇幅偏薄：{b}")
        if not struct.get("missing") and not struct.get("below_words"):
            w("- ✅ 章节齐全、篇幅达标")
    w("")

    w("## 三、第 2 层：内容规则\n")
    if content:
        for it in content:
            w(_fmt_issue(it))
    else:
        w("- ✅ 未命中内容规则")
    w("")

    w("## 四、第 3 层：数据真实性\n")
    any_guard = False
    for key, label in (("blocking", "阻断问题"), ("data_validity", "数据有效性"),
                       ("fabricated", "疑似编造"), ("numeric_ranges", "数值越界"),
                       ("regulations", "法规文号"), ("materials", "必要材料")):
        items = guard.get(key) or []
        if items:
            any_guard = True
            w(f"**{label}**（{len(items)}）")
            for it in items:
                w(_fmt_issue(it))
    if not any_guard:
        w("- ✅ 未发现编造数据、越界数值或编造文号")
    w("")

    w("## 五、第 4 层：指标阈值\n")
    if threshold.get("skipped"):
        w("- ℹ️ 未提供 `--filled` 事实卡，跳过指标阈值校验")
    else:
        issues = threshold.get("issues") or []
        if not issues:
            w("- ✅ 规则库校验通过")
        for it in issues:
            w(f"- {'ℹ️' if _sev(it) == 'info' else '⚠️'} {it.get('message')}")
        miss = threshold.get("missing") or []
        if miss:
            w(f"- ⚠️ 必填字段缺失 {len(miss)} 项：" + "、".join(
                str(m.get("label") or m.get("field")) for m in miss[:10]))
    w("")

    w("## 六、第 5 层：格式规范\n")
    if spec.get("skipped"):
        w("- ℹ️ 未找到格式规范文件，跳过（可用 `--spec` 指定）")
    else:
        w(f"- 规范：{spec.get('spec')}；规则 {spec.get('total')} 条，"
          f"通过 {spec.get('passed')} 条")
        for f in spec.get("failed") or []:
            w(f"- ⚠️ {f.get('rule')}：{'；'.join(f.get('issues') or []) or '不符合要求'}")
    w("")

    w("## 七、第 6 层：交付体检\n")
    w(f"- 段落 {delivery.get('total')}，需人工复核 {delivery.get('flagged')} 段，"
      f"可用率 **{float(delivery.get('rate') or 0) * 100:.1f}%**"
      + ("（低于 80% 目标）" if delivery.get("below_target") else ""))
    by_kind = delivery.get("by_kind") or {}
    if by_kind:
        w("- 问题分布：" + "、".join(f"{k} {v}" for k, v in by_kind.items()))
    for it in (delivery.get("items") or [])[:20]:
        w(f"- ⚠️ [第{it.get('chapter')}章] {it.get('label')}：{it.get('detail')}"
          f"（{it.get('text')}…）")
    w("")

    w("## 八、第 7 层：要求覆盖与字数预算\n")
    if gate:
        cov = gate.get("coverage") or {}
        bud = gate.get("word_budget") or {}
        w(f"- 要求覆盖 **{cov.get('covered', 0)}/{cov.get('total', 0)}** 项"
          + (f"，缺 {len(cov.get('missing') or [])} 项"
             f"（其中必需 {len(cov.get('missing_critical') or [])} 项）"
             if cov.get("missing") else "，全部覆盖"))
        for m in (cov.get("missing") or []):
            tag = "🔴 必需" if m.get("severity") == "critical" else "⚠️ 建议"
            w(f"- {tag}：{m.get('name')}（依据：{m.get('basis')}）")
        if bud.get("total_min_words"):
            w(f"- 字数 **{bud.get('total_words')}** / 下限合计 {bud.get('total_min_words')}"
              f"（{round(float(bud.get('total_ratio') or 0) * 100)}%）")
        for row in (bud.get("missing_chapters") or []):
            w(f"- 🔴 第{row.get('chapter')}章「{row.get('title')}」没有正文")
        for row in (bud.get("thin_chapters") or []):
            w(f"- 🔴 第{row.get('chapter')}章「{row.get('title')}」{row.get('words')} 字，"
              f"不足下限 {row.get('min_words')} 字的 60%")
    else:
        w("- ℹ️ 未执行覆盖/字数检查")
    w("")

    w("## 九、结论与建议\n")
    _gate_bad = bool(
        (gate or {}).get("coverage", {}).get("missing_critical")
        or (gate or {}).get("word_budget", {}).get("missing_chapters")
        or (gate or {}).get("word_budget", {}).get("thin_chapters"))
    if score["critical"] or _gate_bad:
        w("- 退回修改：先处理 🔴 阻断项（含第 7 层缺项/偏薄章），再重跑本审核。")
    elif score["grade"] == "优秀":
        w("- 可直接交付。")
    elif score["grade"] == "良好":
        w("- 修复上述 ⚠️ 项后交付。")
    else:
        w("- 退回修改：先处理 🔴 阻断项，再重跑本审核。")
    if score["critical"]:
        w(f"- 当前有 **{score['critical']} 个 critical 问题**，必须清零。")
    if _gate_bad:
        w("- 第 7 层闸门未通过，本轮**不可交付**。")
    w("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="六层报告审核")
    ap.add_argument("report", help="报告文件（.docx / .pdf）")
    ap.add_argument("--domain", default="stability",
                    choices=["stability", "reclamation", "bidding"])
    ap.add_argument("--filled", help="事实卡 JSON（跑指标阈值层）")
    ap.add_argument("--spec", help="格式规范 .docx（不传则从库里找）")
    ap.add_argument("--out", help="Markdown 输出路径")
    ap.add_argument("--json", action="store_true", help="同时输出 JSON")
    args = ap.parse_args(argv)

    path = Path(args.report).expanduser()
    if not path.exists():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2
    if path.suffix.lower() not in (".docx", ".pdf"):
        print("只支持 .docx / .pdf", file=sys.stderr)
        return 2

    filled = None
    if args.filled:
        filled = json.loads(Path(args.filled).read_text(encoding="utf-8"))

    blocks = _extract_blocks(path)
    chapters = _split_chapters(blocks, args.domain)
    full_text = "\n\n".join(t for _, t in blocks)

    struct = layer_structure(chapters, args.domain)
    content = layer_content_rules(chapters)
    guard = layer_guardrails(full_text, filled, args.domain)
    threshold = layer_thresholds(filled)
    spec = layer_spec(path, args.spec, args.domain)
    delivery = layer_delivery(chapters)
    gate = layer_gate(chapters, args.domain)
    score = score_all(struct, content, guard, threshold, spec, delivery, gate)

    md = render_markdown(path.stem, args.domain, path, struct, content,
                         guard, threshold, spec, delivery, score, gate)

    out_path = Path(args.out) if args.out else path.with_name(
        f"{path.stem}_审核报告_{datetime.now():%Y%m%d_%H%M}.md")
    out_path.write_text(md, encoding="utf-8")

    if args.json:
        payload = {"score": score, "structure": struct, "content_rules": content,
                   "guardrails": guard, "thresholds": threshold, "spec": spec,
                   "delivery": delivery, "gate": gate}
        out_path.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(md)
    print(f"\n审核报告已写入：{out_path}")
    # 🔴 第 7 层是**硬闸门**：缺必需要求、或某章明显偏薄，即使总分过 70 也判不通过
    _gate_blocked = bool(
        (gate.get("coverage") or {}).get("missing_critical")
        or (gate.get("word_budget") or {}).get("missing_chapters")
        or (gate.get("word_budget") or {}).get("thin_chapters")
    )
    passed = score["total"] >= 70 and score["critical"] == 0 and not _gate_blocked
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
