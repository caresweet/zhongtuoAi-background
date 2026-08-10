"""Phase 3: Section-relative image positioning and intelligent matching.

Key improvements over old approach:
1. Section-relative ImageSlot resolution (not absolute paragraph indices)
2. LLM-based image-to-slot matching (not hardcoded IMG_FILE_MAP)
3. Auto-generated captions from project data
"""

import re
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.services.pipeline.pipeline_context import (
    ImageSlot, DocumentStructure, PipelineContext,
)


# ── Image matching prompt template ──

IMAGE_MATCHING_PROMPT = """你是一个报告图片匹配专家。请将项目图片文件匹配到报告中的图片位置。

## 报告图片位置（ImageSlot）：
{slot_descriptions}

## 可用图片文件：
{image_list}

## 匹配规则：
1. 根据图片文件名和所属文件夹判断图片内容
2. 公示照片 → 匹配到含"公示"/"公告"的slot
3. 现场照片 → 匹配到含"现场"/"勘察"/"地块"的slot
4. 会议/座谈照片 → 匹配到含"座谈"/"会议"/"村民"的slot
5. 专家评审照片 → 匹配到含"专家"/"评审"的slot
6. 地图/位置图 → 匹配到含"位置"/"地图"/"示意"的slot
7. 问卷扫描件 → 匹配到附件中的问卷slot
8. 每张图片只能用一次，每个slot只能匹配一张图片

请以JSON格式输出匹配结果：
{{"matches": [{{"slot_id": "...", "image_path": "...", "confidence": "high"}}]}}
如果某个slot无法匹配，将confidence设为"none"并省略image_path。
"""


