"""
Autonomous Discovery Agent — §1–10, §26–29 of Master Prompt

The AGENT is the brain of the OpenDB system. It:
  - THINKS:   selects next domain/subdomain/keyword (8-domain global taxonomy)
  - SEARCHES: dispatches SearXNG search tasks via Celery
  - LEARNS:   evaluates batch feedback, learns keyword performance
  - ADAPTS:   replaces deprecated keywords, expands from discovered entities
  - LOOPS:    24/7 continuous discovery until paused

User-facing actions:
  - RUN   → start_loop()
  - PAUSE → set_status("PAUSED")
"""
import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from app.persistence.database import SessionLocal
from app.persistence.models import (
    AgentState, BatchResult, SearchHistory, KeywordPerformance,
    UniversalRecord, VerificationRecord
)
from app.agent.keyword_expander import keyword_expander

logger = logging.getLogger(__name__)

# Agent loop pacing — seconds between dispatched search tasks
LOOP_PACE_SECONDS = 8
BATCH_SIZE = 100


class AutonomousDiscoveryAgent:
    """
    Stateful global discovery agent.
    State is persisted in AgentState (PostgreSQL) so it survives container restarts.
    """

    def __init__(self):
        self.is_running_loop: bool = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ─── Public Control Interface ──────────────────────────────────────────────

    def set_status(self, status: str) -> Dict[str, Any]:
        """RUN or PAUSE the agent."""
        db = SessionLocal()
        try:
            state = self._get_or_create_state(db)
            state.status = status.upper()
            state.last_run_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"[Agent] Status → {state.status}")

            if state.status == "RUNNING" and not self.is_running_loop:
                self._start_background_thread()
            elif state.status == "PAUSED":
                self.is_running_loop = False

            return {
                "status": state.status,
                "current_domain": state.current_domain,
                "current_keyword": state.current_keyword,
            }
        finally:
            db.close()

    def resume_if_was_running(self):
        """Called at app startup — resumes if agent was RUNNING before restart."""
        db = SessionLocal()
        try:
            state = db.query(AgentState).first()
            if state and state.status == "RUNNING" and not self.is_running_loop:
                logger.info("[Agent] Auto-resuming agent loop after restart...")
                self._start_background_thread()
        except Exception as e:
            logger.error(f"[Agent] Auto-resume failed: {e}")
        finally:
            db.close()

    # ─── Background Thread (wraps async event loop) ───────────────────────────

    def _start_background_thread(self):
        """
        Run the async discovery loop in a dedicated OS thread with its own event loop.
        This avoids any conflict with FastAPI's event loop.
        """
        self.is_running_loop = True
        self._thread = threading.Thread(
            target=self._thread_entry,
            name="opendb-agent-loop",
            daemon=True,
        )
        self._thread.start()
        logger.info("[Agent] Background discovery thread started.")

    def _thread_entry(self):
        """Entry point for the background thread — creates its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._discovery_loop())
        except Exception as e:
            logger.error(f"[Agent] Discovery loop crashed: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()
            self._loop = None
            self.is_running_loop = False
            logger.info("[Agent] Background discovery thread stopped.")

    # ─── Core Discovery Loop ───────────────────────────────────────────────────

    async def _discovery_loop(self):
        """24/7 continuous agent loop — §1, §26."""
        logger.info("[Agent] Discovery loop starting...")

        all_domains = keyword_expander.all_domains()
        domain_idx = 0

        while self.is_running_loop:
            db = SessionLocal()
            try:
                state = self._get_or_create_state(db)

                # Check PAUSE signal
                if state.status != "RUNNING":
                    logger.info("[Agent] PAUSED. Sleeping...")
                    await asyncio.sleep(3)
                    continue

                # ── 1. THINK: select next domain/keyword ──────────────────────
                current_domain = all_domains[domain_idx % len(all_domains)]
                domain_idx += 1

                # Use keyword expander to get a diverse global query
                query_info = keyword_expander.get_next_query(
                    domain=current_domain,
                    skip_geos=self._get_recently_used_geos(db),
                )
                query = query_info["query"]
                keyword = query_info["keyword"]
                subdomain = query_info["subdomain"]

                # Check if keyword is deprecated in performance table
                if self._is_keyword_deprecated(db, keyword):
                    # Try next query from same domain
                    query_info = keyword_expander.get_next_query(domain=current_domain)
                    query = query_info["query"]
                    keyword = query_info["keyword"]
                    subdomain = query_info["subdomain"]

                # ── 2. BATCH MANAGEMENT ───────────────────────────────────────
                batch = self._get_or_create_batch(db, state)

                # ── 3. UPDATE AGENT STATE ─────────────────────────────────────
                state.current_domain = current_domain
                state.current_subdomain = subdomain
                state.current_keyword = keyword
                state_data = state.state_data or {}
                state_data["batch_id"] = str(batch.id)
                state_data["search_count"] = state_data.get("search_count", 0) + 1
                state_data["last_query"] = query
                state.state_data = state_data
                db.commit()

                batch_id_str = str(batch.id)
                logger.info(
                    f"[Agent] Batch={batch_id_str[:8]} #{batch.searches_executed+1}/{BATCH_SIZE} "
                    f"→ Domain='{current_domain}' Sub='{subdomain}' Query='{query}'"
                )

                # ── 4. DISPATCH SEARCH TASK ───────────────────────────────────
                await asyncio.to_thread(
                    self._dispatch_search_task,
                    query=query,
                    keyword=keyword,
                    domain=current_domain,
                    subdomain=subdomain,
                    batch_id=batch_id_str,
                )

                # ── 5. UPDATE BATCH PROGRESS ──────────────────────────────────
                batch.searches_executed = (batch.searches_executed or 0) + 1
                db.commit()

                # ── 6. BATCH FEEDBACK (every 100 searches) ───────────────────
                if batch.searches_executed >= BATCH_SIZE:
                    self._generate_batch_feedback(db, batch)
                    # Start new batch
                    new_batch_id = str(uuid.uuid4())
                    new_batch = BatchResult(
                        id=new_batch_id,
                        status="RUNNING",
                        searches_planned=BATCH_SIZE,
                        searches_executed=0,
                    )
                    db.add(new_batch)
                    state_data["batch_id"] = new_batch_id
                    state_data["search_count"] = 0
                    state.state_data = state_data
                    db.commit()

            except Exception as e:
                logger.error(f"[Agent] Loop iteration error: {e}", exc_info=True)
            finally:
                db.close()

            await asyncio.sleep(LOOP_PACE_SECONDS)

        logger.info("[Agent] Discovery loop exited cleanly.")

    # ─── Task Dispatch ─────────────────────────────────────────────────────────

    def _dispatch_search_task(self, query: str, keyword: str, domain: str,
                               subdomain: str, batch_id: str):
        """Dispatch Celery search task. Falls back to direct execution if Celery unavailable."""
        from app.worker.tasks import search_and_discover_task
        try:
            search_and_discover_task.delay(
                query=query,
                keyword=keyword,
                domain=domain,
                subdomain=subdomain,
                batch_id=batch_id,
            )
        except Exception as celery_err:
            logger.warning(f"[Agent] Celery unavailable ({celery_err}), running inline...")
            try:
                search_and_discover_task(
                    query=query,
                    keyword=keyword,
                    domain=domain,
                    subdomain=subdomain,
                    batch_id=batch_id,
                )
            except Exception as inline_err:
                logger.error(f"[Agent] Inline task also failed: {inline_err}")

    # ─── State Helpers ─────────────────────────────────────────────────────────

    def _get_or_create_state(self, db: Session) -> AgentState:
        state = db.query(AgentState).first()
        if not state:
            state = AgentState(
                status="PAUSED",
                current_domain="Information Technology",
                current_subdomain="SaaS & Cloud",
                current_keyword="SaaS startups B2B",
                state_data={"batch_id": None, "search_count": 0},
            )
            db.add(state)
            db.commit()
            db.refresh(state)
        return state

    def _get_or_create_batch(self, db: Session, state: AgentState) -> BatchResult:
        state_data = state.state_data or {}
        batch_id = state_data.get("batch_id")
        batch = None
        if batch_id:
            batch = db.query(BatchResult).filter(BatchResult.id == batch_id).first()
        if not batch or batch.status == "COMPLETED":
            batch_id = str(uuid.uuid4())
            batch = BatchResult(
                id=batch_id,
                status="RUNNING",
                searches_planned=BATCH_SIZE,
                searches_executed=0,
            )
            db.add(batch)
            db.commit()
        return batch

    def _is_keyword_deprecated(self, db: Session, keyword: str) -> bool:
        perf = db.query(KeywordPerformance).filter(
            KeywordPerformance.keyword == keyword,
            KeywordPerformance.is_deprecated == True,
        ).first()
        return perf is not None

    def _get_recently_used_geos(self, db: Session) -> List[str]:
        """Return recently used geo modifiers to avoid repetition."""
        recent = (
            db.query(SearchHistory)
            .order_by(SearchHistory.executed_at.desc())
            .limit(20)
            .all()
        )
        # Extract geo from search history domain/keyword metadata if stored
        return []  # Simple implementation — could be extended

    # ─── Batch Feedback & Learning ─────────────────────────────────────────────

    def _generate_batch_feedback(self, db: Session, batch: BatchResult):
        """§8 — Learn from batch results. Update keyword performance. Mark batch COMPLETED."""
        bid = str(batch.id)
        logger.info(f"[Agent] Generating feedback for Batch {bid[:8]}...")

        searches = db.query(SearchHistory).filter(SearchHistory.batch_id == bid).all()
        total_sources = sum(s.sources_found or 0 for s in searches)

        batch.urls_discovered = total_sources
        batch.entities_discovered = db.query(UniversalRecord).count()
        batch.entities_verified = (
            db.query(VerificationRecord).filter(VerificationRecord.is_verified == True).count()
        )
        batch.status = "COMPLETED"
        batch.completed_at = datetime.now(timezone.utc)
        batch.feedback_generated = True

        # Update keyword performance with EMA-style success rate
        for s in searches:
            if not s.keyword:
                continue
            perf = db.query(KeywordPerformance).filter(
                KeywordPerformance.keyword == s.keyword
            ).first()
            if not perf:
                perf = KeywordPerformance(
                    keyword=s.keyword,
                    domain=s.domain,
                    usage_count=0,
                    success_rate=0.5,
                )
                db.add(perf)
                db.flush()

            perf.usage_count = (perf.usage_count or 0) + 1
            sources = s.sources_found or 0
            # EMA: new_rate = 0.7 * old_rate + 0.3 * current_yield
            current_yield = min(1.0, sources / 15.0)
            old_rate = float(perf.success_rate or 0.5)
            perf.success_rate = round(0.7 * old_rate + 0.3 * current_yield, 4)

            if sources == 0 and (perf.usage_count or 0) >= 3:
                perf.is_deprecated = True
                perf.feedback_notes = f"Deprecated after {perf.usage_count} consecutive zero-result searches."
                logger.info(f"[Agent] Deprecated keyword: '{s.keyword}'")

        db.commit()
        logger.info(
            f"[Agent] Batch {bid[:8]} completed: "
            f"{total_sources} URLs | {batch.entities_discovered} entities | "
            f"{batch.entities_verified} verified"
        )

    # ─── Metrics API ───────────────────────────────────────────────────────────

    def get_metrics(self, db: Session) -> Dict[str, Any]:
        state = self._get_or_create_state(db)

        total_searches = db.query(SearchHistory).count()
        sources_rows = db.query(SearchHistory.sources_found).all()
        total_sources = sum((r[0] or 0) for r in sources_rows)

        total_entities = db.query(UniversalRecord).count()
        verified_entities = (
            db.query(VerificationRecord).filter(VerificationRecord.is_verified == True).count()
        )
        duplicates_removed = (
            db.query(UniversalRecord).filter(UniversalRecord.status == "Duplicate").count()
        )

        recent_batch = (
            db.query(BatchResult).order_by(BatchResult.started_at.desc()).first()
        )
        recent_records = (
            db.query(UniversalRecord).order_by(UniversalRecord.created_at.desc()).limit(10).all()
        )

        return {
            "status": state.status,
            "current_domain": state.current_domain,
            "current_subdomain": getattr(state, "current_subdomain", None) or "General",
            "current_keyword": state.current_keyword,
            "is_loop_running": self.is_running_loop,
            "total_searches": total_searches,
            "sources_discovered": total_sources,
            "entities_discovered": total_entities,
            "entities_verified": verified_entities,
            "duplicates_removed": duplicates_removed,
            "active_batch": {
                "id": recent_batch.id if recent_batch else None,
                "status": recent_batch.status if recent_batch else "IDLE",
                "searches_executed": recent_batch.searches_executed if recent_batch else 0,
                "searches_planned": recent_batch.searches_planned if recent_batch else BATCH_SIZE,
            },
            "recent_entities": [
                {
                    "id": r.id,
                    "canonical_name": r.canonical_name or "Organization Lead",
                    "domain": (
                        r.domain.name
                        if (r.domain and hasattr(r.domain, "name"))
                        else "Technology"
                    ),
                    "country": r.country or "Global",
                    "url": r.url,
                    "status": r.status or "Discovered",
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in recent_records
            ],
        }


discovery_agent = AutonomousDiscoveryAgent()
