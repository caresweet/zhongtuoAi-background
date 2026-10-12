# -*- coding: utf-8 -*-
"""按域取「章目录」—— 下游（report_workflow / report_assembler /
master_orchestrator）按域取章集合/章标题/章类型/章结构文本的唯一收口点。

稳评（stability）走 `app.agent.chapter_definitions`（14 章），行为与历史逐字一致；
复垦（reclamation）走 `app.agent.reclamation_definitions`（10 章）。
两套定义同构，差异只在这里收口，避免调用点里长出一串 `if domain == ...` 分支。

除「章集合/标题/类型」（report_workflow 用）外，这里还收口了：
  · `chapter_titles` / `has_review_table` / `appendix_chapter_no` /
    `front_matter_labels`（装配器用）
  · `target_words` / `sections_for_prompt` / `table_format_reference` /
    `key_labels` / `report_persona` / `has_scoring_guidance` /
    `has_chapter_specific_requirements`（单章提示词用）
  · `material_heading` / `template_reference_for_prompt` /
    `custom_prompt_persona` / `writing_guide`（默认单章 user prompt 与 act() 公共下游用）

🔴 默认域（stability）的取值逐字等于改动前的实现 —— 这是「稳评行为不变」的依据。
   故上列多数函数在稳评分支**直接委托**给既有实现，不复制第二份。
"""

import re
from typing import Any, Dict, List, Optional

_RECLAMATION = "reclamation"


def all_chapters(domain: str | None) -> List[int]:
    """该域的章号列表（按顺序）。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_ALL_CHAPTERS
        return list(RECLAMATION_ALL_CHAPTERS)
    from app.agent.chapter_definitions import ALL_CHAPTERS
    return list(ALL_CHAPTERS)


def total_chapters(domain: str | None) -> int:
    """该域的总章数。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_TOTAL_CHAPTERS
        return RECLAMATION_TOTAL_CHAPTERS
    from app.agent.chapter_definitions import TOTAL_CHAPTERS
    return TOTAL_CHAPTERS


def chapter_title(domain: str | None, chapter_number: int) -> str:
    """章标题。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import get_reclamation_chapter_title
        return get_reclamation_chapter_title(chapter_number)
    from app.agent.chapter_definitions import get_chapter_title
    return get_chapter_title(chapter_number)


def chapter_titles(domain: str | None) -> Dict[int, str]:
    """该域的全量 {章号: 标题} 映射。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        return {n: d.get("title", f"第{n}章")
                for n, d in RECLAMATION_CHAPTER_DEFINITIONS.items()}
    from app.agent.chapter_definitions import get_all_chapter_titles
    return get_all_chapter_titles()


def chapter_kind(domain: str | None, chapter_number: int) -> str:
    """章类型（fact/table/template/attachment…）。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import get_reclamation_chapter_kind
        return get_reclamation_chapter_kind(chapter_number)
    from app.agent.chapter_definitions import get_chapter_kind
    return get_chapter_kind(chapter_number)


def standard_outline(domain: str | None) -> List[Dict[str, Any]]:
    """按域生成确定性大纲。

    稳评：逐字段等于改动前 `report_workflow._standard_outline` 的实现
    （含 `get_chapter_data_requirements` 推出的依赖数据与依赖章）。
    复垦：复垦章节目录没有「依赖数据/依赖章」概念，故这两项留空。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        definitions = RECLAMATION_CHAPTER_DEFINITIONS
        outline: List[Dict[str, Any]] = []
        for num in all_chapters(domain):
            d = definitions.get(num, {})
            outline.append({
                "chapter_no": str(num),
                "title": d.get("title", f"第{num}章"),
                "depend_on_data": [],
                "need_spec_tags": [s.split(" ", 1)[-1] for s in d.get("sections", [])],
                "depends_on": [],
                "argument_note": "",
                "kind": chapter_kind(domain, num),
                "raw_content": None, "review_score": None, "review_msg": None,
                "retry_count": 0, "status": "pending",
            })
        return outline

    from app.agent.chapter_definitions import (
        CHAPTER_DEFINITIONS, get_chapter_data_requirements,
    )
    outline = []
    for num in all_chapters(domain):
        d = CHAPTER_DEFINITIONS.get(num, {})
        req = get_chapter_data_requirements(num)
        deps = [k for k, _n, *_r in req.get("required", [])] + \
               [k for k, _n in req.get("recommended", [])]
        outline.append({
            "chapter_no": str(num),
            "title": d.get("title", f"第{num}章"),
            "depend_on_data": deps,
            "need_spec_tags": [s.split(" ", 1)[-1] for s in d.get("sections", [])],
            "depends_on": req.get("depends_on_chapters", []),
            "argument_note": "",
            "kind": chapter_kind(domain, num),
            "raw_content": None, "review_score": None, "review_msg": None,
            "retry_count": 0, "status": "pending",
        })
    return outline


# ═══════════════════════════════════════════════════════════════
# 报告装配（report_assembler）按域取值
# ═══════════════════════════════════════════════════════════════

def has_review_table(domain: str | None) -> bool:
    """该域的报告是否有「评审表」章。

    稳评第 3 章是江苏省统一制式评审表（表格章，由系统渲染，不经 LLM）；
    复垦**没有**评审表章 —— 其第 3 章是「项目概况」（叙述章）。
    装配器据此决定第 3 章走评审表渲染，还是当普通正文写出来。
    """
    return domain != _RECLAMATION


