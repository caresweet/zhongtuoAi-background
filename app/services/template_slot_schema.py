"""模板槽位类型学 —— 定义「模板正文里什么算可变槽位、什么算固定话术」。

背景：数据库模板（`templates` 表 id=4 洪泽）的章节正文是**带示例项目数据的成品文字**，
没有 `{{}}` 占位符。例如第3章含：

    洞庭湖路（S350-宁连一级路段）工程土地征收决策      ← 项目名（可变）
    稳评责任单位：江苏洪泽经济开发区管理委员会          ← 责任单位（可变）
    风险分析：随着《土地管理法》的修正…                ← 固定话术（不可动）

纯正则无法区分后两者。因此本模块只做两件事：

1. **正则探测器** `DETECTORS`：抓出「形状可辨认」的候选（文号/日期/面积/份数/百分比/金额）。
2. **固定话术词典** `FIXED_PHRASE_LEXICON`：明确豁免法律法规名、固定段首，避免误替换。

真正的「示例值 → 字段」映射由离线标注表（`template_profile.py`）给出，本模块是它的兜底与守卫。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# 槽位类型
# ═══════════════════════════════════════════════════════════════

SLOT_TYPES = {
    "doc_ref",     # 文号：洪拟征告〔2026〕7号
    "date",        # 日期：2026年6月2日
    "area",        # 面积：326342平方米 / 489.51亩
    "count",       # 数量：185户 / 204份 / 20人
    "pct",         # 百分比：100% / 34.04%
    "money",       # 金额：1234.56万元
    "region",      # 区划地名：朱坝街道三圩社区
    "unit_name",   # 单位名：淮安市洪泽区朱坝街道办事处
    "person",      # 人名
    "land_use",    # 土地用途/地类
    "table_no",    # 表号：表3-1
    "figure_no",   # 图号：图4-1
    "image_ref",   # 图片位
    "table_ref",   # 表格位
}


@dataclass(frozen=True)
class SlotRule:
    """一条槽位探测规则。"""
    slot_type: str
    pattern: re.Pattern
    # 候选字段（按单位/上下文择一）；空表示「只标记类型，字段由标注表决定」
    field_keys: tuple = ()
    unit_hint: str = ""          # ㎡ / 亩 / 户 / 份 / % / 万元
    priority: int = 50           # 数值越大越优先
    note: str = ""


# ═══════════════════════════════════════════════════════════════
# 正则探测器
# ═══════════════════════════════════════════════════════════════

DETECTORS: List[SlotRule] = [
    # ── 文号：任意「前缀〔年份〕序号号」，括号兼容 〔〕（）()【】 ──
    SlotRule(
        "doc_ref",
        re.compile(r'[一-龥A-Za-z]{1,10}\s*[〔（(\[【]\s*\d{4}\s*[〕）)\]】]\s*\d+\s*号'),
        ("doc_reference",), "", 90, "公告文号",
    ),
    # ── 日期 ──
    SlotRule(
        "date",
        re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日'),
        ("notice_date", "survey_date", "symposium_date", "commission_date", "fill_date"),
        "", 80, "日期，字段由上下文（公示/勘察/座谈/委托）择一",
    ),
    # ── 面积 ──
    SlotRule(
        "area",
        re.compile(r'(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(平方米|㎡|m2|公顷|亩)'),
        ("area_m2", "area_mu", "hectares"), "", 70, "按单位择字段",
    ),
    # ── 数量 ──
    SlotRule(
        "count",
        re.compile(r'(?<![\d.])(\d[\d,]*)\s*(户|人|份|个|名)'),
        ("household_count", "population_count", "total_samples", "dept_survey_count",
         "symposium_attendees"),
        "", 60, "按单位与上下文择字段",
    ),
    # ── 百分比 ──
    SlotRule(
        "pct",
        re.compile(r'(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%'),
        ("support_rate", "oppose_rate", "awareness_rate"), "%", 60, "按上下文择字段",
    ),
    # ── 金额 ──
    SlotRule(
        "money",
        re.compile(r'(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(万元|亿元|元/亩|万元/亩|元)'),
        ("funding", "compensation_standard", "compensation_per_mu"), "", 50, "按上下文择字段",
    ),
    # ── 表号 / 图号（只标记，不替换值；由组装器统一重编号）──
    SlotRule("table_no", re.compile(r'表\s*\d+\s*[-–—]\s*\d+'), (), "", 30, "表号"),
    SlotRule("figure_no", re.compile(r'图\s*\d+\s*[-–—]\s*\d+'), (), "", 30, "图号"),
]

# 单位 → 候选字段（面积类择字段用）
_UNIT_TO_FIELD: Dict[str, str] = {
    "平方米": "area_m2", "㎡": "area_m2", "m2": "area_m2",
    "公顷": "hectares",
    "亩": "area_mu",
    "户": "household_count",
    "人": "population_count",
    "份": "total_samples",
    "%": "support_rate",
}


# ═══════════════════════════════════════════════════════════════
# 固定话术词典 —— 命中即**不是槽位**
# ═══════════════════════════════════════════════════════════════

# 法律法规 / 标准 / 固定文件名（这些里的年份、数字绝不能当槽位替换）
FIXED_PHRASE_LEXICON = frozenset({
    "中华人民共和国土地管理法", "中华人民共和国土地管理法实施条例",
    "中华人民共和国突发事件应对法", "中华人民共和国城乡规划法",
    "中华人民共和国农村土地承包法", "中华人民共和国村民委员会组织法",
    "中华人民共和国行政诉讼法", "中华人民共和国行政复议法",
    "中华人民共和国民法典", "信访工作条例", "中华人民共和国政府信息公开条例",
    "重大行政决策程序暂行条例", "江苏省土地管理条例", "江苏省信访条例",
    "江苏省重大行政决策程序实施办法", "江苏省土地征收示范文本",
    "江苏省被征地农民社会保障办法", "江苏省重大决策社会稳定风险评估办法",
    "第三方社会稳定风险评估规范", "土地征收前社会稳定风险评估规范",
    "国有土地上房屋征收与补偿条例", "国家大规模群体性事件应急预案",
})

# 固定段首（这些句子是行业通用表述，不含项目专属数据）
FIXED_PREFIXES = (
    "风险分析：",
    "根据该事项风险因素的识别和风险等级的判断",
    "综上所述",
    "根据江苏省地方标准",
    "按照评估委托约定",
    "稳评采用的方法主要有",
    "本次社会稳定风险评估的决策事项为",
)

# 🔴 标准阈值上下文标记：这些段落里的数字是**法规/标准固定阈值**，不是项目数据。
#    必须用**表格/判定专用**的强标记，不能用「风险」这种在四性分析里到处出现的词，
#    否则会把正文里的「支持率100%」一并误豁免。
FIXED_CONTEXT_MARKERS = (
    "风险等级判定", "判定标准", "直接定性指标", "量化指标", "评分标准", "计分方法",
    "扣分", "反对率百分比", "风险发生概率", "群体性事件", "恶性案件", "个人极端事件",
    "攀比", "负面舆论", "恶意炒作", "维权人士", "敌对组织",
    "测评项目", "权重", "满分",
)

# 判定表专用：出现这些组合才认定为「标准阈值表」
_TABLE_MARKERS = ("测评项目", "权重", "评分标准", "风险等级判定", "反对率百分比")

# 「纯数值片段」判定（含 m2/km2 这类带字母的单位写法）
_NUMERIC_SPAN = re.compile(r'^[\d,.\s%a-zA-Z人份户个名万元亿亩㎡平方米]+$')

# 判定表**行**特征：表格行（含 | 分隔）+ 风险判定用语
_THRESHOLD_ROW_MARKERS = ("高风险", "中风险", "低风险", "群体性事件", "群众反对",
                          "恶性案件", "个人极端事件", "发生概率", "激烈程度", "得  分", "得分",
                          "不计分", "计分", "测评项目", "评分标准", "满意度",
                          "为高风险", "为中风险", "为低风险")

# 🔴 公司简介/资质段落：这些数字是**公司自身信息**（办公面积、从业人员数），
#    与具体项目无关，绝不随项目替换。
COMPANY_PROFILE_MARKERS = (
    "公司简介", "办公场所面积", "固定办公场所", "从业人员", "业务部门",
    "承接了", "营业执照", "资质证书", "经营范围", "登记注册",
)


def is_fixed_phrase(span: str, context: str = "") -> bool:
    """判断某段文本是否为固定话术（不应被当作槽位替换）。

    Args:
        span: 候选槽位文本。
        context: 其所在段落（用于判断是否落在法规清单/固定段首里）。

    Returns:
        True = 固定话术，**不得替换**。
    """
    s = str(span or "").strip()
    ctx = str(context or "")
    if not s:
        return True

    # 1) 法规全称命中 —— **只看片段本身**，不能因为段落里引用了法规就把整段数字都豁免
    #    （工作计划的段落里同样会引用法规，但其中的日期是项目数据）
    for name in FIXED_PHRASE_LEXICON:
        if name in s:
            return True

    # 2) 固定段首
    for pre in FIXED_PREFIXES:
        if ctx.strip().startswith(pre):
            return True

    # 3) 《》书名号内的内容一律保留
    if len(s) >= 6 and re.search(r'《[^》]*' + re.escape(s[:6]) + r'[^》]*》', ctx):
        return True

    # 4) 标准号（DB32/T4013-2021 之类）里的年份
    if re.search(r'DB\s*\d+\s*/?\s*T\s*\d+', ctx):
        if re.fullmatch(r'\d{4}年?', s):
            return True

    # 5) 标准阈值上下文（风险等级判定表 / 评分标准表）里的数字是固定阈值，不是项目数据。
    #    两种情形：① 段落是判定/评分表（含测评项目/权重等）；② 表格行 + 风险判定用语。
    _is_table_row = (' | ' in ctx) or ('|' in ctx)
    _strong_judgment = any(mk in ctx for mk in ("为高风险", "为中风险", "为低风险"))
    if (_strong_judgment or any(mk in ctx for mk in _TABLE_MARKERS)
            or (_is_table_row and any(mk in ctx for mk in _THRESHOLD_ROW_MARKERS))):
        # 只豁免「带数字/百分比」的短片段；地名等单位名仍允许作为槽位
        if _NUMERIC_SPAN.match(s):
            return True

    # 6) 法规清单段（段落含书名号 + 法规体裁词）：**仅豁免法规自身的施行/发布日期**，
    #    不能把同段里的项目日期一并豁免（工作计划的段落里也常引用法规）。
    if '《' in ctx and any(g in ctx for g in _REGULATION_GENRE):
        _is_date = bool(re.fullmatch(r'\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?', s))
        if _is_date:
            # 紧邻「施行/实施/生效/发布」或紧跟书名号括号 → 法规日期
            if (re.search(re.escape(s) + r'\s*(起)?\s*(施行|实施|生效|发布)', ctx)
                    or re.search(r'》\s*[（(][^）)]{0,24}' + re.escape(s), ctx)
                    or re.search(r'[（(]' + re.escape(s) + r'[^）)]{0,24}[）)]', ctx)):
                return True
        elif _NUMERIC_SPAN.match(s):
            return True

    # 7) 公司简介/资质段落：公司自身信息（办公面积、人员数），与项目无关
    if any(mk in ctx for mk in COMPANY_PROFILE_MARKERS):
        if _NUMERIC_SPAN.match(s):
            return True

    return False


# ═══════════════════════════════════════════════════════════════
# 字段择取
# ═══════════════════════════════════════════════════════════════

# 上下文关键词 → 字段（数量/日期/百分比类靠它消歧）
_CTX_FIELD_HINTS: List[tuple] = [
    (("支持率", "赞成率", "满意率"), "support_rate"),
    (("反对率",), "oppose_rate"),
    (("知晓率",), "awareness_rate"),
    (("公示", "公告期", "预公告"), "notice_date"),
    (("勘察", "勘查", "踏勘", "现场"), "survey_date"),
    (("座谈", "会议"), "symposium_date"),
    (("委托", "受托"), "commission_date"),
    (("问卷", "调查表", "有效问卷"), "total_samples"),
    (("部门", "单位问卷"), "dept_survey_count"),
    (("参会", "参加", "出席"), "symposium_attendees"),
    (("涉及", "农户", "户数"), "household_count"),
    (("人口", "人口数"), "population_count"),
    (("资金", "投资", "筹措"), "funding"),
    (("补偿标准", "区片综合地价"), "compensation_standard"),
]


def _qualifies(v) -> bool:
    """值是否可用于填充（拒绝空与占位符）。"""
    if v is None:
        return False
    s = str(v).strip()
    return bool(s) and not s.startswith("【")


def context_field(span: str, context: str, slot_type: str = "") -> Optional[str]:
    """**只看片段与上下文**推断字段（不要求事实卡已有值）。

    判定顺序：**单位优先**（户→户数、份→问卷数、%→支持率）→ 上下文关键词。

    Args:
        span: 命中的文本片段（如「71户」「2024年6月27日」）。
        context: 所在段落。
        slot_type: 槽位类型（date/count/pct/area…），用于收窄候选。

    Returns:
        字段名；推断不出返回 None。
    """
    ctx = str(context or "")
    sp = str(span or "")

    allowed = {
        "date": {"notice_date", "survey_date", "symposium_date", "commission_date", "fill_date"},
        "count": {"household_count", "population_count", "total_samples",
                  "dept_survey_count", "symposium_attendees"},
        "pct": {"support_rate", "oppose_rate", "awareness_rate"},
        "area": {"area_m2", "area_mu", "hectares"},
        "money": {"funding", "compensation_standard", "compensation_per_mu"},
    }.get(slot_type)

    # 🔴 单位优先：「71户」一定是户数，不该被段落里的其它词带偏
    m = re.search(r'(平方米|㎡|m2|公顷|亩|户|人|份|个|名)', sp)
    if m:
        fk = _UNIT_TO_FIELD.get(m.group(1))
        if fk and (not allowed or fk in allowed):
            # 「人」在座谈语境下是参会人数
            if fk == "population_count" and "座谈" in ctx:
                return "symposium_attendees"
            return fk

    for kws, fk in _CTX_FIELD_HINTS:
        if not any(k in ctx for k in kws):
            continue
        if allowed and fk not in allowed:
            continue
        return fk
    return None


def pick_field(rule: SlotRule, span: str, context: str, filled: dict) -> Optional[str]:
    """从候选字段中挑出**本项目确有值**的那一个。

    择优顺序：上下文关键词 → 单位映射 → 候选列表中第一个有值的。

    Args:
        rule: 命中的探测规则。
        span: 命中文本。
        context: 所在段落。
        filled: 事实卡。

    Returns:
        字段名；无法确定时返回 None（调用方记为 unmatched_slot）。
    """
    filled = filled or {}

    # 1) 上下文关键词
    ctx = str(context or "")
    for kws, fk in _CTX_FIELD_HINTS:
        if any(k in ctx for k in kws) and fk in (rule.field_keys or (fk,)) and _qualifies(filled.get(fk)):
            return fk

    # 2) 单位映射
    unit = (rule.unit_hint or "").strip()
    if not unit:
        m = re.search(r'(平方米|㎡|m2|公顷|亩|户|人|份)', span)
        unit = m.group(1) if m else ("%" if "%" in span else "")
    fk = _UNIT_TO_FIELD.get(unit)
    if fk and fk in (rule.field_keys or (fk,)) and _qualifies(filled.get(fk)):
        return fk

    # 3) 候选列表里第一个有值的
    for cand in (rule.field_keys or ()):
        if _qualifies(filled.get(cand)):
            return cand

    return None


def value_fits_unit(value, span: str) -> bool:
    """类型守卫：值必须与槽位的单位兼容（如 `%` 槽位不得填「489.51亩」）。"""
    sv = str(value or "")
    ss = str(span or "")
    if "%" in ss:
        return not re.search(r'(亩|平方米|㎡|户|人|份)', sv)
    if "亩" in ss:
        return not re.search(r'%|平方米|㎡', sv)
    if re.search(r'平方米|㎡', ss):
        return not re.search(r'%|亩', sv)
    return True


# ── 法规体裁词：段落里出现这些，说明该段是法规清单，其中的文号一律不是项目槽位 ──
_REGULATION_GENRE = ("条例", "办法", "意见", "通知", "规定", "细则", "标准", "规范",
                     "决定", "批复", "纲要", "规划", "法律", "法规", "暂行办法", "实施方案")


def is_regulation_citation(span: str, context: str) -> bool:
    """该文号是否为**法规文号**（而非项目公告文号）。

    判据（任一命中即为法规）：
      1. 所在段落含书名号《》且含法规体裁词
      2. 文号紧跟在《…》之后
      3. 文号后紧跟「文/文件/规定/要求/精神/明确」等引用语
      4. 发文前缀是法规发文机关（中办发/国办发/苏政发/苏政办发/淮政规/发改投资…），
         而**不是**项目公告前缀（X拟征告 / X征预告 / X预告 / X征地公告）

    例：「10.《关于加强新形势下…的意见》（中办发〔2021〕11号）」→ True
        「中办发〔2021〕11号文规定：…」→ True
        「洪拟征告〔2026〕7号 征收土地预公告」→ False
    """
    ctx = str(context or "")
    s = str(span or "").strip()
    m = re.search(r'([一-龥A-Za-z]{1,10})\s*[〔（(\[]\s*(\d{4})\s*[〕）)\]]\s*(\d+)\s*号', s)
    if not m:
        return False
    prefix = m.group(1)

    # 项目公告前缀（拟征告 / 征预告 / 征地公告 …）→ 明确不是法规
    if re.search(r'(拟征告|征预告|征地公告|征收公告|拟征收|征收土地预公告)$', prefix):
        return False

    # 3) 后紧跟引用语
    if re.search(re.escape(s) + r'\s*(文|文件|规定|要求|精神|明确|指出|强调)', ctx):
        return True

    # 1) 段落含书名号 + 体裁词
    if '《' in ctx and any(g in ctx for g in _REGULATION_GENRE):
        return True

    # 2) 紧跟在《…》之后
    if re.search(r'》\s*[（(]?' + re.escape(s), ctx):
        return True

    # 4) 发文前缀像法规发文机关
    if re.search(r'(办发|政发|政办发|政规|发改|国发|国办发|号令|政府令|办字|规字)', prefix):
        return True

    return False


def is_valid_slot_value(value, field_key: str) -> bool:
    """值有效性：非空、非占位符、纯数字类必须可解析。"""
    if not _qualifies(value):
        return False
    s = str(value).strip()
    if field_key in ("area_m2", "area_mu", "hectares", "household_count",
                     "population_count", "total_samples", "dept_survey_count",
                     "symposium_attendees"):
        return bool(re.fullmatch(r'\d+(?:\.\d+)?', s.replace(",", "")))
    if field_key in ("support_rate", "oppose_rate", "awareness_rate"):
        return bool(re.fullmatch(r'\d+(?:\.\d+)?%?', s.replace(",", "")))
    return True
