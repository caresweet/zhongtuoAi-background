#!/usr/bin/env python3
"""从人工成品 PDF 抽取「可照搬章节」→ 清洗 → 转 markdown → 落盘语料库。

**为什么需要它**：业主要求报告的第 1、2、4、5、7 章直接沿用参照件
《白马湖农场2号风电项目社会稳定风险评估报告（送审稿）》原文，只替换地区/项目信息。
参照件是 PDF，正文里混着页眉页脚、按行宽硬换行的断句、以及项目专属数字，
必须先离线清洗成「带占位符的章节 markdown」，运行时才能零 LLM 地做字面替换。

用法：
    cd backend
    python3 scripts/build_reference_corpus.py            # 用内置源
    python3 scripts/build_reference_corpus.py --review   # 额外导出人工校订表

产物：
    data/reference_reports/<ref_id>/meta.json       源信息、章号、占位符词典
    data/reference_reports/<ref_id>/chapters.json   各章 markdown（形状同 chapter_template_chunks.json）
    data/reference_reports/<ref_id>/_review_sheet.md  人工校订表（需人工确认后才可用）
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND)); sys.path.insert(0, str(BACKEND))

OUT_ROOT = BACKEND / "data" / "reference_reports"

# ═══════════════════════════════════════════════════════════════
# 源定义：参照件 PDF + 要照搬的章 + 项目专属词 → 占位符
# ═══════════════════════════════════════════════════════════════

SOURCES = {
    "baimahu_wind_2_shenshen_v1": {
        "pdf": "/Users/mac/Downloads/白马湖农场2号风电项目社会稳定风险评估报告（送审稿）.pdf",
        "title": "白马湖农场2号风电项目社会稳定风险评估报告（送审稿）",
        # 🔴 第4章暂不纳入：4.1 是风电参数、4.2.2 整段是风电话术（见 chapter_reference_agent 说明）。
        #    语料仍抽取第4章（便于人工比对），只是不分派给它。
        "verbatim_chapters": [1, 2, 5, 7],
        # 🔴 按章指定来源：第1章编制说明用**知识库里的洪泽土地征收模板**
        #    （结构与体例完全对得上：「（一）稳评理由 → 1. 2. 3.」三块），
        #    其余章用白马湖参照件 PDF。
        "chapter_sources": {
            "1": {
                "kind": "docx",
                "path": "storage/templates/598da27a8ef34400b1dd8762d776808c.docx",
                "chapter_cn": "二",          # 该模板的「第二章 编制说明」
                "token_map": [],
                # 🔴 该模板的编制说明从「（一）稳评理由」直接开始，**没有开篇前言**。
                #    业主口径：① 前言可以加内容，但**总字数要与参照件第1章相当**
                #    （2号 848 字 / 洪泽 756 字）；② 加的内容不得与模板已有内容重复。
                #    故前言只保留**受托背景**这一层（其余留给 agent 补写），控制在 200 字内。
                "preamble": (
                    "根据中共中央办公厅、国务院办公厅《关于加强新形势下重大决策社会稳定"
                    "风险评估机制建设的意见》（中办发〔2021〕11号）及省政府办公厅"
                    "《关于加强新形势下重大决策社会稳定风险评估机制建设实施意见》"
                    "（苏办发〔2021〕15号）等文件要求，{{project_name}}属应当开展社会稳定"
                    "风险评估的重大决策事项。为保证社会稳定风险分析的独立、客观、公正和专业，"
                    "我公司受{{org_name}}委托，承担了本决策的社会稳定风险评估工作。"
                ),
            },
        },
        # 🔴 顺序敏感：长串必须排在短串前面，否则「白马湖农场2号风电项目」
        #    会先被「白马湖农场」吃掉，替换出四不像。
        "token_map": [
            # 项目名（含 PDF 抽字常见空格变体）
            ("白马湖农场2 号风电项目", "{{project_name}}"),
            ("白马湖农场2号风电项目", "{{project_name}}"),
            ("白马湖2 号风电项目", "{{project_name}}"),
            ("白马湖2号风电项目", "{{project_name}}"),
            ("江苏省淮安市淮安区白马湖农场", "{{location}}"),
            ("江苏省白马湖农场有限公司", "{{support_orgs}}"),
            ("淮安名通新能源科技有限公司", "{{org_name}}"),
            ("淮安区委政法委", "{{support_orgs}}"),
            ("淮安市淮安区", "{{region}}"),
            ("白马湖农场", "{{site_name}}"),
            ("第七管理区", "{{site_units}}"),
            ("第八管理区", "{{site_units}}"),
            # 项目专属数值（风电参数，土地征收项目无对应值 → 运行时整句降级）
            ("31.25MW", "{{capacity_mw}}"),
            ("6.25 兆瓦", "{{unit_capacity}}"),
            ("6.25兆瓦", "{{unit_capacity}}"),
            ("17437.96 万元", "{{investment}}"),
            ("17437.96万元", "{{investment}}"),
            ("0.79 公顷", "{{area_ha}}"),
            ("0.79公顷", "{{area_ha}}"),
            ("220kV", "{{voltage}}"),
            ("160m", "{{hub_height}}"),
            ("220m", "{{rotor_diameter}}"),
            ("5 台", "{{turbine_count}}"),
            ("5台", "{{turbine_count}}"),
            ("12 个月", "{{construction_period}}"),
            ("12个月", "{{construction_period}}"),
        ],
        # 🔴 项目类型专属内容：参照件是**风电**项目，本项目是**土地征收**。
        #    这些条目照搬到征收项目里就是张冠李戴，运行时按关键词整条剔除。
        #    按「条目」粒度过滤（一行一条法条/文件名），删掉后不影响上下文通顺。
        "project_type_deny": [
            "风电", "风能", "风机", "机位", "升压站", "驭风",
            "可再生能源", "能源局", "能源体系", "陆上风电",
            "电力法", "气象法", "特种设备", "石油天然气管道",
            "噪声", "光影", "鸟类",
        ],
        # 🔴 去个人化改写：参照件「5.4 组织领导」的成员名单是**别家单位**的
        #    真人姓名 + 7 个手机号（淮安市名城控股集团 / 名通新能源的一批人）。
        #    照搬进本项目成稿＝泄露他人个人信息，且对本项目毫无意义。
        #    在**语料阶段**就改写成模板惯用的 {{占位符}} 表述 —— 不留空、不编造，
        #    运行期仍是零 LLM 字面替换。
        "rewrites": [
            (r'，成员名单如下：\s*组长：[^\n]{0,900}?(?=领导小组负责)',
             '，由{{org_name}}牵头，{{support_orgs}}等为成员单位。'),
            # 🔴 第7章「成立稳评小组」那份名单同理：是**参照件项目组**的人（真实姓名），
            #    照搬即张冠李戴。换成系统标记，运行时按**界面勾选的人员**渲染。
            (r'小组名单如下：\s*稳评负责人：[\s\S]{0,400}?(?=2\.制定评估方案)',
             '小组名单如下：\n\n[SYS_ROSTER]\n\n'),
        ],
    },
}

# 章号（阿拉伯）↔ 中文
_CN = "一二三四五六七八九十"


def _cn_num(s: str):
    s = s.strip()
    if not s or any(c not in _CN for c in s):
        return None
    m = {c: i + 1 for i, c in enumerate(_CN)}
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + m.get(s[1], 0)
    if len(s) == 1:
        return m.get(s, 0)
    return None


# ═══════════════════════════════════════════════════════════════
# ① 抽取
# ═══════════════════════════════════════════════════════════════

def extract_docx_paragraphs(path: str):
    """从 docx 取段落文本（保留层级判据：加粗/缩进由文本形态体现）。"""
    from docx import Document
    d = Document(path)
    return [p.text.strip() for p in d.paragraphs if p.text and p.text.strip()]


def slice_docx_chapter(path: str, cn_num: str):
    """按「第X章」切出 docx 的某一章（含标题行之后、下一章之前）。"""
    import re as _re
    paras = extract_docx_paragraphs(path)
    nxt = {"一": "二", "二": "三", "三": "四", "四": "五", "五": "六",
           "六": "七", "七": "八", "八": "九", "九": "十", "十": "十一"}
    a = b = None
    for i, t in enumerate(paras):
        if a is None and _re.match(rf'^第\s*{cn_num}\s*章', t):
            a = i + 1          # 跳过章标题行本身
        elif a is not None and _re.match(rf'^第\s*{nxt.get(cn_num, "")}\s*章', t):
            b = i
            break
    if a is None:
        return []
    return paras[a:b]


def paras_to_md_plain(paras) -> str:
    """docx 段落 → markdown。

    🔴 编制说明这类章的层级是「（一）稳评理由 → 1. xxx」，没有 X.Y 编号；
    但「（一）（二）（三）」在体例上**就是二级标题**（与其它章的 1.1/2.1 同级），
    因此输出为 `### （一）xxx`，由组装器渲染成 Heading 2 —— 这样目录层级才一致，
    且下游的重编号逻辑（按标题重置序号）也能正确识别分块边界。
    """
    import re as _re
    out = []
    for t in paras:
        if _re.match(r'^[（(][一二三四五六七八九十]+[）)]\s*\S', t) and len(t) <= 30:
            out.append(f"### {t}")
        else:
            out.append(t)
    return "\n\n".join(out).strip()


def extract_pages(pdf_path: str):
    """逐页取「行」列表（保留页边界，供页眉页脚判定）。"""
    import fitz
    doc = fitz.open(pdf_path)
    pages = []
    for i in range(len(doc)):
        lines = []
        for b in doc[i].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = "".join(s["text"] for s in l["spans"]).strip()
                if not t:
                    continue
                size = round(l["spans"][0]["size"], 1) if l["spans"] else 0
                lines.append({"text": t, "size": size})
        pages.append(lines)
    doc.close()
    return pages


# ═══════════════════════════════════════════════════════════════
# ② 清洗
# ═══════════════════════════════════════════════════════════════

def detect_running_lines(pages, ratio: float = 0.5):
    """找出「在 ≥ratio 比例的页面上出现」的行 → 判为页眉/页脚。

    🔴 保守原则：**只删跨页重复的行**，绝不按「含项目名」删 ——
    正文里真正提到项目名的句子必须留下（这是照搬章最容易被删坏的地方）。
    """
    from collections import Counter
    total = len(pages)
    if total < 4:
        return set()
    c = Counter()
    for lines in pages:
        for i, l in enumerate(lines):
            # 只看每页首 3 行 / 末 3 行，避免把正文里重复的句子误判
            if i < 3 or i >= len(lines) - 3:
                c[re.sub(r'\s+', '', l["text"])] += 1
    thresh = max(2, int(total * ratio))
    return {t for t, n in c.items() if n >= thresh}


def clean_lines(pages):
    """去页眉页脚 + 去纯页码 + 去目录行，返回各页剩余行。"""
    running = detect_running_lines(pages)
    out = []
    for lines in pages:
        buf = []
        for l in lines:
            t = l["text"]
            key = re.sub(r'\s+', '', t)
            if key in running:
                continue
            if re.fullmatch(r'\d{1,4}', t):     # 裸页码
                continue
            # 🔴 目录行：带点前导符 + 末尾页码（「2.1 国家法律法规和文件......3」）。
            #    不剔除的话会把整段目录当成章节正文搬到报告里。
            if re.search(r'[.．·]{3,}\s*\d*\s*$', t):
                continue
            buf.append(l)
        out.append(buf)
    return out


def to_paragraphs(pages) -> list:
    """把「按行宽硬换行」的 PDF 文本合并回段落。

    规则：下一行若以「编号/标题/图注/表名」开头，或上一行以句末标点结尾 → 另起一段；
    否则视为同一段被折断，直接拼接。
    """
    _NEW = re.compile(
        r'^(?:'
        r'\d+\.\d+(?:\.\d+)?\s'            # 1.1 / 1.1.1 标题
        r'|第[一二三四五六七八九十]+[章节]'   # 第X章
        r'|[（(][一二三四五六七八九十\d]+[）)]'  # （一）
        r'|\d+[.、]\s*\S'                  # 1. xxx
        r'|图\s*\d+\s*[-－]\s*\d+'          # 图7-1
        r'|表\s*\d+\s*[-－]\s*\d+'          # 表4-1
        r')'
    )
    _END = re.compile(r'[。！？；：”"』）)]$')

    paras, cur = [], ""
    for lines in pages:
        for l in lines:
            t = l["text"]
            if _NEW.match(t):
                if cur:
                    paras.append(cur); cur = ""
                # ① 章标题 / 小节标题 / 图注表名：**独立成段**，且必须立刻落盘 ——
                #    章标题若进了缓冲区，会被后面的正文行续接（「第一章编制说明根据中共
                #    中央办公厅…」），split_chapters 再把整段当章标题丢掉，整章开头就没了。
                # ② 编号条目（「1. xxx」）：允许续行（PDF 硬换行会把条目截断）。
                #    但**短且无句末标点**的条目是小标题（「1.客观公正性原则」），仍独立成段。
                _is_chapter = bool(re.match(r'^第[一二三四五六七八九十]+\s*章', t))
                _is_section = bool(re.match(r'^\d+\.\d+(?:\.\d+)?\s', t))
                _looks_subhead = (len(t) <= 20 and not _END.search(t))
                if _is_chapter or _is_section or _looks_subhead:
                    paras.append(t)
                else:
                    cur = t
                continue
            if not cur:
                cur = t
            elif _END.search(cur):
                paras.append(cur); cur = t
            else:
                cur += t
    if cur:
        paras.append(cur)
    return paras


# ═══════════════════════════════════════════════════════════════
# ③ 分章 + 转 markdown
# ═══════════════════════════════════════════════════════════════

def split_chapters(paras) -> dict:
    """按「第X章」切成 {章号: [段落]}。"""
    out, cur = {}, None
    for p in paras:
        m = re.match(r'^第([一二三四五六七八九十]+)\s*章', p.strip())
        if m:
            n = _cn_num(m.group(1))
            if n:
                cur = n
                out.setdefault(cur, [])
                continue
        if cur is not None:
            out[cur].append(p)
    return out


def paras_to_markdown(paras) -> str:
    """段落列表 → markdown（X.Y → ## 、X.Y.Z → ### ）。"""
    # 🔴 先修「标题被 PDF 断行切开」：`5.1 总` + `则` → `5.1 总则`。
    #    PDF 按行宽硬换行，短标题常被切成两行。
    merged = []
    i = 0
    while i < len(paras):
        cur = paras[i]
        m = re.match(r'^(\d+(?:\.\d+){1,3})\s+(\S{1,4})$', cur.strip())
        if m and i + 1 < len(paras):
            nxt = paras[i + 1].strip()
            # 下一行不是新标题/编号，且很短 → 判定为本标题的续行
            if (nxt and len(nxt) <= 6
                    and not re.match(r'^(\d+\.\d+|[（(]\d|第[一二三四五六七八九十]+[章节])', nxt)):
                cur = f"{cur.strip()}{nxt}"
                i += 2
                merged.append(cur)
                continue
        merged.append(cur)
        i += 1

    lines = []
    for p in merged:
        m = re.match(r'^(\d+(?:\.\d+){1,3})\s*(.+)$', p)
        if m:
            depth = m.group(1).count('.')
            level = '##' if depth == 1 else '###'
            lines.append(f"{level} {m.group(1)} {m.group(2).strip()}")
        else:
            lines.append(p)
    return "\n\n".join(lines).strip()


