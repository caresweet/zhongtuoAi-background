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

🔴 默认域（stability）的取值逐字等于改动前的实现 —— 这是「稳评行为不变」的依据。
   故上列多数函数在稳评分支**直接委托**给既有实现，不复制第二份。
"""

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
    """该域「附件章」的章号；没有附件章返回 None。

    稳评第 14 章 = 附件（附件清单 + 图片材料）。复垦 10 章无附件章。
    """
    if domain == _RECLAMATION:
        return None
    from app.agent.chapter_definitions import TOTAL_CHAPTERS
    return TOTAL_CHAPTERS


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
            # 封面落款两个抬头（各 6 字 + 冒号，天然对齐）
            "org_label": "复垦责任主体：",
            "implement_label": "复垦实施单位：",
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


def cover_type_line(domain: str | None) -> str:
    """封面副标题（报告类型那一行，带书名号）。

    稳评逐字等于业主 2026-09-21 口径的「《社会稳定性风险评估》」；复垦换成复垦方案名。
    """
    if domain == _RECLAMATION:
        return "《土地复垦方案》"
    return "《社会稳定性风险评估》"


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


def requirements_block(domain: str | None, min_words: int, max_words: int) -> str:
    """单章提示词末段的「写作要求」块（按域取）。

    复垦：返回复垦口径的要求文本（不含稳评的规范/口径）；
    稳评：返回空串 —— 稳评那份是 `master_orchestrator` 的内联正文，
    逐字保持不变，由调用点继续使用内联版本。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import render_reclamation_requirements
        return render_reclamation_requirements(min_words, max_words)
    return ""


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
