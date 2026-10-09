"""正文配图图注按域取：稳评逐字不变；复垦绝不复用稳评图注。

复垦的「章 → 图片类型」规格是**业主尚未定的设计**，本仓不臆造 ——
故复垦图注一律取「该复垦章的标题 + 示意图」（域中立），而非稳评的器物名
（如「社会稳定风险评估流程图」「拟征收地块位置示意图」）。
"""
import os
import tempfile

import pytest

import app.agent.chapter_catalog as catalog
from app.services.image_catalog import build_image_catalog

_JPEG = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffd9')


def _img_in(folder: str, filename: str = "a.jpg") -> str:
    """在临时目录的 `folder/` 下放一张最小 JPEG，返回其路径。"""
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, folder)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, filename)
    with open(p, "wb") as f:
        f.write(_JPEG)
    return p


def test_default_domain_is_stability_byte_identical():
    """不传 domain 与显式 stability 逐字节相同。"""
    p = _img_in("位置图")
    assert build_image_catalog([p]) == build_image_catalog([p], domain="stability")


def test_stability_map_caption_unchanged():
    """稳评：位置图 → 第6章，图注仍是规格表原值「项目位置示意图」。"""
    p = _img_in("位置图")
    cat = build_image_catalog([p], domain="stability")
    assert [i["label"] for i in cat["by_chapter"].get(6, [])] == ["项目位置示意图"]


def test_stability_flowchart_caption_unchanged():
    """稳评：评估流程图 → 第7章，图注仍是「社会稳定风险评估流程图」。"""
    p = _img_in("评估流程")
    cat = build_image_catalog([p], domain="stability")
    assert [i["label"] for i in cat["by_chapter"].get(7, [])] == ["社会稳定风险评估流程图"]


def test_reclamation_caption_is_reclamation_chapter_title():
    """复垦：同两张图，图注改为**复垦章的标题 + 示意图**，绝无稳评器物名。"""
    p = _img_in("位置图")
    cat = build_image_catalog([p], domain="reclamation")
    labels = [i["label"] for i in cat["by_chapter"].get(6, [])]
    assert labels == ["土地复垦工程设计及工程量测算示意图"]
    assert "社会稳定风险评估" not in labels[0]


def test_reclamation_flowchart_never_gets_stability_caption():
    """复垦：稳评流程图那档类别，绝不再印「社会稳定风险评估流程图」。"""
    p = _img_in("评估流程")
    stab = build_image_catalog([p], domain="stability")
    assert [i["label"] for i in stab["by_chapter"].get(7, [])] == ["社会稳定风险评估流程图"]

    rec = build_image_catalog([p], domain="reclamation")
    rec_labels = [i["label"] for i in rec["by_chapter"].get(7, [])]
    assert rec_labels == ["土地复垦投资估算示意图"]
    assert "社会稳定风险评估流程图" not in rec_labels


def test_reclamation_caption_formula_chapter_title_plus_schematic():
    """图注公式 = 章标题 + 「示意图」（以第3章「项目概况」为例）。

    直呼 `chapter_catalog.image_caption`，不依赖图片能否绑定到该章。
    """
    assert catalog.chapter_title("reclamation", 3) == "项目概况"
    assert catalog.image_caption("reclamation", 3, "map") == "项目概况示意图"


def test_chapter_image_specs_empty_plan_for_reclamation():
    """复垦无「章→图类型」规格：不回落稳评那张表，且规格不限定类别。"""
    rec = catalog.chapter_image_specs("reclamation")
    assert set(rec) == set(catalog.all_chapters("reclamation"))
    for specs in rec.values():
        assert len(specs) == 1
        # 中性规格：不限定图片类别（不建立任何「类型↔章」对应）
        assert specs[0]["type"] not in {
            "map", "survey_map", "planning", "flowchart", "announcement",
            "photo", "meeting", "sentiment", "review", "opinion", "survey",
        }
    # 稳评仍是原表（同一对象，逐字不变）
    assert catalog.chapter_image_specs("stability") is not None
    assert set(catalog.chapter_image_specs("stability")) == {6, 7, 8, 9}