def appendix_chapter_no(domain: str | None) -> Optional[int]:
    """该域「附件块」的章号；没有则 None。

    稳评第 14 章 = 附件（附件清单 + 图片材料）。
    复垦的附件在样本目录里也是**第 14 部分**（`10 保障措施 / 11 复垦方案报告表 /
    12 请示 / 13 图件 / 14 附件`）—— 11/12/13 尚未实现，故目录会出现 10 → 14 的跳号，
    这是刻意的（补上那三部分后跳号自然消失）。内容与稳评完全不同，
    见 `appendix_kind`。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_APPENDIX_CHAPTER_NO
        return RECLAMATION_APPENDIX_CHAPTER_NO
    from app.agent.chapter_definitions import TOTAL_CHAPTERS
    return TOTAL_CHAPTERS


def appendix_kind(domain: str | None) -> str:
    """附件块由**哪套渲染器**产出。

    两域的附件内容毫无重叠：稳评是「专家评审/拟征地公告/勘测定界摘要/现场调查图片」
    四条图片分组（`_add_appendices`），复垦是样本目录里那张 **22 项**清单
    （委托函/合同/资质证书/协议书…，`_add_reclamation_appendices`）。
    装配器据此二选一，**不写**字面 `domain == "reclamation"`。
    """
    return "reclamation" if domain == _RECLAMATION else "stability"


def uses_company_archive_images(domain: str | None) -> bool:
    """该域的公司资质图是否取自**公司档案**（而非投标用的资产库）。

    稳评与复垦都要（两份样本的前置/附件里都有「编制单位资质证书」）；
    投标文档走另一条（`asset_images` 表）。
    """
    return domain != "bidding"


def has_questionnaire_tables(domain: str | None) -> bool:
    """该域是否有「问卷调查统计汇总表」（表9-1 个人 / 表9-2 单位）。

    只有稳评有 —— 它的公众参与靠两份问卷（`questionnaire_parser` 的题面常量 +
    逐页清点）。**复垦没有问卷这回事**：样本的公众参与只有公示与走访，
    第9章是「效益分析」。不按域关掉的话，复垦报告里会凭空长出两张
    人数全是 0、比例全是「[待分析]」的问卷表（实测 2026-10-10）。
    """
    return domain != _RECLAMATION


def injects_audit_comments(domain: str | None) -> bool:
    """该域的产物里是否注入 **Word 批注**（缺口清单/审核项/人工意见）。

    稳评：注入 —— 它的口径是「正文不许出现【待补充】，但缺口必须显性」，
          批注是当时唯一能同时满足两条的落点（逐字不变）。
    复垦：**不注入**。业主 2026-10-10 口径：「不需要你写这个，这些内容你可以在
          项目生成报告结束后跟用户提示」—— 交付稿里一个批注气球都不该有，
          缺口改走前端（`GET /generate/{sid}/gaps` → `AssistantView` 的缺口面板）。
    """
    return domain != _RECLAMATION


def front_matter_labels(domain: str | None) -> Dict[str, str]:
    """装配器「前置件 / 固定抬头 / 兜底文案」按域取的中文标注。

    这些字串由 `report_assembler` **按固定路径盖上去**（封面落款两个抬头、
    公司简介里的工作组标题与兜底岗位名、资质证书页的人员培训证书节标题、
    简介末句的材料出处说明、无图注时的兜底图名、Word 审核批注的作者名），
    与报告内容无关、却写着稳评的字样 —— 复垦报告里出现「稳评责任主体」
    这类抬头即是同一类泄漏（业主口径：复垦报告只允许出现复垦内容）。

    🔴 稳评（默认域）逐字等于改动前装配器里写死的那几处 —— 这是「稳评行为不变」
       的依据。复垦取其等价口径：能逐字替换的把「稳评」换成「复垦」，
       带稳评专有落点（如「附件1」）的改写为复垦自己的出处。
    """
    if domain == _RECLAMATION:
        return {
            # 封面落款两个抬头。🔴 取值来自两份复垦样本的封面：
            #   「项目单位：中铁建工集团有限公司 / 编制单位：江苏众拓测绘有限公司」——
            #   复垦用的是「项目单位 / 编制单位」，不是稳评的「责任主体 / 实施单位」。
            "org_label": "项目单位：",
            "implement_label": "编制单位：",
            # 公司简介里「人员及分工」区标题
            "roster_heading": "复垦方案编制人员及分工情况",
            # 人员库为空时兜底名单的首个岗位名
            "roster_lead_role": "复垦方案负责人",
            # 资质证书页里「人员培训证书」小节的标题
            "cert_section_heading": "土地复垦业务培训证书",
            # 简介末句：材料出处说明。🔴 复垦没有附件章（见 appendix_chapter_no），
            #    原句「详见附件1」会指向不存在的章 —— 改写为指向资质证书节。
            "material_note": "本报告相关机构营业执照、项目人员职称及培训证书等材料详见公司资质证书。",
            # 地图类图片无图注时的兜底图名
            "map_caption": "位置示意图",
            # Word 审核批注的作者名
            "audit_comment_author": "复垦审核Agent",
        }
    return {
        "org_label": "稳评责任主体：",
        "implement_label": "稳评实施单位：",
        "roster_heading": "稳评工作组人员及分工情况",
        "roster_lead_role": "稳评负责人",
        "cert_section_heading": "社会稳定风险评估业务培训证书",
        "material_note": "本报告相关机构营业执照、项目人员职称及稳评培训证书详见附件1。",
        "map_caption": "拟征收土地位置示意图",
        "audit_comment_author": "稳评审核Agent",
    }


# ═══════════════════════════════════════════════════════════════
# 前置件 / 章标题体例（装配器 `report_assembler` 按域取值）
# ═══════════════════════════════════════════════════════════════
# 两份复垦样本（明远佳苑二期报告书、盐业井神方案）规格完全一致：
#   封面(项目单位/编制单位/年月) → 扉页(项目名称/项目单位/单位地址/联系人/电话)
#   → 目录 → 「1 前言 / 1.1 …」（阿拉伯数字；章标题不独占整页）
# **没有**稳评那两页：「公司资质证书」「公司简介(含人员名单)」。
# 稳评（默认域）逐字等于改动前 —— 这是「稳评输出不变」的结构性依据。

_FRONT_MATTER_ORDER = {
    "stability": ["cover", "qualifications", "intro", "toc"],
    "reclamation": ["cover", "title_page", "toc"],
}


_TYPO = {
    # 稳评（默认域）：**逐字等于改动前**。
    #   一级 黑体 22pt 是业主 2026-09-21 明确选型；二三级与正文按参照件
    #   《白马湖农场1号风电项目》实测值。
    "stability": {
        "h1": ("黑体", 22), "h2": ("宋体", 15), "h3": ("宋体", 14),
        "body": ("宋体", 12),
        "cover_title": ("宋体", 26), "cover_sign": ("宋体", 14),
        "title_page_head": ("宋体", 22), "title_page_info": ("宋体", 12),
        "toc_heading": ("黑体", 18), "toc_l1": ("宋体", 12), "toc_l2": ("宋体", 12),
    },
    # 复垦：**四份样本逐份实测，取值完全一致**
    #   （明远路 139 页 / 王元佳苑 154 页 / 污水处理厂 126 页 / 宁淮城际 127 页）。
    #   ⚠️ 一级标题样本是 16pt 黑体，**不是**稳评那个 22pt —— 22pt 是稳评的业主选型，
    #      四份复垦样本没有一份这么印。二三级在样本里同样是**黑体**（不是宋体）。
    #      「明远路」那份的编制人员栏写的是江苏众拓测绘 —— 即自家交出去、业主收下的成品。
    "reclamation": {
        "h1": ("黑体", 16), "h2": ("黑体", 15), "h3": ("黑体", 14),
        "body": ("仿宋", 12),
        "cover_title": ("黑体", 26), "cover_sign": ("黑体", 15),
        "title_page_head": ("黑体", 22), "title_page_info": ("仿宋", 14),
        "toc_heading": ("黑体", 18), "toc_l1": ("黑体", 12), "toc_l2": ("仿宋", 12),
    },
}


def typography(domain: str | None, slot: str) -> tuple:
    """该域某个版式槽位的 `(中文字体, 字号 pt)`。

    槽位：`h1/h2/h3`（标题）· `body`（正文）· `cover_title/cover_sign`（封面标题/落款）·
    `title_page_head/title_page_info`（扉页标题/信息行）· `toc_heading/toc_l1/toc_l2`（目录）。

    🔴 稳评是默认域，取值与改动前逐字相同；未知槽位也回落到稳评，不抛异常。
    """
    _d = _TYPO.get(domain or "stability", _TYPO["stability"])
    return _d.get(slot) or _TYPO["stability"].get(slot) or ("宋体", 12)


def cover_bold(domain: str | None) -> bool:
    """封面标题/落款、扉页标题是否**加粗**。

    稳评：加粗（参照件口径，逐字等于改动前）。
    复垦：**不加粗** —— 样本封面实测（明远路 p1）三行标题 26.1pt 黑体、
    落款 15pt 黑体，`flags` 里**没有**粗体位；扉页标题 22pt 同样不加粗。
    """
    return domain != _RECLAMATION


# 前置件各行之间的**空档**（pt）。业主口径：「中间都要隔一行」。
#   复垦取值按样本逐行实测的间距反推：
#     封面（明远路 p1）落款三行 y=590/646/700 → 行距 56pt，15pt 字 → 空档约 36pt；
#     扉页（p2）六行 y=277/395/485/569/653/719 → 平均行距约 88pt，14pt 字 → 空档约 70pt，
#       整块铺满版心（277→719 跨 442pt，版心 698pt）。
_FRONT_MATTER_GAP_PT = {
    "stability": {"cover_sign": 0.0, "title_page_head": 96.0, "title_page_row": 22.0},
    "reclamation": {"cover_sign": 36.0, "title_page_head": 44.0, "title_page_row": 70.0},
}


def front_matter_gap_pt(domain: str | None, slot: str) -> float:
    """前置件某个空档槽位的 pt。稳评逐字等于改动前写死的值。"""
    _d = _FRONT_MATTER_GAP_PT.get(domain or "stability", _FRONT_MATTER_GAP_PT["stability"])
    return float(_d.get(slot, _FRONT_MATTER_GAP_PT["stability"].get(slot, 0.0)))


def front_matter_order(domain: str | None) -> List[str]:
    """前置件渲染顺序（token 列表）。稳评四件，逐字等于改动前的拼接顺序与分页点。"""
    return list(_FRONT_MATTER_ORDER.get(domain or "stability",
                                        _FRONT_MATTER_ORDER["stability"]))


def has_title_page(domain: str | None) -> bool:
    """是否有「扉页/项目信息页」。稳评没有；复垦有。"""
    return "title_page" in front_matter_order(domain)


def cover_org_value(domain: str | None, filled: dict, org_name: str) -> str:
    """封面落款第一行 `front_matter_labels(domain)["org_label"]` 后面跟的**值**。

    稳评：责任主体 = 公告/征收主体，即装配器算出来的 `org_name` —— **逐字等于改动前**。
    复垦：抬头写的是「项目单位」，其值是**用地/建设单位**，在材料的
          `project_unit` 里（样本《明远佳苑二期》封面写的就是项目单位名）；
          抽不到才退回 `org_name`。

    🔴 为什么单独收口：封面与扉页必须写**同一个**项目单位。扉页
       （`_add_title_page`）取 `project_unit`；封面若照旧取稳评口径的 `org_name`，
       同一份报告的两页会印出两个不同的「项目单位」—— 实测 2026-10-10 明远佳苑：
       封面「淮安市自然资源和规划局」（其实是公告机关）、扉页「中铁建工集团有限公司」。
    """
    if domain == _RECLAMATION:
        return str((filled or {}).get("project_unit") or org_name or "")
    return str(org_name or "")


def chapter_heading_text(domain: str | None, chapter_number: int,
                         cn: str, title: str) -> str:
    """章标题文本（Heading 1）。

    稳评 `第三章、评估过程、方法和依据` —— 逐字等于改动前 `_add_chapter_title_page`
    的 `f"第{cn}章、{title}"`；复垦 `3 项目概况`（样本体例：阿拉伯数字 + 空格）。
    """
    if domain == _RECLAMATION:
        return f"{int(chapter_number)} {title}"
    return f"第{cn}章、{title}"


def chapter_title_own_page(domain: str | None) -> bool:
    """章标题是否独占整页并垂直居中。稳评是；复垦不是（样本里标题就在正文页顶）。"""
    return domain != _RECLAMATION


def has_chinese_sub_headings(domain: str | None) -> bool:
    """本域小节体例是否为稳评式「（一）（二）」。

    真 → 单章后处理走 `_normalize_headings` / `_apply_section_subs` 那套归一；
    假（复垦）→ 走 `normalize_reclamation_headings`，保住 `X.Y` 编号。
    """
    return domain != _RECLAMATION


def is_chapter_heading_text(domain: str | None, text: str) -> bool:
    """该文本是不是「正文里的章标题」（`_fill_toc_cache` 据此跳过前置页标题）。"""
    t = (text or "").strip()
    if domain == _RECLAMATION:
        return bool(re.match(r'^\d+\s+\S', t))
    return bool(re.match(r'^第[一二三四五六七八九十]+章', t))


def chapter_no_of_text(domain: str | None, text: str) -> int:
    """段文本 → 章号；**不是章标题返回 0**。

    🔴 为什么要有它：装配器里按章挂东西的地方（审核批注的锚点）原先直接写了
       稳评的正则 `^第([一二三四五六七八九十\\d]+)章\\s` —— 复垦章标题是
       `1 前言`，**一条都匹配不上**，于是所有批注统统回落到 `doc.paragraphs[0]`
       （封面标题），交付稿**首页挂满批注气球**（实测 2026-10-10，9 条全在封面）。

    稳评：委托 `chapter_definitions.chapter_no_of_heading`（逐字不变）；
    复垦：`3 项目概况` 这种「阿拉伯数字 + 空格」。
    """
    t = (text or "").strip()
    if domain == _RECLAMATION:
        m = re.match(r'^(\d{1,2})\s+\S', t)
        return int(m.group(1)) if m else 0
    from app.agent.chapter_definitions import chapter_no_of_heading
    return chapter_no_of_heading(t)


def _strip_heading_number(s: str) -> str:
    """去掉标题自带的 `X.Y` / `（一）` 编号，只留名字。"""
    s = re.sub(r'^[\d\.]+\s*', '', s or '')
    s = re.sub(r'^[（(][一二三四五六七八九十]+[）)]\s*', '', s)
    return s.strip()


def normalize_reclamation_headings(chapter_number: int, md: str) -> str:
    """复垦单章后处理：把 LLM 写下的小节标题拉回 `X.Y 标题` 体例。

    🔴 为什么需要它：单章后处理原先只有稳评那一套 —— `_normalize_headings` 拿
       `template_headings.CHAPTER_HEADINGS`（**稳评的**标准标题集）比对，复垦的
       `## 3.1 项目简介` 不在集合里，于是命中「自创标题」分支被改写成
       `**（一）项目简介**`：层级掉成正文加粗、`X.Y` 编号也没了。
       （照搬章 2/5/7/10 走参照件渲染、不过这段后处理，所以它们的 `2.1` 一直是对的
        —— 这正是「一半章对、一半章错」的由来。）

    规则：
      ① 与 `RECLAMATION_CHAPTER_DEFINITIONS[ch]["sections"]`（如 `1.2 复垦方案摘要`）
         同名的小节 → 拉回规范编号；
      ② 认不出的「（一）（二）」条目 → 归到**当前小节**下做三级编号（`1.2.1`、`1.2.2`…，
         与样本体例一致）。序号按出现顺序重排，避免 LLM 分成两段后从「（一）」重来
         （实测第 6 章出现过两组「（一）」）；
      ③ 其余标题原样保留（不硬改）。
    """
    from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
    d = RECLAMATION_CHAPTER_DEFINITIONS.get(int(chapter_number), {})
    sections = [str(s).strip() for s in (d.get("sections") or [])]
    if not sections:
        return md
    by_name: Dict[str, str] = {}
    for s in sections:
        m = re.match(r'^(\d+(?:\.\d+)*)\s*(.+)$', s)
        if m:
            by_name[_strip_heading_number(m.group(2))] = m.group(1)

    out: List[str] = []
    cur = ""      # 当前二级小节号（如 `1.2`）
    sub = 0       # 当前小节下的三级序号
    for line in md.split("\n"):
        s = line.strip()
        if not s:
            out.append(line)
            continue
        m = re.match(r'^(#{2,4})\s*(.+)$', s)
        if m:
            raw = m.group(2).strip().strip('*').strip()
        else:
            mb = re.match(r'^\*\*(.+)\*\*$', s)
            if mb:
                raw = mb.group(1).strip()
            else:
                # 🔴 裸行的「（一）表土剥离」也算标题 —— 装配器本来就把它当二级标题渲染
                #    （见 `report_assembler` 的 `^（[一二三四五六七八九十]+）…$` 分支），
                #    这里不认就会漏：实测第6章 LLM 写成裸行，5 条 `（一）…（五）` 原样印进报告，
                #    没编号也没层级。判据与装配器**逐字一致**（短、且不含句读）。
                if not re.match(r'^[（(][一二三四五六七八九十\d]+[）)]\s*[^\s。，,；;]{2,30}$', s):
                    out.append(line)
                    continue
                raw = s
        body = _strip_heading_number(raw)
        num = by_name.get(body)
        if num:
            cur = num
            sub = 0
            out.append(f"{'###' if num.count('.') >= 2 else '##'} {num} {body}")
            continue
        # 🔴 标题**自带编号**（如 `### 6.1.1 工程技术措施设计`，其名不在 `sections` 里）
        #    → 它同样定义了一个「当前小节」，否则它下面的「（一）…」会被归到
        #    6.1 那一层，编出与 6.1.1 撞号的 `6.1.1`（实测第6章）。
        ms = re.match(r'^(\d+(?:\.\d+)+)\s*\S', raw)
        if ms:
            cur = ms.group(1)
            sub = 0
            out.append(line)
            continue
        # 「（一）xxx」条目 → 当前小节下的下一级编号
        if cur and re.match(r'^[（(][一二三四五六七八九十\d]+[）)]', raw):
            sub += 1
            out.append(f"{'#' * min(4, cur.count('.') + 2)} {cur}.{sub} {body}")
            continue
        out.append(line)
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════
# 单章提示词（master_orchestrator.build_chapter_prompt）按域取值
# ═══════════════════════════════════════════════════════════════

def target_words(domain: str | None, chapter_number: int) -> tuple:
    """该域某章的 (下限, 上限) 字数。

    稳评委托 `master_orchestrator.target_words`（单一事实源，逐字不变）；
    复垦取 `RECLAMATION_CHAPTER_DEFINITIONS` 里已有的 min_words/max_words。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        d = RECLAMATION_CHAPTER_DEFINITIONS.get(int(chapter_number), {})
        hi = d.get("max_words")
        if hi is None:
            return (800, 2000)          # 兜底与稳评同形
        lo = d.get("min_words")
        return (int(lo) if lo is not None else max(800, int(hi * 0.6)), int(hi))
    from app.services.master_orchestrator import target_words as _stability_target_words
    return _stability_target_words(chapter_number)


