"""回归：字段来源追踪 + 弹窗清单动态化。

对应业主口径（2026-09-22）：
    「弹窗的日期这些不能固定，正确的逻辑应该是 agent 觉得缺少什么找用户，
      然后解析报告的内容是否准确找用户确认。」

两条断言线：
  ① **清单不能写死** —— 问什么必须从 `CHAPTER_SECTION_SPECS` 推导出来，
     且「问了也答不上来」的字段（系统算的风险等级等）必须被滤掉；
  ② **机器解析的值要能问出来** —— 靠 `field_sources` 的来源标记 + `_confirmed_`，
     而不是靠 `_locked_`（它一个标记担了两种含义，判不了「用户看过没有」）。
"""

from __future__ import annotations

import pytest

from app.services import field_sources as FS


# ═══════════════════════════════════════════════════════════════
# ① 来源追踪
# ═══════════════════════════════════════════════════════════════

class TestSourceTracking:
    def test_records_source(self):
        f = {}
        FS.mark(f, FS.MATERIAL, "area_m2")
        assert FS.source_of(f, "area_m2") == FS.MATERIAL
        assert FS.label_of(f, "area_m2") == "材料识别"

    def test_higher_priority_not_overwritten_by_lower(self):
        """🔴 材料识别出来的值，不能被后来的 AI 提取盖掉。"""
        f = {}
        FS.mark(f, FS.MATERIAL, "area_m2")
        FS.mark(f, FS.AI, "area_m2")
        assert FS.source_of(f, "area_m2") == FS.MATERIAL

    def test_user_overrides_everything(self):
        f = {}
        FS.mark(f, FS.MATERIAL, "location")
        FS.mark(f, FS.USER, "location")
        assert FS.source_of(f, "location") == FS.USER

    def test_ignores_underscore_keys(self):
        f = {}
        FS.mark(f, FS.MATERIAL, "_internal", "ok_field")
        assert FS.source_of(f, "_internal") == ""
        assert FS.source_of(f, "ok_field") == FS.MATERIAL

    def test_mark_written_skips_empty_values(self):
        f = {}
        FS.mark_written(f, FS.AI, {"a": "1", "b": "", "c": None, "d": "  "})
        assert FS.source_of(f, "a") == FS.AI
        for k in ("b", "c", "d"):
            assert FS.source_of(f, k) == ""

    def test_works_with_state_or_filled(self):
        st = {"filled_data": {}}
        FS.mark(st, FS.SYSTEM, "publicity_end_date")
        assert FS.source_of(st, "publicity_end_date") == FS.SYSTEM
        assert FS.source_of(st["filled_data"], "publicity_end_date") == FS.SYSTEM


class TestConfirmed:
    def test_confirmed_is_separate_from_locked(self):
        """🔴 `_locked_` 与 `_confirmed_` **不是一回事**。

        `_locked_` 还承担着「别覆盖我」的旧含义 —— 预公告解析也会打它，
        所以拿它判「用户看过没有」是错的（这正是要新增 `_confirmed_` 的原因）。
        """
        f = {"doc_reference": "洪拟征告〔2026〕7号", "_locked_doc_reference": True}
        # 直接断言标记本身（`is_confirmed()` 守卫已随「材料抽到的不再问」一并删除，
        # 但「`_locked_` 不等于 `_confirmed_`」这条语义不变，`_missing_for` 仍在用它）
        assert not f.get("_confirmed_doc_reference")
        FS.mark_confirmed(f, "doc_reference")
        assert f.get("_confirmed_doc_reference") is True


# ═══════════════════════════════════════════════════════════════
# ② 弹窗清单动态化
# ═══════════════════════════════════════════════════════════════

class TestDynamicNeedFields:
    def test_derives_from_section_specs(self):
        """第8章的 spec 声明了 `symposium_attendees` —— 旧硬清单里没有它。"""
        from app.agent.chapter_definitions import chapter_need_fields
        keys = [f[0] for f in chapter_need_fields(8)]
        assert "symposium_attendees" in keys, "spec 声明的依赖必须进清单"

    def test_filters_non_askable(self):
        """🔴 问了也答不上来的字段必须滤掉 —— 风险等级是系统算的。"""
        from app.agent.chapter_definitions import (
            chapter_need_fields, NON_ASKABLE_FIELDS)
        for ch in range(1, 15):
            keys = {f[0] for f in chapter_need_fields(ch)}
            bad = keys & NON_ASKABLE_FIELDS
            assert not bad, f"第{ch}章的问清单里混进了不可问字段：{bad}"

    def test_returns_six_tuples(self):
        from app.agent.chapter_definitions import chapter_need_fields
        for ch in range(1, 15):
            for spec in chapter_need_fields(ch):
                assert len(spec) == 6, f"第{ch}章 {spec} 不是六元组"
                k, label, ftype, pat, maxlen, ex = spec
                assert k and label and ftype, f"元数据不全：{spec}"

    def test_unknown_field_still_included(self):
        """两处都没登记过的字段也不能丢 —— 按名字后缀猜类型。"""
        from app.agent.chapter_definitions import chapter_need_fields
        for ch in range(1, 15):
            for k, label, ftype, *_ in chapter_need_fields(ch):
                assert ftype in ("text", "number", "date", "phone"), \
                    f"{k} 的推断类型不合法：{ftype}"


