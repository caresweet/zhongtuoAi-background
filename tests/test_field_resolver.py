"""回归：字段三级取值链 —— 用户填的 > 材料识别的 > 【待填写】。

业主口径（2026-09-22）：
    「每次都会给用户出弹窗让填写，用户填了就用用户填的，用户跳过就用**预公告里
      识别到的**，两者都没有才落「待填写」占位。其他内容也都一样。」

钉住两件事：
① 三级顺序不能乱 —— 尤其是「材料值不能盖掉用户填的值」；
② 别名映射只接**语义对得上**的栏（预公告的「联系电话」→ 责任单位联系电话 ✅；
   预公告的公示联系人 → 责任单位**负责人** ❌，那是张冠李戴）。
"""

from __future__ import annotations

import pytest

from app.services.field_resolver import PLACEHOLDER, MATERIAL_ALIASES, resolve, resolve_many


class TestPriority:
    def test_user_value_wins(self):
        """第二级有值时，用户填的仍然是第一优先。"""
        filled = {"org_phone": "13900000000", "contact_phone": "13952361878"}
        assert resolve(filled, "org_phone") == "13900000000"

    def test_material_used_when_user_skips(self):
        """用户跳过（键不存在/为空）→ 落到材料识别值。"""
        assert resolve({"contact_phone": "13952361878"}, "org_phone") == "13952361878"
        assert resolve({"org_phone": "", "contact_phone": "13952361878"}, "org_phone") \
            == "13952361878"

    def test_placeholder_when_neither(self):
        assert resolve({}, "org_phone") == PLACEHOLDER
        assert resolve({"org_phone": None, "contact_phone": None}, "org_phone") == PLACEHOLDER

    def test_default_override_for_body_text(self):
        """正文不能写占位符 —— 调用方传中性表述。"""
        out = resolve({}, "org_name", default="以预公告载明为准")
        assert out == "以预公告载明为准"
        assert "【待" not in out

    def test_many_preserves_priority_order(self):
        got = resolve_many({"contact_name": "张三"}, ["org_contact", "org_phone"])
        assert got == {"org_contact": "张三", "org_phone": PLACEHOLDER}


class TestPseudoValues:
    @pytest.mark.parametrize("bad", ["", "  ", "-", "—", "/", "无", "未检测到", "未提及",
                                     "未知", "不详", "None", "null", "nan"])
    def test_pseudo_values_count_as_missing(self, bad):
        """伪值不能被当「有值」——否则会一路写进成品。"""
        assert resolve({"org_phone": bad}, "org_phone") == PLACEHOLDER

    def test_existing_placeholder_is_not_a_value(self):
        """已经是占位符的值不能再被当成「用户填的内容」传下去。"""
        assert resolve({"org_phone": "【待填写】", "contact_phone": "13912345678"},
                       "org_phone") == "13912345678"
        assert resolve({"org_phone": "【待填写】"}, "org_phone") == PLACEHOLDER

    def test_real_value_passes_through(self):
        assert resolve({"org_phone": "0517-80920828"}, "org_phone") == "0517-80920828"


class TestAliasSemantics:
    def test_contact_aliases_are_wired(self):
        assert "contact_phone" in MATERIAL_ALIASES["org_phone"]
        assert "contact_name" in MATERIAL_ALIASES["org_contact"]

    def test_leader_has_no_material_alias(self):
        """负责人/职务**不接**材料别名：预公告上没有甲方人事信息，
        硬套别名就会把「公示联系人」填进「负责人」栏（此前踩过的张冠李戴）。"""
        assert not MATERIAL_ALIASES.get("org_leader")
        assert not MATERIAL_ALIASES.get("org_leader_title")
        assert resolve({"contact_name": "王慧", "contact_phone": "0517-80920828"},
                       "org_leader") == PLACEHOLDER

    def test_no_self_referential_alias(self):
        """别名表里不能出现指向自己的死循环式配置。"""
        for k, alias in MATERIAL_ALIASES.items():
            assert k not in alias, f"{k} 的别名链里包含自己"
