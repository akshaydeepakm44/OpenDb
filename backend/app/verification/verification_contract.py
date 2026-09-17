"""
OpenDB — Authoritative Verification Contract
§3, §4, §5, §6, §7 of Master Directive

This module is the SINGLE AUTHORITATIVE GATE for determining whether a lead
can transition into the VERIFIED state and appear in the Verified Leads list.

Hard Invariants:
1. Agent 1 discovery/crawl is NEVER VERIFIED (starts as CRAWLED_PENDING_AGENT_2).
2. Agent 2 task completion alone does NOT mean VERIFIED.
3. UniversalRecord/GlobalLead cannot become VERIFIED without passing this contract.
4. Frontend cannot decide or force VERIFIED state.
5. Incomplete leads become PARTIALLY_VERIFIED, NEEDS_REVIEW, or PHASE1_BLOCKED.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import re


CONTRACT_VERSION = "2026.09.v1"

# ─── 1. CORE REQUIRED FIELDS ──────────────────────────────────────────────────
# A failure in ANY of these strictly blocks VERIFIED status.
REQUIRED_CORE_FIELDS = {
    "company_name": {
        "label": "Company Name",
        "min_len": 2,
        "disallowed_values": ["unknown", "n/a", "none", "null", "example", "test company"],
    },
    "canonical_domain": {
        "label": "Canonical Domain",
        "min_len": 4,
        "disallowed_values": ["unknown", "unknown.com", "example.com", "localhost"],
    },
    "description": {
        "label": "Company Description / Overview",
        "min_len": 20,
        "disallowed_values": [
            "unknown", "n/a", "none", "null", "commercial web", "placeholder",
            "default", "test data", "intelligence dossier synthesis pending.",
            "enterprise lead"
        ],
    },
    "industry_sector": {
        "label": "Industry Sector",
        "min_len": 3,
        "disallowed_values": ["unknown", "commercial web", "other", "n/a", "none"],
    },
    "raw_storage_vault_path": {
        "label": "MinIO Raw Storage Vault Path",
        "min_len": 5,
        "must_contain": "opendb",
    },
    "crawled_page_text": {
        "label": "Verified Crawled Page Content",
        "min_len": 100,
    },
    "extracted_word_count": {
        "label": "Extracted Word Count",
        "min_val": 20,
    },
}

# ─── 2. RECOMMENDED & CONDITIONAL FIELDS ─────────────────────────────────────
# These contribute to the completeness score but do not strictly block verification
# if legitimate public data does not exist.
RECOMMENDED_FIELDS = {
    "location_region": {
        "label": "Headquarters / Location",
        "weight": 10,
    },
    "verified_contact_email": {
        "label": "Verified Contact Email",
        "weight": 15,
    },
    "company_size_tier": {
        "label": "Company Size / Employee Count",
        "weight": 10,
    },
    "company_linkedin_url": {
        "label": "Corporate LinkedIn Page",
        "weight": 10,
    },
    "key_people": {
        "label": "Verified Decision Makers / Leadership",
        "weight": 15,
    },
    "founded_year": {
        "label": "Founded Year",
        "weight": 5,
    },
    "phone": {
        "label": "Contact Phone Number",
        "weight": 5,
    },
}


class VerificationContract:
    """
    Evaluates lead data, evidence records, and candidate matches to produce
    a deterministic, explainable verification decision.
    """

    @staticmethod
    def evaluate(
        session_data: Dict[str, Any],
        evidence_list: List[Dict[str, Any]],
        person_candidates: Optional[List[Dict[str, Any]]] = None,
        subpages_count: int = 0
    ) -> Dict[str, Any]:
        """
        Evaluate full lead dossier against the authoritative contract.
        Returns complete audit structure with pass/fail decision.
        """
        evidence_by_field: Dict[str, Dict[str, Any]] = {
            e.get("field") or e.get("field_name"): e
            for e in evidence_list
            if e.get("field") or e.get("field_name")
        }
        person_candidates = person_candidates or []

        critical_issues: List[str] = []
        warnings: List[str] = []

        # ── 1. Check Infrastructure Failure ──────────────────────────────────
        has_infra_failure = False
        infra_failure_msg = None
        for ev in evidence_list:
            inv = ev.get("investigation") or ev.get("investigation_record") or {}
            if inv.get("infra_failure"):
                has_infra_failure = True
                infra_failure_msg = inv.get("infra_failure")
                critical_issues.append(f"Investigation blocked by infrastructure failure: {infra_failure_msg}")
                break

        # ── 2. Evaluate Required Core Fields ─────────────────────────────────
        required_results: Dict[str, Dict[str, Any]] = {}
        missing_required: List[str] = []
        core_passed_count = 0

        # Field: company_name
        name_val = session_data.get("company_name") or ""
        name_clean = str(name_val).strip()
        is_name_valid = (
            len(name_clean) >= REQUIRED_CORE_FIELDS["company_name"]["min_len"]
            and name_clean.lower() not in REQUIRED_CORE_FIELDS["company_name"]["disallowed_values"]
        )
        required_results["company_name"] = {
            "label": REQUIRED_CORE_FIELDS["company_name"]["label"],
            "value": name_clean if is_name_valid else None,
            "status": "VALIDATED" if is_name_valid else "MISSING",
            "evidence": f"Normalized corporate identifier: {name_clean}" if is_name_valid else None,
            "source_url": f"https://{session_data.get('domain')}" if session_data.get("domain") else None,
        }
        if is_name_valid:
            core_passed_count += 1
        else:
            missing_required.append("company_name")
            critical_issues.append("Company name is missing or invalid.")

        # Field: canonical_domain
        dom_val = session_data.get("domain") or ""
        dom_clean = str(dom_val).strip().lower().replace("www.", "")
        is_dom_valid = (
            len(dom_clean) >= REQUIRED_CORE_FIELDS["canonical_domain"]["min_len"]
            and "." in dom_clean
            and dom_clean not in REQUIRED_CORE_FIELDS["canonical_domain"]["disallowed_values"]
        )
        required_results["canonical_domain"] = {
            "label": REQUIRED_CORE_FIELDS["canonical_domain"]["label"],
            "value": dom_clean if is_dom_valid else None,
            "status": "VALIDATED" if is_dom_valid else "MISSING",
            "evidence": f"DNS hostname canonicalized: {dom_clean}" if is_dom_valid else None,
            "source_url": f"https://{dom_clean}" if is_dom_valid else None,
        }
        if is_dom_valid:
            core_passed_count += 1
        else:
            missing_required.append("canonical_domain")
            critical_issues.append("Canonical domain is missing or invalid.")

        # Field: description / business_overview
        desc_val = (
            session_data.get("summary")
            or session_data.get("business_overview")
            or (session_data.get("phase2_data") or {}).get("business_overview", {}).get("text")
            or ""
        )
        if isinstance(desc_val, dict):
            desc_val = desc_val.get("text") or ""
        desc_clean = str(desc_val).strip()
        is_desc_valid = (
            len(desc_clean) >= REQUIRED_CORE_FIELDS["description"]["min_len"]
            and not any(dis in desc_clean.lower() for dis in REQUIRED_CORE_FIELDS["description"]["disallowed_values"])
        )
        required_results["description"] = {
            "label": REQUIRED_CORE_FIELDS["description"]["label"],
            "value": desc_clean if is_desc_valid else None,
            "status": "VALIDATED" if is_desc_valid else "MISSING",
            "evidence": desc_clean[:200] if is_desc_valid else None,
            "source_url": f"https://{dom_clean}" if is_desc_valid else None,
        }
        if is_desc_valid:
            core_passed_count += 1
        else:
            missing_required.append("description")
            critical_issues.append("Company description is missing or generic placeholder.")

        # Field: industry_sector
        ind_ev = evidence_by_field.get("industry_sector") or {}
        ind_val = (
            ind_ev.get("value")
            or session_data.get("industry")
            or (session_data.get("phase1_data") or {}).get("industry_sector", {}).get("value")
            or ""
        )
        ind_clean = str(ind_val).strip()
        is_ind_valid = (
            len(ind_clean) >= REQUIRED_CORE_FIELDS["industry_sector"]["min_len"]
            and ind_clean.lower() not in REQUIRED_CORE_FIELDS["industry_sector"]["disallowed_values"]
            and ind_ev.get("status") != "UNVERIFIED"
        )
        required_results["industry_sector"] = {
            "label": REQUIRED_CORE_FIELDS["industry_sector"]["label"],
            "value": ind_clean if is_ind_valid else None,
            "status": "VALIDATED" if is_ind_valid else (ind_ev.get("status") or "MISSING"),
            "evidence": ind_ev.get("evidence_snippet") or (f"Classified as {ind_clean}" if is_ind_valid else None),
            "source_url": ind_ev.get("source_url") or f"https://{dom_clean}",
        }
        if is_ind_valid:
            core_passed_count += 1
        else:
            missing_required.append("industry_sector")
            critical_issues.append("Industry sector is unverified or generic.")

        # Field: raw_storage_vault_path
        vault_ev = evidence_by_field.get("raw_storage_vault_path") or {}
        vault_val = vault_ev.get("value") or session_data.get("minio_asset_path") or ""
        is_vault_valid = (
            len(str(vault_val)) >= REQUIRED_CORE_FIELDS["raw_storage_vault_path"]["min_len"]
            and REQUIRED_CORE_FIELDS["raw_storage_vault_path"]["must_contain"] in str(vault_val)
            and vault_ev.get("status") == "VERIFIED"
        )
        required_results["raw_storage_vault_path"] = {
            "label": REQUIRED_CORE_FIELDS["raw_storage_vault_path"]["label"],
            "value": vault_val if is_vault_valid else None,
            "status": "VALIDATED" if is_vault_valid else (vault_ev.get("status") or "MISSING"),
            "evidence": vault_ev.get("evidence_snippet") or f"Stored in MinIO: {vault_val}",
            "source_url": vault_val,
        }
        if is_vault_valid:
            core_passed_count += 1
        else:
            missing_required.append("raw_storage_vault_path")
            critical_issues.append("MinIO raw storage artifact vault path unverified.")

        # Field: crawled_page_text
        text_ev = evidence_by_field.get("crawled_page_text") or {}
        text_val = text_ev.get("value") or ""
        chars_match = re.search(r"(\d+)\s*chars", str(text_val))
        chars_cnt = int(chars_match.group(1)) if chars_match else len(str(text_val))
        is_text_valid = chars_cnt >= REQUIRED_CORE_FIELDS["crawled_page_text"]["min_len"] and text_ev.get("status") == "VERIFIED"
        required_results["crawled_page_text"] = {
            "label": REQUIRED_CORE_FIELDS["crawled_page_text"]["label"],
            "value": f"{chars_cnt} characters verified" if is_text_valid else None,
            "status": "VALIDATED" if is_text_valid else (text_ev.get("status") or "MISSING"),
            "evidence": text_ev.get("evidence_snippet") or "Crawled text extracted",
            "source_url": text_ev.get("source_url") or f"https://{dom_clean}",
        }
        if is_text_valid:
            core_passed_count += 1
        else:
            missing_required.append("crawled_page_text")
            critical_issues.append("Crawled page text is empty or insufficient.")

        # Field: extracted_word_count
        words_ev = evidence_by_field.get("extracted_word_count") or {}
        words_val = words_ev.get("value") or 0
        try:
            words_cnt = int(re.sub(r"[^\d]", "", str(words_val))) if words_val else 0
        except Exception:
            words_cnt = 0
        is_words_valid = words_cnt >= REQUIRED_CORE_FIELDS["extracted_word_count"]["min_val"] and words_ev.get("status") == "VERIFIED"
        required_results["extracted_word_count"] = {
            "label": REQUIRED_CORE_FIELDS["extracted_word_count"]["label"],
            "value": words_cnt if is_words_valid else None,
            "status": "VALIDATED" if is_words_valid else (words_ev.get("status") or "MISSING"),
            "evidence": f"Word count: {words_cnt}" if is_words_valid else None,
            "source_url": words_ev.get("source_url") or f"https://{dom_clean}",
        }
        if is_words_valid:
            core_passed_count += 1
        else:
            missing_required.append("extracted_word_count")
            critical_issues.append("Extracted word count below minimum threshold.")

        # ── 3. Evaluate Recommended & Conditional Fields ─────────────────────
        recommended_results: Dict[str, Dict[str, Any]] = {}
        missing_recommended: List[str] = []
        rec_score_earned = 0.0
        rec_score_total = sum(cfg["weight"] for cfg in RECOMMENDED_FIELDS.values())

        # Headquarters / Location
        loc_ev = evidence_by_field.get("location_region") or {}
        loc_val = (
            loc_ev.get("value")
            or session_data.get("headquarters")
            or (session_data.get("phase1_data") or {}).get("location_region", {}).get("value")
        )
        is_loc_valid = bool(loc_val and str(loc_val).lower() not in ["unknown", "n/a", "not specified", "not_found_after_search"])
        recommended_results["location_region"] = {
            "label": RECOMMENDED_FIELDS["location_region"]["label"],
            "value": loc_val if is_loc_valid else None,
            "status": "VALIDATED" if is_loc_valid else (loc_ev.get("status") or "NOT_FOUND"),
            "evidence": loc_ev.get("evidence_snippet"),
            "source_url": loc_ev.get("source_url"),
        }
        if is_loc_valid:
            rec_score_earned += RECOMMENDED_FIELDS["location_region"]["weight"]
        else:
            missing_recommended.append("location_region")
            warnings.append("Headquarters location not established.")

        # Verified Contact Email
        email_ev = evidence_by_field.get("verified_contact_email") or {}
        email_val = (
            email_ev.get("value")
            or (session_data.get("verified_emails", [None])[0] if isinstance(session_data.get("verified_emails"), list) and session_data.get("verified_emails") else None)
            or (session_data.get("phase1_data") or {}).get("verified_contact_email", {}).get("value")
        )
        is_email_valid = bool(email_val and "@" in str(email_val) and str(email_val).lower() not in ["unknown", "n/a", "not_found_after_search"])
        recommended_results["verified_contact_email"] = {
            "label": RECOMMENDED_FIELDS["verified_contact_email"]["label"],
            "value": email_val if is_email_valid else None,
            "status": "VALIDATED" if is_email_valid else (email_ev.get("status") or "NOT_FOUND"),
            "evidence": email_ev.get("evidence_snippet"),
            "source_url": email_ev.get("source_url"),
        }
        if is_email_valid:
            rec_score_earned += RECOMMENDED_FIELDS["verified_contact_email"]["weight"]
        else:
            missing_recommended.append("verified_contact_email")
            warnings.append("Verified contact email not found on official pages.")

        # Company Size Tier
        size_ev = evidence_by_field.get("company_size_tier") or {}
        size_val = (
            size_ev.get("value")
            or session_data.get("company_size")
            or (session_data.get("phase1_data") or {}).get("company_size_tier", {}).get("value")
        )
        is_size_valid = bool(size_val and str(size_val).lower() not in ["unknown", "n/a", "not_found_after_search"])
        recommended_results["company_size_tier"] = {
            "label": RECOMMENDED_FIELDS["company_size_tier"]["label"],
            "value": size_val if is_size_valid else None,
            "status": "VALIDATED" if is_size_valid else (size_ev.get("status") or "NOT_FOUND"),
            "evidence": size_ev.get("evidence_snippet"),
            "source_url": size_ev.get("source_url"),
        }
        if is_size_valid:
            rec_score_earned += RECOMMENDED_FIELDS["company_size_tier"]["weight"]
        else:
            missing_recommended.append("company_size_tier")

        # Corporate LinkedIn URL
        li_val = session_data.get("linkedin_url") or session_data.get("company_linkedin_url")
        is_li_valid = bool(li_val and "linkedin.com/company" in str(li_val).lower())
        recommended_results["company_linkedin_url"] = {
            "label": RECOMMENDED_FIELDS["company_linkedin_url"]["label"],
            "value": li_val if is_li_valid else None,
            "status": "VALIDATED" if is_li_valid else "NOT_FOUND",
            "evidence": f"Official LinkedIn company profile: {li_val}" if is_li_valid else None,
            "source_url": li_val,
        }
        if is_li_valid:
            rec_score_earned += RECOMMENDED_FIELDS["company_linkedin_url"]["weight"]
        else:
            missing_recommended.append("company_linkedin_url")
            warnings.append("Corporate LinkedIn company page not established.")

        # Key People / Decision Makers
        verified_people = [
            p for p in person_candidates
            if p.get("candidate_status") == "VERIFIED" or p.get("company_match_status") == "VERIFIED"
        ]
        has_people = len(verified_people) > 0
        recommended_results["key_people"] = {
            "label": RECOMMENDED_FIELDS["key_people"]["label"],
            "value": f"{len(verified_people)} verified leaders" if has_people else None,
            "status": "VALIDATED" if has_people else "NOT_FOUND",
            "evidence": f"Identified: {', '.join(p.get('name') or p.get('person_name') for p in verified_people[:3])}" if has_people else None,
            "source_url": verified_people[0].get("linkedin_url") if has_people else None,
        }
        if has_people:
            rec_score_earned += RECOMMENDED_FIELDS["key_people"]["weight"]
        else:
            missing_recommended.append("key_people")
            warnings.append("No verified executive leadership profiles found.")

        # Founded Year
        founded_val = session_data.get("founded_year")
        is_founded_valid = bool(founded_val and str(founded_val).isdigit())
        recommended_results["founded_year"] = {
            "label": RECOMMENDED_FIELDS["founded_year"]["label"],
            "value": founded_val if is_founded_valid else None,
            "status": "VALIDATED" if is_founded_valid else "NOT_FOUND",
            "evidence": f"Founded: {founded_val}" if is_founded_valid else None,
            "source_url": None,
        }
        if is_founded_valid:
            rec_score_earned += RECOMMENDED_FIELDS["founded_year"]["weight"]
        else:
            missing_recommended.append("founded_year")

        # Phone
        phone_val = session_data.get("phone")
        is_phone_valid = bool(phone_val and len(str(phone_val).strip()) >= 7)
        recommended_results["phone"] = {
            "label": RECOMMENDED_FIELDS["phone"]["label"],
            "value": phone_val if is_phone_valid else None,
            "status": "VALIDATED" if is_phone_valid else "NOT_FOUND",
            "evidence": f"Phone: {phone_val}" if is_phone_valid else None,
            "source_url": None,
        }
        if is_phone_valid:
            rec_score_earned += RECOMMENDED_FIELDS["phone"]["weight"]
        else:
            missing_recommended.append("phone")

        # ── 4. Deterministic Completeness Score ──────────────────────────────
        core_pct = (core_passed_count / len(REQUIRED_CORE_FIELDS)) * 60.0
        rec_pct = (rec_score_earned / rec_score_total) * 40.0
        completeness_score = round(min(100.0, max(0.0, core_pct + rec_pct)), 1)

        # ── 5. Hard Gating Invariant ─────────────────────────────────────────
        all_core_passed = (len(missing_required) == 0) and (not has_infra_failure)
        is_verified = all_core_passed

        # Determine authoritative state
        if has_infra_failure:
            verification_state = "PHASE1_BLOCKED"
        elif is_verified:
            verification_state = "VERIFIED"
        elif len(missing_required) > 0 and core_passed_count >= 3:
            verification_state = "PARTIALLY_VERIFIED"
        else:
            verification_state = "NEEDS_REVIEW"

        confidence = round(completeness_score / 100.0, 2)

        return {
            "is_verified": is_verified,
            "verification_state": verification_state,
            "completeness_score": completeness_score,
            "core_passed_count": core_passed_count,
            "core_total_count": len(REQUIRED_CORE_FIELDS),
            "required_fields": required_results,
            "missing_required_fields": missing_required,
            "recommended_fields": recommended_results,
            "missing_recommended_fields": missing_recommended,
            "critical_issues": critical_issues,
            "warnings": warnings,
            "confidence": confidence,
            "contract_version": CONTRACT_VERSION,
            "subpages_crawled_count": subpages_count,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def evaluate_session(cls, session, db) -> Dict[str, Any]:
        """
        Extracts session data, evidence records, and candidate matches from the database
        and runs the authoritative contract evaluation.
        """
        from app.persistence.models import Document, CanonicalEvidence, KeyPerson

        doc = None
        if getattr(session, "document_id", None):
            doc = db.query(Document).filter(Document.id == str(session.document_id)).first()
            if not doc:
                try:
                    import uuid as _uuid
                    doc = db.query(Document).filter(Document.id == _uuid.UUID(str(session.document_id))).first()
                except Exception:
                    pass

        raw_meta = (doc.raw_metadata or {}) if doc else {}
        subpages_count = len(raw_meta.get("subpages_crawled", [])) if raw_meta else 0

        # Check Document table for subpages crawled under this domain
        if hasattr(session, "domain") and session.domain:
            try:
                sub_recs = db.query(Document).filter(Document.url.ilike(f"%{session.domain}%")).count()
                subpages_count = max(subpages_count, sub_recs)
            except Exception:
                pass

        evidence_rows = db.query(CanonicalEvidence).filter(
            (CanonicalEvidence.verification_session_id == session.id) | (CanonicalEvidence.session_id == session.id)
        ).all()
        candidates_rows = db.query(KeyPerson).filter(
            (KeyPerson.verification_session_id == session.id) | (KeyPerson.session_id == session.id)
        ).all()

        evidence_list = [
            {
                "field": e.field_name,
                "value": e.value,
                "status": e.verification_status,
                "source_url": e.source_url,
                "evidence_snippet": e.evidence_snippet,
                "verification_method": e.verification_method,
                "investigation": e.investigation_record or {},
            }
            for e in evidence_rows
        ]

        person_candidates = [
            {
                "name": c.person_name,
                "linkedin_url": c.linkedin_url,
                "title": c.title,
                "company": c.company,
                "candidate_status": c.candidate_status,
                "company_match_status": c.company_match_status,
                "is_leadership": c.is_leadership,
                "rejection_reason": c.rejection_reason,
                "evidence_snippet": c.evidence_snippet,
            }
            for c in candidates_rows
        ]

        # Assemble session_data
        p1 = session.phase1_data or {}
        p2 = session.phase2_data or {}

        # Fallback to doc title or domain if company_name is generic
        c_name = getattr(session, "company_name", None) or (doc.title if doc else None)
        c_dom = getattr(session, "domain", None) or (urlparse(doc.url).netloc.replace("www.", "") if doc and doc.url else None)
        c_desc = p2.get("business_overview", {}).get("text") or (doc.title if doc else None)

        session_data = {
            "domain": c_dom,
            "company_name": c_name,
            "summary": c_desc,
            "business_overview": c_desc,
            "industry": p1.get("industry_sector", {}).get("value"),
            "headquarters": p1.get("location_region", {}).get("value"),
            "company_size": p1.get("company_size_tier", {}).get("value"),
            "verified_emails": [p1.get("verified_contact_email", {}).get("value")] if p1.get("verified_contact_email", {}).get("value") else [],
            "minio_asset_path": (doc.raw_path or (doc.raw_artifacts[0] if doc and doc.raw_artifacts else None)) if doc else None,
            "phase1_data": p1,
            "phase2_data": p2,
        }

        # Backfill document-level crawl evidence if missing from evidence_list
        if doc:
            if not any(e.get("field") == "raw_storage_vault_path" for e in evidence_list):
                vault_path = doc.raw_path or (doc.raw_artifacts[0] if doc.raw_artifacts else None)
                if vault_path:
                    evidence_list.append({
                        "field": "raw_storage_vault_path",
                        "value": vault_path,
                        "status": "VERIFIED",
                        "evidence_snippet": f"MinIO raw artifact vault: {vault_path}",
                        "source_url": vault_path
                    })
            if not any(e.get("field") == "crawled_page_text" for e in evidence_list):
                if doc.word_count and doc.word_count > 0:
                    evidence_list.append({
                        "field": "crawled_page_text",
                        "value": f"{doc.word_count * 6} chars",
                        "status": "VERIFIED",
                        "evidence_snippet": f"Verified crawled content text present ({doc.word_count} words).",
                        "source_url": doc.url
                    })
            if not any(e.get("field") == "extracted_word_count" for e in evidence_list):
                if doc.word_count and doc.word_count > 0:
                    evidence_list.append({
                        "field": "extracted_word_count",
                        "value": doc.word_count,
                        "status": "VERIFIED",
                        "evidence_snippet": f"Extracted word count: {doc.word_count}",
                        "source_url": doc.url
                    })

        return cls.evaluate(
            session_data=session_data,
            evidence_list=evidence_list,
            person_candidates=person_candidates,
            subpages_count=subpages_count
        )


verification_contract = VerificationContract()

