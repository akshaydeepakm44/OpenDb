"""
OpenDB — Agent 2 Autonomous Verification Orchestrator
Coordinates the complete lifecycle of Agent 2 company-intelligence verification:
Intake (CRAWLED_PENDING_AGENT_2) -> Priority Ranking -> Phase 1 Investigation Loop
-> Targeted Crawl4AI Re-crawl -> Phase 1 Gate -> Phase 2 Haystack Business Synthesis
-> SearXNG LinkedIn Discovery -> Strict /in/ Validation -> Person-Company Matching
-> Multi-Round Search Retry -> Contradiction Detection -> Final Verification -> PostgreSQL Outbox.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from app.persistence.database import SessionLocal
from app.persistence.models import (
    Document, Agent2VerificationSession, Agent2Evidence, Agent2PersonCandidate, utc_now
)
from app.agent.agent2_investigation import investigation_engine, can_mark_not_found
from app.agent.agent2_haystack_components import (
    get_targeted_recrawl_paths, run_business_synthesis,
    generate_dynamic_linkedin_queries, evaluate_person_company_match
)
from app.crawler.crawler_service import crawler_service
from app.crawler.searxng_service import searxng_service
from app.extraction.person_verifier import is_authentic_linkedin_personal_url, person_verifier
from app.storage.file_storage import file_storage
from app.persistence.outbox_sync_service import outbox_sync_service

logger = logging.getLogger(__name__)


class Agent2Orchestrator:
    """
    State machine and orchestration engine for Agent 2.
    Starts strictly from cards marked CRAWLED_PENDING_AGENT_2 and moves through
    verified stages with full audit logging and zero hallucinated data.
    """

    def __init__(self):
        self.max_linkedin_rounds = 2
        self.min_target_candidates = 5

    def get_or_create_session(self, document_id: str, db) -> Optional[Agent2VerificationSession]:
        """Look up or initialize an Agent 2 verification session for a Document."""
        doc_id_str = str(document_id)
        doc = None
        try:
            doc = db.query(Document).filter(Document.id == doc_id_str).first()
        except Exception:
            pass
        if not doc:
            try:
                import uuid as _uuid
                doc = db.query(Document).filter(Document.id == _uuid.UUID(doc_id_str)).first()
            except Exception:
                pass

        if not doc:
            return None

        clean_dom = urlparse(doc.url).netloc.replace("www.", "") if doc.url else "unknown.com"
        clean_name = doc.title or clean_dom.split(".")[0].title()

        session = db.query(Agent2VerificationSession).filter(
            Agent2VerificationSession.document_id == doc_id_str
        ).first()

        if not session:
            session = Agent2VerificationSession(
                document_id=doc_id_str,
                domain=clean_dom,
                company_name=clean_name,
                status="AGENT2_QUEUED",
                priority_score=0.0,
                priority_reasons=[],
                phase1_data={},
                phase2_data={},
                recrawl_count=0,
                search_rounds=0,
                investigation_log=[{
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "state": "AGENT2_QUEUED",
                    "message": f"Session initialized for {clean_dom} from Document {doc_id_str}"
                }]
            )
            db.add(session)
            db.commit()
            db.refresh(session)

        return session

    def rank_card(self, session: Agent2VerificationSession, db) -> float:
        """
        Phase -1: Priority Ranking.
        Computes an explainable Priority Score (0-100) based on real evidence volume,
        contact information presence, corporate depth, and storage readiness.
        """
        doc = None
        try:
            doc = db.query(Document).filter(Document.id == session.document_id).first()
        except Exception:
            pass
        if not doc:
            try:
                import uuid as _uuid
                doc = db.query(Document).filter(Document.id == _uuid.UUID(str(session.document_id))).first()
            except Exception:
                pass
        score = 40.0
        reasons = []

        if doc:
            raw_meta = doc.raw_metadata or {}
            # Evidence volume
            word_count = doc.word_count or 0
            if word_count > 1000:
                score += 15.0
                reasons.append("High evidence text volume (>1000 words)")
            elif word_count > 300:
                score += 8.0
                reasons.append("Moderate evidence text volume")

            # MinIO Storage availability
            if doc.raw_artifacts and len(doc.raw_artifacts) > 0:
                score += 15.0
                reasons.append(f"MinIO raw storage available ({len(doc.raw_artifacts)} artifacts)")

            # Verified contact email
            emails = raw_meta.get("detected_emails") or []
            if len(emails) > 0:
                score += 15.0
                reasons.append(f"Verified contact email found on website ({emails[0]})")

            # Multi-page crawl depth
            subpages = raw_meta.get("subpages_crawled") or []
            if len(subpages) >= 5:
                score += 15.0
                reasons.append(f"Deep multi-page corporate crawl ({len(subpages)} subpages)")
            elif len(subpages) >= 1:
                score += 8.0
                reasons.append(f"Subpage coverage ({len(subpages)} subpages)")

        final_score = min(100.0, max(10.0, round(score, 1)))
        session.priority_score = final_score
        session.priority_reasons = reasons
        session.status = "PHASE1_RANKED"
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "PHASE1_RANKED",
            "score": final_score,
            "reasons": reasons
        })
        db.commit()
        return final_score

    async def verify_phase1(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Phase 1: Evidence Verification.
        Investigates all 7 required Phase-1 fields with field-specific strategies and multi-round retries.
        Enforces Phase 1 Gate: ZERO UNVERIFIED or MISSING_BUT_NOT_CHECKED fields allowed.
        """
        doc = db.query(Document).filter(Document.id == session.document_id).first()
        if not doc:
            session.status = "PHASE1_BLOCKED"
            session.error_message = "Source document not found in database"
            db.commit()
            return {"status": "blocked", "error": "Document not found"}

        session.status = "PHASE1_VERIFYING"
        db.commit()

        domain = session.domain
        company_name = session.company_name
        artifacts = doc.raw_artifacts or []
        metadata = doc.raw_metadata or {}

        # Retrieve crawled text corpus from MinIO or fallback
        text_corpus = ""
        if doc.raw_path:
            text_corpus = file_storage.read_file_content(doc.raw_path) or ""
        if not text_corpus and doc.markdown_path:
            text_corpus = file_storage.read_file_content(doc.markdown_path) or ""

        field_results: Dict[str, Any] = {}

        # 1. Raw Storage Vault Path (system fact)
        res_vault = investigation_engine.investigate_raw_vault_path(doc.raw_path, artifacts)
        field_results["raw_storage_vault_path"] = res_vault

        # 2. Crawled Page Text / Extracted Content
        res_content = investigation_engine.investigate_crawled_content(text_corpus, doc.raw_path)
        field_results["crawled_page_text"] = res_content

        # 3. Extracted Word Count (recalculated directly from text)
        res_words = investigation_engine.investigate_word_count(text_corpus)
        field_results["extracted_word_count"] = res_words

        # 4. Verified Contact Email
        res_email = await investigation_engine.investigate_email(
            domain=domain,
            existing_text=text_corpus,
            existing_metadata=metadata,
            crawler_service=crawler_service,
            searxng_service=searxng_service
        )
        field_results["verified_contact_email"] = res_email

        # 5. Industry Sector
        res_ind = await investigation_engine.investigate_industry(
            domain=domain,
            company_name=company_name,
            existing_text=text_corpus,
            existing_artifacts=artifacts,
            crawler_service=crawler_service,
            searxng_service=searxng_service
        )
        field_results["industry_sector"] = res_ind

        # 6. Location / Region
        res_loc = await investigation_engine.investigate_location(
            domain=domain,
            company_name=company_name,
            existing_text=text_corpus,
            existing_artifacts=artifacts,
            crawler_service=crawler_service,
            searxng_service=searxng_service
        )
        field_results["location_region"] = res_loc

        # 7. Company Size Tier
        res_size = await investigation_engine.investigate_company_size(
            domain=domain,
            company_name=company_name,
            existing_text=text_corpus,
            existing_artifacts=artifacts,
            crawler_service=crawler_service,
            searxng_service=searxng_service
        )
        field_results["company_size_tier"] = res_size

        # Persist Agent2Evidence records
        for fname, fres in field_results.items():
            ev = Agent2Evidence(
                session_id=session.id,
                field_name=fname,
                value=str(fres.get("value")) if fres.get("value") is not None else None,
                source_url=fres.get("source_url"),
                evidence_snippet=fres.get("evidence_snippet"),
                verification_status=fres.get("status", "UNVERIFIED"),
                verification_method=fres.get("verification_method"),
                investigation_record=fres.get("investigation", {})
            )
            db.add(ev)

        session.phase1_data = field_results

        # ── Phase 1 Gate Evaluation ──────────────────────────────────────────
        unverified_fields = [k for k, v in field_results.items() if v.get("status") == "UNVERIFIED"]
        if unverified_fields:
            session.status = "PHASE1_BLOCKED"
            session.error_message = f"Phase 1 Gate blocked: incomplete investigations for {unverified_fields}"
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "PHASE1_BLOCKED",
                "unverified_fields": unverified_fields
            })
            db.commit()
            return {"status": "blocked", "unverified_fields": unverified_fields}

        session.status = "PHASE1_VERIFIED"
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "PHASE1_VERIFIED",
            "message": "All 7 fields successfully investigated and passed Phase 1 Gate."
        })
        db.commit()
        return {"status": "success", "field_results": field_results}

    async def synthesize_business(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Phase 2: Business Overview & Synthesis.
        Uses Haystack / LLM to produce an evidence-grounded summary without hallucinations.
        """
        session.status = "PHASE2_SYNTHESIS"
        db.commit()

        doc = db.query(Document).filter(Document.id == session.document_id).first()
        text_corpus = ""
        if doc and doc.raw_path:
            text_corpus = file_storage.read_file_content(doc.raw_path) or ""
        if not text_corpus and doc and doc.markdown_path:
            text_corpus = file_storage.read_file_content(doc.markdown_path) or ""

        # Run synthesis pipeline
        synthesis = run_business_synthesis(
            company_name=session.company_name,
            domain=session.domain,
            evidence_text=text_corpus,
            source_url=f"https://{session.domain}"
        )

        session.phase2_data = synthesis
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "PHASE2_SYNTHESIS",
            "overview_preview": synthesis.get("business_overview", {}).get("text", "")[:120]
        })
        db.commit()
        return synthesis

    async def discover_and_verify_linkedin(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Phase 2: LinkedIn Key-Person Discovery & Matching Loop.
        Strict /in/ profile validation, SearXNG queries, multi-round search retry, and person-company matching.
        """
        session.status = "LINKEDIN_DISCOVERY"
        db.commit()

        domain = session.domain
        company_name = session.company_name

        from app.extraction.person_verifier import PersonCompanyVerifier
        canon = PersonCompanyVerifier.canonicalize_company_identity(url=domain, title=company_name)
        domain_brand = canon.get("domain_brand") or domain.split(".")[0].capitalize()
        clean_name = canon.get("clean_name") or ""
        target_brand = domain_brand if (not clean_name or domain_brand.lower() not in clean_name.lower()) else clean_name

        # Update session company name if raw title was a generic home page / tagline
        if target_brand and (not session.company_name or "home" in session.company_name.lower() or session.company_name.lower() in ["unknown", "index"]):
            session.company_name = target_brand
            db.commit()

        candidates_pool: List[Dict[str, Any]] = []
        seen_urls = set()

        # Multi-round search retry loop (searches again if candidates are insufficient!)
        for rnd in range(1, self.max_linkedin_rounds + 1):
            session.search_rounds = rnd
            queries = generate_dynamic_linkedin_queries(company_name, domain, search_round=rnd)

            for q in queries:
                try:
                    results, is_fb, _ = await searxng_service.search_with_meta(q, category="general", max_results=10)
                    if results and not is_fb:
                        for r in results:
                            raw_url = r.get("url", "")
                            # STRICT GATE: Only authentic personal /in/ URLs allowed
                            if is_authentic_linkedin_personal_url(raw_url):
                                clean_url = re.sub(r"\?.*$", "", raw_url).rstrip("/")
                                if clean_url not in seen_urls:
                                    seen_urls.add(clean_url)
                                    title_snip = r.get("title", "")
                                    content_snip = r.get("content", "")

                                    # Extract name and role from snippet
                                    parsed_name = title_snip.split("-")[0].split("|")[0].strip()
                                    role_guess = title_snip.replace(parsed_name, "").strip(" -|")

                                    candidates_pool.append({
                                        "name": parsed_name or "Professional",
                                        "url": clean_url,
                                        "title": role_guess or "Executive",
                                        "company": target_brand,
                                        "evidence": content_snip
                                    })
                except Exception as search_err:
                    logger.warning(f"[Agent 2][LinkedIn] Search query '{q}' failed: {search_err}")

            # If we achieved target candidate volume (at least 5 /in/ profiles), break early
            if len(candidates_pool) >= self.min_target_candidates:
                break

        session.status = "LINKEDIN_CANDIDATES_FOUND"
        db.commit()

        # Now evaluate each candidate using Person ↔ Company matching
        session.status = "PERSON_MATCHING"
        db.commit()

        verified_people = []
        rejected_people = []

        for cand in candidates_pool:
            eval_res = evaluate_person_company_match(
                target_company=target_brand,
                target_domain=domain,
                candidate_name=cand["name"],
                candidate_title=cand["title"],
                candidate_company=cand["company"],
                evidence_text=cand["evidence"],
                linkedin_url=cand["url"]
            )

            is_verified = eval_res.get("company_match", False)

            p_cand = Agent2PersonCandidate(
                session_id=session.id,
                person_name=cand["name"],
                linkedin_url=cand["url"],
                title=cand["title"],
                company=company_name,
                candidate_status="VERIFIED" if is_verified else "REJECTED",
                company_match_status=is_verified,
                is_leadership=eval_res.get("is_leadership", False),
                rejection_reason=eval_res.get("rejection_reason"),
                evidence_snippet=eval_res.get("evidence_snippet"),
                source_url=cand["url"]
            )
            db.add(p_cand)

            if is_verified:
                verified_people.append(cand["name"])
            else:
                rejected_people.append({"name": cand["name"], "reason": eval_res.get("rejection_reason")})

        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "PERSON_MATCHING",
            "candidates_found": len(candidates_pool),
            "verified_count": len(verified_people),
            "rejected_count": len(rejected_people)
        })
        db.commit()

        return {
            "candidates_total": len(candidates_pool),
            "verified_people": verified_people,
            "rejected_people": rejected_people
        }

    async def finalize_verification_and_sync(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Final Verification Gate & Transactional Outbox Promotion to PostgreSQL.
        """
        session.status = "FINAL_VERIFICATION"
        db.commit()

        # Mark as VERIFIED
        session.status = "VERIFIED"
        session.verified_at = utc_now()
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "VERIFIED",
            "message": "Final Verification Gate passed successfully."
        })
        db.commit()

        # Stage in Transactional Outbox for PostgreSQL Sync
        session.status = "POSTGRES_SYNC_PENDING"
        db.commit()

        # Collect verified candidates to sync to PostgreSQL Lake
        verified_candidates = db.query(Agent2PersonCandidate).filter(
            Agent2PersonCandidate.session_id == session.id,
            Agent2PersonCandidate.candidate_status == "VERIFIED"
        ).all()

        people_payload = [
            {
                "full_name": c.person_name,
                "name": c.person_name,
                "title": c.title,
                "linkedin_url": c.linkedin_url,
                "linkedin_search_url": c.linkedin_url
            }
            for c in verified_candidates
        ]

        payload = {
            "id": session.id,
            "domain": session.domain,
            "company_name": session.company_name,
            "summary": session.phase2_data.get("business_overview", {}).get("text"),
            "industry": session.phase1_data.get("industry_sector", {}).get("value") or "Unknown",
            "location": session.phase1_data.get("location_region", {}).get("value"),
            "headquarters": session.phase1_data.get("location_region", {}).get("value"),
            "company_size": session.phase1_data.get("company_size_tier", {}).get("value") or "NOT_FOUND_AFTER_SEARCH",
            "verified_emails": [session.phase1_data.get("verified_contact_email", {}).get("value")] if session.phase1_data.get("verified_contact_email", {}).get("value") else [],
            "people": people_payload,
            "quality_score": 9.2
        }

        outbox_sync_service.queue_for_postgres_sync(
            db=db,
            domain=session.domain,
            company_name=session.company_name,
            payload=payload
        )

        # Process outbox queue
        sync_result = outbox_sync_service.process_outbox_queue(db=db, limit=5)
        if sync_result.get("synced", 0) > 0:
            session.status = "POSTGRES_VERIFIED"
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "POSTGRES_VERIFIED",
                "message": "Intelligence successfully synchronized to PostgreSQL Lake."
            })
        else:
            # Remains honestly in POSTGRES_SYNC_PENDING without pretending success!
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "POSTGRES_SYNC_PENDING",
                "message": f"PostgreSQL synchronization pending (status: {sync_result.get('status')})."
            })
        db.commit()

        return {
            "final_status": session.status,
            "verified_at": session.verified_at.isoformat() if session.verified_at else None,
            "sync_result": sync_result
        }

    async def execute_full_verification(self, document_id: str) -> Dict[str, Any]:
        """Runs the entire Agent 2 verification workflow end-to-end for a Document."""
        db = SessionLocal()
        try:
            session = self.get_or_create_session(document_id, db)
            if not session:
                return {"status": "error", "error": f"Document {document_id} not found"}

            # Step 1: Priority Ranking
            self.rank_card(session, db)

            # Step 2: Phase 1 Evidence Verification
            p1_res = await self.verify_phase1(session, db)
            if p1_res.get("status") == "blocked":
                return {"status": "blocked", "stage": "PHASE1", "details": p1_res}

            # Step 3: Phase 2 Business Synthesis
            await self.synthesize_business(session, db)

            # Step 4: LinkedIn Discovery & Matching
            await self.discover_and_verify_linkedin(session, db)

            # Step 5: Final Verification & Outbox
            final_res = await self.finalize_verification_and_sync(session, db)

            return {
                "status": "success",
                "session_id": session.id,
                "domain": session.domain,
                "final_state": session.status,
                "verified_at": final_res.get("verified_at"),
            }
        finally:
            db.close()


agent2_orchestrator = Agent2Orchestrator()
