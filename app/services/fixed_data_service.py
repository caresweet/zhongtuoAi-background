"""Domain-aware fixed data retrieval service.

Queries the knowledge base for company-specific fixed data (business licenses,
personnel certificates, company profile) based on the selected report domain.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COMPANY_IMAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "extracted_imgs"


async def get_fixed_data_for_domain(domain_id: str) -> Dict[str, Any]:
    """Retrieve fixed company data from the KB for a given report domain.

    Returns:
        {
            "company_name": str,
            "assets": [{"asset_type": ..., "title": ..., "content": ...}, ...],
            "images": [{"filename": ..., "path": ...}, ...],
            "kb_docs": [{"document": ..., "metadata": ...}, ...],
        }
    """
    from app.domains.registry import get_domain

    domain_cfg = get_domain(domain_id)
    company_name = domain_cfg.company_name
    if not company_name:
        return {"company_name": "", "assets": [], "images": [], "kb_docs": []}

    assets = await _load_company_assets(company_name)
    images = _scan_company_images()
    kb_docs = await _query_kb_company_info(domain_cfg.rag_domain)

    return {
        "company_name": company_name,
        "assets": assets,
        "images": images,
        "kb_docs": kb_docs,
    }


async def _load_company_assets(company_name: str) -> List[Dict[str, Any]]:
    """Load CompanyAsset records from the database."""
    try:
        from app.services.bidding_asset_extractor import get_assets
        return await get_assets(company=company_name)
    except Exception as e:
        logger.warning("CompanyAsset query failed (company=%s): %s", company_name, e)
        return []


def _scan_company_images() -> List[Dict[str, str]]:
    """Scan storage/extracted_imgs/ for company image files."""
    if not COMPANY_IMAGE_DIR.is_dir():
        return []
    results = []
    for p in sorted(COMPANY_IMAGE_DIR.iterdir()):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".bmp"):
            results.append({"filename": p.name, "path": str(p)})
    return results


async def _query_kb_company_info(rag_domain: str) -> List[Dict[str, Any]]:
    """Query the RAG collection for company_info documents in this domain."""
    try:
        from app.rag.rag_service import rag_service
        items = await rag_service.retrieve_with_query(
            query="公司营业执照 人员资质证书 稳评平台备案 公司简介",
            session_id="__fixed_data__",
            n_results=5,
            domain=rag_domain,
        )
        return [{"document": it.get("document", ""), "metadata": it.get("metadata", {})} for it in (items or [])]
    except Exception as e:
        logger.warning("KB company_info query failed (domain=%s): %s", rag_domain, e)
        return []