def sections_for_prompt(domain: str | None, chapter_number: int) -> str:
    """该域某章的「二级标题结构规范」提示词注入文本。"""
    if domain == _RECLAMATION:
        return _reclamation_sections_for_prompt(chapter_number)
    from app.agent.chapter_definitions import format_sections_for_prompt
    return format_sections_for_prompt(chapter_number)


def _reclamation_sections_for_prompt(chapter_number: int) -> str:
    """复垦某章的二级标题清单 → 提示词注入文本。

    与稳评 `format_sections_for_prompt`（`### （一）…` + 三级标题）体例不同：
    复垦章节目录的小节名**自带 `X.Y` 编号**（见 RECLAMATION_CHAPTER_DEFINITIONS），
    直接沿用即可，不再套稳评那套「（一）（二）」编号。
    """
    from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
    d = RECLAMATION_CHAPTER_DEFINITIONS.get(int(chapter_number), {})
    sections = d.get("sections") or []
    if not sections:
        return ""
    lines = [
        "\n## 📋 本章结构规范（严格按此生成，禁止增删改二级标题）",
        "\n🔴 内容组织铁律：**所有正文内容必须写在二级标题下**。"
        "「第X章 标题」一级标题之后直接进入第一个二级标题，禁止游离正文段落。",
        "🔴 二级标题写成 `## X.Y 小节名`（编号用阿拉伯数字，与下列清单逐字一致）。",
        f"\n🔴 本章内容范围：{d.get('description', '')}",
    ]
    lines.extend(f"## {s}" for s in sections)
    lines.append("\n严格按上述二级标题顺序逐一撰写，每个标题下必须有实质内容，禁止标题后空白。")
    return "\n".join(lines)


