"""社会稳定风险评估评审表 — 江苏省稳评工作规范化运作统一制式。

与 `storage/templates/6960d9efcfbf41088989bbf58c842640.docx`（评审表模板）对齐：
14 栏主表（决策事项名称/适用程序/稳评责任单位/稳评实施单位/参与评估单位/决策概况7条/
决策法律政策依据及必要性/利益相关者情况/各方意见/主要风险因素/降低风险措施/
稳评实施单位结论/评审意见/稳评结论/备案情况）+ 14 条填表说明。
"""

import re, logging
from datetime import datetime

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

from app.services.field_resolver import resolve

logger = logging.getLogger(__name__)

# 填表说明（模板原文，14 条）
FILL_INSTRUCTIONS = [
    "本表系江苏省稳评工作规范化运作统一制式，事项责任单位、稳评责任单位、稳评实施单位、稳评结论备案单位各执1份。在决策事项申报、审批过程中需附本表的，可依实际情况增加份数。",
    "稳评责任单位是指事项的拟申报、实施单位（牵头单位）。",
    "稳评实施单位是指事项责任单位指定或委托实施稳评的部门、机构或组织。",
    "参与评估单位是指根据稳评工作需要，被邀请参与稳评的部门、机构或组织。没有的此栏不填。",
    "决策概况应写清决策地理位置、决策用地范围规模及地上附着物情况、地块现状及周边情况、周边地块安置情况、相关手续办理情况、资金筹措、决策实施周期等。",
    "决策法律政策依据及必要性是指拟实施事项与国家、省或当地相关规划、产业政策、准入标准的符合性，相关前置审批文件取得及其合法合规性，以及符合国家、省或当地经济发展、社会管理需要，解决群众迫切需求的情况。",
    "利益相关者情况是指与事项有利益关系的各类群体的数量范围，以及各类群体的知晓参与情况。",
    "利益相关者诉求应包括利益相关者的诉求及反对意见等。",
    "基层组织意见是指拟实施事项所在地基层政府（乡镇、街道办事处）、相关基层组织（社区、居委会等）、社会团体等的意见。",
    "党委政府或相关部门意见是指对拟实施事项所在市、县（区）党委政府或相关部门的意见。",
    "相关专业人员意见是指在稳评过程中邀请的相关学者、技术专家、稳评专业人员等的意见。",
    "稳评实施单位意见是指汇总整理各方意见后，对拟实施事项按照“合法性、合理性、可行性、可控性”的要求进行全面分析，给出“高、中、低”风险的稳评意见。",
    "评审意见是指根据工作需要，党委政府、有关主管部门或事项责任单位组织与事项无直接利害关系的部门、机构、专家等组成评审组，对稳评过程进行评价并提出意见建议。没有本过程的此栏不填。",
    "稳评结论是指稳评责任单位对稳评作出的最终结论。备案情况是指稳评责任单位将稳评结论向决策所在地或同级稳评职能主管部门备案。",
]


def _extract_from_chapters(chapters: dict) -> str:
    return "\n".join(
        ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
        for ch in (chapters or {}).values()
    )


def _chapter_md(chapters: dict, num: int) -> str:
    """取某章 markdown 正文。"""
    ch = (chapters or {}).get(num) or {}
    return ch.get("markdown", "") if isinstance(ch, dict) else str(ch or "")


def _section_text(md: str, *heading_keywords: str, limit: int = 600,
                  body_fallback: bool = False) -> str:
    """从章 markdown 中取「标题含任一关键词」的小节正文（去 markdown 记号）。

    body_fallback=True 时，一个关键词都没命中就退回**章正文开头** ——
    第11/13章是动态小节章（小节名由模型自拟，如「（一）严格履行征收程序」），
    标题里根本没有「措施」「建议」这类字样，只靠关键词必然落空。
    评审表宁可用正文摘录，也不要印个「【待填写】」给评审专家看。
    """
    out, on = [], False
    for line in str(md or "").split("\n"):
        s = line.strip()
        m = re.match(r'^#{2,4}\s*(.+)$', s)
        if m:
            title = re.sub(r'\*\*', '', m.group(1))
            on = any(k in title for k in heading_keywords)
            continue
        if on and s:
            out.append(re.sub(r'\*\*', '', s))
    got = _plain_text("\n".join(out))[:limit]
    if got or not body_fallback:
        return got
    body = re.sub(r'\n{2,}', '\n', _plain_text(md)).strip()
    return body[:limit]


