import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.material_ingestion_service import material_ingestion_service


async def main():
    artifacts = [
        {
            "source_path": "images/demo.png",
            "source_type": "image",
            "status": "completed",
            "retrieval_text": "图片提取到公告内容",
            "structured_data": {"location": "洪泽区", "responsible_unit": "街道办"},
        },
        {
            "source_path": "knowledge_docs/demo.docx",
            "source_type": "word",
            "status": "completed",
            "retrieval_text": "Word 提取到项目名称",
            "structured_data": {"project_name": "高铁枢纽北片区开发地块项目"},
        },
    ]

    facts = material_ingestion_service.merge_project_facts(artifacts)
    summary = material_ingestion_service.summarize_analysis(artifacts)

    assert facts["location"] == "洪泽区"
    assert facts["responsible_unit"] == "街道办"
    assert summary["total_files"] == 2
    assert summary["completed_files"] == 2
    assert summary["missing_fields"] == []
    print("material ingestion summary ok")


if __name__ == "__main__":
    asyncio.run(main())
