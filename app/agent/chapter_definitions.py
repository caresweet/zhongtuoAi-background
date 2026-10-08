"""Unified chapter definitions — single source of truth for all agents.

**十四章案卷结构**（与参照件《白马湖农场2号风电项目社会稳定风险评估报告（送审稿）》
完全一致；业主明确要求「后续所有文档的布局与标题名称都仿照这三个文件」）：

    1  编制说明
    2  编制依据
    3  江苏省XX市社会稳定风险评估评审表
    4  社会稳定风险评估工作报告
    5  突发性事件或不稳定因素处置应对工作预案
    6  项目基本情况
    7  稳评工作方案
    8  稳评方法与过程
    9  各利益相关者对项目的反映
    10 项目合法性、合理性、可行性、可控性分析
    11 分析预测风险源
    12 确定风险等级
    13 防范和化解风险的工作建议
    14 附件

二级标题编号用 `（一）（二）（三）`（照参照件 1号；业主明确「全报告体例照 1号」），
块内再分条用 `1. 2. 3.`。
**第11章、第13章的小节名不写死**：参照件里那两章的小节是风电项目专属
（「项目选址方面的风险」「噪声涉水生态专项防控措施」），土地征收等项目套不上，
因此保留「每个风险/措施一个小节」的写法，小节名按项目实际生成。

本章数据：
- CHAPTER_DEFINITIONS: 标题/描述/小节
- CHAPTER_RAG_QUERIES: RAG 检索词
- CHAPTER_DATA_REQUIREMENTS: 必需/建议/可选字段
- CHAPTER_FORMAT_RULES: 格式合规规则
- CHAPTER_SECTION_SPECS: 二级标题结构规范（内容要点/数据/图片规格）
- CHAPTER_KIND: 章节类型（table/review_table/fact/attachment），决定生成方式
"""

import re
from typing import Dict, List, Any

# 评审表章（第3章，表格章，数据驱动渲染）
REVIEW_TABLE_TITLE = "江苏省淮安市社会稳定风险评估评审表"  # 🔴 制式表名（模板固有），非项目数据
REVIEW_TABLE_KIND = "review_table"
# 兼容旧引用
COMPILATION_NOTE_TITLE = "编制说明"
APPENDIX_TITLE = "附件"
APPENDIX_KIND = "attachment"

# ═══════════════════════════════════════════════════════════════
# 章节类型：决定「怎么生成」
#   fact     → LLM 结合 RAG + 知识库 + 事实卡撰写
#   table    → 数据驱动渲染（评审表）
#   template → 固定模板渲染（不走 LLM）
#   attachment → 附件章（文件挂载）
# ═══════════════════════════════════════════════════════════════

CHAPTER_KIND: Dict[int, str] = {
    1: "fact",
    2: "fact",
    3: "table",
    4: "fact",
    5: "fact",
    6: "fact",
    7: "fact",
    8: "fact",
    9: "fact",
    10: "fact",
    11: "fact",
    12: "fact",
    13: "fact",
    14: "attachment",
}

# 报告总章数（单一事实源，其他模块一律引用此常量，不得写死）
TOTAL_CHAPTERS = 14
CHAPTER_NUMBERS = list(range(1, TOTAL_CHAPTERS + 1))
ALL_CHAPTERS = CHAPTER_NUMBERS

# ═══════════════════════════════════════════════════════════════
# 章号解析（**单一事实源**）—— 组装期的「按章分组」逻辑全靠它
# ═══════════════════════════════════════════════════════════════

_CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_num_to_int(text: str) -> int:
    """中文数字 → int（「十一」→11、「二十三」→23、「一百零五」→105）；纯数字原样转。

    🔴 各处的章号映射表原来**只写到「十」**，第 11 章及以后一律回落到 0 ——
       于是第 11～14 章的表号重编、同章表格去重**整体静默失效**
       （实测第12章因此留下两张一模一样的测评表）。
       任何需要「第X章」→章号的地方都必须走这里，不得再各写一份映射表。
    """
    s = str(text or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    section = number = 0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        else:
            return 0
    return section + number


# 🔴 章号后**必须**跟分隔符才算「章标题」。
#    正文里提到别的章时也以「第…章」开头（实测第12章正文
#    「第11章识别的主要风险因素，经问卷调查…」），旧写法把它当成了章标题，
#    后续同章去重 / 表号重编全部错位到「第11章」上。
_CHAPTER_HEAD_RE = re.compile(r'^第([一二三四五六七八九十百\d]+)章(?=[、．.：:\s]|$)')


def chapter_no_of_heading(text: str) -> int:
    """从「第X章 …」标题文本取章号；**不是章标题则返回 0**。"""
    m = _CHAPTER_HEAD_RE.match(str(text or "").strip())
    return cn_num_to_int(m.group(1)) if m else 0


# ═══════════════════════════════════════════════════════════════
# Core Definitions
# ═══════════════════════════════════════════════════════════════

CHAPTER_DEFINITIONS: Dict[int, Dict[str, Any]] = {
    1: {
        "title": "编制说明",
        "description": "稳评理由、稳评目的、稳评原则（体例为（一）（二）（三）三块，块内 1. 2. 3. 分条，不设 X.Y 小节）",
        "key_tables": [],
        # 🔴 本章体例特殊：参照件与知识库模板都用「（一）稳评理由 → 1. …」两级，
        #    没有 1.1/1.2 这种 X.Y 小节，故 sections 为空。
        # 🔴 本章体例特殊：用「（一）（二）（三）」作二级标题（等同其它章的 X.Y），
        #    块内用 1. 2. 3. 分条。sections 里写的是这三块的名称，供目录与提示词使用。
        "sections": ["（一）稳评理由", "（二）稳评目的", "（三）稳评原则"],
        "min_words": 1800, "max_words": 4000,
    },
    2: {
        "title": "编制依据",
        "description": "国家法律法规和文件、稳评编制依据、相关文件",
        "key_tables": [],
        "sections": ["（一）国家法律法规和文件", "（二）稳评编制依据", "（三）相关文件"],
        "min_words": 300, "max_words": 1200,
    },
    3: {
        "title": REVIEW_TABLE_TITLE,
        "description": "江苏省统一制式评审表：决策事项名称、适用程序、责任单位、实施单位、决策概况、各方意见、主要风险因素、降低风险措施、结论、评审意见、备案情况",
        "key_tables": ["社会稳定风险评估评审表"],
        "sections": [],
        "min_words": 0, "max_words": 0,
    },
    4: {
        "title": "社会稳定风险评估工作报告",
        "description": "项目基本情况、四性分析、不稳定风险因素、稳评结论及工作建议、参与人员签字",
        "key_tables": ["表4-1 决策可能出现的不稳定风险一览表"],
        "sections": ["（一）项目基本情况", "（二）四性分析", "（三）不稳定风险因素", "（四）稳评结论及工作建议", "（五）参与人员签字"],
        "min_words": 900, "max_words": 2500,
    },
    5: {
        "title": "突发性事件或不稳定因素处置应对工作预案",
        "description": "总则、指导思想、工作原则、组织领导、职责任务、工作流程、预警机制、应急响应、信息报送和处理、后期处置、保障措施、奖惩责任追究",
        "key_tables": [],
        "sections": ["（一）总则", "（二）指导思想", "（三）工作原则", "（四）组织领导", "（五）职责任务", "（六）工作流程", "（七）预警机制", "（八）应急响应", "（九）信息报送和处理", "（十）后期处置", "（十一）保障措施", "（十二）奖惩、责任追究"],
        "min_words": 1200, "max_words": 3500,
    },
    6: {
        "title": "项目基本情况",
        "description": "项目概况（项目名称、单位、位置、范围及面积、用途、权属与地类构成、资金筹措、实施周期）、利益相关者情况",
        "key_tables": ["表6-1 土地分类面积表"],
        "sections": ["（一）项目概况", "（二）利益相关者情况"],
        "min_words": 900, "max_words": 2500,
    },
    7: {
        "title": "稳评工作方案",
        "description": "指导思想、评估方法、评估内容、评估程序及步骤、评估时限（过程）、工作计划",
        "key_tables": [],
        "sections": ["（一）指导思想", "（二）评估方法", "（三）评估内容", "（四）评估程序及步骤", "（五）评估时限（过程）", "（六）工作计划"],
        "min_words": 800, "max_words": 2200,
    },
    8: {
        "title": "稳评方法与过程",
        "description": "风险调查的方法、风险调查的范围及对象、风险调查内容、风险调查过程（现场公示/实地勘察/座谈会）",
        "key_tables": [],
        "sections": ["（一）风险调查的方法", "（二）风险调查的范围及对象", "（三）风险调查内容", "（四）风险调查过程"],
        "min_words": 900, "max_words": 2500,
    },
    9: {
        "title": "各利益相关者对项目的反映",
        "description": "问卷调查情况（发放/回收/有效率、知晓率、支持率及问卷各题分项统计）、各方意见汇总情况（部门/基层组织/群众/网络舆情）",
        "key_tables": ["表9-1 问卷调查统计汇总表", "表9-2 单位问卷调查统计汇总表"],
        "sections": ["（一）问卷调查情况", "（二）各方意见汇总情况"],
        "min_words": 900, "max_words": 2500,
    },
    10: {
        "title": "项目合法性、合理性、可行性、可控性分析",
        "description": "项目的合法性分析、合理性分析、可行性分析、可控性分析",
        "key_tables": [],
        "sections": ["（一）项目的合法性分析", "（二）项目的合理性分析", "（三）项目的可行性分析", "（四）项目的可控性分析"],
        "min_words": 900, "max_words": 2200,
    },
    11: {
        "title": "分析预测风险源",
        "description": "按风险类别逐项分析（审批/用地/选址/资金/环境/交通/安全/信访舆论等，类别随项目类型而定），每项含风险描述、风险分析、风险程度判定",
        "key_tables": [],
        # 🔴 小节名不写死：参照件里是风电专属类别，土地征收等项目套不上，
        #    由 LLM 按本项目实际风险类别命名（写法保持「11.x 项目XX方面的风险」）。
        "sections": [],
        "dynamic_sections": True,
        "min_words": 900, "max_words": 2500,
    },
    12: {
        "title": "确定风险等级",
        "description": "风险分析方法、稳评风险等级确定量化指标评价体系、主要风险因素初始风险等级表",
        "key_tables": [
            "表12-1 直接定性指标", "表12-2 稳评风险等级确定量化指标评价体系",
            "表12-3 群众意见分析", "表12-4 主要风险因素初始风险等级表",
        ],
        "sections": ["（一）风险分析方法", "（二）稳评风险等级确定量化指标评价体系", "（三）主要风险因素初始风险等级表"],
        "min_words": 900, "max_words": 2500,
    },
    13: {
        "title": "防范和化解风险的工作建议",
        "description": "与第11章识别出的风险一一对应的防范化解措施（逐条给出措施与责任主体）",
        "key_tables": [],
        # 🔴 同第11章：小节名按本项目实际措施命名（写法保持「13.x 措施名」）。
        "sections": [],
        "dynamic_sections": True,
        "min_words": 800, "max_words": 2200,
    },
    14: {
        "title": "附件",
        "description": "位置示意图、征收（用地）公告、勘测定界报告、座谈会签到表、问卷调查表、专家评审材料等",
        "key_tables": [],
        "sections": [],
        "min_words": 0, "max_words": 0,
    },
}



def prepare_chapter4_markdown(md: str) -> str:
    """第4章 markdown 预处理：签字表改系统标记，并删掉 LLM 写的单位/时间行。

    🔴 两处都要做：
      · 「参与人员签字」的表格 → `[SYS_TABLE:4-5]`（由系统用勾选人员+手写签名图渲染；
        LLM 会把模板里的老团队名单抄进来，且签名图它给不出）；
      · 「编制单位/编制时间（编写单位/编写时间）」→ 删除（由系统渲染，
        否则会与系统那两行重复且口径不一致）。
    抽成共用函数是因为**正式组装与单章导出是两条路径**，只改一处会不一致。
    """
    import re as _re
    if not md or "签字" not in md:
        return md
    # 🔴 按**小节**定位：「（五）参与人员签字」标题之后的第一张 markdown 表就是签名表。
    #    不能只认表头 —— 模型常把最后一列写成「分工」而不是「签字」，
    #    也常在前面加一列「序号」，靠表头文字匹配时对时错。小节定位稳。
    _lines, _out, _i, _done, _in_sign = md.split("\n"), [], 0, False, False
    while _i < len(_lines):
        _s = _lines[_i].strip()
        if _s.startswith('#'):
            _in_sign = ('参与人员签字' in _s) or ('签字' in _s and '人员' in _s)
            _out.append(_lines[_i])
            _i += 1
            continue
        if _in_sign and not _done and _s.startswith('|') and '姓名' in _s:
            _out.append("[SYS_TABLE:4-5]")
            while _i < len(_lines) and _lines[_i].strip().startswith('|'):
                _i += 1
            _done = True
            continue
        _out.append(_lines[_i])
        _i += 1
    md = "\n".join(_out)
    md = _re.sub(r'(?m)^\s*(?:编制|编写)(?:单位|时间)\s*[：:][^\n]*\n?', '', md)
    return md

# ═══════════════════════════════════════════════════════════════════════════════
# 🔴 第6章「（一）项目概况」的**固定骨架** —— 标题由系统写死，模型只负责内容
# ═══════════════════════════════════════════════════════════════════════════════
# 业主口径（2026-09-21）：项目概况要像人工成品案卷第四章那样**平铺条目、每项一句话**
#   （「1.决策名称 / 2.决策主体 / 3.社会稳定风险评估责任单位 / 4.…实施单位 / …」）。
# 🔴 为什么必须写死在代码里：实测光靠提示词锁不住 —— `CHAPTER_SECTION_SPECS` 的
#    points 只是**建议**，模型照样起「1.项目名称」「2.项目相关单位」，还把责任单位
#    与实施单位并进一段、把「土地用途」写成四段。方案 A：标题系统说了算，
#    模型写的行按关键词归位到对应条名下。
# 🔴 条目名**照参照件《白马湖农场2号风电项目》第六章 6.1 项目概况**（实测 11 条）：
#      6.1.1 项目名称 / 6.1.2 项目相关单位 / 6.1.3 项目实施背景 / 6.1.4 项目地理位置
#      6.1.5 建设规模及内容 / 6.1.6 项目用地及周边情况 / 6.1.7 风电机组与现状民房的距离
#      6.1.8 限制因素及管控要求 / 6.1.9 项目投资 / 6.1.10 项目建设周期 / 6.1.11 相关手续办理情况
#   🔴 6.1.7 是**风电专属**（风机与民房的防护距离），土地征收套不上 ——
#      按人工成品案卷的对应条目换成「周边地块安置情况」。
#      这与「第11/13章小节名不写死」是同一条原则：骨架照搬，项目专属条目按项目类型替换。
#   编号沿用业主示范的 `1.1 / 1.2 / …`（对应白马湖的 6.1.1 / 6.1.2 / …）。
_CH6_OVERVIEW_SKELETON = (
    ("项目名称",         ("名称",)),
    ("项目相关单位",      ("相关单位", "责任单位", "实施单位", "主体")),
    ("项目实施背景",      ("背景",)),
    ("项目地理位置",      ("地理位置", "位置")),
    ("建设规模及内容",    ("规模", "范围", "面积", "附着物", "SYS_TABLE")),
    # 🔴 「周边」不能放在这一条 —— 它会把「周边地块安置情况」抢先命中（骨架顺序在前）
    ("项目用地及周边情况", ("现状", "用地")),
    ("周边地块安置情况",   ("安置",)),
    ("限制因素及管控要求", ("限制", "管控")),
    ("项目投资",         ("投资", "资金", "筹措")),
    ("项目建设周期",      ("周期", "公告期限")),
    ("相关手续办理情况",   ("手续",)),
)
_CH6_ANSWER_MAX_CHARS = 80   # 单项答案上限；超了记 issue（内容管不了，但要知道）
# 块内「XXX：内容」式标签行（如「决策主体：XX市XX区人民政府。」）
# 🔴 只认**行首短标签**（「实施周期：」「决策主体：」）—— 放宽到 24 字会把
#    「上述地块权属主体为XX街道XX社区和…」这类续写句也当成新条目。
_CH6_LABEL_RE = __import__('re').compile(r'^[^：:]{2,12}[：:]')


def prepare_chapter6_markdown(md: str) -> str:
    """第6章 markdown 预处理：「（一）项目概况」按固定骨架重排。

    🔴 标题一律用 `_CH6_OVERVIEW_SKELETON` 的 —— 模型写的 `####` 小标题
       只用来判断「这一段讲的是哪一项」，不进输出。业主：「能保证骨架和标题
       对上号么」→ 方案 A：把标题从模型手里拿走。

    🔴 归位按**模型的整块**走，不按行：逐行匹配实测会串味
       （「用地范围」那段收尾句带「权属主体」被吸到「决策主体」）。
       只有块内每行自带「XXX：」标签、且分别命中不同条名时才拆行
       （「决策主体：… / 责任单位：… / 实施单位：…」这种）。
       归不到任何一项的进「其他」保留，绝不丢内容。
    """
    import re as _re
    if not md:
        return md
    lines = md.split("\n")
    start = end = None
    for i, ln in enumerate(lines):
        if _re.match(r'^#{2,4}\s*（一）\s*项目概况', ln.strip()):
            start = i
        elif start is not None and _re.match(r'^#{1,3}\s', ln.strip()) and not ln.strip().startswith('####'):
            end = i
            break
    if start is None:
        return md
    if end is None:
        end = len(lines)

    # 🔴 `_match` 必须在**分块之前**定义 —— 下面判断「裸编号行是不是小标题」要用它。
    def _match(text: str):
        for title, kws in _CH6_OVERVIEW_SKELETON:
            if any(k in text for k in kws):
                return title
        return None

    def _match_all(text: str):
        """这一行/标题能命中哪些条目（判断「一块讲了两件事」）。"""
        return [t for t, kws in _CH6_OVERVIEW_SKELETON if any(k in text for k in kws)]

    # ① 切成「(模型小标题, [内容行])」块
    blocks, cur = [], None
    for ln in lines[start + 1:end]:
        s2 = ln.strip()
        if not s2:
            continue
        # 🔴 模型有时不用 `####`，改用**整行加粗**当小标题（`**3. 项目实施背景**`）——
        #    只认 `#` 会把这类标题当成正文，那几条就永远归不到位。
        _bold_head = _re.match(r'^\*\*\s*\d+(?:\.\d+)?\s*[.、]?\s*\S.*\*\*$', s2)
        # 🔴 还有第三种写法：**裸编号行** —— `3. 项目实施背景`，既没 `#` 也没加粗。
        #    只认前两种的话，这些行会被当**正文**留在 bucket 里原样印出去。
        #    实测第6章成品里就出现了两套标题混排：
        #        `1.项目名称`(骨架，Heading 3) 、`2.项目相关单位`(骨架)
        #        `3. 项目实施背景`(模型原文，普通段落)、`3.项目地理位置`(骨架)…
        #    —— 序号打架、顺序也乱（业主：「3.项目地理位置 这块的小标题第一个就是 5.」）。
        #    ⚠️ 但不能见「N.xxx」就吃：正文里的编号列表（「1. 已完成现场公示」）
        #       会被误伤。所以要求**短**(≤30 字) + **不以句读结尾** + **能命中骨架条目**。
        _bare_head = (
            not s2.startswith('#')
            and len(s2) <= 30
            and bool(_re.match(r'^\d+(?:\.\d+)?\s*[.、]\s*\S', s2))
            and not s2.endswith(('。', '；', '：', '，', ';'))
            and _match(_re.sub(r'^\d+(?:\.\d+)?\s*[.、]\s*', '', s2)) is not None
        )
        if s2.startswith('#') or _bold_head or _bare_head:
            _h = s2.strip('*').strip()
            _h = _re.sub(r'^\d+(?:\.\d+)?\s*[.、]?\s*', '', _h)
            cur = [_h, []]
            blocks.append(cur)
            continue
        if cur is None:
            cur = ["", []]
            blocks.append(cur)
        cur[1].append(s2)

    buckets = {t: [] for t, _ in _CH6_OVERVIEW_SKELETON}
    other = []
    for head, body in blocks:
        # ② 块内每行自带标签且命中不同条名 → 拆行；否则整块归位
        _head_item = _match(head)
        # 🔴 拆行条件：① 块内每行带「XXX：」标签且命中不同条目；或
        #    ② 块标题本身就同时命中两个条目（「资金筹措与实施周期」这种合并标题）。
        _split = bool(_CH6_LABEL_RE.match(body[0])) if body else False
        _lab = [((_match(l) if _CH6_LABEL_RE.match(l or '') else None) or _head_item, l) for l in body]
        if len(_match_all(head)) >= 2:
            _lab = [(_match(l) or _match_all(head)[-1], l) for l in body]
        _hits = {t for t, _ in _lab if t}
        if len(body) > 1 and len(_hits) >= 2:
            for t, l in _lab:
                (buckets[t] if t else other).append(l)
            continue
        t = _match(head) or _match(" ".join(body))
        (buckets[t] if t else other).extend(body)

    # ③ 按骨架顺序输出
    out = [lines[start]]
    # 🔴 序号必须**连续**：骨架里没内容的条目会被跳过（「不留空、不编造」），
    #    若直接用骨架下标当序号，报告里就会出现 1、2、4、6、7、10 这种跳号
    #    （业主看到会以为漏了内容）。所以用独立计数器。
    _seq = 0
    for title, _ in _CH6_OVERVIEW_SKELETON:
        # 🔴 只按「有没有真内容」判断：空串 / 纯空白 不算内容 —— 否则会留下
        #    **一个光秃秃的标题**，比缺这一条更糟。
        #    ⚠️ `[SYS_TABLE:6-1]` **算内容**（它就是要渲染的那张表），不能过滤掉；
        #       万一该表渲染不出来（提取表缺失），由组装器后处理第 6.2 步
        #       「删光标题」兜底，不在这里预判。
        _body = [x for x in buckets[title] if x.strip()]
        if not _body:
            continue                       # 没内容就不出这一条（不留空、不编造）
        _seq += 1
        # 🔴 用 `1. 2. 3.` 而**不是** `1.1 1.2` —— `_add_chapter` 有一条既有规则
        #    「`^#{3,4}\s+(\d+\.\d+\s)` → `## `」会把 `X.Y` 形式的标题
        #    **提升成二级标题**，于是第6章条目会跟「（一）（二）」同级、还带数字编号，
        #    破坏全报告「二级一律（一）（二）（三）」的体例（业主发现）。
        out.append(f"#### {_seq}.{title}")
        out.append("")
        for it in _body:
            out.append(it)
            out.append("")
    if other:
        out.append("#### 其他")
        out.append("")
        for it in other:
            out.append(it)
            out.append("")
    out.extend(lines[end:])
    return "\n".join(out)


def chapter6_answer_issues(md: str) -> list:
    """第6章项目概况里**超过一句话**的条目 → issues（供审核感知）。

    标题能锁死，但「每条不超过两句」是内容层面的，系统替不了 ——
    只能量出来交人工看。
    """
    import re as _re
    issues, _cur, _on = [], "", False
    for ln in (md or "").split("\n"):
        s2 = ln.strip()
        if _re.match(r'^#{2,4}\s*（一）\s*项目概况', s2):
            _on = True
            continue
        if _on and _re.match(r'^#{1,3}\s', s2) and not s2.startswith('####'):
            break                      # 出了（一）项目概况就停
        if s2.startswith('####'):
            _cur = s2.lstrip('#').strip()
            continue
        if _on and _cur and len(s2) > _CH6_ANSWER_MAX_CHARS:
            issues.append({"chapter": 6, "item": _cur,
                           "detail": f"{len(s2)} 字（上限 {_CH6_ANSWER_MAX_CHARS}），疑似写成了段落"})
    return issues


def get_chapter_kind(chapter_number: int) -> str:
    """返回章节类型：fact / table / template / attachment。"""
    return CHAPTER_KIND.get(chapter_number, "fact")


def is_template_chapter(chapter_number: int) -> bool:
    """是否为「模板渲染章」（不经 LLM，保证内容稳定）。"""
    return get_chapter_kind(chapter_number) == "template"


def get_chapter_title(chapter_number: int) -> str:
    """返回章节标题（唯一事实源）。"""
    return CHAPTER_DEFINITIONS.get(chapter_number, {}).get("title", f"第{chapter_number}章")


def get_all_chapter_titles() -> Dict[int, str]:
    """返回 {章号: 标题} 全量映射。"""
    return {n: d["title"] for n, d in CHAPTER_DEFINITIONS.items()}


def has_dynamic_sections(chapter_number: int) -> bool:
    """该章的小节名是否由 LLM 按项目实际生成（如第11、13章）。"""
    return bool(CHAPTER_DEFINITIONS.get(chapter_number, {}).get("dynamic_sections"))


# ═══════════════════════════════════════════════════════════════
# RAG Query Templates
# ═══════════════════════════════════════════════════════════════

CHAPTER_RAG_QUERIES: Dict[int, str] = {
    1: "编制说明 稳评目的 稳评原则 社会稳定风险评估 必要性 重大决策 受托",
    2: "编制依据 国家法律法规 稳评编制依据 相关文件 土地管理法 江苏省土地管理条例 DB32/T4013-2021",
    3: "社会稳定风险评估评审表 江苏省统一制式 决策事项名称 责任单位 实施单位 评估结论",
    4: "社会稳定风险评估工作报告 项目基本情况 四性分析 不稳定风险因素 评估结论 工作建议 参与人员签字",
    5: "突发性事件 不稳定因素 处置应对工作预案 总则 指导思想 工作原则 组织领导 职责任务 工作流程 预警机制 应急响应 信息报送 后期处置 保障措施 奖惩责任追究",
    6: "项目基本情况 项目名称 建设单位 项目位置 建设内容与规模 用地范围 面积 土地用途 权属 地类构成 资金筹措 实施周期 利益相关者",
    7: "稳评工作方案 指导思想 评估方法 评估内容 评估程序及步骤 评估时限 工作计划 稳评小组 调研方案",
    8: "稳评方法与过程 风险调查方法 调查范围及对象 调查内容 调查过程 现场公示 实地勘察 座谈会 问卷",
    9: "各利益相关者对项目的反映 问卷调查情况 发放回收 知晓率 支持率 满意度 各方意见汇总 部门意见 基层组织意见 群众诉求 网络舆情",
    10: "合法性分析 合理性分析 可行性分析 可控性分析 审批程序 用地合规 资金来源 群体性事件可能性",
    11: "分析预测风险源 项目审批风险 用地风险 选址风险 资金风险 环境风险 交通风险 安全风险 信访舆论风险 风险程度",
    12: "确定风险等级 风险分析方法 稳评风险等级确定量化指标评价体系 直接定性指标 群众意见分析 主要风险因素初始风险等级表 扣分制",
    13: "防范和化解风险的工作建议 报批程序 用地管理 资金保障 生态保护 交通疏导 安全防护 信访舆情 责任主体",
    14: "附件 位置示意图 公告 勘测定界报告 签到表 问卷调查表 专家评审材料",
}

# ═══════════════════════════════════════════════════════════════
# Data Requirements per Chapter
# ═══════════════════════════════════════════════════════════════

CHAPTER_DATA_REQUIREMENTS: Dict[int, Dict[str, Any]] = {
    1: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [("org_name", "责任单位"), ("doc_reference", "公告/批复文号")],
        "optional": [],
    },
    2: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [("doc_reference", "公告文号"), ("compensation_doc", "征地区片综合地价文件")],
        "optional": [("city", "所属市"), ("district", "所属区县")],
    },
    3: {
        "required": [
            ("project_name", "事项名称", "critical"),
            ("org_name", "稳评责任单位", "critical"),
        ],
        "recommended": [("implement_unit", "稳评实施单位"), ("doc_reference", "公告文号")],
        "optional": [
            ("org_leader", "责任单位负责人"), ("org_leader_title", "负责人职务"),
            ("org_contact", "责任单位联系人"), ("org_phone", "责任单位联系电话"),
            ("household_count", "涉及户数"),
        ],
    },
    4: {
        "required": [
            ("project_name", "项目名称", "critical"),
            ("risk_level", "风险等级", "critical"),
        ],
        "recommended": [
            ("doc_reference", "公告文号"), ("org_name", "责任单位"),
            ("location", "项目位置"), ("area_m2", "用地面积"), ("funding", "资金筹措"),
        ],
        "optional": [("score", "评估总分"), ("leader", "评估负责人")],
        "depends_on_chapters": [6, 9, 10, 11, 12, 13],
    },
    5: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [("org_name", "责任单位"), ("location", "项目位置")],
        "optional": [],
        "depends_on_chapters": [11, 13],
    },
    6: {
        "required": [
            ("project_name", "项目名称", "critical"),
            ("location", "项目位置", "critical"),
            ("area_m2", "用地面积", "critical"),
        ],
        "recommended": [
            ("doc_reference", "公告文号"),
            ("org_name", "责任单位"),
            ("implement_unit", "稳评实施单位"),
            ("area_mu", "用地面积（亩）"),
            ("land_use", "土地用途"),
            ("land_classification", "土地分类面积"),
            ("household_count", "涉及户数"),
            ("funding", "资金筹措"),
            ("notice_date", "公告发布日期"),
            ("notice_period", "公告期限"),
        ],
        "optional": [
            ("boundary_desc", "四至描述"), ("current_status", "地块现状"),
            ("ground_attachments", "地上附着物"), ("resettlement", "安置方式"),
            ("special_groups", "特殊群体情况"), ("scope_text", "范围描述"),
            ("purpose_text", "征收/用地目的"),
        ],
    },
    7: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [
            ("org_name", "责任单位"),
            ("commission_date", "委托日期"),
            ("notice_date", "公示时间"),
            ("survey_date", "实地勘察时间"),
            ("symposium_date", "座谈会时间"),
        ],
        "optional": [("leader", "评估负责人")],
    },
    8: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [
            ("location", "项目位置"),
            ("notice_date", "现场公示时间"),
            ("survey_date", "实地勘察时间"),
            ("symposium_date", "座谈会时间"),
            ("symposium_attendees", "座谈参会人数"),
        ],
        "optional": [("household_count", "涉及户数")],
    },
    9: {
        "required": [
            ("project_name", "项目名称", "critical"),
            ("total_samples", "问卷调查份数", "critical"),
        ],
        "recommended": [
            ("support_rate", "支持率"), ("oppose_rate", "反对率"),
            ("awareness_rate", "知晓率"), ("dept_survey_count", "单位问卷份数"),
        ],
        "optional": [
            ("villager_demands", "群众诉求"), ("grassroots_opinion", "基层组织意见"),
            ("dept_opinions", "部门意见"), ("online_opinion", "网络舆情"),
        ],
    },
    10: {
        "required": [
            ("project_name", "项目名称", "critical"),
            ("doc_reference", "公告文号", "critical"),
        ],
        "recommended": [
            ("org_name", "责任单位"), ("location", "项目位置"),
            ("land_use", "土地用途"), ("support_rate", "支持率"), ("funding", "资金来源"),
        ],
        "optional": [("compensation_doc", "补偿标准文件"), ("purpose_legal_basis", "法定依据")],
    },
    11: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [
            ("org_name", "责任单位"), ("location", "项目位置"),
            ("support_rate", "支持率（影响评分）"),
        ],
        "optional": [("compensation_standard", "补偿标准")],
        "depends_on_chapters": [6, 8, 9, 10],
    },
    12: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [
            ("support_rate", "支持率（影响评分）"), ("oppose_rate", "反对率"),
            ("total_samples", "问卷份数"), ("location", "项目位置"),
        ],
        "optional": [("score", "措施前得分"), ("risk_level", "风险等级")],
        "depends_on_chapters": [9, 11],
    },
    13: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [
            ("org_name", "责任单位（措施责任主体）"), ("location", "项目位置"),
        ],
        "optional": [("compensation_doc", "补偿标准文件")],
        "depends_on_chapters": [11],
    },
    14: {
        "required": [("project_name", "项目名称", "critical")],
        "recommended": [],
        "optional": [],
    },
}

