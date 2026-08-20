"""Seed 四维度审核 skill（基于测试项目 洪拟征告〔2026〕7号）。

四个维度：
1. 地方规范   —— 反对率0%、年份2026、DB32/T4013-2021 量化指标、评分范围
2. 字体/图片/表格格式 —— 图片不占位、表格有表头有真实数据、图注规范
3. 用户公告信息 —— 文号/面积/位置/地类/支持率与公告一致
4. 减少AI口语化 —— 概述/综上所述/老百姓/套词等

用法：cd backend && python3 -m seed_data.seed_review_skill
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.db"

# (skill_type, rule_pattern, rule_desc, severity, correction, chapter_num)
SKILLS = [
    # ══════════════════ 维度1：地方规范 ══════════════════
    ("rule", r'反对率\s*[：:]?\s*[1-9]\d*(?:\.\d+)?\s*%',
     "征地项目反对率必须为0%，出现非0反对率为违规", "critical",
     "反对率改为0%；征地项目合规要求反对率必须为0%", 0),
    ("rule", r'202[0-57-9]年',
     "报告年度应为2026年，出现其他年份为错误", "error",
     "年份统一为2026年（2024/2025/2027均为错误）", 0),
    ("text", "", "评分项必须来自DB32/T4013-2021量化指标体系（合法性/合理性/可行性/可控性四大类）",
     "warning", "按DB32/T4013-2021标准组织评分项，不得自创评分维度", 6),
    ("text", "", "措施前评分60-85分，措施后比措施前高5-15分",
     "warning", "措施前反映当前风险（60-85分），措施后体现化解效果（高5-15分）", 6),

    # ══════════════════ 维度2：字体/图片/表格格式 ══════════════════
    ("rule", r'待插入|【待插入',
     "图片占位未插入实际图片", "error",
     "插入实际图片（位置示意图第1章、公示照片第3章、评审照片评审章节），不保留占位", 0),
    ("text", "", "表格必须有表头、有真实数据来源，表头加粗",
     "warning", "用markdown表格语法|表头|，表头加粗，缺数据填【待补充】，不写空表", 0),
    ("text", "", "图片必须带规范图注（图X-X 描述）",
     "warning", "图注格式：图X-X 中文描述，不用原始文件名", 0),

    # ══════════════════ 维度3：用户公告信息（洪拟征告〔2026〕7号）══════════════════
    ("rule", r'[^\s]{1,6}拟征告\s*〔\d{4}〕\s*(?!7\s*号)\d+\s*号',
     "项目公告文号必须为 洪拟征告〔2026〕7号", "critical",
     "全文文号统一为 洪拟征告〔2026〕7号，不得出现其他文号", 0),
    ("rule", r'(?<![\d.])(?!489\.51\s*亩)\d+\.?\d*\s*亩',
     "征收面积必须为489.51亩（约326342㎡），其他亩数疑似错误", "critical",
     "征收面积489.51亩（约326342㎡），与勘测定界报告一致", 0),
    ("text", "", "项目关键信息必须与公告一致：文号/位置/面积/地类",
     "critical", "项目名称洪拟征告〔2026〕7号；位置朱坝街道三圩社区；面积489.51亩（326342㎡）；地类水田、林地、坑塘水面", 0),
    ("text", "", "征地项目支持率应为100%（座谈会52份调查逐页累加）",
     "critical", "支持率100%、反对率0%、知晓率96.2%；所有调查数据来自座谈会PDF逐页累加", 3),

    # ══════════════════ 维度4：减少AI口语化 ══════════════════
    ("rule", r'总的来说|显而易见|毋庸置疑|值得一提|我们可以看出|值得注意的是',
     "AI口语化总结表达", "error",
     "删去口语化总结，用规范公文表达（综合以上分析/调查表明）", 0),
    ("rule", r'老百姓|大伙',
     "口语化称呼「老百姓」", "error",
     "用「群众」「被征收人」", 0),
    ("text", "", "避免AI套词：具有重要意义/切实保障/多措并举/统筹推进/有力支撑等",
     "warning", "用具体事实陈述，不用空泛套词", 0),
]


def main():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
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
    print(f"✅ 插入 {inserted} 条四维度审核 skill，跳过 {skipped} 条重复")
    print("当前活跃 skill 总数:", cur.execute("SELECT count(*) FROM review_skills WHERE is_active=1").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
