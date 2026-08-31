import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.persistence.database import SessionLocal
from app.persistence.models import (
    AgentState, BatchResult, SearchHistory, KeywordPerformance,
    UniversalRecord, VerificationRecord
)
from app.worker.tasks import search_and_discover_task

logger = logging.getLogger(__name__)

# Predefined domain taxonomy seeds
DOMAINS_TAXONOMY = {
    "Information Technology": [
        "SaaS startups", "enterprise software companies", "cybersecurity providers",
        "cloud computing vendors", "AI machine learning startups", "IT consulting firms",
        "mobile app development companies", "data analytics platforms"
    ],
    "Healthcare": [
        "biotech companies", "medical device manufacturers", "telehealth platforms",
        "digital health startups", "clinical trial organizations", "pharma tech solutions"
    ],
    "Education": [
        "EdTech platforms", "online learning universities", "e-learning software companies",
        "corporate training providers", "educational institutions technology"
    ],
    "Business & Finance": [
        "FinTech startups", "investment management firms", "corporate consulting agencies",
        "accounting software vendors", "B2B payment platforms", "insurtech companies"
    ]
}

class AutonomousDiscoveryAgent:
    def __init__(self):
        self.is_running_loop = False
        self._task: Optional[asyncio.Task] = None

    def get_state(self, db: Session) -> AgentState:
        state = db.query(AgentState).first()
        if not state:
            state = AgentState(
                status="PAUSED",
                current_domain="Information Technology",
                current_subdomain="SaaS",
                current_keyword="SaaS startups",
                state_data={"batch_id": str(uuid.uuid4()), "search_count": 0}
            )
            db.add(state)
            db.commit()
            db.refresh(state)
        return state

    def set_status(self, status: str) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            state = self.get_state(db)
            state.status = status.upper()
            state.last_run_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Agent status changed to: {state.status}")
            
            # Start background loop if set to RUNNING and not already running
            if state.status == "RUNNING" and not self.is_running_loop:
                self.start_loop()
                
            return {
                "status": state.status,
                "current_domain": state.current_domain,
                "current_keyword": state.current_keyword
            }
        finally:
            db.close()

    def start_loop(self):
        if not self.is_running_loop:
            self.is_running_loop = True
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._discovery_loop())
            except Exception:
                try:
                    self._task = asyncio.ensure_future(self._discovery_loop())
                except Exception as e:
                    logger.error(f"Failed to create discovery loop task: {e}")
            logger.info("Autonomous Agent Loop initiated.")

    async def _discovery_loop(self):
        """Continuous 24/7 Agent Thinking & Strategy Loop."""
        logger.info("Starting Autonomous Agent Discovery Loop...")
        domain_keys = list(DOMAINS_TAXONOMY.keys())
        domain_idx = 0

        while self.is_running_loop:
            db = SessionLocal()
            try:
                state = self.get_state(db)
                if state.status != "RUNNING":
                    logger.info("Agent is PAUSED. Waiting...")
                    await asyncio.sleep(3)
                    continue

                # 1. THINK & SELECT NEXT KEYWORD / DOMAIN
                current_domain = domain_keys[domain_idx % len(domain_keys)]
                keywords = DOMAINS_TAXONOMY[current_domain]
                
                # Check keyword performance to prioritize high yield search terms
                best_keyword = None
                for kw in keywords:
                    perf = db.query(KeywordPerformance).filter(KeywordPerformance.keyword == kw).first()
                    if not perf or not perf.is_deprecated:
                        best_keyword = kw
                        break
                
                if not best_keyword:
                    best_keyword = keywords[0]

                # Ensure active batch exists
                state_data = state.state_data or {}
                batch_id = state_data.get("batch_id")
                search_count = state_data.get("search_count", 0)

                batch = None
                if batch_id:
                    batch = db.query(BatchResult).filter(BatchResult.id == batch_id).first()

                if not batch or batch.status == "COMPLETED":
                    batch_id = str(uuid.uuid4())
                    batch = BatchResult(
                        id=batch_id,
                        status="RUNNING",
                        searches_planned=100,
                        searches_executed=0
                    )
                    db.add(batch)
                    db.commit()
                    search_count = 0

                # 2. UPDATE STATE
                state.current_domain = current_domain
                state.current_keyword = best_keyword
                state_data["batch_id"] = batch_id
                state_data["search_count"] = search_count + 1
                state.state_data = state_data
                db.commit()

                logger.info(f"[Agent Loop] Batch={batch_id} (#{search_count+1}/100) -> Domain='{current_domain}', Keyword='{best_keyword}'")

                # 3. DISPATCH SEARCH TASK TO WORKERS (Non-blocking)
                def _dispatch_task():
                    try:
                        search_and_discover_task.delay(
                            keyword=best_keyword,
                            domain=current_domain,
                            batch_id=batch_id
                        )
                    except Exception as task_err:
                        logger.warning(f"Celery task dispatch failed ({task_err}), falling back to direct execution...")
                        search_and_discover_task(
                            best_keyword,
                            current_domain,
                            batch_id
                        )

                await asyncio.to_thread(_dispatch_task)

                # Update batch progress
                batch.searches_executed += 1
                db.commit()

                # 4. EVALUATE BATCH FEEDBACK (every 10 searches or when batch reaches 100)
                if batch.searches_executed >= 100:
                    self._generate_batch_feedback(db, batch)
                    state_data["batch_id"] = str(uuid.uuid4())
                    state_data["search_count"] = 0
                    state.state_data = state_data
                    db.commit()

                domain_idx += 1

            except Exception as e:
                logger.error(f"Error in agent discovery loop: {e}")
            finally:
                db.close()

            # Pace out loop iterations
            await asyncio.sleep(5)

    def _generate_batch_feedback(self, db: Session, batch: BatchResult):
        """Generate structured feedback report after a discovery batch."""
        logger.info(f"Generating feedback report for Batch {batch.id}...")
        
        # Calculate discovered stats
        searches = db.query(SearchHistory).filter(SearchHistory.batch_id == batch.id).all()
        total_sources = sum(s.sources_found for s in searches)
        
        batch.urls_discovered = total_sources
        batch.entities_discovered = db.query(UniversalRecord).count()
        batch.entities_verified = db.query(VerificationRecord).filter(VerificationRecord.is_verified == True).count()
        batch.status = "COMPLETED"
        batch.completed_at = datetime.now(timezone.utc)
        batch.feedback_generated = True
        
        # Learn from keywords performance
        for s in searches:
            perf = db.query(KeywordPerformance).filter(KeywordPerformance.keyword == s.keyword).first()
            if not perf:
                perf = KeywordPerformance(keyword=s.keyword, domain=s.domain)
                db.add(perf)
            
            perf.usage_count += 1
            if s.sources_found == 0:
                perf.is_deprecated = True
                perf.feedback_notes = "Zero sources returned in search."
            else:
                perf.success_rate = min(1.0, float(s.sources_found) / 15.0)
                
        db.commit()
        logger.info(f"Feedback report generated for Batch {batch.id}. Metrics updated.")

    def get_metrics(self, db: Session) -> Dict[str, Any]:
        state = self.get_state(db)
        
        total_searches = db.query(SearchHistory).count()
        sources_discovered = db.query(SearchHistory).with_entities(SearchHistory.sources_found).all()
        total_sources = sum(s[0] for s in sources_discovered) if sources_discovered else 0
        
        total_entities = db.query(UniversalRecord).count()
        verified_entities = db.query(VerificationRecord).filter(VerificationRecord.is_verified == True).count()
        duplicates_removed = db.query(UniversalRecord).filter(UniversalRecord.status == "Duplicate").count()
        
        recent_batch = db.query(BatchResult).order_by(BatchResult.started_at.desc()).first()
        
        # Get list of recently discovered canonical entities
        recent_records = db.query(UniversalRecord).order_by(UniversalRecord.created_at.desc()).limit(10).all()
        
        return {
            "status": state.status,
            "current_domain": state.current_domain,
            "current_keyword": state.current_keyword,
            "total_searches": total_searches,
            "sources_discovered": total_sources,
            "entities_discovered": total_entities,
            "entities_verified": verified_entities,
            "duplicates_removed": duplicates_removed,
            "active_batch": {
                "id": recent_batch.id if recent_batch else None,
                "status": recent_batch.status if recent_batch else "IDLE",
                "searches_executed": recent_batch.searches_executed if recent_batch else 0,
                "searches_planned": recent_batch.searches_planned if recent_batch else 100
            },
            "recent_entities": [
                {
                    "id": r.id,
                    "canonical_name": r.canonical_name or "Organization Lead",
                    "domain": r.domain.name if (r.domain and hasattr(r.domain, "name")) else "Technology",
                    "country": r.country or "Global",
                    "url": r.url,
                    "status": r.status or "Discovered",
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
                for r in recent_records
            ]
        }

discovery_agent = AutonomousDiscoveryAgent()