# ═══════════════════════════════════════════════════════════════
# Format Compliance Rules per Chapter
# ═══════════════════════════════════════════════════════════════

CHAPTER_FORMAT_RULES: Dict[int, Dict[str, Any]] = {
    1: {"must_contain": ["稳评目的", "稳评原则"], "must_have_table": False},
    2: {"must_contain": ["法律法规", "编制依据", "相关文件"], "must_have_table": False},
    3: {"must_contain": ["事项名称", "稳评责任单位"], "must_have_table": True},
    4: {"must_contain": ["项目基本情况", "四性", "风险", "结论"], "must_have_table": True},
    5: {"must_contain": ["组织领导", "应急响应", "保障措施"], "must_have_table": False},
    6: {"must_contain": ["项目概况", "利益相关者"], "must_have_table": True},
    7: {"must_contain": ["指导思想", "评估方法", "评估程序", "工作计划"], "must_have_table": False},
    8: {"must_contain": ["调查方法", "调查范围", "调查内容", "调查过程"], "must_have_table": False},
    9: {"must_contain": ["问卷调查", "意见汇总"], "must_have_table": True},
    10: {"must_contain": ["合法性", "合理性", "可行性", "可控性"], "must_have_table": False},
    11: {"must_contain": ["风险"], "must_have_table": False},
    12: {"must_contain": ["风险分析", "量化指标", "风险等级"], "must_have_table": True},
    13: {"must_contain": ["措施", "建议"], "must_have_table": False},
    14: {"must_contain": ["附件"], "must_have_table": False},
}

