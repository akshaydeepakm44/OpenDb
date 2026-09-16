"""
Distributed Crawl Slot Manager for OpenDB
Provides distributed, fail-closed concurrency boundaries across all Celery workers and containers:
- Standard Pool: 'opendb:crawler:slots:standard' (MAX_CONCURRENT_CRAWLS = 2)
- Deep Pool: 'opendb:crawler:slots:deep' (MAX_CONCURRENT_DEEP_CRAWLS = 1)

Guarantees:
1. Atomic acquisition via Redis sorted sets with timestamped leases.
2. Automatic reclamation of stale/expired leases (crashed worker recovery).
3. FAIL-CLOSED IN PRODUCTION: If Redis is unavailable, refuses new browser work.
4. Guaranteed release in finally blocks.
5. Atomic telemetry on active browser context/slot counts.
"""
import asyncio
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from app.config import settings
from app.cache.redis_client import get_redis

logger = logging.getLogger(__name__)

SLOT_KEY_PREFIX = "opendb:crawler:slots:"

# In-process semaphores as secondary per-process safety boundaries
_LOCAL_SEMAPHORES: Dict[Any, asyncio.Semaphore] = {}


def _get_local_semaphore(slot_type: str, max_slots: int) -> asyncio.Semaphore:
    global _LOCAL_SEMAPHORES
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    key = (slot_type, loop)
    if key not in _LOCAL_SEMAPHORES:
        _LOCAL_SEMAPHORES[key] = asyncio.Semaphore(max_slots)
    return _LOCAL_SEMAPHORES[key]


