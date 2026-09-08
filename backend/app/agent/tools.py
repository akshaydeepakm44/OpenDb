"""
Haystack 2.x Formal Agent Tools — §2 & §11 of Master Rules

Formal tool definitions for the Haystack autonomous strategy agent:
- search_companies
- evaluate_search_results
- queue_company_for_crawl
- get_pipeline_metrics
- discover_new_subdomain
- analyse_batch
- modify_search_strategy
- inspect_infrastructure_health
"""
import logging
from typing import Dict, Any, List, Optional
from haystack.tools import Tool
from app.persistence.database import SessionLocal
from app.persistence.models import UniversalRecord, Document, CrawlActivityLog, BatchResult
from app.crawler.candidate_classifier import candidate_classifier
from app.crawler.deduplicator import pre_crawl_deduplicator

logger = logging.getLogger(__name__)

def get_pipeline_metrics_func() -> Dict[str, Any]:
    """Retrieve full pipeline operational metrics."""
    db = SessionLocal()
    try:
        total_discovered = db.query(UniversalRecord).count()
        verified_count = db.query(UniversalRecord).filter(UniversalRecord.status == "VERIFIED").count()
        raw_documents = db.query(Document).count()
        recent_logs = db.query(CrawlActivityLog).order_by(CrawlActivityLog.timestamp.desc()).limit(100).all()
        
        filtered_count = sum(1 for l in recent_logs if l.status == "FILTERED")
        queued_count = sum(1 for l in recent_logs if l.status in ("QUEUED", "QUEUED_FOR_CRAWL"))
        
        duplicate_rate = (filtered_count / len(recent_logs)) if recent_logs else 0.0
        
        return {
            "total_discovered": total_discovered,
            "verified_leads": verified_count,
            "raw_documents": raw_documents,
            "recent_activity_logs": len(recent_logs),
            "duplicate_rate": round(duplicate_rate, 4),
            "queued_tasks": queued_count,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


def search_companies_func(query: str, category: str = "general") -> Dict[str, Any]:
    """Execute targeted SearXNG discovery search."""
    from app.crawler.searxng_service import searxng_service
    from app.worker.tasks import run_async
    try:
        res = run_async(
            searxng_service.search_with_meta(query=query, category=category, max_results=15)
        )
        if hasattr(res, "__await__"):
            # Handle if returned coroutine inside existing loop
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Return fallback empty list if async event loop running in thread
                    return {"query": query, "results_found": 0, "results": []}
                results, is_fallback, msg = loop.run_until_complete(res)
            except Exception:
                return {"query": query, "results_found": 0, "results": []}
        else:
            results, is_fallback, msg = res[0], res[1], res[2]

        return {
            "query": query,
            "results_found": len(results),
            "is_fallback": is_fallback,
            "log_message": msg,
            "results": results[:5]  # Sample
        }
    except Exception as err:
        return {"query": query, "results_found": 0, "error": str(err), "results": []}


def queue_company_for_crawl_func(url: str, domain: str = "Technology") -> Dict[str, Any]:
    """Classify, deduplicate, and queue company URL for parallel crawling."""
    # 1. Candidate classification
    category, cat_reason, is_allowed = candidate_classifier.classify(url)
    if not is_allowed:
        return {"url": url, "status": "REJECTED", "reason": cat_reason, "category": category}

    # 2. Pre-crawl deduplication
    is_dup, canonical_domain, dup_reason = pre_crawl_deduplicator.is_duplicate(url)
    if is_dup:
        return {"url": url, "status": "DUPLICATE", "reason": dup_reason, "domain": canonical_domain}

    # 3. Dispatch to worker
    from app.worker.tasks import crawl_entity_task, _safe_dispatch
    _safe_dispatch(crawl_entity_task, url=url, domain=domain)
    
    # Mark in Redis
    pre_crawl_deduplicator.mark_as_crawled(canonical_domain)

    return {"url": url, "status": "QUEUED_FOR_CRAWL", "category": category, "domain": canonical_domain}


def inspect_infrastructure_health_func() -> Dict[str, Any]:
    """Check health status of all infrastructure services."""
    from app.api.health import services_health_check
    from app.persistence.database import SessionLocal
    db = SessionLocal()
    try:
        return services_health_check(db)
    finally:
        db.close()


def analyse_batch_func(batch_id: str = "") -> Dict[str, Any]:
    """Analyse batch yield and performance metrics."""
    metrics = get_pipeline_metrics_func()
    return {
        "batch_id": batch_id or "latest",
        "yield_rate": metrics.get("verified_leads", 0) / max(metrics.get("total_discovered", 1), 1),
        "duplicate_rate": metrics.get("duplicate_rate", 0.0),
        "recommendation": "Expand search strategy to underrepresented domains if duplicate rate > 0.30"
    }

# Formal Haystack Tool Instances
get_pipeline_metrics_tool = Tool(
    name="get_pipeline_metrics",
    description="Retrieve live pipeline statistics including discovered count, verified count, and duplicate rate.",
    parameters={"type": "object", "properties": {}},
    function=get_pipeline_metrics_func
)

search_companies_tool = Tool(
    name="search_companies",
    description="Execute a web discovery query to find company candidates.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "category": {"type": "string"}
        },
        "required": ["query"]
    },
    function=search_companies_func
)

queue_company_for_crawl_tool = Tool(
    name="queue_company_for_crawl",
    description="Classify, deduplicate, and enqueue a candidate URL for parallel crawling.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "domain": {"type": "string"}
        },
        "required": ["url"]
    },
    function=queue_company_for_crawl_func
)

inspect_infrastructure_health_tool = Tool(
    name="inspect_infrastructure_health",
    description="Inspect runtime status of Postgres, Redis, MinIO, SearXNG, Celery, and LLM.",
    parameters={"type": "object", "properties": {}},
    function=inspect_infrastructure_health_func
)

analyse_batch_tool = Tool(
    name="analyse_batch",
    description="Analyse discovery yield and quality metrics for the current execution batch.",
    parameters={
        "type": "object",
        "properties": {
            "batch_id": {"type": "string"}
        }
    },
    function=analyse_batch_func
)

HAYSTACK_TOOLS = [
    get_pipeline_metrics_tool,
    search_companies_tool,
    queue_company_for_crawl_tool,
    inspect_infrastructure_health_tool,
    analyse_batch_tool,
]
