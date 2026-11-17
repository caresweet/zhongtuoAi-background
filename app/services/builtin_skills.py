# -*- coding: utf-8 -*-
"""内置技能库 —— 系统出厂自带，不依赖专家反馈蒸馏。

与 `review_skills` 表的关系：
  · 表里的是「后加的」：专家反馈蒸馏、用户报告要求、高频问题自动提炼；
  · 本文件是「出厂自带」：审核、检索、工具、计算、外部对接五类模块的
    硬规矩，所有报告域默认生效，不需要入库、不会被误删。

两类技能：
  · rule —— 正则规则，审核阶段由 `quality_review_agent` 逐章检查；
  · text —— 行文要求，注入生成 prompt（`master_orchestrator`）与评审提示。

新增/修改内置技能只改本文件；改完跑 `tests/test_builtin_skills.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import sqlite3

from app.services.de_ai_rules import HARD_AI_PATTERNS, DE_AI_STYLE_TEXT

# 同一标签的多条正则合并（如「空洞开头」有“随着…”“在当今…”两条）
_DE_AI_RULES: Dict[str, Tuple[str, Optional[str]]] = {}
for _pat, _label, _repl in HARD_AI_PATTERNS:
    _old = _DE_AI_RULES.get(_label)
    if _old:
        _one = _old[1] if _old[1] == _repl else None
        _DE_AI_RULES[_label] = (f"(?:{_old[0]})|(?:{_pat})", _one)
    else:
        _DE_AI_RULES[_label] = (_pat, _repl)

ALL_DOMAINS = ("stability", "reclamation", "bidding")

MODULES: Dict[str, str] = {
    "retrieval": "检索",
    "tools": "工具",
    "calc": "计算",
    "integration": "外部对接",
    "audit": "审核校验",
}
MODULE_ORDER = ("retrieval", "tools", "calc", "integration", "audit")


@dataclass(frozen=True)
class BuiltinSkill:
    id: str
    module: str                       # MODULES 的键
    desc: str                         # 要求（人话）
    correction: str                   # 正确做法 / 纠正示例
    domains: Tuple[str, ...] = ALL_DOMAINS
    skill_type: str = "text"          # text | rule
    pattern: str = ""                 # rule 专用
    replacement: Optional[str] = None # rule 自动修复的替换文本；None = 不自动改
    severity: str = "warning"


# ═══════════════════════════════════════════════════════════════
# 技能目录
# ═══════════════════════════════════════════════════════════════

CATALOG: Tuple[BuiltinSkill, ...] = (
    # ── 审核校验（规则型，审核阶段可自动检查）──
    BuiltinSkill(
        id="b-audit-001", module="audit", skill_type="rule",
        pattern=r"(?:综上所述|总而言之|众所周知|不言而喻|毋庸置疑)[，,、]?",
        desc="不要用「综上所述/总而言之/众所周知」等口水词",
        correction="直接写结论", replacement="",
        severity="warning",
    ),
    BuiltinSkill(
        id="b-audit-002", module="audit", skill_type="rule",
        pattern=r"(?:XXX|xxx|×××|TBD)",
        desc="正文不得残留 XXX / TBD 占位符",
        correction="补真实数据；确实缺失时写【待补充】",
        replacement="【待补充】",
        severity="critical",
    ),

    # ── 检索 ──
    BuiltinSkill(
        id="b-ret-001", module="retrieval",
        desc="法规/标准引用必须有出处",
        correction="写全称+文号，来源限定为知识库检索结果或用户资料；"
                   "检索不到按【待补充：法规依据】标注，禁止凭记忆写法条",
    ),
    BuiltinSkill(
        id="b-ret-002", module="retrieval",
        desc="范文只学结构，不搬内容",
        correction="项目名、地名、金额、面积、日期一律用本项目事实卡，不得照抄范文",
    ),
    BuiltinSkill(
        id="b-ret-003", module="retrieval",
        desc="政府名称必须是项目所在地真实存在的",
        correction="镇写「XX镇人民政府」、街道写「XX街道办事处」、乡写「XX乡人民政府」；"
                   "区划类型以资料或联网核验结果为准，不得把镇写成街道，不得编造政府",
    ),

    # ── 工具 ──
    BuiltinSkill(
        id="b-tool-001", module="tools",
        desc="能查能算的不要心算",
        correction="工程量、投资估算、风险分值、问卷统计、勘界数据优先调用系统工具或已解析资料",
    ),
    BuiltinSkill(
        id="b-tool-002", module="tools", domains=("reclamation",),
        desc="复垦工程量与投资走固定入口",
        correction="工程量用 calc_earthwork / 表6-1，投资用 calc_investment / 投资估算表；"
                   "保留工具给出的算法与来源，便于反查",
    ),
    BuiltinSkill(
        id="b-tool-003", module="tools",
        desc="工具失败不等于可以编数",
        correction="工具/接口无结果时正文写【待补充】，不得用经验值或其他项目数据顶替",
    ),

    # ── 计算 ──
    BuiltinSkill(
        id="b-calc-001", module="calc",
        desc="同一指标全文一个事实源",
        correction="面积、户数、支持率、投资额首次确定后，后文只引用不重算",
    ),
    BuiltinSkill(
        id="b-calc-002", module="calc", domains=("reclamation",),
        desc="复垦单位与投资口径",
        correction="面积用公顷（4 位小数）或㎡，金额用万元（2 位）；"
                   "静态总投资不含价差预备费，动态总投资=静态+价差预备费",
    ),
    BuiltinSkill(
        id="b-calc-003", module="calc", domains=("stability",),
        desc="稳评关键数字必须自洽",
        correction="支持率=100%、反对率=0%；问卷份数/户数与事实卡一致；"
                   "风险分值措施后必须高于措施前",
    ),
    BuiltinSkill(
        id="b-calc-004", module="calc", domains=("reclamation",),
        desc="涉及挖损的必须单列开挖与回填",
        correction="管道开挖带、沟槽、基坑按「挖损面积 × 开挖深度」单列沟槽开挖与回填量，"
                   "表6-1 工程量与投资估算同一口径；有挖损分区就必须有开挖深度，"
                   "缺失写【待补充】，不得只写压占",
    ),
    BuiltinSkill(
        id="b-calc-005", module="calc",
        desc="关键数据必须带来源和算式",
        correction="面积→勘测定界；户数/问卷→调查统计表；金额→投资估算表；"
                   "工程量写「面积 × 厚度/深度」的算式。来源不明的数不写，"
                   "算不出的写【待补充】并说明缺什么",
    ),

    # ── 外部对接 ──
    BuiltinSkill(
        id="b-int-001", module="integration",
        desc="外部服务失败按缺失处理",
        correction="预算/舆情/视觉/LLM 不可用时，相关数据写【待补充】，不得编造结果",
    ),
    BuiltinSkill(
        id="b-int-002", module="integration", domains=("stability",),
        desc="舆情检索无结果不等于无风险",
        correction="检索未命中只能写「未检索到公开舆情信息」，"
                   "不得写成「无负面舆情/未发现风险」",
    ),

    # ── 审核自检（文本型，交付前提醒）──
    BuiltinSkill(
        id="b-audit-003", module="audit",
        desc="交付前自检四项",
        correction="章节齐全、图表编号连续、表有表题和单位、正文无占位符残留",
    ),
    BuiltinSkill(
        id="b-audit-004", module="audit",
        desc="关键数据不用模糊词",
        correction="不要写「大约/若干/左右 + 数字」，给实测值或【待补充】",
    ),
    BuiltinSkill(
        id="b-audit-005", module="audit",
        desc="语句要通顺：短句为主，一句一事",
        correction="主谓宾完整，不用多层从句；每句不超过 60 字；"
                   "不出现「操作方式为将土…」这类缺字、断句或词语粘连",
    ),
    # 🔴 去 AI 味（2026-11-17 按网络公开 skill 的「表达层」规则抽取）
    BuiltinSkill(
        id="b-audit-006", module="audit", skill_type="rule",
        pattern=_DE_AI_RULES["AI过渡词"][0],
        desc="不用「值得注意的是/由此可见/总的来说/换言之」等 AI 过渡词",
        correction="直接写下一句事实，删掉过渡语",
        replacement=_DE_AI_RULES["AI过渡词"][1],
        severity="warning",
    ),
    BuiltinSkill(
        id="b-audit-007", module="audit", skill_type="rule",
        pattern=_DE_AI_RULES["AI热词"][0],
        desc="不用「赋能/抓手/组合拳/底层逻辑/生态位」等互联网黑话",
        correction="换成具体动作和对象：谁做了什么、做到什么程度",
        severity="warning",
    ),
    BuiltinSkill(
        id="b-audit-008", module="audit", skill_type="rule",
        pattern=_DE_AI_RULES["空洞开头"][0],
        desc="不用「随着…的不断发展」「在当今…背景下」这类空洞开头",
        correction="直接写项目事实：时间、地点、面积、任务来源",
        severity="warning",
    ),
    BuiltinSkill(
        id="b-audit-009", module="audit", skill_type="rule",
        pattern=_DE_AI_RULES["翻译腔"][0],
        desc="不用「通过…的方式」「对于…而言」等翻译腔",
        correction="直接写主语和动作，如「采用…方法」改为「按…方法做」",
        severity="warning",
    ),
    BuiltinSkill(
        id="b-audit-010", module="audit", skill_type="rule",
        pattern=_DE_AI_RULES["破折号堆叠"][0],
        desc="一句里不要用两个以上破折号制造金句/反转",
        correction="拆成两句，直接说结论；公文里的定义性破折号保留一处即可",
        severity="warning",
    ),
    BuiltinSkill(
        id="b-style-001", module="audit", skill_type="text",
        desc="去 AI 味行文：具体、短句、少排比、不写金句",
        correction=DE_AI_STYLE_TEXT,
    ),
    BuiltinSkill(
        id="b-style-002", module="audit", skill_type="text",
        desc="润色只改表达，不改事实",
        correction="数字、单位、文号、日期、专名、标题、表格一律原样保留；"
                   "改完必须核对数字与表格行数",
    ),
)


# ═══════════════════════════════════════════════════════════════
# 开关（缺省全开；管理员在「内置技能」页可关）
# ═══════════════════════════════════════════════════════════════

def _db_path():
    try:
        from app.config import settings
        return settings.DATA_DIR / "knowledge_base.db"
    except Exception:
        return None


def _disabled_ids() -> set:
    """读 `builtin_skill_settings`；表/库不可用时视为全部启用。"""
    p = _db_path()
    if not p or not p.exists():
        return set()
    try:
        conn = sqlite3.connect(str(p))
        try:
            rows = conn.execute(
                "SELECT skill_id FROM builtin_skill_settings WHERE enabled = 0"
            ).fetchall()
        finally:
            conn.close()
        return {str(r[0]) for r in rows}
    except Exception:
        return set()


def set_enabled(skill_id: str, enabled: bool) -> Dict:
    """写入开关（UPSERT）。未知 skill_id 直接拒绝。"""
    if skill_id not in {s.id for s in CATALOG}:
        return {"updated": 0, "message": f"未知内置技能：{skill_id}"}
    p = _db_path()
    if not p:
        return {"updated": 0, "message": "知识库路径不可用"}
    try:
        conn = sqlite3.connect(str(p))
        try:
            # 迁移未跑（老库）时也能用
            conn.execute("""
                CREATE TABLE IF NOT EXISTS builtin_skill_settings (
                    skill_id TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT INTO builtin_skill_settings (skill_id, enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(skill_id) DO UPDATE SET
                    enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP
            """, (skill_id, 1 if enabled else 0))
            conn.commit()
        finally:
            conn.close()
        return {"updated": 1, "skill_id": skill_id, "enabled": bool(enabled)}
    except Exception as e:
        return {"updated": 0, "message": f"保存失败：{e}"}


# ═══════════════════════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════════════════════

def _applies(skill: BuiltinSkill, domain: str) -> bool:
    return domain in skill.domains


def catalog(domain: Optional[str] = None,
            module: Optional[str] = None) -> List[Dict]:
    """列出内置技能（供 API/前端展示）。"""
    disabled = _disabled_ids()
    out = []
    for s in CATALOG:
        if domain and domain not in s.domains:
            continue
        if module and s.module != module:
            continue
        out.append({
            "id": s.id, "module": s.module,
            "module_title": MODULES.get(s.module, s.module),
            "skill_type": s.skill_type, "severity": s.severity,
            "desc": s.desc, "correction": s.correction,
            "pattern": s.pattern, "replacement": s.replacement,
            "domains": list(s.domains),
            "enabled": s.id not in disabled,
        })
    return out


def rules_for(domain: str = "stability") -> List[Dict]:
    """审核阶段要跑的规则（与 review_skills 的 rules 同结构 + source 标记）。"""
    disabled = _disabled_ids()
    return [
        {"pattern": s.pattern, "desc": s.desc, "severity": s.severity,
         "correction": s.correction, "replacement": s.replacement, "chapter": 0,
         "skill_id": s.id, "module": s.module, "source": "builtin"}
        for s in CATALOG
        if s.skill_type == "rule" and domain in s.domains and s.pattern
        and s.id not in disabled
    ]


def texts_for(domain: str = "stability") -> List[Dict]:
    """文本型技能（与 review_skills 的 texts 同结构）。"""
    disabled = _disabled_ids()
    return [
        {"desc": s.desc, "correction": s.correction, "chapter": 0,
         "skill_id": s.id, "module": s.module, "source": "builtin"}
        for s in CATALOG
        if s.skill_type == "text" and domain in s.domains
        and s.id not in disabled
    ]


def hints_for(domain: str = "stability") -> str:
    """生成 prompt 用的内置技能块（按模块分组）。无适用项返回空串。"""
    disabled = _disabled_ids()
    items = [s for s in CATALOG
             if _applies(s, domain) and s.id not in disabled]
    if not items:
        return ""

    lines: List[str] = ["\n## 🧩 内置技能（系统自带，必须遵守）"]
    for mod in MODULE_ORDER:
        group = [s for s in items if s.module == mod]
        if not group:
            continue
        lines.append(f"### {MODULES[mod]}")
        for s in group:
            lines.append(f"- {s.desc}：{s.correction}")
    return "\n".join(lines) + "\n"
