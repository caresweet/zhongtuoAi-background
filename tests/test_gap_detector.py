"""回归：缺口检测 —— 只抓**真缺东西**的地方。

🔴 2026-10-29 业主口径：「以…为准」不要再当缺口处理（那是标准法律写法，
   拿大白话去改公文只会越改越不像话）。现在只抓：悬置（待…后补）、
   材料截断、空泛判断。


这个模块的全部价值在于**信噪比**：
  · 漏报 → 客户看不到缺口，遁词留在成稿里（「不留空」被架空）；
  · 误报 → 满屏假警报，客户看两条就不看了，真缺口一起被淹没。
所以下面每一条**假警报**用例和**漏报**用例同等重要。
"""

from __future__ import annotations

import pytest

from app.services.gap_detector import collect_gaps, detect_evasions


def _kinds(gaps):
    return [g["reason"] for g in gaps]


class TestLegitimateReferencesMustPass:
    """稳评在补偿安置方案批准**之前**做 —— 指向后续法定程序的援引是**事实陈述**，不是推脱。"""

    @pytest.mark.parametrize("sentence", [
        "具体资金预算和拨付安排以征收实施单位公布的补偿安置方案为准。",
        "相关环境影响防护措施以主管部门意见和批准的实施方案为准。",
        "社会保障安置对象和名单应以批准的征收补偿安置方案为准。",
        "征收补偿标准以省人民政府公布的区片综合地价为准。",
        "具体补偿金额以依法备案的安置方案为准。",
    ])
    def test_future_stage_reference_not_flagged(self, sentence):
        gaps = detect_evasions(sentence, 10, {})
        assert not [g for g in gaps if g["reason"] == "evasion_phrase"], (
            f"法定援引被误报成遁词：{sentence}")

    @pytest.mark.parametrize("sentence", [
        "土地征收完成后，按照土地利用总体规划确定的用途进行开发建设。",
        "资金拨付应严格按照签订协议和审核确认结果执行。",
        "按照公告和征收程序执行，各项现场工作均在可控范围内。",
    ])
    def test_procedural_wording_not_flagged(self, sentence):
        """「按照…执行」描述的是**做法**，不是推脱**数据** —— 一条都不该报。"""
        assert detect_evasions(sentence, 10, {}) == []


class TestDeferredAndTruncated:
    """仍然要抓的两类：把数据推给后续程序、材料原文没抄完。"""

    def test_deferred_data(self):
        md = "拟征收土地涉及的具体户数、人口数，待现状调查和权属确认工作完成后进一步补充确认。"
        gaps = detect_evasions(md, 6, {})
        assert "deferred_data" in _kinds(gaps)

    def test_material_truncation_self_disclosure(self):
        md = "本次征收土地目的为在土地利用总体，公告原文表述至此，具体以审批文件为准。"
        gaps = detect_evasions(md, 6, {})
        assert "material_truncated" in _kinds(gaps)

    def test_reference_wording_is_no_longer_a_gap(self):
        """🔴 业主 2026-10-29：「以……为准」不再作为缺口处理。"""
        for md in (
            "具体征收范围以勘测定界成果为准。",
            "实际参加人员以座谈会签到表为准。",
            "本次征收范围内的具体户数以现状调查确认结果为准。",
        ):
            assert detect_evasions(md, 6, {"_classified_images": {"survey": ["images/签到表_x.jpg"]}}) == [], md


class TestDoesNotTouchBody:
    def test_detector_returns_gaps_only(self):
        """检测器**只标注、不改正文** —— 正文留占位符违反硬约束。"""
        md = "具体户数待现状调查完成后补充确认。"
        gaps = detect_evasions(md, 6, {})
        assert gaps
        assert md == "具体户数待现状调查完成后补充确认。"
        for g in gaps:
            assert "【待补充】" not in g["detail"]
            assert "【待填写】" not in g["detail"]

    def test_empty_markdown(self):
        assert detect_evasions("", 6, {}) == []
        assert detect_evasions(None, 6, {}) == []


class TestCollectGaps:
    def test_merges_existing_and_detected(self):
        st = {
            "_chapter_gaps": {"7": [{"field": "plan_date", "reason": "work_schedule_missing",
                                     "severity": "warn", "detail": "「制定评估方案」日期未落位"}]},
            "_classified_images": {"survey": ["images/签到表_x.jpg"]},
        }
        chapters = {
            7: "## 7.6 工作计划\n\n正常正文。",
            8: "具体人数待现状调查完成后补充确认。",
        }
        acc = collect_gaps(st, chapters)
        assert 7 in acc and any(g["reason"] == "work_schedule_missing" for g in acc[7])
        assert 8 in acc and any(g["reason"] == "deferred_data" for g in acc[8])

    def test_tolerates_string_and_int_chapter_keys(self):
        """JSON 往返会把章号变成字符串 —— 两种键都要认。"""
        st = {"_chapter_gaps": {"6": [{"detail": "x", "severity": "warn", "reason": "r"}]}}
        acc = collect_gaps(st, {6: "正文。"})
        assert 6 in acc

    def test_empty_state(self):
        assert collect_gaps({}, {}) == {}


