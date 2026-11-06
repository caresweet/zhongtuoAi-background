# -*- coding: utf-8 -*-
"""地区识别：从报告文本里认出区县名。"""

from app.services.region_detect import detect_region


def test_huai_an_qu():
    r = detect_region("项目位于淮安市淮安区山阳街道黄土桥村")
    assert r["region"] == "淮安区"
    assert "淮安区" in r["evidence"]


def test_other_counties():
    assert detect_region("项目位于金湖县黎城街道")["region"] == "金湖县"
    assert detect_region("洪泽区某项目临时用地")["region"] == "洪泽区"
    assert detect_region("涟水县施工便道复垦")["region"] == "涟水县"
    assert detect_region("盱眙县某井场")["region"] == "盱眙县"


def test_non_region_words_rejected():
    assert detect_region("在表土堆放区和井场区施工")["region"] == ""
    assert detect_region("项目区面积 2.3 公顷，复垦区范围内无基本农田")["region"] == ""


def test_out_of_province_after_city():
    assert detect_region("宿州市埇桥区某项目")["region"] == "埇桥区"
