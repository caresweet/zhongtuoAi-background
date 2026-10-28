"""回归：项目名要**从材料里取**，不能拿文号拼一个。

🔴 业主：「第一页的内容应该是 食品xxx的项目。」

实测：材料里到处写着真项目名 ——
    案卷首页第一行    「食品科技产业园道路工程（项目1）土地征收决策」
    座谈会签到表      「决策名称:食品科技产业园道路工程(项目1)土地征收决策」
    专家评审意见表    「决策事项名称 食品科技产业园道路工程（项目1）土地征收决策」

但 `_derive_project_name` 拿**文号**拼了个「洪拟征告〔2026〕10号土地征收决策」，
`is_junk()` 又判它「不垃圾」→ 不替换 → **封面印的是文号，不是项目名**。
"""

from __future__ import annotations

import pytest

from app.services.field_sanitizer import extract_project_name, sanitize_fields


def _state_with(*texts):
    return {"_project_materials": [{"text_content": t} for t in texts]}


REAL = "食品科技产业园道路工程（项目1）土地征收决策"


class TestExtractFromMaterials:
    def test_from_labeled_line(self):
        """带「决策名称：」标签的行最可靠。"""
        st = _state_with("座谈会签到表\n决策名称:食品科技产业园道路工程(项目1)土地征收决策\n时间:2026.6.18")
        assert extract_project_name(st).replace("(", "（").replace(")", "）") == REAL

    def test_from_first_line(self):
        """案卷首页第一行就是项目名。"""
        st = _state_with(REAL + "\n社会稳定风险评估工作案卷\n江苏众拓项目代理咨询有限公司")
        assert extract_project_name(st) == REAL

    def test_ignores_form_title(self):
        """「社会稳定风险评估工作案卷」这类套话不能被当项目名。"""
        st = _state_with("社会稳定风险评估工作案卷\n公司简介\n……")
        assert extract_project_name(st) == ""

    def test_empty_state(self):
        assert extract_project_name({}) == ""
        assert extract_project_name({"_project_materials": []}) == ""


class TestMaterialNameWins:
    def test_overrides_derived_from_docref(self):
        """🔴 材料里真有名字时，必须顶掉「文号派生」的那个 —— 哪怕它看起来不垃圾。"""
        st = _state_with(REAL, "洪拟征告〔2026〕10号")
        filled = {
            "doc_reference": "洪拟征告〔2026〕10号",
            "project_name": "洪拟征告〔2026〕10号土地征收决策",   # 文号派生的
            "land_use": "工业用地",
        }
        sanitize_fields(filled, state=st)
        assert filled["project_name"] == REAL, "材料里的真名没顶掉文号派生的名字"

    def test_user_confirmed_value_kept(self):
        """用户**亲手填过**的不能被顶掉（判据是 `_confirmed_`，不是 `_locked_`）。"""
        st = _state_with(REAL)
        filled = {"project_name": "用户自己填的项目名", "_confirmed_project_name": True}
        sanitize_fields(filled, state=st)
        assert filled["project_name"] == "用户自己填的项目名"

    def test_falls_back_to_derivation(self):
        """材料里没有 → 才退回文号派生。"""
        st = _state_with("洪拟征告〔2026〕10号")
        filled = {"doc_reference": "洪拟征告〔2026〕10号", "land_use": "工业用地"}
        sanitize_fields(filled, state=st)
        assert "洪拟征告〔2026〕10号" in filled["project_name"]


class TestVisionCannotTouchMaterialFields:
    """业主 2026-10-28：视觉分析不得影响用户填的 / 材料识别出来的信息。

    实测事故：洪拟征告〔2026〕7号 的会话里混进一张「万通国际新城」的位置图，
    视觉分析把 `project_name` 写成万通国际新城，封面与评审表全变成别的项目。
    """

    def _state(self, filled):
        return {"filled_data": filled}

    def test_material_sourced_key_is_untouched(self):
        from app.services.deep_material_analyzer import apply_analysis_to_state

        st = self._state({
            "doc_reference": "洪拟征告〔2026〕7号",
            "org_name": "淮安市洪泽区人民政府",
            "_field_sources": {"org_name": "material"},
        })
        apply_analysis_to_state(st, {"filled_data_updates": {
            "org_name": "万通国际新城管委会", "project_name": "万通国际新城"}})
        assert st["filled_data"]["org_name"] == "淮安市洪泽区人民政府"
        assert st["filled_data"].get("project_name", "") == ""

    def test_user_sourced_key_is_untouched(self):
        from app.services.deep_material_analyzer import apply_analysis_to_state

        st = self._state({"area_mu": "489.511", "_field_sources": {"area_mu": "user"}})
        apply_analysis_to_state(st, {"filled_data_updates": {"area_mu": "12.1"}})
        assert st["filled_data"]["area_mu"] == "489.511"

    def test_ai_can_still_fill_unclaimed_keys(self):
        from app.services.deep_material_analyzer import apply_analysis_to_state

        st = self._state({})
        apply_analysis_to_state(st, {"filled_data_updates": {"household_count": "50"}})
        assert st["filled_data"]["household_count"] == "50"
