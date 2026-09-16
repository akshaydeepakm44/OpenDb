"""
Phase 3: 30-Minute Continuous Autonomous Staging Soak Runner
Runs the full autonomous OpenDB discovery -> crawl -> extraction -> verification pipeline.
Records comprehensive telemetry metrics every 15 seconds to a JSON log file:
- timestamp
- CPU %
- memory %
- Chromium process count
- active standard crawl slots
- active deep crawl slots
- active browser contexts
- discovery queue depth
- verification queue depth
- crawl success count
- crawl failure count
- PostgreSQL health
- Redis health
- MinIO health
- pending ArtifactOutbox count
- Agent 1 state
- Agent 2 state
"""
import asyncio
import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List
import psutil

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.persistence.database import SessionLocal, get_database_status
from app.persistence.models import (
    Document, UniversalRecord, AgentState, ArtifactOutbox,
    Agent2VerificationSession, CrawlActivityLog, utc_now
)
from app.safety.resource_governor import governor
from app.crawler.distributed_slot_manager import slot_manager
from app.crawler.crawler_service import get_active_browser_contexts_count
from app.cache.redis_client import get_redis
from app.agent.discovery_agent import discovery_agent
from app.storage.file_storage import file_storage

logger = logging.getLogger(__name__)


def count_chromium_processes() -> int:
    count = 0
    for p in psutil.process_iter(['name', 'cmdline', 'status']):
        try:
            if p.info.get('status') == psutil.STATUS_ZOMBIE:
                continue
            name = (p.info.get('name') or '').lower()
            cmd = ' '.join(p.info.get('cmdline') or []).lower()
            if 'chromium' in name or 'chrome' in name or 'playwright' in cmd:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return count


