"""ImageAnalysisAgent — dedicated agent for analyzing uploaded images.

Handles multi-modal analysis of:
- Survey questionnaire forms → extract statistical data (votes, support rates)
- Public notice/announcement photos → extract project info
- Site/location photos → generate professional captions
- Meeting/event photos → generate scene descriptions
- Red-line maps → extract location and boundary info

This agent is called by MasterAgent when users upload images during data collection.
"""

import asyncio
from typing import Dict, List, Any, Optional

from .base_agent import BaseAgent


class ImageAnalysisAgent(BaseAgent):
    """Analyzes uploaded images and extracts structured data for report filling."""

    name = "ImageAnalysisAgent"
    description = "分析上传的图片（问卷/公示/现场照片），提取结构化数据"
    covered_steps = [2, 5, 6]

    async def think(self, state: dict) -> Dict[str, Any]:
        """Determine what images need analysis.

        🔴 ALL images are analyzed by vision API to extract data.
        - 公告/通知类 → extract project info (文号, 日期, 位置)
        - 问卷/调查类 → extract statistics (人数, 支持率, 诉求)
        - 照片/地图 → still analyzed for embedded text, then placed directly
        - Only clearly decorative images are skipped

        Previous logic: only keyword-matched filenames were analyzed.
        New logic: all images analyzed; filename helps classify the type.
        """
        structured = state.get("structured_data", {})
        images_to_analyze = []
        images_to_place_directly = []

        # Keywords for classifying image type (NOT for filtering — all images analyzed)
        ANNOUNCEMENT_KEYWORDS = ["公告", "征收公告", "预公告", "批文", "通知"]
        SURVEY_KEYWORDS = ["问卷", "调查", "意见表", "意见", "评审表", "评审意见", "评价"]
        PHOTO_KEYWORDS = ["照片", "现场", "公示", "会议", "座谈", "走访", "IMG", "DSC", "photo"]

        for step_key in sorted(structured.keys()):
            if not step_key.startswith("step_"):
                continue
            step_data = structured.get(step_key, {})
            step_images = step_data.get("images", []) or step_data.get("attachments", [])
            if not step_images:
                continue

            for img_path in step_images:
                if not img_path or img_path.startswith("["):
                    continue
                if img_path in [i.get("path") for i in images_to_analyze]:
                    continue
                if img_path in [i.get("path") for i in images_to_place_directly]:
                    continue

                fname = img_path.rsplit("/", 1)[-1] if "/" in img_path else img_path

                # 🔴 Skip non-image files — PDF/DOCX etc handled by text extraction
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                if ext in ("pdf", "docx", "doc", "xlsx", "xls", "zip", "rar", "txt", "md"):
                    continue

                # Classify by content type
                if any(kw in fname for kw in ANNOUNCEMENT_KEYWORDS):
                    img_type = "notice"
                elif any(kw in fname for kw in SURVEY_KEYWORDS):
                    img_type = "survey"
                elif any(kw in fname for kw in PHOTO_KEYWORDS):
                    img_type = "photo"  # Still analyzed, but for captions/scene description
                else:
                    img_type = "generic"  # Unknown — analyze to determine content

                images_to_analyze.append({
                    "path": img_path,
                    "type": img_type,
                    "name": fname,
                })

        if not images_to_analyze:
            return {
                "summary": "没有图片需要分析",
                "actions": [],
                "steps": [],
                "direct_place": 0,
            }

        # Count by type for summary
        type_counts = {}
        for img in images_to_analyze:
            t = img["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        summary_parts = []
        if type_counts.get("notice"): summary_parts.append(f"{type_counts['notice']}张公告")
        if type_counts.get("survey"): summary_parts.append(f"{type_counts['survey']}张问卷")
        if type_counts.get("photo"): summary_parts.append(f"{type_counts['photo']}张照片")
        if type_counts.get("generic"): summary_parts.append(f"{type_counts['generic']}张通用")

        steps = [
            f"📷 共 {len(images_to_analyze)} 张图片全部分析（{'、'.join(summary_parts)}）",
            "🔍 使用视觉AI提取图片中的文字和数据...",
        ]
        for img in images_to_analyze[:10]:
            steps.append(f"  📷 {img['name'][:40]} → {img['type']}")
        if len(images_to_analyze) > 10:
            steps.append(f"  ... 及其他 {len(images_to_analyze) - 10} 张")

        return {
            "summary": f"分析 {len(images_to_analyze)} 张图片（{'、'.join(summary_parts)}）",
            "steps": steps,
            "actions": [{"type": "analyze_images", "images": images_to_analyze}],
            "direct_place": 0,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute image analysis (announcements + opinion forms only)
        and report directly-placed images."""
        actions = plan.get("actions", [])
        filled_data = state.get("filled_data", {})
        structured = state.get("structured_data", {})
        generated = state.get("generated_sections", {})
        results = {}

        # Report directly-placed images (no vision API needed)
        direct_count = plan.get("direct_place", 0)
        if direct_count > 0:
            direct_images = state.get("_direct_place_images", [])
            if self._stream_queue and direct_images:
                names = [d.get("name", "") for d in direct_images[:8]]
                await self._stream_queue.put({
                    "event": "message",
                    "data": {
                        "role": "agent",
                        "content": (
                            f"📎 {len(direct_images)} 张图片（照片/地图）按名称直接填入报告对应位置：\n"
                            + "\n".join(f"  • {n}" for n in names)
                            + ("\n  • ..." if len(direct_images) > 8 else "")
                        ),
                        "message_type": "text",
                    },
                })

        # Analyze announcement + opinion form images via vision API
        for action in actions:
            if action.get("type") != "analyze_images":
                continue

            images = action.get("images", [])
            if not images:
                continue

            results = await self._analyze_images(images, state)
            await self._fill_from_analysis(results, state, filled_data, generated)
            await self._emit_analysis_results(results)

        return {
            "filled_data": filled_data,
            "generated_sections": generated,
            "image_analysis": results,
        }

    async def _analyze_images(
        self, images: List[dict], state: dict
    ) -> Dict[str, Any]:
        """Analyze images in PARALLEL with progress feedback.

        Key optimizations vs old sequential version:
        1. asyncio.gather with semaphore(3) for parallel vision API calls
        2. Reduced timeout: 30s per image (was 60s)
        3. Real-time progress events via SSE
        4. Fast-fail: skip non-image files immediately
        """
        import asyncio as _asyncio

        results = {
            "survey_results": [],
            "notice_results": [],
            "photo_results": [],
            "errors": [],
            "aggregated_survey": None,
            "timed_out": False,
        }

        # Import vision service
        try:
            from app.services.image_analyzer import image_analyzer
        except ImportError:
            results["errors"].append({"error": "图片分析服务不可用"})
            return results

        if not image_analyzer.is_available:
            results["errors"].append({
                "error": "视觉API未配置，图片已保存。请描述图片内容。",
                "fallback": True,
            })
            return results

        # Filter to valid image files only
        valid_images = []
        for img_info in images:
            img_path = img_info.get("path", "")
            ext = img_path.rsplit(".", 1)[-1].lower() if "." in img_path else ""
            if ext in ("pdf", "docx", "doc", "xlsx", "xls", "zip", "rar", "txt", "md"):
                results["errors"].append({
                    "path": img_path,
                    "error": f"跳过非图片文件(.{ext})",
                })
                continue
            valid_images.append(img_info)

        if not valid_images:
            return results

        total = len(valid_images)
        completed = 0

        # Semaphore to limit concurrent API calls (prevent rate limiting)
        sem = _asyncio.Semaphore(3)

        async def _analyze_one(img_info: dict) -> dict:
            """Analyze one image with timeout and semaphore."""
            nonlocal completed
            async with sem:
                img_path = img_info.get("path", "")
                img_type = img_info.get("type", "general")
                try:
                    result = await _asyncio.wait_for(
                        image_analyzer.analyze(img_path, img_type),
                        timeout=30.0,  # Reduced from 60s
                    )
                    completed += 1
                    # Emit progress every image
                    if self._stream_queue:
                        await self._stream_queue.put({
                            "event": "thinking",
                            "data": {"content": f"📷 [{completed}/{total}] 图片分析完成: {img_path.rsplit('/', 1)[-1][:30]}"},
                        })
                    return {"result": result, "type": img_type, "path": img_path}
                except _asyncio.TimeoutError:
                    completed += 1
                    return {"error": "分析超时(30s)", "path": img_path, "timed_out": True}
                except Exception as e:
                    completed += 1
                    return {"error": f"{type(e).__name__}: {str(e)[:50]}", "path": img_path}

        # Emit start event
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "thinking",
                "data": {"content": f"🚀 并行分析 {total} 张图片（并发3路，每张30s超时）..."},
            })

        # Run all analyses in parallel
        tasks = [_analyze_one(img) for img in valid_images]
        outcomes = await _asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                results["errors"].append({"error": str(outcome)})
                continue
            if not isinstance(outcome, dict):
                continue

            if outcome.get("error"):
                results["errors"].append(outcome)
                if outcome.get("timed_out"):
                    results["timed_out"] = True
                continue

            result = outcome.get("result", {})
            img_type = outcome.get("type", "general")

            if "error" in result:
                results["errors"].append({"path": outcome.get("path", ""), "error": result["error"]})
                continue

            if img_type == "survey":
                results["survey_results"].append(result)
            elif img_type == "notice":
                results["notice_results"].append(result)
            else:
                results["photo_results"].append(result)

        if results["survey_results"]:
            results["aggregated_survey"] = self._aggregate_survey_results(results["survey_results"])

        # Emit completion summary
        success = len(results["survey_results"]) + len(results["notice_results"]) + len(results["photo_results"])
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "thinking",
                "data": {"content": f"✅ 图片分析完成：{success}/{total} 成功，{len(results['errors'])} 失败"},
            })

        return results

    def _aggregate_survey_results(
        self, survey_results: List[dict]
    ) -> Dict[str, Any]:
        """Aggregate multiple survey image analyses into unified statistics."""
        aggregated = {
            "total_samples": 0,
            "support_count": 0,
            "oppose_count": 0,
            "conditional_support_count": 0,
            "support_rate": 0.0,
            "main_opinions": [],
            "survey_dates": [],
            "scopes": [],
        }

        for result in survey_results:
            if "error" in result:
                continue

            # Sum up sample counts
            aggregated["total_samples"] += result.get("total_samples", 0)

            # Process individual results
            for item in result.get("results", []):
                option = item.get("option", "")
                count = item.get("count", 0)
                if "支持" in option and "条件" not in option and "有" not in option:
                    aggregated["support_count"] += count
                elif "反对" in option:
                    aggregated["oppose_count"] += count
                elif "条件" in option or "有" in option:
                    aggregated["conditional_support_count"] += count

            # Collect opinions
            opinions = result.get("main_opinions", "")
            if opinions and opinions != "[待从问卷统计中提取]":
                aggregated["main_opinions"].append(opinions)

            # Collect dates
            date = result.get("survey_date", "")
            if date and date != "[待提取]":
                aggregated["survey_dates"].append(date)

            # Collect scopes
            scope = result.get("scope", "")
            if scope and scope != "[待提取]":
                aggregated["scopes"].append(scope)

        # Calculate support rate
        total = aggregated["total_samples"] or 1
        aggregated["support_rate"] = round(
            aggregated["support_count"] / total * 100, 1
        )

        return aggregated

    async def _fill_from_analysis(
        self,
        results: Dict[str, Any],
        state: dict,
        filled_data: dict,
        generated: dict,
    ):
        """Fill structured data from image analysis results."""
        placeholders = state.get("template_placeholders", [])

        # Fill survey data into relevant placeholders
        survey = results.get("aggregated_survey")
        if survey and survey.get("total_samples", 0) > 0:
            # Store in structured data for SurveyAnalyzer to use
            step_6 = state.get("structured_data", {}).get("step_6", {})
            step_6.update({
                "total_samples": survey["total_samples"],
                "support_count": survey["support_count"],
                "oppose_count": survey["oppose_count"],
                "conditional_support_count": survey["conditional_support_count"],
                "support_rate": survey["support_rate"],
            })
            state.setdefault("structured_data", {})["step_6"] = step_6

            # 🔴 Always write to well-known filled_data keys so downstream
            # consumers (Chapter3Agent, table_registry, assembler) find the data
            filled_data.setdefault("survey_total_count", str(survey["total_samples"]))
            filled_data.setdefault("total_samples", str(survey["total_samples"]))
            filled_data.setdefault("support_count", str(survey["support_count"]))
            filled_data.setdefault("oppose_count", str(survey["oppose_count"]))
            filled_data.setdefault("conditional_support_count", str(survey["conditional_support_count"]))
            filled_data.setdefault("support_rate", str(survey["support_rate"]))

            # Try to match to template placeholders
            for ph in placeholders:
                key = ph.get("key", "")
                display = ph.get("display_name", "")
                if key in filled_data and filled_data[key]:
                    continue

                if "样本" in display or "总样本" in display:
                    filled_data[key] = str(survey["total_samples"])
                elif "支持率" in display:
                    filled_data[key] = f"{survey['support_rate']}%"
                elif "支持" in display and "率" not in display:
                    filled_data[key] = str(survey["support_count"])
                elif "反对" in display:
                    filled_data[key] = str(survey["oppose_count"])

        # Fill photo captions
        if results.get("photo_results"):
            photo_captions = []
            for photo in results["photo_results"]:
                if "error" not in photo:
                    caption = photo.get("caption", "")
                    if caption and caption != "[待从照片中生成图注]":
                        photo_captions.append(caption)

            if photo_captions:
                generated["photo_captions"] = photo_captions

                # Fill into image-related placeholders
                for ph in placeholders:
                    key = ph.get("key", "")
                    display = ph.get("display_name", "")
                    if key in filled_data and filled_data[key]:
                        continue
                    if "图" in display:
                        # Find the most relevant caption
                        for caption in photo_captions:
                            if any(kw in caption for kw in ["位置", "红线", "公示", "现场"]):
                                filled_data[key] = caption
                                break

        # Fill notice data
        if results.get("notice_results"):
            for notice in results["notice_results"]:
                if "error" in notice:
                    continue

                # Try to match notice info to placeholders
                notice_fields = {
                    "announcement_number": ["公告文号", "预公告号", "文号"],
                    "responsible_unit": ["责任单位", "征收主体", "主体"],
                    "scope": ["征收范围", "范围", "位置"],
                    "purpose": ["征收目的", "目的"],
                    "period": ["公告期限", "期限"],
                }

                for field, keywords in notice_fields.items():
                    value = notice.get(field, "")
                    if not value or value.startswith("[待"):
                        continue

                    for ph in placeholders:
                        key = ph.get("key", "")
                        display = ph.get("display_name", "")
                        if key in filled_data and filled_data[key]:
                            continue
                        if any(kw in display for kw in keywords):
                            filled_data[key] = value
                            break

    async def _emit_analysis_results(self, results: Dict[str, Any]):
        """Emit image analysis results as chat messages."""
        if not self._stream_queue:
            return

        # If all images failed/timed out, tell the user to describe manually
        total_errors = len(results.get("errors", []))
        total_success = (
            len(results.get("survey_results", []))
            + len(results.get("photo_results", []))
        )

        if total_errors > 0 and total_success == 0:
            # All images failed — ask user to describe
            error_msgs = []
            for err in results["errors"][:3]:
                error_msgs.append(f"  • {err.get('path', '图片')}: {err.get('error', '分析失败')[:80]}")

            fallback_msg = (
                f"## 📷 图片已收到\n\n"
                f"收到 {total_errors} 张图片，但自动分析未能完成：\n"
                + "\n".join(error_msgs) +
                f"\n\n💡 **请简单描述图片内容**，例如：\n"
                f"• 如果是问卷调查表 → 告诉我有多少份、支持/反对票数\n"
                f"• 如果是位置图 → 告诉我具体地址（XX县XX街道）\n"
                f"• 如果是公示照片 → 告诉我在哪里公示的、什么时候"
            )
            await self._emit_message(fallback_msg, message_type="warning")
            return

        # Emit survey analysis summary
        survey = results.get("aggregated_survey")
        if survey and survey.get("total_samples", 0) > 0:
            msg_parts = ["## 📊 问卷调查图片分析结果\n"]
            msg_parts.append(f"- **总样本数**：{survey['total_samples']} 份")
            msg_parts.append(f"- **支持**：{survey['support_count']} 人")
            msg_parts.append(f"- **条件支持**：{survey['conditional_support_count']} 人")
            msg_parts.append(f"- **反对**：{survey['oppose_count']} 人")
            msg_parts.append(f"- **支持率**：{survey['support_rate']}%")

            if survey.get("main_opinions"):
                msg_parts.append(f"\n**主要意见**：")
                for op in survey["main_opinions"][:3]:
                    msg_parts.append(f"  • {op[:100]}")

            await self._emit_message("\n".join(msg_parts), message_type="analysis_result")

        # Emit photo analysis
        if results.get("photo_results"):
            captions = [
                r.get("caption", "")
                for r in results["photo_results"]
                if "error" not in r and r.get("caption")
            ]
            if captions:
                msg_parts = ["## 📷 图片分析结果\n"]
                for i, caption in enumerate(captions[:5], 1):
                    msg_parts.append(f"{i}. {caption[:150]}")
                await self._emit_message("\n".join(msg_parts), message_type="analysis_result")

        # Emit any errors
        if results.get("errors"):
            error_msgs = []
            for err in results["errors"][:3]:
                error_msgs.append(f"  ⚠️ {err.get('path', '')}: {err.get('error', '')[:80]}")
            if error_msgs:
                await self._emit_message(
                    "⚠️ 部分图片分析失败：\n" + "\n".join(error_msgs),
                    message_type="warning",
                )

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """Validate image analysis completeness."""
        issues = []
        analysis = result.get("image_analysis", {})

        if analysis.get("errors"):
            issues.append(f"图片分析有 {len(analysis['errors'])} 个错误")

        survey = analysis.get("aggregated_survey", {})
        if survey and survey.get("support_rate", 0) < 0:
            issues.append("调查支持率数据异常")

        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """Write image analysis results into state."""
        if "filled_data" in result:
            state["filled_data"] = {
                **state.get("filled_data", {}),
                **result["filled_data"],
            }
        if "generated_sections" in result:
            state["generated_sections"] = {
                **state.get("generated_sections", {}),
                **result["generated_sections"],
            }
        return state