# ═══════════════════════════════════════════════════════════════
# 章节二级标题结构规范 — 每章每个二级标题的内容要点、数据来源、图片规格
# 章节 agent 严格按此生成，禁止自由发挥标题/结构
# 图片规格尺寸单位 cm
# ═══════════════════════════════════════════════════════════════

CHAPTER_SECTION_SPECS: Dict[int, List[Dict[str, Any]]] = {
    1: [
        # 🔴 本章二级标题本身就是「（一）（二）（三）」，不是 X.Y 编号。
        {"title": "（一）稳评理由", "points": ["条目名称**原样照抄**：1.为维护人民群众的合法权益／2.为促进决策的民主化、科学化／3.为确保重大决策的顺利推进——条目行原样输出，**其下再各写 2～3 段**，先讲该理由的一般内涵与政策依据，再结合本项目（征收范围、涉及权属单位、群众关注点、决策程序）具体展开，不得只写一句泛泛之谈"], "data": [], "images": []},
        {"title": "（二）稳评目的", "points": ["条目名称**必须原样照抄、不得改写**：1.坚持以人民为中心，以民生需求为导向，妥善解决群众诉求，实现好、维护好、发展好人民群众的根本利益／2.稳评结论作为重大决策事项的重要依据，实现对社会稳定风险的预知预警预防，尽可能降低重大决策事项可能引发的风险／3.统筹考虑各方面关系，全面分析论证，科学客观稳评，反映决策事项可能引发的各种社会稳定风险及其影响程度，将稳评工作科学地嵌入到重大决策事项程序环节中。——条目行原样输出，**其下再各写 2～3 段展开**（引《土地管理法》第四十七条、中办发〔2021〕11号等依据并结合本项目）"], "data": [], "images": []},
        {"title": "（三）稳评原则", "points": ["条目名称**原样照抄**：1.客观公正性原则／2.科学评价性原则／3.全面系统性原则／4.权责统一性原则——**每条先单独一行原样写出原则名称（用 `1.` `2.` `3.` `4.`，不要用（一）（二））**，再另起段说明**（1～2 段）：写清该原则的含义、本项目如何落实（调查方式、覆盖对象、数据处理、责任分工）、落实后达到什么效果"], "data": [], "images": []},
    ],
    2: [
        {"title": "（一）国家法律法规和文件", "points": ["按 国家法律→行政法规 顺序列出，**只能引用「可引用法规清单」内的名称与文号**，禁止编造"], "data": ["city", "district"], "images": []},
        {"title": "（二）稳评编制依据", "points": ["稳评专门的规范与文件：DB32/T4013-2021、DB32/T4937-2024、江苏省重大决策社会稳定风险评估办法、被征地农民社会保障办法、征地区片综合地价标准"], "data": ["compensation_doc"], "images": []},
        {"title": "（三）相关文件", "points": ["本项目实际取得的文件：公告、勘测定界报告、用地预审与选址意见书、红线图等"], "data": ["doc_reference"], "images": []},
    ],
    3: [],
    4: [
        {"title": "（一）项目基本情况", "points": ["项目名称、责任单位、实施单位、位置、范围及面积、用途、资金筹措、实施周期的精简汇总"], "data": ["project_name", "location", "area_m2", "area_mu", "funding"], "images": []},
        {"title": "（二）四性分析", "points": ["合法性/合理性/可行性/可控性四方面结论的精简汇总（详见第10章）"], "data": ["doc_reference"], "images": []},
        {"title": "（三）不稳定风险因素", "points": ["依据第11章识别的风险因素，列出主要不稳定风险及等级，表4-1"], "data": [], "images": []},
        {"title": "（四）稳评结论及工作建议", "points": ["1.稳评结论（总分、风险等级判定依据、结论）；2.工作建议（若干条）"], "data": ["score", "risk_level"], "images": []},
        {"title": "（五）参与人员签字", "points": ["编写单位、编写时间、各参与人员姓名与职务（留签字空行）"], "data": ["leader"], "images": []},
    ],
    5: [
        {"title": "（一）总则", "points": ["编制目的、编制依据、适用范围"], "data": [], "images": []},
        {"title": "（二）指导思想", "points": ["维护政治安全、政权安全、意识形态安全和社会稳定"], "data": [], "images": []},
        {"title": "（三）工作原则", "points": ["统一领导分级负责、预防为主化解矛盾、依法处置防止激化、快速反应相互配合、以人为本、加强教育引导舆情"], "data": [], "images": []},
        {"title": "（四）组织领导", "points": ["成立处置应对工作领导小组并下设办公室；协调联络组/政策宣传组/应急救援组/后勤保障组/资金统筹组分工"], "data": ["org_name", "location"], "images": []},
        {"title": "（五）职责任务", "points": ["领导小组职责、办公室职责、各成员单位职责"], "data": [], "images": []},
        {"title": "（六）工作流程", "points": ["信息报告→先期处置→分级响应→现场处置→后期处置的流程"], "data": [], "images": []},
        {"title": "（七）预警机制", "points": ["信息搜集报送机制、信息报告内容、预警级别划分"], "data": [], "images": []},
        {"title": "（八）应急响应", "points": ["分级响应层级与启动条件，响应等级一律用中文（一级/二级/三级响应）"], "data": [], "images": []},
        {"title": "（九）信息报送和处理", "points": ["报送时限、报送内容、信息发布与舆情应对"], "data": [], "images": []},
        {"title": "（十）后期处置", "points": ["善后处理、调查评估、恢复秩序"], "data": [], "images": []},
        {"title": "（十一）保障措施", "points": ["通信保障、物资保障、资金保障、法制保障"], "data": [], "images": []},
        {"title": "（十二）奖惩、责任追究", "points": ["表扬情形、追究责任的若干情形"], "data": [], "images": []},
    ],
    6: [
        {"title": "（一）项目概况", "points": [
            "🔴 条目名**照参照件《白马湖农场2号风电项目》第六章 6.1 项目概况**，共 11 条：",
            "1.1 项目名称 —— 一行写项目全称；",
            "1.2 项目相关单位 —— 稳评责任单位、稳评实施单位各一行；",
            "1.3 项目实施背景 —— 决策缘起一两句；",
            "1.4 项目地理位置 —— 一两句 + 图6-1 位置示意图；",
            "1.5 建设规模及内容 —— 面积（㎡与亩）+ 涉及村组 + 土地用途；🔴 此处用「引导语 + [SYS_TABLE:6-1] + 解读句」承接，表6-1 土地分类面积表**由系统就地渲染**，正文不要手写该表；",
            "1.6 项目用地及周边情况 —— 现状地类 + 四至 + 周边；",
            "1.7 周边地块安置情况 —— 安置口径一句；",
            "1.8 限制因素及管控要求 —— 规划/用地管控要求一句；",
            "1.9 项目投资 —— 资金渠道一句；",
            "1.10 项目建设周期 —— 起止时间一句；",
            "1.11 相关手续办理情况 —— 前置手续办理情况一句。",
            "🔴 **11 条都要写**（缺哪条就少哪条标题）；每条**不超过两句**，禁止展开成段落、禁止重复堆砌地点/面积。"],
         "data": ["project_name", "org_name", "implement_unit", "location", "boundary_desc",
                  "area_m2", "area_mu", "land_classification", "land_use", "funding", "notice_date"],
         "images": [{"type": "map", "caption": "图6-1 项目位置示意图", "size": (14.0, 9.0)}]},
        {"title": "（二）利益相关者情况", "points": ["与项目有利益关系的各类群体的数量范围（涉及户数、权属单位、集体经济组织及其成员），以及各类群体的知晓参与情况"],
         "data": ["household_count", "location"], "images": []},
    ],
    7: [
        {"title": "（一）指导思想", "points": ["以人民为中心、源头预防化解矛盾的总体思路"], "data": [], "images": []},
        {"title": "（二）评估方法", "points": ["对照表法、实地考察法、问卷调查法、综合研判法等，逐项说明做法"],
         "data": [], "images": [{"type": "flowchart", "caption": "图7-1 社会稳定风险评估流程图", "size": (14.0, 9.0)}]},
        {"title": "（三）评估内容", "points": ["围绕合法性、合理性、可行性、可控性四方面各自要评估的内容"], "data": [], "images": []},
        {"title": "（四）评估程序及步骤", "points": ["九个步骤：明确目的→成立评估组制定方案→收集审阅资料→听取各方意见→全面评估论证→确定风险等级→编制报告→专家评审修改→正式提交"], "data": [], "images": []},
        {"title": "（五）评估时限（过程）", "points": ["按委托约定完成，工作程序：前往责任单位了解情况→对属地与被征地农户调研→问卷调查→汇总分析研判→起草修改报告"], "data": ["location", "commission_date"], "images": []},
        {"title": "（六）工作计划", "points": ["成立稳评小组（负责人/调研/报告编制/数据汇总/档案分工）＋调研方案（公告公示/现场勘察/座谈会及走访）＋风险预测＋报告编制审核＋评审报送"],
         "data": ["commission_date", "notice_date", "survey_date", "symposium_date", "leader"], "images": []},
    ],
    8: [
        {"title": "（一）风险调查的方法", "points": ["收集资料、现场查勘、张贴调查公告、问卷调查、座谈访谈等"], "data": [], "images": []},
        {"title": "（二）风险调查的范围及对象", "points": ["调查范围（覆盖全部直接利益群体）；调查对象：责任单位、集体经济组织/建设单位、被调查群众（含户数及分组分解）"], "data": ["location", "household_count"], "images": []},
        {"title": "（三）风险调查内容", "points": ["围绕合法性/合理性/可行性/可控性，从群众了解情况、可能影响、对项目实施的态度等方面调查"], "data": [], "images": []},
        {"title": "（四）风险调查过程", "points": ["按时间顺序写清：现场公示（公示点/公示期/公示周期）、实地勘察（地点/方式/时间）、座谈会（地点/时间/参会人员/参会人数/座谈情况）。时间一律取事实卡真实值，缺则进缺口清单"],
         "data": ["notice_date", "survey_date", "symposium_date", "symposium_attendees"],
         "images": [{"type": "notice", "caption": "图8-1 公示栏公示照片", "size": (7.5, 5.6)},
                    {"type": "photo", "caption": "图8-2 实地勘察照片", "size": (7.5, 5.6)},
                    {"type": "meeting", "caption": "图8-3 群众座谈会照片", "size": (7.5, 5.6)}]},
    ],
    9: [
        {"title": "（一）问卷调查情况", "points": ["个人问卷与单位问卷的发放数/有效回收数/回收率；知晓率、支持率、反对率；🔴 表9-1/表9-2 **由系统渲染**（题面照抄问卷原件、人数取自逐页清点）：本节内**单独占一行**输出 [TABLE:ch9_public_survey]（个人问卷）与 [TABLE:ch9_dept_survey]（单位/部门问卷，无部门问卷时省略），**不要手写表格、不要写「表9-X」表题行**；正文按「引导语 + 标记 + 解读句」承接"],
         "data": ["total_samples", "dept_survey_count", "support_rate", "oppose_rate", "awareness_rate"], "images": []},
        {"title": "（二）各方意见汇总情况", "points": ["1.政府及相关部门意见；2.基层组织意见；3.被调查群众诉求；4.网络媒体舆论意见"],
         "data": ["dept_opinions", "grassroots_opinion", "villager_demands", "online_opinion"],
         "images": [{"type": "opinion", "caption": "图9-1 网络舆情搜索截图", "size": (10.0, 7.0)}]},
    ],
    10: [
        {"title": "（一）项目的合法性分析", "points": ["1.符合法律法规要求；2.实施主体合法；3.项目目的合法；4.实施程序合法"], "data": ["doc_reference", "org_name", "land_use", "purpose_legal_basis"], "images": []},
        {"title": "（二）项目的合理性分析", "points": ["1.促进区域发展；2.符合群众利益；3.实施难度较低；最后总结具备合理性"], "data": ["district"], "images": []},
        {"title": "（三）项目的可行性分析", "points": ["1.资金来源可靠；2.各级政府支持；3.基层组织和群众认同；最后总结具有一定可行性"], "data": ["funding", "town"], "images": []},
        {"title": "（四）项目的可控性分析", "points": ["1.安全可控性；2.发生群体性事件的可能性；3.引发其他社会治安问题的可能性"], "data": ["support_rate"], "images": []},
    ],
    11: [],   # 小节名按项目实际风险类别动态生成
    12: [
        {"title": "（一）风险分析方法", "points": ["定性与定量相结合，风险等级分高、中、低三级；找出主要风险因素，预测估计发生概率与影响程度"], "data": [], "images": []},
        {"title": "（二）稳评风险等级确定量化指标评价体系", "points": ["表12-1 直接定性指标（无上述情形则说明）；表12-2 量化指标评价体系（合法性/合理性/可行性/可控性四维度权重与评分标准）；表12-3 群众意见分析；附评分规则与计算公式（扣分制）"], "data": [], "images": []},
        {"title": "（三）主要风险因素初始风险等级表", "points": ["表12-4 主要风险因素初始风险等级表；汇总说明各项风险等级及初始总得分与等级"], "data": ["score", "risk_level"], "images": []},
    ],
    13: [],   # 小节名按第11章风险一一对应的措施动态生成
}



