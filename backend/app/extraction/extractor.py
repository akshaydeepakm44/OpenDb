import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.extraction.css_extractor import css_extractor
from app.extraction.llm_extractor import llm_extractor
from app.classification.domain_classifier import domain_classifier
from app.schemas.registry import schema_registry
from app.normalization.normalizer import normalizer
from app.extraction.firmographics import (
    standardize_company_tier,
    extract_firmographic_size,
    extract_firmographic_location
)

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

        # 2. Domain & Industry Sector Classification
        domain_name, subdomain_name, class_confidence = domain_classifier.classify(
            text_content=text_content,
            title=title,
            url=url,
            user_domain=user_domain,
            schema_org_type=det_meta.get("schema_org_type")
        )

        # 3. Load Domain Schema (fallback to business or technology)
        schema_def = schema_registry.get_domain_schema(domain_name)
        if not schema_def:
            schema_def = schema_registry.get_domain_schema("business") or schema_registry.get_domain_schema("technology") or {"properties": {}}

        # 4. Domain-Specific Extraction (MODE 2)
        domain_data, evidence_list = await llm_extractor.extract_domain_data(
            text_content=text_content,
            domain_name=domain_name,
            schema_def=schema_def,
            page_url=url
        )

        # Merge deterministic emails into domain_data
        det_emails = det_meta.get("contact_emails") or []
        if det_emails:
            existing_emails = domain_data.get("contact_emails") or domain_data.get("emails") or []
            if isinstance(existing_emails, str):
                existing_emails = [existing_emails]
            merged_emails = list(dict.fromkeys(existing_emails + det_emails))
            domain_data["contact_emails"] = merged_emails

        # 4b. Multi-signal Location & Region Resolution
        loc_info = det_meta.get("location_info") or {}
        firmographic_loc = extract_firmographic_location(
            text=text_content,
            page_url=url,
            html_location_info=loc_info
        )

        resolved_hq = (
            domain_data.get("headquarters")
            or domain_data.get("location")
            or firmographic_loc.get("formatted")
        )
        resolved_country = (
            firmographic_loc.get("country")
            or normalizer.normalize_country(domain_data.get("country"))
            or "United States"
        )

        domain_data["headquarters"] = resolved_hq
        domain_data["location"] = resolved_hq
        domain_data["country"] = resolved_country

        # 4c. Multi-signal Company Size & Size Tier Resolution
        raw_size = (
            domain_data.get("company_size")
            or domain_data.get("employee_count")
            or det_meta.get("schema_employees")
        )
        if not raw_size or str(raw_size).upper() == "UNKNOWN":
            txt_size, txt_tier = extract_firmographic_size(text_content)
            if txt_size != "Unknown":
                raw_size = txt_size
                company_tier = txt_tier
            else:
                company_tier = "Unknown"
        else:
            company_tier = standardize_company_tier(raw_size)

        domain_data["company_size"] = raw_size if raw_size else "Unknown"
        domain_data["company_tier"] = company_tier

        # 4d. Industry Sector Resolution
        resolved_industry = (
            domain_data.get("industry")
            if domain_data.get("industry") and domain_data.get("industry").lower() not in ["unknown", "general", "none", f"{domain_name.lower()} industry"]
            else domain_name
        )
        domain_data["industry"] = resolved_industry

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
            "entity_type": "Organization",
            "source_id": None,
            "document_id": document_id,
            "language": det_meta.get("language") or "en",
            "country": resolved_country,
            "location": resolved_hq,
            "status": "Active",
            "confidence": float(class_confidence),
            "metadata_json": {
                "industry_sector": resolved_industry,
                "location_region": resolved_hq,
                "country": resolved_country,
                "company_size": raw_size if raw_size else "Unknown",
                "company_tier": company_tier
            },
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
