"""回归：勘测定界解析路径 / 第6章骨架编号 / 工作程序时间落位。

这三个都出自同一次实跑（会话 a5b889e9…，2026-09-21）暴露出来的缺陷，
共同点是**单看代码都对、一跑全本才炸**，所以各留一条断言钉住。

────────────────────────────────────────────────────────────────
① 勘测定界「文件不存在」
   材料登记里存的是**相对 storage** 的路径（`images/0-勘测定界报告-_157b8cb3.pdf`），
   解析器按 CWD 找 → 每次都走「文件不存在」分支 →
   第6章 `[SYS_TABLE:6-1]` 渲染不出表，正文却印着「上表所列拟征收土地总面积…」，
   **报告自相引用一张不存在的表**。

② 第6章概况项跳号 / 空标题
   骨架里没内容的条目会被跳过（「不留空、不编造」），若用骨架下标当序号，
   正文里就出现 1、2、4、6… 的跳号。反之若某条的正文只有渲染不出来的
   `[SYS_TABLE:…]` 标记，又会留下一个**光秃秃的标题**。

③ 用户填的工作程序时间没进正文
   17 个日期只落位 7 个。第7章「工作计划」分项在参照件里带日期，
   改写时被当参照件项目日期剔除（对的），但没把用户填的补回去。
────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re

import pytest


# ═══════════════════════════════════════════════════════════════
# ① 勘测定界：相对 storage 的路径必须能解析
# ═══════════════════════════════════════════════════════════════

class TestLandSurveyPath:
    def test_resolve_relative_to_storage(self, tmp_path, monkeypatch):
        """`images/x.pdf` 应被还原成 `<STORAGE_DIR>/images/x.pdf`。"""
        from app.services import land_survey_parser as L
        from app.config import settings

        fake_storage = tmp_path / "storage"
        (fake_storage / "images").mkdir(parents=True)
        real = fake_storage / "images" / "0-勘测定界报告-_abc.pdf"
        real.write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(settings, "STORAGE_DIR", fake_storage, raising=False)
        out = L.resolve_material_path("images/0-勘测定界报告-_abc.pdf")
        assert out == str(real), "相对 storage 的路径没被还原 → 解析器永远报「文件不存在」"

    def test_absolute_path_untouched(self, tmp_path):
        from app.services import land_survey_parser as L
        p = tmp_path / "x.pdf"
        p.write_bytes(b"%PDF-1.4")
        assert L.resolve_material_path(str(p)) == str(p)

    def test_missing_path_returns_input(self):
        from app.services import land_survey_parser as L
        assert L.resolve_material_path("images/不存在.pdf") == "images/不存在.pdf"

    def test_parse_land_survey_reports_missing(self, caplog):
        """找不到文件时仍返回空 dict（不抛），保持调用方可降级。"""
        from app.services import land_survey_parser as L
        assert L.parse_land_survey("images/绝对不存在_zzz.pdf") == {}

    def test_collect_land_survey_scans_uploaded_files(self, tmp_path, monkeypatch):
        """`collect_land_survey` 要扫 `_uploaded_files`（权威上传清单），
        而不是只扫常常漏登记的 `_project_materials`。"""
        from app.services import land_survey_parser as L
        from app.config import settings

        fake_storage = tmp_path / "storage"
        (fake_storage / "images").mkdir(parents=True)
        (fake_storage / "images" / "1-勘测定界报告-.pdf").write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(settings, "STORAGE_DIR", fake_storage, raising=False)

        seen = {}

        def _fake_parse(path):
            seen["path"] = path
            return {"area_m2": 326342, "location": "朱坝街道", "plots": [], "classifications": []}

        monkeypatch.setattr(L, "parse_land_survey", _fake_parse)
        monkeypatch.setattr(L, "to_extracted_tables", lambda p: [{"headers": ["a"], "rows": [[1]]}])

        state = {"_uploaded_files": [
            {"path": "images/无关.pdf", "original_name": "公示照片.pdf"},
            {"path": "images/1-勘测定界报告-.pdf", "original_name": "稳评资料/1-勘测定界报告-.pdf"},
        ]}
        tables, parsed = L.collect_land_survey(state)
        assert tables, "从 _uploaded_files 里没找到勘测定界报告"
        assert parsed["area_m2"] == 326342
        assert "定界" in seen["path"] or "勘测" in seen["path"]

    def test_collect_land_survey_empty_when_absent(self):
        from app.services import land_survey_parser as L
        assert L.collect_land_survey({"_uploaded_files": [
            {"path": "images/公示照片.jpg", "original_name": "公示照片.jpg"}]}) == ([], {})


# ═══════════════════════════════════════════════════════════════
# ② 第6章骨架：编号连续 + 不留空标题
# ═══════════════════════════════════════════════════════════════

_CH6_SAMPLE = """# 第六章 项目基本情况

## （一）项目概况

#### 1. 项目名称

洪拟征告〔2026〕7号土地征收决策。

#### 2. 项目相关单位

实施单位为江苏众拓项目代理咨询有限公司。

#### 5. 建设规模及内容

[SYS_TABLE:6-1]

#### 6. 项目用地及周边情况

拟征收土地现状地类为水田、林地等。

#### 10. 项目建设周期

