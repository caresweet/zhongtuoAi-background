"""从「征收土地预公告」原文通用提取字段。

设计原则（用户要求）：
- **文号不写死**：不限「拟征告」前缀，任意「前缀〔年份〕序号号」都认。
- **地区不写死**：不限「街道/镇/社区/村」，直接取公告原文里的位置描述。
- **全部来自预公告**：这些字段的权威来源就是用户上传的预公告 PDF 逐字文本。

任何新项目（不同市/县/乡镇/园区/开发区/林场…）都应开箱可用。
"""
import re
from typing import Any, Dict

# 文号：任意「前缀〔年份〕序号号」（前缀不限，如 洪拟征告 / 清拟征告 / 苏政发 / 发改投资…）
#   括号兼容 〔〕/（）/()/【】
DOC_REF_RE = re.compile(r'[一-龥A-Za-z]{1,10}\s*[〔（(\[【]\s*\d{4}\s*[〕）)\]】]\s*\d+\s*号')

# 方括号/圆括号年份统一成 〔〕
_BRACKET_FIX = re.compile(r'[（(\[]\s*(\d{4})\s*[）)\]]')


def normalize_doc_ref(value: str) -> str:
    """统一文号写法：去空格、括号统一为〔〕。"""
    s = re.sub(r'\s+', '', str(value or ''))
    return _BRACKET_FIX.sub(r'〔\1〕', s)


def extract_doc_reference(text: str) -> str:
    """取预公告文号：优先「公告/预公告」附近的文号，否则第一篇文号。

    不假设前缀是「拟征告」——只要形如「XXX〔2026〕7号」都认。
    """
    if not text:
        return ""
    cands = list(DOC_REF_RE.finditer(text))
    if not cands:
        return ""
    for m in cands:
        ctx = text[max(0, m.start() - 30): m.end() + 30]
        if "预公告" in ctx or "公告" in ctx:
            return normalize_doc_ref(m.group(0))
    return normalize_doc_ref(cands[0].group(0))


# ── 位置：直接取公告原文片段，**不限行政区划层级** ──
#   公告可能写「X区Y街道Z社区」「X镇Y村」「X开发区Y路东侧」「X林场Y工区」…一律照单全收。
_LOC_PATTERNS = [
    # 「拟征收土地位于 …」「土地坐落于 …」
    re.compile(r'(?:拟征收土地|征收土地|拟征地|土地|地块|项目)\s*(?:位于|坐落于|坐落在|坐落|四至为)\s*([^\s，,。；;、\n]{4,40})'),
    # 「土地坐落：…」「征收位置：…」
    re.compile(r'(?:土地坐落|土地位置|拟征收位置|征收位置|拟征收范围|征收范围|涉及范围|位置|坐落)\s*[：:]\s*([^\s。；;\n]{4,40})'),
    # 「征收范围为 …」
    re.compile(r'(?:拟征收|征收)(?:土地)?(?:范围)?(?:为|是)\s*([^\s。；;\n]{4,40})'),
]

_LOC_TRIM = re.compile(r'(?:范围(?:内|以内的)?|一带|附近|区域)$')


def extract_location(text: str) -> str:
    """从预公告原文提取征收土地位置（完整到公告所载层级，不做行政区划假设）。"""
    if not text:
        return ""
    for pat in _LOC_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        v = str(m.group(1) or "").strip()
        v = _LOC_TRIM.sub("", v).strip("，,。；;、 ")
        if 4 <= len(v) <= 60:
            return v
    return ""


# 公告标题尾缀（去掉后即为「决策/项目名称」）
_ANNOUNCE_SUFFIX = re.compile(
    r'(?:征收土地预公告|征地预公告|土地征收预公告|征收土地公告|拟征收土地公告|'
    r'征地公告|征收公告|预公告)$'
)