class TestMissingForRespectsConfirmed:
    def test_confirmed_empty_field_not_asked_again(self):
        """🔴 用户确认过「确实没有」的字段（值为空）不该再弹 —— 「已有值不问」盖不住这种。"""
        from app.services.report_workflow import _missing_for
        spec = ("org_leader", "责任单位负责人", "text", None, 20, "张三")
        assert _missing_for({}, [spec]) , "为空时应当报缺"
        confirmed = {"_confirmed_org_leader": True}
        assert _missing_for(confirmed, [spec]) == [], "用户确认过的不该再问"

    def test_locked_alone_does_not_suppress(self):
        """`_locked_` 判不了「用户确认过」—— 材料解析也会打它。"""
        from app.services.report_workflow import _missing_for
        spec = ("org_leader", "责任单位负责人", "text", None, 20, "张三")
        assert _missing_for({"_locked_org_leader": True}, [spec]), \
            "只有 _locked_ 不该被当成「用户确认过」"


# ═══════════════════════════════════════════════════════════════
# ③ 弹窗文案必须是中文（业主：「弹窗的消息可以用中国标题么不要用英文字段」）
# ═══════════════════════════════════════════════════════════════

_HAS_CN = __import__("re").compile(r'[一-鿿]')


def _cn(s) -> bool:
    return bool(_HAS_CN.search(str(s or "")))


class TestPopupLabelsAreChinese:
    """🔴 弹窗里**任何**给用户看的文字都不能是英文 key。

    实测踩过：`FIELD_META` 只登记了一部分字段，兜底直接回落到 key，
    于是弹窗上出现 `doc_reference`、`symposium_attendees` 这种，业主看不懂。
    """

    def test_chapter_popup_labels(self):
        from app.agent.chapter_definitions import chapter_need_fields
        bad = [(ch, k, label)
               for ch in range(1, 15)
               for k, label, *_ in chapter_need_fields(ch) if not _cn(label)]
        assert not bad, f"章节弹窗里出现非中文 label：{bad}"

    def test_review_table_popup_labels(self):
        from app.services.report_workflow import REVIEW_TABLE_FIELDS
        bad = [f for f in REVIEW_TABLE_FIELDS if not _cn(f[1])]
        assert not bad, f"评审表弹窗里出现非中文 label：{bad}"

    def test_data_confirm_popup_labels(self):
        """「数据核对」弹窗的候选字段 —— 每个都要有中文元数据。"""
        from app.agent.chapter_definitions import FIELD_META, NON_ASKABLE_FIELDS
        from app.services.data_usage_map import FIELD_USAGE
        ranked = sorted(FIELD_USAGE.keys(), key=lambda x: -len(FIELD_USAGE.get(x) or []))
        cand = [k for k in ranked
                if len(FIELD_USAGE.get(k) or []) >= 2 and k not in NON_ASKABLE_FIELDS]
        bad = [k for k in cand if not _cn((FIELD_META.get(k) or ("",))[0])]
        assert not bad, f"核对弹窗里出现非中文 label：{bad}"

    def test_every_registry_label_is_chinese(self):
        """`FIELD_META` 整张表都不许出现英文 label。"""
        from app.agent.chapter_definitions import FIELD_META
        bad = [k for k, v in FIELD_META.items() if not _cn(v[0])]
        assert not bad, f"FIELD_META 里的 label 不是中文：{bad}"

    def test_unknown_field_falls_back_to_chinese(self):
        """🔴 没登记的字段，兜底也必须是中文 —— 绝不回落英文 key。"""
        from app.agent.chapter_definitions import chapter_need_fields
        import app.agent.chapter_definitions as cd
        # 临时往 spec 里塞一个没登记过的字段
        _spec = cd.CHAPTER_SECTION_SPECS.setdefault(6, [{}, ])
        try:
            _spec[0].setdefault("data", []).append("__nobody_registered_this__")
            keys = {f[0]: f for f in chapter_need_fields(6)}
            spec = keys.get("__nobody_registered_this__")
            assert spec is not None, "未登记的字段不该被丢掉"
            assert _cn(spec[1]), f"兜底 label 漏了英文：{spec[1]!r}"
        finally:
            _spec[0]["data"].remove("__nobody_registered_this__")


# ═══════════════════════════════════════════════════════════════
# ④ 「问了也答不上来 / 不该问」的字段必须被挡住
# ═══════════════════════════════════════════════════════════════

