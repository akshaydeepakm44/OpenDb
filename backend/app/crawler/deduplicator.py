"""
Pre-Crawl Deduplication Engine — §8 of Master Rules

Normalizes URLs and root domains before dispatching crawl tasks.
Uses Redis L1 memory sets (`opendb:crawled_domains`) and database domain lookups
to prevent re-crawling active companies (e.g. datadoghq.com, stripe.com, figma.com).
"""
import logging
from typing import Tuple
from urllib.parse import urlparse
from app.cache.redis_client import get_redis
from app.persistence.database import SessionLocal
from app.persistence.models import UniversalRecord, DomainRecord

logger = logging.getLogger(__name__)

REDIS_DOMAIN_SET_KEY = "opendb:crawled_domains"

class PreCrawlDeduplicator:
    @staticmethod
    def extract_canonical_domain(url: str) -> str:
        """Extract clean root domain from URL."""
        if not url:
            return ""
        if "://" not in url:
            url = f"http://{url}"
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def is_duplicate(self, url: str) -> Tuple[bool, str, str]:
        """
        Check if URL/domain is already processed.
        Returns: (is_duplicate: bool, domain: str, reason: str)
        """
        domain = self.extract_canonical_domain(url)
        if not domain:
            return True, "", "Invalid domain format"

        # Signal 1: Fast L1 Redis Set Check (<0.5ms)
        try:
            r = get_redis()
            if r is not None:
                if r.sismember(REDIS_DOMAIN_SET_KEY, domain):
                    return True, domain, f"Domain '{domain}' exists in Redis deduplication cache"
        except Exception as e:
            logger.debug(f"[Dedup] Redis check skipped: {e}")

        # Signal 2: Database Pre-Crawl Query Check
        db = SessionLocal()
        try:
            # Check domain in UniversalRecord
            existing = (
                db.query(UniversalRecord)
                .filter(UniversalRecord.url.ilike(f"%{domain}%"))
                .first()
            )
            if existing:
                # Add to Redis set for fast future hits
                self.mark_as_crawled(domain)
                return True, domain, f"Domain '{domain}' already exists in database (ID: {existing.id[:8]})"
        except Exception as err:
            logger.warning(f"[Dedup] DB check error: {err}")
        finally:
            db.close()

        return False, domain, "Clean unique domain"

    def mark_as_crawled(self, domain_or_url: str):
        """Mark domain as crawled in Redis deduplication set."""
        domain = self.extract_canonical_domain(domain_or_url) if "://" in domain_or_url else domain_or_url.lower()
        if not domain:
            return
        try:
            r = get_redis()
            if r is not None:
                r.sadd(REDIS_DOMAIN_SET_KEY, domain)
        except Exception as err:
            logger.debug(f"[Dedup] Failed to add domain to Redis: {err}")


pre_crawl_deduplicator = PreCrawlDeduplicator()
