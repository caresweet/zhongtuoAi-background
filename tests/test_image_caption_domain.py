"""正文配图「章→图规格 / 图注 / 图片→章」按域取：稳评逐字不变；复垦走真规格。

复垦的章→图规格 `RECLAMATION_IMAGE_SPECS` 由业主 5 份真实复垦方案逐图提取
（ch1 1/5、ch3 5/5、ch4 5/5、ch5 1/5；ch2/6/7/8/9/10 无图）。绑定按该规格的
关键词判定，图注取规格自己的图名，绝不复用稳评器物名。
"""
import os
import tempfile

import pytest

import app.agent.chapter_catalog as catalog
from app.services.image_catalog import build_image_catalog

_JPEG = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffd9')


def _img_in(folder: str, filename: str = "a.jpg") -> str:
    """在临时目录的 `folder/` 下放一张最小 JPEG，返回其路径。

    内容带上文件夹名 —— 让不同文件夹的图**指纹不同**，避免被内容去重干掉。
    """
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, folder)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, filename)
    with open(p, "wb") as f:
        f.write(_JPEG + folder.encode("utf-8"))
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


def test_reclamation_location_map_binds_to_chapter3():
    """复垦：位置示意图 → 第3章「项目概况」（不是稳评口径的第6章），图注取规格图名。"""
    p = _img_in("项目地理位置示意图")
    cat = build_image_catalog([p], domain="reclamation")
    labels = [i["label"] for i in cat["by_chapter"].get(3, [])]
    assert labels == ["项目地理位置示意图"]
    assert "社会稳定风险评估" not in labels[0]
    assert not cat["by_chapter"].get(6)


def test_reclamation_measure_layout_beats_generic_location():
    """『表土堆放区位置示意图』归第4章「措施类」，不被第3章泛名『位置示意图』抢走。"""
    p = _img_in("表土堆放区位置示意图")
    cat = build_image_catalog([p], domain="reclamation")
    assert [i["label"] for i in cat["by_chapter"].get(4, [])] == ["表土堆放区位置示意图"]
    assert not cat["by_chapter"].get(3)


def test_reclamation_ch4_three_kinds_bind():
    """第4章三类图各按关键词落到自己的图名：损毁分析 / 占用情况。"""
    p1 = _img_in("临时用地现场照片")
    p2 = _img_in("临时用地占用生态红线示意图")
    cat = build_image_catalog([p1, p2], domain="reclamation")
    ch4 = [i["label"] for i in cat["by_chapter"].get(4, [])]
    assert "临时用地现场照片" in ch4
    assert "临时用地占用永久基本农田示意图" in ch4


def test_reclamation_weak_evidence_chapters_bind():
    """第1章、第5章的弱证据图（各 1/5 样本）也能按名绑定到本域章。"""
    c1 = build_image_catalog([_img_in("各类土地面积关系图")], domain="reclamation")
    c5 = build_image_catalog([_img_in("临时用地硬化层断面示意图")], domain="reclamation")
    assert [i["label"] for i in c1["by_chapter"].get(1, [])] == ["各类土地面积关系图"]
    assert [i["label"] for i in c5["by_chapter"].get(5, [])] == ["临时用地硬化层断面示意图"]


def test_reclamation_never_gets_stability_caption():
    """复垦：只有稳评口径的图（评估流程图）不绑任何正文章，也绝无稳评图注。"""
    p = _img_in("评估流程")
    stab = build_image_catalog([p], domain="stability")
    assert [i["label"] for i in stab["by_chapter"].get(7, [])] == ["社会稳定风险评估流程图"]

    rec = build_image_catalog([p], domain="reclamation")
    rec_labels = [i["label"] for imgs in rec["by_chapter"].values() for i in imgs]
    assert all("社会稳定风险评估" not in lb for lb in rec_labels)


def test_reclamation_caption_formula_fallback():
    """未命中规格时，图注退回「章标题 + 示意图」的中性兜底（直呼 image_caption）。"""
    assert catalog.chapter_title("reclamation", 3) == "项目概况"
    assert catalog.image_caption("reclamation", 3, "map") == "项目概况示意图"
    # 命中规格时用规格图名，而非泛化的章标题
    assert catalog.image_caption("reclamation", 3, "location_map",
                                 spec_label="项目地理位置示意图") == "项目地理位置示意图"


def test_chapter_image_specs_reclamation_from_samples():
    """复垦配图规格：真表（非中性兜底），据样本落地的章号、图名与关键词。"""
    rec = catalog.chapter_image_specs("reclamation")
    # 只有样本支持的章（1/3/4/5）有条目；其余章缺省＝无正文图
    assert set(rec) == {1, 3, 4, 5}
    for specs in rec.values():
        for s in specs:
            assert set(s) >= {"type", "label", "min", "max", "keywords"}
            assert s["min"] == 0 and s["max"] >= 1
            assert s["keywords"]
    # 第3章：位置图（正名 + 变体关键词）+ 用户上传的「现状图」
    loc = rec[3][0]
    assert loc["label"] == "项目地理位置示意图"
    assert "位置示意图" in loc["keywords"] and "工程位置" in loc["keywords"]
    assert any(s["label"] == "项目区土地利用现状图" and "现状图" in s["keywords"]
               for s in rec[3])
    # 第4章：三类图（损毁分析 / 占用情况 / 措施）
    labels4 = {s["label"] for s in rec[4]}
    assert {"临时用地现场照片", "临时用地占用永久基本农田示意图",
            "表土堆放区位置示意图", "临时用地位置关系图"} <= labels4
    # 🔴 界址/红线图认「勘测定界图」三个字，**不能只认「勘测定界」** ——
    #    否则 PDF 抽出来的「pdf_勘测定界报告_p3_img0.jpeg」会被当图印进正文
    _rel = next(s for s in rec[4] if s["label"] == "临时用地位置关系图")
    assert "勘测定界图" in _rel["keywords"] and "勘测定界" not in _rel["keywords"]
    # 稳评仍是原表（同一对象，逐字不变）
    assert set(catalog.chapter_image_specs("stability")) == {6, 7, 8, 9}
