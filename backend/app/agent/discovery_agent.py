"""
Haystack-inspired Autonomous Agent Orchestrator
§1–10, §26–29 of Master Prompt

This is a continuous 24/7 autonomous discovery agent.
It uses an LLM (via litellm) to evaluate the current state of the global discovery taxonomy,
select appropriate tools, and adapt its search strategy.
"""
import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.persistence.database import SessionLocal
from app.persistence.models import (
    AgentState, BatchResult, SearchHistory, KeywordPerformance,
    UniversalRecord, VerificationRecord, utc_now
)
from app.agent.keyword_expander import keyword_expander
from app.config import settings

try:
    import litellm
except ImportError:
    litellm = None

logger = logging.getLogger(__name__)

LOOP_PACE_SECONDS = 4
BATCH_SIZE = 100

# ─── Agent Tools ─────────────────────────────────────────────────────────────

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Dispatch a search strategy to SearXNG to find company URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The exact search query."},
                    "domain": {"type": "string", "description": "The high-level domain (e.g. Information Technology)."},
                    "subdomain": {"type": "string", "description": "The subdomain (e.g. SaaS)."},
                    "keyword": {"type": "string", "description": "The base keyword used."}
                },
                "required": ["query", "domain", "subdomain", "keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "discover_new_subdomain",
            "description": "Dynamically add a new subdomain to the global taxonomy based on observed trends.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "The parent domain."},
                    "new_subdomain": {"type": "string", "description": "The new subdomain discovered."},
                    "reason": {"type": "string", "description": "Why this subdomain is being added."}
                },
                "required": ["domain", "new_subdomain", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_batch",
            "description": "Evaluate the current batch results to update strategy and keyword performance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "batch_id": {"type": "string"}
                },
                "required": ["batch_id"]
            }
        }
    }
]