def table_format_reference(domain: str | None, chapter_number: int) -> str:
    """该域某章的「表格格式参考」提示词注入文本。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        d = RECLAMATION_CHAPTER_DEFINITIONS.get(int(chapter_number), {})
        tables = d.get("key_tables") or []
        if not tables:
            return ""
        listed = "\n".join(f"  - 「{t}」" for t in tables)
        return (
            "## 📐 本章表格（见本章结构规定的表）\n"
            "下列表格由本章结构规定，供你参考表名与用途；列结构与内容由你根据实际数据设计。"
            "有数据支撑才写，没有数据就写规范文字描述，绝不写空表或编造数据：\n"
            + listed + "\n"
        )
    from app.services.master_orchestrator import get_table_format_reference
    return get_table_format_reference(chapter_number)


def key_labels(domain: str | None) -> Dict[str, str]:
    """该域「数据字段 → 中文标注」映射（提示词里标注每个字段的出处）。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_KEY_LABELS
        return dict(RECLAMATION_KEY_LABELS)
    from app.services.master_orchestrator import stability_key_labels
    return stability_key_labels()


def project_card_labels(domain: str | None) -> Dict[str, str]:
    """该域「事实卡字段 → 中文标注」映射（`build_project_card` 用）。

    事实卡是每章 agent 的**唯一事实源**，其字段标注原先写死稳评口径
    （`稳评实施单位` / `征地预公告文号` / `征收面积（亩）`…）—— 复垦事实卡因此
    顶着稳评字段名被喂给每一章。这里按域取：

    🔴 稳评（默认域）逐字等于改动前 `master_orchestrator._PROJECT_CARD_LABELS`
       （委托 `stability_project_card_labels`，不复制第二份）。
    🔴 复垦复用其**既有**的 `RECLAMATION_KEY_LABELS`（与单章提示词「可用数据」标注
       同源，见 `key_labels`），不另造一份 —— 复垦没有的稳评字段（户数/支持率/
       各种日期）即标注缺失，`build_project_card` 按 `.get(k, k)` 回落原 key，
       不拿稳评的字段名顶替。
    """
    if domain == _RECLAMATION:
        return key_labels(domain)
    from app.services.master_orchestrator import stability_project_card_labels
    return stability_project_card_labels()