# ═══════════════════════════════════════════════════════════════
# 三级标题（X.Y.Z）—— 对齐参照件《白马湖…送审稿》
#
# 参照件在多数二级标题下还有三级标题（4.2.1 合法性评估、8.4.1 现场公示、
# 9.2.1 党委政府或相关部门意见 …），本系统原先只到二级。
# 这里只登记**与项目类型无关**的三级标题；参照件里风电专属的
# （如 6.1.7 风电机组与现状民房的距离）一律不采用。
# ═══════════════════════════════════════════════════════════════

CHAPTER_SECTION_SUBS: Dict[int, Dict[str, List[str]]] = {
    4: {
        "（二）四性分析": ["合法性评估", "合理性评估", "可行性评估", "可控性评估"],
        "（四）稳评结论及工作建议": ["稳评结论", "工作建议"],
    },
    6: {
        "（一）项目概况": ["项目名称", "项目相关单位", "项目地理位置",
                          "用地范围与规模", "土地用途与征收目的", "资金筹措与实施周期"],
    },
    8: {
        "（二）风险调查的范围及对象": ["调查范围", "调查对象"],
        "（四）风险调查过程": ["现场公示", "实地勘察", "现场走访调查", "座谈会"],
    },
    9: {
        "（一）问卷调查情况": ["问卷调查数据汇总", "问卷调查数据分析"],
        "（二）各方意见汇总情况": ["党委政府或相关部门意见", "基层组织意见",
                                  "利益相关者诉求", "网络媒体舆论意见"],
    },
    10: {
        "（一）项目的合法性分析": ["评估主体合法性", "建设内容合法性", "程序合法性"],
        "（二）项目的合理性分析": ["符合区域发展需要", "符合群众利益", "实施难度相对可控"],
        "（三）项目的可行性分析": ["资金来源有保障", "各级政府支持", "基层组织和群众认同"],
        "（四）项目的可控性分析": ["安全可控性", "发生群体性事件的可能性",
                                  "引发其他社会治安问题的可能性"],
    },
}

