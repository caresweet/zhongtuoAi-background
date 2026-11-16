"""Strict content guardrails for generated report chapters.

Enhanced with:
- Fabricated data detection (numbers not traceable to user-provided data)
- Numeric range validation (no negatives, plausible ranges)
- Opposition rate checks (must be 0% for land acquisition projects)
- Overly precise data detection (4+ decimal places → likely fabricated)
- Hallucinated regulation references
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Optional, Set

# 只对稳评生效的检查名。复垦报告没有「支持率/户数/补偿」这类概念，
# 且年份不该被钉死 —— 不加门控就会误报。
STABILITY_ONLY_CHECKS: set[str] = {
    "numeric_ranges",     # 「征地项目支持率应为 100%」「不应有反对率」
    "data_validity",      # 「年份错误，应为 2026 年」等写死年份的判据
    "required_materials",  # 营业执照/稳评平台备案等稳评材料清单
}

# ═══════════════════════════════════════════════════════════════
# Centralized AI buzzword list — single source of truth
# Import this from other files instead of duplicating
# ═══════════════════════════════════════════════════════════════

AI_BUZZWORDS = [
    '具有重要意义', '切实保障', '多措并举', '统筹推进',
    '综上所述', '有力支撑', '奠定了坚实基础', '提供了有力保障',
    '注入了强劲动力', '夯实基础',
    # 公文/宣传腔（实测 2026-10-28 第1章「稳评理由」整段都是这类）：
    # 「把重大事项晾晒在最广大群众的监督之下」「集中民智、凝聚民心」「搭起互动桥梁」
    '集中民智', '凝聚民心', '晾晒', '互动桥梁', '有效组织实施',
]

AI_BUZZWORD_PATTERNS = [
    (r'具有重要意义', "AI套词-具有重要意义"),
    (r'切实保障', "AI套词-切实保障"),
    (r'多措并举', "AI套词-多措并举"),
    (r'统筹推进', "AI套词-统筹推进"),
    (r'夯实基础', "AI套词-夯实基础"),
    (r'综上所述', "AI套词-综上所述"),
    (r'有力支撑', "AI套词-有力支撑"),
    (r'奠定了坚实基础', "AI套词-奠定了坚实基础"),
    (r'提供了有力保障', "AI套词-提供了有力保障"),
    (r'注入了强劲动力', "AI套词-注入了强劲动力"),
    (r'(集中民智|凝聚民心|互动桥梁|有效组织实施)', "AI套词-公文宣传腔"),
    (r'晾晒', "AI套词-晾晒"),
    (r'全方位[、，]多层次[、，]宽领域', "AI套词-三字排比"),
    (r'多维度[、，]全方位[、，]深层次', "AI套词-三字排比"),
    (r'系统[、，]全面[、，]深入', "AI套词-三字排比"),
    (r'第一[，,、].*第二[，,、].*第三', "AI套词-工整排比"),
    (r'一是[，,、].*二是[，,、].*三是', "AI套词-工整排比"),
    (r'通过系统识别', "AI套词-机器翻译腔"),
    (r'依据指令要求', "AI套词-机器翻译腔"),
    (r'经综合分析', "AI套词-机器翻译腔"),
    (r'据调查显示', "AI套词-机器翻译腔"),
]

ALLOWED_MISSING_MARKER = r"【待用户补充：[^】]+（对应章节：[^】]+）】"

# ═══════════════════════════════════════════════════════════════
# Original blocking patterns (placeholder/residue/chatbot checks)
# ═══════════════════════════════════════════════════════════════

BLOCKING_PATTERNS: List[Tuple[str, str]] = [
    (r"待补充(?!：)", "未完成占位表达"),
    (r"后续提供|稍后补充|后期提供", "未完成资料表达"),
    (r"需要补充|需补充", "未完成资料表达"),
    (r"请提供|请补充|请填写", "对话式指令残留"),
    (r"根据实际情况|视情况而定", "泛化不确定表达"),
    (r"具体.*?待定|暂未确定|尚未明确", "待定表达"),
    # 🔴 只在「由/责成 + 相关部门 + 动词」这种把责任推给泛称时才算泛化；
    #    「党委政府或相关部门意见」「与区级相关部门沟通」是正常表述
    (r"(?:由|责成|督促|要求)\s*(?:有关|相关)部门\s*"
     r"(?:应|应当|要|必须|负责|及时|加强|落实|处理|解决)",
     "责任主体泛化表达"),
    (r"\{\{[^}]+\}\}|____+|<[^>]{1,50}>", "占位符残留"),
    (r"\[.*?\]\(.*?\)", "Markdown链接残留"),
    (r"好的[，,]|当然可以[，,]|下面我来|我将为您", "口语/对话式表达"),
    (r"哈哈|呵呵|嘻嘻|yyds|666|给力", "网络或口语表达"),
    (r"我们认为|我们建议|笔者认为", "第一人称主观表达"),
    (r"以上内容仅供参考|以上是.*?的内容", "呈现式表达残留"),
]

# ═══════════════════════════════════════════════════════════════
# 🔴 NEW: Data validity blocking patterns
# ═══════════════════════════════════════════════════════════════

DATA_VALIDITY_PATTERNS: List[Tuple[str, str]] = [
    # Negative numbers in data contexts
    (r'(?<!\d)-(\d+(?:\.\d+)?)\s*(?:分|人|户|亩|㎡|平方米|万元|%|元|公顷)',
     "出现负数数据"),
    # Opposition rate > 0% (land acquisition projects must have 0% opposition)
    # 🔴 限定在同一句内，避免「反对率为0%。…3家单位」跨句误抓
    (r'反对(?:率|人数|比例)[^。；\n]{0,16}?(?:[1-9]\d*(?:\.\d+)?\s*%|[1-9]\d*人)',
     "征地项目出现反对率/反对人数，合规项目不应有反对"),
    # Overly precise numbers (4+ decimal places → likely fabricated)
    (r'(?<!\d)\d+\.\d{4,}(?!\d)', "数据过于精确（4位以上小数），疑似编造"),
    # Scores exceeding 100
    (r'(?<!\d)1[0-9]{2,}\s*分', "评分超过100分，超出合理范围"),
    # Year errors — not 2026
    (r'202[0-57-9]年', "年份错误，应为2026年"),
]

# ═══════════════════════════════════════════════════════════════
# 🔴 NEW: Fabricated regulation detection
# ═══════════════════════════════════════════════════════════════

# Known valid regulation patterns (whitelist)
#
# 🔴 **不写死**：从 data/regulation_whitelist.json（按地区维护）汇总；
#    新增省/市法规只需改 JSON，无需动代码。另附少量跨地区通用条目。
_UNIVERSAL_REGULATIONS = {
    "国有土地上房屋征收与补偿条例",
    "大中型水利水电工程建设征地补偿和移民安置条例",
}


def _reg_keys(item: str) -> Set[str]:
    """把「《某办法》（苏政发〔2021〕87号）」拆成 {'某办法', '苏政发〔2021〕87号'}。

    同时收三类「文号形态」，否则正文引用它们会被误判成编造：
      - 令号：国务院令第743号   → '第743号'
      - 标准号：DB32/T4013-2021 → 'DB32/T4013-2021'（去空格）
    """
    s = str(item or "").strip()
    keys: Set[str] = set()
    for name in re.findall(r'《([^》]+)》', s):
        if name.strip():
            keys.add(name.strip())
    for num in re.findall(r'[一-龥A-Za-z]{1,12}\s*〔\s*\d{4}\s*〕\s*\d+\s*号', s):
        keys.add(re.sub(r'\s+', '', num))
    # 🔴 令号（国务院令第743号 / 省政府令第XX号）
    for ling in re.findall(r'第\s*\d+\s*号', s):
        keys.add(re.sub(r'\s+', '', ling))
    # 🔴 标准号（DB32/T 4013-2021 → DB32/T4013-2021）
    for std in re.findall(r'[A-Z]{2,}\s*/\s*T\s*\d+\s*[-—–－]\s*\d+', s):
        keys.add(re.sub(r'\s+', '', std))
    if not keys and s:
        keys.add(s)
    return keys


def _load_known_regulations() -> Set[str]:
    import json
    from pathlib import Path
    keys: Set[str] = set(_UNIVERSAL_REGULATIONS)
    try:
        p = Path(__file__).resolve().parents[2] / "data" / "regulation_whitelist.json"
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            groups = [cfg.get("national") or []]
            for reg in (cfg.get("regions") or {}).values():
                groups.append(reg.get("province") or [])
                groups.append(reg.get("standard") or [])
                for lst in (reg.get("cities") or {}).values():
                    groups.append(lst or [])
            for grp in groups:
                for item in grp:
                    keys |= _reg_keys(item)
    except Exception:
        pass
    return keys


KNOWN_REGULATIONS = _load_known_regulations()

# Regex to extract regulation-like patterns from text
REGULATION_CITATION_PATTERN = re.compile(
    r'(?:〔\d{4}〕\s*\d+\s*号)'  # 发文格式: 〔2021〕87号
    r'|(?:[A-Z]+/\s*T\s*\d+[-—]\d+)'  # 标准格式: DB32/T4013-2021
    r'|(?:第\s*\d+\s*号)'  # 补: 第XX号
)


# ═══════════════════════════════════════════════════════════════
# 🔴 NEW: Numeric range validation
# ═══════════════════════════════════════════════════════════════

# Per-field reasonable ranges
NUMERIC_RANGES = {
    "area_mu": (1, 10000, "亩", "征收面积"),
    "area_m2": (100, 10000000, "㎡", "征收面积"),
    "hectares": (0.1, 1000, "公顷", "征收面积"),
    "household_count": (1, 5000, "户", "涉及户数"),
    "population_count": (1, 50000, "人", "涉及人口"),
    "total_samples": (10, 5000, "份", "调查样本数"),
    "support_rate": (0, 100, "%", "支持率"),
    "awareness_rate": (0, 100, "%", "知晓率"),
    "score": (0, 100, "分", "风险评分"),
    "compensation_per_mu": (1000, 500000, "元/亩", "补偿标准"),
    "funding": (10000, 10000000000, "万元", "资金测算"),
}

# Fields that must match between report and filled_data
TRACEABLE_NUMERIC_FIELDS = [
    ("area_mu", "亩", ["征收面积", "拟征收.*?面积", "总面积", "面积"]),
    ("area_m2", "㎡|平方米", ["征收面积", "用地面积"]),
    ("household_count", "户", ["涉及.*?户", "农户", "居民户"]),
    ("total_samples", "份|人", ["调查.*?样本", "问卷.*?数", "发放.*?份"]),
    ("support_rate", "%", ["支持率", "赞成率", "同意率"]),
    ("compensation_standard", "", ["补偿标准", "区片.*?地价"]),
    ("funding", "万元|元", ["资金", "投资", "补偿.*?金额"]),
]


# ═══════════════════════════════════════════════════════════════
# Original functions (kept for backward compatibility)
# ═══════════════════════════════════════════════════════════════

def find_blocking_issues(text: str) -> List[Dict[str, str]]:
    """Return hard-blocking wording/placeholder issues in generated content."""
    masked = re.sub(ALLOWED_MISSING_MARKER, "", text or "")
    issues: List[Dict[str, str]] = []
    for pattern, desc in BLOCKING_PATTERNS:
        if desc == "占位符残留":
            # 签字/日期栏的下划线是给人手签的，不是未替换占位符
            for m in re.finditer(pattern, masked):
                line_start = masked.rfind("\n", 0, m.start()) + 1
                line = masked[line_start:m.end() + 10]
                if any(k in line for k in ("签字", "签名", "评估助理", "资料员",
                                           "编制人", "负责人", "日期", "盖章")):
                    continue
                issues.append({"type": "blocking_wording",
                               "description": desc, "pattern": pattern})
                break
            continue
        if re.search(pattern, masked):
            issues.append({"type": "blocking_wording", "description": desc, "pattern": pattern})
    return issues


def has_blocking_issues(text: str) -> bool:
    return bool(find_blocking_issues(text))


# ═══════════════════════════════════════════════════════════════
# 🔴 NEW: Enhanced validation functions
# ═══════════════════════════════════════════════════════════════

def find_data_validity_issues(text: str) -> List[Dict[str, str]]:
    """Check for data validity problems: negatives, opposition, precision, scores, years."""
    masked = re.sub(ALLOWED_MISSING_MARKER, "", text or "")
    issues: List[Dict[str, str]] = []
    for pattern, desc in DATA_VALIDITY_PATTERNS:
        matches = list(re.finditer(pattern, masked))
        for m in matches[:3]:  # Report up to 3 instances per pattern
            # 年份规则只查「项目年份」，法规的施行/修正/通过日期是合法引用
            if desc.startswith("年份"):
                seg = masked[max(0, m.start() - 30):m.end() + 15]
                if any(k in seg for k in ("施行", "修正", "通过", "公布", "出版")):
                    continue
            issues.append({
                "type": "data_validity",
                "description": desc,
                "pattern": pattern,
                "match": m.group(0)[:50],
            })
    return issues


def find_fabricated_data(
    markdown: str,
    filled_data: Dict[str, any],
    pdf_raw_text: str = "",
) -> List[Dict[str, str]]:
    """Detect numeric values in markdown that cannot be traced to user-provided data.

    For each traceable field type, extracts values from both the report markdown
    and the user-provided filled_data/PDF text. If the report contains a specific
    number that doesn't appear in any data source, flags it as potentially fabricated.

    Returns:
        List of issues, each with: type, field, report_value, severity, message
    """
    issues = []
    seen = set()  # dedup by (field_key, rounded_value)
    filled = {k: str(v).strip() for k, v in (filled_data or {}).items() if v}
    all_source_text = " ".join(filled.values())
    if pdf_raw_text:
        all_source_text += " " + str(pdf_raw_text)[:50000]

    for field_key, unit, text_patterns in TRACEABLE_NUMERIC_FIELDS:
        # Get the user-provided value for this field
        user_val = filled.get(field_key, "")
        if not user_val:
            continue

        # Extract numbers from user-provided value
        user_nums = set()
        for num_str in re.findall(r'\d+\.?\d*', str(user_val)):
            try:
                user_nums.add(float(num_str))
            except ValueError:
                pass

        # Find report numbers near field-related context
        for pattern in text_patterns:
            # Find context windows around field mentions
            for ctx_match in re.finditer(pattern, markdown):
                # Narrow context: 15 chars before to 40 chars after
                start = max(0, ctx_match.start() - 15)
                end = min(len(markdown), ctx_match.end() + 40)
                context = markdown[start:end]

                # For fields with units, require the unit to appear in the match
                if unit:
                    if '|' in unit:
                        num_pattern = r'(?<!\d)(\d+\.?\d*)\s*(?:' + unit + r')'
                    else:
                        num_pattern = r'(?<!\d)(\d+\.?\d*)\s*' + unit
                else:
                    num_pattern = r'(?<!\d)(\d+\.?\d*)\s*%'

                for num_match in re.finditer(num_pattern, context):
                    try:
                        report_num = float(num_match.group(1))
                    except ValueError:
                        continue

                    # Skip obviously non-data numbers (years, phone numbers, etc.)
                    num_str_full = num_match.group(0)
                    if re.search(r'(?:202[0-9]年|电话|\d{7,})', num_str_full):
                        continue

                    # Check if this number appears in user data
                    found_in_source = False
                    # Exact match in user value
                    if num_match.group(1) in str(user_val):
                        found_in_source = True
                    # Close match (within 1% tolerance for large numbers)
                    for un in user_nums:
                        if un > 0 and abs(report_num - un) / max(un, 1) < 0.01:
                            found_in_source = True
                            break
                    # Search in all source text
                    if not found_in_source and num_match.group(1) in all_source_text:
                        found_in_source = True

                    if not found_in_source and report_num > 0:
                        # Only flag meaningful numbers (>10 or decimals)
                        if report_num >= 10 or '.' in str(report_num):
                            # Dedup: skip if same (field, rounded_value) already reported
                            dedup_key = (field_key, round(report_num, 1))
                            if dedup_key in seen:
                                continue
                            seen.add(dedup_key)
                            issues.append({
                                "type": "fabricated_data",
                                "field": field_key,
                                "value": num_match.group(0)[:30],
                                "severity": "critical",
                                "message": (
                                    f"报告中出现{field_key}相关数值「{num_match.group(0)[:30]}」，"
                                    f"但该数值未在用户提供的资料中找到依据，疑似编造"
                                ),
                            })

    return issues


def validate_numeric_ranges(markdown: str) -> List[Dict[str, str]]:
    """Validate that numeric values in markdown fall within reasonable ranges.

    Checks area, household count, survey samples, support rate, scores,
    compensation amounts, and funding against plausible bounds.

    Returns:
        List of issues for values outside reasonable ranges.
    """
    issues = []

    # ── Area (亩) ──
    for m in re.finditer(r'(\d+\.?\d*)\s*亩', markdown):
        val = float(m.group(1))
        lo, hi, unit, label = NUMERIC_RANGES["area_mu"]
        if val <= 0:
            issues.append({"type": "invalid_range", "field": "area_mu",
                          "value": m.group(0), "severity": "critical",
                          "message": f"{label}出现非正值: {m.group(0)}"})
        elif val < lo or val > hi:
            issues.append({"type": "invalid_range", "field": "area_mu",
                          "value": m.group(0), "severity": "error",
                          "message": f"{label}超出合理范围({lo}-{hi}{unit}): {m.group(0)}"})

    # ── Household count ──
    for m in re.finditer(r'(\d+)\s*(?:户|农户)', markdown):
        val = int(m.group(1))
        lo, hi, unit, label = NUMERIC_RANGES["household_count"]
        if val > hi:
            issues.append({"type": "invalid_range", "field": "household_count",
                          "value": m.group(0), "severity": "warning",
                          "message": f"{label}偏大({lo}-{hi}{unit}): {m.group(0)}"})

    # ── Survey samples ──
    # 🔴 限定同一句内，且跳过「单位问卷 3 份」——3 家单位是正常的，不是个人问卷样本
    for m in re.finditer(
            r'(?:发放|回收|调查|问卷)[^。；\n]{0,30}?(\d+)\s*(?:份|人)', markdown):
        seg = markdown[max(0, m.start() - 20):m.end() + 10]
        if "单位" in seg:
            continue
        val = int(m.group(1))
        lo, hi, unit, label = NUMERIC_RANGES["total_samples"]
        if val < lo:
            issues.append({"type": "invalid_range", "field": "total_samples",
                          "value": m.group(0), "severity": "warning",
                          "message": f"{label}偏小({lo}-{hi}{unit}): {m.group(0)}"})

    # ── Support rate ──
    for m in re.finditer(r'支持率[^%\n]{0,10}?(\d+\.?\d*)\s*%', markdown):
        val = float(m.group(1))
        if val < 0 or val > 100:
            issues.append({"type": "invalid_range", "field": "support_rate",
                          "value": m.group(0), "severity": "critical",
                          "message": f"支持率超出0-100%范围: {m.group(0)}"})
        # For land acquisition projects, support should be 100%
        if val < 100 and val > 0:
            issues.append({"type": "invalid_range", "field": "support_rate",
                          "value": m.group(0), "severity": "critical",
                          "message": f"征地项目支持率应为100%，当前为{val}%，存在反对风险"})

    # ── Opposition rate ──
    for m in re.finditer(r'反对率[^%]*?(\d+\.?\d*)\s*%', markdown):
        val = float(m.group(1))
        if val > 0:
            issues.append({"type": "invalid_range", "field": "opposition_rate",
                          "value": m.group(0), "severity": "critical",
                          "message": f"征地项目不应有反对率: {m.group(0)}"})

    # ── Scores (风险评分) ──
    for m in re.finditer(r'(\d+)\s*分', markdown):
        val = int(m.group(1))
        if val < 0:
            issues.append({"type": "invalid_range", "field": "score",
                          "value": m.group(0), "severity": "critical",
                          "message": f"风险评分出现负数: {m.group(0)}"})
        elif val > 100:
            issues.append({"type": "invalid_range", "field": "score",
                          "value": m.group(0), "severity": "critical",
                          "message": f"风险评分超过100分: {m.group(0)}"})

    # ── Compensation (元/亩) ──
    for m in re.finditer(r'(\d+)\s*(?:元/亩|万元/亩)', markdown):
        val = float(m.group(1))
        lo, hi, unit, label = NUMERIC_RANGES["compensation_per_mu"]
        if val > hi:
            issues.append({"type": "invalid_range", "field": "compensation_per_mu",
                          "value": m.group(0), "severity": "warning",
                          "message": f"补偿标准异常偏高: {m.group(0)}"})

    return issues


def find_hallucinated_regulations(markdown: str) -> List[Dict[str, str]]:
    """Detect regulation/policy citations that don't match known valid ones.

    Extracts patterns like 'DB32/T4013-2021', '苏政发〔2021〕87号', '第XX号'
    and checks against the KNOWN_REGULATIONS whitelist. Unknown citations
    are flagged as potentially hallucinated.

    Also flags generic/vague regulation references that lack specific identifiers.
    """
    issues = []

    # Find all regulation-like citations
    citations = set()
    for m in REGULATION_CITATION_PATTERN.finditer(markdown):
        citations.add(m.group(0))

    # 🔴 排除项目公告文号（如"洪拟征告〔2026〕7号"）——这些是项目文号，不是法规，
    # 不应判为编造法规。项目文号错误由文号一致性检查处理。
    def _is_project_doc(citation: str) -> bool:
        # 检查 citation 前是否有"征告/预公告/拟征告/公告"等词
        start = max(0, markdown.find(citation) - 8)
        context = markdown[start:start + 8 + len(citation)]
        return any(kw in context for kw in [
            '征告', '拟征告', '预公告', '征收土地预公告', '公告',
            '发改', '批复文号', '备案', '核准', '环评', '水保', '规划许可',
        ])

    for citation in citations:
        if _is_project_doc(citation):
            continue  # 项目文号跳过，不是法规编造
        # Check if this exact string or a close variant is in known list
        # 🔴 比较前去掉空白：白名单里存的是「DB32/T4013-2021」，正文常写「DB32/T 4013-2021」
        _c = re.sub(r'\s+', '', citation)
        is_known = False
        for known in KNOWN_REGULATIONS:
            _k = re.sub(r'\s+', '', str(known))
            if _c in _k or _k in _c:
                is_known = True
                break
        if not is_known:
            issues.append({
                "type": "hallucinated_regulation",
                "value": citation,
                "severity": "critical",
                "message": f"引用的法规文号「{citation}」不在已知法规库中，疑似编造。"
                           f"请确认该法规真实存在后再引用，或删除该引用。",
            })

    # Check for vague regulation patterns
    # 🔴 必须带「依据/根据/按照」这类引导词才算泛化引用；
    #    否则小节标题「（三）决策相关文件」会被误判。
    vague_patterns = [
        (r'(?:依据|根据|按照|按|遵照|执行)\s*(?:国家|省|市|地方|上级|有关|相关)\s*'
         r'(?:的)?\s*(?:有关|相关)?\s*(?:法律法规|政策|规定|文件|标准|要求)',
         "使用了泛化法规引用，应引用具体法规名称和文号"),
        (r'(?:依据|根据|按照)\s*(?:国家|省|市)\s*(?:有关|相关)\s*(?:规定|政策|要求)',
         "使用了「国家/省/市有关规定」等模糊依据，应写明具体法规"),
    ]
    for pattern, desc in vague_patterns:
        matches = re.findall(pattern, markdown)
        if matches:
            issues.append({
                "type": "vague_regulation",
                "value": matches[0][:60],
                "severity": "warning",
                "message": desc,
            })

    return issues


def check_required_materials(fixed_assets: Dict[str, any]) -> List[Dict[str, str]]:
    """Verify that required company materials are present in fixed assets.

    Checks for:
    - Business license (营业执照) — critical
    - Personnel qualification certificates (人员资质证书) — warning
    - Company profile/info — warning
    - Stability assessment platform registration (稳评平台备案) — info

    Args:
        fixed_assets: The state["_fixed_company_assets"] dict with keys:
            company_name, assets (list), images (list), kb_docs (list)

    Returns:
        List of missing-material issues.
    """
    if not fixed_assets or not isinstance(fixed_assets, dict):
        return [{
            "type": "missing_materials",
            "severity": "critical",
            "message": "未检索到公司固定数据（营业执照、资质证书等），报告可能缺少必要附件",
        }]

    issues = []
    assets = fixed_assets.get("assets", []) or []
    images = fixed_assets.get("images", []) or []
    kb_docs = fixed_assets.get("kb_docs", []) or []

    # Collect all available text for keyword search
    asset_texts = []
    for a in assets:
        if isinstance(a, dict):
            asset_texts.append((a.get("asset_type", "") or "") + " " +
                             (a.get("title", "") or "") + " " +
                             (a.get("content", "") or ""))
    for d in kb_docs:
        if isinstance(d, dict):
            asset_texts.append((d.get("document", "") or "") + " " +
                             str(d.get("metadata", {}) or {}))
    all_text = " ".join(asset_texts).lower()

    # Also check image filenames
    image_names = []
    for img in images:
        if isinstance(img, dict):
            image_names.append((img.get("filename", "") or "").lower())

    # 1. Business license (营业执照) — critical
    has_license = any(kw in all_text for kw in ["营业执照", "统一社会信用代码", "法定代表人"])
    has_license = has_license or any("执照" in fn or "营业执照" in fn or "license" in fn for fn in image_names)
    if not has_license:
        issues.append({
            "type": "missing_materials",
            "field": "business_license",
            "severity": "critical",
            "message": "未找到公司营业执照，报告附件中将缺失营业执照复印件。请上传营业执照扫描件。",
        })

    # 2. Personnel qualification certificates (人员资质证书) — warning
    has_certs = any(kw in all_text for kw in ["资质证书", "资格证书", "职称", "注册工程师",
                                                "咨询工程师", "评估师", "稳评培训"])
    has_certs = has_certs or any("证书" in fn or "资质" in fn or "cert" in fn for fn in image_names)
    if not has_certs:
        issues.append({
            "type": "missing_materials",
            "field": "personnel_certs",
            "severity": "warning",
            "message": "未找到人员资质证书，建议上传稳评工程师资格证书或相关培训证书。",
        })

    # 3. Company basic info — warning
    company_name = fixed_assets.get("company_name", "")
    has_company_info = bool(company_name) or any(
        kw in all_text for kw in ["公司简介", "企业概况", "公司成立于", "注册地址"]
    )
    if not has_company_info:
        issues.append({
            "type": "missing_materials",
            "field": "company_info",
            "severity": "warning",
            "message": "未找到公司简介/基本信息，报告封面和前言可能缺少公司信息。",
        })

    # 4. Stability assessment platform registration (稳评平台备案) — info
    has_platform = any(kw in all_text for kw in ["稳评平台", "备案", "信息系统",
                                                   "社会稳定风险评估平台"])
    if not has_platform:
        issues.append({
            "type": "missing_materials",
            "field": "platform_registration",
            "severity": "info",
            "message": "未找到稳评平台备案信息，如已在省稳评信息系统备案，请确认。",
        })

    return issues


def full_guardrail_check(
    markdown: str,
    filled_data: Optional[Dict[str, any]] = None,
    pdf_raw_text: str = "",
    fixed_assets: Optional[Dict[str, any]] = None,
    chapter_num: int = 0,
    domain: str = "stability",
) -> Dict[str, any]:
    """Run all guardrail checks and return a comprehensive report.

    This is the single entry point for comprehensive content validation.
    Call this from quality review agents to get all issues in one pass.

    Returns:
        {
            "passed": bool,
            "total_issues": int,
            "blocking": [...],
            "data_validity": [...],
            "fabricated": [...],
            "numeric_ranges": [...],
            "regulations": [...],
            "materials": [...],
        }
    """
    def _gated(check_name: str, fn, *a, **kw):
        """域独有检查：非稳评域一律不跑，返回空列表。"""
        if check_name in STABILITY_ONLY_CHECKS and domain != "stability":
            return []
        return fn(*a, **kw)

    report = {
        "chapter": chapter_num,
        "passed": True,
        "total_issues": 0,
        "blocking": find_blocking_issues(markdown),
        "data_validity": _gated("data_validity", find_data_validity_issues, markdown),
        "fabricated": [],
        "numeric_ranges": _gated("numeric_ranges", validate_numeric_ranges, markdown),
        "regulations": find_hallucinated_regulations(markdown),
        "materials": [],
    }

    if filled_data:
        report["fabricated"] = find_fabricated_data(markdown, filled_data, pdf_raw_text)

    if fixed_assets:
        report["materials"] = _gated("required_materials",
                                     check_required_materials, fixed_assets)

    # 域自定义护栏（DomainConfig.guardrails 早已存在，此前无人读）
    #
    # 🔴 只对非稳评域生效：稳评的 `DomainConfig.guardrails` 在本分支出现前**从未被读过**，
    #    它的两条（空串 + 一句不会逐字出现在报告里的话）本就不参与 `total_issues`。
    #    本分支若对稳评也跑一遍，就成了「首次读取」——即便当下计数不变，也破坏了
    #    「稳评调用逐字节等于改动前」这一硬约束。故稳评直接跳过：不读、不计。
    domain_issues = []
    if domain != "stability":
        try:
            from app.domains import get_domain
            for rule in (get_domain(domain).guardrails or []):
                if rule and rule in markdown:
                    domain_issues.append(f"[{domain}] 命中域护栏：{rule}")
        except Exception:
            pass

    # Count total issues
    report["total_issues"] = (
        len(report["blocking"]) +
        len(report["data_validity"]) +
        len(report["fabricated"]) +
        len(report["numeric_ranges"]) +
        len(report["regulations"]) +
        len(report["materials"]) +
        len(domain_issues)
    )

    # Passed = no critical blocking or data validity issues
    critical_count = sum(
        1 for cat in ["blocking", "data_validity", "fabricated"]
        for i in report[cat]
        if i.get("severity", "") == "critical"
    )
    report["passed"] = critical_count == 0

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# 🔴 项目事实库 + 事实比对器（治「数据不准」B+C：报告数字回对资料）
#
# ProjectFacts：从资料提取出的「唯一事实源」，是收口后的干净数据。
# check_chapters_against_facts：把报告各章关键数字回对事实，输出数据问题清单。
# 这两个都是「确定性代码」，不靠 LLM，可断言、可回归。
# ═══════════════════════════════════════════════════════════════════════════════


def _norm_num(s) -> Optional[float]:
    """把字符串/数字归一成 float：去 %、去全角、去逗号空格、去「亩/㎡/份/户」等中文单位。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s)
    # 去全角
    t = t.replace('％', '%').replace('，', ',').replace('．', '.')
    # 去千分位逗号
    t = t.replace(',', '')
    # 去中文单位词
    for unit in ['亩', '平方米', '㎡', '平方', '份', '户', '人', '%', '％', '号']:
        t = t.replace(unit, '')
    t = t.strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _extract_numbers(text: str) -> List[Tuple[float, str]]:
    """从文本抽数字及其附带单位（用于回对面积/支持率等）。

    返回 [(数值, 单位), ...]，单位可能为 ''。
    """
    results: List[Tuple[float, str]] = []
    # 支持率/百分比
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*%', text):
        results.append((float(m.group(1)), '%'))
    # 面积（亩）
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*亩', text):
        results.append((float(m.group(1)), '亩'))
    # 面积（平方米）
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(?:平方米|㎡)', text):
        results.append((float(m.group(1)), '㎡'))
    # 问卷/户数（份、户）
    for m in re.finditer(r'(\d+)\s*(份|户)', text):
        results.append((float(m.group(1)), m.group(2)))
    return results


