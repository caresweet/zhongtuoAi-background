# -*- coding: utf-8 -*-
"""把模板成稿改造成「测试稿」：换一套自洽的虚构数据 + 图片全部占位。

业主口径（2026-11-20）：测试文档不能和给我们的资料一样，数据要另造，
照片不能还是模板原项目的，全部换成「图片占位」。

用法
----
    from app.services.test_doc_makeup import make_test_doc
    make_test_doc(region, src_docx, out_docx)

做的事
------
1. 项目身份（项目名/文号/责任单位/位置/镇村/日期）换成该地区的虚构测试值；
2. 项目规模数据（总面积、亩数、户数、问卷份数、风险总分、投资等）换成
   与虚构项目自洽的一套值；
3. 区县错配（如淮安区报告里出现「洪泽区财政统筹」）归到本项目所在区县；
4. word/media 里的照片全部替换为对应尺寸的「图片占位」图（小图标/logo 保留）。
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")
_DISTRICTS = ("淮安区", "淮阴区", "清江浦区", "洪泽区", "涟水县", "盱眙县", "金湖县")


# ═══════════════════════════════════════════════════════════════
# 每个地区的测试数据（刻意与用户资料不同）
# ═══════════════════════════════════════════════════════════════
TEST_DATA: Dict[str, Dict[str, Any]] = {
    "淮阴区": {
        "project": "淮阴区2027-03号地块土地征收决策",
        "old_location": "淮阴区马头镇吴城村",
        "unit": "淮安市淮阴区渔沟镇人民政府",
        "location": "淮阴区渔沟镇蒋桥村",
        "doc_ref": "淮拟征地告〔2027〕9号",
        "old": ["淮阴区2026-05号地块土地征收决策",
                "淮拟征地告〔2025〕第16号土地征收决策",
                "淮拟征地告〔2025〕第16号土地征收",
                "淮拟征地告〔2026〕17号",
                "淮安市淮阴区马头镇人民政府", "淮阴区刘老庄镇人民政府",
                "马头镇", "吴城村"],
        "area_m2": 64200, "area_mu": 96.3, "households": 118,
        "survey": 96, "score": "84.5", "district": "淮阴区",
    },
    "洪泽": {
        "project": "洪泽区2027-05号地块土地征收决策",
        "old_location": "洪泽区高良涧街道临河社区",
        "unit": "淮安市洪泽区三河镇人民政府",
        "location": "洪泽区三河镇四坝村",
        "doc_ref": "洪拟征告〔2027〕11号",
        "old": ["洞庭湖路（S350-宁连一级路段）工程土地征收决策",
                "洪泽区2026-02号地块土地征收决策",
                "洪拟征告〔2026〕128号土地征收决策稳定评估",
                "洪拟征告〔2026〕129号",
                "淮安市洪泽区高良涧街道办事处",
                "高良涧街道", "临河社区", "朱坝街道大魏社区"],
        "area_m2": 52800, "area_mu": 79.2, "households": 96,
        "survey": 88, "score": "86.0", "district": "洪泽区",
    },
    "金湖": {
        "project": "金湖县塔集镇工业储备地块土地征收决策",
        "old_location": "金湖县银涂镇涂沟村",
        "unit": "金湖县塔集镇人民政府",
        "location": "金湖县塔集镇闵桥村",
        "doc_ref": "金征预告〔2027〕4号",
        "old": ["根据用户提供资料填充",
                "金湖县银涂镇工业储备地块土地征收决策",
                "银涂工业储备地块项目",
                "金征预告〔2026〕2号（银涂工业储备地块项目）土地征收决策社会稳定风险评估报告20260505",
                "金征预告〔2026〕3号", "金征预告〔2026〕2号",
                "金湖县银涂镇人民政府", "银涂镇", "涂沟村"],
        "area_m2": 81500, "area_mu": 122.3, "households": 143,
        "survey": 110, "score": "82.5", "district": "金湖县",
    },
    "清江浦区": {
        "project": "清江浦区2027-02号地块土地征收决策",
        "old_location": "清江浦区黄码镇黄码村",
        "unit": "淮安市清江浦区和平镇人民政府",
        "location": "清江浦区和平镇唐桥村",
        "doc_ref": "清拟征告〔2027〕3号",
        "old": ["清拟征告〔2024〕35号土地征收决策",
                "清江浦区2026-03号地块土地征收决策",
                "清拟征告〔2026〕4号", "清拟征告〔2024〕35号",
                "淮安市清江浦区黄码镇人民政府", "黄码镇", "黄码村"],
        "area_m2": 48600, "area_mu": 72.9, "households": 88,
        "survey": 82, "score": "85.0", "district": "清江浦区",
    },
    "淮安区": {
        "project": "淮安区白马湖农场3号风电项目土地征收决策",
        "old_location": "淮安区范集镇白马湖农场",
        "unit": "淮安市淮安区范集镇人民政府",
        "location": "淮安区范集镇白马湖农场三队",
        "doc_ref": "淮安区拟征告〔2027〕5号",
        "old": ["淮安区白马湖农场2号风电项目土地征收决策",
                "淮安区拟征告〔2026〕6号",
                "江苏省白马湖农场有限公司"],
        "pairs": [("白马湖农场2号风电项目", "白马湖农场3号风电项目"),
                  ("白马湖2号风电项目", "白马湖3号风电项目"),
                  ("白马湖农场1号风电项目", "白马湖农场3号风电项目"),
                  ("白马湖1号风电项目", "白马湖3号风电项目"),
                  ("2.37亿元", "2.86亿元"),
                  ("8台6.25MW", "6台5.0MW")],
        "area_m2": 76200, "area_mu": 114.3, "households": 102,
        "survey": 96, "score": "83.5", "district": "淮安区",
    },
    "淮安市": {
        "project": "盐城市沿海新能源基地送电淮安±800千伏直流工程淮安段土地征收决策",
        "old_location": "淮安市境内，线路经过涟水县、淮安区",
        "unit": "淮安市人民政府",
        "location": "淮安市境内，线路经过盱眙县、洪泽区",
        "doc_ref": "淮自然资征〔2027〕6号",
        "old": ["内蒙古库布齐沙漠基地送电上海±800千伏直流工程淮安段",
                "内蒙古库布齐沙漠基地送电上海工程淮安段",
                "淮安市220千伏输变电工程土地征收决策（跨区县）",
                "内蒙古库布齐新能源基地", "库布齐沙漠基地",
                "淮自然资征〔2026〕8号"],
        "pairs": [("送电上海", "送电淮安"), ("库布齐", "盐城沿海"),
                  ("内蒙古", "江苏"), ("沙戈荒", "沿海滩涂"),
                  ("上海", "淮安"),
                  ("涟水县", "盱眙县"), ("淮安区", "洪泽区"),
                  ("64.3公里", "58.6公里"), ("632亿元", "6.8亿元"),
                  ("78000万元", "68000万元")],
        "area_m2": 124000, "area_mu": 186.0, "households": 210,
        "survey": 160, "score": "81.0", "district": "淮安市",
    },
}


def _pairs_for(region: str) -> List[Tuple[str, str]]:
    d = TEST_DATA.get(region)
    if not d:
        return []
    pairs: List[Tuple[str, str]] = []
    for old in d.get("old") or []:
        if "拟征" in old or "征告" in old or "征预告" in old:
            new = d["doc_ref"]
        elif re.search(r"(人民政府|办事处|有限公司)$", old):
            new = d["unit"]
        else:
            new = d["project"]
        pairs.append((old, new))
    pairs += list(d.get("pairs") or [])
    if d.get("old_location"):
        pairs.append((d["old_location"], d["location"]))
    return [(a, b) for a, b in pairs if a and b and a != b]


def _num_patterns(region: str) -> List[Tuple[re.Pattern, str]]:
    d = TEST_DATA.get(region)
    if not d:
        return []
    area_m2, area_mu = d["area_m2"], d["area_mu"]
    hh, survey, score = d["households"], d["survey"], d["score"]
    pats = [
        (re.compile(r"80000(?:\.0+)?\s*平方米"), f"{area_m2}平方米"),
        (re.compile(r"120(?:\.0+)?\s*亩"), f"{area_mu}亩"),
        (re.compile(r"(?<!\d)32\s*户"), f"{hh}户"),
        (re.compile(r"(?<!\d)(\d{2,4})\s*份"), f"{survey}份"),
        (re.compile(r"(扣分合计\s*(?:为|是)?\s*)0(?:\.0+)?(?=\s*分)"),
         r"\g<1>" + f"{100 - float(score):.1f}"),
        (re.compile(r"公众问卷涉及农户调查\s*\d+\s*户[^。]*。"),
         f"公众问卷涉及农户调查{hh}户，其中支持{hh}户，反对0户。"),
        (re.compile(r"(总分\s*(?:W\s*=\s*)?(?:为|是)?\s*)0(?:\.0+)?"),
         r"\g<1>" + score),
        (re.compile(r"0\.0+(?=\s*分)"), score),
    ]
    return pats


def _year_patterns() -> List[Tuple[re.Pattern, str]]:
    return [
        (re.compile(r"2026\s*年"), "2027年"),
        (re.compile(r"二〇二六年"), "二〇二七年"),
        (re.compile(r"〔2026〕"), "〔2027〕"),
    ]


def _district_patterns(region: str) -> List[Tuple[re.Pattern, str]]:
    d = TEST_DATA.get(region)
    if not d:
        return []
    own = d.get("district") or ""
    if not own or own == "淮安市":
        return []
    pats = []
    for dist in _DISTRICTS:
        if dist != own:
            pats.append((re.compile(re.escape(dist)), own))
    return pats


# ═══════════════════════════════════════════════════════════════
# 文本替换（段落 + 表格）
# ═══════════════════════════════════════════════════════════════
def _plain_sub(p, pairs: List[Tuple[str, str]]) -> int:
    old_text = p.text or ""
    new_text = old_text
    for a, b in pairs:
        if a and a in new_text:
            new_text = new_text.replace(a, b)
    if new_text == old_text:
        return 0
    if p.runs:
        for r in p.runs:
            if r.text:
                for a, b in pairs:
                    if a and a in r.text:
                        r.text = r.text.replace(a, b)
        if p.text != new_text:
            p.runs[0].text = new_text
            for r in p.runs[1:]:
                r.text = ""
    else:
        p.add_run(new_text)
    return 1


def _regex_sub_para(p, pats: List[Tuple[re.Pattern, str]]) -> int:
    old_text = p.text or ""
    if not old_text:
        return 0
    new_text = old_text
    for pat, repl in pats:
        new_text = pat.sub(repl, new_text)
    if new_text == old_text:
        return 0
    if p.runs:
        for r in p.runs:
            if r.text:
                for pat, repl in pats:
                    r.text = pat.sub(repl, r.text)
        if p.text != new_text:
            p.runs[0].text = new_text
            for r in p.runs[1:]:
                r.text = ""
    else:
        p.add_run(new_text)
    return 1


def _iter_paras(doc):
    """正文、表格、页眉页脚、文本框里的段落都算（页眉常写旧项目名）。"""
    seen = set()        # 存 lxml 元素本身（按 id() 去重会被回收后的新对象重用）

    def _walk(container):
        for p in container.paragraphs:
            if p._p not in seen:
                seen.add(p._p)
                yield p
        for t in container.tables:
            for row in t.rows:
                for c in row.cells:
                    for p in c.paragraphs:
                        if p._p not in seen:
                            seen.add(p._p)
                            yield p

    yield from _walk(doc)
    for sec in doc.sections:
        for hf in (sec.header, sec.footer,
                   sec.first_page_header, sec.first_page_footer,
                   sec.even_page_header, sec.even_page_footer):
            try:
                if hf.is_linked_to_previous:
                    continue
            except Exception:
                continue
            yield from _walk(hf)
    # 文本框（w:txbxContent）不在 doc.paragraphs 里
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    for txbx in doc.element.body.iter(qn('w:txbxContent')):
        for p_el in txbx.iter(qn('w:p')):
            if p_el in seen:
                continue
            seen.add(p_el)
            yield Paragraph(p_el, doc)


def apply_test_facts(doc, region: str) -> int:
    """把文档里的旧事实换成该地区的测试数据；返回改动段落数。"""
    pairs = sorted(_pairs_for(region), key=lambda x: -len(x[0]))
    pats = _district_patterns(region) + _num_patterns(region) + _year_patterns()
    n = 0
    for p in _iter_paras(doc):
        n += _plain_sub(p, pairs)
        n += _regex_sub_para(p, pats)
    return n


# ═══════════════════════════════════════════════════════════════
# 图片占位
# ═══════════════════════════════════════════════════════════════
def _load_font(size: int):
    from PIL import ImageFont
    for path in ("/System/Library/Fonts/Supplemental/Songti.ttc",
                 "/System/Library/Fonts/STHeiti Medium.ttc",
                 "/System/Library/Fonts/PingFang.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _placeholder_image(w: int, h: int, idx: int):
    from PIL import Image, ImageDraw
    # 控制像素总量，保持原图长宽比
    max_side = 1400
    if max(w, h) > max_side:
        k = max_side / float(max(w, h))
        w, h = max(1, int(w * k)), max(1, int(h * k))
    img = Image.new("RGB", (w, h), (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(170, 170, 170),
                width=max(2, min(w, h) // 180))
    text = f"图 片 占 位 {idx}"
    font = _load_font(max(16, min(w, h) // 9))
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if tw < w - 20 and th < h - 20:
        d.text(((w - tw) / 2 - bbox[0], (h - th) / 2 - bbox[1]),
               text, fill=(120, 120, 120), font=font)
    return img


def replace_images_with_placeholders(path: str | Path,
                                     min_side: int = 160) -> int:
    """word/media 里大于 min_side 的图片全部换成占位图；返回替换张数。

    小图（logo、印章、图标）保留，避免把单位标识也抹掉。
    """
    from PIL import Image
    path = Path(path)
    tmp = path.with_name(path.name + ".tmpzip")
    n = idx = 0
    with zipfile.ZipFile(path) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            ext = os.path.splitext(item.filename)[1].lower()
            if item.filename.startswith("word/media/") and ext in _IMG_EXT:
                try:
                    im = Image.open(io.BytesIO(data))
                    w, h = im.size
                except Exception:
                    w = h = 0
                if w and h and min(w, h) >= min_side:
                    idx += 1
                    ph = _placeholder_image(w, h, idx)
                    buf = io.BytesIO()
                    if ext in (".jpg", ".jpeg"):
                        ph.save(buf, format="JPEG", quality=85)
                    elif ext in (".tif", ".tiff"):
                        ph.save(buf, format="TIFF")
                    elif ext == ".gif":
                        ph.save(buf, format="GIF")
                    elif ext == ".bmp":
                        ph.save(buf, format="BMP")
                    else:
                        ph.save(buf, format="PNG")
                    data = buf.getvalue()
                    n += 1
            zout.writestr(item, data)
    tmp.replace(path)
    logger.info(f"[测试稿] 图片占位 {n} 张：{path.name}")
    return n


def make_test_doc(region: str, src_docx: str | Path,
                  out_docx: str | Path) -> Dict[str, Any]:
    """复制成稿 → 换测试数据 → 图片占位。返回统计信息。"""
    import shutil
    from docx import Document

    src, out = Path(src_docx), Path(out_docx)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)
    doc = Document(str(out))
    n = apply_test_facts(doc, region)
    d = TEST_DATA.get(region) or {}
    if d:
        # 文件属性也换掉（模板作者名/旧标题会带出去）
        cp = doc.core_properties
        cp.title = d.get("project", "")
        cp.subject = "社会稳定风险评估"
        cp.author = "江苏众拓项目代理咨询有限公司"
        cp.last_modified_by = "江苏众拓项目代理咨询有限公司"
        cp.comments = ""
        cp.keywords = ""
        cp.category = ""
    doc.save(str(out))
    imgs = replace_images_with_placeholders(out)
    return {"paragraphs_changed": n, "images_placeholder": imgs,
            "region": region, "out": str(out)}
