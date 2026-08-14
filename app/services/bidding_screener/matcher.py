"""匹配引擎 — 规则粗筛 + LLM 精筛。

规则粗筛（快，免费）：招标项目文本命中公司能力关键词 → 候选。
LLM 精筛（准）：对候选项目，LLM 判断"公司能不能做"，输出匹配度 + 理由。
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from .capability import match_by_rules, COMPANY_CAPABILITIES, COMPANY_NAME

logger = logging.getLogger(__name__)


async def screen_projects(
    projects: List[Dict],
    llm_service=None,
    use_llm: bool = True,
) -> List[Dict]:
    """筛选招标项目，返回公司可做的项目列表。

    Args:
        projects: excel 解析出的结构化记录
        llm_service: LLM 服务（use_llm=True 时必需）
        use_llm: 是否用 LLM 精筛

    Returns:
        匹配的项目列表（按匹配度降序），每项含 match_score / match_reason / link
    """
    # ── 规则粗筛 ──
    candidates = []
    for p in projects:
        text = f"{p.get('name','')} {p.get('type','')} {p.get('qualification','')}"
        capability = match_by_rules(text)
        if capability:
            candidates.append({**p, "_capability": capability})

    if not candidates:
        return []

    # ── LLM 精筛 ──
    if use_llm and llm_service:
        return await _llm_refine(candidates, llm_service)
    else:
        # 无 LLM：规则命中即算匹配，匹配度按能力置信度
        return [
            _build_result(p, 0.8, f"命中能力「{p['_capability']}」")
            for p in candidates
        ]


async def _llm_refine(candidates: List[Dict], llm_service) -> List[Dict]:
    """LLM 精筛：对规则候选逐一判断公司能否承接。"""
    capabilities_desc = "\n".join(
        f"- {name}：{'、'.join(info['keywords'])}"
        + (f"（资质：{info['qualification']}）" if info['qualification'] else "")
        for name, info in COMPANY_CAPABILITIES.items()
    )

    results = []
    # 批量处理（每批最多 10 条，控制 LLM 调用）
    for i in range(0, len(candidates), 10):
        batch = candidates[i:i + 10]
        batch_text = "\n".join(
            f"{j+1}. 项目：{p.get('name','')}；类型：{p.get('type','')}；"
            f"资质要求：{p.get('qualification','')}；预算：{p.get('budget','')}"
            for j, p in enumerate(batch)
        )

        prompt = f"""你是招标筛选助手。判断江苏众拓项目代理咨询有限公司能否承接以下招标项目。

## 公司能力
{capabilities_desc}

## 候选项目（规则初筛命中）
{batch_text}

## 判断规则
- 能承接：项目内容属于公司业务范围（稳评/招标代理/工程咨询/土地规划咨询），且资质要求不超出公司能力
- 不能承接：项目需要公司没有的资质（如施工资质、医疗资质、特定行业资质），或完全不属于公司业务
- 匹配度：0-1，越高越适合

## 输出 JSON 格式
```json
{{"results": [{{"index": 1, "can_do": true/false, "score": 0.0-1.0, "reason": "一句话理由"}}]}}
```"""

        try:
            resp = await llm_service.chat_with_reasoning(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000, temperature=0.1,
            )
            content = resp.get("content", "")
            verdicts = _parse_verdicts(content)
            for j, p in enumerate(batch):
                v = verdicts.get(j + 1)
                if v and v.get("can_do"):
                    results.append(_build_result(p, v.get("score", 0.7), v.get("reason", "")))
        except Exception as e:
            logger.warning(f"LLM 精筛失败（保留规则命中）: {e}")
            # LLM 失败时保留规则命中
            for p in batch:
                results.append(_build_result(p, 0.7, f"命中能力「{p['_capability']}」"))

    # 按匹配度降序
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results


def _parse_verdicts(content: str) -> Dict[int, Dict]:
    """解析 LLM 返回的 JSON 判定。"""
    import re
    m = re.search(r'\{[\s\S]*\}', content)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return {r.get("index"): r for r in data.get("results", [])}
    except json.JSONDecodeError:
        return {}


def _build_result(project: Dict, score: float, reason: str) -> Dict:
    """构建匹配结果（保留项目字段 + 匹配信息）。"""
    return {
        "name": project.get("name", ""),
        "region": project.get("region", ""),
        "type": project.get("type", ""),
        "buyer": project.get("buyer", ""),
        "budget": project.get("budget", ""),
        "qualification": project.get("qualification", ""),
        "publish_date": project.get("publish_date", ""),
        "link": project.get("link", ""),
        "match_score": round(float(score), 2),
        "match_reason": reason or "",
        "capability": project.get("_capability", ""),
    }
