"""
Real-Time Event Architecture — §7 of Master Rules

Emits real pipeline events across the entire autonomous system:
Worker -> Pipeline Event -> Redis PubSub (`opendb:pipeline_events`) -> WebSocket (`ws_manager`) -> React Dashboard.

No simulated counters, no hardcoded logs, no randomly generated activity.
"""
import json
import logging
import time
from typing import Dict, Any, Optional

from app.cache.redis_client import get_redis
from app.api.websocket import ws_manager

logger = logging.getLogger(__name__)

REDIS_PUBSUB_CHANNEL = "opendb:pipeline_events"

class EventType:
    AGENT_STARTED = "AGENT_STARTED"
    SEARCH_STARTED = "SEARCH_STARTED"
    SEARCH_RESULTS_FOUND = "SEARCH_RESULTS_FOUND"
    URL_CLASSIFIED = "URL_CLASSIFIED"
    URL_REJECTED = "URL_REJECTED"
    DOMAIN_DEDUPLICATED = "DOMAIN_DEDUPLICATED"
    CRAWL_QUEUED = "CRAWL_QUEUED"
    CRAWL_STARTED = "CRAWL_STARTED"
    CRAWL_COMPLETED = "CRAWL_COMPLETED"
    RAW_INGESTED = "RAW_INGESTED"
    BATCH_STARTED = "BATCH_STARTED"
    ENRICHMENT_STARTED = "ENRICHMENT_STARTED"
    PLAYWRIGHT_VERIFIED = "PLAYWRIGHT_VERIFIED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    COMPANY_VERIFIED = "COMPANY_VERIFIED"
    POSTGRES_SYNC_STARTED = "POSTGRES_SYNC_STARTED"
    POSTGRES_SYNC_COMPLETED = "POSTGRES_SYNC_COMPLETED"
    COMPANY_REJECTED = "COMPANY_REJECTED"
    PIPELINE_ERROR = "PIPELINE_ERROR"


def publish_pipeline_event(event_type: str, payload: Dict[str, Any], entity_url: str = "", domain: str = ""):
    """
    Publish real pipeline event to Redis PubSub and WebSocket stream.
    """
    event = {
        "event_type": event_type,
        "timestamp": time.time(),
        "url": entity_url,
        "domain": domain,
        "payload": payload
    }
    
    # 1. Publish to Redis PubSub for multi-worker synchronization
    try:
        r = get_redis()
        if r is not None:
            r.publish(REDIS_PUBSUB_CHANNEL, json.dumps(event))
    except Exception as err:
        logger.debug(f"[EventBus] Redis publish error: {err}")

    # 2. Broadcast directly via WebSocket manager if event loop active
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(ws_manager.broadcast(event))
    except Exception:
        pass


event_publisher = publish_pipeline_event