def project_card_keys(domain: str | None) -> List[str]:
    """该域「事实卡字段清单」（顺序即渲染顺序）。

    事实卡原先渲染一份**写死 59 键的稳评清单** —— 复垦自己的字段（服务年限 / 复垦方向 /
    动态总投资 / 建设单位…）不在其中，即便有值也**进不了**事实卡、到不了模型眼前。
    这里按域取：

    🔴 稳评（默认域）逐字等于改动前 `build_project_card` 内联的那份
       （委托 `stability_project_card_keys`，不复制第二份）。
    🔴 复垦取 `RECLAMATION_CARD_KEYS` —— 业主 2026-10-09 核准过的复垦字段清单
       （项目 / 勘测定界 / 复垦方向 / 工程量与钱 / 图件与附件 / 日期 六组）。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_CARD_KEYS
        return list(RECLAMATION_CARD_KEYS)
    from app.services.master_orchestrator import stability_project_card_keys
    return stability_project_card_keys()


def project_card_lexicon(domain: str | None) -> Dict[str, Any]:
    """`master_orchestrator.build_project_card` 里「随域而变」的全部字串。

    除**字段清单**（见 `project_card_keys`）与字段标注（见 `project_card_labels`）外，
    事实卡还有三个**附带小节**：征收土地预公告正文摘录 / 勘测定界明细 / 座谈会记录。
    它们的抬头与小节字段标注原先也写死稳评口径 ——「征收土地预公告」「稳评公示期」
    「（第9章 各方意见汇总采集）」之类会随事实卡进入复垦章的提示词。

    返回：
      keys          —— 字段清单（顺序即渲染顺序）
      fields        —— {字段 key: 中文标注}
      ann_heading / ann_fields    —— 预公告摘录小节（fields 为 [(key,label)]，空 = 整块不出现）
      land_heading                —— 勘测定界明细小节抬头（该小节各行标注域中立，留在调用点）
      meet_heading / meet_fields  —— 座谈会记录小节

    🔴 稳评（默认域）逐字等于改动前 `build_project_card` 内联的那几处。
    🔴 复垦没有「征收土地预公告」与「稳评座谈会」两个概念 → 对应小节字段表留空，
       整块不出现；**不把稳评那几行换个词冒充**。勘测定界两域共用，抬头去掉稳评章号。
    """
    if domain == _RECLAMATION:
        return {
            "keys": project_card_keys(domain),
            "fields": project_card_labels(domain),
            "ann_heading": "", "ann_fields": [],
            "land_heading": "  —— 勘测定界明细 ——",
            "meet_heading": "", "meet_fields": [],
        }
    return {
        "keys": project_card_keys(domain),
        "fields": project_card_labels(domain),
        "ann_heading": "  —— 征收土地预公告正文摘录 ——",
        "ann_fields": [
            ("legal_basis_cited", "公告引用的法律法规"),
            ("preamble_text", "公告前言（决定启动的表述）"),
            ("purpose_text", "征收目的（公告原文）"),
            ("purpose_legal_basis", "征收目的法定依据"),
            ("scope_text", "征收范围（公告原文）"),
            ("notice_period_text", "公告期限（原文）"),
            ("work_arrangement_text", "工作安排（公告原文）"),
            ("other_matters_text", "其他事项（公告原文）"),
            # 🔴 稳评公示期（用户填开始日 → 系统推算一周），与上面的「公告期限」不是一回事
            ("publicity_period_text", "稳评公示期（公示期一周）"),
            ("contact_name", "公告联系人"),
            ("contact_phone", "公告联系电话"),
            ("attachment_note", "公告附图说明"),
        ],
        "land_heading": "  —— 勘测定界明细（第6章 1.5 涉及村组/地类构成、第10章合法性论证用）——",
        "meet_heading": "  —— 座谈会记录（第9章 各方意见汇总取材）——",
        "meet_fields": [
            ("public_demands", "座谈会群众诉求（记录原文）"),
            ("discussion_text", "座谈讨论情况（记录原文）"),
            ("conclusion_text", "座谈结论（记录原文）"),
        ],
    }


def report_persona(domain: str | None) -> str:
    """单章提示词的「身份」首句。

    稳评逐字等于改动前的首句；复垦取其域配置的 `identity_prompt`。
    """
    if domain == _RECLAMATION:
        from app.domains.registry import get_domain
        return get_domain(domain).identity_prompt
    return "你是社会稳定风险评估报告的资深编写专家。"


def report_kind_name(domain: str | None) -> str:
    """报告类型名（用于页眉抬头、输出文件名等）。

    稳评逐字等于改动前的「社会稳定风险评估报告」；复垦取其域 `display_name`。
    """
    if domain == _RECLAMATION:
        from app.domains.registry import get_domain
        return get_domain(domain).display_name or "土地复垦方案"
    return "社会稳定风险评估报告"


def template_name(domain: str | None) -> str:
    """建会话时钉进 `state["template_name"]` 的「模板显示名」（按域取）。

    这个名字是给用户看的「报告结构」抬头 —— 起始响应、历史库落库、主 Agent 的
    模板上下文都读它。旧实现里 `report.py` 与 `state.create_initial_state` 各写死一份
    「内置十四章案卷标准结构」类字样，复垦会话顶着它即是同一类泄漏（复垦是 10 章、
    没有「案卷」概念）。

    稳评（默认域）逐字等于改动前 `report.py` 里写死的那个串 —— 这是「稳评行为不变」
    的依据；复垦换成复垦口径。
    """
    if domain == _RECLAMATION:
        return "内置复垦方案标准结构（知识库综合生成）"
    return "内置十四章案卷标准结构（知识库综合生成）"


def cover_type_line(domain: str | None) -> str:
    """封面副标题（报告类型那一行）。

    稳评逐字等于业主 2026-09-21 口径的「《社会稳定性风险评估》」（**带书名号**）。
    复垦按样本：五份样本的封面这一行都是「土地复垦方案**报告书**」，
    「临时用地土地复垦方案报告书」这类写法也**一律不带书名号** ——
    带书名号是稳评被业主点名改的写法，复垦照抄就成了稳评格式。
    """
    if domain == _RECLAMATION:
        return "土地复垦方案报告书"
    return "《社会稳定性风险评估》"


def cover_date_parts(domain: str | None, now=None) -> tuple:
    """封面落款第三行的 `(标签, 值)`。

    稳评：`('报告编制日期：', '二〇二六年十月')` —— 逐字等于改动前。
    复垦按样本：明远路封面是纯数字的「2023  年  1  月」，**没有标签**，
          所以标签返回空串、值返回同样的纯数字写法。
    """
    from datetime import datetime as _dt
    _now = now or _dt.now()
    if domain == _RECLAMATION:
        return "", f"{_now.year}  年  {_now.month}  月"
    # 延迟 import：装配器在模块级 import 本文件，运行期再取不会成环
    # （本文件已有从 `app.services.*` 取值的先例，见 `image_caption` 等）。
    from app.services.report_assembler import _cn_year_month
    return "报告编制日期：", _cn_year_month(_now)


def cover_fallback_title(domain: str | None) -> str:
    """封面「无项目名」时的兜底主标题。"""
    if domain == _RECLAMATION:
        return "土地复垦方案"
    return "土地征收决策"


def has_scoring_guidance(domain: str | None) -> bool:
    """是否注入「扣分制」评分规则（`build_chapter_prompt` 的评分块）。

    扣分制（DB32/T4013-2021）是稳评专有口径；复垦无此章、不注入。
    """
    return domain != _RECLAMATION


def has_chapter_specific_requirements(domain: str | None) -> bool:
    """是否注入「本章特定要求」块（逐章的落位说明）。

    该块是稳评专有的逐章结构说明（如第3章「江苏省统一制式评审表」）；
    复垦章暂无对应文本，不注入稳评的那份。
    """
    return domain != _RECLAMATION


def requirements_block(domain: str | None, min_words: int, max_words: int,
                       chapter_number: int = None) -> str:
    """单章提示词末段的「写作要求」块（按域取）。

    复垦：返回复垦口径的要求文本（不含稳评的规范/口径）；`chapter_number` 用于并进
    该章的专属要求（第6章工程量表那一段）。
    稳评：返回空串 —— 稳评那份是 `master_orchestrator` 的内联正文，
    逐字保持不变，由调用点继续使用内联版本（**不接收 chapter_number**）。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import render_reclamation_requirements
        return render_reclamation_requirements(min_words, max_words, chapter_number)
    return ""


# ═══════════════════════════════════════════════════════════════
# 默认单章 system prompt（`chapter_base._default_system_prompt`）按域取值
# ═══════════════════════════════════════════════════════════════
# `ChapterOrchestrator` 路径（前端「生成某章」走的就是它）**不设** `_use_custom_prompt`，
# 故这套默认提示词就是模型实际收到的 system prompt —— 它原先满篇稳评：身份叫「资深稳评
# 工程师」、规范写 DB32/T4013、结构/小标题/模板底稿/表格骨架全取自稳评目录，于是复垦章
# 由稳评工程师按稳评章结构写出来。下面把**随域而变**的几处收口到这里；通用规则
# （AI 套词禁令、第一/第二/第三禁令、禁编造、真人工程师口吻、实施单位提示、结构注入
# 机制）在 chapter_base 原地保留、逐字不变。稳评（默认域）取值逐字等于改动前。

def chapter_persona(domain: str | None) -> str:
    """默认单章 system prompt 的「身份首句」（按域）。

    🔴 与 `report_persona` 不同：那是 `master_orchestrator.build_chapter_prompt`
       那条路的身份串；这里是 `ChapterOrchestrator` 默认提示词的身份串 —— 稳评那份是
       「你是一位资深稳评工程师，长期做土地征收社会稳定风险评估。」（逐字不变）。
       复垦取其域配置的 `identity_prompt`（= `RECLAMATION_IDENTITY_PROMPT`）。
    """
    if domain == _RECLAMATION:
        from app.domains.reclamation import RECLAMATION_IDENTITY_PROMPT
        return RECLAMATION_IDENTITY_PROMPT
    return "你是一位资深稳评工程师，长期做土地征收社会稳定风险评估。"


def chapter_principle_lead(domain: str | None) -> str:
    """「核心原则」首条 —— 组织内容所依据的规范（按域）。

    稳评逐字等于改动前首条（DB32/T4013-2021）；复垦换成其编制依据
    （《土地复垦条例》《土地复垦条例实施办法》与 TD/T 1031 系列编制规程，
    见 `reclamation.py` 的 `RECLAMATION_IDENTITY_PROMPT`）。
    """
    if domain == _RECLAMATION:
        return ("依据《土地复垦条例》《土地复垦条例实施办法》与 TD/T 1031 系列"
                "编制规程，结合复垦方案编制经验组织内容")
    return "根据DB32/T4013-2021规范和你8年的实战经验来组织内容"


def chapter_transition_examples(domain: str | None) -> str:
    """「鼓励」块里的基层过渡语示例行（按域，含行首 `- ` 与行尾换行）。

    稳评的「入户走访/社区书记」是土地征收稳评的公众参与语汇；复垦换成它的
    现场作业与权属人口径（「现场查勘」对应勘测定界，「权属人意见」见
    `reclamation.py` 的 data_categories）。稳评逐字不变。
    """
    if domain == _RECLAMATION:
        return "- 基层过渡语：「结合现场查勘」「根据权属人意见」\n"
    return "- 基层过渡语：「结合入户走访」「根据社区书记反馈」\n"


# 「数据归位铁律」块（按域）—— 告诉模型「哪类数据该落在哪一章」。
# 🔴 稳评那份逐字等于改动前 `chapter_base` 内联的七行（含块尾换行、块后无空行）。
#    复垦那份由 `reclamation_definitions` 的十章节结构逐条推导，**不复用稳评章号**；
#    稳评里没有复垦对应物的规则（如「四性分析」「问卷/座谈支持率」）一律不带过来。
_STABILITY_DATA_IRON_RULE = (
    "## 🔴 数据归位铁律（本报告各章数据各归其位，不得越界、不得重复）\n"
    "- 项目概况类数据（征收面积/地类面积/四至/户数）只在第4章「决策事项基本情况」出现，后续章节不得重复罗列\n"
    "- 勘测定界数据（各地类/各权属单位面积）只属于第4章，不得出现在第7章四性分析\n"
    "- 问卷/座谈数据（份数、支持率、参会人数、诉求）只属于第6章「各利益相关者对决策事项的反映」\n"
    "- 风险因素与评分只属于第8章「分析预测风险源」；防范化解措施只属于第9章\n"
    "- 文号（如洪拟征告〔2026〕X号）必须严格来自「可用数据」里的真实文号，绝不编造、不改号、不抄范文里的文号\n"
    "- 同一数据全文只写一次，避免同一组数字在多章反复出现\n"
)

