#!/usr/bin/env python3
"""compare_and_optimize.py — 生成报告与模板对比 + 迭代优化

用法:
  python3 compare_and_optimize.py stability <run_id或md文件路径>
  python3 compare_and_optimize.py bidding <run_id或md文件路径>

流程:
  1. 加载生成报告 + 模板大纲
  2. 结构对比（章节覆盖/子节覆盖/占位词）
  3. 输出差异报告
  4. 对有问题的章节退回重写
  5. 保存优化后的版本
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_engine import DualKB, LLMClient, ReviewGuard, KB_DOMAIN_STABILITY, KB_DOMAIN_BIDDING
from kb_engine.template_compare import TemplateComparator
from kb_engine.docx_writer import DocxWriter

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


async def optimize(domain: str, md_path: str = "", run_id: str = ""):
    db = DualKB()
    llm = LLMClient()
    guard = ReviewGuard()
    comparator = TemplateComparator()

    # 加载报告内容
    if md_path and os.path.exists(md_path):
        report_text = Path(md_path).read_text(encoding="utf-8")
    elif run_id:
        run = db.get_run(domain, run_id)
        if not run:
            print(f"未找到运行记录: {run_id}")
            return
        chapters_json = run.get("chapters_json", "[]")
        chapters = json.loads(chapters_json)
        report_text = "\n\n---\n\n".join(
            ch.get("markdown", "") for ch in sorted(chapters, key=lambda c: c.get("chapter_no", 0))
        )
    else:
        # 找最新的输出文件
        files = sorted(OUTPUT_DIR.glob(f"*_{domain}_*.md"), key=os.path.getmtime, reverse=True)
        if not files:
            print(f"未找到 {domain} 领域的生成报告")
            return
        md_path = str(files[0])
        report_text = Path(md_path).read_text(encoding="utf-8")
        print(f"使用最新报告: {md_path}")

    # 加载模板
    template = db.get_primary_template(domain)
    template_outline = []
    if template and template.get("outline_json"):
        template_outline = json.loads(template["outline_json"])

    print("=" * 60)
    print("  报告对比与优化")
    print("=" * 60)

    # ── 1. 内容审核扫描 ────────────────────────────────────────
    print("\n【1. 内容审核扫描】")
    hits = guard.quick_scan(report_text)
    if hits:
        from collections import Counter
        counter = Counter(hits)
        print(f"  发现 {len(hits)} 处占位词:")
        for word, count in counter.most_common():
            print(f"    「{word}」× {count}")
    else:
        print("  ✅ 未发现占位词")

    # 口语化扫描
    from kb_engine.guardrails import COLLOQUIAL_PATTERNS, META_COMMENT_PATTERNS
    colloquial_hits = []
    for pat in COLLOQUIAL_PATTERNS + META_COMMENT_PATTERNS:
        matches = re.findall(pat, report_text)
        if matches:
            colloquial_hits.extend(matches)
    if colloquial_hits:
        print(f"  发现 {len(colloquial_hits)} 处口语化/元注释: {set(colloquial_hits)}")
    else:
        print("  ✅ 未发现口语化表达")

    # ── 2. 章节结构对比 ────────────────────────────────────────
    print("\n【2. 章节结构对比】")
    # 从报告中提取已生成的章节（支持 "第X章" 和 "1." 两种格式）
    generated_chapters = []
    for line in report_text.split("\n"):
        m = re.match(r"^#{1,2}\s+(?:第)?(\d{1,2})[章.\s]+(.+)", line)
        if m:
            generated_chapters.append({
                "chapter_no": int(m.group(1)),
                "title": f"{m.group(1)} {m.group(2)}",
                "markdown": "",
            })

    comparison = comparator.compare(template_outline, template, generated_chapters)
    print(f"  {comparison['summary']}")
    print(f"  覆盖率: {comparison['coverage_pct']}%")

    if comparison["missing_chapters"]:
        print(f"\n  缺失章节:")
        for ch in comparison["missing_chapters"]:
            print(f"    第{ch.get('chapter_no','')}章 {ch.get('title','')}")

    if comparison["extra_chapters"]:
        print(f"\n  额外章节: {comparison['extra_chapters']}")

    # 子节覆盖
    print("\n  子节覆盖明细:")
    for tno, info in comparison.get("sub_coverage", {}).items():
        status = "✅" if not info.get("missing_subs") else "⚠️"
        missing = ", ".join(info.get("missing_subs", [])[:3])
        print(f"    {status} 第{tno}章: {info['covered']}/{info['template_subs']} 子节"
              + (f" (缺: {missing}...)" if missing else ""))

    # ── 3. 生成差异报告 ────────────────────────────────────────
    diff_report = {
        "domain": domain,
        "report_path": md_path or "(from run)",
        "total_chars": len(report_text),
        "placeholder_hits": hits,
        "colloquial_hits": list(set(colloquial_hits)),
        "comparison": comparison,
        "optimization_needed": bool(hits or colloquial_hits or comparison["missing_chapters"]),
    }

    diff_path = OUTPUT_DIR / f"diff_report_{domain}.json"
    diff_path.write_text(json.dumps(diff_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  差异报告已保存: {diff_path}")

    # ── 4. 优化（如有需要）────────────────────────────────────
    if not diff_report["optimization_needed"]:
        print("\n✅ 报告质量良好，无需优化。")
        db.close()
        return

    print("\n【3. 迭代优化】")
    optimized = report_text

    # 4a. 替换占位词
    if hits:
        print("  清除占位词...")
        for w in set(hits):
            optimized = optimized.replace(w, "")

    # 4b. 清除口语化
    if colloquial_hits:
        print("  清除口语化表达...")
        for hit in set(colloquial_hits):
            optimized = optimized.replace(hit, "")

    # 4c. 补充缺失章节
    if comparison["missing_chapters"]:
        print(f"  补充 {len(comparison['missing_chapters'])} 个缺失章节...")
        learned = db.get_learned_chapters(domain)
        fixed_ctx = f"公司: {_domain_company(domain)}"
        for miss_ch in comparison["missing_chapters"]:
            ch_no = miss_ch.get("chapter_no", 0)
            ch_title = miss_ch.get("title", "")
            learned_ch = next((c for c in learned if c.get("chapter_no") == ch_no), miss_ch)
            print(f"    生成第 {ch_no} 章: {ch_title}")
            try:
                content = await _generate_missing_chapter(
                    llm, domain, learned_ch, fixed_ctx, optimized[:4000]
                )
                optimized += f"\n\n---\n\n{content}"
            except Exception as e:
                print(f"    ⚠️ 生成失败: {e}")

    # 保存优化版本
    opt_path = OUTPUT_DIR / f"optimized_{domain}_{_timestamp()}.md"
    opt_path.write_text(optimized, encoding="utf-8")

    docx_path = str(opt_path).replace(".md", ".docx")
    DocxWriter().write(optimized, docx_path, title=_domain_title(domain))

    # 重新扫描
    opt_hits = guard.quick_scan(optimized)
    print(f"\n  优化后占位词: {len(opt_hits)} 处")
    print(f"  优化后总字数: {len(optimized)}")

    # 更新对比结果
    opt_diff = {
        "domain": domain,
        "optimized_path": str(opt_path),
        "before_placeholder_count": len(hits),
        "after_placeholder_count": len(opt_hits),
        "before_chars": len(report_text),
        "after_chars": len(optimized),
        "missing_chapters_added": len(comparison["missing_chapters"]) if comparison["missing_chapters"] else 0,
    }
    opt_diff_path = OUTPUT_DIR / f"optimization_result_{domain}.json"
    opt_diff_path.write_text(json.dumps(opt_diff, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 优化完成！")
    print(f"  优化版 Markdown: {opt_path}")
    print(f"  优化版 Word: {docx_path}")
    print(f"  优化结果: {opt_diff_path}")

    db.close()
    return opt_diff


def _domain_company(domain):
    return "江苏众拓项目代理咨询有限公司" if domain == KB_DOMAIN_STABILITY else "江苏众拓测绘有限公司"

def _domain_title(domain):
    return "社会稳定风险评估报告" if domain == KB_DOMAIN_STABILITY else "响应文件"

def _timestamp():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def _generate_missing_chapter(llm, domain, ch, fixed_ctx, report_context):
    guide = ch.get("writing_guide", "")
    subs = ch.get("subsections", [])
    sub_list = "\n".join(f"  - {s['title']}" for s in subs) if subs else ""
    title = ch.get("title", f"第{ch.get('chapter_no','')}章")
    company = _domain_company(domain)

    system = (
        f"你是报告编写专家，隶属于{company}。请补充生成缺失的章节。"
        "要求：正式书面语，禁止占位词和口语化，禁止AI元注释。"
    )
    prompt = (
        f"## 章节标题\n{title}\n\n"
        f"## 子节结构\n{sub_list}\n\n"
        f"## 写作指引\n{guide}\n\n"
        f"## 公司信息\n{fixed_ctx}\n\n"
        f"## 已有报告上下文（参考）\n{report_context}\n\n"
        "请撰写该章节完整内容（800-2000字），直接输出正文。"
    )
    return await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        system=system, max_tokens=4096, temperature=0.4,
    )


async def main():
    domain = sys.argv[1] if len(sys.argv) > 1 else "stability"
    target = sys.argv[2] if len(sys.argv) > 2 else ""
    if target and not target.endswith(".md"):
        # 当作 run_id
        await optimize(domain, run_id=target)
    else:
        await optimize(domain, md_path=target)


if __name__ == "__main__":
    asyncio.run(main())