async def run_soak_test(duration_seconds: int = 1800, sample_interval: int = 15):
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    metrics_log_file = os.path.join(log_dir, f"soak_metrics_{int(time.time())}.jsonl")

    print(f"\n" + "="*70)
    print(f"STARTING OPENDB AUTONOMOUS STAGING SOAK TEST")
    print(f"Duration: {duration_seconds} seconds ({duration_seconds/60:.1f} minutes)")
    print(f"Telemetry interval: {sample_interval} seconds")
    print(f"Metrics output: {metrics_log_file}")
    print("="*70 + "\n")

    # Initial baseline audit
    baseline_chromium = count_chromium_processes()
    print(f"Baseline Chromium host processes: {baseline_chromium}")

    # Check governor before driving workload
    initial_circuit = governor.evaluate_circuit_breaker()
    if initial_circuit["level"] == "PAUSED" and "HIGH_MEMORY" in (initial_circuit["reason"] or ""):
        print(f"❌ Cannot start soak test: Resource Governor is PAUSED ({initial_circuit['reason']}).")
        print("Per instructions: DO NOT bypass governor. Environment is not ready.")
        return

    # Activate Real Autonomous Pipeline
    print("🚀 Activating Agent 1 autonomous discovery pipeline...")
    discovery_agent.set_status("RUNNING")

    start_time = time.time()
    end_time = start_time + duration_seconds
    cycle = 0

    success_count = 0
    failure_count = 0

    try:
        while time.time() < end_time:
            cycle += 1
            now = time.time()
            elapsed = now - start_time

            # 1. System & Process Metrics
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=None)
            chrom_count = count_chromium_processes()

            # 2. OpenDB Engine Metrics
            contexts = get_active_browser_contexts_count()
            slots_info = slot_manager.get_total_active_crawls()
            circuit = governor.evaluate_circuit_breaker()

            # 3. Redis Queue Depths
            r = get_redis()
            disc_depth = 0
            crawl_depth = 0
            verif_depth = 0
            redis_ok = False
            if r is not None:
                try:
                    disc_depth = r.llen("discovery") + r.llen("celery")
                    crawl_depth = r.llen("crawl")
                    verif_depth = r.llen("verification")
                    redis_ok = r.ping()
                except Exception:
                    pass

            # 4. PostgreSQL Persistence & Outbox Metrics
            pg_status = "UNHEALTHY"
            pending_outbox = 0
            agent1_status = "UNKNOWN"
            agent2_pending = 0
            agent2_verified = 0

            try:
                with SessionLocal() as db:
                    db_info = get_database_status()
                    pg_status = db_info["status"]
                    pending_outbox = db.query(ArtifactOutbox).filter(ArtifactOutbox.status.in_(["PENDING", "RETRY"])).count()
                    ag_state = db.query(AgentState).first()
                    if ag_state:
                        agent1_status = ag_state.status
                    agent2_pending = db.query(Document).filter(Document.lifecycle_state == "CRAWLED_PENDING_AGENT_2").count()
                    agent2_verified = db.query(Agent2VerificationSession).filter(Agent2VerificationSession.status.in_(["VERIFIED", "POSTGRES_VERIFIED"])).count()

                    # Calculate success / failure from CrawlActivityLog in last interval
                    recent_logs = db.query(CrawlActivityLog).filter(
                        CrawlActivityLog.timestamp >= datetime.fromtimestamp(now - sample_interval, tz=timezone.utc)
                    ).all()
                    for l in recent_logs:
                        if l.status in ("OK", "SUCCESS", "QUALIFIED"):
                            success_count += 1
                        elif l.status in ("ERROR", "FAILED"):
                            failure_count += 1
            except Exception as db_err:
                pg_status = f"ERROR: {db_err}"

            # 5. MinIO Object Storage
            minio_ok = file_storage.client is not None and not file_storage.is_degraded

            telemetry_point = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(elapsed, 1),
                "cpu_percent": round(cpu, 1),
                "memory_percent": round(mem.percent, 1),
                "memory_available_mb": round(mem.available / (1024 * 1024), 1),
                "chromium_process_count": chrom_count,
                "baseline_chromium": baseline_chromium,
                "active_browser_contexts": contexts,
                "active_standard_slots": slots_info["standard_active"],
                "active_deep_slots": slots_info["deep_active"],
                "total_active_slots": slots_info["total_active"],
                "discovery_queue_depth": disc_depth,
                "crawl_queue_depth": crawl_depth,
                "verification_queue_depth": verif_depth,
                "crawl_success_count": success_count,
                "crawl_failure_count": failure_count,
                "postgresql_status": pg_status,
                "redis_status": "HEALTHY" if redis_ok else "UNAVAILABLE",
                "minio_status": "HEALTHY" if minio_ok else "DEGRADED (Outbox active)",
                "pending_artifact_outbox_count": pending_outbox,
                "governor_level": circuit["level"],
                "governor_reason": circuit["reason"],
                "agent1_state": agent1_status,
                "agent2_state": "ACTIVE" if (agent2_pending > 0 or verif_depth > 0) else "IDLE",
                "agent2_pending_count": agent2_pending,
                "agent2_verified_count": agent2_verified,
            }

            # Write to JSONL log
            with open(metrics_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(telemetry_point) + "\n")

            # Console heartbeat
            print(
                f"[{telemetry_point['timestamp'][:19]}] "
                f"Elapsed: {elapsed/60:.1f}m | "
                f"CPU: {telemetry_point['cpu_percent']}% | "
                f"RAM: {telemetry_point['memory_percent']}% | "
                f"Chromium: {chrom_count} (base: {baseline_chromium}) | "
                f"Contexts: {contexts} | "
                f"Slots: [std={slots_info['standard_active']}/2, deep={slots_info['deep_active']}/1] | "
                f"Gov: {circuit['level']} ({circuit['reason'] or 'OK'}) | "
                f"Queues: [disc={disc_depth}, crawl={crawl_depth}, verif={verif_depth}] | "
                f"Crawled: [ok={success_count}, fail={failure_count}]"
            )

            # Check circuit breaker: if governor paused, log notice
            if not circuit["can_crawl"] or not circuit["can_discover"]:
                print(f"  --> System throttled by ResourceGovernor: {circuit['reason']}")

            await asyncio.sleep(sample_interval)

    finally:
        # Bounded Cleanup: ensure Agent 1 is paused on completion, error, or cancellation
        print("\n🛑 Stopping Agent 1 autonomous discovery pipeline...")
        discovery_agent.set_status("PAUSED")
        # Cleanup any potential stale leases in Redis
        try:
            slot_manager.cleanup_stale_leases()
        except Exception:
            pass
        # Allow in-flight tasks a moment to settle
        await asyncio.sleep(2.0)

    final_chromium = count_chromium_processes()
    final_slots = slot_manager.get_total_active_crawls()

    print("\n" + "="*70)
    print("SOAK TEST RUN COMPLETE")
    print(f"Total Duration: {(time.time() - start_time)/60:.1f} minutes")
    print(f"Baseline Chromium: {baseline_chromium} -> Final Chromium: {final_chromium}")
    print(f"Final Active Browser Contexts: {get_active_browser_contexts_count()}")
    print(f"Final Active Distributed Slots: {final_slots}")
    print(f"Total Crawl Successes: {success_count}, Failures: {failure_count}")
    print(f"Detailed log written to: {metrics_log_file}")
    print("="*70 + "\n")


if __name__ == "__main__":
    duration = 1800  # 30 minutes default
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            pass
    asyncio.run(run_soak_test(duration_seconds=duration))
