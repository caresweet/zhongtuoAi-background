# -*- coding: utf-8 -*-
"""电话字段只能是号码 —— 不能把整句公示内容印到扉页（业主 2026-11-09）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PUBLICITY = ("公示内容包括：项目名称、项目单位、项目简介、复垦内容等相关情况介绍"
              "以及公众认为必要时向建设单位提供建议时的联系方式。")


def test_phone_field_rejects_publicity_sentence():
    from app.services.field_sanitizer import sanitize_fields

    filled = {"contact_phone": _PUBLICITY}
    cleared = sanitize_fields(filled)
    assert filled["contact_phone"] == ""
    assert any("contact_phone" in c for c in cleared)


def test_phone_field_keeps_number_only():
    from app.services.field_sanitizer import sanitize_fields

    filled = {"contact_phone": "联系电话：0517-85391266"}
    sanitize_fields(filled)
    assert filled["contact_phone"] == "0517-85391266"


def test_phone_field_untouched_when_already_clean():
    from app.services.field_sanitizer import sanitize_fields

    filled = {"contact_phone": "0517-85391266"}
    sanitize_fields(filled)
    assert filled["contact_phone"] == "0517-85391266"


def test_material_ingestion_does_not_take_publicity_as_phone():
    """别名「联系方式」出现在句末但没有冒号，不能再被当成电话取值。"""
    from app.services.material_ingestion_service import material_ingestion_service

    out = material_ingestion_service._extract_structured_fields(_PUBLICITY)
    assert "contact_phone" not in out

    out2 = material_ingestion_service._extract_structured_fields(
        _PUBLICITY + "\n联系电话：0517-85391266")
    assert out2.get("contact_phone") == "0517-85391266"
