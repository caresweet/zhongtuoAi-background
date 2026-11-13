# -*- coding: utf-8 -*-
"""报告文件名时间戳规则（业主 2026-11-12：新稿加标识、不覆盖旧稿）。"""
from datetime import datetime

from app.services.report_naming import stamped_filename


def test_appends_timestamp():
    when = datetime(2026, 11, 12, 15, 30)
    assert stamped_filename("测试_城东污水厂_复垦方案.docx", when) == \
        "测试_城东污水厂_复垦方案_20261112_153000.docx"


def test_is_idempotent():
    when = datetime(2026, 11, 12, 15, 30)
    once = stamped_filename("测试_淮阴马头镇_稳评报告.docx", when)
    assert stamped_filename(once, when) == once


def test_keeps_directory_out_of_name():
    when = datetime(2026, 11, 12, 8, 5)
    assert stamped_filename("/tmp/a/报告.docx", when) == "报告_20261112_080500.docx"