# 复垦版：逐条对应 RECLAMATION_CHAPTER_DEFINITIONS 的章名与「key_tables / sections」。
_RECLAMATION_DATA_IRON_RULE = (
    "## 🔴 数据归位铁律（本报告各章数据各归其位，不得越界、不得重复）\n"
    "- 第1章「前言」的复垦方案摘要只作概述：服务年限、各类土地面积、土地损毁情况、复垦目标、投资情况各以结论性数字点到为止，明细归各自专章，不得展开\n"
    "- 项目基础信息（项目名称、建设单位/复垦义务人、临时用地位置、服务年限）在第3章「项目概况」集中交代，后续章节需要时以通称指代，不再重复罗列\n"
    "- 土地利用现状与地类构成只在第3章「项目概况·项目区土地利用状况」与第4章「复垦区土地利用状况」出现，其他章不得重复罗列地类面积明细\n"
    "- 土地损毁分析与评定、复垦适宜性评价、水土资源平衡、生态保护红线及生态空间管控分析、复垦的目标任务只属于第4章「土地复垦方向可行性分析」\n"
    "- 复垦质量要求与预防控制、复垦（工程/生物化学/永久基本农田）、监测、管护各项措施只属于第5章「土地复垦质量要求与复垦措施」\n"
    "- 工程设计及工程量测算数据只属于第6章「土地复垦工程设计及工程量测算」\n"
    "- 投资估算数据（工程施工费、设备购置费、其他费用、监测管护费、预备费、动态总投资）只属于第7章「土地复垦投资估算」，其他章只能引用其结论数字，不得重复估算过程与费用明细\n"
    "- 复垦工作计划与费用安排（分期、进度）只属于第8章「土地复垦工作计划安排」\n"
    "- 效益数据（社会效益、生态效益、经济效益、复垦后耕地质量）只属于第9章「土地复垦效益分析」\n"
    "- 文号（批复/备案文号）必须严格来自「可用数据」里的真实文号，绝不编造、不改号、不抄范文里的文号\n"
    "- 同一数据全文只写一次，避免同一组数字在多章反复出现\n"
)


def chapter_data_iron_rule(domain: str | None) -> str:
    """默认单章 system prompt 的「数据归位铁律」块（按域，含块尾换行）。"""
    if domain == _RECLAMATION:
        return _RECLAMATION_DATA_IRON_RULE
    return _STABILITY_DATA_IRON_RULE


def headings_for_prompt(domain: str | None, chapter_number: int) -> str:
    """默认单章 system prompt 的「标准子标题」块（按域）。

    稳评：委托 `template_headings.format_headings_for_prompt`（逐字不变）。
    复垦：不单独给子标题 —— 其小节名与 `X.Y` 编号已由 `sections_for_prompt`
          （「本章结构规范」块）逐条列出，再出一份就是重复，故返回空串。
    """
    if domain == _RECLAMATION:
        return ""
    from app.agent.template_headings import format_headings_for_prompt
    return format_headings_for_prompt(chapter_number)


def template_block_for_prompt(domain: str | None, chapter_number: int) -> str:
    """默认单章 system prompt 的「人工模板底稿」块（按域）。

    稳评：委托 `template_chapter_chunker.build_template_block_for_prompt`（逐字不变）。
    复垦：公司标准模板库目前只有稳评模板，复垦无底稿 —— 返回空串，绝不把
          稳评模板底稿当作复垦章的「行文基准」注入。
    """
    if domain == _RECLAMATION:
        return ""
    from app.services.template_chapter_chunker import build_template_block_for_prompt
    return build_template_block_for_prompt(chapter_number)


def table_skeletons(domain: str | None, chapter_number: int) -> Dict[str, Any]:
    """该域某章的「模板表格骨架」表 `{表名: {columns, fill_types, ...}}`（按域）。

    稳评：`knowledge_agent.TEMPLATE_TABLE_SKELETONS`（逐字不变）。
    复垦：该表是从稳评报告模板里提取的（如第3章「拟征收土地基本情况表」），
          复垦无对应骨架 —— 返回 `{}`，绝不把稳评表结构塞进复垦章。
    """
    if domain == _RECLAMATION:
        return {}
    from app.agent.agents.knowledge_agent import TEMPLATE_TABLE_SKELETONS
    return TEMPLATE_TABLE_SKELETONS.get(chapter_number, {})


# ═══════════════════════════════════════════════════════════════
# 默认单章 user prompt（`chapter_base._default_user_prompt`）按域取值
# ═══════════════════════════════════════════════════════════════
# 这条提示词把「写作材料」按优先级排开：材料1 = 规范 + 优秀范文、材料2 = 法规标准、
# 材料3 = 本项目数据、材料4 = 原始文件。原先「材料1」抬头**写死 DB32/T4013-2021**
#   （**稳评**的规范）**并往里注入 `data/template_chapters.json` 切出的稳评模板段落** ——
# 复垦章据此「优先参考稳评规范 + 稳评模板范文」写复垦正文，正是要消除的掺兑。
# 下面把这两处按域收口；稳评（默认域）取值逐字等于改动前。

def material_heading(domain: str | None) -> str:
    """默认单章 user prompt「材料1」抬头行（按域，**含行尾换行**）。

    稳评逐字等于改动前（DB32/T4013-2021 + 优秀范文）；
    复垦点名其编制依据（《土地复垦条例》《土地复垦条例实施办法》与 TD/T 1031 系列
    编制规程，见 `reclamation.py` 的 `RECLAMATION_IDENTITY_PROMPT`），不再顶着稳评规范。
    「优秀范文」保留 —— 复垦范文来自其**自有** RAG（`kb_reclamation_*`，5 份真实方案）。
    """
    if domain == _RECLAMATION:
        return ("### 材料1：复垦编制规范（《土地复垦条例》《土地复垦条例实施办法》"
                "与 TD/T 1031 系列编制规程）+ 优秀范文（最重要，优先参考）\n")
    return "### 材料1：DB32/T4013-2021规范 + 优秀范文（最重要，优先参考）\n"


def template_reference_for_prompt(
    domain: str | None, chapter_number: int, filled_data: dict,
) -> str:
    """默认单章 user prompt 的「模板报告写法示例」块（按域）。

    稳评：委托 `template_content.TemplateContentProvider.build_template_prompt`
          （逐字不变）—— 从 `data/template_chapters.json` 取本章最长的模板段落当写作标杆。
    复垦：**不注入** —— `template_chapters.json` 是稳评产物（12 条，按稳评章号建键），
          复垦没有对标模板库，其素材来自**自有** RAG（`kb_reclamation_*`）。此处返回空串，
          绝不拿稳评模板段落冒充复垦的「写作标杆」，也不拿别的东西替。
    """
    if domain == _RECLAMATION:
        return ""
    from app.agent.template_content import template_content_provider
    return template_content_provider.build_template_prompt(chapter_number, filled_data)


# ═══════════════════════════════════════════════════════════════
# `chapter_base.act()` 公共下游「按域」的两处（自定义 prompt 兜底身份 / 写作要求）
# ═══════════════════════════════════════════════════════════════

def custom_prompt_persona(domain: str | None) -> str:
    """自定义 prompt 分支（`_use_custom_prompt`）兜底 system prompt 的身份句（按域）。

    该分支用主编排给的 `_custom_prompt`（`master_orchestrator.build_chapter_prompt`
    已按域写好身份），这里的 `system_prompt` 只是兜底 —— 原先写死「你是一位资深稳评
    工程师。」，复垦会话顶着稳评身份。稳评逐字不变；复垦取其域 `identity_prompt`。
    """
    if domain == _RECLAMATION:
        from app.domains.registry import get_domain
        return get_domain(domain).identity_prompt
    return "你是一位资深稳评工程师。"


