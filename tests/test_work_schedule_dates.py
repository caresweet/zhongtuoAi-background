"""第7章「工作计划」的日期落位。

🔴 为什么有这组用例
    业主：「我在前台写入的时间内容一个都没有用到」。
    实测（`reports.id=82`）用户把 14 个日期全填成当天，成稿里只出现 **2 次**，
    而且**位置是错的** —— 日期被插到了**下一条分项标题前面**：

        …意见和建议。2026年9月24日，5.风险因素预测和防范措施制定。

    根因：模型写第7章时，分项标题与正文**在同一行**（`5.现场走访调查：评估工作组赴…`），
    而落位器一律去**下一行**找正文 —— 下一行正是下一条分项标题。
"""

from app.services.work_schedule import apply_blank_dates, apply_to_chapter7

# 模型实际写出来的形态：编号 + 标题 +「：」+ 正文，全在一行
_CH7 = """### （六）工作计划

1.成立稳评小组：受委托后成立社会稳定风险评估小组，小组名单如下：

2.制定评估方案：评估工作组从工作内容、工作方法、工作计划等方面细化评估报告大纲。

3.方案审核：将评估工作方案提交责任单位审查，根据审查意见修改完善后组织实施。

4.公告公示：在征收土地预公告期限内，在项目涉及的社区公示栏对本项目评估情况进行公示。

5.现场走访调查：评估工作组赴项目所在地，对被征地群众及周边群众开展走访调查。

6.风险因素分析：评估小组对收集的问卷、走访记录和文件资料进行系统分析。

7.报告编制与审核：编制社会稳定风险评估报告初稿，公司内部校对、审核后报送。

8.报告评审及报送：根据相关部门提出的意见修改完善评估报告，最后按规定报送备案。
"""

_FILLED = {
    "plan_date": "2026-09-02",
    "plan_approve_date": "2026-09-13",
    "publicity_start_date": "2026-09-03",
    "publicity_end_date": "2026-09-09",
    "survey_date": "2026-09-05",
    "symposium_date": "2026-09-05",
    "risk_predict_date": "2026-09-07",
    "risk_predict_end_date": "2026-09-08",
    "report_draft_date": "2026-09-09",
    "report_draft_end_date": "2026-09-10",
    "review_date": "2026-09-11",
    "review_end_date": "2026-09-12",
}


def _run(md, filled=None):
    return apply_to_chapter7(md, filled if filled is not None else dict(_FILLED))


def test_dates_land_on_the_right_item():
    out, gaps = _run(_CH7)
    # 每个分项行里都该出现自己的日期
    for kw, want in (("2.制定评估方案", "2026年9月2日"),
                     ("3.方案审核", "2026年9月13日"),
                     ("4.公告公示", "2026年9月3日"),
                     ("5.现场走访调查", "2026年9月5日"),
                     ("6.风险因素分析", "2026年9月7日"),
                     ("7.报告编制与审核", "2026年9月9日"),
                     ("8.报告评审及报送", "2026年9月11日")):
        line = next((l for l in out.split("\n") if l.startswith(kw)), "")
        assert want in line, f"「{kw}」没落上 {want}：{line[:90]}"
    assert not gaps, f"不该有未落位的日期：{[g['field'] for g in gaps]}"


def test_date_never_lands_on_the_next_item_title():
    """🔴 **回归点**：日期不能插到下一条分项标题前面（业主实测到的那个错位）。"""
    out, _ = _run(_CH7)
    for line in out.split("\n"):
        s = line.strip()
        # 「2026年…，5.风险因素…」这种「日期 + 编号标题」的形态就是错位
        assert not __import__("re").match(r'^\d{4}\s*年.*[，,]\s*[（(]?\d+\s*[.、）)]', s), \
            f"日期被插到了分项标题前面：{s[:90]}"


def test_idempotent():
    """跑两遍不能叠加日期（组装可能被重试）。"""
    once, _ = _run(_CH7)
    twice, _ = _run(once)
    assert once == twice
    assert once.count("2026年9月3日") == 1, "同一个日期被插了不止一次"


def test_missing_field_reports_gap_not_invents():
    """字段没填 → 正文一字不动，只记一条 gap（绝不编日期、不写【待补充】）。"""
    filled = dict(_FILLED)
    filled.pop("survey_date")
    filled.pop("symposium_date")
    out, gaps = _run(_CH7, filled)
    assert "2026年9月5日" not in out
    assert any(g["field"] == "survey_date" for g in gaps)


def test_no_dates_leaves_text_untouched():
    """一个日期都没填 → 正文一字不动；每条日期各记一条 gap 供审核提示。"""
    out, gaps = _run(_CH7, {})
    assert out == _CH7, "没有任何日期时不该改动正文"
    assert gaps and all(g["reason"] == "work_schedule_missing" for g in gaps)
    assert not any("_unplaced" in g["reason"] for g in gaps), "没填的不该报「已填未落位」"


# ════════════════════════════════════════════════════════════
# 空日期占位（第4章「编写时间：2026年　　月　　日」）
# ════════════════════════════════════════════════════════════

_CH4_TAIL = """### （五）参与人员签字

编写单位：某某工程咨询有限公司

编写时间：2026年　　月　　日

稳评工作组成员：
"""


def test_blank_month_day_gets_filled():
    """用户填了「报告编写年月」→ 正文那处空档要填上（全角空格也要认）。"""
    out, gaps = apply_blank_dates(_CH4_TAIL, {"report_month": "2026-09"})
    assert "编写时间：2026年9月" in out
    assert "月　　日" not in out
    assert not gaps


def test_blank_month_day_left_alone_when_unfilled():
    """没填 → **原样留着**并记 gap（绝不写当前日期糊弄）。"""
    out, gaps = apply_blank_dates(_CH4_TAIL, {})
    assert out == _CH4_TAIL, "没填时不该改动正文"
    assert [g["field"] for g in gaps] == ["report_month"]


def test_blank_month_day_idempotent():
    once, _ = apply_blank_dates(_CH4_TAIL, {"report_month": "2026-09"})
    twice, _ = apply_blank_dates(once, {"report_month": "2026-09"})
    assert once == twice
