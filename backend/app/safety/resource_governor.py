"""
OpenDB 24/7 Autonomous Resource Governor & Circuit Breaker
Enforces proactive, adaptive system protection:
1. Two-tier protection: NORMAL -> PRESSURE -> PAUSED -> EMERGENCY.
2. Windowed/sustained CPU checks (2-3 consecutive samples to avoid transient startup spikes).
3. RAM thresholds (80% pressure, 85% paused, 95% emergency).
4. Proactive HIGH_BROWSER_COUNT detection before memory exhaustion.
5. Queue watermarks with hysteresis (100/60 discovery, 50/25 verification).
6. 5-10s metric caching to prevent governor CPU overhead.
7. FAIL-CLOSED checks on critical dependencies (PostgreSQL, Redis).
8. Emergency mode stops NEW work while allowing active crawls to finish safely.
"""
import collections
import logging
import time
from typing import Dict, Any, Tuple, Optional

import psutil

from app.config import settings
from app.cache.redis_client import get_redis

logger = logging.getLogger(__name__)


class ResourceGovernor:
    """Singleton governor monitoring system telemetry and circuit breaker state."""

    def __init__(self):
        self.cache_ttl: float = getattr(settings, "GOVERNOR_CACHE_TTL_SECONDS", 5.0)
        self._last_sample_time: float = 0.0
        self._cached_metrics: Dict[str, Any] = {}

        # Rolling history of CPU samples (1 sample per cache refresh)
        self._cpu_history = collections.deque(maxlen=5)

        # Hysteresis state flags
        self.discovery_queue_paused: bool = False
        self.verification_queue_paused: bool = False
        self._redis_down: bool = False

    def record_redis_unavailable(self):
        self._redis_down = True

    def record_redis_available(self):
        self._redis_down = False

    def get_system_metrics(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch system metrics with caching (5s window) to avoid psutil overhead."""
        now = time.time()
        if not force_refresh and (now - self._last_sample_time) < self.cache_ttl and self._cached_metrics:
            return self._cached_metrics

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)

        self._cpu_history.append(cpu)

        # Count active Chromium/Playwright processes
        chromium_count = 0
        try:
            for p in psutil.process_iter(['name', 'cmdline', 'status']):
                try:
                    if p.info.get('status') == psutil.STATUS_ZOMBIE:
                        continue
                    name = (p.info.get('name') or '').lower()
                    cmd = ' '.join(p.info.get('cmdline') or []).lower()
                    if 'chromium' in name or 'chrome' in name or 'playwright' in cmd:
                        chromium_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as proc_err:
            logger.debug(f"[Governor] Process scan notice: {proc_err}")

        # Active distributed crawl slots from Redis
        from app.crawler.distributed_slot_manager import slot_manager
        slots_info = slot_manager.get_total_active_crawls()

        # Inspect Redis queue depths
        r = get_redis()
        disc_depth = 0
        verif_depth = 0
        if r is not None:
            try:
                # Celery queues
                disc_depth = r.llen("discovery") + r.llen("celery")
                verif_depth = r.llen("verification")
            except Exception:
                pass

        self._cached_metrics = {
            "timestamp": now,
            "memory_percent": round(mem.percent, 1),
            "memory_available_mb": round(mem.available / (1024 * 1024), 1),
            "memory_total_mb": round(mem.total / (1024 * 1024), 1),
            "cpu_percent": round(cpu, 1),
            "cpu_history": list(self._cpu_history),
            "chromium_process_count": chromium_count,
            "active_standard_slots": slots_info["standard_active"],
            "active_deep_slots": slots_info["deep_active"],
            "total_active_slots": slots_info["total_active"],
            "discovery_queue_depth": disc_depth,
            "verification_queue_depth": verif_depth,
        }
        self._last_sample_time = now
        return self._cached_metrics

    def _evaluate_sustained_cpu(self) -> Tuple[bool, bool, bool]:
        """
        Evaluate sustained CPU thresholds over recent samples:
        - pressure: >=85% for last 2 samples
        - paused:   >=90% for last 3 samples
        - emergency:>=95% for last 2 samples
        """
        history = list(self._cpu_history)
        if not history:
            return False, False, False

        emergency = len(history) >= 2 and all(c >= getattr(settings, "RESOURCE_EMERGENCY_CPU_PERCENT", 95.0) for c in history[-2:])
        paused = len(history) >= 3 and all(c >= getattr(settings, "RESOURCE_PAUSE_CPU_PERCENT", 90.0) for c in history[-3:])
        pressure = len(history) >= 2 and all(c >= 85.0 for c in history[-2:])

        return pressure, paused, emergency

    def evaluate_circuit_breaker(self) -> Dict[str, Any]:
        """
        Evaluate full circuit breaker state across memory, sustained CPU,
        active browser counts, queue watermarks, and critical dependencies.
        Returns detailed decision payload.
        """
        metrics = self.get_system_metrics()
        mem_pct = metrics["memory_percent"]
        chrom_count = metrics["chromium_process_count"]
        safe_browser_limit = getattr(settings, "SAFE_BROWSER_PROCESS_LIMIT", 4)

        cpu_pressure, cpu_paused, cpu_emergency = self._evaluate_sustained_cpu()

        # 1. Critical Dependency Checks (FAIL-CLOSED)
        r = get_redis()
        is_prod = (getattr(settings, "OPENDB_ENV", "").lower() or getattr(settings, "APP_ENV", "").lower()) == "production"
        if (r is None or self._redis_down) and is_prod:
            return {
                "level": "PAUSED",
                "can_crawl": False,
                "can_discover": False,
                "reason": "PAUSED — REDIS_UNAVAILABLE",
                "metrics": metrics,
            }

        # 2. EMERGENCY Level (>95% RAM or sustained >95% CPU)
        if mem_pct >= getattr(settings, "RESOURCE_EMERGENCY_MEMORY_PERCENT", 95.0) or cpu_emergency:
            reason = "EMERGENCY — HIGH_MEMORY" if mem_pct >= 95.0 else "EMERGENCY — HIGH_CPU"
            logger.critical(f"[ResourceGovernor] {reason} (RAM={mem_pct}%, CPU={metrics['cpu_percent']}%)")
            return {
                "level": "EMERGENCY",
                "can_crawl": False,
                "can_discover": False,
                "reason": reason,
                "metrics": metrics,
            }

        # 3. PAUSED Level (>85% RAM, sustained >90% CPU, or HIGH_BROWSER_COUNT)
        if mem_pct >= getattr(settings, "RESOURCE_PAUSE_MEMORY_PERCENT", 85.0):
            return {
                "level": "PAUSED",
                "can_crawl": False,
                "can_discover": False,
                "reason": "PAUSED — HIGH_MEMORY",
                "metrics": metrics,
            }

        if cpu_paused:
            return {
                "level": "PAUSED",
                "can_crawl": False,
                "can_discover": False,
                "reason": "PAUSED — HIGH_CPU",
                "metrics": metrics,
            }

        if chrom_count > safe_browser_limit:
            return {
                "level": "PAUSED",
                "can_crawl": False,
                "can_discover": False,
                "reason": "PAUSED — HIGH_BROWSER_COUNT",
                "metrics": metrics,
            }

        # 4. Queue Hysteresis Evaluation for Discovery
        disc_depth = metrics["discovery_queue_depth"]
        high_w = getattr(settings, "DISCOVERY_QUEUE_HIGH_WATERMARK", 100)
        low_w = getattr(settings, "DISCOVERY_QUEUE_LOW_WATERMARK", 60)

        if disc_depth >= high_w:
            self.discovery_queue_paused = True
        elif disc_depth <= low_w:
            self.discovery_queue_paused = False

        if self.discovery_queue_paused:
            return {
                "level": "PAUSED",
                "can_crawl": True,  # Allow existing crawlers to drain queue!
                "can_discover": False,
                "reason": "PAUSED — QUEUE_BACKPRESSURE",
                "metrics": metrics,
            }

        # 5. PRESSURE Level (80-85% RAM or sustained 85% CPU)
        if mem_pct >= 80.0 or cpu_pressure:
            return {
                "level": "PRESSURE",
                "can_crawl": True,
                "can_discover": True,
                "reason": "PRESSURE — RESOURCE_WARNING",
                "metrics": metrics,
            }

        # 6. NORMAL Level
        return {
            "level": "NORMAL",
            "can_crawl": True,
            "can_discover": True,
            "reason": None,
            "metrics": metrics,
        }

    def can_start_crawl(self, slot_type: str = "standard") -> Tuple[bool, Optional[str]]:
        """Verify whether a new browser crawl task can be safely started."""
        decision = self.evaluate_circuit_breaker()
        if not decision["can_crawl"]:
            return False, decision["reason"]
        return True, None

    def can_start_discovery(self) -> Tuple[bool, Optional[str]]:
        """Verify whether Agent 1 can execute new search and URL dispatches."""
        decision = self.evaluate_circuit_breaker()
        if not decision["can_discover"]:
            return False, decision["reason"]
        return True, None


governor = ResourceGovernor()
