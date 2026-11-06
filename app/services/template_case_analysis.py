# -*- coding: utf-8 -*-
"""模板的「人工批注式分析」——把一份范文/模板逐节讲清楚：

    这一节是固定话术，还是只换数据，还是必须按本项目重写？
    换哪些数？从哪份资料取？用什么口径/公式？模板里的原值是多少？

依据是业主对《井神复垦报告》的人工批注（2026-11-05，73 条）：
批注不描述「格式」，而是逐节指出数据类型、来源与算法。本模块把同一套
判断规则固化下来，对知识库里**任意一份模板**（docx / PDF）自动产出一份
《批注式分析》，供编制人员核对，也可作为新地区模板的接入检查单。

用法（脚本）：`python scripts/analyze_templates.py --domain reclamation`
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 数据键 → 中文名（报告里怎么称呼它）
KEY_LABELS: Dict[str, str] = {
    "project_name": "项目名称",
    "build_unit": "建设单位",
    "location": "项目位置",
    "area_m2": "临时用地面积",
    "land_classification": "土地利用现状/地类面积",
    "project_investment": "项目总投资",
    "service_years": "土地复垦方案服务年限",
    "land_use_start": "临时用地开始日期",
    "land_use_end": "临时用地结束日期",
    "topsoil_thickness": "表土剥离厚度",
    "hard_surface_type": "硬化结构类型",
    "hard_area_m2": "硬化面积",
    "surface_thickness_cm": "面层厚度",
    "cushion_thickness_cm": "垫层厚度",
    "reclaim_direction": "复垦方向",
    "land_ownership": "土地权属",
    "rec_laim_direction": "复垦方向",
    "_budget_estimate": "投资估算（表7-3/7-4/7-5）",
    "damage_area_m2": "损毁面积",
    "stockpile_area_m2": "表土堆放区面积",
}

# 逐节判断规则 —— 关键词命中即套用（顺序敏感：先专后泛）
_RECLAMATION_SECTION_RULES: List[Tuple[Tuple[str, ...], Dict[str, str]]] = [
    # ── 第四章 ──
    (("土地权属状况",), {
        "kind": "换数",
        "replace": "权属单位、权属性质、各分区面积",
        "source": "临时用地勘测定界报告（批注：4.2.3 取勘测定界报告）",
    }),
    (("国土空间规划",), {"kind": "不变", "replace": "", "source": "模板原文（批注：不变）"}),
    (("复垦责任范围占用耕地", "占用耕地"), {
        "kind": "重写",
        "replace": "占用耕地面积、占比，并论证「临时用地占用耕地不可避免」",
        "source": "临时用地勘测定界报告 + 表4-2",
        "formula": "三层论证：选址/布局无替代方案；临时用地只占不征、用后复垦；"
                   "表土剥离回覆后可恢复耕种（占补平衡）",
    }),
    (("土地损毁分析与评定", "已损毁", "拟损毁", "损毁环节"), {
        "kind": "换数",
        "replace": "分区名称、面积、损毁方式、损毁程度",
        "source": "表4-2 / 临时用地功能分区（表土堆放区中度，其余重度）",
    }),
    (("适宜性评价", "评价单元", "评价过程", "复垦方向的确定"), {
        "kind": "重写",
        "replace": "评价范围与分区面积、评价单元、得分、复垦方向",
        "source": "与表4-2 一致；面积取勘测定界报告",
        "formula": "评价单元综合得分 90～98（>90 且不离谱）；表土堆放区中度、其余重度",
    }),
    (("评价原则", "评价方法", "评价体系", "评价指标", "分级标准"), {
        "kind": "不变", "replace": "", "source": "模板原文（批注：基本不变）",
    }),
    (("水土资源平衡", "土资源平衡"), {
        "kind": "换数",
        "replace": "耕地面积、剥离厚度、剥离量、回填量、需土量",
        "source": "勘测定界报告（耕地面积）+ 设计参数（剥离厚度）",
        "formula": "剥离量 = 耕地面积 × 剥离厚度（0.30m）；回填量 = 剥离量（剥覆平衡）",
    }),
    (("生态环境影响",), {"kind": "不变", "replace": "", "source": "模板原文（批注：4.3 基本不变）"}),
    # ── 第五章 ──
    (("道路工程",), {
        "kind": "条件段",
        "replace": "恢复道路长度、宽度",
        "source": "勘测定界报告（农村道路）；**不涉及农村道路时整段不写**",
    }),
    (("恢复水利", "沟渠"), {
        "kind": "条件段",
        "replace": "沟渠面积/疏通量",
        "source": "勘测定界报告（沟渠）；**不涉及沟渠时整段不写**",
    }),
    (("表土剥离",), {
        "kind": "换数",
        "replace": "剥离面积、剥离厚度、剥离量",
        "source": "与 4.5.1 土资源平衡同一批数字",
        "formula": "剥离量 = 耕地面积 × 剥离厚度；剥离的表土集中堆放并设拦挡（图6-1）",
    }),
    (("质量要求", "预防控制", "监测措施", "管护措施", "复垦措施"), {
        "kind": "不变", "replace": "", "source": "模板原文（批注：5.1/5.2 不变）",
    }),
    # ── 第六章 ──
    (("工程量测算", "工程量"), {
        "kind": "换数",
        "replace": "表6-1 全部分项工程量",
        "source": "系统按地类/设计参数计算；用户上传投资估算 Excel 时以 Excel 为准",
        "formula": "剥离=耕地×0.30；回填=剥离；草籽=表土堆放区×1.5；"
                   "垫层面积=总面积−表土堆放区、碎石=垫层面积×0.15；"
                   "翻耕/平整=耕地；有机肥=耕地×15t/ha、复合肥=耕地×0.75t/ha；"
                   "袋装土为估列值（无公式，资料给多少写多少）",
    }),
    (("工程设计", "工程技术措施设计"), {
        "kind": "重写",
        "replace": "各措施的构造、尺寸、施工方法",
        "source": "设计参数 + 本项目地类",
    }),
    # ── 第七章 ──
    (("投资估算编制依据", "费用构成", "估算说明"), {
        "kind": "不变", "replace": "", "source": "模板原文（批注：7.1.1/7.1.2 不调整）",
    }),
    (("工程施工费",), {
        "kind": "换数",
        "replace": "表7-3 逐项工程量、综合单价、工程施工费",
        "source": "**用户上传的投资估算 Excel**（工程量取表6-1 同源）",
        "formula": "分项金额 = 工程量 × 综合单价；合计 = 各分项之和",
    }),
    (("其他费用",), {
        "kind": "换数",
        "replace": "表7-4 前期工作费/监理费/竣工验收费/业主管理费",
        "source": "投资估算 Excel",
        "formula": "按 Excel 里的取费基数与费率",
    }),
    (("监测管护费",), {
        "kind": "换数",
        "replace": "监测费、管护费、土壤污染监测、耕地质量评定、验收报告编制费",
        "source": "投资估算 Excel；没有 Excel 时按费率算",
        "formula": "监测费=工程施工费×1.5%；管护费=1000元/(人·月)×人数×月数；"
                   "污染/质量各按 2.57 元/㎡；验收报告 5.5 万元",
    }),
    (("预备费",), {
        "kind": "换数",
        "replace": "基本预备费、价差预备费、风险金",
        "source": "投资估算 Excel；无 Excel 时按公式",
        "formula": "基本预备费=(一+二+三+四)×6%；风险金=工程施工费×2%；"
                   "价差预备费 Excel 有值读 Excel，否则按年上涨 5% 计算",
    }),
    (("技术经济指标", "投资估算表"), {
        "kind": "换数",
        "replace": "表7-5 静态/动态总投资、亩均投资",
        "source": "上述各项汇总",
        "formula": "静态=一+二+三+四+基本预备费+风险金；动态=静态+价差预备费",
    }),
    # ── 第八章 ──
    (("服务年限",), {
        "kind": "换数",
        "replace": "用地时间、服务年限、起止年月",
        "source": "基础资料「用地开始/用地结束/复垦期/管护期」；服务年限由用户提供",
        "formula": "用地时间=建设单位提供日期起 24 个月（示例）；"
                   "服务年限=用地+复垦期+管护期（示例 36 个月）",
    }),
    (("工作计划", "计划安排"), {
        "kind": "重写",
        "replace": "各阶段起止年月与工作内容",
        "source": "用地起止日期 + 复垦期/管护期",
        "formula": "首月表土剥离堆放；第二月至用地结束监测；结束后复垦期施工；再管护期",
    }),
    (("费用安排",), {
        "kind": "换数",
        "replace": "表8-1 四期动态投资额",
        "source": "表7-3 工程费 + 其他费/预备费按比例分摊",
        "formula": "阶段工程费+按工程费占比分摊其他费/基本预备费/价差/风险；合计=动态总投资",
    }),
    # ── 第九章 / 第十章 ──
    (("效益", "耕地质量"), {
        "kind": "条件段",
        "replace": "效益测算数字",
        "source": "本项目复垦面积、耕地质量等别；批注：不变（按项目数据替换少量）",
    }),
    (("保障措施", "组织保障", "监管保障", "技术保证", "费用保障", "公众参与", "权属调整"), {
        "kind": "不变", "replace": "单位名称、公示地点等少量专名",
        "source": "模板原文（批注：10 章不变）",
    }),
]

_PRIOR_LABEL = {
    "fixed": "不变（固定话术）",
    "slot": "换数（只替换数据位）",
    "create": "重写（按本项目资料编撰）",
    "mixed": "固定 + 换数/重写（混合）",
}

# 域无关的粗分类（稳评/投标模板没有复垦那套批注规则时用）
_DATA_KW = ("名称", "单位", "位置", "面积", "范围", "资金", "周期", "日期",
            "投资", "预算", "人口", "户数", "规模", "数量", "比例", "红线",
            "联系人", "电话", "编制单位", "坐落", "权属")
_FIXED_KW = ("依据", "程序", "方法", "原则", "目的", "组织", "保障", "职责",
             "制度", "结论", "定义", "术语", "内容", "意义", "标准")


def _heuristic_kind(title: str, chars: int) -> str:
    t = str(title or "")
    if any(k in t for k in _DATA_KW):
        return "换数（只替换数据位）"
    if any(k in t for k in _FIXED_KW):
        return "不变（固定话术）"
    return "重写（按本项目资料编撰）" if chars > 80 else "固定 + 换数/重写（混合）"


_KIND_LABEL = {
    "不变": "不变（固定话术）",
    "换数": "换数（只替换数据位）",
    "重写": "重写（按本项目资料编撰）",
    "条件段": "条件段（按项目地类取舍）",
}


# ═══════════════════════════════════════════════════════════════
# 稳评模板的逐节口径（DB32/T4013-2021 / 江苏 14 章案卷式）
# ═══════════════════════════════════════════════════════════════
_STABILITY_SECTION_RULES: List[Tuple[Tuple[str, ...], Dict[str, str]]] = [
    (("决策名称", "工程名称", "项目名称", "事项名称"), {
        "kind": "换数", "replace": "项目/决策名称、建设内容",
        "source": "立项批复、用地预审/规划许可等批文",
    }),
    (("决策单位", "建设单位", "实施单位", "责任主体"), {
        "kind": "换数", "replace": "单位名称、法人、联系方式",
        "source": "项目批复文件 / 建设单位提供",
    }),
    (("拟征地位置", "地理位置", "项目位置", "坐落"), {
        "kind": "换数", "replace": "位置描述、四至、所属行政区",
        "source": "勘测定界报告 / 征地红线图",
    }),
    (("征收范围", "征地面积", "面积", "地类"), {
        "kind": "换数", "replace": "征收范围、面积、地类构成",
        "source": "勘测定界报告（面积一律以勘界为准）",
        "formula": "各地类面积合计 = 征收总面积；表与正文同口径",
    }),
    (("资金筹措", "资金来源", "投资估算", "投资"), {
        "kind": "换数", "replace": "总投资、资金来源与落实方式",
        "source": "可研/立项批复 / 建设单位提供",
    }),
    (("实施周期", "工期", "进度计划"), {
        "kind": "换数", "replace": "起止时间、阶段安排",
        "source": "施工组织设计 / 建设单位提供日期",
    }),
    (("红线图", "征地范围图", "位置图"), {
        "kind": "换数", "replace": "图件与图号",
        "source": "勘测定界报告附图 / 征地红线图",
    }),
    (("评估依据", "编制依据", "法律法规", "政策文件", "标准规范"), {
        "kind": "不变", "replace": "补充本项目适用批文号",
        "source": "模板原文（固定条款，不随项目改写）",
    }),
    (("评估程序", "评估方法", "评估过程", "工作程序", "评估工作"), {
        "kind": "不变", "replace": "参与单位、时间节点等少量专名",
        "source": "模板原文（固定流程）",
    }),
    (("问卷调查", "群众意见", "民意", "座谈", "访谈", "调查表", "入户"), {
        "kind": "换数", "replace": "样本量、支持/反对比例、主要诉求",
        "source": "问卷统计结果（系统按问卷解析）",
        "formula": "支持率/反对率 = 对应人数 ÷ 有效样本；三档合计 100%",
    }),
    (("风险因素", "风险源", "风险点", "风险识别", "风险分析", "风险预测"), {
        "kind": "重写", "replace": "本项目风险因素清单与逐项分析",
        "source": "项目资料 + 现场调查 + 部门意见",
        "formula": "风险等级按第12章量化评分表计算，不得自拟分值",
    }),
    (("风险等级", "量化", "评分", "指标", "权重"), {
        "kind": "换数", "replace": "措施前后得分与风险等级",
        "source": "系统量化表（表12-x）",
        "formula": "综合风险指数 = Σ(权重×得分)；等级按 DB32/T4013-2021 判据",
    }),
    (("评审表", "评审意见", "专家意见"), {
        "kind": "不变", "replace": "项目信息、评审结论",
        "source": "江苏省统一制式评审表（14 栏）",
    }),
    (("应急预案", "保障措施", "组织保障", "工作保障"), {
        "kind": "不变", "replace": "单位名称、联系人",
        "source": "模板原文（固定条款）",
    }),
    (("结论", "建议", "对策", "措施建议"), {
        "kind": "重写", "replace": "评估结论与建议",
        "source": "本项目风险分析结果",
    }),
    (("项目概况", "基本情况", "自然概况", "社会经济"), {
        "kind": "重写", "replace": "项目与区域概况",
        "source": "项目资料 / 统计年鉴 / 现场调查",
    }),
]

# ═══════════════════════════════════════════════════════════════
# 投标文件的逐节口径
# ═══════════════════════════════════════════════════════════════
_BIDDING_SECTION_RULES: List[Tuple[Tuple[str, ...], Dict[str, str]]] = [
    (("投标函", "报价", "投标报价", "开标"), {
        "kind": "换数", "replace": "项目名称、报价、工期、有效期",
        "source": "招标文件 + 本项目报价表",
    }),
    (("资质", "证书", "营业执照", "信用"), {
        "kind": "换数", "replace": "证书名称、编号、有效期",
        "source": "公司档案（资质证书/营业执照）",
    }),
    (("业绩", "类似项目", "案例"), {
        "kind": "换数", "replace": "项目名称、合同额、完成时间",
        "source": "公司业绩库",
    }),
    (("项目负责人", "人员", "团队", "组织机构"), {
        "kind": "换数", "replace": "姓名、职称、证书、分工",
        "source": "人员库 / 招标文件要求",
    }),
    (("技术方案", "服务方案", "实施方案", "工作方案", "技术路线"), {
        "kind": "重写", "replace": "针对本项目的技术/服务方案",
        "source": "招标文件 + 项目资料",
    }),
    (("服务承诺", "质量承诺", "售后"), {
        "kind": "条件段", "replace": "承诺条款与响应偏离",
        "source": "招标文件要求；无要求的整段不写",
    }),
    (("格式", "目录", "封面", "签署"), {
        "kind": "不变", "replace": "项目名称、日期",
        "source": "招标文件规定格式（原文不动）",
    }),
]


def _rules_for_domain(domain: str) -> List[Tuple[Tuple[str, ...], Dict[str, str]]]:
    d = str(domain or "").lower()
    if d == "reclamation":
        return _RECLAMATION_SECTION_RULES
    if d == "stability":
        return _STABILITY_SECTION_RULES
    if d == "bidding":
        return _BIDDING_SECTION_RULES
    return []


def _rule_for(title: str, domain: str = "reclamation") -> Optional[Dict[str, str]]:
    t = str(title or "")
    for kws, rule in _rules_for_domain(domain):
        if any(k in t for k in kws):
            return rule
    return None




def _examples_for_key(prof: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for slot in prof.get("data_slots") or []:
        out[str(slot.get("key") or "")] = list(slot.get("examples") or [])
    return out


def analyze_template_file(path: str | Path, *, domain: str = "reclamation",
                          name: str = "", region: str = "") -> Dict[str, Any]:
    """一份模板 → 批注式分析结构（章节/小节/类型/换什么/来源/公式/原值）。"""
    from app.services.template_corpus_profile import analyze_template_file as _corpus
    path = _resolve(path)
    prof = _corpus(path, domain=domain)
    if not prof:
        # .doc 等不支持的格式：返回空分析，调用方按「无可识别章节」处理
        return {"name": name or Path(path).stem, "region": region, "domain": domain,
                "path": str(path), "format": Path(path).suffix.lstrip("."),
                "title": "", "chapters": 0, "counts": {}, "typography": {},
                "outline_check": {}, "sections": []}
    examples = _examples_for_key(prof)
    items = prof.get("items") or []
    sections: List[Dict[str, Any]] = []
    for it in items:
        title = str(it.get("title") or "")
        if not title:
            continue
        prior = str(it.get("prior") or "mixed")
        # 各域用自己的规则库（复垦/稳评/投标），没有规则库的域走通用粗分类
        rule = _rule_for(title, domain) or {}
        if rule.get("kind"):
            kind = rule["kind"]
        elif prior in _PRIOR_LABEL:
            kind = _PRIOR_LABEL[prior]
            if prior == "mixed":
                kind = _heuristic_kind(title, int(it.get("chars") or 0))
        else:
            kind = _heuristic_kind(title, int(it.get("chars") or 0))
        kind = _KIND_LABEL.get(kind, kind)
        keys = [k for k in (it.get("keys") or []) if k]
        sections.append({
            "no": str(it.get("no") or ""),
            "title": title,
            "level": int(it.get("level") or 0),
            "chars": int(it.get("chars") or 0),
            "kind": kind,
            "prior": prior,
            "replace": rule.get("replace") or "",
            "source": rule.get("source") or "",
            "formula": rule.get("formula") or "",
            "keys": [{"key": k, "label": KEY_LABELS.get(k, k),
                      "examples": examples.get(k) or []} for k in keys],
            "fixed": int(it.get("fixed") or 0),
            "slot": int(it.get("slot") or 0),
            "create": int(it.get("create") or 0),
        })
    _counts = prof.get("counts") or {}
    if str(domain) != "reclamation":
        _counts = {
            "fixed": sum(1 for s in sections if s["kind"].startswith("不变")),
            "slot": sum(1 for s in sections if s["kind"].startswith("换数")),
            "create": sum(1 for s in sections if s["kind"].startswith("重写")),
        }
    return {
        "name": name or prof.get("title") or Path(path).stem,
        "region": region,
        "domain": domain,
        "path": str(path),
        "format": prof.get("format") or Path(path).suffix.lstrip("."),
        "title": prof.get("title") or "",
        "chapters": prof.get("chapters") or 0,
        "counts": _counts,
        "typography": prof.get("typography") or {},
        "outline_check": prof.get("outline_check") or {},
        "sections": sections,
    }


_OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "template_case_analysis"


def _resolve(template_path: str | Path) -> Path:
    """会话里的 template_path 常是 `templates/xxx.docx`（相对 storage），这里统一解析。"""
    p = Path(str(template_path))
    if p.exists():
        return p
    try:
        from app.config import settings
        for cand in (Path(settings.STORAGE_DIR) / str(template_path),
                     Path(settings.STORAGE_DIR) / p.name):
            if cand.exists():
                return cand
    except Exception:
        pass
    return p


def _paths(template_path: str | Path, domain: str) -> Tuple[Path, Path]:
    stem = _resolve(template_path).stem
    base = _OUT_DIR / f"{domain}_{stem}"
    return base.with_suffix(".json"), base.with_suffix(".md")


def save_analysis(analysis: Dict[str, Any], *, table_plan: Optional[Dict[int, list]] = None) -> Path:
    """把分析结果落盘（JSON 供程序复用，Markdown 供人看）。返回 JSON 路径。"""
    import json
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    jp, mp = _paths(analysis.get("path") or analysis.get("name") or "template",
                    str(analysis.get("domain") or "reclamation"))
    jp.write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_markdown(analysis, table_plan=table_plan), encoding="utf-8")
    return jp


def load_analysis(template_path: str | Path, domain: str = "reclamation") -> Optional[Dict[str, Any]]:
    import json
    jp, _ = _paths(template_path, domain)
    if not jp.exists():
        return None
    try:
        return json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return None


def case_prompt_block(template_path: str | Path, chapter_no: int,
                      domain: str = "reclamation") -> str:
    """生成某章 prompt 的「逐节取数口径」块（来自批注式分析）。

    没做过分析就现做一次并落盘；只输出**有明确来源/公式**的小节，
    每章最多 15 行，避免挤占模板底稿的篇幅。
    """
    a = load_analysis(template_path, domain)
    if not a:
        try:
            a = analyze_template_file(template_path, domain=domain)
            save_analysis(a)
        except Exception:
            return ""
    ch = str(int(chapter_no))
    picked = []
    for s in (a.get("sections") or []):
        no = str(s.get("no") or "")
        if no != ch and not no.startswith(ch + "."):
            continue
        if not (s.get("source") or s.get("formula")):
            continue
        picked.append(s)
    if not picked:
        return ""
    lines = ["## 📘 本章逐节取数口径（按人工批注式分析执行）"]
    for s in picked[:15]:
        bits = [f"{s.get('no')} {s.get('title')}：{s.get('kind')}"]
        if s.get("replace"):
            bits.append("换 " + s["replace"])
        if s.get("source"):
            bits.append("取 " + s["source"])
        if s.get("formula"):
            bits.append("口径 " + s["formula"])
        lines.append("- " + "；".join(bits))
    return "\n".join(lines)


def render_markdown(analysis: Dict[str, Any], *, table_plan: Optional[Dict[int, list]] = None) -> str:
    """批注式分析 → Markdown（给人看的检查单）。"""
    a = analysis
    lines: List[str] = []
    lines.append(f"# 《{a['name']}》批注式分析")
    lines.append("")
    meta = [f"- 文件：`{a['path']}`", f"- 域：{a['domain']}　格式：{a['format']}"]
    if a.get("region"):
        meta.append(f"- 地区：{a['region']}")
    c = a.get("counts") or {}
    if str(a.get("domain") or "") != "reclamation":
        # 非复垦模板的结构画像沿用了复垦的三色口径（换数常为 0），
        # 头部统计改用本报告实际判定出的类型分布。
        _secs = a.get("sections") or []
        c = {
            "fixed": sum(1 for s in _secs if str(s.get("kind") or "").startswith("不变")),
            "slot": sum(1 for s in _secs if str(s.get("kind") or "").startswith("换数")),
            "create": sum(1 for s in _secs if str(s.get("kind") or "").startswith("重写")),
        }
    meta.append(f"- 结构：{a.get('chapters', 0)} 章 / {len(a.get('sections') or [])} 节"
                f"（固定 {c.get('fixed', 0)}、换数 {c.get('slot', 0)}、重写 {c.get('create', 0)}）")
    ty = a.get("typography") or {}
    if ty:
        _t = "；".join(f"{k}:{v.get('font')}{v.get('size')}pt"
                       for k, v in ty.items() if isinstance(v, dict))
        if _t:
            meta.append(f"- 版式：{_t}")
    oc = a.get("outline_check") or {}
    if oc:
        meta.append(f"- 规程大纲核对：{'通过' if oc.get('ok') else '缺 ' + '、'.join(oc.get('missing') or [])}")
    lines.extend(meta)
    lines.append("")
    if not a.get("sections"):
        lines.append("> 该模板没有可识别的章节标题（属于版式/表单类模板，"
                     "只提供字体字号与页面版式，不提供逐节内容口径）。")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    lines.append("> 说明：类型按「不变 / 换数 / 重写 / 条件段」标注；"
                 "换数项给出数据来源与口径；模板原值来自画像抽取。")
    lines.append("")

    cur_ch = ""
    for s in a.get("sections") or []:
        if s["level"] == 1:
            cur_ch = s["title"]
            lines.append(f"## {s['no']} {s['title']}".strip())
            lines.append("")
            continue
        if not s["no"] and not s["chars"]:
            continue
        lines.append(f"### {s['no']} {s['title']}".strip())
        lines.append(f"- 类型：**{s['kind']}**")
        if s.get("replace"):
            lines.append(f"- 换什么：{s['replace']}")
        if s.get("source"):
            lines.append(f"- 从哪取：{s['source']}")
        if s.get("formula"):
            lines.append(f"- 口径/公式：{s['formula']}")
        ks = s.get("keys") or []
        if ks:
            _k = "；".join(
                f"{k['label']}" + (f"（模板原值：{'、'.join(k['examples'])}）"
                                   if k.get("examples") else "")
                for k in ks)
            lines.append(f"- 数据位：{_k}")
        lines.append("")
    if table_plan:
        lines.append("## 附：本章表格清单（模板中识别到的表）")
        lines.append("")
        for ch in sorted(table_plan):
            caps = [t.get("caption") or t.get("title") for t in table_plan[ch]]
            if caps:
                lines.append(f"- 第{ch}章：" + "；".join(caps))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