# ═══════════════════════════════════════════════════════════════
# ④ 占位符替换
# ═══════════════════════════════════════════════════════════════

def apply_token_map(text: str, token_map) -> tuple:
    """把项目专属词换成 {{占位符}}。长串优先，避免被短串吃掉。"""
    hits = {}
    for src, ph in sorted(token_map, key=lambda x: -len(x[0])):
        if src in text:
            hits[ph] = hits.get(ph, 0) + text.count(src)
            text = text.replace(src, ph)
    return text, hits


def apply_rewrites(text: str, rewrites) -> tuple:
    """按 (正则, 替换串) 改写语料。用于**去个人化**等整段重写。

    🔴 去个人化必须在**语料阶段**做完：参照件里他人姓名/手机号一旦留在语料里，
       就会被照搬进本项目成稿。改写成模板惯用的 `{{占位符}}` 表述，
       运行时仍是零 LLM 字面替换。
    """
    n = 0
    for pat, rep in rewrites or []:
        text, k = re.subn(pat, rep, text)
        n += k
    return text, n


# ═══════════════════════════════════════════════════════════════
# ④b 插图抽取（图5-1 流程图 这类，模型写不出来，只能从参照件原样抽）
# ═══════════════════════════════════════════════════════════════

_FIG_CAP = re.compile(r'^图\s*(\d+)\s*[-—－]\s*(\d+)\s*(.*)$')
FIG_ROOT = BACKEND / "storage" / "reference_reports"