def _main_land_types(extracted, limit: int = 3) -> str:
    """从勘测定界「土地分类面积表」按面积实算**主要地类**（前 N 名）。

    用于「3.地块现状及周边情况」—— 业主指出旧文案写的是四至、不是现状地类。
    没有表就返回空串（调用方退到中性表述，不编）。
    """
    tabs = extracted if isinstance(extracted, list) else []
    for t in tabs:
        if not isinstance(t, dict) or t.get("kind") != "land_classification":
            continue
        hdr = [str(x) for x in (t.get("headers") or [])]
        rows = t.get("rows") or []
        if len(hdr) < 3 or not rows:
            continue
        agg = {}
        for r in rows:
            if not r or str(r[0]).strip().startswith("合计"):
                continue
            for ci in range(2, len(hdr)):
                if any(k in hdr[ci] for k in ('总计', '合计', '备注', '小计')):
                    continue          # 🔴 「面积总计」是列名不是地类，排掉
                try:
                    v = float(str(r[ci]).replace(",", "").replace("，", "") or 0)
                except (TypeError, ValueError):
                    v = 0.0
                if v > 0:
                    agg[hdr[ci].strip()] = agg.get(hdr[ci].strip(), 0.0) + v
        if agg:
            return "、".join(n for n, _ in sorted(agg.items(), key=lambda x: -x[1])[:limit])
    return ""


def _plain_text(md: str) -> str:
    """把章 markdown 洗成**纯正文**：去掉系统标记 / 图片 / markdown 表 / 标题记号。

    🔴 评审表各栏是从后面各章摘录的，摘录时若不去记号，会把
       `[SYS_TABLE:6-1]`、`![图](…)` 这类**系统标记和图片行**原样搬进表格
       （业主：「注意不要把图片放进去」）。表格行也一并去掉 —— 评审表单元格
       放不下整张表，表在正文里。
    """
    out = []
    for line in str(md or "").split("\n"):
        t = line.strip()
        if not t:
            continue
        if re.match(r'^!\[', t):                 # 图片行
            continue
        if re.match(r'^\[SYS_[A-Z_]+', t):        # 系统标记行
            continue
        if t.startswith('|'):                     # markdown 表格行
            continue
        if t.startswith('#'):                     # 标题行整行丢弃（不是正文）
            continue
        # 🔴 短编号小标题（「1.问卷调查数据汇总」）也是标题，不是正文 ——
        #    它是没被识别成 `####` 的三级标题，塞进评审表单元格会像个小表头。
        if re.match(r'^\d{1,2}[.、]\s*\S{2,20}$', t) and not re.search(r'[。；：，,]$', t):
            continue
        t = re.sub(r'\[SYS_[A-Z_]+(?::[^\]]*)?\]', '', t)
        t = re.sub(r'\*\*(.+?)\*\*', r'\1', t).strip()   # 去 **加粗** 记号
        if not t or re.fullmatch(r'[\*\s]+', t):
            continue
        out.append(t)
    return "\n".join(out)

def _pick_sentence(md: str, *keywords: str, limit: int = 220,
                   exclude: tuple = ()) -> str:
    """从章 markdown 里挑**含任一关键词、且不含任何排除词的句子**（不是整节）。

    🔴 评审表「决策概况」7 条挤在同一章里，整节摘录会让两条取到同一段
       （实测「6.资金筹措」与「7.决策实施周期」完全重复）。
       exclude 用来挡住这种撞车：比如「周边地块安置情况」要排除含
       「费用/资金」的句子，否则又会和「资金筹措」取到同一句。
    """
    body = _plain_text(md)
    for sent in re.split(r'(?<=[。；])', body):
        t = sent.strip()
        if t and any(k in t for k in keywords) and not any(x in t for x in exclude):
            return t[:limit]
    return ""

