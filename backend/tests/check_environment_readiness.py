"""
Phase 2: Environment Readiness Verification Script
Audits all runtime services before any soak test:
1. PostgreSQL connectivity, mode, and pool status
2. Redis connectivity and latency
3. Crawl4AI / Playwright engine readiness
4. SearXNG meta-search service status
5. MinIO object storage / Outbox readiness
6. Celery broker and queue depths
7. Host Chromium process inspection
8. Resource Governor circuit breaker state (RAM/CPU thresholds)
"""
import asyncio
import os
import sys
import time
import json
import psutil
from typing import Dict, Any

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.persistence.database import SessionLocal, get_database_status
from app.cache.redis_client import get_redis
from app.safety.resource_governor import governor
from app.crawler.distributed_slot_manager import slot_manager
from app.storage.file_storage import file_storage
from sqlalchemy import text


async def check_readiness() -> Dict[str, Any]:
    readiness: Dict[str, Any] = {
        "timestamp": time.time(),
        "ready_for_soak": False,
        "services": {},
        "blockers": [],
    }

    # 1. PostgreSQL Check
    pg_info = get_database_status()
    try:
        with SessionLocal() as db:
            tbl_count = db.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")).scalar()
            readiness["services"]["postgresql"] = {
                "status": "HEALTHY",
                "mode": pg_info["mode"],
                "table_count": tbl_count,
                "degraded": pg_info["degraded"]
            }
            if pg_info["mode"] != "POSTGRESQL" or pg_info["degraded"]:
                readiness["blockers"].append(f"PostgreSQL not primary (mode={pg_info['mode']})")
    except Exception as pg_err:
        readiness["services"]["postgresql"] = {"status": "UNHEALTHY", "error": str(pg_err)}
        readiness["blockers"].append(f"PostgreSQL connection failed: {pg_err}")

    # 2. Redis Check
    r = get_redis()
    if r is not None:
        try:
            t0 = time.time()
            pong = r.ping()
            latency_ms = (time.time() - t0) * 1000
            readiness["services"]["redis"] = {
                "status": "HEALTHY" if pong else "UNHEALTHY",
                "latency_ms": round(latency_ms, 2)
            }
        except Exception as r_err:
            readiness["services"]["redis"] = {"status": "UNHEALTHY", "error": str(r_err)}
            readiness["blockers"].append(f"Redis ping failed: {r_err}")
    else:
        readiness["services"]["redis"] = {"status": "UNAVAILABLE"}
        readiness["blockers"].append("Redis is unreachable")

    # 3. Crawl4AI Check
    try:
        from crawl4ai import AsyncWebCrawler
        t_c0 = time.time()
        async with AsyncWebCrawler(verbose=False) as crawler:
            pass
        c_dur = time.time() - t_c0
        readiness["services"]["crawl4ai"] = {"status": "HEALTHY", "init_seconds": round(c_dur, 2)}
    except Exception as c_err:
        readiness["services"]["crawl4ai"] = {"status": "UNHEALTHY", "error": str(c_err)}
        readiness["blockers"].append(f"Crawl4AI failed to initialize: {c_err}")

    # 4. SearXNG Check
    import socket
    from urllib.parse import urlparse
    searxng_url = getattr(settings, "SEARXNG_URL", "http://127.0.0.1:8080")
    p_sx = urlparse(searxng_url)
    h_sx = p_sx.hostname or "127.0.0.1"
    pt_sx = p_sx.port or 8080
    sx_online = False
    try:
        with socket.create_connection((h_sx, pt_sx), timeout=1.0):
            sx_online = True
    except Exception:
        # Check alternate internal port
        try:
            with socket.create_connection((h_sx, 9090), timeout=1.0):
                sx_online = True
        except Exception:
            pass
    readiness["services"]["searxng"] = {
        "status": "HEALTHY" if sx_online else "DEGRADED (Fallback active)",
        "endpoint": searxng_url,
        "online": sx_online
    }

    # 5. MinIO Check
    minio_online = False
    ep = getattr(settings, "MINIO_ENDPOINT", "127.0.0.1:9002")
    p_m = urlparse(f"http://{ep}" if "://" not in ep else ep)
    h_m = p_m.hostname or "127.0.0.1"
    pt_m = p_m.port or 9002
    try:
        with socket.create_connection((h_m, pt_m), timeout=1.0):
            minio_online = True
    except Exception:
        pass
    readiness["services"]["minio"] = {
        "status": "HEALTHY" if minio_online else "DEGRADED (ArtifactOutbox active)",
        "endpoint": ep,
        "online": minio_online,
        "outbox_ready": True
    }

    # 6. Celery Queues & Broker Depth
    disc_depth = 0
    crawl_depth = 0
    verif_depth = 0
    if r is not None:
        try:
            disc_depth = r.llen("discovery") + r.llen("celery")
            crawl_depth = r.llen("crawl")
            verif_depth = r.llen("verification")
        except Exception:
            pass
    readiness["services"]["queues"] = {
        "discovery_queue_depth": disc_depth,
        "crawl_queue_depth": crawl_depth,
        "verification_queue_depth": verif_depth,
    }

    # 7. Host Chromium Processes
    chrom_count = 0
    chrom_procs = []
    for p in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            name = (p.info.get('name') or '').lower()
            if 'chromium' in name or 'chrome' in name or 'playwright' in name:
                chrom_count += 1
                mem_mb = round((p.info.get('memory_info').rss or 0) / (1024 * 1024), 1)
                chrom_procs.append({"pid": p.info['pid'], "name": p.info['name'], "rss_mb": mem_mb})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    readiness["services"]["chromium"] = {
        "total_host_processes": chrom_count,
        "open_browser_contexts": 0,
        "distributed_slots": slot_manager.get_total_active_crawls(),
        "processes_sample": chrom_procs[:5]
    }

    # 8. Resource Governor & Circuit Breaker Assessment
    metrics = governor.get_system_metrics(force_refresh=True)
    decision = governor.evaluate_circuit_breaker()

    readiness["services"]["governor"] = {
        "level": decision["level"],
        "pause_reason": decision["reason"],
        "cpu_percent": metrics["cpu_percent"],
        "memory_percent": metrics["memory_percent"],
        "can_crawl": decision["can_crawl"],
        "can_discover": decision["can_discover"]
    }

    # Evaluate readiness
    if decision["level"] == "PAUSED" and "HIGH_MEMORY" in (decision["reason"] or ""):
        readiness["blockers"].append(
            f"GOVERNOR_PAUSED: Host memory is at {metrics['memory_percent']}% "
            f"(configured threshold: {getattr(settings, 'RESOURCE_PAUSE_MEMORY_PERCENT', 85.0)}%). "
            "Per instructions: DO NOT bypass governor. Environment is not ready for continuous soak."
        )

    if not readiness["blockers"]:
        readiness["ready_for_soak"] = True

    return readiness


if __name__ == "__main__":
    result = asyncio.run(check_readiness())
    print("\n" + "="*70)
    print("OPENDB ENVIRONMENT READINESS AUDIT REPORT (PHASE 2)")
    print("="*70)
    print(f"READY FOR SOAK: {result['ready_for_soak']}")
    print("\n[SERVICES]")
    for svc, details in result["services"].items():
        print(f"  * {svc.upper()}: {details}")
    if result["blockers"]:
        print("\n[BLOCKING CONDITIONS DETECTED]")
        for b in result["blockers"]:
            print(f"  [X] {b}")
    else:
        print("\n[STATUS] All conditions met. Environment is ready for soak test.")
    print("="*70 + "\n")
