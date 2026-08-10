"""Template service — CRUD operations and analysis orchestration."""
import json
from typing import Optional, List
from datetime import datetime

from fastapi import UploadFile, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge import Template, PlaceholderDefinition
from app.schemas.knowledge import TemplateCreate, TemplateUpdate
from app.services.file_service import file_service


class TemplateService:
    """Handles template CRUD and triggers AI analysis."""

    @staticmethod
    async def create_template(
        db: AsyncSession,
        data: TemplateCreate,
        template_file: UploadFile,
        example_file: Optional[UploadFile] = None,
    ) -> Template:
        """Upload and create a new template."""
        # Save template file
        template_path, _ = await file_service.save_upload(
            template_file, file_service.TEMPLATES
        )

        # Save example file if provided
        example_path = None
        if example_file and example_file.filename:
            example_path, _ = await file_service.save_upload(
                example_file, file_service.EXAMPLES
            )

        # Get file size
        file_size = file_service.get_file_size(template_path)

        # Create DB record
        template = Template(
            name=data.name,
            description=data.description or "",
            category=data.category,
            domain=getattr(data, "domain", "stability") or "stability",
            template_file_path=template_path,
            example_file_path=example_path,
            analysis_status="pending",
            file_size=file_size,
        )
        db.add(template)
        await db.flush()
        await db.refresh(template)

        return template

    @staticmethod
    async def get_templates(
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        category: Optional[str] = None,
        search: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> tuple[List[Template], int]:
        """List templates with pagination and filtering.

        Excludes category="格式规范" — those are spec documents used for
        internal format validation, not user-selectable templates.
        """
        query = select(Template).where(
            Template.is_active == True,
            Template.category != "格式规范",
        )

        if category:
            query = query.where(Template.category == category)
        if domain:
            query = query.where(Template.domain == domain)
        if search:
            query = query.where(
                or_(
                    Template.name.contains(search),
                    Template.description.contains(search),
                )
            )

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # Paginate
        query = query.order_by(Template.updated_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        templates = list(result.scalars().all())

        return templates, total

    @staticmethod
    async def get_template(db: AsyncSession, template_id: int) -> Optional[Template]:
        """Get a single template by ID with placeholders loaded."""
        query = (
            select(Template)
            .where(Template.id == template_id, Template.is_active == True)
            .options(selectinload(Template.placeholders))
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def update_template(
        db: AsyncSession, template_id: int, data: TemplateUpdate
    ) -> Optional[Template]:
        """Update template metadata."""
        template = await TemplateService.get_template(db, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(template, key, value)

        template.updated_at = datetime.now()
        await db.flush()
        await db.refresh(template)
        return template

    @staticmethod
    async def delete_template(db: AsyncSession, template_id: int) -> bool:
        """Soft-delete a template and remove its files."""
        template = await TemplateService.get_template(db, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")

        # Delete files
        if template.template_file_path:
            file_service.delete_file(template.template_file_path)
        if template.example_file_path:
            file_service.delete_file(template.example_file_path)

        # Soft delete from DB
        template.is_active = False
        template.updated_at = datetime.now()
        await db.flush()
        return True

    @staticmethod
    async def save_analysis_results(
        db: AsyncSession,
        template_id: int,
        sections: list,
        placeholders: list,
    ) -> Template:
        """Save AI analysis results: sections and placeholder definitions."""
        template = await TemplateService.get_template(db, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")

        # Store JSON snapshots
        template.sections_json = json.dumps(sections, ensure_ascii=False)
        template.placeholders_json = json.dumps(placeholders, ensure_ascii=False)
        template.analysis_status = "completed"
        template.updated_at = datetime.now()

        # Delete old placeholder records before inserting new ones
        from sqlalchemy import delete
        from app.models.knowledge import PlaceholderDefinition
        await db.execute(
            delete(PlaceholderDefinition).where(
                PlaceholderDefinition.template_id == template_id
            )
        )
        await db.flush()

        # Create placeholder records
        for p in placeholders:
            placeholder = PlaceholderDefinition(
                template_id=template_id,
                placeholder_key=p.get("key", p.get("placeholder_key", "")),
                display_name=p.get("display_name", ""),
                section_index=p.get("section_index"),
                section_title=p.get("section_title"),
                paragraph_index=p.get("paragraph_index"),
                run_index=p.get("run_index"),
                expected_type=p.get("expected_type", "text"),
                expected_format=p.get("expected_format"),
                options_json=json.dumps(p.get("options", []), ensure_ascii=False) if p.get("options") else None,
                description=p.get("description", ""),
                is_required=p.get("is_required", True),
                default_value=p.get("default_value", "需后期提供"),
                sort_order=p.get("sort_order", 0),
            )
            db.add(placeholder)

        await db.flush()
        return template

    @staticmethod
    async def get_categories(db: AsyncSession) -> List[str]:
        """Get distinct template categories."""
        query = select(Template.category).where(Template.is_active == True).distinct()
        result = await db.execute(query)
        return [row[0] for row in result.all()]

    @staticmethod
    async def get_placeholders(
        db: AsyncSession, template_id: int
    ) -> List[PlaceholderDefinition]:
        """Get all placeholders for a template, sorted by section and order."""
        query = (
            select(PlaceholderDefinition)
            .where(PlaceholderDefinition.template_id == template_id)
            .order_by(
                PlaceholderDefinition.section_index,
                PlaceholderDefinition.sort_order,
            )
        )
        result = await db.execute(query)
        return list(result.scalars().all())


template_service = TemplateService()