def build_project_facts(filled_data: Dict[str, Any]) -> Dict[str, Any]:
    """从 filled_data 构建「项目事实库」（唯一事实源）。

    只收口、归一，不新增/不猜测数据；缺的字段留空。
    """
    facts: Dict[str, Any] = {}

    def _g(*keys):
        for k in keys:
            v = filled_data.get(k)
            if v not in (None, '', '【待补充】'):
                return v
        return None

    # 文号
    dr = _g('doc_reference')
    if dr:
        facts['doc_reference'] = str(dr).strip()

    # 征收面积（亩 / ㎡，保留两者）
    am = _g('area_mu')
    am_n = _norm_num(am)
    if am_n is not None and am_n > 0:
        facts['area_mu'] = am_n
    a2 = _g('area_m2')
    a2_n = _norm_num(a2)
    if a2_n is not None and a2_n > 0:
        facts['area_m2'] = a2_n

    # 支持率 / 反对率
    sr = _norm_num(_g('support_rate'))
    if sr is not None:
        facts['support_rate'] = sr
    or_ = _norm_num(_g('oppose_rate'))
    if or_ is not None:
        facts['oppose_rate'] = or_

    # 问卷份数
    sc = _norm_num(_g('total_samples', 'survey_total_count'))
    if sc is not None and sc > 0:
        facts['total_samples'] = sc

    # 户数
    hc = _norm_num(_g('household_count'))
    if hc is not None and hc > 0:
        facts['household_count'] = hc

    return facts


