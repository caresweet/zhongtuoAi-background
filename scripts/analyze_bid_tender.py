# -*- coding: utf-8 -*-
"""分析一份招标文件 + 对照人工投标案例，产出投标文件编写清单。

用法：
    python scripts/analyze_bid_tender.py \
        --tender "~/Downloads/招标文件-….doc" \
        --case "~/Downloads/投标文件（众拓测绘）3.27.docx" \
        --seed 1

产出：
    docs/bid_learning/<项目简称>.md
      · 招标要求（项目信息/资格/人员/评分/投标文件格式章节与目录）
      · 人工案例与招标格式的差异（案例缺什么、多什么）
      · 随机抽取的人员配置（满足最低配置）与得分测算
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

OUT = BACKEND / "docs" / "bid_learning"


def _norm(s: str) -> str:
    return re.sub(r'[\s（）()、：:，,.．·\-—_]', '', str(s or ""))


def _case_headings(path: Path) -> list:
    """案例的一/二级标题。优先用结构画像；画像为空时直接读 Word 标题样式。"""
    from app.services.template_case_analysis import analyze_template_file
    a = analyze_template_file(path, domain="bidding")
    secs = [s for s in (a.get("sections") or []) if s.get("level", 1) <= 2]
    if secs:
        return secs
    if path.suffix.lower() != ".docx":
        return []
    from docx import Document
    out = []
    for para in Document(str(path)).paragraphs:
        t = para.text.strip()
        if not t or len(t) > 40:
            continue
        st = (para.style.name or "").lower()
        if st.startswith("heading"):
            lvl = int(re.sub(r'\D', '', para.style.name) or 1)
            m = re.match(r'^([一二三四五六七八九十]+)[、.．]\s*(.+)$', t)
            no = m.group(1) if m else ""
            title = m.group(2) if m else t
            out.append({"no": no, "title": title, "level": min(lvl, 2)})
        elif re.match(r'^[一二三四五六七八九十]+、', t):
            out.append({"no": "", "title": re.sub(r'^[一二三四五六七八九十]+、\s*', '', t),
                        "level": 1})
    return out


def _match(fmt_title: str, case_titles: list) -> str:
    """招标格式项 → 案例里对应的标题（按关键词/包含匹配）。"""
    key = _norm(fmt_title)
    # 去掉「（格式）」等后缀
    key = re.sub(r'格式.*$', '', key)
    for t in case_titles:
        n = _norm(t)
        if not n:
            continue
        if key and (key in n or n in key):
            return t
        # 关键词兜底：投标函/报价/资格/偏离/评分/方案/人员/其他
        for kw in ("投标函", "磋商函", "报价", "资格", "偏离", "评分", "方案",
                   "人员", "其他", "声明", "授权"):
            if kw in fmt_title and kw in t:
                return t
    return ""


def _score_of(personnel: list, tender: dict) -> dict:
    """按招标评分口径粗算人员得分（负责人类 4 分 + 组员类 6 分）。"""
    def rank(t):
        t = str(t or "")
        return 3 if "正高级" in t else 2 if "高级" in t else 1 if ("中级" in t or t == "工程师") else 0

    leader = personnel[0] if personnel else {}
    lp = 0
    if rank(leader.get("title")) >= 2 and any(
            k in str(leader.get("major") or "") for k in ("土地", "测绘")):
        lp += 2
    if any("注册测绘师" in c for c in (leader.get("certs") or [])):
        lp += 2
    mp = 0
    for p in personnel[1:]:
        if any(k in str(p.get("major") or "") for k in ("土地", "测绘", "规划", "国土")):
            mp += 2 if rank(p.get("title")) >= 2 else 1 if rank(p.get("title")) >= 1 else 0
    return {"leader": min(lp, 4), "members": min(mp, 6), "total": min(lp, 4) + min(mp, 6)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tender", required=True)
    ap.add_argument("--case", default="")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    from app.services.bidding_tender import parse_tender, select_bid_personnel

    tp = Path(args.tender).expanduser()
    tender = parse_tender(tp)
    personnel = select_bid_personnel(seed=args.seed)
    scores = _score_of(personnel, tender)

    case_titles, missing, extra = [], [], []
    if args.case:
        case = Path(args.case).expanduser()
        if case.exists():
            secs = _case_headings(case)
            case_titles = [f"{s.get('no') or ''} {s.get('title') or ''}".strip() for s in secs]
            for f in tender.get("format_sections") or []:
                if f.get("level") != 1:
                    continue
                if not _match(f.get("title") or "", case_titles):
                    missing.append(f.get("title"))
            fmt_all = " ".join(_norm(f.get("title") or "")
                               for f in (tender.get("format_sections") or []))
            # 案例「多出来」的：明确不在招标格式目录里的类别
            for t in case_titles:
                if not any(k in t for k in ("函", "报价", "资格", "偏离", "评分",
                                            "方案", "人员", "声明", "授权", "业绩",
                                            "认证", "证书", "身份证")):
                    continue
                if not any(k in fmt_all for k in
                           ("投标函", "磋商函", "报价", "资格", "偏离", "评分",
                            "方案", "人员", "其他", "声明", "授权")):
                    extra.append(t)

    lines = [f"# 投标文件编写清单：{tender.get('project_name') or tp.stem}", "",
             "## 一、招标文件要求", "",
             f"- 项目编号：{tender.get('project_no') or '-'}",
             f"- 采购人：{tender.get('purchaser') or '-'}",
             f"- 采购代理机构：{tender.get('agency') or '-'}",
             f"- 预算/最高限价：{tender.get('budget_wan') or '-'} 万元 / "
             f"{tender.get('max_price_wan') or '-'} 万元",
             f"- 投标文件格式位置：**{tender.get('format_chapter') or '未识别'}**",
             f"- 不接受联合体：{'是' if tender.get('no_consortium') else '否'}；"
             f"要求中小企业声明：{'是' if tender.get('sme_required') else '否'}",
             "", "### 资格条件", ""]
    for q in tender.get("qualifications") or []:
        lines.append(f"- {q}")
    lines += ["", "### 人员最低配置", ""]
    for k, v in (tender.get("staff_min") or {}).items():
        lines.append(f"- {k}：{v} 名")
    lines += ["", "### 评分办法（原文摘录）", ""]
    for s in (tender.get("scoring_points") or [])[:30]:
        lines.append(f"- {s}")
    lines += ["", "### 投标文件格式目录（按招标文件顺序）", ""]
    for f in tender.get("format_sections") or []:
        indent = "  " if f.get("level") == 2 else ""
        lines.append(f"{indent}- （{f.get('no')}）{f.get('title')}")

    if args.case:
        lines += ["", "## 二、人工案例对照", "",
                  f"案例文件：`{args.case}`", "",
                  "### 招标要求但案例没有的（需补齐）", ""]
        for m in missing or ["（无）"]:
            lines.append(f"- {m}")
        lines += ["", "### 案例有、招标格式目录未单列的（按确认加入）", ""]
        for e in extra or ["（无）"]:
            lines.append(f"- {e}")

    lines += ["", "## 三、拟投入人员（随机抽取，满足最低配置）", "",
              "| 角色 | 姓名 | 性别 | 年龄 | 学历 | 专业 | 职称 | 证书 |",
              "|---|---|---|---|---|---|---|---|"]
    for p in personnel:
        lines.append(f"| {p.get('role')} | {p.get('name')} | {p.get('gender') or '-'} | "
                     f"{p.get('age') or '-'} | {p.get('education') or '-'} | "
                     f"{p.get('major') or '-'} | {p.get('title') or '-'} | "
                     f"{'、'.join(p.get('certs') or []) or '-'} |")
    lines += ["", f"- 人员评分粗算：负责人 {scores['leader']}/4，组员 {scores['members']}/6，"
                  f"合计 {scores['total']}/10（以证书与社保材料实际提供为准）", ""]

    OUT.mkdir(parents=True, exist_ok=True)
    fn = re.sub(r'[\\/:*?"<>|\s]+', "_", str(tender.get("project_name") or tp.stem))[:60]
    out = OUT / f"{fn}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
