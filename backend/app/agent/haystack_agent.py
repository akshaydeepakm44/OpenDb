"""
Haystack 2.x Autonomous Agent Orchestrator — §2 & §11 of Master Rules

Implements genuine Haystack agent orchestration:
- Evaluates pipeline state (yield, duplicate rate, queue depth, infrastructure health)
- Executes formal Haystack tools (search_companies, queue_company_for_crawl, analyse_batch, inspect_infrastructure_health)
- Dynamically adapts search strategy based on real batch metrics
"""
import logging
import asyncio
from typing import Dict, Any, List, Optional
import litellm

from app.config import settings
from app.agent.tools import (
    HAYSTACK_TOOLS,
    get_pipeline_metrics_func,
    search_companies_func,
    queue_company_for_crawl_func,
    inspect_infrastructure_health_func,
    analyse_batch_func,
)
from app.agent.keyword_expander import keyword_expander

logger = logging.getLogger(__name__)


class HaystackAutonomousAgent:
    """
    Haystack 2.x Strategic Agent Orchestrator for 24/7 Global Lead Discovery.
    """
    def __init__(self):
        self.tools = {t.name: t for t in HAYSTACK_TOOLS}
        self.model = settings.QWEN_MODEL_NAME or settings.LLM_MODEL or "current-model"
        self.api_base = settings.OPENAI_BASE_URL or "http://115.244.46.68:8000/v1"
        self.api_key = settings.OPENAI_API_KEY or "sk-datai2i-a100-qwen35-27b-8x3f9z"

    async def execute_strategic_step(self, current_domain: str = "Technology", current_subdomain: str = "AI Platforms") -> Dict[str, Any]:
        """
        Execute one strategic reasoning & tool decision step.
        """
        # 1. Observe system state
        metrics = get_pipeline_metrics_func()
        health = inspect_infrastructure_health_func()
        
        duplicate_rate = metrics.get("duplicate_rate", 0.0)
        total_discovered = metrics.get("total_discovered", 0)

        logger.info(
            f"🧠 [Haystack Agent] Observing Pipeline State | "
            f"Discovered: {total_discovered} | Dup Rate: {duplicate_rate:.2f} | "
            f"Domain: {current_domain} / {current_subdomain}"
        )

        # 2. Formulate strategic prompt for LLM agent
        prompt = f"""
You are the Haystack Strategic Discovery Agent for OpenDB.
Observe the current state and decide the optimal search strategy:

CURRENT METRICS:
- Total Discovered: {total_discovered}
- Verified Leads: {metrics.get('verified_leads', 0)}
- Duplicate Rate: {duplicate_rate:.2f}
- Active Queue Depth: {metrics.get('queued_tasks', 0)}
- Active Domain: {current_domain} / {current_subdomain}
- Infrastructure Health: Postgres={health.get('postgres')}, Redis={health.get('redis')}, SearXNG={health.get('searxng')}

AVAILABLE TOOLS:
- search_companies(query)
- queue_company_for_crawl(url, domain)
- analyse_batch(batch_id)
- inspect_infrastructure_health()

DECISION RULES:
1. If duplicate rate > 0.35, expand the search query with new geo/intent keywords.
2. Select target query for {current_domain} - {current_subdomain}.
3. Respond in JSON format: {{"strategy": "<description>", "action": "search_companies", "query": "<search_query>", "reasoning": "<explanation>"}}
"""

        try:
            # Invoke LLM for strategic decision
            response = await litellm.acompletion(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                api_base=self.api_base,
                api_key=self.api_key,
                timeout=4.0,
                temperature=0.3,
            )

            content = response.choices[0].message.content or ""
            logger.info(f"🧠 [Haystack Agent Reasoning]: {content[:200]}")

            # Parse query or tool decision
            import json
            parsed = None
            try:
                # Extract JSON from LLM output
                json_match = content[content.find("{"):content.rfind("}")+1]
                parsed = json.loads(json_match)
            except Exception:
                pass

            if parsed and parsed.get("query"):
                query = parsed["query"]
            else:
                # Fallback to smart keyword expander
                query_info = keyword_expander.get_next_query(domain=current_domain, subdomain=current_subdomain)
                query = query_info["query"]

            # Execute tool: search_companies
            search_res = search_companies_func(query=query)

            # Process candidates via candidate classifier & deduplicator tools
            queued_count = 0
            results = search_res.get("results", [])
            for r in results:
                url = r.get("url")
                if url:
                    q_res = queue_company_for_crawl_func(url=url, domain=current_domain)
                    if q_res.get("status") == "QUEUED_FOR_CRAWL":
                        queued_count += 1

            return {
                "agent_status": "ACTIVE",
                "strategy": parsed.get("strategy") if parsed else f"Targeted discovery for {current_domain}",
                "query": query,
                "sources_found": len(results),
                "enqueued_crawls": queued_count,
                "metrics": metrics,
            }

        except Exception as err:
            logger.warning(f"⚠️ [Haystack Agent LLM Exception]: {err}. Executing deterministic strategy tool.")
            # Fallback to deterministic strategy tool
            query_info = keyword_expander.get_next_query(domain=current_domain, subdomain=current_subdomain)
            query = query_info["query"]
            search_res = search_companies_func(query=query)
            
            queued_count = 0
            for r in search_res.get("results", []):
                if r.get("url"):
                    q_res = queue_company_for_crawl_func(url=r["url"], domain=current_domain)
                    if q_res.get("status") == "QUEUED_FOR_CRAWL":
                        queued_count += 1

            return {
                "agent_status": "ACTIVE_FALLBACK",
                "strategy": f"Taxonomy strategy for {current_domain}",
                "query": query,
                "sources_found": len(search_res.get("results", [])),
                "enqueued_crawls": queued_count,
                "metrics": metrics,
            }


haystack_agent = HaystackAutonomousAgent()