class TestNotAskable:
    def test_opinion_fields_not_asked(self):
        """🔴 「各方意见」类字段**没有任何材料来源**，问了就是逼用户自己编群众意见。

        业主：「群众意见你不能没有解析就去直接让用户去写，这一段是需要你发挥的。」
        """
        from app.agent.chapter_definitions import chapter_need_fields
        keys = {f[0] for f in chapter_need_fields(9)}
        for k in ("villager_demands", "dept_opinions", "grassroots_opinion",
                  "online_opinion"):
            assert k not in keys, f"{k} 不该让用户从零写"

    def test_field_without_consumer_not_asked(self):
        """🔴 `notice_publish_date` 全仓库无消费方 —— 问了也白问。"""
        from app.agent.chapter_definitions import NON_ASKABLE_FIELDS
        assert "notice_publish_date" in NON_ASKABLE_FIELDS

    def test_no_fallback_for_org_personnel(self):
        """🔴 责任单位人事信息**不给 AI 兜底拟稿** —— 拟出来一定是错的。

        实测：让模型拟「稳评责任单位」，它按事实卡写成了
        「江苏众拓项目代理咨询有限公司」——那是**实施单位**（本公司），
        责任单位是政府。事实卡里两者并存，模型分不清。
        """
        from app.services.report_workflow import _NO_FALLBACK_FIELDS
        for k in ("org_name", "org_leader", "org_leader_title",
                  "org_contact", "org_phone", "decision_unit"):
            assert k in _NO_FALLBACK_FIELDS, f"{k} 不该被 AI 兜底拟稿"


class TestAttachmentCleanup:
    def test_ledger_and_tail_sections_removed(self):
        """🔴 「不稳定因素动态排查台账样表」「后续补充归档材料」已删（业主口径 B）。

        它们是**纯文本、不是图片**，与「附件只放图片」的要求不符。
        """
        import inspect
        from app.services import report_assembler as RA
        src = inspect.getsource(RA)
        assert "'ledger'" not in src and "'tail'" not in src, \
            "附件章里还留着 ledger/tail 两节"


class TestPlaceholdersNotTreatedAsValues:
    """🔴 `【待补充】` 是**占位符**不是数据 —— 不能当「有值」用。

    踩过：核对弹窗把它预填进输入框，用户以为「系统认为这就是答案」。
    """

    def test_popup_examples_have_no_real_project_data(self):
        """🔴 `FIELD_META` 的示例是**给用户看的占位符**，不能是真实项目数据。

        业主看到食品项目的弹窗里显示「洪拟征告〔2026〕7号」，
        以为系统把别的项目的数据串进来了 —— 其实是示例文字写成了真实文号。
        """
        import re
        from app.agent.chapter_definitions import FIELD_META
        BAD = ("洪拟征", "朱坝", "万通", "三圩")
        hits = [(k, v[4]) for k, v in FIELD_META.items()
                if any(b in str(v[4] or "") for b in BAD)]
        assert not hits, f"示例里混进了真实项目数据：{hits}"
        # 日期类示例不出现在界面上（date 输入框无 placeholder），跳过
        for k, v in FIELD_META.items():
            if v[1] == "date":
                continue
            ex = str(v[4] or "")
            if ex and not re.search(r'(XX|例如|如：|张三|李四|王五)', ex):
                # 纯数字示例（100/50/2/13800000000）也放行
                if not re.fullmatch(r'[\d.\-]+', ex):
                    print(f"  提示：{k} 的示例 {ex!r} 未含通用占位符")


class TestSkipDoesNotLoop:
    """🔴 用户**跳过**弹窗后，同一批字段不能再弹第二次 —— 否则死循环。

    实测端到端跑第7章那 12 个日期时卡死：全部跳过后，`_missing_for` 每次重算
    都发现它们还是空 → 再次 interrupt → 前端/脚本若也跳过同一批，就永远出不来。

    修法：跳过的字段打 `_confirmed_`（「跳过」= 用户明确表示「别问了」）。
    """

    def test_skipped_fields_stop_being_asked(self):
        from app.services.report_workflow import _missing_for
        specs = [("commission_date", "委托日期", "date", None, 10, "2026-06-02"),
                 ("survey_date", "实地勘察日期", "date", None, 10, "2026-06-05"),
                 ("symposium_date", "座谈会日期", "date", None, 10, "2026-06-05")]
        filled = {}
        assert len(_missing_for(filled, specs)) == 3, "首次应当报 3 项"
        # 用户点「跳过」→ 系统打 `_confirmed_`
        FS.mark_confirmed(filled, *[s[0] for s in specs])
        assert _missing_for(filled, specs) == [], "跳过后不能再弹（否则死循环）"

    def test_partial_skip(self):
        """用户填了一部分、跳过一部分 —— 只补问剩下的。"""
        from app.services.report_workflow import _missing_for
        specs = [("a_date", "甲日期", "date", None, 10, ""),
                 ("b_date", "乙日期", "date", None, 10, "")]
        filled = {"a_date": "2026-01-01"}
        FS.mark_confirmed(filled, "b_date")          # b 被跳过
        miss = _missing_for(filled, specs)
        assert [m["key"] for m in miss] == [], f"不该再问：{[m['key'] for m in miss]}"
