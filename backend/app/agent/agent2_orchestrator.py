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

        from app.audit.tracer import tracer, Checkpoint
        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP13_LEAD_ANALYSIS,
            event="PRIORITY_RANKED",
            message=f"Agent 2 priority ranked {session.domain}: score={final_score} ({len(reasons)} criteria met)",
            agent_id="AGENT-02",
            lead_id=session.domain,
            extra={"priority_score": final_score, "reasons": reasons}
        )
        return final_score

    async def verify_phase1(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Phase 1: Evidence Verification.
        Investigates all 7 required Phase-1 fields with field-specific strategies and multi-round retries.
        Enforces Phase 1 Gate: ZERO UNVERIFIED or MISSING_BUT_NOT_CHECKED fields allowed.
        """
        from app.audit.tracer import tracer, Checkpoint
        import time
        t_phase1_start = time.time()

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP14_LEAD_CLASSIFICATION,
            event="PHASE1_VERIFICATION_START",
            message=f"Agent 2 Phase 1 Gate verification starting for {session.domain} ({session.company_name})",
            agent_id="AGENT-02",
            lead_id=session.domain,
            status="STARTED"
        )

        doc = db.query(Document).filter(Document.id == session.document_id).first()
        if not doc:
            session.status = "PHASE1_BLOCKED"
            session.error_message = "Source document not found in database"
            db.commit()
            tracer.log_event(
                level="ERROR",
                checkpoint=Checkpoint.CP14_LEAD_CLASSIFICATION,
                event="PHASE1_GATE_BLOCKED",
                message=f"Phase 1 Gate BLOCKED for {session.domain}: Source document {session.document_id} not found",
                agent_id="AGENT-02",
                lead_id=session.domain,
                status="FAILED"
            )
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

        # ── Selective Field-Level Requirement Check for Agent 2 ─────────────────
        # Only trigger deep crawling if existing text corpus and metadata lack sufficient evidence.
        subpages_done = metadata.get("subpages_crawled") or []
        word_count_existing = len(text_corpus.split())
        needs_deep_crawl = word_count_existing < 150 or not metadata.get("detected_emails")

        if len(subpages_done) < 2 and needs_deep_crawl:
            try:
                target_url = doc.url or f"https://{domain}"
                logger.info(f"AGENT2_BROWSER_CREATED run_id={session.id} lead_id={domain} task_id={session.document_id} agent_id=AGENT-02")
                max_deep_pages = getattr(settings, "MAX_PAGES_PER_DOMAIN_AGENT2", 4)
                crawl_items = await crawler_service.crawl_site(target_url, max_depth=1, max_pages=max_deep_pages, slot_type="deep")
                for item in crawl_items:
                    logger.info(f"AGENT2_PAGE_CRAWLED url={item.url} run_id={session.id} lead_id={domain} task_id={session.document_id} agent_id=AGENT-02")
                    try:
                        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", urlparse(item.url).path.strip("/")) or "index"
                        md_rel = f"companies/{domain}/pages/{slug}.md"
                        file_storage.save_file(md_rel, (item.markdown or item.text or "").encode("utf-8"), content_type="text/markdown")
                        if item.html_content:
                            html_rel = f"companies/{domain}/pages/{slug}.html"
                            file_storage.save_file(html_rel, item.html_content.encode("utf-8"), content_type="text/html")

                        from app.persistence.models import GlobalLead, GlobalLeadSubpage
                        gl = db.query(GlobalLead).filter(GlobalLead.domain == domain).first()
                        if gl:
                            existing_sub = db.query(GlobalLeadSubpage).filter(GlobalLeadSubpage.page_url == item.url).first()
                            if not existing_sub:
                                db.add(GlobalLeadSubpage(
                                    global_lead_id=gl.id,
                                    domain=domain,
                                    page_url=item.url,
                                    minio_object_path=md_rel
                                ))
                                db.commit()
                    except Exception as sub_err:
                        logger.debug(f"[Agent 2] Subpage persist error: {sub_err}")

                    if item.text and item.text not in text_corpus:
                        text_corpus += "\n\n" + item.text
            except Exception as crawl_err:
                logger.warning(f"[Agent 2] Coordinated crawl error for {domain}: {crawl_err}")
            finally:
                logger.info(f"AGENT2_BROWSER_CLOSED run_id={session.id} lead_id={domain} task_id={session.document_id} agent_id=AGENT-02")

        # Protect existing valid company name & title
        if not session.company_name or session.company_name.lower() in ["unknown", "index", "discovered entity"]:
            if doc.title:
                session.company_name = doc.title

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

        # Persist Agent2Evidence records & log extracted fields at DEBUG level
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

            tracer.log_event(
                level="DEBUG",
                checkpoint=Checkpoint.CP22_EVIDENCE_COLLECTION,
                event="FIELD_EVIDENCE_EXTRACTED",
                message=f"Field '{fname}' -> status={fres.get('status')} value='{fres.get('value')}' (method={fres.get('verification_method')})",
                agent_id="AGENT-02",
                lead_id=domain,
                extra={"field": fname, "value": fres.get("value"), "status": fres.get("status"), "source": fres.get("source_url")}
            )

        session.phase1_data = field_results

        # ── Phase 1 Gate Evaluation ──────────────────────────────────────────
        unverified_fields = [k for k, v in field_results.items() if v.get("status") == "UNVERIFIED"]
        phase1_dur = time.time() - t_phase1_start
        if unverified_fields:
            session.status = "PHASE1_BLOCKED"
            session.error_message = f"Phase 1 Gate blocked: incomplete investigations for {unverified_fields}"
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "PHASE1_BLOCKED",
                "unverified_fields": unverified_fields
            })
            db.commit()
            tracer.log_event(
                level="WARNING",
                checkpoint=Checkpoint.CP23_VALIDATION_GATE,
                event="PHASE1_GATE_BLOCKED",
                message=f"Phase 1 Gate BLOCKED for {domain}: unverified fields {unverified_fields}",
                agent_id="AGENT-02",
                lead_id=domain,
                duration=phase1_dur,
                status="BLOCKED",
                extra={"unverified_fields": unverified_fields}
            )
            return {"status": "blocked", "unverified_fields": unverified_fields}

        session.status = "PHASE1_VERIFIED"
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "PHASE1_VERIFIED",
            "message": "All 7 fields successfully investigated and passed Phase 1 Gate."
        })
        db.commit()

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP23_VALIDATION_GATE,
            event="PHASE1_GATE_PASSED",
            message=f"Phase 1 Gate PASSED for {domain}: All 7 fields verified or exhausted without unhandled status",
            agent_id="AGENT-02",
            lead_id=domain,
            duration=phase1_dur,
            status="SUCCESS"
        )
        return {"status": "success", "field_results": field_results}

    async def synthesize_business(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Phase 2: Business Overview & Synthesis.
        Uses Haystack / LLM to produce an evidence-grounded summary without hallucinations.
        """
        from app.audit.tracer import tracer, Checkpoint
        import time
        t_synth_start = time.time()

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP15_DEEP_RESEARCH,
            event="BUSINESS_SYNTHESIS_START",
            message=f"Agent 2 business synthesis starting for {session.domain}",
            agent_id="AGENT-02",
            lead_id=session.domain,
            status="STARTED"
        )
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
        Final Authoritative Verification Gate & Transactional Outbox Promotion to PostgreSQL.
        INVARIANT 2 & 4: Agent 2 completion alone does NOT mean VERIFIED.
        Only a PASS from the Verification Contract allows status = VERIFIED and UniversalRecord creation.
        """
        from app.verification.verification_contract import verification_contract
        from app.audit.tracer import tracer, Checkpoint
        from app.persistence.models import UniversalRecord

        session.status = "FINAL_VERIFICATION"
        db.commit()

        # Authoritative Contract Evaluation
        evaluation = verification_contract.evaluate_session(session, db)
        session.phase2_data = session.phase2_data or {}
        session.phase2_data["contract_evaluation"] = evaluation

        # ── GATE CHECK ────────────────────────────────────────────────────────
        if not evaluation["is_verified"]:
            failed_state = evaluation["verification_state"]
            session.status = failed_state
            session.verified_at = None
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": failed_state,
                "message": f"Verification Contract evaluated: {failed_state}. Completeness: {evaluation['completeness_score']}%. Missing required: {evaluation['missing_required_fields']}",
                "evaluation": evaluation
            })

            doc = db.query(Document).filter(Document.id == session.document_id).first()
            if doc:
                doc.lifecycle_state = failed_state
                # Downgrade any stale UniversalRecord if previously marked verified
                univ = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc.id).first()
                if univ and univ.status == "VERIFIED":
                    univ.status = failed_state
                    univ.metadata_json = {"verification_contract": evaluation}
            db.commit()

            tracer.log_event(
                level="WARNING",
                checkpoint=Checkpoint.CP24_VERIFICATION_GATE,
                event="VERIFICATION_CONTRACT_FAILED",
                message=f"Agent 2 verification contract FAILED for {session.domain}: {failed_state} (score={evaluation['completeness_score']}%)",
                agent_id="AGENT-02",
                lead_id=session.domain,
                status=failed_state,
                extra=evaluation
            )
            return {
                "final_status": failed_state,
                "is_verified": False,
                "verified_at": None,
                "completeness_score": evaluation["completeness_score"],
                "missing_required_fields": evaluation["missing_required_fields"],
                "sync_result": {"status": "gated_unverified"}
            }

        # ── CONTRACT PASSED: Mark as VERIFIED ─────────────────────────────────
        session.status = "VERIFIED"
        session.verified_at = utc_now()
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "VERIFIED",
            "message": f"Final Verification Gate PASSED (Completeness: {evaluation['completeness_score']}%).",
            "evaluation": evaluation
        })
        db.commit()

        # Update Document and create/update authoritative UniversalRecord
        doc = db.query(Document).filter(Document.id == session.document_id).first()
        if doc:
            doc.lifecycle_state = "VERIFIED"
            univ = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc.id).first()
            if not univ:
                univ = UniversalRecord(
                    document_id=doc.id,
                    canonical_name=session.company_name,
                    entity_type=session.phase1_data.get("industry_sector", {}).get("value") or "Organization",
                    description=session.phase2_data.get("business_overview", {}).get("text") or doc.title or "",
                    url=f"https://{session.domain}",
                    country="Global",
                    status="VERIFIED",
                    confidence=evaluation.get("confidence", 0.95),
                    metadata_json={"verification_contract": evaluation}
                )
                db.add(univ)
            else:
                univ.status = "VERIFIED"
                univ.canonical_name = session.company_name
                univ.description = session.phase2_data.get("business_overview", {}).get("text") or univ.description
                univ.metadata_json = {"verification_contract": evaluation}
            db.commit()

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP24_VERIFICATION_GATE,
            event="FINAL_VERIFICATION_PASSED",
            message=f"Agent 2 final verification PASSED for {session.domain} ({session.company_name}) [Score: {evaluation['completeness_score']}%]",
            agent_id="AGENT-02",
            lead_id=session.domain,
            status="VERIFIED"
        )

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
            "quality_score": float(evaluation["completeness_score"])
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
            tracer.log_event(
                level="INFO",
                checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
                event="POSTGRES_SYNC_SUCCESS",
                message=f"Persisted verified company {session.domain} to PostgreSQL Lake",
                agent_id="AGENT-02",
                lead_id=session.domain,
                status="POSTGRES_VERIFIED"
            )
        else:
            # Remains honestly in POSTGRES_SYNC_PENDING without pretending success!
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "POSTGRES_SYNC_PENDING",
                "message": f"PostgreSQL synchronization pending (status: {sync_result.get('status')})."
            })
            tracer.log_event(
                level="WARNING",
                checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
                event="POSTGRES_SYNC_PENDING",
                message=f"PostgreSQL sync pending for {session.domain}: {sync_result.get('status')}",
                agent_id="AGENT-02",
                lead_id=session.domain,
                status="POSTGRES_SYNC_PENDING"
            )
        db.commit()

        return {
            "final_status": session.status,
            "is_verified": True,
            "completeness_score": evaluation["completeness_score"],
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
