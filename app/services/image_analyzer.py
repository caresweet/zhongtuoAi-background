"""Multi-modal image analysis service.

Uses vision-capable LLM (via Anthropic-compatible API) to analyze
uploaded images and extract structured data for report generation:
- Survey forms → statistical data (support rate, sample counts)
- Public notices → project information (name, location, area, dates)
- Site photos → scene descriptions for report figures
- Meeting photos → attendance and event descriptions
"""

import os
import base64
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config import settings

# Image analysis prompts for different document types
ANALYSIS_PROMPTS = {
    "notice": """请分析这张征地公告/预公告图片，提取以下结构化信息：
1. 公告文号（如"金征预告〔2026〕3号"）
2. 征收目的
3. 征收范围（具体位置描述）
4. 公告期限
5. 征收主体/责任单位
6. 涉及的地块信息（数量、位置）
7. 其他关键信息（补偿标准、工作安排等）

请以JSON格式输出，若无某项信息则填"未提及"：
{
  "announcement_number": "",
  "purpose": "",
  "scope": "",
  "period": "",
  "responsible_unit": "",
  "land_info": "",
  "other_info": ""
}""",

    "survey": """请分析这张问卷调查表/统计表图片，提取以下信息：
1. 调查类型（公众调查/部门调查/单位调查）
2. 有效样本总数
3. 各项选择的统计结果（支持、反对、有条件支持的人数及占比）
4. 主要意见和建议（文本内容）
5. 调查时间
6. 调查对象范围（如涉及的村/社区/部门名称）

请以JSON格式输出：
{
  "survey_type": "",
  "total_samples": 0,
  "results": [
    {"option": "支持", "count": 0, "percentage": 0.0},
    {"option": "反对", "count": 0, "percentage": 0.0},
    {"option": "有条件支持", "count": 0, "percentage": 0.0}
  ],
  "main_opinions": "",
  "survey_date": "",
  "scope": ""
}""",

    "photo": """请描述这张照片的内容，用于社会稳定风险评估报告配图。包括：
1. 场景类型（公示现场/座谈会现场/地块现场/其他）
2. 场景描述（可用于图注的文字说明，正式公文风格）
3. 可见的关键信息（横幅文字、公示栏内容、参会人员情况等）
4. 时间（如有显示）
5. 地点（如有显示）

请以JSON格式输出：
{
  "scene_type": "",
  "caption": "",
  "key_info": "",
  "date": "",
  "location": ""
}""",

    "general": """请分析这张图片，提取与社会稳定风险评估报告编制相关的所有信息。
包括但不限于：文档标题、关键数据、人员信息、日期、地点等。

请以JSON格式输出，字段根据实际内容命名。""",
}


class ImageAnalyzer:
    """Analyzes uploaded images using vision-capable LLM."""

    def __init__(self):
        from app.services.llm_service import llm_service as _llm
        self._llm = _llm

    @property
    def is_available(self) -> bool:
        """Check if the vision API is configured."""
        return self._llm.is_available

    async def analyze(
        self,
        image_path: str,
        analysis_type: str = "general",
        custom_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze a single image and extract structured data.

        Args:
            image_path: Absolute or relative path to the image file.
            analysis_type: One of "notice", "survey", "photo", "general".
            custom_prompt: Optional custom analysis prompt.

        Returns:
            Dict with extracted data.
        """
        # Resolve path
        path = Path(image_path)
        if not path.is_absolute():
            path = settings.STORAGE_DIR / image_path
        if not path.exists():
            return {"error": f"图片文件不存在: {image_path}"}

        # Read and encode image
        with open(path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Detect MIME type from extension
        ext = path.suffix.lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
        }
        media_type = mime_map.get(ext, "image/png")

        # Get analysis prompt
        prompt = custom_prompt or ANALYSIS_PROMPTS.get(
            analysis_type, ANALYSIS_PROMPTS["general"]
        )

        if not self.is_available:
            return self._mock_analysis(analysis_type, path.name)

        try:
            text = await self._llm.chat_with_image(
                text=prompt,
                image_base64=image_data,
                media_type=media_type,
                max_tokens=2048,
            )
            return self._parse_json_response(text or "{}")

        except Exception as e:
            print(f"Image analysis failed: {e}")
            return {
                "error": str(e),
                "fallback": True,
                "image_name": path.name,
                "analysis_type": analysis_type,
            }

    async def analyze_batch(
        self,
        images: List[Dict[str, str]],
        analysis_type: str = "general",
    ) -> List[Dict[str, Any]]:
        """Analyze multiple images in parallel with rate limiting.

        Args:
            images: List of {"path": "...", "type": "survey|photo|notice"} dicts.
            analysis_type: Default analysis type if not specified per image.

        Returns:
            List of analysis result dicts.
        """
        import asyncio

        sem = asyncio.Semaphore(3)

        async def _analyze_with_limit(img: dict) -> dict:
            async with sem:
                img_type = img.get("type", analysis_type)
                try:
                    return await asyncio.wait_for(
                        self.analyze(img["path"], img_type),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    return {"error": "analysis timeout (30s)", "image": img.get("path", "")}
                except Exception as e:
                    return {"error": str(e), "image": img.get("path", "")}

        tasks = [_analyze_with_limit(img) for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        formatted = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                formatted.append({"error": str(result), "image": images[i].get("path", "")})
            else:
                formatted.append(result)

        return formatted

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Extract JSON from LLM response, handling markdown code blocks."""
        # Try to find JSON in code block
        import re
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find bare JSON object
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Return raw text as fallback
        return {"raw_response": text, "parse_error": True}

    def _mock_analysis(self, analysis_type: str, image_name: str) -> Dict[str, Any]:
        """Return mock analysis when API is unavailable (for testing flow)."""
        mock_results = {
            "notice": {
                "announcement_number": "[待提取]",
                "purpose": "[待从公告中提取]",
                "scope": "[待从公告中提取]",
                "period": "[待提取]",
                "responsible_unit": "[待提取]",
                "land_info": "[待提取]",
                "other_info": "[待提取]",
                "_note": f"离线模式：图片'{image_name}'已接收，API配置后将自动提取信息",
            },
            "survey": {
                "survey_type": "[待提取]",
                "total_samples": 0,
                "results": [],
                "main_opinions": "[待从问卷统计中提取]",
                "survey_date": "[待提取]",
                "scope": "[待提取]",
                "_note": f"离线模式：图片'{image_name}'已接收",
            },
            "photo": {
                "scene_type": "[待识别]",
                "caption": "[待从照片中生成图注]",
                "key_info": "",
                "date": "",
                "location": "",
                "_note": f"离线模式：图片'{image_name}'已接收",
            },
            "general": {
                "extracted_info": {},
                "_note": f"离线模式：图片'{image_name}'已接收",
            },
        }
        return mock_results.get(analysis_type, mock_results["general"])


# Singleton
image_analyzer = ImageAnalyzer()