def check_chapters_against_facts(
    chapters: Dict[int, Dict[str, Any]],
    facts: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """把各章关键数字回对项目事实，返回数据问题清单。

    每个 issue 形如 {chapter, type, severity, message, suggestion}，
    suggestion='regenerate' 用于触发对应章节重写（方案丙）。

    判断口径：
    - 面积「亩」：|报告值 - 事实| / 事实 > 1% 视为不一致（326342㎡ vs 489.51亩 本身不是同一单位，需换算）
    - 支持率：差异 > 0.5 个百分点视为不一致
    - 问卷份数/户数：整数不相等视为不一致
    """
    issues: List[Dict[str, Any]] = []

    def _add(ch, typ, msg, sev='critical'):
        issues.append({
            "chapter": ch, "type": typ, "severity": sev,
            "message": msg, "suggestion": "regenerate",
        })

    fact_area_mu = facts.get('area_mu')
    fact_area_m2 = facts.get('area_m2')
    fact_support = facts.get('support_rate')
    fact_oppose = facts.get('oppose_rate')
    fact_total = facts.get('total_samples')
    fact_household = facts.get('household_count')

    for ch_num, ch in chapters.items():
        if not isinstance(ch, dict):
            continue
        md = ch.get('markdown', '') or ''
        if not md:
            continue
        for val, unit in _extract_numbers(md):
            if unit == '%' and fact_support is not None:
                # 仅当数值接近支持率（±15 以内）时才当作「支持率」核对，避免误抓其他百分比
                if abs(val - fact_support) <= 15:
                    if abs(val - fact_support) > 0.5:
                        _add(ch_num, 'support_rate_mismatch',
                             f"第{ch_num}章出现支持率「{val}%」，与资料 {fact_support}% 不一致")
            elif unit in ('份', '户') and unit == '份' and fact_total is not None:
                if val != fact_total:
                    _add(ch_num, 'survey_count_mismatch',
                         f"第{ch_num}章出现问卷「{int(val)}份」，与资料 {int(fact_total)}份 不一致")
            elif unit in ('份', '户') and unit == '户' and fact_household is not None:
                if val != fact_household:
                    _add(ch_num, 'household_mismatch',
                         f"第{ch_num}章出现户数「{int(val)}户」，与资料 {int(fact_household)}户 不一致")

        # ── 面积一致性：只在「总/合计」上下文核对总面积，避免把各权属/地类分项面积误判为不一致 ──
        if fact_area_mu is not None:
            for m in re.finditer(
                r'(总面积|合计面积|合计|拟征收土地总面积|拟征收土地面积|征收面积合计|拟征收总面积)'
                r'\s*[约为共]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*亩', md):
                val = float(m.group(2))
                if abs(val - fact_area_mu) / fact_area_mu > 0.01:
                    _add(ch_num, 'area_mismatch',
                         f"第{ch_num}章出现面积「{val}亩」，与资料 {fact_area_mu} 亩 不一致")
        if fact_area_m2 is not None:
            for m in re.finditer(
                r'(总面积|合计面积|合计|拟征收土地总面积|拟征收土地面积|征收面积合计|拟征收总面积)'
                r'\s*[约为共]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*(?:平方米|㎡)', md):
                val = float(m.group(2))
                if abs(val - fact_area_m2) / fact_area_m2 > 0.01:
                    _add(ch_num, 'area_mismatch',
                         f"第{ch_num}章出现面积「{val}㎡」，与资料 {fact_area_m2}㎡ 不一致")

    # 去重（同 chapter + 同 type 只保留一条，避免同一数字多段重复刷屏）
    seen = set()
    dedup = []
    for it in issues:
        k = (it['chapter'], it['type'])
        if k not in seen:
            seen.add(k)
            dedup.append(it)
    return dedup


def fix_chapters_against_facts(
    chapters: Dict[int, Dict[str, Any]],
    facts: Dict[str, Any],
):
    """确定性强制修正：把各章与资料不符的关键数字，直接替换为事实库的正确值。

    与 check_chapters_against_facts 的区别：那个只「报」，这个直接「改」。
    不依赖 LLM 重写（LLM 重写往往改不对），保证数据 100% 准确。

    安全口径（避免误改）：
    - 面积（亩/㎡）、问卷（份）、户数（户）：单位无歧义，直接改
    - 支持率/反对率（%）：% 有歧义（可能误伤「满意度85%」），只改带明确上下文的
      「支持率X%」「反对率X%」表述

    返回 (修正后的 chapters, 修正记录列表 [(chapter, 旧值, 新值), ...])。
    """
    def _fmt(v):
        """数字格式化：整数不带小数点（100.0 → 100）。"""
        return str(int(v)) if float(v) == int(v) else str(v)

    fixes = []
    fact_area_mu = facts.get('area_mu')
    fact_area_m2 = facts.get('area_m2')
    fact_support = facts.get('support_rate')
    fact_oppose = facts.get('oppose_rate')
    fact_total = facts.get('total_samples')
    fact_household = facts.get('household_count')

    for ch_num, ch in chapters.items():
        if not isinstance(ch, dict):
            continue
        md = ch.get('markdown', '') or ''
        if not md:
            continue
        orig = md

        # ── 面积（亩）── 只在「总/合计/拟征收」上下文替换，避免把各权属/地类分项面积误改成总面积
        if fact_area_mu is not None:
            def _fix_mu(m):
                val = float(m.group(2))
                if abs(val - fact_area_mu) / fact_area_mu > 0.01:
                    fixes.append((ch_num, f"{val}亩", f"{_fmt(fact_area_mu)}亩"))
                    return f"{m.group(1)}{_fmt(fact_area_mu)}亩"
                return m.group(0)
            md = re.sub(
                r'(总面积|合计面积|合计|拟征收土地总面积|拟征收土地面积|征收面积合计|拟征收总面积)'
                r'\s*[约为共]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*亩', _fix_mu, md)

        # ── 面积（㎡）── 同上，只在「总/合计」上下文替换
        if fact_area_m2 is not None:
            def _fix_m2(m):
                val = float(m.group(2))
                if abs(val - fact_area_m2) / fact_area_m2 > 0.01:
                    fixes.append((ch_num, f"{val}㎡", f"{int(fact_area_m2)}㎡"))
                    return f"{m.group(1)}{int(fact_area_m2)}㎡"
                return m.group(0)
            md = re.sub(
                r'(总面积|合计面积|合计|拟征收土地总面积|拟征收土地面积|征收面积合计|拟征收总面积)'
                r'\s*[约为共]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*(?:平方米|㎡)', _fix_m2, md)

        # ── 地类分项套总面积（第4章常见：每个地类都写成总面积）──
        if fact_area_m2 is not None and ch_num == 4:
            _land = r'(?:耕地|园地|林地|草地|水域及水利设施用地|水域|其他土地|商业服务业设施用地|住宅用地|交通运输用地)'
            _at = str(int(fact_area_m2))
            _land_repeat = re.compile(
                r'(?:' + _land + r'面积\s*' + _at + r'\s*(?:平方米|㎡))'
                r'(?:\s*[、，,和及与]\s*(?:' + _land + r'面积\s*' + _at + r'\s*(?:平方米|㎡)))+'
            )
            if _land_repeat.search(md):
                md = _land_repeat.sub('各地类构成及面积以表6-1（土地分类面积表）为准', md)
                fixes.append((ch_num, f"地类面积套用总面积{_at}㎡", "改以表6-1为准"))

        # ── 支持率（仅「支持率X%」明确上下文）──
        if fact_support is not None:
            def _fix_sr(m):
                val = float(m.group(1))
                if abs(val - fact_support) > 0.5:
                    fixes.append((ch_num, f"支持率{val}%", f"支持率{_fmt(fact_support)}%"))
                    return f"支持率{_fmt(fact_support)}%"
                return m.group(0)
            md = re.sub(r'支持率\s*[：:为]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*%', _fix_sr, md)

        # ── 反对率（仅「反对率X%」明确上下文）──
        if fact_oppose is not None:
            def _fix_or(m):
                val = float(m.group(1))
                if abs(val - fact_oppose) > 0.5:
                    fixes.append((ch_num, f"反对率{val}%", f"反对率{_fmt(fact_oppose)}%"))
                    return f"反对率{_fmt(fact_oppose)}%"
                return m.group(0)
            md = re.sub(r'反对率\s*[：:为]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*%', _fix_or, md)

        # ── 问卷份数 ──
        if fact_total is not None:
            def _fix_sc(m):
                val = float(m.group(1))
                if val != fact_total:
                    fixes.append((ch_num, f"{int(val)}份", f"{int(fact_total)}份"))
                    return f"{int(fact_total)}份"
                return m.group(0)
            md = re.sub(r'(?<![\d.])(\d+)\s*份', _fix_sc, md)

        # ── 户数 ──
        if fact_household is not None:
            def _fix_hc(m):
                val = float(m.group(1))
                if val != fact_household:
                    fixes.append((ch_num, f"{int(val)}户", f"{int(fact_household)}户"))
                    return f"{int(fact_household)}户"
                return m.group(0)
            md = re.sub(r'(?<![\d.])(\d+)\s*户', _fix_hc, md)

        if md != orig:
            ch['markdown'] = md
            if ch.get('status') not in ('human_reviewed', 'human_review'):
                ch['status'] = 'fact_fixed'

    return chapters, fixes


# ═══════════════════════════════════════════════════════════════════════════════
# 稳评总分的一致性（跨章）
# ═══════════════════════════════════════════════════════════════════════════════

# 「稳评总分W=0分」「综合风险指数为12.5分」「风险指数 8 分」三种写法都认。
_RISK_SCORE_PAT = re.compile(
    r'(?P<lead>(?:稳评总分|综合风险指数|风险指数)\s*(?:W\s*)?(?:为|是)?\s*[=＝]?\s*)'
    r'(?P<num>\d+(?:\.\d+)?)\s*分')


def fix_risk_score_consistency(chapters: Dict[Any, Dict[str, Any]]):
    """把各章引用的「稳评总分/综合风险指数」统一成**第12章评分表的结论值**。

    为什么必须有这条（实测 2026-10-28，洪拟征告〔2026〕7号）：
        第12章量化评分表 21 项扣分全 0，明确写「措施前稳评总分 W=0 分」；
        第13章开头却写「措施前综合风险指数为**12.5分**」—— 12.5 是从参照范文里
        抄来的**别的项目**的数字，与本报告自己的评分表直接打架，业主一眼看出来。
        这类数字不在事实库里（它是第12章表算出来的），`fix_chapters_against_facts`
        管不到，只能做**跨章一致性**：以第12章为准，别的章一律对齐。

    取权威值：第12章里**最后一次**出现的那个（结论句在章末）。
    返回 (chapters, [(章号, 旧值, 新值), ...])。
    """
    ref = None
    _md12 = (chapters.get(12) or chapters.get("12") or {})
    if isinstance(_md12, dict):
        _hits = _RISK_SCORE_PAT.findall(str(_md12.get("markdown") or ""))
        if _hits:
            ref = _hits[-1][1]
    if ref is None:
        return chapters, []

    fixes = []
    for ch_num, ch in chapters.items():
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue

        def _sub(m, _n=ch_num):
            if m.group("num") == ref:
                return m.group(0)
            fixes.append((_n, m.group("num"), ref))
            return f"{m.group('lead')}{ref}分"

        new_md = _RISK_SCORE_PAT.sub(_sub, md)
        # 🔴 已全项 0 分还说「扣分均有下降空间、可进一步降低」是**事实矛盾**（不能再降了）。
        #    实测 2026-10-28 第13章结尾就是这么写的。W=0 时把这些句子整句删掉。
        if str(ref).strip() in ("0", "0.0", "0.00"):
            new_md = re.sub(r'[^。！？\n]*?(?:扣分|风险指数)[^。！？\n]{0,30}'
                            r'(?:下降空间|进一步降低)[^。！？\n]*[。！？]', '', new_md)
        if new_md != md:
            ch["markdown"] = new_md

    return chapters, fixes


# 「第11章识别出…」「经第12章量化评分…」这类**内部章号自述**。
# 只认后面紧跟这几种动词的写法；「详见第12章表12-4」这种合法交叉引用不受影响。
_CH_SELFREF_PAT = re.compile(
    r'第[一二三四五六七八九十百\d]+章(?=\s*(?:识别|量化|分析|测算|所述|评价|判定))')

# 「本章针对…」「本章将…」整句删除（句首位置，含换行后）。
_CH_SELF_SENT_PAT = re.compile(
    r'(?:(?<=[。！？])|(?<=\n)|^)\s*本章[^。！？\n]{0,80}[。！？]', re.M)


def strip_chapter_self_reference(chapters: Dict[Any, Dict[str, Any]]):
    """去掉正文里的「第11章识别出…、经第12章量化评分…」式章号自述。

    业主 2026-10-28 指出这句「话术不正确」：评审表与正文里写「第11章…第12章…」，
    读起来像**编制说明**而不是报告正文；章号本来是目录的事，正文该直接说事。
    改成「识别出本项目六项风险因素，经量化评分，…」—— 语义不变、不丢数据。

    返回 (chapters, 改写处数)。
    """
    n = 0
    for ch in chapters.values():
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        new_md, k = _CH_SELFREF_PAT.subn("", md)
        # 「本章针对各项风险逐条提出…」「本章将…」这类**章节自述**一并删掉：
        # 正文只写事，不写"我这一章打算写什么"（业主 2026-10-28 指出「话术不正确」）。
        new_md, k2 = _CH_SELF_SENT_PAT.subn("", new_md)
        if k or k2:
            ch["markdown"] = new_md
            n += k + k2
    return chapters, n


# ═══════════════════════════════════════════════════════════════════════════════
# 正文去重（相邻重复句 / 残句 / 重复段）
# ═══════════════════════════════════════════════════════════════════════════════

# 「…涉及约50户被征地群众。50户。」这类**把刚说过的数又秃噜一遍**的残句。
# 判据：去掉标点后 ≤ 8 字、含数字、且内容原样出现在上一句里。
_FRAG_MAX = 8


def dedup_repeated_text(md: str) -> Tuple[str, int]:
    """去掉正文里**紧邻重复**的句子/残句/段落，返回 `(新文本, 去掉处数)`。

    只动「一模一样」或「明显是秃噜嘴」的重复，不做语义改写 —— 宁可漏，不可误删。

    实测来源（业主 2026-10-28 指出「需要去重」）：
        「本次拟征收土地面积489.5亩……涉及户数50户。50户。补偿方案制定应……」
    以及模型偶发的整段重复（同一段连写两遍）。
    """
    text = str(md or "")
    if not text:
        return text, 0
    removed = 0

    # ── ① 段落级：相邻两段逐字相同 → 去后一段 ──
    paras = text.split("\n\n")
    kept_paras = []
    for p in paras:
        if kept_paras and p.strip() and p.strip() == kept_paras[-1].strip():
            removed += 1
            continue
        kept_paras.append(p)
    text = "\n\n".join(kept_paras)

    # ── ② 句子级：逐段拆句，去掉紧邻重复句与残句 ──
    out_paras = []
    for p in text.split("\n\n"):
        if not p.strip():
            out_paras.append(p)
            continue
        sents = [s for s in re.split(r'(?<=[。！？；])', p) if s]
        kept: List[str] = []
        for s in sents:
            core = s.strip().rstrip("。！？；")
            prev = kept[-1].strip() if kept else ""
            prev_core = prev.rstrip("。！？；")
            # 紧邻重复句（≥4 字才算重复，太短的多为标点碎片）
            if core and len(core) >= 4 and core == prev_core:
                removed += 1
                continue
            # 残句：极短 + 含数字 + 上一句已包含同样内容
            if (core and len(core) <= _FRAG_MAX and re.search(r'\d', core)
                    and prev_core and core in prev_core):
                removed += 1
                continue
            kept.append(s)
        out_paras.append("".join(kept))

    return "\n\n".join(out_paras), removed


def dedup_chapters(chapters: Dict[Any, Dict[str, Any]]):
    """全章正文去重，返回 `(chapters, 去掉处数)`。"""
    n = 0
    for ch in chapters.values():
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        new_md, k = dedup_repeated_text(md)
        if k:
            ch["markdown"] = new_md
            n += k
    return chapters, n


# ═══════════════════════════════════════════════════════════════════════════════
# 表12-4 主要风险因素初始风险等级表 —— 必须与第11章逐条对应
# ═══════════════════════════════════════════════════════════════════════════════
# 实测 2026-10-28：第11章列了**六项**风险（（一）～（六）），第12章正文也写
# 「六项风险、4 项一般 2 项较小」；可模型手写的 表12-4 只有 **5 行**，风险名还是
# 自拟的（「补偿标准争议」「社保安置衔接」…）—— 表、章、结论三处对不上。
# 这里按第11章的小节与各小节自己的「风险程度为 X」结论**重排该表**。

_RISK_LEVEL_PAT = re.compile(r'风险程度为\s*(重大|较大|一般|较小|微小)')


def _ch11_items(md: str) -> List[Tuple[str, str]]:
    """第11章的风险条目 → [(风险名, 风险程度)]，程度取该小节内最后一次判定。

    🔴 第11章的写法有两种：
      ① 结构小节 `### （一）决策风险因素分析预测`，风险源写在节内 `**1. ××风险**`；
      ② 直接以风险为小节 `### 11.1 ××方面的风险`。
    旧实现只认 ①，结果把「（一）（二）（三）」三个**结构小节**当成了三项风险，
    表12-4 于是印成「决策风险因素分析预测 / 风险估计方法 / 主要风险因素初始风险等级」
    （2026-11-11 实测）。现在优先取节内编号风险条目，没有再退回小节标题。
    """
    lines = str(md or "").split("\n")
    # ── ① 节内编号风险条目：`**1. 土地征收补偿方案风险**` / `1.征收程序方面的风险`
    _ITEM = re.compile(r'^(?:\*\*)?\s*(\d{1,2})\s*[.、]\s*([^\n*]{2,40}?风险)\s*(?:\*\*)?\s*$')
    idxs = [(i, m.group(2).strip()) for i, ln in enumerate(lines)
            for m in [_ITEM.match(ln.strip())] if m]
    if len(idxs) >= 3:
        items = []
        for k, (i, name) in enumerate(idxs):
            end = idxs[k + 1][0] if k + 1 < len(idxs) else len(lines)
            seg = "\n".join(lines[i + 1:end])
            lv = _RISK_LEVEL_PAT.findall(seg)
            items.append((name, lv[-1] if lv else ""))
        return items

    # ── ② 以风险为小节：`### 11.1 ××方面的风险`
    _SUB = re.compile(r'^#{2,4}\s*(?:\d+(?:\.\d+)*)?\s*[.、]?\s*([^\n]{2,40}风险)\s*$')
    subs = [(i, m.group(1).strip()) for i, ln in enumerate(lines)
            for m in [_SUB.match(ln.strip())] if m]
    if len(subs) >= 3:
        items = []
        for k, (i, name) in enumerate(subs):
            # `### （一）甲风险`：去掉中文序号，只留风险名
            name = re.sub(r'^[（(][一二三四五六七八九十]+[）)]\s*', '', name).strip()
            end = subs[k + 1][0] if k + 1 < len(subs) else len(lines)
            lv = _RISK_LEVEL_PAT.findall("\n".join(lines[i + 1:end]))
            items.append((name, lv[-1] if lv else ""))
        return items

    # ── ③ 兜底：（一）（二）（三）小节
    items, cur, buf = [], None, []

    def _flush():
        if cur is None:
            return
        lv = _RISK_LEVEL_PAT.findall("\n".join(buf))
        items.append((cur, lv[-1] if lv else ""))

    for line in lines:
        s = line.strip()
        m = re.match(r'^#{2,4}\s*（[一二三四五六七八九十]+）\s*(.+)$', s)
        if m:
            _flush()
            cur, buf = m.group(1).strip(), []
            continue
        if cur is not None and s:
            buf.append(s)
    _flush()
    return items


def rebuild_risk_level_table(chapters: Dict[Any, Dict[str, Any]]):
    """按第11章小节重排第12章的「主要风险因素初始风险等级表」，返回改写处数。

    只在**行数与第11章风险条数不一致**时动手 —— 一致就说明模型自己排对了。
    """
    md11 = str(((chapters.get(11) or chapters.get("11") or {}) if isinstance(
        chapters.get(11) or chapters.get("11"), dict) else {}).get("markdown") or "")
    items = _ch11_items(md11)
    if len(items) < 3:
        return 0
    ch12 = chapters.get(12) or chapters.get("12")
    if not isinstance(ch12, dict):
        return 0
    md = str(ch12.get("markdown") or "")
    if not md:
        return 0
    # 定位那张表：表头含「风险因素」与「风险等级」的 markdown 表块
    lines = md.split("\n")
    hdr_i = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("|")
                  and "风险因素" in ln and "风险等级" in ln), None)
    if hdr_i is None:
        return 0
    end = hdr_i
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    data_rows = max(0, end - hdr_i - 2)          # 去表头与分隔线
    header = [c.strip() for c in lines[hdr_i].strip().strip("|").split("|")]
    name_ci = next((k for k, c in enumerate(header) if "风险因素" in c), None)
    # 行数相同还不够 —— 名字必须一一对应（实测 6 行 6 名全不一样也会被放过）
    if data_rows == len(items) and name_ci is not None:
        same = True
        for k, r in enumerate(lines[hdr_i + 2:end]):
            cs = [c.strip() for c in r.strip().strip("|").split("|")]
            if name_ci >= len(cs) or cs[name_ci] != items[k][0]:
                same = False
                break
        if same:
            return 0

    new_tbl = ["| 序号 | 风险因素 | 初始风险等级 |", "| --- | --- | --- |"]
    for i, (name, level) in enumerate(items, 1):
        new_tbl.append(f"| {i} | {name} | {level or '低风险'} |")
    ch12["markdown"] = "\n".join(lines[:hdr_i] + new_tbl + lines[end:])
    return 1


