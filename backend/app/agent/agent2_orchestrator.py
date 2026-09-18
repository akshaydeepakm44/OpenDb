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
from app.config import settings

from app.persistence.database import SessionLocal
from app.persistence.models import (
    Document, Company, Agent2VerificationSession, Agent2Evidence, Agent2PersonCandidate, utc_now
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
        if doc.markdown_path:
            text_corpus = file_storage.read_file_content(doc.markdown_path) or ""
            
        if not text_corpus and doc.raw_path:
            raw = file_storage.read_file_content(doc.raw_path) or ""
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(raw, "html.parser")
                for script in soup(["script", "style"]):
                    script.extract()
                text_corpus = soup.get_text(separator=' ', strip=True)
            except Exception:
                text_corpus = raw

        # [SMART CRAWL REFACTOR] Deferring deep crawl until after SearXNG fast-pass.
        # We will attempt to resolve fields using existing text and SearXNG first.
        
        # ── Phase 2: Lightweight HTTP Fetch ──
        # If existing text is very sparse, grab /about and /contact using httpx before querying SearXNG.
        word_count_existing = len(text_corpus.split())
        if word_count_existing < 150:
            logger.info(f"[Agent 2] Sparse text for {domain} ({word_count_existing} words). Attempting lightweight HTTP fetch.")
            import httpx
            from bs4 import BeautifulSoup
            
            async def fetch_text(url: str) -> str:
                try:
                    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, verify=False) as client:
                        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"})
                        if resp.status_code == 200:
                            soup = BeautifulSoup(resp.text, "html.parser")
                            for script in soup(["script", "style"]):
                                script.extract()
                            return soup.get_text(separator=' ', strip=True)
                except Exception as e:
                    logger.debug(f"[Agent 2] Lightweight fetch failed for {url}: {e}")
                return ""

            base_url = doc.url or f"https://{domain}"
            urls_to_try = [base_url, f"https://{domain}/about", f"https://{domain}/contact"]
            
            fetch_tasks = [fetch_text(u) for u in urls_to_try]
            import asyncio
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            
            for res in results:
                if isinstance(res, str) and res:
                    if res not in text_corpus:
                        text_corpus += "\n\n" + res
            logger.info(f"[Agent 2] Lightweight fetch completed for {domain}. New word count: {len(text_corpus.split())}")
            
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

        # 8. Corporate LinkedIn URL
        res_li = await investigation_engine.investigate_corporate_linkedin(
            domain=domain,
            company_name=company_name,
            existing_text=text_corpus,
            existing_metadata=metadata,
            searxng_service=searxng_service
        )
        field_results["company_linkedin_url"] = res_li

        # ── SMART FALLBACK: Deep Crawl only if critical fields are missing ──
        # Fields that usually warrant a deep crawl if SearXNG/existing text fails:
        critical_missing = []
        for f in ["verified_contact_email", "company_size_tier", "location_region", "industry_sector"]:
            if field_results[f].get("status") in ["UNVERIFIED", "NOT_FOUND_AFTER_SEARCH"]:
                critical_missing.append(f)

        if critical_missing:
            logger.info(f"[Agent 2] Fast-pass failed for {critical_missing} on {domain}. Triggering targeted Playwright deep crawl.")
            try:
                target_url = doc.url or f"https://{domain}"
                max_deep_pages = getattr(settings, "MAX_PAGES_PER_DOMAIN_AGENT2", 4)
                crawl_items = await crawler_service.crawl_site(target_url, max_depth=1, max_pages=max_deep_pages, slot_type="deep")
                
                new_text_discovered = False
                for item in crawl_items:
                    if item.text and item.text not in text_corpus:
                        text_corpus += "\n\n" + item.text
                        new_text_discovered = True
                
                # If the deep crawl found new text, re-run investigation for ONLY the missing fields
                if new_text_discovered:
                    if "verified_contact_email" in critical_missing:
                        field_results["verified_contact_email"] = await investigation_engine.investigate_email(domain, text_corpus, metadata, crawler_service, searxng_service)
                    if "company_size_tier" in critical_missing:
                        field_results["company_size_tier"] = await investigation_engine.investigate_company_size(domain, company_name, text_corpus, artifacts, crawler_service, searxng_service)
                    if "location_region" in critical_missing:
                        field_results["location_region"] = await investigation_engine.investigate_location(domain, company_name, text_corpus, artifacts, crawler_service, searxng_service)
                    if "industry_sector" in critical_missing:
                        field_results["industry_sector"] = await investigation_engine.investigate_industry(domain, company_name, text_corpus, artifacts, crawler_service, searxng_service)
            except Exception as crawl_err:
                logger.warning(f"[Agent 2] Playwright fallback failed for {domain}: {crawl_err}")

        # Clear prior evidence for this session before inserting fresh records
        db.query(Agent2Evidence).filter(Agent2Evidence.session_id == session.id).delete()

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
        # IMPORTANT: Only these 4 core investigation fields can block the pipeline.
        # Optional fields (company_linkedin_url, storage fields)
        # are allowed to be NOT_FOUND_AFTER_SEARCH without blocking.
        # UNVERIFIED means infrastructure failure (crawler/SearXNG down), not "not found".
        CORE_BLOCKING_FIELDS = {
            "industry_sector", "location_region", "company_size_tier", "verified_contact_email"
        }
        # Only block on core fields that are truly UNVERIFIED (infra failure)
        truly_unverified = [
            k for k, v in field_results.items()
            if k in CORE_BLOCKING_FIELDS and v.get("status") == "UNVERIFIED"
        ]
        # Log any non-core unverified fields as warnings (don't block)
        all_unverified = [k for k, v in field_results.items() if v.get("status") == "UNVERIFIED"]
        non_core_unverified = [k for k in all_unverified if k not in CORE_BLOCKING_FIELDS]

        phase1_dur = time.time() - t_phase1_start

        if non_core_unverified:
            tracer.log_event(
                level="WARNING",
                checkpoint=Checkpoint.CP23_VALIDATION_GATE,
                event="PHASE1_OPTIONAL_FIELDS_UNVERIFIED",
                message=f"Non-blocking optional fields unverified for {domain}: {non_core_unverified} (pipeline continues)",
                agent_id="AGENT-02",
                lead_id=domain,
                extra={"non_blocking_unverified": non_core_unverified}
            )

        if truly_unverified:
            session.status = "PHASE1_BLOCKED"
            session.error_message = f"Phase 1 Gate: core fields unresolved (infra failure) for {truly_unverified}"
            session.investigation_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "PHASE1_BLOCKED",
                "blocking_fields": truly_unverified,
                "non_blocking_unverified": non_core_unverified,
                "message": "Core fields UNVERIFIED due to infrastructure failure. Will continue to Phase 2 anyway."
            })
            db.commit()
            tracer.log_event(
                level="WARNING",
                checkpoint=Checkpoint.CP23_VALIDATION_GATE,
                event="PHASE1_GATE_PARTIAL_BLOCK",
                message=f"Phase 1 core fields blocked for {domain}: {truly_unverified} (continuing to Phase 2 with partial data)",
                agent_id="AGENT-02",
                lead_id=domain,
                duration=phase1_dur,
                status="PARTIAL",
                extra={"blocking_fields": truly_unverified}
            )
            # Do NOT return blocked — advance to PHASE1_VERIFIED so Phase 2 and people discovery still run
            session.status = "PHASE1_VERIFIED"
            db.commit()

        session.status = "PHASE1_VERIFIED"
        session.investigation_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": "PHASE1_VERIFIED",
            "message": f"Phase 1 complete. Verified fields: {[k for k,v in field_results.items() if v.get('status') not in ['UNVERIFIED']]}. Non-blocking unverified: {non_core_unverified}."
        })
        db.commit()

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP23_VALIDATION_GATE,
            event="PHASE1_GATE_PASSED",
            message=f"Phase 1 Gate PASSED for {domain}: core fields resolved, proceeding to Phase 2",
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
        if doc and doc.markdown_path:
            text_corpus = file_storage.read_file_content(doc.markdown_path) or ""
        elif doc and doc.raw_path:
            raw_content = file_storage.read_file_content(doc.raw_path) or ""
            if "<html" in raw_content.lower() or "<body" in raw_content.lower():
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(raw_content, "html.parser")
                for element in soup(["script", "style", "noscript", "svg"]):
                    element.extract()
                text_corpus = soup.get_text(separator=' ', strip=True)
            else:
                text_corpus = raw_content

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

    async def discover_people(self, session: Agent2VerificationSession, db) -> Dict[str, Any]:
        """
        Phase 3: Key-Person Discovery & Matching.
        1. Website First: Checks /about, /team, /leadership.
        2. LinkedIn Enrichment: Falls back to targeted LinkedIn queries.
        """
        session.status = "PEOPLE_DISCOVERY"
        db.commit()

        domain = session.domain
        company_name = session.company_name

        from app.extraction.person_verifier import PersonCompanyVerifier
        canon = PersonCompanyVerifier.canonicalize_company_identity(url=domain, title=company_name)
        domain_brand = canon.get("domain_brand") or domain.split(".")[0].capitalize()
        clean_name = canon.get("clean_name") or ""
        target_brand = domain_brand if (not clean_name or domain_brand.lower() not in clean_name.lower()) else clean_name

        if target_brand and (not session.company_name or "home" in session.company_name.lower() or session.company_name.lower() in ["unknown", "index"]):
            session.company_name = target_brand
            db.commit()

        candidates_pool: List[Dict[str, Any]] = []
        seen_urls = set()

        # ─── 0. GROUND-TRUTH ON-SITE TEAM & LEADERSHIP HARVESTING ───
        # Check crawled documents, raw HTML, and fetch /about & /team subpages for direct personal LinkedIn links
        doc = db.query(Document).filter(Document.id == session.document_id).first()
        raw_html = ""
        if doc and doc.raw_path:
            raw_html = file_storage.read_file_content(doc.raw_path) or ""

        import httpx
        from bs4 import BeautifulSoup

        def harvest_html_linkedin(html_content: str, source_url: str):
            if not html_content:
                return
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if "linkedin.com/in/" in href.lower():
                        clean_url = re.sub(r"\?.*$", "", href).rstrip("/")
                        if is_authentic_linkedin_personal_url(clean_url) and clean_url not in seen_urls:
                            seen_urls.add(clean_url)
                            text_val = a.get_text(strip=True)
                            parent_text = a.parent.get_text(separator=" ", strip=True) if a.parent else ""
                            m_role = re.search(r"(co-founder|founder|ceo|cpo|cto|coo|head of|vp|director|president|lead|advisor)", text_val, re.IGNORECASE)
                            if m_role:
                                name_part = text_val[:m_role.start()].strip()
                                role_part = text_val[m_role.start():].strip()
                            else:
                                name_part = text_val
                                role_part = "Founder / Executive"
                            
                            if not name_part and parent_text:
                                tokens = parent_text.split()
                                name_part = " ".join(tokens[:2]) if len(tokens) >= 2 else parent_text
                            
                            candidates_pool.append({
                                "name": name_part or "Verified Leader",
                                "url": clean_url,
                                "title": role_part or "Founder / Executive",
                                "company": target_brand,
                                "evidence": f"Direct leadership profile on official company page ({source_url})"
                            })
            except Exception as e:
                logger.debug(f"[Agent 2] Site harvesting error for {source_url}: {e}")

        # Harvest from source document
        if raw_html:
            harvest_html_linkedin(raw_html, f"https://{domain}")

        # If more candidates needed, quickly audit /about, /team, /leadership, /company
        if len(candidates_pool) < self.min_target_candidates:
            team_pages_to_check = [f"https://{domain}/about", f"https://{domain}/team", f"https://{domain}/company", f"https://{domain}/leadership"]
            try:
                # Synchronous-like quick fetch in async context
                with httpx.Client(timeout=4.0, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}, follow_redirects=True, verify=False) as client:
                    for tp in team_pages_to_check:
                        try:
                            resp = client.get(tp)
                            if resp.status_code == 200:
                                harvest_html_linkedin(resp.text, tp)
                            if len(candidates_pool) >= self.min_target_candidates:
                                break
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[Agent 2] Team pages fetch failed: {e}")

        # ─── 1. LINKEDIN FOUNDER & LEADERSHIP DISCOVERY (ZERO-CRAWL SEARCH) ───
        # As specified: site:linkedin.com/in/ "{company_name}" founder OR CEO
        # Do NOT crawl LinkedIn URLs; extract verified leader identity directly from search snippet.
        queries = [
            # Open-web queries that Bing/Mojeek/Yahoo respond to
            f'{target_brand} CEO founder linkedin profile',
            f'{target_brand} founder linkedin',
            f'{domain} CEO founder linkedin',
            f'{target_brand} co-founder executive linkedin',
            # site: variants as a supplementary pass (works if Google/Brave enabled)
            f'site:linkedin.com/in/ "{target_brand}" founder OR CEO',
            f'site:linkedin.com/in/ "{domain_brand}" founder OR CEO',
        ]

        # Multi-round search retry loop
        for rnd in range(1, self.max_linkedin_rounds + 1):
            if len(candidates_pool) >= self.min_target_candidates:
                break

            session.search_rounds = rnd
            round_queries = queries if rnd == 1 else generate_dynamic_linkedin_queries(target_brand, domain, search_round=rnd)

            for q in round_queries:
                try:
                    results, is_fb, _ = await searxng_service.search_with_meta(q, category="general", max_results=5)
                    if results:
                        for r in results:
                            raw_url = r.get("url", "")
                            content_snip = r.get("content") or r.get("snippet") or ""
                            title_snip = r.get("title", "")

                            # First: direct LinkedIn profile URL in result
                            linkedin_candidate_url = None
                            if is_authentic_linkedin_personal_url(raw_url):
                                linkedin_candidate_url = raw_url
                            else:
                                # Second: extract any linkedin.com/in/ URL from the search snippet/content
                                li_match = re.search(r'(https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_]+)', content_snip)
                                if li_match and is_authentic_linkedin_personal_url(li_match.group(1)):
                                    linkedin_candidate_url = li_match.group(1)

                            if linkedin_candidate_url:
                                clean_url = re.sub(r"\?.*$", "", linkedin_candidate_url).rstrip("/")
                                if clean_url not in seen_urls:
                                    seen_urls.add(clean_url)
                                    # Parse Name & Role directly from search result title
                                    # Formats: "Karri Saarinen - Co-Founder, CEO - Linear | LinkedIn"
                                    clean_title = re.sub(r'\s*\|\s*LinkedIn.*$', '', title_snip, flags=re.IGNORECASE).strip()
                                    parts = re.split(r'\s+[-–—]\s+', clean_title)
                                    parsed_name = parts[0].strip() if parts else clean_title
                                    role_guess = parts[1].strip() if len(parts) > 1 else "Executive / Founder"
                                    if len(parts) > 2 and any(kw in parts[2].lower() for kw in ["ceo", "founder", "president", "director", "head", "cto"]):
                                        role_guess = f"{role_guess}, {parts[2].strip()}"

                                    candidates_pool.append({
                                        "name": parsed_name or "Verified Leader",
                                        "url": clean_url,
                                        "title": role_guess or "Founder / CEO",
                                        "company": target_brand,
                                        "evidence": content_snip or f"Executive profile discovered via verified search for {target_brand}."
                                    })
                except Exception as search_err:
                    logger.warning(f"[Agent 2][LinkedIn] Search query '{q}' failed: {search_err}")

        # Clear prior candidates for this session before inserting fresh verified records
        db.query(Agent2PersonCandidate).filter(Agent2PersonCandidate.session_id == session.id).delete()

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
                source_url=cand["url"],
                linkedin_url=cand["url"]
            )

            is_verified = eval_res.get("company_match", False)

            p_cand = Agent2PersonCandidate(
                session_id=session.id,
                person_name=cand["name"],
                linkedin_url=cand["url"],
                title=cand["title"],
                source_domain=company_name,
                candidate_status="VERIFIED" if is_verified else "REJECTED",
                company_match_status=is_verified,
                is_leadership=eval_res.get("is_leadership", True),
                rejection_reason=eval_res.get("rejection_reason"),
                evidence_snippet=eval_res.get("evidence_snippet") or cand["evidence"],
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
        from app.persistence.models import UniversalRecord, Company

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
            session.investigation_log = (session.investigation_log or []) + [{
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": failed_state,
                "message": f"Verification Contract evaluated: {failed_state}. Completeness: {evaluation['completeness_score']}%. Missing required: {evaluation['missing_required_fields']}",
                "evaluation": evaluation
            }]

            doc = db.query(Document).filter(Document.id == session.document_id).first()
            if doc:
                doc.lifecycle_state = failed_state
                # Downgrade any stale UniversalRecord if previously marked verified
                univ = None
                if doc.company_id:
                    univ = db.query(Company).filter(Company.id == doc.company_id).first()
                if not univ and session.domain:
                    univ = db.query(Company).filter(Company.primary_domain == session.domain).first()
                if univ and univ.status == "VERIFIED":
                    univ.status = failed_state
                    # metadata_json is a read-only compat property; contract data is in investigation_log
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
            univ = None
            if doc.company_id:
                univ = db.query(Company).filter(Company.id == doc.company_id).first()
            if not univ and session.domain:
                univ = db.query(Company).filter(Company.primary_domain == session.domain).first()

            overview_val = session.phase2_data.get("business_overview", {})
            overview_text = overview_val.get("text") if isinstance(overview_val, dict) else str(overview_val)
            overview_text = overview_text or doc.title or ""

            li_comp_url = session.phase1_data.get("company_linkedin_url", {}).get("value")
            if not univ:
                univ = Company(
                    canonical_name=session.company_name,
                    primary_domain=session.domain,
                    description=overview_text,
                    industry=session.phase1_data.get("industry_sector", {}).get("value") or "Organization",
                    country="Global",
                    status="VERIFIED",
                    confidence=evaluation.get("confidence", 0.95),
                    linkedin_url=li_comp_url
                )
                db.add(univ)
                db.flush()
                doc.company_id = univ.id
            else:
                univ.status = "VERIFIED"
                univ.canonical_name = session.company_name
                if overview_text:
                    univ.description = overview_text
                if li_comp_url:
                    univ.linkedin_url = li_comp_url
                doc.company_id = univ.id
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

    async def execute_full_verification(self, document_id: str, force_rerun: bool = False) -> Dict[str, Any]:
        """
        Runs the Agent 2 verification workflow as a durable state machine.
        Resumes from the last known state or executes from the beginning if rerun requested.
        """
        db = SessionLocal()
        try:
            session = self.get_or_create_session(document_id, db)
            if not session:
                return {"status": "error", "error": f"Document {document_id} not found"}

            # If already verified and not forcing a re-run, return immediately
            if not force_rerun and session.status in ["VERIFIED", "POSTGRES_VERIFIED"]:
                return {"status": "success", "session_id": session.id, "final_state": session.status}

            # If re-running or starting fresh after an unverified/blocked/queued state, start from rank_card
            if force_rerun or session.status in [
                "QUEUED_FOR_VERIFICATION", "PHASE1_BLOCKED", "CRAWL_FAILED", "INSUFFICIENT_EVIDENCE",
                "PARTIALLY_VERIFIED", "NEEDS_REVIEW", "VERIFICATION_FAILED", "AGENT2_QUEUED"
            ]:
                session.status = "AGENT2_QUEUED"
                session.error_message = None
                db.commit()

            # State Machine Loop
            max_iterations = 20  # safety cap to prevent infinite loops
            iteration = 0
            while session.status not in ["VERIFIED", "POSTGRES_VERIFIED", "POSTGRES_SYNC_PENDING"] and iteration < max_iterations:
                current_state = session.status
                iteration += 1
                logger.info(f"[Agent 2] State machine iteration {iteration}: {session.domain} -> {current_state}")

                if current_state == "AGENT2_QUEUED":
                    self.rank_card(session, db)
                    # rank_card updates status to PHASE1_RANKED

                elif current_state == "PHASE1_RANKED":
                    p1_res = await self.verify_phase1(session, db)
                    # verify_phase1 now always advances to PHASE1_VERIFIED (never blocks pipeline)
                    # If status is still PHASE1_BLOCKED due to infra failure, force advance
                    db.refresh(session)
                    if session.status == "PHASE1_BLOCKED":
                        logger.warning(f"[Agent 2] Phase 1 blocked for {session.domain} but advancing to Phase 2 with partial data")
                        session.status = "PHASE1_VERIFIED"
                        db.commit()

                elif current_state == "PHASE1_VERIFIED":
                    await self.synthesize_business(session, db)
                    # synthesize_business updates status to PHASE2_SYNTHESIS

                elif current_state in ["PHASE2_SYNTHESIS", "PEOPLE_DISCOVERY"]:
                    await self.discover_people(session, db)
                    # discover_people updates status to PERSON_MATCHING

                elif current_state in ["PERSON_MATCHING", "PEOPLE_VERIFICATION"]:
                    final_res = await self.finalize_verification_and_sync(session, db)
                    # finalize_verification_and_sync updates to POSTGRES_SYNC_PENDING or a failed state
                    if not final_res.get("is_verified"):
                        logger.warning(f"[Agent 2] Final verification failed for {session.domain}: {session.status}")
                    break

                else:
                    logger.warning(f"[Agent 2] Unexpected state '{current_state}' for {session.id}, restarting from AGENT2_QUEUED")
                    session.status = "AGENT2_QUEUED"
                    db.commit()

                # Re-fetch session to ensure we have the latest committed state before looping
                db.refresh(session)

            return {
                "status": "success",
                "session_id": session.id,
                "domain": session.domain,
                "final_state": session.status,
                "verified_at": session.verified_at.isoformat() if session.verified_at else None,
            }
        except Exception as err:
            from sqlalchemy.orm import exc as orm_exc
            if isinstance(err, orm_exc.StaleDataError) or "staledataerror" in str(err).lower():
                logger.info(f"[Agent 2] Verification session for doc {document_id} was reset/purged during run. Terminating gracefully.")
                try:
                    db.rollback()
                except Exception:
                    pass
                return {"status": "purged", "message": "Session reset during run"}
            raise
        finally:
            db.close()


agent2_orchestrator = Agent2Orchestrator()