class ImagePositioner:
    """Phase 3: Section-relative image positioning and intelligent matching."""

    def __init__(self, llm_service=None, multimodal_embedder=None):
        """Initialize with LLM service for image matching.

        Args:
            llm_service: LLMService for chat() calls (image-to-slot matching).
            multimodal_embedder: MultiModalEmbedder for image description.
        """
        self.llm = llm_service
        self.mm_embedder = multimodal_embedder

    # ── Main entry point ──

    async def position_images(
        self,
        doc_structure: DocumentStructure,
        context: PipelineContext,
    ) -> Dict[str, ImageSlot]:
        """Run Phase 3: match project images to document image slots.

        Args:
            doc_structure: From Phase 2.
            context: From Phase 1 (has image_files, image_folders).

        Returns:
            Dict mapping slot_id → ImageSlot (with matched_image filled).
        """
        if not doc_structure.image_slots:
            print("  No image slots detected")
            return {}

        if not context.image_files:
            print("  No project images available")
            return {slot.slot_id: slot for slot in doc_structure.image_slots}

        # Step 1: Resolve each slot to its absolute paragraph index
        for slot in doc_structure.image_slots:
            slot.matched_image = None  # Reset

        # Step 2: Match images to slots (LLM-based)
        matches = await self._match_images_to_slots(
            doc_structure.image_slots,
            context.image_files,
            context.image_folders,
        )

        # Step 3: Apply matches
        for slot in doc_structure.image_slots:
            if slot.slot_id in matches:
                slot.matched_image = matches[slot.slot_id]
                print(f"  ✅ {slot.slot_id} → {Path(matches[slot.slot_id]).name}")
            else:
                print(f"  ⚠️ {slot.slot_id}: no match found")

        # Step 4: Generate captions
        self._generate_captions(doc_structure.image_slots, context)

        return {slot.slot_id: slot for slot in doc_structure.image_slots}

    # ── Image matching ──

    async def _match_images_to_slots(
        self,
        slots: List[ImageSlot],
        image_files: List[str],
        image_folders: Dict[str, List[str]],
    ) -> Dict[str, str]:
        """Match project images to document slots.

        Uses LLM for intelligent matching or falls back to keyword-based matching.
        Returns {slot_id: image_path}.
        """
        # Try LLM matching first
        if self.llm:
            try:
                return await self._llm_match(slots, image_files, image_folders)
            except Exception as e:
                print(f"  ⚠️ LLM matching failed: {e}, using keyword matching")

        # Fallback: keyword-based matching
        return self._keyword_match(slots, image_files, image_folders)

    async def _llm_match(
        self,
        slots: List[ImageSlot],
        image_files: List[str],
        image_folders: Dict[str, List[str]],
    ) -> Dict[str, str]:
        """LLM-based intelligent image-to-slot matching."""
        # Build slot descriptions
        slot_descs = []
        for slot in slots:
            slot_descs.append(
                f"  - slot_id: {slot.slot_id}\n"
                f"    section: {slot.section_title}\n"
                f"    type: {slot.suggested_type}\n"
                f"    caption_template: {slot.caption_template[:60]}\n"
                f"    priority: {'required' if slot.priority == 1 else 'optional'}"
            )

        # Build image list with folder context
        img_list = []
        for i, img in enumerate(image_files):
            folder = Path(img).parent.name
            filename = Path(img).name
            img_list.append(f"  [{i}] {filename} (文件夹: {folder})")

        prompt = IMAGE_MATCHING_PROMPT.format(
            slot_descriptions="\n".join(slot_descs),
            image_list="\n".join(img_list),
        )

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        # Parse response
        import json
        try:
            data = json.loads(response)
            matches = {}
            for m in data.get("matches", []):
                slot_id = m.get("slot_id", "")
                img_path = m.get("image_path", "")
                confidence = m.get("confidence", "none")
                if slot_id and img_path and confidence != "none":
                    # Resolve image path (might be filename, need full path)
                    resolved = self._resolve_image_path(img_path, image_files)
                    if resolved:
                        matches[slot_id] = resolved
            return matches
        except (json.JSONDecodeError, KeyError):
            return {}

    def _keyword_match(
        self,
        slots: List[ImageSlot],
        image_files: List[str],
        image_folders: Dict[str, List[str]],
    ) -> Dict[str, str]:
        """Fallback: keyword-based image matching using FOLDER names primarily."""
        matches = {}
        used_images = set()

        for slot in sorted(slots, key=lambda s: s.priority):
            # Use folder-level keywords (better for Chinese folder names)
            folder_kw = self._slot_to_folder_keywords(slot)
            file_kw = self._slot_keywords(slot)
            all_kw = folder_kw + file_kw

            best_score = 0
            best_image = None
            for img in image_files:
                if img in used_images:
                    continue
                folder = Path(img).parent.name.lower()
                filename = Path(img).name.lower()
                combined = folder + " " + filename

                # Score: folder keyword match = 3pts, filename match = 1pt
                score = sum(3 for kw in folder_kw if kw.lower() in folder)
                score += sum(1 for kw in file_kw if kw.lower() in filename)
                if score > best_score:
                    best_score = score
                    best_image = img

            if best_image and best_score >= 2:  # Lower threshold since folder matches are strong
                matches[slot.slot_id] = best_image
                used_images.add(best_image)

        return matches

    @staticmethod
    def _slot_to_folder_keywords(slot: ImageSlot) -> List[str]:
        """Map slot type to folder name keywords (Chinese folder matching)."""
        stype = slot.suggested_type
        mapping = {
            "location_map": ["图片-稳评", "位置", "地图", "百度"],
            "notice": ["公示照片", "公示"],
            "meeting": ["村民开会", "开会", "座谈", "群众开会"],
            "site_photo": ["临时用地", "现场照片", "现场"],
            "expert_review": ["专家评审", "专家"],
            "survey": ["问卷", "调查"],
            "attachment": ["扫描", "意见", "附件"],
        }
        return mapping.get(stype, [stype])

    @staticmethod
    def _slot_keywords(slot: ImageSlot) -> List[str]:
        """Get search keywords for a slot."""
        keywords = []
        stype = slot.suggested_type

        if stype == "location_map":
            keywords = ["位置", "地图", "示意", "平面", "红线", "百度"]
        elif stype == "notice":
            keywords = ["公示", "公告", "张贴", "公告栏"]
        elif stype == "meeting":
            keywords = ["座谈", "会议", "村民", "开会", "群众"]
        elif stype == "site_photo":
            keywords = ["现场", "实地", "勘察", "地块", "临时用地"]
        elif stype == "expert_review":
            keywords = ["专家", "评审"]
        elif stype == "survey":
            keywords = ["问卷", "调查"]
        elif stype == "attachment":
            keywords = ["附件", "扫描", "问卷", "纪要"]

        return keywords

    @staticmethod
    def _resolve_image_path(name_or_path: str, image_files: List[str]) -> Optional[str]:
        """Resolve a filename or partial path to a full path."""
        # If it's already a full path
        if Path(name_or_path).exists():
            return name_or_path

        # Search by filename
        search_name = Path(name_or_path).name.lower()
        for img in image_files:
            if Path(img).name.lower() == search_name:
                return img

        # Partial match
        for img in image_files:
            if search_name in Path(img).name.lower():
                return img

        return None

    # ── Caption generation ──

    def _generate_captions(
        self, slots: List[ImageSlot], context: PipelineContext
    ) -> None:
        """Generate captions for each slot based on project data."""
        for slot in slots:
            if not slot.matched_image:
                continue

            # Replace template variables
            caption = slot.caption_template

            # {num} → sequential figure number
            caption = caption.replace("{num}", "X")  # Will be filled at insertion time

            # {project_name} → project name
            if "{project_name}" in caption or "{{project_name}}" in caption:
                name = context.project_name or context.doc_reference or ""
                caption = caption.replace("{project_name}", name)
                caption = caption.replace("{{project_name}}", name)

            # {caption_text} → derived from image filename and slot context
            if "{caption_text}" in caption or "{{caption_text}}" in caption:
                img_name = Path(slot.matched_image).stem if slot.matched_image else ""
                img_folder = Path(slot.matched_image).parent.name if slot.matched_image else ""
                derived = self._derive_caption_text(img_folder, img_name, slot)
                caption = caption.replace("{caption_text}", derived)
                caption = caption.replace("{{caption_text}}", derived)

            # Update slot
            slot.caption_template = caption

    @staticmethod
    def _derive_caption_text(
        folder: str, filename: str, slot: ImageSlot
    ) -> str:
        """Derive appropriate caption text from image context."""
        # Remove common prefixes
        name = re.sub(r'^(IMG_|DSC_|P\d+_|微信图片_|mmexport)', '', filename)
        name = re.sub(r'[_\-\d]{8,}$', '', name)  # Remove trailing numbers

        stype = slot.suggested_type

        if stype == "location_map":
            return "项目地理位置图"
        elif stype == "notice":
            return "决策公示照片"
        elif stype == "meeting":
            return "群众座谈会现场照片"
        elif stype == "site_photo":
            return "项目现场照片"
        elif stype == "expert_review":
            return "专家评审会照片"
        elif stype == "survey":
            return "问卷调查扫描件"
        elif stype == "attachment":
            # Use the attachment item text
            return slot.caption_template[:30] if slot.caption_template else "附件材料"
        else:
            return f"{slot.section_title[:20]}相关图片"