def rebuild_risk_overview_table(chapters: Dict[Any, Dict[str, Any]]):
    """第4章表4-1 按**第11章的风险清单**重建（第11章是唯一口径）。

    🔴 第4章的模板表是写死的六类风险（含「征收程序方面的风险」），而第11章按
    项目实际只能列五类（本项目前期程序已依法履行，禁止列征收程序风险）——
    成稿里第4章「六个方面」、第11章五节、评审一眼看出对不上
    （专家批注「4项风险，第11章为6风险」，2026-11-11）。
    这里用第11章的条目重排表4-1：同名/近义的行沿用原描述与发生阶段，
    新出现的风险取该节第一句话作描述。
    """
    import difflib

    ch11 = chapters.get(11) or chapters.get("11")
    ch4 = chapters.get(4) or chapters.get("4")
    if not isinstance(ch11, dict) or not isinstance(ch4, dict):
        return 0
    items = _ch11_items(str(ch11.get("markdown") or ""))
    if len(items) < 3:
        return 0
    md11 = str(ch11.get("markdown") or "")
    md = str(ch4.get("markdown") or "")
    if not md:
        return 0
    lines = md.split("\n")
    hdr_i = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("|") and "风险类型" in ln
                  and "风险因素描述" in ln), None)
    if hdr_i is None:
        return 0
    end = hdr_i
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    header = [c.strip() for c in lines[hdr_i].strip().strip("|").split("|")]
    try:
        name_ci = header.index("风险类型")
        desc_ci = header.index("风险因素描述")
    except ValueError:
        return 0
    stage_ci = header.index("发生阶段") if "发生阶段" in header else None

    old_rows = []
    for r in lines[hdr_i + 2:end]:
        cs = [c.strip() for c in r.strip().strip("|").split("|")]
        if len(cs) > max(name_ci, desc_ci):
            old_rows.append(cs)
    if len(old_rows) == len(items) and all(
            old_rows[i][name_ci] == items[i][0] for i in range(len(items))):
        return 0

    def _norm(s: str) -> str:
        return re.sub(r'(方面的风险|引发的风险|的风险|风险|的)', '', s)

    # 每个风险小节的第一句话，作为新风险行的描述
    def _first_sentence(name: str) -> str:
        pat = re.escape(name)
        m = re.search(pat + r'[\s\S]{0,400}?\n\s*\n([^|\n][^\n]{8,80})', md11)
        if not m:
            return ""
        s = re.split(r'[。；]', m.group(1).strip())[0]
        return s[:60]

    new_rows = []
    for i, (name, _lv) in enumerate(items, 1):
        matched = None
        b = _norm(name)
        # ① 先找**规范化后完全相同**的行（「征收决策资金风险」不能被
        #    「征收决策实施风险」的高相似度抢走描述）
        for cs in old_rows:
            if _norm(cs[name_ci]) == b:
                matched = cs
                break
        if matched is None:
            for cs in old_rows:
                a = _norm(cs[name_ci])
                if (a in b or b in a) and \
                        difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
                    matched = cs
                    break
        if matched is None:
            for cs in old_rows:
                a = _norm(cs[name_ci])
                if difflib.SequenceMatcher(None, a, b).ratio() >= 0.9:
                    matched = cs
                    break
        desc = matched[desc_ci] if matched else _first_sentence(name)
        stage = (matched[stage_ci] if matched and stage_ci is not None
                 and stage_ci < len(matched) else "准备、实施")
        row = [""] * len(header)
        row[0] = str(i)
        row[name_ci] = name
        row[desc_ci] = desc
        if stage_ci is not None and stage_ci < len(row):
            row[stage_ci] = stage
        new_rows.append("| " + " | ".join(row) + " |")

    tbl = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"] + new_rows
    out = "\n".join(lines[:hdr_i] + tbl + lines[end:])

    # 表格下方「…六个方面」的概述句同步改成实际条数
    _cn = {3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}
    n = len(items)
    out = re.sub(r'主要风险集中在[^。]{0,80}?([三四五六七八九]+)个方面',
                 lambda m: f"主要风险集中在" + "、".join(
                     _norm(x[0]) or x[0] for x in items) + f"{_cn.get(n, str(n))}个方面",
                 out)
    out = re.sub(r'共识别\s*\d+\s*(个方面主要风险因素|项主要风险因素)',
                 f"共识别{n}" + r"\1", out)
    ch4["markdown"] = out
    return 1


