"""Seed 通用审核 skill（不含任何项目写死的具体数据）。

四个维度（全部通用，不依赖具体项目）：
1. 地方规范   —— 反对率0%、报告年度、DB32/T4013-2021 量化指标、评分范围
2. 字体/图片/表格格式 —— 图片不占位、表格有表头有真实数据、图注规范
3. 项目数据一致性 —— 见 quality_review_agent 的动态一致性检查
   （文号/面积/支持率/位置 与每次上传资料提取出的 filled_data 动态对比，不写死）
4. 减少AI口语化 —— 概述/综上所述/老百姓/套词等

🔴 重要：skill 里**不写死任何项目数据**（如具体文号/面积/位置）。
  每个项目的具体数据准确性由「动态材料一致性检查」保证：
  - quality_review_agent._check_project_name_consistency（文号）
  - _check_area_consistency（面积）
  - _check_survey_consistency / _check_support_rate_compliance（支持率/反对率）
  - content_guardrails.find_fabricated_data（数据溯源）
  这些从 filled_data（每次资料 OCR 提取的真实数据）动态对比报告内容。

用法：cd backend && python3 -m seed_data.seed_review_skill
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.db"

# (skill_type, rule_pattern, rule_desc, severity, correction, chapter_num)
SKILLS = [
    # ══════════════════ 维度1：地方规范（通用）══════════════════
    ("rule", r'反对率\s*[：:]?\s*[1-9]\d*(?:\.\d+)?\s*%',
     "征地项目反对率必须为0%，出现非0反对率为违规", "critical",
     "反对率改为0%；征地项目合规要求反对率必须为0%", 0),
    ("rule", r'202[0-57-9]年',
     "报告年度错误（应为当前年度），出现其他年份为错误", "error",
     "年份统一为当前年度（2026年）", 0),
    ("text", "", "评分项必须来自DB32/T4013-2021量化指标体系（合法性/合理性/可行性/可控性四大类）",
     "warning", "按DB32/T4013-2021标准组织评分项，不得自创评分维度", 6),
    ("text", "", "措施前评分60-85分，措施后比措施前高5-15分",
     "warning", "措施前反映当前风险（60-85分），措施后体现化解效果（高5-15分）", 6),

    # ══════════════════ 维度2：字体/图片/表格格式（通用）══════════════════
    ("rule", r'待插入|【待插入',
     "图片占位未插入实际图片", "error",
     "插入实际图片（位置示意图第1章、公示照片第3章、评审照片评审章节），不保留占位", 0),
    ("text", "", "表格必须有表头、有真实数据来源，表头加粗",
     "warning", "用markdown表格语法|表头|，表头加粗，缺数据填【待补充】，不写空表", 0),
    ("text", "", "图片必须带规范图注（图X-X 描述）",
     "warning", "图注格式：图X-X 中文描述，不用原始文件名", 0),

    # ══════════════════ 维度3：项目数据一致性（动态，不写死）══════════════════
    # 🔴 这里不放任何具体项目的文号/面积/位置/支持率。
    #   这些由 quality_review_agent 从 filled_data 动态提取对比（见文件头注释）。
    ("text", "", "报告中引用的项目文号/面积/位置/支持率必须与上传资料提取的数据一致",
     "critical", "从 filled_data（doc_reference/area_mu/location/support_rate）动态核对，不得出现与资料不符的数据", 0),

    # ══════════════════ 维度4：减少AI口语化（通用）══════════════════
    ("rule", r'总的来说|显而易见|毋庸置疑|值得一提|我们可以看出|值得注意的是',
     "AI口语化总结表达", "error",
     "删去口语化总结，用规范公文表达（综合以上分析/调查表明）", 0),
    ("rule", r'老百姓|大伙',
     "口语化称呼「老百姓」", "error",
     "用「群众」「被征收人」", 0),
    ("text", "", "避免AI套词：具有重要意义/切实保障/多措并举/统筹推进/有力支撑等",
     "warning", "用具体事实陈述，不用空泛套词", 0),
]

# 🔴 清理历史写死的项目数据规则（仅测试项目洪拟征告〔2026〕7号适用，已废弃）
_PROJECT_SPECIFIC_KEYWORDS = ["洪拟征告", "489.51", "朱坝街道三圩", "支持率应为100%（座谈会52份"]


def main():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 删除写死的项目数据规则
    deleted = 0
    for kw in _PROJECT_SPECIFIC_KEYWORDS:
        cur.execute("DELETE FROM review_skills WHERE rule_desc LIKE ?", (f"%{kw}%",))
        deleted += cur.rowcount
    print(f"🗑️  清理写死的项目数据规则 {deleted} 条")

    # 去重：已有相同 rule_desc 则跳过
    existing = {r[0] for r in cur.execute("SELECT rule_desc FROM review_skills WHERE is_active=1").fetchall()}
    inserted = 0
    skipped = 0
    for skill_type, pattern, desc, severity, correction, ch_num in SKILLS:
        if desc in existing:
            skipped += 1
            continue
        cur.execute(
            "INSERT INTO review_skills (domain, chapter_num, skill_type, rule_pattern, rule_desc, severity, correction, is_active)"
            " VALUES ('stability', ?, ?, ?, ?, ?, ?, 1)",
            (ch_num, skill_type, pattern, desc, severity, correction),
        )
        inserted += 1
    conn.commit()
    print(f"✅ 插入 {inserted} 条通用审核 skill，跳过 {skipped} 条重复")
    print("当前活跃 skill 总数:", cur.execute("SELECT count(*) FROM review_skills WHERE is_active=1").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