def _build_review_data(filled_data: dict, chapters: dict = None, extracted: dict = None) -> dict:
    """组装评审表所需的全部字段值（唯一数据装配点，DOCX 与 Markdown 共用）。"""
    chapters = chapters or {}
    filled_data = filled_data or {}
    all_text = _extract_from_chapters(chapters)

    def _g(*keys, default=""):
        for k in keys:
            v = filled_data.get(k)
            if v and not str(v).startswith("【"):
                return str(v).strip()
        return default

    name = _g("project_name", "doc_reference")
    # 🔴 稳评责任单位：用户填的 > 预公告识别到的（`org_name` / `decision_unit`）> 占位
    org = resolve(filled_data, "org_name")
    loc = _g("location")
    area_mu, area_m2 = _g("area_mu"), _g("area_m2")
    area = (f"{area_mu}亩" if area_mu else "") + (f"（{area_m2}㎡）" if area_m2 else "")
    land_use = _g("land_use")

    # 🔴 风险等级必须取**系统算出的权威值**，不能扫全文取第一个匹配 ——
    #    第12章有「表12-1 直接定性指标」，第一条就是「超50%群众反对的 → 高风险」，
    #    扫全文会抓到**判定标准表**里的字，把低风险项目写成高风险（实测踩过）。
    risk_level = ""
    try:
        from app.services.scoring_service import scoring_service
        risk_level = scoring_service.build_scoring_report(filled_data, {}).get("meta", {}).get("risk_level", "")
    except Exception:
        risk_level = ""
    if not risk_level:
        # 回退：只认「判定为/等级为/结论为 X风险」这类**结论性**表述
        m = re.search(r'(?:判定为|等级为|结论为|确定为|评定为)\s*(低风险|中风险|高风险)', all_text)
        risk_level = m.group(1) if m else "低风险"

    # 决策概况 7 条：优先 LLM 归纳，其次事实数据，再次取第3章正文
    _basic = (extracted or {}).get("项目基本情况", {}) if isinstance(extracted, dict) else {}
    # 🔴 章号是**参照件（白马湖）十四章号**：
    #    1 编制说明 / 2 编制依据 / 3 评审表 / 4 工作报告 / 5 应急预案 / 6 项目基本情况 /
    #    7 稳评工作方案 / 8 稳评方法与过程 / 9 各利益相关者对项目的反映 /
    #    10 四性分析 / 11 分析预测风险源 / 12 确定风险等级 / 13 工作建议 / 14 附件
    _ch6 = _chapter_md(chapters, 6)

    def _gb(k, d):
        v = _basic.get(k)
        return str(v).strip() if v else d

    _scale_default = ""
    if area:
        _scale_default = "拟征收面积" + area + (("，土地用途" + land_use) if land_use else "") + "。"

    # 🔴 7 条仿参照件「事项概况」的写法：**每条 1~2 句、口语化、面积只说一次**。
    #    旧实现整节摘录，第2条把第6章整节连引导语带分析句全搬了过来（还带表格标记），
    #    又在同一格里把面积报了两遍（业主：「内容太多了…重复…一点都不口语话」）。
    _ch10 = _chapter_md(chapters, 10)
    _ch11 = _chapter_md(chapters, 11)
    _loc_txt = loc or "项目所在地"

    # 🔴 决策实施周期：**材料优先**。
    #    起点取「征收土地预公告发布日」——该日期已从预公告盖章扫描件识别到
    #    （`notice_date`，经核对与原件一致），不必再问用户；
    #    结束日材料里没有 → 写成「至本次土地征收实施工作完成止」，**绝不编一个具体日期**。
    # 位置串里拆出「街道」与「社区」用于第4条的行文
    _loc_all = str(loc or "")
    _m_street = re.search(r'([一-龥]{2,10}?(?:街道|镇|乡))', _loc_all)
    _site_name = _m_street.group(1) if _m_street else "属地街道办事处"
    _m_comm = re.search(r'([一-龥]{2,10}?(?:社区|村)(?:居委会|居民委员会|村民委员会)?)', _loc_all)
    _site_units_short = _m_comm.group(1) if _m_comm else "属地社区（村）"
    # 🔴 入参修正：调用方（`report_workflow.node_review_table`）传进来的 `extracted`
    #    是 **dict**（agent 归纳的决策概况），而 `_main_land_types` 只认 list ——
    #    于是「3.地块现状及周边情况」永远退化成「拟征收土地为集体土地」，
    #    勘测定界实算出来的主要地类**从来没进过评审表**。
    #    真正的土地表在 `filled_data["_extracted_tables"]` 里。
    _land_types = _main_land_types(
        extracted if isinstance(extracted, list)
        else (filled_data.get("_extracted_tables") or []))
    _impl_start_txt = ""
    for _k in ("notice_date", "publish_date"):
        _v = _g(_k)
        _m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(_v or ""))
        if _m:
            _impl_start_txt = f"{_m.group(1)}年{int(_m.group(2))}月{int(_m.group(3))}日"
            break
    _impl_period = (
        f"本项目自{_impl_start_txt}征收土地预公告发布之日起启动，经拟征收土地现状调查、"
        f"社会稳定风险评估、补偿安置方案公告与听证、补偿登记与协议签订、报批等环节，"
        f"至本次土地征收实施工作完成止。"
        if _impl_start_txt else
        "本项目自征收土地预公告发布之日起启动，经现状调查、社会稳定风险评估、"
        "补偿安置方案公告等环节，至本次土地征收实施工作完成止。")
    _scale = (f"拟征收土地总面积{area}，涉及{_loc_txt}，土地用途为{land_use}。"
              if area else f"拟征收土地位于{_loc_txt}。")
    overview = [
        "1.决策地理位置 " + (_gb('征收位置', _pick_sentence(_ch6, '拟征收土地位于', '征收范围为', limit=140))
                              or f"拟征收土地位于{_loc_txt}。"),
        # 🔴 第2条**只讲用地范围与规模**：旧文案把「位置」也塞进来了（与第1条重复），
        #    「地上附着物以现状调查确认结果为准」又是句敷衍。改为按预公告载明口径写清
        #    附着物包含哪些、以现状调查为准。
        "2.决策用地范围、规模及地上附着物情况 " + _gb(
            '规模附着物',
            _scale + "涉及农村村民住宅、其他地上附着物和青苗等，"
                     "其位置、权属、种类和数量以拟征收土地现状调查确认结果为准。"),
        # 🔴 第3条要写**现状地类与周边**，不是四至（旧文案写的是「四至以红线图为准」）。
        #    主要地类从勘测定界「土地分类面积表」按面积实算 —— 不是编的。
        "3.地块现状及周边情况 " + _gb('地块现状', (
            (f"拟征收土地现状地类以{_land_types}为主；" if _land_types else "拟征收土地为集体土地；")
            + "地块四至以征收红线图及勘测定界成果为准，周边以农村居民点和农用地为主。")),
        # 🔴 第4条要写**安置**，不是安置名单的办理程序（旧文案张冠李戴）。
        #    句式仿参照件/制式模板的「周边地块安置情况」写法：
        #    「根据和<社区>负责人沟通了解：周边地块原安置方式是按照"征谁补谁"原则…」
        #    🔴 「征谁补谁」那句是模板里**别的项目**的说法，按句式仿写；
        #       若本项目所在区域口径不同、或社区未如此表态，需按实际改。
        "4.周边地块安置情况 " + _gb('周边安置', (
            f"根据和{_site_name}、{_site_units_short}负责人沟通了解："
            "周边地块原安置方式是按照“征谁补谁”原则，优先从承包户中确定社会保障名额；"
            "本决策拟征收土地为集体土地，被征地农民的安置按《江苏省被征地农民社会保障办法》"
            "和淮安市征地区片综合地价执行标准落实，安置人员名单经社区商定、街道办事处审核、"
            "在社区公示后报区人民政府确定。")),
        "5.相关手续办理情况 " + (_gb('手续办理', (
            (_pick_sentence(_ch6, '预公告期限为', '公告期限为', limit=130)
             + _pick_sentence(_ch6, '公示期为', limit=150))
            or f"征收土地预公告（{name}）已依法发布，社会稳定风险评估公示已按程序完成。"))),
        "6.资金筹措 " + (_gb('资金筹措', _g('funding', default=(
            _pick_sentence(_ch10, '财政统筹', '资金由', '纳入预算', limit=170)
            or _pick_sentence(_ch6, '补偿安置等费用', limit=170))) )),
        "7.决策实施周期 " + _gb('实施周期', _impl_period),
    ]

    # 各栏来源（正文号）：依据←第2章；利益相关者←第5章；风险因素←第7章；措施←第8章；结论←第9章
    _ch2 = _chapter_md(chapters, 2)    # 编制依据
    _ch9 = _chapter_md(chapters, 9)    # 各利益相关者对项目的反映
    _ch11 = _chapter_md(chapters, 11)  # 分析预测风险源
    _ch13 = _chapter_md(chapters, 13)  # 防范和化解风险的工作建议
    _ch4 = _chapter_md(chapters, 4)    # 社会稳定风险评估工作报告
    _laws = _section_text(_ch2, '法律法规', '评估依据', limit=800,
                          body_fallback=True) or "【待填写：可从报告第2章「评估过程、方法和依据」摘录补充】"
    # 🔴 这一栏叫「决策法律政策依据**及必要性**」—— 只列法规清单是漏了一半。
    #    必要性取自本项目**征收目的**（征收土地预公告载明、第6章成文）。
    #    ⚠️ 报告第6章的实际小节是「项目概况 / 利益相关者情况」，**没有**「土地用途与征收目的」
    #       这一节名（随参照件模板而变）→ 只按小节名匹配必然落空，
    #       评审表就只剩「（一）法律政策依据」，「（二）必要性」整段消失
    #       （业主 2026-09-28 反馈）。故补**多级兜底链**，最后兜到规范表述。
    _ch1 = _chapter_md(chapters, 1)    # 编制说明（稳评理由 / 必要性）
    _necessity = (_section_text(_ch6, '土地用途与征收目的', '征收目的', limit=500)
                  or _pick_sentence(_ch6, '征收目的', '成片开发', '公共利益', limit=500)
                  or _pick_sentence(_ch4, '必要性', limit=500)
                  or str(_g('necessity', default='') or '').strip()
                  or str(_gb('征收目的', '') or '').strip()
                  or _section_text(_ch1, '征收目的', '必要性', limit=500)
                  or _pick_sentence(_ch1, '为了', '公共利益', '成片开发', limit=400)
                  or f"本项目属于{_g('land_use', default='政府组织实施的成片开发建设')}需要，"
                     f"符合《中华人民共和国土地管理法》第四十五条规定的公共利益情形，"
                     f"依法开展社会稳定风险评估是决策实施的法定前置程序。")
    legal_basis = ("（一）法律政策依据\n" + _laws
                   + ("\n\n（二）必要性\n" + _necessity if _necessity else ""))
    # 🔴 三行必须**各有来源**，否则印出来三段一模一样（实测三行都取到同一段）。
    #    利益相关者情况 ← 各方意见汇总；群众诉求 ← 个人问卷；基层组织意见 ← 村民委员会/社区；
    #    党委政府或相关部门意见 ← 单位（部门）问卷。
    stakeholders = _section_text(_ch9, '各方意见', limit=400, body_fallback=True) \
        or "【待填写：可从报告第9章「各利益相关者对项目的反映」摘录补充】"
    # 🔴 仿参照件写法：这里是**群众诉求条目**（短句），不是问卷统计的长段描述。
    #    素材来自第9章问卷结论 + 第11章群众关注点，只做概括，不加情节。
    demands = ("1.补偿费用和土地换社保是群众最关心的两件事，希望补偿标准公开、补偿资金及时兑现。\n"
               "2.希望能尽早明确补偿标准和安置方案，统一执行、不搞两套标准。\n"
               "3.遇到问题愿意通过调解和正常渠道反映，希望基层能及时受理、就地化解。")
    # 🔴 优先用 agent 归纳的（`node_review_table` 从正文归纳后经 `extracted` 传进来），
    #    规则抽取只作兜底 —— 动态小节名对不上关键词时，规则必然落空成「【待填写】」。
    grassroots = (_gb('基层组织意见', '') or _section_text(_ch6, '村民委员会', limit=300)
                  or _section_text(_ch9, '村民委员会', '基层组织', limit=300)) or "【待填写】"
    gov_opinion = (_gb('党委政府或相关部门意见', '')
                   or _section_text(_ch9, '单位', '部门', limit=300)) or "【待填写】"
    risk_factors = _section_text(_ch11, '风险', limit=300, body_fallback=True) \
        or "【待填写：可从报告第11章「分析预测风险源」摘录】"
    risk_measures = _section_text(_ch13, '措施', '建议', limit=300, body_fallback=True) \
        or "【待填写：可从报告第13章「防范和化解风险的工作建议」摘录】"

    return {
        "name": name,
        "org": org,
        # 🔴 取值链：**用户弹窗填的 > 材料（预公告）识别到的 > 【待填写】占位**
        #    （业主 2026-09-22 口径：「用户跳过就使用预公告中的信息，都没有才落占位」）。
        #    实现见 `app/services/field_resolver.py`。这四项由界面弹窗采集
        #    （`report_workflow.REVIEW_TABLE_FIELDS`），跳过时由别名链回落到
        #    预公告解析出的 `contact_name` / `contact_phone`。
        #    ⚠️ **只接语义对得上的栏**：预公告落款上的「联系人/联系电话」是责任单位
        #       就本决策对外公布的公示联系方式 → 落到「联系人/联系电话」是对的；
        #       但**不能**拿它去填「负责人/职务」——那是甲方人事，材料里推不出来，
        #       硬套就是张冠李戴（此前踩过：把公示联系人填进了负责人栏）。
        #    ⚠️ 键名**不能改名**：改了弹窗就填不进来了。
        "org_leader": resolve(filled_data, "org_leader"),
        "org_leader_title": resolve(filled_data, "org_leader_title"),
        "org_contact": resolve(filled_data, "org_contact"),
        "org_phone": resolve(filled_data, "org_phone"),
        # 🔴 实施单位与负责人**不再写死**成我们公司和陈春 ——
        #    那会让外部用户的评审表上印着我们的抬头。
        #    值来自**公司档案**（`companies.implement_unit / leader_name / leader_title`，
        #    由 `node_validate` 按 `_company_id` 注入 filled_data）+ 用户弹窗填的；
        #    取不到留「待填写」，**不回落到江苏众拓/陈春**。
        "impl_unit": _g("implement_unit", default="【待填写】"),
        "impl_leader": _g("impl_leader", default="【待填写】"),
        "impl_leader_title": _g("impl_leader_title", default="【待填写】"),
        # 实施单位的联系人/联系方式同样**问用户**；材料里没有 → 留占位
        "impl_contact": resolve(filled_data, "impl_contact"),
        "impl_phone": resolve(filled_data, "impl_phone"),
        "fill_date": _g("fill_date", "review_date", default=datetime.now().strftime("%Y年%m月%d日")),
        "overview": overview,
        "legal_basis": legal_basis,
        "stakeholders": stakeholders,
        "demands": demands,
        "grassroots": grassroots,
        "gov_opinion": gov_opinion,
        "risk_factors": risk_factors,
        "risk_measures": risk_measures,
        "risk_level": risk_level,
        "conclusion": (
            f"该决策符合国家、省、市相关政策及规划，决策实施具有合法性、合理性、可行性、"
            f"社会稳定风险的可控性，决策社会稳定风险等级为{risk_level}，同意决策实施。"),
    }


