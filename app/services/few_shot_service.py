"""Dynamic Few-Shot Service — retrieves similar historical reports for chapter generation.

Replaces static hardcoded examples with project-matched retrieval:
1. Query knowledge base for high-scoring reports
2. Score similarity by location, area, land use type
3. Extract chapter content from best matches
4. Return formatted few-shot examples for prompt injection
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Chapter extraction patterns (maps chapter numbers to content markers)
# ═══════════════════════════════════════════════════════════════

CHAPTER_PATTERNS = {
    1: r'(?:第[一二三]章|1[\.\s、])',
    2: r'(?:第[二三四]章|2[\.\s、])',
    3: r'(?:第[三四五]章|3[\.\s、])',
    4: r'(?:第[四五六]章|4[\.\s、])',
    5: r'(?:第[五六七]章|5[\.\s、])',
    6: r'(?:第[六七八]章|6[\.\s、])',
    7: r'(?:第[七八九]章|7[\.\s、])',
    8: r'(?:第[八九]章|8[\.\s、])',
    9: r'(?:第[九十]章|9[\.\s、])',
    10: r'(?:第[十]章|10[\.\s、])',
}


# ═══════════════════════════════════════════════════════════════
# Location similarity scoring
# ═══════════════════════════════════════════════════════════════

_JIANGSU_COUNTIES = {
    "洪泽": "淮安", "金湖": "淮安", "涟水": "淮安", "盱眙": "淮安",
    "清江浦": "淮安", "淮阴": "淮安", "淮安": "淮安",
    "江宁": "南京", "浦口": "南京", "六合": "南京", "溧水": "南京", "高淳": "南京",
    "江阴": "无锡", "宜兴": "无锡",
    "新沂": "徐州", "邳州": "徐州", "丰县": "徐州", "沛县": "徐州", "睢宁": "徐州",
    "溧阳": "常州", "金坛": "常州",
    "张家港": "苏州", "常熟": "苏州", "太仓": "苏州", "昆山": "苏州",
    "如皋": "南通", "海安": "南通", "启东": "南通", "如东": "南通",
    "东海": "连云港", "灌云": "连云港", "灌南": "连云港", "赣榆": "连云港",
    "东台": "盐城", "射阳": "盐城", "阜宁": "盐城", "滨海": "盐城", "响水": "盐城", "建湖": "盐城",
    "宝应": "扬州", "高邮": "扬州", "仪征": "扬州",
    "丹阳": "镇江", "扬中": "镇江", "句容": "镇江",
    "兴化": "泰州", "靖江": "泰州", "泰兴": "泰州",
    "沭阳": "宿迁", "泗洪": "宿迁", "泗阳": "宿迁",
}


def _score_location(loc_a: str, loc_b: str) -> float:
    """Score location similarity: same county=1.0, same city=0.6, same province=0.3, none=0.0."""
    if not loc_a or not loc_b:
        return 0.0
    city_a = ""; city_b = ""
    for county, city in _JIANGSU_COUNTIES.items():
        if county in loc_a: city_a = city
        if county in loc_b: city_b = city
    if city_a and city_a == city_b:
        if any(c in loc_a for c in [k for k,v in _JIANGSU_COUNTIES.items() if v==city_a]) and \
           any(c in loc_b for c in [k for k,v in _JIANGSU_COUNTIES.items() if v==city_b]):
            if any(c in loc_a and c in loc_b for c in [k for k,v in _JIANGSU_COUNTIES.items() if v==city_a]):
                return 1.0  # same county
            return 0.6  # same city
    return 0.3  # same province


def _score_area(area_a, area_b) -> float:
    """Score area similarity — closer areas score higher."""
    try:
        a = float(area_a); b = float(area_b)
    except (ValueError, TypeError):
        return 0.5
    if a <= 0 or b <= 0: return 0.5
    ratio = min(a, b) / max(a, b)
    return ratio  # 0.0-1.0, higher = more similar


# ═══════════════════════════════════════════════════════════════
# Main service
# ═══════════════════════════════════════════════════════════════

class FewShotService:
    """Dynamic few-shot retrieval for report generation."""

    async def find_best_matches(
        self,
        project_info: Dict[str, Any],
        chapter_num: int = 0,
        domain: str = "stability",
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Find the best matching historical reports for a given project.

        Args:
            project_info: {location, area_mu, land_use, project_name, ...}
            chapter_num: specific chapter to retrieve (0 = all)
            domain: knowledge base domain
            top_k: number of matches to return

        Returns:
            List of {title, text, chapter_content, similarity_score}
        """
        try:
            # Get candidates from knowledge base
            candidates = await self._get_candidates(domain)
            if not candidates:
                return []

            # Score and rank
            scored = []
            for cand in candidates:
                score = 0.0
                score += _score_location(project_info.get("location", ""), cand.get("location", "")) * 0.4
                score += _score_area(project_info.get("area_mu", 0), cand.get("area_mu", 0)) * 0.3
                # Land use type match
                if project_info.get("land_use") and cand.get("land_use"):
                    if project_info["land_use"] == cand["land_use"]:
                        score += 0.3
                    elif any(kw in (cand.get("land_use","")) for kw in project_info.get("land_use","").split("用")[0:1] if kw):
                        score += 0.15
                scored.append((score, cand))

            scored.sort(key=lambda x: x[0], reverse=True)

            # Extract chapter content
            results = []
            for score, cand in scored[:top_k]:
                if score < 0.3:  # Too dissimilar
                    continue
                entry = {
                    "title": cand.get("title", ""),
                    "similarity_score": round(score, 2),
                    "location": cand.get("location", ""),
                    "area_mu": cand.get("area_mu", ""),
                    "land_use": cand.get("land_use", ""),
                }
                # Extract specific chapter content
                if chapter_num > 0:
                    ch_text = self._extract_chapter(cand.get("text", ""), chapter_num)
                    if ch_text:
                        entry["chapter_content"] = ch_text[:3000]
                else:
                    entry["full_text"] = (cand.get("text", "") or "")[:8000]
                results.append(entry)

            return results

        except Exception as e:
            logger.warning(f"Few-shot retrieval failed: {e}")
            return []

    async def _get_candidates(self, domain: str) -> List[Dict[str, Any]]:
        """Retrieve high-quality historical reports from the knowledge base."""
        try:
            from app.database.knowledge_db import async_session
            from sqlalchemy import text
            async with async_session() as db:
                rows = await db.execute(text("""
                    SELECT kd.title, kd.raw_text, kd.cleaned_text,
                           gf.overall_score, gf.feedback_json
                    FROM knowledge_documents kd
                    LEFT JOIN generation_feedback gf ON kd.title = gf.report_title
                    WHERE kd.domain = :dom AND kd.document_type IN ('report','example_report')
                      AND kd.is_active = 1
                    ORDER BY gf.overall_score DESC NULLS LAST, kd.created_at DESC
                    LIMIT 20
                """), {"dom": domain})
                results = []
                for row in rows.fetchall():
                    title = row[0] or ""
                    text = row[1] or row[2] or ""
                    score = row[3]
                    # Only use reports with score >= 80 or unscored (assume good)
                    if score is None or score >= 80:
                        # Extract metadata from title
                        loc = ""
                        for county in _JIANGSU_COUNTIES:
                            if county in title:
                                loc = county
                                break
                        area_match = re.search(r'(\d+\.?\d*)\s*亩', title)
                        area = area_match.group(1) if area_match else ""
                        land_use_match = re.search(r'(?:商业|住宅|工业|公共|交通|水利|教育|医疗)', title)
                        land_use = land_use_match.group(0) if land_use_match else ""

                        results.append({
                            "title": title,
                            "text": text,
                            "location": loc,
                            "area_mu": area,
                            "land_use": land_use,
                            "score": score,
                        })
                return results
        except Exception as e:
            logger.warning(f"Candidate retrieval failed: {e}")
            return []

    def _extract_chapter(self, text: str, chapter_num: int) -> str:
        """Extract a specific chapter's content from a full report text."""
        if not text:
            return ""

        # Build a regex to find the chapter boundaries
        titles = ["拟征收决策基本概况", "评估过程、方法和依据", "社会稳定风险因素调查",
                  "决策综合分析", "风险因素识别", "措施前风险等级研判",
                  "风险防范与化解措施", "措施后风险等级评估", "评估结论与建议", "应急预案"]

        if chapter_num < 1 or chapter_num > 10:
            return ""

        current_title = titles[chapter_num - 1]
        next_title = titles[chapter_num] if chapter_num < 10 else None

        # Find current chapter start
        start_pattern = rf'(?:##\s*{re.escape(current_title)}|第\s*{chapter_num}\s*章\s*{re.escape(current_title)})'
        start_match = re.search(start_pattern, text)
        if not start_match:
            # Try simpler patterns
            start_match = re.search(rf'##\s*{re.escape(current_title)}', text)
        if not start_match:
            return ""

        start = start_match.start()

        # Find next chapter start (or end of text)
        if next_title:
            end_pattern = rf'(?:##\s*{re.escape(next_title)}|第\s*{chapter_num+1}\s*章)'
            end_match = re.search(end_pattern, text[start+1:])
            end = start + 1 + end_match.start() if end_match else len(text)
        else:
            end = len(text)

        return text[start:end].strip()


