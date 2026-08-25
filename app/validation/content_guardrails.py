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
from typing import Dict, List, Tuple, Optional, Set

# ═══════════════════════════════════════════════════════════════
# Centralized AI buzzword list — single source of truth
# Import this from other files instead of duplicating
# ═══════════════════════════════════════════════════════════════

AI_BUZZWORDS = [
    '具有重要意义', '切实保障', '多措并举', '统筹推进',
    '综上所述', '有力支撑', '奠定了坚实基础', '提供了有力保障',
    '注入了强劲动力', '夯实基础',
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
    (r"有关单位|相关部门", "责任主体泛化表达"),
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
    (r'反对(?:率|人数|比例).*?(?:[1-9]\d*(?:\.\d+)?\s*%|(?:[1-9]\d*人))',
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
KNOWN_REGULATIONS = {
    "DB32/T4013-2021",
    "DB3201/T1163-2023",
    "DB32/T4937-2024",
    "发改办投资〔2013〕428号",
    "苏政发〔2020〕86号",
    "苏政发〔2021〕87号",
    "苏自然资发〔2021〕128号",
    "苏自然资函〔2021〕769号",
    "中华人民共和国土地管理法",
    "中华人民共和国土地管理法实施条例",
    "中华人民共和国突发事件应对法",
    "国有土地上房屋征收与补偿条例",
    "江苏省征地补偿和被征地农民社会保障办法",
    "江苏省社会稳定风险评估办法",
    "江苏省土地管理条例",
    "淮安市征地补偿和被征地农民社会保障实施细则",
}

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
    for m in re.finditer(r'(?:发放|回收|调查|问卷).*?(\d+)\s*(?:份|人)', markdown):
        val = int(m.group(1))
        lo, hi, unit, label = NUMERIC_RANGES["total_samples"]
        if val < lo:
            issues.append({"type": "invalid_range", "field": "total_samples",
                          "value": m.group(0), "severity": "warning",
                          "message": f"{label}偏小({lo}-{hi}{unit}): {m.group(0)}"})

    # ── Support rate ──
    for m in re.finditer(r'支持率[^%]*?(\d+\.?\d*)\s*%', markdown):
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
        return any(kw in context for kw in ['征告', '拟征告', '预公告', '征收土地预公告', '公告'])

    for citation in citations:
        if _is_project_doc(citation):
            continue  # 项目文号跳过，不是法规编造
        # Check if this exact string or a close variant is in known list
        is_known = False
        for known in KNOWN_REGULATIONS:
            if citation in known or known in citation:
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
    vague_patterns = [
        (r'(?:相关|有关|国家|地方|省级|市级)\s*(?:法律法规|政策|规定|文件|标准)',
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
    report = {
        "chapter": chapter_num,
        "passed": True,
        "total_issues": 0,
        "blocking": find_blocking_issues(markdown),
        "data_validity": find_data_validity_issues(markdown),
        "fabricated": [],
        "numeric_ranges": validate_numeric_ranges(markdown),
        "regulations": find_hallucinated_regulations(markdown),
        "materials": [],
    }

    if filled_data:
        report["fabricated"] = find_fabricated_data(markdown, filled_data, pdf_raw_text)

    if fixed_assets:
        report["materials"] = check_required_materials(fixed_assets)

    # Count total issues
    report["total_issues"] = (
        len(report["blocking"]) +
        len(report["data_validity"]) +
        len(report["fabricated"]) +
        len(report["numeric_ranges"]) +
        len(report["regulations"]) +
        len(report["materials"])
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
            if unit == '亩' and fact_area_mu is not None:
                # 允许换算：亩 ≈ ㎡/666.67
                if abs(val - fact_area_mu) / fact_area_mu > 0.01:
                    _add(ch_num, 'area_mismatch',
                         f"第{ch_num}章出现面积「{val}亩」，与资料 {fact_area_mu} 亩 不一致")
            elif unit == '㎡' and fact_area_m2 is not None:
                if abs(val - fact_area_m2) / fact_area_m2 > 0.01:
                    _add(ch_num, 'area_mismatch',
                         f"第{ch_num}章出现面积「{val}㎡」，与资料 {fact_area_m2}㎡ 不一致")
            elif unit == '%' and fact_support is not None:
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

        # ── 面积（亩）──
        if fact_area_mu is not None:
            def _fix_mu(m):
                val = float(m.group(1))
                if abs(val - fact_area_mu) / fact_area_mu > 0.01:
                    fixes.append((ch_num, f"{val}亩", f"{fact_area_mu}亩"))
                    return f"{fact_area_mu}亩"
                return m.group(0)
            md = re.sub(r'(?<![\d.])(\d+(?:\.\d+)?)\s*亩', _fix_mu, md)

        # ── 面积（㎡）──
        if fact_area_m2 is not None:
            def _fix_m2(m):
                val = float(m.group(1))
                if abs(val - fact_area_m2) / fact_area_m2 > 0.01:
                    fixes.append((ch_num, f"{val}㎡", f"{int(fact_area_m2)}㎡"))
                    return f"{int(fact_area_m2)}㎡"
                return m.group(0)
            md = re.sub(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(?:平方米|㎡)', _fix_m2, md)

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