class TestCollectGapsDedup:
    def test_no_duplicates_when_state_already_has_detected_gaps(self):
        """🔴 `_chapter_gaps` 里已有上次检测结果时，不能再检测一遍。

        组装报告会把遁词检测结果写进 `_chapter_gaps` 并随会话持久化；
        `collect_gaps` 若既读它又重跑一遍，同一处缺口会出现**两条** ——
        实测第6章 5 项翻成 10 项，弹窗里同一件事问两遍。
        """
        md = "参会人员包括群众代表，具体人数待现状调查完成后补充确认。"
        st = {}
        first = collect_gaps(st, {8: md})
        n1 = len(first.get(8) or [])
        assert n1, "首次应当检出缺口"
        st["_chapter_gaps"] = {8: list(first[8])}      # 组装时写回并持久化
        second = collect_gaps(st, {8: md})              # 再次运行（如缺口弹窗节点）
        assert len(second.get(8) or []) == n1, "重复检测导致缺口翻倍"

    def test_idempotent_across_repeated_calls(self):
        md = "具体户数待现状调查完成后补充确认。"
        st = {}
        a = collect_gaps(st, {6: md})
        b = collect_gaps(st, {6: md})
        assert len(a[6]) == len(b[6])



# ═══════════════════════════════════════════════════════════════
# 「下了判断但没给依据」的空泛句
# ═══════════════════════════════════════════════════════════════
from app.services.gap_detector import detect_hollow_claims  # noqa: E402


class TestHollowClaims:
    """🔴 业主：「生成出来的内容还是很虚，不像专家写的。」

    实测根因**不是用词**，是**下了判断却没给依据** —— 抽测 12 章 33 句评价性断言，
    22 句句内没有任何数字/文号/法规名。禁词表治不了这个。
    """

    @pytest.mark.parametrize("sent", [
        "因此，本决策符合合法性要求。",
        "拟征收范围相对集中，用地规模与建设需求相匹配。",
        "项目不涉及大面积连片征收，有利于补偿协商和后续供地工作的推进。",
        "综合地块体量、权属状况和群众意见反馈情况，本次征收总体实施难度相对可控。",
        "切实加强沟通和交流，实现信息资源共享，为科学决策提供可靠依据。",
        "本次用地有利于完善区域基础设施配套，符合当地经济社会发展的实际需要。",
    ])
    def test_hollow_claim_detected(self, sent):
        gaps = detect_hollow_claims(sent, 4)
        assert gaps and gaps[0]["reason"] == "hollow_claim", f"漏报：{sent}"

    @pytest.mark.parametrize("sent", [
        # 有数字
        "本次征收涉及八里居委会 3 个村民小组、12 户，反对率 0%。",
        # 有文号
        "补偿资金按洪拟征告〔2026〕10号预公告载明的方式落实。",
        # 有法规名
        "补偿资金由区财政局按《江苏省征地补偿和被征地农民社会保障办法》纳入预算。",
    ])
    def test_with_evidence_not_flagged(self, sent):
        assert detect_hollow_claims(sent, 4) == [], f"误报：{sent}"

    @pytest.mark.parametrize("sent", [
        # 法定口径 —— 必须写的规范表述
        "拟征收土地位置详见附图，实际征收土地范围以最终批准文件为准。",
        "补偿标准以批准的征收补偿安置方案为准。",
        # 方法性说明 —— 在交代「要评估什么」，不是下结论
        "评估本次土地征收是否与当地经济社会发展水平相适应。",
        "评估拟征收土地的用途和规模是否与实际需要相匹配。",
    ])
    def test_exemptions_not_flagged(self, sent):
        assert detect_hollow_claims(sent, 7) == [], f"误报（应当豁免）：{sent}"

    def test_flows_through_collect_gaps(self):
        """经 `detect_evasions` 汇总，最终能进缺口清单。"""
        st = {}
        acc = collect_gaps(st, {4: "因此，本决策符合合法性要求。"})
        assert 4 in acc and any(g["reason"] == "hollow_claim" for g in acc[4])

    def test_empty_and_short_ignored(self):
        assert detect_hollow_claims("", 4) == []
        assert detect_hollow_claims("有利于。", 4) == []      # 太短，不算


# ═══════════════════════════════════════════════════════════════
# 占位符不得进事实卡、也不得打断组装
# ═══════════════════════════════════════════════════════════════

class TestPlaceholdersDontBreakAssembly:
    """🔴 事故：`【待补充】` 进了事实卡，组装时 `float('【待补充】')` 抛 ValueError，
    **整份报告产不出来**（`assemble failed: could not convert string to float`）。

    两头都要堵：
      · 上游 —— 不再往 `filled_data` 写占位符；
      · 下游 —— 数值转换一律走 `_num_or / _int_or` 兜底。
    """

    def test_num_or_handles_placeholder(self):
        from app.services.report_assembler import _num_or
        assert _num_or("【待补充】", 100) == 100
        assert _num_or("", 100) == 100
        assert _num_or(None, 100) == 100
        assert _num_or("abc", 100) == 100
        assert _num_or("100%", 0) == 100
        assert _num_or("50", 0) == 50

    def test_int_or_handles_placeholder(self):
        from app.services.report_assembler import _int_or
        assert _int_or("【待补充】", 0) == 0
        assert _int_or("12", 0) == 12
        assert _int_or("1,234", 0) == 1234

    def test_upstream_does_not_write_placeholder(self):
        """`node_field_validate` 不能再把 `【待补充】` 写进 filled_data。"""
        import inspect
        from app.services.report_workflow import node_field_validate
        src = inspect.getsource(node_field_validate)
        assert 'filled[key] = "【待补充】"' not in src, \
            "还在往事实卡写占位符 —— 会被下游当值用"