# ── Sync cache for prompt-time access ──
_few_shot_cache = {"examples": {}, "project_hash": "", "updated": 0}


async def refresh_few_shot_cache(project_info: Dict[str, Any], domain: str = "stability"):
    """Refresh the few-shot cache for a project. Called at generation start."""
    import time as _t
    svc = FewShotService()
    project_hash = f"{project_info.get('location','')}|{project_info.get('area_mu','')}|{project_info.get('land_use','')}"
    if _few_shot_cache["project_hash"] == project_hash and _t.time() - _few_shot_cache["updated"] < 3600:
        return  # Already cached for this project
    for ch in range(1, 11):
        matches = await svc.find_best_matches(project_info, chapter_num=ch, domain=domain, top_k=2)
        if matches:
            _few_shot_cache["examples"][ch] = matches
    _few_shot_cache["project_hash"] = project_hash
    _few_shot_cache["updated"] = _t.time()


def get_cached_few_shot(chapter_num: int) -> str:
    """Get cached few-shot examples for a chapter. Sync, for prompt injection."""
    matches = _few_shot_cache["examples"].get(chapter_num, [])
    if not matches:
        return ""
    parts = ["\n## ✍️ 相似项目参考（请模仿风格和数据呈现方式，但替换为本项目数据）\n"]
    for i, m in enumerate(matches, 1):
        if m.get("chapter_content"):
            parts.append(f"### 参考{i}（相似度{m['similarity_score']}）\n{m['chapter_content']}\n")
    return "\n".join(parts)


# Singleton
few_shot_service = FewShotService()
