"""公司能力清单 + 资质库 — 招标筛选的匹配基准。

核心业务清单硬编码（来自 company_info_zhongtuo.md 的业务范围）：
- 社会稳定风险评估（具备江苏省稳评第三方机构资质）
- 项目代理咨询
- 工程咨询
- 土地规划咨询

资质库从 company_assets 表动态读取（资质证书/业绩/人员/设备）。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ⚠️ 公司名统一：固定用"江苏众拓项目代理咨询有限公司"
# （不是 CompanyAsset 默认的"江苏众拓测绘有限公司"）
COMPANY_NAME = "江苏众拓项目代理咨询有限公司"

# 核心业务清单（硬编码，匹配基准）
COMPANY_CAPABILITIES: Dict[str, Dict] = {
    "社会稳定风险评估": {
        "keywords": ["稳评", "社会稳定风险评估", "风险评估", "征收", "征地", "拆迁",
                     "土地征收", "社会风险", "重大决策", "重大行政决策"],
        "qualification": "江苏省社会稳定风险评估第三方机构资质",
    },
    "项目代理咨询": {
        "keywords": ["招标代理", "采购代理", "项目代理", "代理咨询", "咨询服务",
                     "招标咨询", "政府采购代理", "招标投标"],
        "qualification": "",
    },
    "工程咨询": {
        "keywords": ["工程咨询", "可行性研究", "项目建议书", "可研报告", "初步设计",
                     "项目评估", "工程前期", "咨询论证"],
        "qualification": "",
    },
    "土地规划咨询": {
        "keywords": ["土地规划", "用地预审", "规划选址", "勘测定界", "用地报批",
                     "土地整治", "规划调整", "建设用地"],
        "qualification": "",
    },
}


def get_capability_summary() -> List[Dict]:
    """返回公司能力清单（供前端展示/确认）。"""
    return [
        {
            "name": name,
            "keywords": info["keywords"],
            "qualification": info["qualification"],
        }
        for name, info in COMPANY_CAPABILITIES.items()
    ]


def match_by_rules(project_text: str) -> Optional[str]:
    """规则粗筛：项目文本命中哪个业务能力。

    Args:
        project_text: 项目名称 + 类型 + 资质要求 拼接文本

    Returns:
        命中的能力名（第一个命中），未命中返回 None。
    """
    text = project_text or ""
    if not text:
        return None
    for cap_name, info in COMPANY_CAPABILITIES.items():
        for kw in info["keywords"]:
            if kw in text:
                return cap_name
    return None


def get_qualifications() -> List[Dict]:
    """从 company_assets 表读取公司资质证书（资质库）。"""
    try:
        from app.services.bidding_asset_extractor import get_assets
        assets = get_assets(company=COMPANY_NAME, asset_type="资质证书")
        return [
            {"title": a.get("title", ""), "content": a.get("content", "")[:500]}
            for a in assets
        ]
    except Exception as e:
        logger.warning(f"资质库读取失败: {e}")
        return []