class DistributedSlotManager:
    """Manages global browser crawl slots across all worker nodes."""

    def __init__(self):
        self.lease_timeout = getattr(settings, "CRAWL_TIMEOUT_SECONDS", 60) + 30

    def _get_pool_config(self, slot_type: str) -> tuple[str, int]:
        slot_type = slot_type.lower()
        if slot_type == "deep":
            return (
                f"{SLOT_KEY_PREFIX}deep",
                getattr(settings, "MAX_CONCURRENT_DEEP_CRAWLS", 1),
            )
        return (
            f"{SLOT_KEY_PREFIX}standard",
            getattr(settings, "MAX_CONCURRENT_CRAWLS", 2),
        )

    def is_production(self) -> bool:
        env = getattr(settings, "OPENDB_ENV", "").lower() or getattr(settings, "APP_ENV", "").lower()
        return env == "production"

    def acquire_slot_sync(
        self,
        slot_type: str = "standard",
        wait_timeout: float = 60.0,
        cancel_event: Optional[threading.Event] = None
    ) -> str:
        """
        Synchronous slot acquisition using Redis with lease expiration.
        Returns unique lease token on success; raises exception on failure/timeout.
        """
        r = get_redis()
        is_prod = self.is_production()

        if r is None:
            if is_prod:
                from app.safety.resource_governor import governor
                governor.record_redis_unavailable()
                raise RuntimeError("PAUSED — REDIS_UNAVAILABLE: Cannot acquire distributed crawl slot in PRODUCTION mode.")
            logger.warning("[DistributedSlotManager] Redis unavailable in dev mode; proceeding with local lock.")
            return f"local-dev-{uuid.uuid4()}"

        redis_key, max_slots = self._get_pool_config(slot_type)
        lease_id = f"{slot_type}-{uuid.uuid4()}"
        deadline = time.time() + wait_timeout

        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Slot acquisition cancelled")

            try:
                now = time.time()
                expired_threshold = now - self.lease_timeout

                # Atomic cleanup of expired leases using pipeline
                pipe = r.pipeline(transaction=True)
                pipe.zremrangebyscore(redis_key, "-inf", expired_threshold)
                pipe.zcard(redis_key)
                _, active_count = pipe.execute()

                if active_count < max_slots:
                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError("Slot acquisition cancelled")

                    # Attempt to register lease
                    registered = r.zadd(redis_key, {lease_id: now}, nx=True)
                    if registered:
                        if cancel_event is not None and cancel_event.is_set():
                            r.zrem(redis_key, lease_id)
                            raise asyncio.CancelledError("Slot acquisition cancelled")

                        # Double-check we didn't exceed due to race condition
                        current_card = r.zcard(redis_key)
                        if current_card <= max_slots:
                            from app.safety.resource_governor import governor
                            governor.record_redis_available()
                            logger.info(
                                f"[DistributedSlotManager] Acquired '{slot_type}' crawl slot '{lease_id}' "
                                f"({current_card}/{max_slots} active)"
                            )
                            return lease_id
                        else:
                            # Revert over-allocation
                            r.zrem(redis_key, lease_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[DistributedSlotManager] Redis error during slot acquisition: {e}")
                if is_prod:
                    from app.safety.resource_governor import governor
                    governor.record_redis_unavailable()
                    raise RuntimeError(f"PAUSED — REDIS_UNAVAILABLE: Failed to acquire distributed crawl slot ({e})")
                return f"local-dev-{uuid.uuid4()}"

            time.sleep(0.5)

        raise TimeoutError(
            f"TIMEOUT: Could not acquire '{slot_type}' crawl slot within {wait_timeout}s "
            f"(max concurrent={max_slots})"
        )

    def release_slot_sync(self, slot_type: str, lease_id: str):
        """Release a previously acquired slot lease from Redis."""
        if not lease_id or lease_id.startswith("local-dev-"):
            return

        r = get_redis()
        if r is not None:
            try:
                redis_key, max_slots = self._get_pool_config(slot_type)
                r.zrem(redis_key, lease_id)
                remaining = r.zcard(redis_key)
                logger.info(
                    f"[DistributedSlotManager] Released '{slot_type}' crawl slot '{lease_id}' "
                    f"({remaining}/{max_slots} remaining)"
                )
            except Exception as e:
                logger.warning(f"[DistributedSlotManager] Error releasing slot '{lease_id}': {e}")

    def get_active_slots_count(self, slot_type: str = "standard") -> int:
        """Inspect current count of non-expired slots in a pool."""
        r = get_redis()
        if r is None:
            return 0
        try:
            redis_key, _ = self._get_pool_config(slot_type)
            now = time.time()
            expired_threshold = now - self.lease_timeout
            pipe = r.pipeline(transaction=True)
            pipe.zremrangebyscore(redis_key, "-inf", expired_threshold)
            pipe.zcard(redis_key)
            _, count = pipe.execute()
            return int(count)
        except Exception:
            return 0

    def get_total_active_crawls(self) -> Dict[str, int]:
        """Return active slot counts across all pools."""
        return {
            "standard_active": self.get_active_slots_count("standard"),
            "deep_active": self.get_active_slots_count("deep"),
            "total_active": self.get_active_slots_count("standard") + self.get_active_slots_count("deep"),
            "max_standard": getattr(settings, "MAX_CONCURRENT_CRAWLS", 2),
            "max_deep": getattr(settings, "MAX_CONCURRENT_DEEP_CRAWLS", 1),
        }

    def clear_all_slots(self):
        """Clear all active slot leases (used during restart/recovery)."""
        r = get_redis()
        if r is not None:
            try:
                for pool in ["standard", "deep"]:
                    key, _ = self._get_pool_config(pool)
                    r.delete(key)
                logger.info("[DistributedSlotManager] Cleared all distributed crawler slot pools.")
            except Exception as e:
                logger.warning(f"[DistributedSlotManager] Error clearing slots: {e}")

    @asynccontextmanager
    async def distributed_crawl_slot(self, slot_type: str = "standard", wait_timeout: float = 60.0):
        """
        Asynchronous context manager enforcing distributed + process-local concurrency boundaries.
        Guarantees cleanup on normal completion, exception, timeout, or cancellation.
        """
        _, max_slots = self._get_pool_config(slot_type)
        local_sem = _get_local_semaphore(slot_type, max_slots)

        # 1. Acquire local semaphore boundary
        try:
            await asyncio.wait_for(local_sem.acquire(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"TIMEOUT: Could not acquire local boundary for '{slot_type}' within {wait_timeout}s")
        cancel_event = threading.Event()
        lease_id: Optional[str] = None
        try:
            # 2. Acquire global distributed slot in Redis
            acquire_task = asyncio.create_task(
                asyncio.to_thread(
                    self.acquire_slot_sync,
                    slot_type=slot_type,
                    wait_timeout=wait_timeout,
                    cancel_event=cancel_event,
                )
            )
            try:
                lease_id = await asyncio.shield(acquire_task)
            except asyncio.CancelledError:
                cancel_event.set()
                try:
                    acquired_lease = await acquire_task
                    if acquired_lease:
                        self.release_slot_sync(slot_type=slot_type, lease_id=acquired_lease)
                except Exception:
                    pass
                raise

            yield lease_id
        finally:
            try:
                if lease_id:
                    self.release_slot_sync(slot_type=slot_type, lease_id=lease_id)
            except Exception as rel_err:
                logger.warning(f"[DistributedSlotManager] Error in slot release cleanup: {rel_err}")
            finally:
                local_sem.release()


slot_manager = DistributedSlotManager()