def rebuild_risk_measures_table(chapters: Dict[Any, Dict[str, Any]]):
    """第13章表13-1「风险类型」列按第11章风险清单重排。

    只换**风险类型**这一列与行序：同名/近义的措施、责任主体、时限原样沿用；
    第11章有、表里没有的风险，从本章对应小节取第一句作措施（责任主体取表内
    出现最多的一个，时限写「全过程」）；表里多出来的风险行删除。
    """
    import difflib

    ch11 = chapters.get(11) or chapters.get("11")
    ch13 = chapters.get(13) or chapters.get("13")
    if not isinstance(ch11, dict) or not isinstance(ch13, dict):
        return 0
    md11 = str(ch11.get("markdown") or "")
    items = _ch11_items(md11)
    if len(items) < 3:
        return 0
    md = str(ch13.get("markdown") or "")
    lines = md.split("\n")
    hdr_i = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("|") and "风险类型" in ln
                  and "防范化解措施" in ln), None)

    def _norm(s: str) -> str:
        # 🔴 不要剥「环境影响」：它是「水系」类风险的主体词组，
        #    剥掉后「周边水系及环境影响」只剩「周边水系及」，与
        #    「控制施工及环境影响」小节标题的二字组完全对不上（实测配错措施）。
        return re.sub(r'(方面的风险|引发的风险|的风险|风险|方面|的)', '', s)

    # 本章小节：标题 → 正文（新增风险的措施来源）
    _secs = []
    for i, ln in enumerate(lines):
        m = re.match(r'^#{2,4}\s*(?:[（(][一二三四五六七八九十]+[）)]|\d+(?:\.\d+)*)\s*(.+)$',
                     ln.strip())
        if m:
            body = []
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if re.match(r'^#{1,4}\s', s) or s.startswith("|"):
                    break
                if s:
                    body.append(s)
            _secs.append((m.group(1).strip(), " ".join(body)))

    def _bigrams(s: str) -> set:
        s = re.sub(r'\s+', '', s)
        return {s[i:i + 2] for i in range(len(s) - 1)}

    def _core(s: str) -> set:
        return _bigrams(_norm(s))

    def _pick_sentence(body: str, core: set) -> str:
        """小节正文里挑与本风险最相关的一句（二字组命中最多，其次取第一句）。"""
        sents = [s.strip() for s in re.split(r'[。；]', body) if s.strip()]
        if not sents:
            return ""
        best, score = sents[0], -1
        for s in sents:
            ov = len(core & _bigrams(s))
            if ov > score:
                best, score = s, ov
        return best[:80]

    def _measure_for(name: str) -> str:
        """第11章有、表里没有对应行时的措施：先本章对应小节，再第11章风险分析。

        匹配用**二字组**而不是单字：「征收决策实施」与各小节正文几乎每句都有
        「征收」，按单字重叠会随便挑中一段（实测把资金句配给了舆论媒体）。
        """
        core = _core(name)
        # ① 小节标题的二字组（最可靠）
        best, score = "", 0
        for title, body in _secs:
            ov = len(core & _bigrams(title))
            if ov > score:
                best, score = body, ov
        if score >= 2:
            return _pick_sentence(best, core)
        # ② 小节正文的二字组（至少 3 个才敢用）
        best, score = "", 0
        for title, body in _secs:
            ov = len(core & _bigrams(body))
            if ov > score:
                best, score = body, ov
        if score >= 3:
            return _pick_sentence(best, core)
        # ③ 本章没有对应小节（如「征收决策实施」未单列）→ 取第11章该风险的
        #    「风险分析」段里**措施性**的一句，绝不凭空编
        idx = md11.find(name)
        if idx < 0:
            return ""
        seg = md11[idx: idx + 900]
        m = re.search(r'风险分析[：:]\s*([\s\S]{0,400})', seg)
        if not m:
            return ""
        body = m.group(1)
        _sents = [s.strip() for s in re.split(r'[。；]', body) if s.strip()]
        # 优先「应/须/将/建立/落实」这类措施句，排除纯回顾（「已于…发布」）
        for s in _sents:
            if any(k in s for k in ("应", "须", "将", "建立", "落实", "完善",
                                    "专人", "台账", "制度", "宣传", "引导")) \
                    and not s.startswith("已"):
                return s[:80]
        return _sents[0][:80] if _sents else ""

    # 🔴 模型这一版**没写**「风险防范与化解措施汇总表」时，不硬造一张：
    #    本章没有对应措施小节的风险（如舆论媒体）就没有措施可引，
    #    硬拼出来的表格等于编造。缺表由审核清单提示，交人工补。
    if hdr_i is None:
        return 0

    end = hdr_i
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    header = [c.strip() for c in lines[hdr_i].strip().strip("|").split("|")]
    try:
        name_ci = header.index("风险类型")
        measure_ci = header.index("防范化解措施")
    except ValueError:
        return 0
    org_ci = header.index("责任主体") if "责任主体" in header else None
    time_ci = header.index("完成时限") if "完成时限" in header else None
    rows = []
    for r in lines[hdr_i + 2:end]:
        cs = [c.strip() for c in r.strip().strip("|").split("|")]
        if len(cs) > name_ci:
            rows.append(cs)
    if all(i < len(rows) and rows[i][name_ci] == items[i][0]
           for i in range(len(items))) and len(rows) == len(items):
        return 0

    # 表内最常见的责任主体（给新增风险兜底用）
    _orgs = [cs[org_ci] for cs in rows if org_ci is not None and org_ci < len(cs)
             and cs[org_ci]]
    common_org = max(set(_orgs), key=_orgs.count) if _orgs else ""

    # 同义主题：第11章叫「舆论媒体风险」，第13章措施小节/行常写「信访及舆情」
    _TOPIC_ALIASES = {
        "舆论": ("舆情", "信访", "媒体", "网络", "宣传"),
        "媒体": ("舆情", "信访", "媒体", "网络"),
    }

    def _topic_match(b: str, a: str) -> bool:
        for base, alts in _TOPIC_ALIASES.items():
            if base in b and any(k in a for k in alts):
                return True
        return False

    new_rows, used = [], set()
    for i, (name, _lv) in enumerate(items, 1):
        b = _norm(name)
        matched = None
        for k, cs in enumerate(rows):
            if k in used:
                continue
            a = _norm(cs[name_ci])
            if a == b or (a in b or b in a) or _topic_match(b, a) or \
                    difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
                matched, used_k = cs, k
                break
        row = [""] * len(header)
        if matched:
            used.add(used_k)
            row = list(matched)
        else:
            row[name_ci] = name
            row[measure_ci] = _measure_for(name)
            if org_ci is not None and org_ci < len(row):
                row[org_ci] = common_org
            if time_ci is not None and time_ci < len(row):
                row[time_ci] = "全过程"
        row[0] = str(i)
        row[name_ci] = name       # 名称以第11章为准
        new_rows.append("| " + " | ".join(row) + " |")

    tbl = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"] + new_rows
    out = "\n".join(lines[:hdr_i] + tbl + lines[end:])
    # 表下「六项风险均明确了…」按条数改；列错的环节清单收口
    _cn = {3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}
    out = re.sub(r'[一二三四五六七八九十\d]+项风险均明确了',
                 f"{_cn.get(len(items), len(items))}项风险均明确了", out)
    out = re.sub(r'上述措施落实后，[^。]{0,80}?等环节',
                 "上述措施落实后，各风险环节", out)
    ch13["markdown"] = out
    return 1