def writing_guide(domain: str | None) -> str:
    """`act()` 每次生成前置的「## 要求」写作引导块（按域，含首尾换行）。

    原先在 `act()` 里内联，第 1 条身份写死「资深稳评工程师」、第 6 条结构示例写死稳评
    二级标题（4.1 合法性分析 / 6.1 量化评分指标体系）—— 复垦章顶着稳评身份、按稳评
    小节编号写。稳评逐字等于改动前；复垦换成复垦身份与复垦小节示例。
    """
    if domain == _RECLAMATION:
        return (
            "\n\n## 要求\n"
            "1. 你是土地复垦方案编制专家，根据专业知识决定本章每个小节下的内容深度\n"
            "2. 数据用「材料3：本项目数据」中提供的，没有的不编造\n"
            "3. 地名/村名/地块名必须来自项目数据，禁止使用LLM内部知识中的地名\n"
            "4. 写短句，像老工程师写报告，不要AI翻译腔\n"
            "5. 不用「第一/第二/第三」排比，不用「具有重要/切实/有力」等套词\n"
            "6. 🔴 子标题结构：严格用下方「本章结构规范」给出的编号标题"
            "（如 3.1 项目简介、3.4 项目区土地利用状况）。禁止自创数字编号标题，"
            "禁止用「六、」「八、」中文序号标题；更细的分点用（一）（二）（三）写在正文里，不用编号标题。\n"
            "7. 表格只在必要时使用，不要为填模板而加表\n"
        )
    return (
        "\n\n## 要求\n"
        "1. 你是一位资深稳评工程师，根据专业知识决定本章每个小节下的内容深度\n"
        "2. 数据用「材料3：本项目数据」中提供的，没有的不编造\n"
        "3. 地名/村名/社区名必须来自项目数据，禁止使用LLM内部知识中的地名\n"
        "4. 写短句，像老工程师写报告，不要AI翻译腔\n"
        "5. 不用「第一/第二/第三」排比，不用「具有重要/切实/有力」等套词\n"
        "6. 🔴 子标题结构：严格用下方「本章结构规范」/「本章必须使用的子标题」给出的编号标题"
        "（如 4.1 合法性分析、6.1 量化评分指标体系）。禁止自创数字编号标题，"
        "禁止用「六、」「八、」中文序号标题；更细的分点用（一）（二）（三）写在正文里，不用编号标题。\n"
        "7. 表格只在必要时使用，不要为填模板而加表\n"
    )


# ═══════════════════════════════════════════════════════════════
# `chapter_base._maybe_run_tool_round` 的工具表与决策提示按域取值
# ═══════════════════════════════════════════════════════════════
# 工具回合把后端能力当作 LLM 可调用的工具，先让模型自己决定「这一章要不要拉数据」。
# 🔴 注册表按 `ToolSpec.domains` 归属域（见 `app/agent/tools/registry.py`）：
#    稳评专有器物（扣分制评分 / 公众·部门问卷）标 `("stability",)`，不进复垦表；
#    `retrieve_rag` / `parse_kd_table` 两域共用（描述按域取）；复垦三件计算工具
#    （工程量测算 / 投资估算 / 适宜性评价）标 `("reclamation",)`，不进稳评表。
#    稳评拿到的工具表因此**逐字不变**，复垦拿到自己的一套。

def tool_round_schema(domain: str | None) -> List[Dict[str, Any]]:
    """工具回合交给模型的工具表（按域）。

    稳评：`app.agent.tools.ALL_TOOLS_SCHEMA` 全量（逐字不变）。
    复垦：`get_tools_schema("reclamation")` —— 复垦自己那几件（复用的 RAG 检索 /
          勘测定界解析 + 工程量测算 / 投资估算 / 适宜性评价）。
    """
    if domain == _RECLAMATION:
        from app.agent.tools import get_tools_schema
        return get_tools_schema(_RECLAMATION)
    from app.agent.tools import ALL_TOOLS_SCHEMA
    return ALL_TOOLS_SCHEMA


def tool_round_decision_system(domain: str | None) -> str:
    """工具回合「决策」调用的 system prompt（按域）。

    原先在 `chapter_base._maybe_run_tool_round` 里写死「你是一个稳评报告助手」——
    复垦一旦开放工具回合，这句会随决策 prompt 发给模型。稳评逐字不变；复垦换成复垦身份。
    """
    if domain == _RECLAMATION:
        return ("你是一个复垦方案助手。判断是否需要调用工具获取真实数据。"
                "需要时输出 tool call；不需要则直接简短回复'无需工具'。")
    return ("你是一个稳评报告助手。判断是否需要调用工具获取真实数据。"
            "需要时输出 tool call；不需要则直接简短回复'无需工具'。")


def tool_round_available_tools(domain: str | None, chapter_number: int) -> str:
    """工具回合决策 prompt 里「可用工具」清单（按域）。

    稳评：逐字等于改动前 `chapter_base._build_tool_decision_prompt` 内联的那段。
    复垦：只列复垦自己的工具（工程量测算/投资估算/适宜性评价 + 复用的 RAG/勘测定界）。
    """
    if domain == _RECLAMATION:
        return _reclamation_available_tools(chapter_number)
    return _stability_available_tools(chapter_number)


def _stability_available_tools(chapter_number: int) -> str:
    """稳评「可用工具」清单 —— 逐字搬运自改动前的内联实现。"""
    ch = chapter_number
    available = "可用工具：\n"
    if ch in (1, 4):
        available += "- parse_kd_table：获取地块/土地分类面积真实数据（第6章 表6-1），本表内容高度相关\n"
    if ch == 3:
        available += "- query_survey：获取公众问卷调查统计（身份/年龄/职业/了解/态度/诉求方式人数）\n"
        available += "- query_dept_survey：获取部门/单位问卷调查统计\n"
    if ch in (6, 8):
        available += "- calc_risk_scoring：获取风险评分（扣分制）与风险等级\n"
    else:
        available += "- calc_risk_scoring：如需风险评分数据可调用\n"
    available += "- retrieve_rag：正文需引用具体法规/规范/范文条文时调用"
    if ch == 3:
        available += "（quote法规依据、调查规范）"
    available += "\n- parse_kd_table：如需地块/面积数据可调用\n"
    return available


def _reclamation_available_tools(chapter_number: int) -> str:
    """复垦「可用工具」清单 —— 按复垦章（第4/6/7章是计算章）给引导。"""
    ch = chapter_number
    available = "可用工具：\n"
    if ch == 4:
        available += "- evaluate_suitability：计算评价单元适宜性加权总分与等别（一/二/三等地）\n"
    if ch == 6:
        available += "- calc_earthwork：按工程设计测算工程量（表土剥离/混凝土层/垫层/沟渠疏通）\n"
    if ch == 7:
        available += "- calc_investment：测算工程施工费/其他费用/监测管护费/预备费与静态·动态总投资\n"
    if ch in (1, 3, 4, 6):
        available += "- parse_kd_table：获取临时用地勘测定界地块/土地利用现状数据\n"
    available += "- retrieve_rag：正文需引用具体法规/规范/范文条文时调用\n"
    return available


# ═══════════════════════════════════════════════════════════════
# 主对话「身份串」按域取值（master 问候 / report.py 聊天兜底 system prompt）
# ═══════════════════════════════════════════════════════════════
# 这两处原先各写死一句稳评自我介绍 —— 复垦会话说「你好」会被当成「社会稳定风险评估
# 报告编制专家」，聊天兜底的 system prompt 也自称「社会稳定风险评估报告编制助手」，
# 都是拿稳评身份回答复垦用户。稳评（默认域）逐字等于改动前那两句；
# 招投标那句仍留在 master.py（招投标不经章目录，见 master._is_bidding_context）。

def chat_greeting(domain: str | None) -> str:
    """主对话「打招呼」（你好/您好/hi…）时按域回的自报家门。"""
    if domain == _RECLAMATION:
        return ("您好！我是土地复垦方案编制专家。请上传项目资料（PDF/图片）或描述"
                "项目基本信息（临时用地位置、面积、服务年限等），我可以帮您编制土地复垦方案。")
    return ("您好！我是社会稳定风险评估报告编制专家。请上传项目资料（PDF/图片）或描述"
            "项目基本信息（位置、面积、文号等），我可以帮您生成社会稳定风险评估报告。")