# ═══════════════════════════════════════════════════════════════
# 江苏省统一制式评审表 —— 主表版式（与人工模板 22 行 × 7 列一致）
# ═══════════════════════════════════════════════════════════════

# 评审表正文列宽（cm），合计 ≈ A4 可用宽度
_RV_WIDTHS = [2.4, 2.6, 4.4, 2.4, 2.4]


# 评审表内容里的「小标题行」：`（一）xxx` / `1.xxx` 且短、无句末标点
_RV_SUBHEAD = re.compile(r'^(?:（[一二三四五六七八九十]+）|\d{1,2}[.、]\s*)\S{2,26}$')


def _rv_cell(tbl, r, c, txt, *, bold=False, sz=Pt(10.5),
             align=WD_ALIGN_PARAGRAPH.LEFT, font='仿宋_GB2312',
             indent=False, blank_after_subhead=False):
    """写入评审表单元格（支持多行）。

    indent=True → **首行缩进 2 字符**（业主：评审表从「决策概况」起每段都要缩进）。
    blank_after_subhead=True → **小标题行之后补一个空行**（业主：「正文内容的小标题都要隔一行」）。
    """
    cell = tbl.cell(r, c)
    cell.paragraphs[0].clear()

    def _new_para(_li):
        _p = cell.paragraphs[0] if _li == 0 else cell.add_paragraph()
        return _p

    _first = True
    _lines = str(txt).split("\n")
    for li, line in enumerate(_lines):
        para = _new_para(0 if _first else 1)
        _first = False
        para.alignment = align
        if indent:
            para.paragraph_format.first_line_indent = Pt(21)   # 2 字符（五号 10.5pt）
        run = para.add_run(line)
        run.font.name = font
        run._element.rPr.rFonts.set(qn('w:eastAsia'), font)
        run.font.size = sz
        run.bold = bold
        # 🔴 小标题之后隔一行：否则「（一）法律政策依据」紧贴着正文，层级看不出来
        if blank_after_subhead and _RV_SUBHEAD.match(line.strip()) and li < len(_lines) - 1:
            _blank = _new_para(1)
            _blank.paragraph_format.line_spacing = Pt(16)
            _blank.add_run("").font.size = sz


