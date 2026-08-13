"""隔离标签 — 每篇文档打上不可篡改的元数据，检索时三重过滤防混用。

标签维度：{文档类型, 地区, 租户, 年份, 是否现行有效, 风险标签}

数据来源：seed_data/*.md 开头的 `> **【类型/地区/年份/章节/风险标签】**` 五段式元数据。
格式示例：
  【政策法规/淮安市/2026/全文/补偿争议风险、程序风险】
  【固定资料/淮安市金湖县/持续更新/公司信息/程序合规风险】
  【人工范本/淮安市金湖县/2026/第十章全报告/补偿争议风险、社保安置风险】
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# 类型映射：中文类型 → 标准 document_type
# ═══════════════════════════════════════════════════════════════

TYPE_MAP = {
    "政策法规": "regulation",
    "政策文件": "policy",
    "技术标准": "standard",
    "工作指南": "guide",
    "固定资料": "company_info",
    "案例模板": "template",
    "人工范本": "example",
    "理论文献": "theory",
    "范文": "example",
    "报告": "example",
}

# 地区归一化：中文地区 → 标准 region_id
REGION_MAP = {
    "全国": "national",
    "江苏省": "jiangsu",
    "淮安市": "huaian",
    "淮安市金湖县": "huaian-jinhu",
    "淮安市洪泽区": "huaian-hongze",
    "南京市": "nanjing",
    "南通市": "nantong",
    "苏州市": "suzhou",
}


@dataclass
class IsolationTags:
    """一篇文档的隔离标签。检索时必须按这些标签过滤。"""

    document_type: str = ""          # regulation / standard / guide / example / company_info ...
    region: str = ""                 # national / jiangsu / huaian / nanjing ...
    tenant_id: str = "public"        # public = 跨租户共享；否则为租户私有
    year: str = ""                   # 2026 / 2024-2025 / 持续更新 / 通用 / 学术著作
    is_active: bool = True           # 是否现行有效（老旧政策 = False）
    scope: str = ""                  # 全文 / 第十章全报告 / 总则至附则 ...
    risk_tags: List[str] = field(default_factory=list)
    source_url: str = ""             # 爬取来源（合规网站）

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_metadata(self) -> Dict:
        """转为 ChromaDB chunk metadata（不可篡改标签）。"""
        return {
            "document_type": self.document_type,
            "region": self.region,
            "tenant_id": self.tenant_id,
            "year": self.year,
            "is_active": self.is_active,
            "scope": self.scope,
            "risk_tags": ",".join(self.risk_tags),
        }


# ═══════════════════════════════════════════════════════════════
# 解析【】五段式元数据
# ═══════════════════════════════════════════════════════════════

_METADATA_RE = re.compile(r'【([^】]+)】')


def parse_metadata(text: str) -> Optional[IsolationTags]:
    """从文本开头的【类型/地区/年份/章节/风险标签】解析隔离标签。

    Returns None 若无元数据。
    """
    m = _METADATA_RE.search(text or "")
    if not m:
        return None

    parts = [p.strip() for p in m.group(1).split("/")]
    if len(parts) < 3:
        return None

    raw_type = parts[0]
    raw_region = parts[1]
    raw_year = parts[2]
    scope = parts[3] if len(parts) > 3 else "全文"
    risk_tags = [t.strip() for t in parts[4].split("、")] if len(parts) > 4 else []

    return IsolationTags(
        document_type=TYPE_MAP.get(raw_type, raw_type),
        region=REGION_MAP.get(raw_region, raw_region),
        tenant_id="public",  # seed_data 文档默认公共
        year=raw_year,
        is_active=_infer_active(raw_year),
        scope=scope,
        risk_tags=risk_tags,
    )


def _infer_active(year: str) -> bool:
    """判断政策是否现行有效（年份层面）。"""
    if not year:
        return True
    # 持续更新/通用/学术著作 → 视为有效
    if any(kw in year for kw in ("持续", "通用", "学术", "现行", "有效")):
        return True
    # 含年份范围或单年份 → 判断是否 >= 当前年份 - 2（宽松：2年内视为现行）
    import datetime
    current = datetime.datetime.now().year
    # 提取最大年份
    years = re.findall(r'\d{4}', year)
    if years:
        max_year = max(int(y) for y in years)
        return max_year >= current - 2
    return True


# ═══════════════════════════════════════════════════════════════
# 无元数据时：从文件名/内容推断标签
# ═══════════════════════════════════════════════════════════════

def infer_tags_from_filename(filename: str, default_type: str = "example") -> IsolationTags:
    """从文件名推断隔离标签（兜底，当文档无【】元数据时）。

    同时匹配中文地区名（"南京市"）和英文 region_id（"nanjing"）。
    """
    lower_name = filename.lower()

    # 地区推断：先匹配英文 region_id（精确），再匹配中文地区名（含去"省/市"短名）
    region = "national"
    for key, rid in REGION_MAP.items():
        if rid != "national" and rid in lower_name:
            region = rid
            break
    else:
        for key, rid in REGION_MAP.items():
            if key == "全国":
                continue
            # 匹配完整名（"南京市"）或短名（"南京"去"市"/"省"）
            short = key.replace("市", "").replace("省", "")
            if key in filename or (len(short) >= 2 and short in filename):
                region = rid
                break

    # 类型推断
    doc_type = default_type
    for key, mapped in TYPE_MAP.items():
        if key in filename:
            doc_type = mapped
            break

    return IsolationTags(
        document_type=doc_type,
        region=region,
        tenant_id="public",
        year="",
        is_active=True,
        scope="",
        risk_tags=[],
    )


# ═══════════════════════════════════════════════════════════════
# 检索过滤条件构建（三重过滤防混用）
# ═══════════════════════════════════════════════════════════════

def build_retrieval_filter(
    tenant_id: str = "public",
    region: str = "",
    document_type: str = "",
    only_active: bool = True,
) -> Dict:
    """构建 ChromaDB where 过滤条件。

    - tenant_id: 强制包含 public（公共标准跨租户共享）
    - region: 地区过滤（留空则不过滤）
    - document_type: 类型过滤（留空则不过滤）
    - only_active: 是否只检索现行有效的政策
    """
    conditions = []

    # 租户过滤：本租户 + public（公共文档共享）
    if tenant_id == "public":
        conditions.append({"tenant_id": "public"})
    else:
        conditions.append({"tenant_id": {"$in": [tenant_id, "public"]}})

    if region:
        conditions.append({"region": region})

    if document_type:
        conditions.append({"document_type": document_type})

    if only_active:
        conditions.append({"is_active": True})

    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}
