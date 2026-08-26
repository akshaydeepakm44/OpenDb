import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.extraction.css_extractor import css_extractor
from app.extraction.llm_extractor import llm_extractor
from app.classification.domain_classifier import domain_classifier
from app.schemas.registry import schema_registry
from app.normalization.normalizer import normalizer

logger = logging.getLogger(__name__)

class UnifiedExtractorPipeline:
    async def process_document_extraction(
        self,
        document_id: str,
        url: str,
        html_content: str,
        text_content: str,
        user_domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run complete extraction pipeline on page content."""

        # 1. Deterministic Extraction (MODE 1)
        det_meta = css_extractor.extract_deterministic_metadata(html_content, page_url=url)
        title = det_meta.get("title") or url
        description = det_meta.get("description")

        # 2. Domain Classification
        domain_name, subdomain_name, class_confidence = domain_classifier.classify(
            text_content=text_content,
            title=title,
            url=url,
            user_domain=user_domain
        )

        # 3. Load Domain Schema
        schema_def = schema_registry.get_domain_schema(domain_name)
        if not schema_def:
            schema_def = schema_registry.get_domain_schema("technology") or {"properties": {}}

        # 4. Domain-Specific Extraction (MODE 2)
        domain_data, evidence_list = await llm_extractor.extract_domain_data(
            text_content=text_content,
            domain_name=domain_name,
            schema_def=schema_def,
            page_url=url
        )

        # 5. Build Universal Data Schema Record
        canonical_name = (
            domain_data.get("company_name")
            or domain_data.get("organization_name")
            or domain_data.get("institution_name")
            or title
        )
        canonical_name = normalizer.normalize_string(canonical_name)

        universal_record = {
            "resource_id": document_id,
            "canonical_name": canonical_name,
            "title": title,
            "description": description,
            "url": url,
            "domain": domain_name,
            "subdomain": subdomain_name,
            "entity_type": "Organization" if domain_name in ["Technology", "Healthcare", "Education", "Business"] else "Resource",
            "source_id": None,
            "document_id": document_id,
            "language": det_meta.get("language") or "en",
            "country": normalizer.normalize_country("US"),
            "location": domain_data.get("headquarters") or domain_data.get("locations", [None])[0] if isinstance(domain_data.get("locations"), list) and domain_data.get("locations") else None,
            "status": "Active",
            "confidence": float(class_confidence),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        # Combine into complete OpenDB Extraction Payload
        payload = {
            "document_id": document_id,
            "source": {
                "url": url,
                "title": title,
                "retrieved_at": datetime.now(timezone.utc).isoformat()
            },
            "classification": {
                "domain": domain_name,
                "subdomain": subdomain_name,
                "confidence": float(class_confidence)
            },
            "universal": universal_record,
            "domain_data": domain_data,
            "evidence": evidence_list
        }

        return payload

extraction_pipeline = UnifiedExtractorPipeline()