def get_chapter_section_subs(chapter_number: int) -> Dict[str, List[str]]:
    """取某章的「二级标题 → 三级标题列表」映射。"""
    return CHAPTER_SECTION_SUBS.get(int(chapter_number), {})

def get_chapter_section_specs(chapter_number: int) -> List[Dict[str, Any]]:
    """获取章节二级标题结构规范（含内容要点/数据/图片规格）。"""
    return CHAPTER_SECTION_SPECS.get(chapter_number, [])


def format_sections_for_prompt(chapter_number: int) -> str:
    """把章节二级标题规范格式化为 prompt 注入文本。

    LLM 严格按此结构生成：每个二级标题的内容要点 + 应放的图片。
    """
    # 🔴 第1章体例特殊（二级标题是（一）（二）（三）且章首有前言），
    #    其结构规范由 master_orchestrator 的「第一章特定要求」块**单独**给出；
    #    这里返回空串，避免同一份结构规范在提示词里出现两遍 ——
    #    实测重复注入会让 LLM 把整章输出两遍（7 千字 vs 应 3 千余字）。
    if int(chapter_number) == 1:
        return ""

    sections = get_chapter_section_specs(chapter_number)
    if not sections:
        if has_dynamic_sections(chapter_number):
            n = int(chapter_number)
            return (
                f"\n## 📋 本章结构规范\n"
                f"🔴 本章小节**由你按项目实际生成**（参照件同章即按项目类型逐项展开）。\n"
                f"格式：`### （一）XXX`、`### （二）XXX` … 每个小节一个二级标题，**编号用（一）（二）（三）**。\n"
                + ("- 第11章：每个**风险类别**一个小节，标题写法「（×）项目××方面的风险」，"
                   "内容含风险描述＋风险分析＋风险程度判定。".format(n) if n == 11 else "")
                + ("- 第13章：与第11章的风险**一一对应**，每个**措施**一个小节，标题写法「（×）措施名」。" if n == 13 else "")
                + "\n每节必须有实质内容，禁止标题后空白。"
            )
        return ""
    lines = [
        "\n## 📋 本章结构规范（严格按此生成，禁止增删改二级标题）",
        "\n🔴 内容组织铁律：**所有正文内容必须写在二级标题（如 （一）、（二））下**。",
        "「## 第X章 XXX」一级标题之后禁止直接写任何正文段落，必须紧接着第一个二级标题。",
        "结构只能是：`## 第X章 XXX` → `### （一）×××` → 内容 → `### （二）×××` → 内容 ...",
        "一级标题后出现游离正文 = 不合格，必须把所有内容归入二级标题下。",
        "\n🔴 二级标题编号一律用 `（一）（二）（三）`，**写成 `### （一）×××`**，不要用 1.1/2.1 阿拉伯数字式；"
        "\n🔴 二级标题之下的正文再分条时用 `1.` `2.` `3.`，每条独立成行。",
    ]
    _subs = get_chapter_section_subs(chapter_number)
    for sec in sections:
        title = sec.get("title", "")
        points = "；".join(sec.get("points", []))
        data = "、".join(sec.get("data", []))
        lines.append(f"### {title}")
        if points:
            lines.append(f"- 内容：{points}")
        if data:
            lines.append(f"- 数据：{data}")
        # 🔴 三级标题：参照件多数二级标题下还有三级（4.2.1、8.4.1…），必须补，
        #    否则结构与参照件不一致、也不利于容纳内容。
        for i, sub in enumerate(_subs.get(title, []), 1):
            lines.append(f"    {i}. {sub}")
    lines.append("\n严格按上述二级标题顺序逐一撰写，每个标题下必须有实质内容，禁止标题后空白。")
    if _subs:
        lines.append("🔴 标注了分条的二级标题，必须**先按 `1.` `2.` `3.` 逐条列出再各自展开**，不得省略、不得合并。")
    # 🔴 图片统一由组装器按图库分类插入，不再要求正文写任何图片标记/图注。
    return "\n".join(lines)


def get_chapter_rag_query(chapter_number: int) -> str:
    """Get the RAG search query template for a chapter."""
    return CHAPTER_RAG_QUERIES.get(
        chapter_number,
        f"社会稳定风险评估报告 第{chapter_number}章"
    )


def get_chapter_data_requirements(chapter_number: int) -> Dict[str, List[str]]:
    """Get data requirements (required/recommended/optional) for a chapter."""
    return CHAPTER_DATA_REQUIREMENTS.get(chapter_number, {
        "required": ["project_name"],
        "recommended": [],
        "optional": [],
    })


def get_chapter_format_rules(chapter_number: int) -> Dict[str, Any]:
    """Get format compliance rules for a chapter."""
    return CHAPTER_FORMAT_RULES.get(chapter_number, {
        "must_contain": [],
        "must_have_table": False,
    })