def _figure_png(doc, page) -> tuple:
    """把「页面上显示的那张插图」渲染成 PNG（铺白底、裁掉四周留白）。

    🔴 不能用 `doc.extract_image(xref)` 直接取流：**软掩码（smask）会丢**，
       透明区变成纯黑 —— 第7章那张「社会稳定风险评估流程图」整片黑底就是这么来的。
       改成按图片在页面上的 bbox **渲染页面区域**，所见即所得，掩码自然生效。
    """
    import io
    from PIL import Image

    infos = page.get_image_info() or []
    if not infos:
        return None, ""
    # 取面积最大的那张（流程图这类插图永远是页面上最大的元素）
    best = max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
    import fitz
    pix = page.get_pixmap(clip=fitz.Rect(best["bbox"]), dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    # 裁掉四周留白（流程图四边都是空白，不裁的话整张图会被撑成近正方形、显得尺寸不对）
    import numpy as np
    a = np.array(img.convert("L"))
    rows = np.where((a < 245).sum(axis=1) > 2)[0]
    cols = np.where((a < 245).sum(axis=0) > 2)[0]
    if len(rows) and len(cols):
        m = max(4, int(min(img.size) * 0.01))
        img = img.crop((max(0, int(cols[0]) - m), max(0, int(rows[0]) - m),
                        min(img.width, int(cols[-1]) + m), min(img.height, int(rows[-1]) + m)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), "png"


def splice_figures(pdf_path: str, paras: list, ref_id: str, chapter_no: int) -> tuple:
    """把「图N-M 图注」段落换成 `![图注](figures/figN-M.png)`，图片从参照件 PDF 抽出。

    🔴 照搬章原本**只剩图题、没有图**（第5章「图5-1 社会稳定风险处理流程图」、
       第7章「图7-1 社会稳定风险评估流程图」都是流程图）。流程图模型画不出来，
       业主口径「依据模板来」→ 从参照件里原样抽出来用。

    图注在 PDF 抽字时常被拆成两段（「图5-1」+「社会稳定风险处理流程图」），这里一并并回。
    """
    import fitz

    out_dir = FIG_ROOT / ref_id / "figures"
    doc, out, i, n_fig = None, [], 0, 0
    while i < len(paras):
        m = _FIG_CAP.match(str(paras[i]).strip())
        if not m:
            out.append(paras[i]); i += 1
            continue
        no, idx, cap = m.group(1), m.group(2), (m.group(3) or "").strip()
        # 图注被拆成下一段（短、且本身不是图注）→ 并回来
        if not cap and i + 1 < len(paras):
            _nxt = str(paras[i + 1]).strip()
            if 0 < len(_nxt) <= 30 and not _FIG_CAP.match(_nxt):
                cap = _nxt; i += 1
        tag = f"fig{no}-{idx}"
        rel = None
        try:
            if doc is None:
                doc = fitz.open(pdf_path)
            page = next((p for p in doc if f"图{no}-{idx}" in p.get_text()), None)
            if page is not None:
                data, ext = _figure_png(doc, page)
                if data and len(data) > 3000:      # 太小的多半是印章/线条，不是插图
                    out_dir.mkdir(parents=True, exist_ok=True)
                    fp = out_dir / f"{tag}.{ext}"
                    fp.write_bytes(data)
                    rel = f"figures/{fp.name}"
        except Exception as e:
            print(f"   ⚠️ 抽图 {tag} 失败: {e}")
        if rel:
            out.append(f"![{cap}]({rel})")
            n_fig += 1
        else:
            print(f"   ⚠️ 第{chapter_no}章 {tag} 未抽到图，保留图题")
            out.append(f"图{no}-{idx} {cap}".strip())
        i += 1
    if doc is not None:
        doc.close()
    return out, n_fig


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def build(ref_id: str, src: dict, write_review: bool = False):
    pdf = src["pdf"]
    if not os.path.exists(pdf):
        print(f"❌ 找不到参照件: {pdf}"); return
    print(f"📖 读取 {Path(pdf).name}")
    pages = extract_pages(pdf)
    print(f"   {len(pages)} 页")

    running = detect_running_lines(pages)
    print(f"🏷️  识别为页眉/页脚（跨页重复，共 {len(running)} 条）:")
    for r in sorted(running)[:6]:
        print(f"     · {r[:60]}")

    cleaned = clean_lines(pages)
    paras = to_paragraphs(cleaned)
    chapters = split_chapters(paras)
    print(f"📚 切出 {len(chapters)} 章: {sorted(chapters)}")

    out_dir = OUT_ROOT / ref_id
    out_dir.mkdir(parents=True, exist_ok=True)

    out_chapters, all_hits = {}, {}
    _cs = src.get("chapter_sources") or {}
    for n in src["verbatim_chapters"]:
        _spec = _cs.get(str(n))
        if _spec and _spec.get("kind") == "docx":
            _paras = slice_docx_chapter(_spec["path"], _spec.get("chapter_cn", ""))
            if not _paras:
                print(f"   ⚠️ 第{n}章在 docx 源里未抽到"); continue
            md = paras_to_md_plain(_paras)
            _pre = (_spec.get("preamble") or "").strip()
            if _pre:
                md = _pre + "\n\n" + md
            md, hits = apply_token_map(md, _spec.get("token_map") or [])
            md, _rw = apply_rewrites(md, _spec.get("rewrites") or src.get("rewrites") or [])
            if _rw:
                print(f"   🧹 第{n}章去个人化改写 {_rw} 处")
            out_chapters[str(n)] = {
                "title": f"第{'一二三四五六七八九十'[n-1]}章",
                "text": md, "chars": len(re.sub(r'\s', '', md)),
                "placeholders": hits, "source_kind": "docx",
            }
            for k, v in hits.items(): all_hits[k] = all_hits.get(k, 0) + v
            print(f"   ✅ 第{n}章(docx源): {len(re.sub(chr(92)+'s','',md))} 字")
            continue
        if n not in chapters:
            print(f"   ⚠️ 第{n}章未抽到"); continue
        _paras, _nfig = splice_figures(pdf, chapters[n], ref_id, n)
        if _nfig:
            print(f"   🖼️ 第{n}章抽出 {_nfig} 张插图")
        md, hits = apply_token_map(paras_to_markdown(_paras), src["token_map"])
        md, _rw = apply_rewrites(md, src.get("rewrites") or [])
        if _rw:
            print(f"   🧹 第{n}章去个人化改写 {_rw} 处")
        out_chapters[str(n)] = {
            "title": f"第{'一二三四五六七八九十'[n-1]}章",
            "text": md,
            "chars": len(re.sub(r'\s', '', md)),
            "placeholders": hits,
        }
        for k, v in hits.items():
            all_hits[k] = all_hits.get(k, 0) + v
        print(f"   ✅ 第{n}章: {len(re.sub(chr(92)+'s','',md))} 字，占位符 {sum(hits.values())} 处")

    (out_dir / "chapters.json").write_text(json.dumps(
        {"source": src["title"], "ref_id": ref_id, "chapters": out_chapters},
        ensure_ascii=False, indent=1), encoding="utf-8")

    (out_dir / "meta.json").write_text(json.dumps({
        "ref_id": ref_id,
        "source_pdf": pdf,
        "source_title": src["title"],
        "page_count": len(pages),
        "verbatim_chapters": src["verbatim_chapters"],
        "running_lines_removed": sorted(running),
        "placeholder_values": {k[2:-2]: v for k, v in {
            "{{project_name}}": "项目名称（取事实卡）",
            "{{org_name}}": "稳评责任单位/委托方",
            "{{support_orgs}}": "协助单位/成员单位",
            "{{location}}": "项目位置",
            "{{region}}": "行政区划",
            "{{site_name}}": "所在乡镇/农场",
            "{{site_units}}": "涉及的村组/管理区",
            "{{capacity_mw}}": "装机容量（风电专属，征收项目无）",
            "{{unit_capacity}}": "单机容量（风电专属，征收项目无）",
            "{{turbine_count}}": "机组台数（风电专属，征收项目无）",
            "{{hub_height}}": "轮毂高度（风电专属，征收项目无）",
            "{{rotor_diameter}}": "叶轮直径（风电专属，征收项目无）",
            "{{voltage}}": "升压站电压（风电专属，征收项目无）",
            "{{investment}}": "投资额",
            "{{area_ha}}": "占地面积",
            "{{construction_period}}": "建设工期",
        }.items()},
        "project_type_deny": src.get("project_type_deny", []),
        "placeholders": all_hits,
        "reviewed": False,   # 🔴 人工校订后置 true 才允许上线
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n💾 已写入 {out_dir}")
    print(f"   占位符使用统计: {all_hits}")

    if write_review:
        rows = ["# 参照件语料人工校订表", "",
                f"来源：{src['title']}", f"照搬章：{src['verbatim_chapters']}", "",
                "> 校订通过后把 `meta.json` 的 `reviewed` 改为 true。", ""]
        for n, ch in out_chapters.items():
            rows += [f"## 第{n}章（{ch['chars']} 字）", "",
                     f"占位符：{ch['placeholders']}", "", "```markdown",
                     ch["text"][:2500], "```", ""]
        (out_dir / "_review_sheet.md").write_text("\n".join(rows), encoding="utf-8")
        print(f"   📝 校订表 → {out_dir}/_review_sheet.md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="baimahu_wind_2_shenshen_v1")
    ap.add_argument("--review", action="store_true", help="额外导出人工校订表")
    args = ap.parse_args()
    src = SOURCES.get(args.ref)
    if not src:
        print(f"❌ 未知参照件: {args.ref}"); return
    build(args.ref, src, write_review=args.review)


if __name__ == "__main__":
    main()