def clean_project_title(title: str) -> str:
    """把预公告标题规整为项目/决策名称。

    处理三类噪声：
      1. 去掉「…预公告」体裁尾缀
      2. 去掉开头的发文机关（「淮安市洪泽区人民政府征收土地预公告」→ 只剩机关名，无意义）
      3. 去掉首尾括号空格

    用户要求「直接用预公告的标题」，此处仅去掉公告体裁词与发文机关，不改写、不拼接、不套模板。
    """
    t = re.sub(r'\s+', '', str(title or "")).strip()
    t = _ANNOUNCE_SUFFIX.sub("", t)
    # 去掉开头的发文机关（省/市/区/县政府、管委会、街道办）
    t = re.sub(r'^(?:[一-龥]{2,10}?(?:省|市|区|县))?'
               r'[一-龥]{0,10}?(?:人民政府|管理委员会|管委会|街道办事处)', '', t)
    return t.strip("（）()：: 【】[]、")


def derive_project_name(doc_ref: str, title: str = "", fallback_suffix: str = "土地征收决策") -> str:
    """项目名称：优先公告标题本身（用户要求），其次「文号+体裁后缀」。"""
    t = clean_project_title(title)
    if t:
        return t
    dr = normalize_doc_ref(doc_ref)
    return f"{dr}{fallback_suffix}" if dr else ""


# ═══════════════════════════════════════════════════════════════
# 预公告正文全文解析 —— 正文每个部分都是有效内容，不能只取文号/位置
# ═══════════════════════════════════════════════════════════════

# 中文序号小节标题：「一、征收目的」
_SECTION_RE = re.compile(r'^\s*([一二三四五六七八九十]+)\s*[、.．]\s*(\S{2,12})\s*$')
# 法规引用：《中华人民共和国土地管理法》
_LAW_REF_RE = re.compile(r'《([^》]{2,40})》')


def _merge_wrapped_lines(text: str) -> str:
    """PDF 取文会把一段拆成多行，按行合并；遇到中文序号小节标题则断开。"""
    out, buf = [], []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _SECTION_RE.match(line):
            if buf:
                out.append("".join(buf)); buf = []
            out.append(line)
            continue
        buf.append(line)
    if buf:
        out.append("".join(buf))
    return "\n".join(out)


def extract_sections(text: str) -> Dict[str, str]:
    """按「一、二、三…」切出预公告正文各小节。

    Returns:
        {小节标题: 正文}，例如 {"征收目的": "根据《…》第四十五条的规定，…", …}
    """
    merged = _merge_wrapped_lines(text)
    sections: Dict[str, str] = {}
    cur = None
    for line in merged.split("\n"):
        m = _SECTION_RE.match(line)
        if m:
            cur = m.group(2).strip()
            sections.setdefault(cur, "")
            continue
        if cur is None:
            continue
        # 🔴 文末结束标记：落款、联系人、附图、制图信息不属于任何小节
        if _SECTION_STOP_RE.search(line) or _STANDALONE_DATE_RE.fullmatch(line):
            line = _SECTION_STOP_RE.split(line)[0]
            if line and not _STANDALONE_DATE_RE.fullmatch(line):
                sections[cur] = (sections[cur] + line).strip()
            cur = None
            continue
        sections[cur] = (sections[cur] + line).strip()
    return {k: v for k, v in sections.items() if v}


# 小节正文的终止标记（出现即认为该小节结束）
_SECTION_STOP_RE = re.compile(r'(特此公告|联系人[：:]|附图[：:]|拟征收土地位置示意图|'
                              r'制图日期|比例尺|图例|二〇[一二三四五六七八九〇]+年)')
# 「整行就是一个日期」= 落款日期
_STANDALONE_DATE_RE = re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日[。]?')