def chat_system_prompt(domain: str | None) -> str:
    """聊天兜底的 system prompt 前缀（`report.py` 的 WS 聊天用；调用点后接 data_context）。

    逐行等于改动前 `report.py` 写死的那段，只有「稳评」这层身份按域换。
    """
    if domain == _RECLAMATION:
        return ("你是土地复垦方案编制助手。\n"
                "你的职责：引导用户提供项目信息，解答复垦相关问题。\n"
                "如果用户发送了文件或图片，请确认已收到并简要说明文件内容。\n"
                "风格：专业但友好，简洁直接，不说废话。\n")
    return ("你是社会稳定风险评估报告编制助手。\n"
            "你的职责：引导用户提供项目信息，解答稳评相关问题。\n"
            "如果用户发送了文件或图片，请确认已收到并简要说明文件内容。\n"
            "风格：专业但友好，简洁直接，不说废话。\n")


# ═══════════════════════════════════════════════════════════════
# 正文配图（image_catalog.build_image_catalog）按域取值
# ═══════════════════════════════════════════════════════════════
# 「章 → 图规格」「图注」「图片 → 章」三件事都在此按域收口，调用点（image_catalog）
# 不写任何 `if domain == ...` 分支。稳评逐字委托既有实现；复垦走复垦自己的规格
# （`RECLAMATION_IMAGE_SPECS`，由业主 5 份真实方案逐图提取），绝不复用稳评那张表。

def chapter_image_specs(domain: str | None) -> Dict[int, List[Dict[str, Any]]]:
    """该域正文各章的「配图规格」表 `{章号: [{type,label,min,max,keywords}, ...]}`。

    稳评：委托 `image_catalog.CHAPTER_IMAGE_SPECS`（逐字不变）。
    复垦：取 `reclamation_definitions.RECLAMATION_IMAGE_SPECS`（章号 1/3/4/5 有条目，
          其余章缺省＝无正文图）。
    🔴 复垦绝不回落稳评那张表 —— 否则图片会被盖上「社会稳定风险评估流程图」这类稳评图注。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_IMAGE_SPECS
        return RECLAMATION_IMAGE_SPECS
    from app.services.image_catalog import CHAPTER_IMAGE_SPECS
    return CHAPTER_IMAGE_SPECS


# 🔴 正文配图的**落位锚点**：该(域,章)的图插在**含此关键词的小节**标题之后。
#    稳评**全部为 None** —— 稳评的落位仍在装配器那几处写死分支（第6章「地理位置」、
#    第8章「风险调查过程」）里，逐字不动；改成表反而有回归风险。
#    复垦按两份样本的图位定：第3章的「位置示意图」落在 3.1 项目简介 之后；
#    第4章的「现场影像资料及临时用地平面布置图」落在 4.1 土地损毁分析与评定 之后。
_RECLAMATION_BODY_IMAGE_ANCHOR: Dict[int, str] = {
    3: "项目简介",
    4: "土地损毁分析与评定",
}


def body_image_anchor(domain: str | None, chapter_number: int) -> Optional[str]:
    """该(域,章)正文配图的落位锚点（小节标题关键词）；`None` = 不自动落位。

    🔴 为什么需要它：装配器只在正文出现 `![图注]` 标记时才插图，而提示词
       **明令 LLM 不许写图片标记**（图片由系统统一放置）。于是「系统插标记」这一步
       必须自己做 —— 稳评有第6/8章两处写死的落位，复垦原先一处都没有，
       结果十章正文**一张图都进不去**（实测 2026-10-10 明远佳苑产物 media=0）。
    """
    if domain != _RECLAMATION:
        return None
    return _RECLAMATION_BODY_IMAGE_ANCHOR.get(int(chapter_number))


def image_caption(
    domain: str | None,
    chapter_number: int,
    category: str,
    *,
    rule_label: str = "",
    spec_label: str = "",
    first_spec_label: str = "",
) -> str:
    """已绑定到 `(域, 章号)` 的一张正文图该印什么图注。

    稳评：保持历史三档优先级 —— 材料规则标签 > 命中规格的标签 >
          按类别的兜底表（`IMAGE_CAPTION_FALLBACK`）> 首条规格标签。
          逐字等于改动前 `build_image_catalog` 的取值。
    复垦：命中规格 → 用规格自己的图名（如第3章「项目地理位置示意图」）；
          未命中任何规格 → 退回「章标题 + 示意图」的中性兜底
          （如第3章无规格命中时「项目概况示意图」）。绝不点名稳评器物。
    """
    if domain == _RECLAMATION:
        return spec_label or rule_label or f"{chapter_title(domain, chapter_number)}示意图"
    from app.services.image_catalog import IMAGE_CAPTION_FALLBACK
    return (
        rule_label
        or spec_label
        or IMAGE_CAPTION_FALLBACK.get(category)
        or first_spec_label
    )


def material_image_rule(
    domain: str | None,
    name: str,
    folder_hint: str = "",
    pdf_page=None,
) -> tuple:
    """① 确定性材料规则 → `(目标章号|None, 类别, 材料规则标签)`。

    稳评：委托 `image_catalog` 既有的 PDF 页规则 / 材料规则（逐字不变）。
    复垦：按复垦配图规格的关键词判定（复垦无「PDF 装订页序」口径，PDF 页不套规则）。
    """
    if domain == _RECLAMATION:
        return _reclamation_material_rule(name, folder_hint)
    from app.services.image_catalog import _pdf_page_rule, _match_material_rule
    if pdf_page:
        return _pdf_page_rule(pdf_page[0], pdf_page[1])
    return _match_material_rule(name, folder_hint)


def image_chapter_hint(domain: str | None, name: str, folder_hint: str = "") -> Optional[int]:
    """② 材料规则未命中时，按文件名/文件夹推「图片 → 正文目标章」；识别不出返回 None。

    稳评：委托 `image_catalog._classify_chapter_hint`（逐字不变）。
    复垦：关键词已在 `material_image_rule` 判过，此处不再判 —— 绝不借稳评那套
          写死第 6/7/8/9 章的文件夹提示，否则复垦图会被错绑到复垦的同号章。
    """
    if domain == _RECLAMATION:
        return None
    from app.services.image_catalog import _classify_chapter_hint
    return _classify_chapter_hint(name, folder_hint)


# 🔴 复垦绑定扫描的「泛名」章：第3章的『位置示意图』会被第4章『表土堆放区位置示意图』
#    里的同名片段误命中，故把第3章留到最后扫 —— 专名先落袋，泛名兜底。
_RECLAMATION_GENERIC_CHAPTER = 3


def _reclamation_hint_chapters() -> List[int]:
    """复垦绑定扫描的章顺序：第3章（泛名位置图）排最后，其余章升序在前。

    新增章自动纳入，无需手工维护顺序；只要不出现「专名里含另一章泛名关键词」的新情况，
    这个次序即稳。
    """
    from app.agent.reclamation_definitions import RECLAMATION_IMAGE_SPECS
    chs = sorted(RECLAMATION_IMAGE_SPECS)
    if _RECLAMATION_GENERIC_CHAPTER in chs:
        chs.remove(_RECLAMATION_GENERIC_CHAPTER)
        chs.append(_RECLAMATION_GENERIC_CHAPTER)
    return chs


def _reclamation_material_rule(name: str, folder_hint: str = "") -> tuple:
    """复垦：文件名 + 文件夹 → `(章号, 规格类别, 规格图名)`；识别不出 `(None, "", "")`。

    匹配文本 = **最后一级文件夹名 + 文件名**（与稳评 `_basename_text` 同口径）——
    不用整条路径，免得 `/资料/位置示意图/…` 里的上级目录名盖过真名。
    扫描顺序见 `_reclamation_hint_chapters`（专名章先、泛名章后）。
    """
    from app.agent.reclamation_definitions import RECLAMATION_IMAGE_SPECS
    parts = [p for p in str(folder_hint or "").replace("\\", "/").split("/") if p]
    text = f"{parts[-1] if parts else ''} {name or ''}".lower()
    for ch in _reclamation_hint_chapters():
        for spec in RECLAMATION_IMAGE_SPECS.get(ch, []):
            if any(k in text for k in spec["keywords"]):
                return (ch, spec["type"], spec["label"])
    return (None, "", "")