class AutonomousDiscoveryAgent:
    """
    Stateful global discovery agent.
    State is persisted in AgentState (PostgreSQL) so it survives container restarts.
    """

    def __init__(self):
        self.is_running_loop: bool = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.llm_model = settings.LLM_MODEL or "gpt-4o-mini"

    # ─── Public Control Interface ──────────────────────────────────────────────

    def set_status(self, status: str) -> Dict[str, Any]:
        """RUN or PAUSE the agent."""
        db = SessionLocal()
        try:
            state = self._get_or_create_state(db)
            state.status = status.upper()
            state.last_run_at = utc_now()
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
        self.is_running_loop = True
        self._thread = threading.Thread(
            target=self._thread_entry,
            name="opendb-agent-loop",
            daemon=True,
        )
        self._thread.start()
        logger.info("[Agent] Background discovery thread started.")

    def _thread_entry(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._discovery_loop())
        except Exception as e:
            logger.error(f"[Agent] Discovery loop crashed: {e}", exc_info=True)
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
        """24/7 continuous agent loop using LLM for decision making."""
        logger.info("[Agent] Discovery loop starting...")

        while self.is_running_loop:
            db = SessionLocal()
            try:
                state = self._get_or_create_state(db)

                if state.status != "RUNNING":
                    logger.info("[Agent] PAUSED. Sleeping...")
                    db.close()
                    await asyncio.sleep(3)
                    continue

                batch = self._get_or_create_batch(db, state)
                state_data = state.state_data or {}
                batch_id_str = str(batch.id)
                current_domain = state.current_domain

                # ── 1. GATHER STATE FOR LLM ──────────────────────────────────
                metrics = self.get_metrics(db)
                prompt = self._build_agent_prompt(metrics, batch)
                logger.info(f"[Agent] Thinking... Batch={batch_id_str[:8]} #{batch.searches_executed}/{BATCH_SIZE}")
            finally:
                db.close()

            # ── 2. INVOKE AGENT LLM (NETWORK CALL - NO DB LOCK HELD) ──
            tool_calls = await self._invoke_llm_agent(prompt)

            if not tool_calls:
                # Fallback to deterministic expansion if LLM fails or doesn't use tools
                query_info = keyword_expander.get_next_query(domain=current_domain)
                tool_calls = [{
                    "function": {
                        "name": "search_web",
                        "arguments": json.dumps(query_info)
                    }
                }]

            # ── 3. EXECUTE TOOLS & UPDATE DB ─────────────────────────
            db = SessionLocal()
            try:
                state = self._get_or_create_state(db)
                batch = self._get_or_create_batch(db, state)
                state_data = state.state_data or {}
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except Exception:
                        args = {}

                    logger.info(f"[Agent] Decision -> {func_name}({args})")

                    if func_name == "search_web":
                        query = args.get("query")
                        domain = args.get("domain", state.current_domain)
                        subdomain = args.get("subdomain", state.current_subdomain)
                        keyword = args.get("keyword", state.current_keyword)

                        # Code-Level Safety Guardrail Hard Constraint Pre-Check
                        allowed, block_reason = self._pre_check_agent_instruction(db, query, domain)
                        if not allowed:
                            logger.warning(f"🛡️ [AGENT GUARDRAIL] Aborting search_web for '{query}' due to safety block: {block_reason}")
                            continue

                        # Update State
                        state.current_domain = domain
                        state.current_subdomain = subdomain
                        state.current_keyword = keyword
                        state_data["search_count"] = state_data.get("search_count", 0) + 1
                        state_data["last_query"] = query
                        state.state_data = state_data
                        
                        batch.searches_executed = (batch.searches_executed or 0) + 1
                        db.commit()

                        # Guardrail Step 6: Batch Block Threshold Monitoring
                        from app.persistence.models import BlockedDomain
                        blocked_in_batch = db.query(BlockedDomain).filter(BlockedDomain.created_at >= batch.started_at).count()
                        searches_done = max(1, batch.searches_executed or 1)
                        block_rate = blocked_in_batch / searches_done
                        if block_rate > 0.10 and searches_done >= 5:
                            logger.critical(f"🚨 [SAFETY ALERT] Batch block rate ({block_rate:.1%}) exceeded safety threshold (10%). Pausing batch.")
                            batch.status = "PAUSED_SAFETY_THRESHOLD"
                            db.commit()
                            break

                        # Dispatch
                        await asyncio.to_thread(
                            self._dispatch_search_task,
                            query=query,
                            keyword=keyword,
                            domain=domain,
                            subdomain=subdomain,
                            batch_id=batch_id_str,
                        )

                    elif func_name == "discover_new_subdomain":
                        logger.info(f"[Agent] Discovered new subdomain: {args.get('new_subdomain')} in {args.get('domain')} because: {args.get('reason')}")
                        # Could persist this to a DynamicTaxonomy table.
                        db.commit()

                    elif func_name == "evaluate_batch":
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

                # Force batch evaluation if reached size
                if batch.searches_executed >= BATCH_SIZE and batch.status != "COMPLETED":
                     self._generate_batch_feedback(db, batch)
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

                # Continuous Haystack verification agent: analyze unverified crawled records one after another
                try:
                    from sqlalchemy import or_
                    unverified_recs = db.query(UniversalRecord).filter(
                        or_(UniversalRecord.status == "Discovered", UniversalRecord.status == "Raw Ingested", UniversalRecord.status == None)
                    ).limit(1).all()
                    if unverified_recs:
                        from app.worker.tasks import _safe_dispatch, enrich_and_verify_task
                        for u_rec in unverified_recs:
                            _safe_dispatch(enrich_and_verify_task, universal_record_id=u_rec.id)
                except Exception as sweep_err:
                    logger.warning(f"[Agent] Continuous verification sweep notice: {sweep_err}")

            except Exception as e:
                logger.error(f"[Agent] Loop iteration error: {e}", exc_info=True)
            finally:
                db.close()

            await asyncio.sleep(LOOP_PACE_SECONDS)

        logger.info("[Agent] Discovery loop exited cleanly.")

    def _pre_check_agent_instruction(self, db: Session, query: str, domain: str = "") -> Tuple[bool, Optional[str]]:
        """
        Code-level hard constraint pre-check function running before agent issues
        a crawl/search instruction. Does NOT rely on LLM self-censorship.
        """
        from app.safety.guardrails import is_domain_blocked, check_content_heuristics, add_to_blocklist
        if query and is_domain_blocked(db, query):
            return False, "database_blocklist"
        is_disallowed, category = check_content_heuristics(f"{query} {domain}")
        if is_disallowed:
            add_to_blocklist(db, query, reason_category=category, source="content_moderation")
            return False, f"heuristic_{category}"
        return True, None

    # ─── LLM Orchestration ─────────────────────────────────────────────────────

    def _build_agent_prompt(self, metrics: Dict[str, Any], batch: BatchResult) -> str:
        searches_exec = batch.searches_executed or 0
        prompt = f"""
        You are the OpenDB 24x7 Autonomous Discovery Agent.
        Your goal is to continuously discover companies, identify new subdomains, and orchestrate search strategies.
        
        CRITICAL MANDATORY SAFETY CONSTRAINTS:
        1. OBJECTIVE: Your objective is strictly identifying legitimate, registered commercial B2B companies, businesses, and organizations with a public web presence.
        2. DISALLOWED CATEGORIES: You must NEVER search for, pursue, evaluate, or reason about content in any of these categories:
           - Adult / sexual content / escort services / NSFW
           - Unlicensed gambling / casinos / betting / lotteries
           - Weapons / firearms / ammunition / darknet / illicit drugs / narcotics
           - Counterfeit goods / pirated media / torrents / warez / cracks
           - Phishing / malware / ransomware / keyloggers / botnets
           - Human trafficking / exploitation content
           - Extremist content / hate speech / terrorism
           - Sites requiring circumvention of access controls / darknet onion sites / bypass paywalls
        3. BLOCKLIST ROUTING: If any candidate query, domain, or website appears to fall into any of these disallowed categories, you must IMMEDIATELY reject it and route it to the blocklist mechanism. Do NOT analyze or evaluate it further.

        Current State:
        - Total Companies Discovered: {metrics.get('entities_discovered', 0)}
        - Verified Companies: {metrics.get('entities_verified', 0)}
        - Current Batch Progress: {searches_exec}/{BATCH_SIZE} searches.
        - Active Domain: {metrics.get('current_domain')}
        - Active Subdomain: {metrics.get('current_subdomain')}
        - Last Query Used: {metrics.get('current_keyword')}
        
        INSTRUCTIONS:
        1. If the batch progress is >= {BATCH_SIZE}, you MUST call 'evaluate_batch'.
        2. Otherwise, call 'search_web' with a new query variation to discover more companies. Use dynamic geographic or intent modifiers (e.g., 'SaaS companies Germany', 'top fintech startups Brazil').
        3. If you notice a gap in the taxonomy based on your knowledge, call 'discover_new_subdomain'.
        
        Decide your next action by calling a tool.
        """
        return prompt

    async def _invoke_llm_agent(self, prompt: str) -> List[Dict[str, Any]]:
        """Call the GPU Qwen LLM endpoint or LiteLLM and return tool calls."""
        api_key = getattr(settings, "OPENAI_API_KEY", "") or getattr(settings, "QWEN_API_KEY", "")
        base_url = getattr(settings, "OPENAI_BASE_URL", "http://115.244.46.68:8000/v1")
        model = getattr(settings, "LLM_MODEL", "current-model")

        if not api_key:
            return None

        # 1. Try OpenAI AsyncOpenAI client directly with configured base_url
        try:
            import openai
            client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": prompt}],
                tools=AGENT_TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=500,
                timeout=12.0
            )
            message = response.choices[0].message
            if hasattr(message, "tool_calls") and message.tool_calls:
                return [{"function": {"name": t.function.name, "arguments": t.function.arguments}} for t in message.tool_calls]
        except Exception as err:
            logger.debug(f"[Agent] Direct AsyncOpenAI GPU call error ({err}), trying LiteLLM fallback...")

        # 2. Try LiteLLM completion fallback
        if litellm:
            try:
                response = await asyncio.to_thread(
                    litellm.completion,
                    model=model,
                    api_key=api_key,
                    api_base=base_url,
                    messages=[{"role": "system", "content": prompt}],
                    tools=AGENT_TOOLS,
                    tool_choice="auto",
                    temperature=0.2,
                    max_tokens=500
                )
                message = response.choices[0].message
                if hasattr(message, "tool_calls") and message.tool_calls:
                    return [{"function": {"name": t.function.name, "arguments": t.function.arguments}} for t in message.tool_calls]
            except Exception as e:
                logger.warning(f"[Agent] LLM tool call failed: {e}. Falling back to deterministic strategy.")

        return None

    # ─── Task Dispatch ─────────────────────────────────────────────────────────

    def _dispatch_search_task(self, query: str, keyword: str, domain: str,
                               subdomain: str, batch_id: str):
        """Dispatch search task via safe background dispatch."""
        from app.worker.tasks import search_and_discover_task, _safe_dispatch
        _safe_dispatch(
            search_and_discover_task,
            query=query,
            keyword=keyword,
            domain=domain,
            subdomain=subdomain,
            batch_id=batch_id,
        )

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
        batch.completed_at = utc_now()
        batch.feedback_generated = True

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

        from sqlalchemy import or_
        from app.persistence.models import GlobalLead
        total_entities = db.query(GlobalLead).count() or db.query(UniversalRecord).count()
        verified_entities = db.query(GlobalLead).count() or db.query(UniversalRecord).filter(
            or_(UniversalRecord.status == "Verified", UniversalRecord.status == "Active")
        ).count()
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