# ═══════════════════════════════════════════════════════════════════════════════
# 长句断开 —— 「用户只看一遍」的第一大障碍
# ═══════════════════════════════════════════════════════════════════════════════
# 实测 2026-10-30（交付体检 baseline）：
#   稳评 402 段里 **81 段**含超过 60 字的长句、22 段段首雷同；复垦 432 段里 84 段长句。
#   长句是最好改、也是评审最刺眼的一类 —— 在装配前确定性地断一次，
#   比让模型「下次注意」靠谱得多。
#
# 断句只断**安全**的地方：
#   ① 引号/括号内的逗号不碰（「根据《X办法》第3条，……」不能断在书名号里）；
#   ② 断点前后都要 ≥ 15 字（否则会断出「本项目，位于……」这种碎句）；
#   ③ 含冒号的列举句不断（「包括：①…；②…」断了更乱）；
#   ④ 一句最多断两次，避免把一句话切成一堆短句。
_MAX_SENT_LEN = 60
_MIN_PIECE = 15
_MAX_SPLITS_PER_SENT = 2
# 🔴 后半句以这些词开头时**不断**：那是目的/结果状语从句，
#    断了会得到「……为及时复垦利用被损毁的土地。促进土地集约节约利用。」这种碎句
#    （实测 2026-10-31 第1章）。
_NO_SPLIT_HEADS = (
    # 结果/目的类：切在它们前面，后半句就成了没主语的残句
    "促进", "实现", "达到", "确保", "保障", "推动", "提高", "增强",
    "使", "从而", "进而", "最终", "防止", "避免",
    # 🔴 状语/介词短语开头（2026-11-02 实测）：在逗号处切开会产出
    #    「…进行适宜性评价。结合当地国土空间规划确定的用途。」这种残句 ——
    #    第 8 轮产物 7 章共 24 处。**宁可留长句，也不要语法不通的短句**。
    "结合", "按", "依照", "根据", "依据", "通过", "对于", "针对",
    "鉴于", "随着", "基于", "本着", "围绕", "经过", "由", "从", "在", "对",
    # 🔴 动宾续写（2026-11-03 对照人工稿实测）：这些词开头的半句是上一句谓语的
    #    延续（「…损毁程度。合理确定复垦时间」「…使用期满后。立即组织施工」），
    #    切成独立句就是病句。宁可留长句。
    "合理", "恢复", "减少", "立即", "完成", "组织", "开展", "编制", "实施",
    "采取", "采用", "进行", "形成", "构成", "确定", "制定", "提出", "建立",
    "加强", "严格", "落实", "做好", "完善", "规范", "健全", "统一", "优化",
    "缓解", "减轻", "保证", "维护", "保持", "满足", "符合", "控制", "降低",
    "增加", "扩大", "缩小", "分析", "研究", "推进", "深化", "执行", "遵守",
    "将", "把", "与", "和", "及", "或", "等",
)

_OPEN_BRACKETS = "「『（(《《【“"
_CLOSE_BRACKETS = "」』）)）》】”"


def _comma_positions(sent: str) -> list[int]:
    """返回**不在引号/括号里**的逗号下标。"""
    depth = 0
    out = []
    for i, ch in enumerate(sent):
        if ch in _OPEN_BRACKETS:
            depth += 1
        elif ch in _CLOSE_BRACKETS:
            depth = max(0, depth - 1)
        elif ch == "，" and depth == 0:   # 🔴 只认中文逗号：顿号（、）是并列项，断开就错了
            out.append(i)
    return out


def split_long_sentences(md: str, max_len: int = _MAX_SENT_LEN):
    """把超长句在安全的逗号处断开，返回 `(新文本, 断句处数)`。

    只处理正文行；标题（#）、表格（|）、图片行原样保留。
    """
    import re as _re

    if not md:
        return md, 0

    n = 0
    out_lines = []
    for line in md.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith("!["):
            out_lines.append(line)
            continue
        # 按句末标点切，保留标点
        parts = _re.split(r"(?<=[。！？])", s)
        new_parts = []
        for sent in parts:
            if len(sent) <= max_len or "：" in sent or ":" in sent:
                new_parts.append(sent)
                continue
            cur = sent
            for _ in range(_MAX_SPLITS_PER_SENT):
                if len(cur) <= max_len:
                    break
                pos = _comma_positions(cur)
                # 选「离中点最近、且两边都够长」的逗号
                cand = [p for p in pos
                        if p >= _MIN_PIECE and len(cur) - p - 1 >= _MIN_PIECE
                        and not cur[p + 1:].lstrip().startswith(_NO_SPLIT_HEADS)]
                if not cand:
                    break
                mid = len(cur) / 2
                cut = min(cand, key=lambda p: abs(p - mid))
                cur = cur[:cut] + "。" + cur[cut + 1:]
                n += 1
            new_parts.append(cur)
        out_lines.append("".join(new_parts))
    return "\n".join(out_lines), n