# ═══════════════════════════════════════════════════════════════
# 文风约束：以范文为蓝本重写，去掉 AI 痕迹
# ═══════════════════════════════════════════════════════════════
# 🔴 业主口径（2026-09-22）：
#    「全找范文对应的内容参考，不应该存在让 llm 自己去编造的内容，
#      要 agent 去编造，并且要模仿范文的人语气不要有 ai 痕迹。」
#
#    两句要分开读：
#      · 「不应该让 LLM 自己编造」= 每段内容都要有**范文蓝本**，不许凭空生成；
#      · 「要 agent 去编造」= 内容仍由 agent 重写（不是照抄），
#        但要**模仿范文的人味** —— 照抄会带进别的项目的数据，自由发挥会带进 AI 腔。
#
#    下面这些「人味特征」是从知识库里的人工成品（清拟征告各号案卷）里归纳的，
#    不是凭空写的规矩。禁用词表来自实测：这几类词是 AI 腔最集中的标志。
FANWEN_STYLE_GUIDE = """
## 🖋 文风铁律：以范文为蓝本重写（不是自由发挥，也不是照抄）

上面【知识库·同类项目范文】里的段落，是同类章节在**别的项目里实际印出来**的样子。
你要做的是**照着它写**：它怎么写这一节，你就怎么写这一节，只把内容换成本项目的。

1. **结构照搬范文**：范文有几种写法、每节几段、先说什么后说什么 —— 照它的骨架来，不要自己另起一套。
2. **用词照搬范文**：范文用的实词优先用 —— 应、须、由…负责、按…规定、经调查、据…载明、
   以…为准、逐户、逐宗、登记造册、书面记录、签字确认、落实到位、闭环管理。
3. **只换事实**：范文里的项目名/地名/文号/数字**一个都不许抄**，全部换成本项目事实卡的值。

### 一、禁用词（出现即算不合格）

**程度副词类**：切实、有力、扎实、显著、极大地、充分地、深入地、高度重视、进一步加强、不断提升
**套话四字格**：多措并举、全方位、齐抓共管、标本兼治、久久为功、聚力攻坚、夯实基础、筑牢防线
**万能动词**：旨在、致力于、着眼于、聚焦于、赋能、助力、保驾护航、营造良好氛围
**空转名词**：重要意义、深刻内涵、强大动力、有力支撑、坚实基础、良好格局、长效机制
**开场套话**：随着…的不断推进、在…的大背景下、近年来…日益受到重视、当前形势下
**谄媚/提示句**：值得注意的是、需要强调的是、不可忽视的是、众所周知、不难看出、毋庸置疑
**收尾套话**：综上所述、总而言之、为…奠定了坚实基础、必将为…作出贡献

### 二、句式禁忌

| ❌ 不要 | ✅ 要 |
|---|---|
| 第一，…；第二，…；第三，… | 直接分条「1.…2.…」 |
| 首先…其次…最后… | 同上 |
| 不是…而是… / 不仅…更是… | 直陈「是…」 |
| 一方面…另一方面… | 分句直写 |
| 长排比（三个以上同构短句） | 拆成独立陈述句 |
| 破折号补充说明（—…—） | 换行或改逗号 |
| 每句换行、要点式罗列 | 成段书写，一句话 60–100 字 |
| 「我们认为」「可以看出」 | 「经调查」「据…载明」 |

### 三、正反例（照着右边改）

| ❌ AI 腔 | ✅ 范文写法 |
|---|---|
| 本项目具有重要意义，切实保障群众合法权益 | 该决策涉及被征收农户切身利益，须依法保障 |
| 多措并举、全方位推进，显著提升工作质效 | 由属地街道负责，逐户登记造册 |
| 通过深入调研分析，我们认为补偿标准合理 | 经调查，补偿标准符合区片综合地价规定 |
| 高度重视群众诉求，进一步加强沟通协调 | 群众诉求逐条登记，涉及补偿的书面答复 |
| 为项目顺利实施奠定了坚实基础 | 措施落实后，六项风险均在可控范围 |
| 值得注意的是，部分群众存在顾虑 | 座谈中有群众提出社保衔接问题 |

### 四、句子节奏

范文是**长句、书面语、一逗到底**（一句话常带 60–100 字），不是短促的要点罗列。
不要每句都换行，不要用项目符号代替段落。段落 3–6 句，段间自然过渡，不写小标题之外的总结句。

## ⚖️ 比文风更要紧：**凡下判断，必须说清具体事实**

🔴 业主两条口径：
   · 「整篇文章我都需要有 RAG 的情况去写」—— 素材来自知识库同类案卷，不是凭空想
   · 「**不要写那种依据什么什么的话语**」—— 不要「依据《XX》」「根据《XX》」「按照《XX》规定」式引经据典

   实测根因不是用词，是**下了判断却没有具体事实** —— 抽测 12 章 33 句评价性断言，
   22 句句内没有任何数字/专名/时间/主体，纯靠形容词撑着。

   **具体事实** = 谁（具名单位/部门）+ 做什么 + 什么数字／时间／地点。
   **写不出具体事实的判断句，要么改成陈述句，要么删掉。**

### 对照改

| ❌ 空判断 | ❌ 引经据典（同样不要） | ✅ 说清事实 |
|---|---|---|
| 补偿资金拟按洪泽区有关规定落实 | 补偿资金由区财政局按《江苏省征地补偿和被征地农民社会保障办法》纳入财政预算 | 补偿资金由洪泽区财政局在征收批准前拨付至指定专户，专款专用 |
| 本次征收总体实施难度相对可控 | 依据《土地管理法》第四十七条，本次征收条件成熟 | 本次征收涉及八里居委会 3 个村民小组、12 户，反对率 0% |
| 有利于完善区域基础设施配套 | 根据《城乡规划法》，该用地符合规划要求 | （说不出事实 —— **删掉**，不要写） |
| 该区域用地需求明确 | 按照《江苏省土地管理条例》有关规定执行 | 拟征收土地用于 XX 道路工程建设，已列入《洪泽区 2026 年城建计划》 |

### 🚫 不要这样写（引经据典式）

「依据《…》…」「根据《…》的规定…」「按照《…》有关要求…」「符合《…》第X条…」
—— 这类句式**把法规名当垫词**，读起来像法律意见书，不像专家报告。

**法规名只出现在它本身就是内容的地方**（第2章「编制依据」的法规清单、
转述预公告原文时）。正文里说事，就直接说事。

### 🚫 也不要这样写（空判断）

「有利于…」「有助于…」「符合…需要／要求／实际」「具备…能力／条件」
「具有…意义／作用」「为…提供／奠定／营造…」「与…相匹配／相适应」
「总体…可控／良好／可行」「切实…」「有力／有效保障…」

### ⚠️ 例外（这些要保留）

1. **转述材料原文** —— 预公告/批复原文里怎么写的就怎么引（那是材料，不是你编的）。
2. **法定口径的标准表述** —— 「实际征收范围以最终批准文件为准」。
3. **方法性说明** —— 「评估本次征收是否与当地发展规划相适应」（含「是否」是在交代评估内容）。
"""


# ═══════════════════════════════════════════════════════════════
# 字段元数据 + 「可问性」判定（弹窗清单的动态来源）
# ═══════════════════════════════════════════════════════════════
# 🔴 业主口径（2026-09-22）：
#    「弹窗的日期这些不能固定，正确的逻辑应该是 agent 觉得缺少什么找用户。」
#
#    旧做法：`CHAPTER_REQUIRED_FIELDS[ch]` 是一份**写死的清单**，
#    跟本章实际需要什么无关 —— 第7章固定问 14 个日期，其中好几个材料已经给了。
#
#    新做法：清单从 `CHAPTER_SECTION_SPECS[n]["data"]` **推导** ——
#    那是「本章每一节声明依赖哪些字段」，本来就是这个项目的事实源。
#    改 spec，弹窗自动跟着变，不用再去另一个地方同步一份硬编码清单
#    （这个仓库已经因为「两份事实源」出过多次事故）。
#
#    ⚠️ 但 spec 声明的 `data` 是「本章**依赖**哪些数据」，**不等于**「缺了要问用户」——
#       比如 `risk_level` 是 `scoring_service` 算出来的，问用户「风险等级是多少」是荒唐的。
#       所以还要一层「可问性」过滤，见 `NON_ASKABLE_FIELDS`。

# 字段 → (中文名, 类型, 正则, 最大长度, 示例)
# 类型：text / number / date / phone —— 前端据此渲染输入控件
FIELD_META = {
    "area_m2":             ("征收面积（平方米）", "number", r'^\d+(\.\d+)?$', 16, "326342"),
    "awareness_rate":      ("群众知晓率（%）",    "number", r'^\d+(\.\d+)?$', 5, "100"),
    "boundary_desc":       ("界址范围描述",       "text",   None, 120, "东至…南至…西至…北至…"),
    "city":                ("所属地级市",         "text",   None, 20, "XX市"),
    "compensation_doc":    ("补偿安置方案文号",   "text",   None, 40, "洪政发〔2026〕X号"),
    "dept_opinions":       ("部门意见",           "text",   None, 200, ""),
    "dept_survey_count":   ("部门问卷单位数",     "number", r'^\d+$', 6, "2"),
    "district":            ("所属区县",           "text",   None, 20, "XX区"),
    "grassroots_opinion":  ("基层组织意见",       "text",   None, 200, ""),
    "implement_unit":      ("稳评实施单位",       "text",   None, 40, ""),  # 默认空：值来自本报告所属公司
    "land_classification": ("土地分类面积表",     "text",   None, 200, ""),
    "land_use":            ("土地用途",           "text",   None, 40, "工业用地"),
    "leader":              ("稳评工作组组长",     "text",   None, 20, "张三"),
    "online_opinion":      ("网络舆情情况",       "text",   None, 200, ""),
    "oppose_rate":         ("群众反对率（%）",    "number", r'^\d+(\.\d+)?$', 5, "0"),
    "purpose_legal_basis": ("征收目的法定依据",   "text",   None, 200, ""),
    "risk_level":          ("社会稳定风险等级",   "text",   None, 10, "低风险"),
    "score":               ("风险评分",           "number", r'^\d+(\.\d+)?$', 6, "18"),
    "symposium_attendees": ("座谈会参会人员",     "text",   None, 200, "张三、李四、王五"),
    "town":                ("所属乡镇/街道",      "text",   None, 20, "XX街道"),
    "villager_demands":    ("群众诉求",           "text",   None, 200, ""),
    # ── 以下为「data_usage_map / CHAPTER_REQUIRED_FIELDS」里会出现、
    #    因而**可能进弹窗**的字段。业主：「弹窗的消息可以用中国标题么不要用英文字段」
    #    —— 漏一个，弹窗里就会露出 `doc_reference` 这种英文 key。
    "announcement_title":  ("公告标题",           "text",   None, 80, ""),
    "area_mu":             ("征收面积（亩）",     "number", r'^\d+(\.\d+)?$', 12, "100.00"),
    "attachment_note":     ("公告附图说明",       "text",   None, 80, ""),
    "commission_date":     ("委托日期",           "date",   None, 10, "2026-09-01"),
    "contact_name":        ("公告联系人",         "text",   None, 20, "张三"),
    "contact_phone":       ("公告联系电话",       "phone",  r'^(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})$', 13, "13800000000"),
    "decision_unit":       ("决策主体",           "text",   None, 40, "XX市XX区人民政府"),
    "doc_reference":       ("公告文号",           "text",   None, 40, "XX拟征告〔2026〕X号"),
    "funding":             ("资金筹措单位",       "text",   None, 40, "区财政统筹"),
    "household_count":     ("涉及户数",           "number", r'^\d+$', 8, "100"),
    "impl_contact":        ("实施单位联系人",     "text",   None, 20, "王五"),
    "impl_phone":          ("实施单位联系电话",   "phone",  r'^(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})$', 13, "13800000000"),
    "legal_basis_cited":   ("公告引用的法律法规", "text",   None, 200, ""),
    "location":            ("项目位置",           "text",   None, 60, "XX街道XX社区"),
    "map_date":            ("附图成图日期",       "text",   None, 20, ""),
    "map_plots":           ("附图示意的地块",     "text",   None, 60, ""),
    "map_scale":           ("附图比例尺",         "text",   None, 20, ""),
    "notice_date":         ("现场公示开始日期",   "date",   None, 10, "2026-09-03"),
    "notice_end_date":     ("现场公示结束日期",   "date",   None, 10, "2026-09-04"),
    "notice_period":       ("公告期限",           "text",   None, 40, ""),
    "notice_period_text":  ("公告期限（原文）",   "text",   None, 100, ""),
    "notice_publish_date": ("公告发布日期",       "date",   None, 10, "2026-09-14"),
    "org_contact":         ("责任单位联系人",     "text",   None, 20, "李四"),
    "org_leader":          ("责任单位负责人",     "text",   None, 20, "张三"),
    "org_leader_title":    ("负责人职务",         "text",   None, 20, "主任"),
    "org_name":            ("稳评责任单位",       "text",   None, 40, "XX区人民政府"),
    "org_phone":           ("责任单位联系电话",   "phone",  r'^(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})$', 13, "13800000000"),
    "other_matters_text":  ("其他事项（原文）",   "text",   None, 150, ""),
    "plan_approve_date":   ("方案审核日期",       "date",   None, 10, "2026-09-13"),
    "plan_date":           ("制定方案日期",       "date",   None, 10, "2026-09-02"),
    "preamble_text":       ("公告前言（原文）",   "text",   None, 150, ""),
    "project_name":        ("项目名称",           "text",   None, 60, "XX拟征告〔2026〕X号土地征收决策"),
    "publicity_start_date":("稳评公示开始日期",   "date",   None, 10, "2026-07-23"),
    "publish_date":        ("公告发布日期",       "date",   None, 10, "2026-09-14"),
    "purpose_text":        ("征收目的（原文）",   "text",   None, 150, ""),
    "report_draft_date":   ("报告编制开始",       "date",   None, 10, "2026-09-09"),
    "report_draft_end_date":("报告编制结束",      "date",   None, 10, "2026-09-10"),
    "report_month":        ("报告编写年月",       "text",   r'^\d{4}-\d{1,2}$', 7, "2026-09"),
    "review_date":         ("专家评审开始",       "date",   None, 10, "2026-09-11"),
    "review_end_date":     ("专家评审结束",       "date",   None, 10, "2026-09-12"),
    "risk_predict_date":   ("风险预测开始",       "date",   None, 10, "2026-09-07"),
    "risk_predict_end_date":("风险预测结束",      "date",   None, 10, "2026-09-08"),
    "scope_text":          ("征收范围（原文）",   "text",   None, 150, ""),
    "support_rate":        ("群众支持率（%）",    "number", r'^\d+(\.\d+)?$', 5, "100"),
    "survey_date":         ("实地勘察日期",       "date",   None, 10, "2026-09-05"),
    "symposium_date":      ("座谈会日期",         "date",   None, 10, "2026-09-06"),
    "symposium_location":  ("座谈会地点",         "text",   None, 60, ""),
    "total_samples":       ("问卷份数",           "number", r'^\d+$', 6, "50"),
    "work_arrangement_text":("工作安排（原文）",  "text",   None, 150, ""),
    "work_scope":          ("工作范围",           "text",   None, 60, ""),
}