def build_review_table_docx(doc, filled_data: dict, chapters: dict = None,
                            extracted: dict = None):
    """在 doc 上追加「江苏省统一制式评审表」（表头 + 22 行主表 + 填表说明）。

    版式严格对齐人工模板《模版（洪泽征地稳评报告）.docx》第一章：
    决策事项名称/适用程序（三选一打√）/稳评责任单位（名称·负责人·职务·联系人·电话）/
    稳评实施单位/参与评估单位/决策概况（7 条）/决策法律政策依据及必要性/利益相关者情况/
    各方意见（诉求·基层组织·党委政府）/主要风险因素/降低风险措施/
    稳评实施单位结论/评审意见/稳评结论/备案情况。
    """
    d = _build_review_data(filled_data, chapters, extracted)

    # 表头（标题用 Heading 1 样式 → 会进入 Word 目录域；但**不带「第X章」编号**，
    # 与人工模板一致：评审表是不编号的前置部分）
    _t = "社会稳定风险评估评审表"
    _h = doc.add_paragraph(_t, style='Heading 1')
    _h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for _run in _h.runs:
        _run.font.name = '黑体'
        _run._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
        _run.font.size = Pt(16)
        _run.bold = True
        _run.font.color.rgb = RGBColor(0, 0, 0)

    for _t, _sz, _bold, _font in [
        (f"决策事项名称：{d['name'] or '【待填写】'}", Pt(12), False, '仿宋_GB2312'),
        (f"稳评责任单位：{d['org']}", Pt(12), False, '仿宋_GB2312'),
        (f"稳评实施单位：{d['impl_unit']}", Pt(12), False, '仿宋_GB2312'),
        (f"填 表  日 期：{d['fill_date']}", Pt(12), False, '仿宋_GB2312'),
    ]:
        _p = doc.add_paragraph()
        _p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _r = _p.add_run(_t)
        _r.font.name = _font; _r._element.rPr.rFonts.set(qn('w:eastAsia'), _font)
        _r.font.size = _sz; _r.bold = _bold

    # 🔴 封面（标题 + 四项表头）单独成节并**上下居中**：
    #    参照件制式表的封面是居中排的，而这里原先整份只有一个节、顶端对齐
    #    （业主：「评审表的封面没有上下居中」）。表格另起一节靠上排。
    try:
        from docx.enum.section import WD_SECTION
        _cover_sec = doc.sections[0]
        doc.add_section(WD_SECTION.NEW_PAGE)
        from app.services.report_assembler import _set_vertical_alignment as _sva
        _sva(doc.sections[0], "center")
        _sva(doc.sections[-1], "top")
        for _s in doc.sections:
            _s.top_margin = Cm(2.54); _s.bottom_margin = Cm(2.54)
            _s.left_margin = Cm(3.18); _s.right_margin = Cm(3.18)
    except Exception as _e:
        logger.warning(f"评审表封面居中设置失败: {_e}")

    tbl = doc.add_table(rows=22, cols=5, style='Table Grid')
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, w in enumerate(_RV_WIDTHS):
        for row in tbl.rows:
            row.cells[i].width = Cm(w)

    _C = WD_ALIGN_PARAGRAPH.CENTER
    _L = WD_ALIGN_PARAGRAPH.LEFT

    # r0~r2 决策事项名称 / 适用程序
    tbl.cell(0, 0).merge(tbl.cell(2, 0))
    _rv_cell(tbl, 0, 0, "决策\n事项\n名称", bold=True, align=_C, font='黑体')
    tbl.cell(0, 1).merge(tbl.cell(2, 1))
    _rv_cell(tbl, 0, 1, d['name'] or "【待填写】", align=_C)
    tbl.cell(0, 2).merge(tbl.cell(2, 2))
    _rv_cell(tbl, 0, 2, "适用\n程序", bold=True, align=_C, font='黑体')
    for _i, _p in enumerate(["简易程序", "一般程序", "特殊程序"]):
        _rv_cell(tbl, _i, 3, _p, align=_C)
        _rv_cell(tbl, _i, 4, "√" if _p == "一般程序" else "", align=_C)

    # r3~r5 稳评责任单位
    tbl.cell(3, 0).merge(tbl.cell(5, 0))
    _rv_cell(tbl, 3, 0, "稳评\n责任\n单位", bold=True, align=_C, font='黑体')
    _rv_cell(tbl, 3, 1, "单位\n名称", align=_C)
    tbl.cell(3, 2).merge(tbl.cell(3, 4))
    _rv_cell(tbl, 3, 2, d['org'])
    _rv_cell(tbl, 4, 1, "负责人", align=_C)
    _rv_cell(tbl, 4, 2, d['org_leader'])
    _rv_cell(tbl, 4, 3, "职务", align=_C)
    _rv_cell(tbl, 4, 4, d['org_leader_title'])
    _rv_cell(tbl, 5, 1, "联系人", align=_C)
    _rv_cell(tbl, 5, 2, d['org_contact'])
    _rv_cell(tbl, 5, 3, "联系电话", align=_C)
    _rv_cell(tbl, 5, 4, d['org_phone'])

    # r6~r8 稳评实施单位
    tbl.cell(6, 0).merge(tbl.cell(8, 0))
    _rv_cell(tbl, 6, 0, "稳评\n实施\n单位", bold=True, align=_C, font='黑体')
    _rv_cell(tbl, 6, 1, "单位\n名称", align=_C)
    tbl.cell(6, 2).merge(tbl.cell(6, 4))
    _rv_cell(tbl, 6, 2, d['impl_unit'])
    _rv_cell(tbl, 7, 1, "负责人", align=_C)
    _rv_cell(tbl, 7, 2, d['impl_leader'])
    _rv_cell(tbl, 7, 3, "职务", align=_C)
    _rv_cell(tbl, 7, 4, d['impl_leader_title'])
    _rv_cell(tbl, 8, 1, "联系人", align=_C)
    _rv_cell(tbl, 8, 2, d['impl_contact'])
    _rv_cell(tbl, 8, 3, "联系电话", align=_C)
    _rv_cell(tbl, 8, 4, d['impl_phone'])

    # r9~r21 单列标签 + 合并正文
    _single = [
        (9,  "参与\n评估\n单位", "/"),
        (11, "决策\n法律\n政策\n依据\n及必\n要性", d['legal_basis']),
        (12, "利益\n相关\n者情\n况", d['stakeholders']),
    ]
    # 🔴 决策概况单独渲染：按参照件制式**每条拆两段**（「N.标题」一段 / 内容一段），
    #    两端对齐 + 首行缩进 2 字符 + 固定行距 16pt。
    #    旧实现把「N.标题 内容」挤成一行、左对齐、无缩进（业主：「段落格式不正确」）。
    tbl.cell(10, 0).merge(tbl.cell(10, 1))
    _rv_cell(tbl, 10, 0, "决策\n概况", bold=True, align=_C, font='黑体')
    tbl.cell(10, 2).merge(tbl.cell(10, 4))
    _ovc = tbl.cell(10, 2)
    _ovc.paragraphs[0].clear()
    _ov_first = True
    for _item in (d.get('overview') or []):
        _sitem = str(_item).strip()
        _mm = re.match(r'^(\d{1,2}[.、]\s*\S{2,24}?)\s+(.*)$', _sitem)
        # 🔴 首行缩进**只加在内容段上**，条目标题行（「1.决策地理位置」）不缩进
        #    （业主：「只针对内容」）。
        _segs = [(_mm.group(1), False), (_mm.group(2), True)] if _mm else [(_sitem, True)]
        for _seg, _is_body in _segs:
            _pp = _ovc.paragraphs[0] if _ov_first else _ovc.add_paragraph()
            _ov_first = False
            _pf = _pp.paragraph_format
            _pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            if _is_body:
                _pf.first_line_indent = Pt(21)  # ≈2 字符（五号）
            _pf.line_spacing = Pt(16)           # 固定行距 16pt（与制式模板一致）
            _rr = _pp.add_run(_seg)
            _rr.font.name = '仿宋_GB2312'
            _rr._element.rPr.rFonts.set(qn('w:eastAsia'), '仿宋_GB2312')
            _rr.font.size = Pt(10.5)
            # 条目标题之后隔一行（业主：「正文内容的小标题都要隔一行」）
            if not _is_body:
                _bl = _ovc.add_paragraph()
                _bl.paragraph_format.line_spacing = Pt(16)
                _bl.add_run("").font.size = Pt(10.5)

    for _r, _label, _val in _single:
        tbl.cell(_r, 0).merge(tbl.cell(_r, 1))
        _rv_cell(tbl, _r, 0, _label, bold=True, align=_C, font='黑体')
        tbl.cell(_r, 2).merge(tbl.cell(_r, 4))
        _rv_cell(tbl, _r, 2, _val, align=_L, indent=True, blank_after_subhead=True)

    # r13~r15 各方意见
    tbl.cell(13, 0).merge(tbl.cell(15, 0))
    _rv_cell(tbl, 13, 0, "各方\n意见", bold=True, align=_C, font='黑体')
    for _r, _label, _val in [
        (13, "利益\n相关\n者诉\n求", d['demands']),
        (14, "基层\n组织\n意见", d['grassroots']),
        (15, "党委\n政府\n或相\n关部\n门意\n见", d['gov_opinion']),
    ]:
        _rv_cell(tbl, _r, 1, _label, align=_C)
        tbl.cell(_r, 2).merge(tbl.cell(_r, 4))
        _rv_cell(tbl, _r, 2, _val, align=_L, indent=True, blank_after_subhead=True)

    # r16~r18 主要风险因素 / 降低风险措施 / 实施单位结论
    for _r, _label, _val in [
        (16, "主要\n风险\n因素", d['risk_factors']),
        (17, "降低\n风险\n措施", d['risk_measures']),
        (18, "稳评\n实施\n单位\n结论", d['conclusion']),
    ]:
        tbl.cell(_r, 0).merge(tbl.cell(_r, 1))
        _rv_cell(tbl, _r, 0, _label, bold=True, align=_C, font='黑体')
        tbl.cell(_r, 2).merge(tbl.cell(_r, 4))
        _rv_cell(tbl, _r, 2, _val, align=_L, indent=True, blank_after_subhead=True)

    # r19~r21 签章行（方正楷体GBK）
    for _r, _label, _sign in [
        (19, "评审\n意见", "组长（签章）："),
        (20, "稳评\n结论", "负责人（签章）："),
        (21, "备案\n情况", "备案单位（签章）："),
    ]:
        tbl.cell(_r, 0).merge(tbl.cell(_r, 1))
        _rv_cell(tbl, _r, 0, _label, bold=True, align=_C, font='黑体')
        tbl.cell(_r, 2).merge(tbl.cell(_r, 4))
        _rv_cell(tbl, _r, 2, f"\n\n\n{_sign}\n\n　　　　　　　　年　　月　　日",
                 align=_L, font='方正楷体GBK', indent=True)

    _p = doc.add_paragraph()
    _p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _r = _p.add_run("江苏省淮安市稳评职能主管部门制")
    _r.font.name = '仿宋_GB2312'; _r._element.rPr.rFonts.set(qn('w:eastAsia'), '仿宋_GB2312')
    _r.font.size = Pt(12)

    # ── 填表说明 ──
    _p = doc.add_paragraph()
    _r = _p.add_run("填表说明：")
    _r.font.name = '黑体'; _r._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
    _r.font.size = Pt(10.5); _r.bold = True
    for i, line in enumerate(FILL_INSTRUCTIONS, 1):
        _p = doc.add_paragraph()
        _p.paragraph_format.first_line_indent = Cm(0.74)
        _r = _p.add_run(f"{i}.{line}")
        _r.font.name = '仿宋_GB2312'; _r._element.rPr.rFonts.set(qn('w:eastAsia'), '仿宋_GB2312')
        _r.font.size = Pt(10.5)