# 🔴 断句的「事后修补」（2026-11-03 对照人工稿）：
#    旧版断句把逗号直接换成句号，后半句以动词/动宾开头时就是病句：
#    「并根据临时用地的损毁时间、损毁性质和损毁程度。合理确定复垦时间…」
#    断句逻辑已经补了「哪些词开头不能断」，但**已经生成/已落库的文本**仍带病；
#    这里做一次确定性回并：句号后面紧跟下列续写词时，把句号还原成逗号。
_MERGE_HEADS = (
    "合理", "恢复", "减少", "立即", "完成", "组织", "开展", "编制", "实施",
    "采取", "采用", "进行", "形成", "构成", "确定", "制定", "提出", "建立",
    "加强", "严格", "落实", "做好", "缓解", "减轻", "维护", "保持", "满足",
    "符合", "控制", "降低", "增加", "扩大", "缩小", "推进", "深化", "执行",
    "遵守", "保证",
)


def merge_broken_sentences(md: str):
    """把被断句切坏的「…。动词…」回并成「…，动词…」，返回 `(新文本, 处数)`。

    只处理同一段落内的正文行；标题/表格/图片行原样保留。
    """
    if not md:
        return md, 0
    n = 0
    out = []
    for line in md.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith("!["):
            out.append(line)
            continue
        for h in _MERGE_HEADS:
            pat = "。" + h
            if pat in line:
                k = line.count(pat)
                line = line.replace(pat, "，" + h)
                n += k
        out.append(line)
    return "\n".join(out), n


# 面积单位换算自检：模型常把「22215 平方米，折合 2.3248 公顷」写错
# （用总面积而不是分项面积）。这里按 1 公顷 = 10000 ㎡、1 亩 = 666.6667 ㎡ 重算。
_AREA_CONV_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:平方米|㎡|m2|m²)\s*[，,]?\s*'
    r'(?:折合|约合|约|合|相当于)?\s*'
    r'(\d+(?:\.\d+)?)\s*(公顷|hm2|hm²|ha|亩)'
)


def fix_area_conversions(md: str):
    """修正「X 平方米，折合 Y 公顷/亩」里的换算错误，返回 `(新文本, 处数)`。"""
    if not md:
        return md, 0
    n = 0

    def _repl(m):
        nonlocal n
        raw, conv, unit = m.group(1), float(m.group(2)), m.group(3)
        try:
            v = float(raw)
        except ValueError:
            return m.group(0)
        if v <= 0:
            return m.group(0)
        right = v / 10000.0 if unit in ("公顷", "hm2", "hm²", "ha") else v / 666.6667
        # 换算正确（误差 < 0.5% 或 1 个末位）就不动
        if abs(conv - right) <= max(0.0005, right * 0.005):
            return m.group(0)
        n += 1
        return m.group(0).replace(m.group(2), f"{right:.4f}".rstrip("0").rstrip("."), 1)

    return _AREA_CONV_RE.sub(_repl, md), n


# 「23248 平方米，合 2.3248 公顷」「0.2325 公顷（合 2325 平方米）」——
# 基础信息里同句换算属于多余解释（业主 2026-11-04：「这些基础资料不需要有
# 多余的场地大小解释」）。正文保留第一个面积，删掉「，合…」这段。
_AREA_EXPL_RE = re.compile(
    r'(\d[\d,.]*\s*(?:平方米|㎡|m2|m²|公顷|hm2|hm²|ha|亩))'
    r'\s*[，,（(]?\s*(?:折合|约合|合|相当于)\s*'
    r'\d[\d,.]*\s*(?:平方米|㎡|m2|m²|公顷|hm2|hm²|ha|亩)\s*[）)]?'
)


_PROJECT_NAME_PLAN_RE = re.compile(
    r'(项目名称\s*[：:]\s*)([^。；\n]*?)(?:土地复垦方案|复垦方案)(?=[。；\n]|$)')


def strip_project_name_plan_suffix(md: str):
    """「项目名称：XX工程三井组临时用地土地复垦方案」→ 去掉方案后缀。

    方案名是报告标题，不是项目名（业主 2026-11-04）。只改「项目名称：」字段，
    正文里正常的「编制…土地复垦方案」不动。
    """
    if not md:
        return md, 0
    n = 0

    def _repl(m):
        nonlocal n
        n += 1
        return m.group(1) + m.group(2).rstrip(" ，,、")

    return _PROJECT_NAME_PLAN_RE.sub(_repl, md), n


def drop_area_explanations(md: str):
    """删掉同句的面积换算解释，只保留第一个面积。返回 (新文本, 处数)。"""
    if not md:
        return md, 0
    n = 0
    out = []
    for line in md.splitlines():
        # 表格里的换算列/单元格保留（表头要求时是有用的）
        if line.lstrip().startswith("|"):
            out.append(line)
            continue
        new = _AREA_EXPL_RE.sub(lambda m: m.group(1), line)
        if new != line:
            n += 1
            # 清掉替换后可能留下的「，，」「，。」之类
            new = re.sub(r'[，,]\s*([。；;])', r'\1', new)
            new = re.sub(r'[，,]\s*[，,]', '，', new)
        out.append(new)
    return "\n".join(out), n


# 「复垦区总面积为 2.3248 公顷」——后面通常紧跟「包括永久性建设用地 8.0762 公顷
# 和临时用地 2.3248 公顷」，总数应为两者之和。实测模型会拿临时用地面积当总数
# （2026-11-04 测试稿：总数 2.3248，分项却是 8.0762 + 2.3248）。
_RE_AREA_TOTAL = re.compile(
    r'(复垦区总面积[^。；\n]{0,8}?)(\d+(?:\.\d+)?)(\s*(?:公顷|hm2|hm²|hm))')


def fix_reclamation_area_total(md: str, filled: dict):
    """复垦区总面积按资料改正（= 永久性建设用地 + 临时用地）。返回 (新文本, 处数)。

    `filled` 没有 project_area_m2 / 两项面积时**不动**——没有依据就不改数。
    """
    if not md or not isinstance(filled, dict):
        return md, 0
    try:
        total_m2 = float(str(filled.get("project_area_m2") or 0).replace(",", ""))
    except (TypeError, ValueError):
        total_m2 = 0.0
    if total_m2 <= 0:
        try:
            total_m2 = (float(str(filled.get("permanent_area_m2") or 0))
                        + float(str(filled.get("area_m2") or 0)))
        except (TypeError, ValueError):
            total_m2 = 0.0
    if total_m2 <= 0:
        return md, 0
    total_ha = f"{total_m2 / 10000.0:.4f}"
    n = 0
    out = []
    for line in md.splitlines():
        if line.lstrip().startswith("|") or "复垦区总面积" not in line:
            out.append(line)
            continue
        new = _RE_AREA_TOTAL.sub(
            lambda m: f"{m.group(1)}{total_ha}{m.group(3)}", line)
        if new != line:
            n += 1
        out.append(new)
    return "\n".join(out), n



# 🔴 前言里引用不存在的表（业主 2026-10-31 实测：「上表所列临时用地总面积…」
#    但第1章按新口径**不排表**，读者找不到「上表」）。
_TABLE_REF_RE = re.compile(
    r'(如下表所示|如下表|上表所列|上表中|上表|下表所列|下表中|见下表|见上表|如表所示|如表|见表)')


def strip_table_references(md: str) -> str:
    """去掉正文里的「上表/下表/如表」引用（用于没有表的章节）。"""
    if not md:
        return md
    out = []
    for sent in re.split(r'(?<=[。！？])', md):
        if _TABLE_REF_RE.search(sent):
            sent = _TABLE_REF_RE.sub('', sent)
            sent = re.sub(r'^[，、；：\s]+', '', sent)
            sent = re.sub(r'，\s*，', '，', sent)
        out.append(sent)
    return ''.join(out)


_UNIT_NUM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(公顷|hm2|hm²|平方米|㎡|亩|万元)')
_DOC_NO_RE = re.compile(r'[一-龥]{2,10}〔\d{4}〕\s*\d+\s*号')
_PLACE_NAME_RE = re.compile(r'[一-龥]{2,10}(?:村|镇|街道|社区|小区|大道|路|苑|安置区)')
_PLACE_STOP = {
    "项目区", "复垦区", "临时用地", "施工便道", "道路", "公路", "铁路",
    "农村道路", "生产路", "田间路", "小区", "社区", "行政区", "开发区",
    # 泛指村组，不是具体地名（「所在村安排1人管护」里的「所在村」不能当地名换）
    "所在村", "本村", "该村", "各村", "每个村", "全村", "邻村", "周边村",
    "行政村", "自然村", "村组", "村民组", "项目村",
    # 道路类型不是地名（「G2高速公路」里只该保留 G2，不能把「速公路」当村名换掉）
    "高速公路", "速公路", "高速", "省道", "国道", "干道", "村道", "乡道",
    "主干道", "次干道", "环路", "大道",
    # 🔴 通用名词（2026-11-03 对照人工稿实测）：这些词以「村/镇/街道/路」收尾，
    #    会被地名正则误捕，替换成项目地址后就成了病句 ——
    #    「充分听取当地乡镇（街道）」→「淮安市淮安区（街道）」；
    #    「0.5km 外的农村道路」→「0.5km 淮安市淮安区」；
    #    「田间道路明确专人管护」→「淮安市淮安区明确专人管护」；
    #    「自然资源、农业农村、水利」→「自然资源、淮安市淮安区、水利」。
    "当地乡镇", "乡镇", "城镇", "农业农村", "农田", "田间道路", "农村道路",
    "周边农田原有道路", "原有道路", "原有田间道路", "田间生产道路",
    "交通运输", "交通道路", "市政道路", "乡村道路", "生产道路",
}
# 候选地名里出现下列词素 → 一定不是专名，不做替换
_PLACE_BAD_PARTS = (
    "当地", "原有", "周边", "其他", "相关", "各级", "各个", "乡镇", "城镇",
    "城市", "农村", "农田", "田间", "道路", "公路", "交通", "水利", "电力",
    "施工", "生产", "行政", "开发", "农业", "乡村", "主要", "部分",
    # 🔴 法规名不是地名：「中华人民共和国村民委员会组织法」会被
    #    `_PLACE_NAME_RE` 截成「中华人民共和国村」，再被当成范文地名换成项目地址，
    #    成稿印成「《朱坝街道办事处、三圩社区、二组、三组、六组民委员会组织法》」
    #    （2026-11-11 实测，专家批注「没有这个说法」）。
    "中华人民共和国", "人民共和国",
)
_VILLAGE_PAIR_RE = re.compile(
    r'[一-龥]{2,8}(?:镇|村|街道|社区)到[一-龥]{2,8}(?:镇|村|街道|社区)之间')
_PLACE_LEAD = ("本项目位于", "项目位于", "位于", "地处", "本项目", "项目",
               "工程位于", "本工程", "在", "于")


_DOC_LEAD = ("批复文号为", "批复文号", "文号为", "文号", "编号为", "编号",
             "批复为", "批复", "同意", "为")


def _trim_doc_no(tok: str) -> str:
    """去掉文号前粘上的标签（「批复文号为苏复垦〔2023〕8号」→「苏复垦〔2023〕8号」）。"""
    t = str(tok or "")
    changed = True
    while changed:
        changed = False
        for p in _DOC_LEAD:
            if t.startswith(p) and len(t) - len(p) >= 5:
                t = t[len(p):]
                changed = True
    return t


def _trim_place(tok: str) -> str:
    """去掉地名前面粘上的动词/指示词（「项目位于明远路」→「明远路」）。"""
    t = str(tok or "")
    changed = True
    while changed:
        changed = False
        for p in _PLACE_LEAD:
            if t.startswith(p) and len(t) - len(p) >= 3:
                t = t[len(p):]
                changed = True
    return t