# 🔴 **不问用户**的字段 —— 要么系统算得出，要么只可能来自材料。
#    缺了不该弹窗（用户也答不上来），交给正文的中性表述 + `gap_detector` 的缺口清单。
NON_ASKABLE_FIELDS = {
    # 系统计算
    "score", "risk_level", "publicity_end_date", "publicity_period_text",
    # 系统从「稳评公示开始日」推算，不问结束日
    "publicity_days",
    # 勘测定界专属 —— 只能来自那份 PDF
    "land_classification", "boundary_desc", "boundary_points", "land_owners",
    # 从 location 拆出来的，不单独问
    "city", "district", "town",
    # 由 area_mu 换算或材料给，不单独问（用户习惯说「亩」）
    "area_m2",
    # 🔴 预公告**原文摘录** —— 是材料里抄出来的段落，不是用户能填的「字段」。
    #    把它们弹给用户，用户只会看到一大段话然后不知所措。
    "preamble_text", "scope_text", "work_arrangement_text", "other_matters_text",
    "legal_basis_cited", "notice_period_text", "attachment_note",
    "announcement_title", "map_plots", "map_scale", "map_date",
    # `notice_publish_date` 已覆盖，`publish_date` 是它的材料别名
    "publish_date", "notice_period",
    # 🔴 `notice_publish_date` 全仓库**没有任何消费方**（实测扫过 app/ 与 scripts/）——
    #    问了也白问，用户填完哪儿都不去。真要落位得先给它找个位置，否则别问。
    "notice_publish_date",
    # 🔴 「各方意见」类 —— **不能没解析就让用户从零写**（业主 2026-09-22）：
    #    「群众意见你不能没有解析就去直接让用户去写，这一段是需要你发挥的，
    #      用户只能提供想法。」
    #    这四项在全仓库**没有任何材料来源**（只写在章节定义与 usage map 里，
    #    从来没人提取），所以永远是空的、永远弹给用户 —— 用户被迫自己编群众意见。
    #    正确做法：由 agent 依据**已解析的问卷统计 + 部门问卷 + 同类范文**来写；
    #    缺素材的地方交给 `gap_detector`（那里 agent 会先拟稿再请用户确认想法）。
    "villager_demands", "dept_opinions", "grassroots_opinion", "online_opinion",
    # ── 🔴 2026-09-29 补齐：**资料解析已能产出**或**纯派生文本**的字段，一律不问 ──
    #    业主：「要解析所有的资料…而且不会再问之前需要确认的问题」。
    "village",                    # 与 town 同理，从 location/征收范围里拆
    "land_ownership",             # 勘测定界权属构成（派生文本）
    "funding_amount",             # 资金筹措金额（材料正则抽取，用户不填数字）
    "symposium_time",             # 座谈会时间（签到表手写）
    "symposium_attendee_count",   # 参会人数（由名单推出）
    "symposium_date_text",        # 座谈会日期中文写法（派生）
    "survey_date_text",           # 调查日期中文写法（派生）
    "questionnaire_date",         # 问卷调查日期（问卷手写，资料里有就以资料为准）
    "questionnaire_date_text",    # 同上·中文写法
    "dept_questionnaire_date",    # 部门问卷日期（部门问卷手写）
    "review_meeting_date",        # 专家评审日期（评审会签到表手写）
    "review_meeting_location",    # 专家评审地点（同上）
    "review_meeting_attendees",   # 专家评审名单（同上）
    # 座谈记录原文（PDF 解析产出，不是用户能填的「字段」）
    "public_demands", "discussion_text", "conclusion_text",
}


def _log_missing_field(key: str) -> None:
    """字段没登记进 `FIELD_META` 时报一声（弹窗会退化成泛化中文名）。"""
    import logging as _lg
    _lg.getLogger(__name__).warning(
        f"[FIELD_META 缺登记] {key!r} —— 弹窗将显示「补充信息」，请补一条中文元数据")


def chapter_need_fields(chapter_number: int) -> list:
    """本章「该问用户什么」的字段清单（六元组，与 `_missing_for` 同构）。

    = (`CHAPTER_SECTION_SPECS[n]` 声明依赖的字段 ∪ `CHAPTER_REQUIRED_FIELDS[ch]`)
      − `NON_ASKABLE_FIELDS`

    元数据优先取 `CHAPTER_REQUIRED_FIELDS`（那里手写的最准），
    缺的用 `FIELD_META` 补；两处都没有的字段按名字后缀猜类型，不丢字段。
    """
    n = int(chapter_number)
    keys: list = []
    for sec in (CHAPTER_SECTION_SPECS.get(n) or []):
        for k in (sec.get("data") or []):
            if k not in keys:
                keys.append(k)

    specs = {}
    for spec in _CHAPTER_REQUIRED_FIELDS_REF().get(n, []):
        try:
            specs[spec[0]] = tuple(spec)
        except (TypeError, IndexError):
            continue
    for k in specs:
        if k not in keys:
            keys.append(k)

    out = []
    for k in keys:
        if k in NON_ASKABLE_FIELDS:
            continue
        if k in specs:
            out.append(specs[k])
            continue
        meta = FIELD_META.get(k)
        if meta is None:
            # 🔴 兜底**绝不能回落到英文 key** —— 弹窗上写 `doc_reference`，
            #    业主看不懂（「弹窗的消息可以用中国标题么不要用英文字段」）。
            #    宁可给个泛化中文名，同时在日志里点名，让运维把登记补上。
            _log_missing_field(k)
            meta = ("补充信息", "date" if k.endswith("_date") else "text", None, 100, "")
        out.append((k, meta[0], meta[1], meta[2], meta[3], meta[4]))
    return out


def _CHAPTER_REQUIRED_FIELDS_REF() -> dict:
    """延迟取 `CHAPTER_REQUIRED_FIELDS`。

    🔴 它定义在 `app.services.report_workflow` 里，而那边 import 本模块 ——
       模块级直接 import 会成环。这里延迟到调用时再取。
    """
    from app.services.report_workflow import CHAPTER_REQUIRED_FIELDS
    return CHAPTER_REQUIRED_FIELDS