实施周期自预公告发布之日起至土地征收实施结束。

## （二）利益相关者情况

略。
"""


class TestChapter6Skeleton:
    def _prep(self, md):
        from app.agent.chapter_definitions import prepare_chapter6_markdown
        return prepare_chapter6_markdown(md)

    def test_numbering_is_consecutive(self):
        out = self._prep(_CH6_SAMPLE)
        nums = [int(m.group(1)) for m in re.finditer(r'^#### (\d+)\.', out, re.M)]
        assert nums, "一条概况项都没输出"
        assert nums == list(range(1, len(nums) + 1)), (
            f"编号跳号：{nums} —— 空条目被跳过后没有重排序号")

    def test_table_marker_survives(self):
        """`[SYS_TABLE:…]` 是**要渲染的表**，不能当「没内容」过滤掉。"""
        out = self._prep(_CH6_SAMPLE)
        assert "[SYS_TABLE:6-1]" in out, "SYS_TABLE 标记被过滤 → 表6-1 永远出不来"

    def test_no_empty_heading(self):
        out = self._prep(_CH6_SAMPLE)
        lines = [l for l in out.split("\n")]
        for i, l in enumerate(lines):
            if re.match(r'^#### \d+\.', l):
                body = [x for x in lines[i + 1:] if x.strip()]
                assert body, f"光标题：「{l}」后面什么都没有"

    def test_content_not_lost(self):
        out = self._prep(_CH6_SAMPLE)
        for frag in ("洪拟征告〔2026〕7号土地征收决策。", "江苏众拓", "水田",
                     "实施周期自预公告发布之日起"):
            assert frag in out, f"归位时丢了内容：{frag}"


# ═══════════════════════════════════════════════════════════════
# ③ 工作程序时间落位
# ═══════════════════════════════════════════════════════════════

_CH7_SAMPLE = """# 第七章 稳评工作方案

## 7.6 工作计划

2.制定评估方案

评估工作组从工作内容、工作方法、工作计划等方面，细化评估报告大纲，制定了评估工作方案，主要包括以下内容：

（1）公告公示

在项目所在地朱坝街道集体公示栏公示，广泛听取利益相关者意见。

（2）现场走访调查

公示期结束后，稳评工作小组走访调查现场以及项目地周边群众。

（3）风险因素预测、制定防范措施及预案

稳评小组对收集的资料进行系统分析。

（4）报告编制与审核

根据规范要求编制稳评报告，编制完成后由公司内部校对、审核。

（5）报告评审及报送

将评估报告送达委托单位征求意见及建议。

3.方案审核

提交评估工作方案至责任单位。

稳评责任单位（盖章）

年月日
"""

_FILLED = {
    "plan_date": "2026-09-02",
    "survey_date": "2026-09-05",
    "symposium_date": "2026-09-06",
    "risk_predict_date": "2026-09-07",
    "risk_predict_end_date": "2026-09-08",
    "report_draft_date": "2026-09-09",
    "report_draft_end_date": "2026-09-10",
    "review_date": "2026-09-11",
    "review_end_date": "2026-09-12",
    "plan_approve_date": "2026-09-13",
}


class TestWorkSchedule:
    def _apply(self, md=None, filled=None):
        from app.services.work_schedule import apply_to_chapter7
        return apply_to_chapter7(md if md is not None else _CH7_SAMPLE,
                                 _FILLED if filled is None else filled)

    def test_all_dates_land(self):
        out, gaps = self._apply()
        for frag in ("2026年9月2日",           # 制定评估方案
                     "2026年9月5日—2026年9月6日",   # 现场走访调查
                     "2026年9月7日—2026年9月8日",   # 风险因素预测
                     "2026年9月9日—2026年9月10日",  # 报告编制与审核
                     "2026年9月11日—2026年9月12日",  # 报告评审及报送
                     "2026年9月13日"):          # 方案审核落款
            assert frag in out, f"日期没落位：{frag}"
        assert not [g for g in gaps if g["reason"] == "work_schedule_unplaced"]

    def test_idempotent(self):
        """重复生成不能把日期叠两遍。"""
        once, _ = self._apply()
        twice, _ = self._apply(once)
        assert once == twice
        assert once.count("2026年9月5日") == 1

    def test_missing_field_leaves_text_untouched(self):
        """字段没填 → 原句一字不动，只记 gap，绝不写占位符。"""
        out, gaps = self._apply(filled={"plan_date": "2026-09-02"})
        assert "2026年9月5日" not in out
        assert "【待补充】" not in out and "【待填写】" not in out
        assert any(g["reason"] == "work_schedule_missing" for g in gaps)

    def test_does_not_touch_existing_dates(self):
        """句子里已经有日期（如已算出的公示期）就不要动它。"""
        md = "（1）公告公示\n\n公示期为2026年7月23日至2026年7月29日。\n"
        filled = dict(_FILLED, publicity_start_date="2026-07-23",
                      publicity_end_date="2026-07-29")
        out, _ = self._apply(md, filled)
        assert "2026年7月23日至2026年7月29日" in out
        assert out.count("2026年") == 2

    def test_no_fabrication_when_all_empty(self):
        out, gaps = self._apply(filled={})
        assert out == _CH7_SAMPLE, "没有可用日期时不该改动正文"