def _num_any(v) -> float:
    try:
        return float(str(v).replace(",", "").replace("，", "").strip())
    except Exception:
        return 0.0


def scrub_sample_facts(md: str, filled: dict, sample_text: str,
                       user_text: str = ""):
    """把范文里残留的**数字/地名/文号**换成用户资料；用户没有就写「待补充」。

    业主 2026-11-02：「范文的数字、地名、面积、文号全部用用户提供的新报告的资料
    填充，如果没有让人工介入填写，人工选择跳过的，就是占位文字『待补充』。」

    只处理三类可判定对象，避免误伤正文：
      · 带单位的数量（公顷/平方米/㎡/亩/万元）—— 范文数值而用户资料里没有的；
      · 行政区/地名（…村/镇/街道/路/苑 等）—— 范文独有、用户资料里没有的；
      · 项目文号（XX〔2026〕1号）—— 范文独有、且**不在书名号内**的（政策引用不动）。

    返回 `(新文本, 替换处数)`。
    """
    if not md or not sample_text:
        return md, 0
    import json as _json
    filled = filled or {}
    user_corpus = _json.dumps(filled, ensure_ascii=False) + "\n" + str(user_text or "")
    sample_text = str(sample_text)
    loc = str(filled.get("location") or "").strip() or "项目区"
    doc_default = str(filled.get("doc_reference") or "").strip() or "待补充"
    area_m2 = _num_any(filled.get("area_m2"))
    area_mu = _num_any(filled.get("area_mu")) or (area_m2 / 666.6667 if area_m2 else 0.0)
    invest = _num_any(filled.get("project_investment"))
    _lat = _num_any(filled.get("_centroid_lat"))
    _lon = _num_any(filled.get("_centroid_lon"))
    n = 0

    # ── ⓪ 坐标：模型常把「33°16′33.45″」写成 `33163345`（符号丢失、数字连成串）──
    _bad_coord = re.compile(
        r'北纬\s*[^。；，]{0,20}?\d{7,}\s*[、,，]\s*东经\s*[^。；，]{0,20}?\d{7,}')

    def _coord_repl(m):
        nonlocal n
        n += 1
        if _lat and _lon:
            return f"北纬 {_lat:.6f}°、东经 {_lon:.6f}°"
        return "项目区范围内"

    md = _bad_coord.sub(_coord_repl, md)

    # ── ⓪.5 「A镇到B镇之间」：整段换成项目区范围，别把两个镇名各换一遍 ──
    def _pair_repl(m):
        nonlocal n
        tok = m.group(0)
        if tok in user_corpus:
            return tok
        n += 1
        return "项目区范围内"

    md = _VILLAGE_PAIR_RE.sub(_pair_repl, md)

    # ── ① 项目文号 ──
    def _doc_repl(m):
        nonlocal n
        tok = m.group(0)
        core = _trim_doc_no(tok)
        if core in user_corpus or core not in sample_text:
            return tok
        # 「批复文号为XX〔2023〕8号」—— 已经明确是项目文号，直接换
        if core != tok:
            n += 1
            return tok.replace(core, doc_default, 1)
        _prev = md[max(0, m.start() - 40):m.start()]
        # 政策文号常见写法：《…通知》（自然资规〔2019〕1号）—— 紧跟在
        # 「（/《」后面的不是项目文号，绝不能换。
        if _prev.rstrip().endswith(("（", "(", "《")):
            return tok
        # 只有**项目批复语境**里的文号才换（批复/文号/批准/备案/核准/许可/同意）；
        # 正文里随口引用的政策文号保持原样。
        if not re.search(r'(批复|文号|批准|备案|核准|许可|同意)', _prev[-25:]):
            return tok
        n += 1
        return doc_default

    md = _DOC_NO_RE.sub(_doc_repl, md)

    # ── ② 地名 ──
    def _is_place(t: str) -> bool:
        """候选串是不是**专名**：以村/镇/街道/路等收尾，且不含通用词素。"""
        if len(t) < 3 or t in _PLACE_STOP:
            return False
        return not any(bad in t for bad in _PLACE_BAD_PARTS)

    _sample_places = {
        _trim_place(t) for t in _PLACE_NAME_RE.findall(sample_text)
        if _is_place(_trim_place(t))
    }

    def _place_repl(m):
        nonlocal n
        tok = m.group(0)
        # 🔴 书名号内是法规/标准名，不是地名 —— 一律不动
        #    （否则「《中华人民共和国村民委员会组织法》」会被换成项目地址）。
        if md.rfind("《", 0, m.start()) > md.rfind("》", 0, m.start()):
            return tok
        base = _trim_place(tok)
        if (base in _PLACE_STOP or tok in user_corpus or base in user_corpus
                or base not in _sample_places):
            return tok
        n += 1
        # 「项目位于明远路」→ 只换地名本身，保留前面的动词
        return tok.replace(base, loc, 1) if base != tok else loc

    md = _PLACE_NAME_RE.sub(_place_repl, md)

    # ── ③ 带单位的数量 ──
    def _num_repl(m):
        nonlocal n
        num, unit = m.group(1), m.group(2)
        # 🔴 「1.61 万元/亩」是**单位面积投资**，不是项目总投资 ——
        #    2026-11-03 测试稿：这个 1.61 被换成了项目总投资 651003.92。
        _tail = m.string[m.end():m.end() + 6].lstrip()
        if unit == "万元" and (_tail.startswith("/亩") or _tail.startswith("/公顷")
                               or _tail.startswith("/hm")):
            return m.group(0)
        if num in user_corpus or num not in sample_text:
            return m.group(0)
        val = ""
        if unit in ("公顷", "hm2", "hm²") and area_m2 > 0:
            val = f"{area_m2 / 10000.0:.4f}"
        elif unit in ("平方米", "㎡") and area_m2 > 0:
            val = f"{area_m2:.0f}"
        elif unit == "亩" and area_mu > 0:
            val = f"{area_mu:.2f}"
        elif unit == "万元" and invest > 0:
            val = f"{invest:.2f}"
        if not val:
            val = "待补充"
        n += 1
        return f"{val} {unit}"

    md = _UNIT_NUM_RE.sub(_num_repl, md)
    return md, n


def clean_broken_placeholders(md: str):
    """清掉模型把「待补充」塞进句子后造成的病句（保留正常的字段级占位）。

    2026-11-02 线上实测：
        「在待补充之间」→「在项目区范围内」
        「待补充四通八达」→「项目区周边四通八达」
        「G2待补充」「309待补充，」→ 去掉多余的待补充，保留编号
    返回 `(新文本, 清理处数)`。
    """
    if not md or "待补充" not in md:
        return md, 0
    n = 0
    for pat, repl in (
        (r'在待补充之间', '在项目区范围内'),
        (r'待补充(?=四通八达)', '项目区周边'),
        (r'(?<=[A-Za-z0-9])待补充', ''),
        (r'待补充(?=[、，。；）)])', ''),
    ):
        md, k = re.subn(pat, repl, md)
        n += k
    return md, n


def ensure_section_headings(md: str, chapter_no: int, domain: str = "reclamation"):
    """补齐被模型漏掉的一级小节标题（如「4.5 水土资源平衡分析」）。

    🔴 2026-11-03 对照人工稿实测：成稿直接从 4.4.4 跳到 4.5.1，
       「4.5」这一级标题缺失 —— 目录里 4.5.1 挂到了 4.4 下面。
       只补 `X.Y` 级（更深一级本来就该存在），不动正文。
    """
    if not md or domain != "reclamation":
        return md, 0
    try:
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        secs = ((RECLAMATION_CHAPTER_DEFINITIONS.get(int(chapter_no)) or {})
                .get("sections") or [])
    except Exception:
        return md, 0
    lines = md.splitlines()
    n = 0
    for sec in secs:
        m = re.match(r'^(\d+(?:\.\d+)*)\s+(.+)$', str(sec).strip())
        if not m or m.group(1).count(".") != 1:
            continue
        num, title = m.group(1), m.group(2).strip()
        # 🔴 复垦参照件的层级整体下移一格（`## 5.1` 渲染前已是 `### 5.1`），
        #    只认 `##` 会把已有的 5.1 当成缺失、再插一条重复标题 ——
        #    随后整节被去重逻辑删掉（2026-11-03 测试稿 5.1.1~5.1.6 全没了）。
        has_h2 = any(re.match(r'^#{2,4}\s*' + re.escape(num) + r'(?!\.)\s', ln.strip())
                     for ln in lines)
        if has_h2:
            continue
        for i, ln in enumerate(lines):
            if re.match(r'^#{3,4}\s*' + re.escape(num) + r'\.', ln.strip()):
                lines.insert(i, f"## {num} {title}")
                n += 1
                break
    return "\n".join(lines), n


def polish_chapters(chapters: Dict[Any, Dict[str, Any]], domain: str | None = None,
                    filled: Dict[str, Any] | None = None,
                    sample_text: str = "", user_text: str = ""):
    """装配前的确定性润色：去重复段 + 断长句 + 范文残留清理。

    返回 `(chapters, 统计dict)`。
    """
    chapters, dup_n = dedup_chapters(chapters)
    split_n = 0
    scrub_n = 0
    ph_n = 0
    merge_n = 0
    conv_n = 0
    area_expl_n = 0
    pname_n = 0
    area_total_n = 0
    sec_n = 0
    for _ch_no, ch in chapters.items():
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        try:
            _md0, _sn0 = ensure_section_headings(md, int(_ch_no), domain or "")
        except (TypeError, ValueError):
            _md0, _sn0 = md, 0
        if _sn0:
            ch["markdown"] = md = _md0
            sec_n += _sn0
        new_md, k = split_long_sentences(md)
        if k:
            ch["markdown"] = new_md
            split_n += k
        # 🔴 范文残留：数字/地名/文号 → 用户资料或「待补充」
        if sample_text:
            _md2, _sn = scrub_sample_facts(ch.get("markdown") or "", filled or {},
                                           sample_text, user_text)
            if _sn:
                ch["markdown"] = _md2
                scrub_n += _sn
        _md3, _pn = clean_broken_placeholders(ch.get("markdown") or "")
        if _pn:
            ch["markdown"] = _md3
            ph_n += _pn
        # 🔴 断句留下的病句回并 + 面积换算自检
        _md4, _mn = merge_broken_sentences(ch.get("markdown") or "")
        if _mn:
            ch["markdown"] = _md4
            merge_n += _mn
        _md5, _cn = fix_area_conversions(ch.get("markdown") or "")
        if _cn:
            ch["markdown"] = _md5
            conv_n += _cn
        _md6, _en = drop_area_explanations(ch.get("markdown") or "")
        if _en:
            ch["markdown"] = _md6
            area_expl_n += _en
        _md7, _qn = strip_project_name_plan_suffix(ch.get("markdown") or "")
        if _qn:
            ch["markdown"] = _md7
            pname_n += _qn
        # 🔴 复垦区总面积 = 永久性建设用地 + 临时用地（模型常拿临时用地面积当总数）
        if domain == "reclamation":
            _md8, _tn = fix_reclamation_area_total(ch.get("markdown") or "", filled or {})
            if _tn:
                ch["markdown"] = _md8
                area_total_n += _tn
    # 🔴 复垦第1章**没有表** → 正文里的「上表/如表」引用一律清掉（否则读者找不到表）
    if domain == "reclamation":
        _ch1 = chapters.get(1) or chapters.get("1")
        if isinstance(_ch1, dict):
            _md = str(_ch1.get("markdown") or "")
            _new = strip_table_references(_md)
            if _new != _md:
                _ch1["markdown"] = _new
    return chapters, {"dedup": dup_n, "split": split_n,
                      "scrub": scrub_n, "placeholder_clean": ph_n,
                      "merge": merge_n, "area_fix": conv_n,
                      "area_expl": area_expl_n, "pname": pname_n,
                      "area_total": area_total_n,
                      "sections_added": sec_n}
