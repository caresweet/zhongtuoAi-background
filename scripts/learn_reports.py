# -*- coding: utf-8 -*-
"""把知识库/历史库里**已经入库的报告**批量识别学习一遍。

对每份报告（docx/pdf/md/html）产出与模板同一套「批注式分析」：
章节结构、逐节类型（不变/换数/重写）、数据位与来源、版式（docx/pdf）。
再汇总成每个域的《学习报告》：

    docs/report_learning/<domain>.md
      · 分析了哪些报告、各自章节数/节数
      · 章节覆盖率（本域报告里，第 N 章出现在多少份中）
      · 高频小节（哪些小节是行业固定结构）
      · 与标准章节结构的差异（缺哪些章）

用法：
    python scripts/learn_reports.py                     # 知识库报告 + 历史生成稿
    python scripts/learn_reports.py --domain stability
    python scripts/learn_reports.py --no-history        # 只学知识库范文

产出：data/report_analysis/<domain>_<id>_<slug>.json + docs/report_learning/
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

KB = BACKEND / "data" / "knowledge_base.db"
HIST = BACKEND / "data" / "history_reports.db"
STORAGE = BACKEND / "storage"
OUT_DATA = BACKEND / "data" / "report_analysis"
OUT_DOCS = BACKEND / "docs" / "report_learning"

REPORT_TYPES = ("example_report", "本地案例", "稳评范文", "other")


def _slug(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(s or "")).strip("_")[:50] or "report"


_FOUND: dict = {}


def _resolve(rel: str) -> Path:
    p = Path(str(rel or ""))
    if p.is_absolute() and p.exists():
        return p
    key = str(rel or "")
    if key in _FOUND:
        return _FOUND[key]
    bases = (STORAGE, BACKEND, BACKEND / "seed_data",
             BACKEND / "seed_data" / "crawled_reports",
             BACKEND / "seed_data" / "crawled_reports" / "clean",
             BACKEND / "seed_data" / "crawled_reports" / "jiangsu",
             Path.home() / "Downloads")
    for base in bases:
        q = base / str(rel)
        if q.exists():
            _FOUND[key] = q
            return q
    # 兜底：按文件名在 seed_data / storage 里找（知识库只登记了裸文件名）
    name = p.name
    for base in (BACKEND / "seed_data", STORAGE):
        try:
            hit = next(base.rglob(name), None)
        except Exception:
            hit = None
        if hit and hit.exists():
            _FOUND[key] = hit
            return hit
    _FOUND[key] = p
    return p


def _from_knowledge(domain: str = "") -> list:
    conn = sqlite3.connect(str(KB))
    q = ("select id,title,domain,document_type,file_path from knowledge_documents "
         f"where document_type in ({','.join('?' * len(REPORT_TYPES))})")
    args = list(REPORT_TYPES)
    if domain:
        q += " and domain=?"
        args.append(domain)
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [{"source": "knowledge", "id": r[0], "title": r[1], "domain": r[2],
             "doc_type": r[3], "path": _resolve(r[4])} for r in rows]


def _from_history(domain: str = "") -> list:
    conn = sqlite3.connect(str(HIST))
    q = "select id,title,domain,report_file_path,status from reports where report_file_path is not null"
    args = []
    if domain:
        q += " and domain=?"
        args.append(domain)
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [{"source": "history", "id": r[0], "title": r[1], "domain": r[2],
             "doc_type": "generated", "path": _resolve(r[3]),
             "status": r[4]} for r in rows]


def _standard_titles(domain: str) -> dict:
    """本域的标准章节表。稳评/复垦有正式目录；投标没有（只统计实际标题）。"""
    if domain not in ("stability", "reclamation"):
        return {}
    try:
        from app.agent.chapter_catalog import chapter_titles
        return dict(chapter_titles(domain) or {})
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from app.services.template_case_analysis import analyze_template_file

    docs = _from_knowledge(args.domain)
    if not args.no_history:
        docs += _from_history(args.domain)
    if args.limit:
        docs = docs[:args.limit]

    OUT_DATA.mkdir(parents=True, exist_ok=True)
    OUT_DOCS.mkdir(parents=True, exist_ok=True)

    by_domain = defaultdict(lambda: {"docs": [], "chapters": Counter(),
                                     "chapter_titles": Counter(),
                                     "unnumbered": 0,
                                     "sections": Counter(), "kinds": Counter()})
    ok = skipped = failed = 0
    for d in docs:
        p: Path = d["path"]
        if not p.exists():
            print(f"[跳过] {d['source']}#{d['id']} {d['title']}：文件不存在 {p}")
            skipped += 1
            continue
        try:
            a = analyze_template_file(p, domain=d["domain"], name=d["title"])
        except Exception as e:
            print(f"[失败] {d['source']}#{d['id']} {d['title']}：{e}")
            failed += 1
            continue
        if not a:
            print(f"[跳过] {d['source']}#{d['id']} {d['title']}：格式不支持或没有章节")
            skipped += 1
            continue
        fn = f"{d['domain']}_{d['source']}_{d['id']}_{_slug(d['title'])}.json"
        (OUT_DATA / fn).write_text(json.dumps(a, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        bucket = by_domain[d["domain"]]
        bucket["docs"].append({**d, "chapters": a.get("chapters") or 0,
                               "sections": len(a.get("sections") or []),
                               "counts": a.get("counts") or {},
                               "report": a})
        for s in a.get("sections") or []:
            if s.get("level") == 1:
                no = str(s.get("no") or "").strip()
                if no:
                    bucket["chapters"][no] += 1
                else:
                    bucket["unnumbered"] += 1
                if s.get("title"):
                    bucket["chapter_titles"][str(s["title"]).strip()] += 1
            elif s.get("title"):
                bucket["sections"][str(s.get("title"))] += 1
            bucket["kinds"][str(s.get("kind") or "")] += 1
        ok += 1
        print(f"[OK] {d['domain']} {d['source']}#{d['id']} {d['title']}"
              f"（{a.get('chapters', 0)} 章 / {len(a.get('sections') or [])} 节）")

    index = ["# 已入库报告学习汇总", "",
             f"扫描 {len(docs)} 份，成功 {ok}，跳过 {skipped}，失败 {failed}", ""]
    for dom, b in sorted(by_domain.items()):
        std = _standard_titles(dom)
        lines = [f"# {dom} 报告学习报告", "",
                 f"- 分析报告数：{len(b['docs'])}",
                 f"- 逐节类型分布：{dict(b['kinds'])}", ""]
        total = max(1, len(b["docs"]))
        lines.append("## 1. 章节覆盖")
        lines.append("")
        if std and dom == "reclamation":
            lines.append("按**标准目录标题**匹配（报告里的章标题包含标准标题即算命中）：")
            lines.append("")
            lines.append("| 标准章号 | 标准标题 | 命中份数 | 占比 |")
            lines.append("|---|---|---|---|")
            _miss = []
            for no, title in std.items():
                key = str(title).replace(" ", "")
                cnt = sum(1 for t, c in b["chapter_titles"].items()
                          if key and key in str(t).replace(" ", ""))
                lines.append(f"| {no} | {title} | {cnt} | {cnt / total:.0%} |")
                if not cnt:
                    _miss.append(f"{no} {title}")
            if _miss:
                lines.append("")
                lines.append("标准结构里**从未出现**的章：" + "；".join(_miss))
        elif not (std and dom == "reclamation"):
            lines.append("以下是实际出现的章标题频次（按报告原文统计）：")
        lines.append("")
        if b["unnumbered"]:
            lines.append(f"另有 {b['unnumbered']} 个无编号的一级标题（如「附件」「总体结论」），未计入章号。")
            lines.append("")
        lines.append("实际章标题频次（前 20）：")
        lines.append("")
        for title, cnt in b["chapter_titles"].most_common(20):
            lines.append(f"- {title}（{cnt} 份）")
        lines.append("")
        lines.append("## 2. 高频小节（≥2 份报告共有）")
        lines.append("")
        for title, cnt in b["sections"].most_common(40):
            if cnt >= 2:
                lines.append(f"- {title}（{cnt} 份）")
        lines.append("")
        lines.append("## 3. 报告清单")
        lines.append("")
        lines.append("| 来源 | id | 名称 | 章/节 | 固定/换数/重写 |")
        lines.append("|---|---|---|---|---|")
        for x in b["docs"]:
            c = x.get("counts") or {}
            lines.append(f"| {x['source']} | {x['id']} | {x['title']} | "
                         f"{x['chapters']}/{x['sections']} | "
                         f"{c.get('fixed', 0)}/{c.get('slot', 0)}/{c.get('create', 0)} |")
        lines.append("")
        md = "\n".join(lines)
        (OUT_DOCS / f"{dom}.md").write_text(md, encoding="utf-8")
        index.append(f"- [{dom} 学习报告]({dom}.md)：{len(b['docs'])} 份报告")
        print(f"[学习汇总] {dom} → {OUT_DOCS / (dom + '.md')}")

    (OUT_DOCS / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"\n完成：成功 {ok}，跳过 {skipped}，失败 {failed}")
    print(f"汇总：{OUT_DOCS / 'README.md'}")
    return 0 if ok else 1


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