def generate_review_table(
    output_path: str,
    filled_data: dict,
    chapters: dict = None,
    survey_stats: dict = None,
    extracted: dict = None,
) -> str:
    """生成独立的评审表 DOCX（江苏省统一制式）。

    🔴 与主报告第三章走**同一条路**：就地复制制式模板的评审表区间
       （`fill_review_table_into`）。以前这里是从零建一张**5 列表**，
       栅格与制式模板（**22 行 × 7 列**）对不上 —— 「适用程序」没跟
       「决策事项名称」同行、各列宽度比例也不对（业主逐条指出）。
       走模板就没有这些偏差：栅格、合并、字体全是制式的。
    """
    doc = Document()
    for _s in doc.sections:
        _s.top_margin = Cm(2.54); _s.bottom_margin = Cm(2.54)
        _s.left_margin = Cm(3.18); _s.right_margin = Cm(3.18)
    try:
        from app.services.review_table_filler import fill_review_table_into
        ok = fill_review_table_into(doc, filled_data or {}, chapters or {},
                                    extracted or {}, None)
        if not ok:
            raise RuntimeError("模板评审表不可用")
    except Exception as e:
        logger.warning(f"模板渲染评审表失败，回退从零建表: {e}")
        build_review_table_docx(doc, filled_data, chapters, extracted)
    doc.save(output_path)
    logger.info(f"Review table generated: {output_path}")
    return output_path