def parse_announcement(text: str) -> Dict[str, Any]:
    """**完整**解析一份征收土地预公告，抽出全部可用内容。

    覆盖：标题 / 文号 / 发文机关 / 发布日期 / 前言（法律依据+决定）/
    征收目的全文+法定条款 / 征收范围全文 / 公告期限 / 工作安排全文 /
    其他事项全文 / 联系人电话 / 附图说明 / 附图内的地块与权属信息。

    Args:
        text: 预公告逐字文本（PDF 文字层或 OCR 全文）。

    Returns:
        字段字典；取不到的键不出现在结果里。
    """
    t = str(text or "")
    if not t.strip():
        return {}
    merged = _merge_wrapped_lines(t)
    out: Dict[str, Any] = {}

    # ── 基础标识 ──
    dr = extract_doc_reference(t)
    if dr:
        out["doc_reference"] = dr
    org = extract_org_name(t)
    if org:
        out["org_name"] = org
    loc = extract_location(t)
    if loc:
        out["location"] = loc

    # 标题行（含「公告」的短行；排除「三、公告期限」这类小节的序号行）
    _raw_lines = [l.strip() for l in t.splitlines() if l.strip()][:10]
    for line in _raw_lines:
        if "公告" not in line or not (4 <= len(line) <= 40):
            continue
        if re.search(r'[。；;：:]', line):
            continue
        if re.match(r'^[一二三四五六七八九十\d]+\s*[、.．]', line):
            continue
        out["announcement_title"] = line
        break

    # 发布日期：取正文中**最后出现**的独立日期行（落款日期）
    for line in reversed(_raw_lines + [l.strip() for l in t.splitlines() if l.strip()]):
        m = re.fullmatch(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', line)
        if m:
            out["publish_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            break

    # ── 前言段（法律依据 + 决定启动）──
    m = re.search(r'(根据《[^》]+》(?:《[^》]+》)*.*?(?:。|现将[^。\n]*[：:]))(?=一、|\n|$)', merged)
    if m:
        out["preamble_text"] = m.group(1).strip()
    laws = [x for x in _LAW_REF_RE.findall(t) if any(
        k in x for k in ("土地管理法", "土地管理条例", "城乡规划法", "实施条例", "民法典"))]
    if laws:
        out["legal_basis_cited"] = "、".join(
            f"《{re.sub(chr(92)+'s+', '', x)}》" for x in dict.fromkeys(laws))

    # ── 正文各小节 ──
    secs = extract_sections(t)
    for name, body in secs.items():
        if not body:
            continue
        if "征收目的" in name:
            out["purpose_text"] = body
            m2 = re.search(r'《([^》]+)》\s*第([一二三四五六七八九十百零\d]+)条', body)
            if m2:
                out["purpose_legal_basis"] = f"《{m2.group(1)}》第{m2.group(2)}条"
            # 🔴 征收目的里通常写明用途（「…等基础设施建设需要用地」「用于XX项目建设」）——
            #    勘测定界报告只有地类表时，这里是「土地用途」的唯一来源。
            m3 = re.search(r'(?:目的为|用于|属于)([^。，]{4,60}?(?:用地|项目))', body)
            if m3:
                _lu = m3.group(1).strip()
                _lu = re.sub(r'^(?:由政府组织实施的|由政府|由|经政府批准的)', '', _lu)
                if 4 <= len(_lu) <= 50:
                    out["land_use"] = _lu
        elif "征收范围" in name:
            out["scope_text"] = body
            m2 = re.search(r'位于([^。，,]{4,40})', body)
            if m2 and "location" not in out:
                out["location"] = m2.group(1).strip()
        elif "公告期限" in name or "公示期限" in name:
            out["notice_period_text"] = body
            m2 = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'
                           r'\s*(?:至|-|—|~)\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', body)
            if m2:
                out["notice_date"] = f"{m2.group(1)}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
                out["notice_end_date"] = f"{m2.group(4)}-{int(m2.group(5)):02d}-{int(m2.group(6)):02d}"
        elif "工作安排" in name:
            out["work_arrangement_text"] = body
            m2 = re.search(r'将组织开展(.{2,20}?)(?:。|，)', body)
            if m2:
                out["work_scope"] = m2.group(1).strip()
        elif "其他事项" in name or "其他" in name:
            out["other_matters_text"] = body
        else:
            # 未识别的小节也保留原文（键名用标题），避免丢内容
            out[f"section_{name}"] = body

    # 公告期限兜底（写成单句时）
    if "notice_period_text" not in out:
        m = re.search(r'(?:公告|公示)期限为?\s*([^\n。]{6,40})', merged)
        if m:
            out["notice_period_text"] = m.group(1).strip()

    # ── 联系人 / 电话 ──
    m = re.search(r'联系人[：:]\s*([^\s；;，,、]+)', t)
    if m:
        out["contact_name"] = m.group(1).strip()
    m = re.search(r'(?:电话|联系电话)[：:]\s*([\d\-—]{7,20})', t)
    if m:
        out["contact_phone"] = m.group(1).strip()

    # ── 附图说明 ──
    m = re.search(r'附图[：:]\s*([^\n。]{2,30})', t)
    if m:
        out["attachment_note"] = m.group(1).strip()

    # ── 附图内的地块/权属信息（地图标注文字）──
    plots = re.findall(r'拟征收地块\s*([\d\-—]+)\s*[，,]?\s*涉及\s*([^\n]{2,30})', t)
    if plots:
        out["map_plots"] = [{"plot_no": a.strip(), "owner": b.strip()} for a, b in plots]
    m = re.search(r'比例尺[：:]\s*1\s*[:：]\s*(\d+)', t)
    if m:
        out["map_scale"] = f"1:{m.group(1)}"
    m = re.search(r'制图日期[：:]\s*([^\n]{4,20})', t)
    if m:
        out["map_date"] = m.group(1).strip()

    # ═══════════════════════════════════════════════════════════
    # 🔴 补充抽取（业主 2026-09-29：「要解析所有的资料…运用在报告上」）
    #    以下字段此前**根本没有解析器**，逐章弹窗只能回头问用户。
    #    统一原则：**宁缺勿错** —— 语境不明确就不收，绝不猜一个数进事实卡。
    # ═══════════════════════════════════════════════════════════

    # ── 涉及户数（要求明确语境，避免把「共3户」之外的数字抓进来）──
    if "household_count" not in out:
        m = re.search(r'(?:涉及|共涉及|共计)\s*(?:被征地)?(?:农户|居民户|户数)?\s*'
                      r'(\d{1,5})\s*户', t)
        if m:
            out["household_count"] = m.group(1)

    # ── 涉及人口 ──
    if "population_count" not in out:
        m = re.search(r'(?:涉及|共涉及|安置)\s*(?:农业)?(?:人口|人数|人员)\s*'
                      r'(\d{1,6})\s*人', t)
        if m:
            out["population_count"] = m.group(1)

    # ── 四至范围：要求**东至…南至…西至…北至**四段齐全才收（缺一段不收）──
    m = re.search(r'(东至[^。；;\n]{1,40}?南至[^。；;\n]{1,40}?'
                  r'西至[^。；;\n]{1,40}?北至[^。；;\n]{1,40})', t)
    if m:
        out["boundary_desc"] = m.group(1).strip("，,。；;、 ")

    # ── 资金筹措单位（如「资金筹措：洪泽区财政局」/「资金由区财政统筹安排」）──
    m = re.search(r'资金(?:筹措|来源)[：:是为]?\s*([^\n。；，,]{2,30})', t)
    if m:
        _f = m.group(1).strip()
        _f = re.sub(r'^(?:由|由本级|由区|由市)', '', _f)
        if 2 <= len(_f) <= 30:
            out["funding"] = _f
    else:
        m = re.search(r'资金由([^\n。；，,]{2,24}?)(?:统筹|安排|保障|筹措)', t)
        if m:
            out["funding"] = (m.group(1).strip() + "统筹").strip()

    # ── 补偿标准（只认明确句式，**宁缺勿错**：抓不准就不写进事实卡）──
    if "compensation_standard" not in out:
        m = re.search(r'(?:土地|征地)?补偿(?:标准|安置补助费标准|费用标准)\s*'
                      r'(?:为|：|:)\s*([^\n。；]{2,60})', t)
        if m:
            out["compensation_standard"] = m.group(1).strip()
        else:
            # 「…补偿标准按《江苏省征地区片综合地价》执行」这类
            m = re.search(r'(?:土地|征地)?补偿标准[^\n。；]{0,8}?'
                          r'(?:按|按照|依据)\s*([^\n。；]{2,60})', t)
            if m:
                _cs2 = m.group(1).strip().rstrip("执行").strip()
                if _cs2:
                    out["compensation_standard"] = _cs2

    # ── 镇（街道）/ 村（社区）：从位置与征收范围原文里按后缀拆 ──
    _scope_txt = " ".join(str(x) for x in (out.get("location"), out.get("scope_text")) if x)
    if _scope_txt:
        _mt = re.search(r'([一-龥]{2,18}?(?:街道|镇|乡|开发区|园区|林场|农场))', _scope_txt)
        if _mt:
            out["town"] = _strip_town_prefix(_mt.group(1))
        # 🔴 村（社区/居委会）后面常紧跟村组名（「三圩社区二组」）——不能要求后面是句读，
        #    否则「三圩社区二组」整串都匹配不上，`village` 永远是空。
        #    ⚠️ 村級单位不止「村/社区」——城镇征地里大量写「XX居委会」
        #       （实测洪泽「三河镇八里居委会集体」漏掉了 居委会 这一支）。
        _mv = re.search(r'([一-龥]{2,14}?(?:社区|村|居委会))'
                        r'(?:居民委员会|村民委员会|委会|委员会|'
                        r'[一二三四五六七八九十\d]|组|、|,|，|。|范围|集体|$)', _scope_txt)
        if _mv:
            # 剥掉可能被一起吞进来的行政层级（「朱坝街道三圩社区」→「三圩社区」）
            _v = re.sub(r'^.*(?:街道|镇|乡|开发区|园区|林场|农场|办事处)', '', _mv.group(1))
            out["village"] = (_v or _mv.group(1)).strip()

    return out


def _strip_town_prefix(name: str) -> str:
    """剥掉镇/街道名前的省市区县前缀（「淮安市洪泽区朱坝街道」→「朱坝街道」）。

    只在**结果仍以 街道/镇/乡 结尾**时才剥 —— 否则「某某开发区」会被剥成空。
    """
    s = str(name or "").strip()
    for _ in range(4):
        if not re.search(r'(?:街道|镇|乡)$', s):
            break
        n = re.sub(r'^[一-龥]{2,8}?(?:省|市|区|县)', '', s)
        if n == s or not n or not re.search(r'(?:街道|镇|乡)', n):
            break
        s = n
    return s


# ── 决策主体 / 责任单位：不限行政区划层级 ──
_ORG_RE = re.compile(
    r'([一-龥]{2,14}?)(?:人民政府|管理委员会|管委会|街道办事处|镇政府|乡政府|'
    r'自然资源和规划局|自然资源局|住房和城乡建设局|发展和改革委员会)'
)

# 🔴 前缀里出现这些词 → 是从法规条款/行文里串进来的，不是机构名。
#    典型误判：「经省级以上人民政府批准由县级以上地方人民政府组织实施」中的
#    「批准由县级以上地方人民政府」——它是《土地管理法》第四十五条原文，
#    在公告的「征收目的」里反复出现，取「出现次数最多」就会被选中。
_ORG_PREFIX_STOP = (
    '由', '经', '报', '批准', '组织', '实施', '公告', '决定', '依照', '根据',
    '按照', '同意', '授权', '委托', '会同', '依法', '程序', '以上', '各级',
    '的', '该', '本', '和', '与', '及', '或', '以及', '相关', '有关', '地方',
    '所在地', '拟', '申请', '呈报', '审批', '审核', '备案',
)


def extract_org_name(text: str) -> str:
    """取公告落款的决策主体/责任单位全称（不写死地区名）。

    优先级：**独占一行的落款** > 出现次数 > 名称长度。
    法规条款里的泛指称谓（县级以上地方人民政府/各级人民政府）一律不取。
    """
    if not text:
        return ""
    from collections import Counter
    cands = []          # (是否独占一行, 名称)
    for m in _ORG_RE.finditer(text):
        prefix, name = m.group(1), m.group(0)
        if any(s in prefix for s in _ORG_PREFIX_STOP):
            continue
        # 紧邻的前一个字是介词/动词 → 同样判为条款串
        if m.start() and text[m.start() - 1] in '由经报批和与及或':
            continue
        _own = (m.start() == 0 or text[m.start() - 1] in '\n\r') and \
               (m.end() >= len(text) or text[m.end()] in '\n\r')
        cands.append((_own, name))
    if not cands:
        return ""
    cnt = Counter(n for _, n in cands)
    _own_names = {n for o, n in cands if o}
    if _own_names:
        # 落款优先：独占一行且靠后的那个才是发文机关
        return max(_own_names, key=lambda n: (cnt[n], len(n)))
    return max(cnt.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
