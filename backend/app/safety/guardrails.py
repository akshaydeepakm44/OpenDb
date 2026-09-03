import logging
from urllib.parse import urlparse
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.persistence.models import BlockedDomain
from app.cache.redis_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# Known disallowed categories required by system safety specification
DISALLOWED_CATEGORIES = {
    "adult": ["porn", "xxx", "adult", "sex", "escort", "nsfw", "camgirl", "erotica", "hentai", "xhamster", "pornhub", "onlyfans"],
    "gambling": ["casino", "betting", "poker", "slots", "sportsbook", "gambling", "roulette", "jackpot", "baccarat", "bet365", "stake.com"],
    "weapons_drugs": ["weapons", "firearms", "ammunition", "illicit drugs", "narcotics", "darknet", "darkweb", "silkroad", "dispensary", "psychedelics"],
    "counterfeit": ["crack", "keygen", "warez", "torrent", "pirated", "counterfeit", "replica", "leaked", "thepiratebay", "1337x"],
    "phishing_malware": ["phishing", "malware", "ransomware", "keylogger", "botnet", "credential-harvest", "exploit-kit", "stealer"],
    "trafficking": ["human trafficking", "escort service", "exploitation", "underage", "trafficking"],
    "extremist": ["extremist", "terrorist", "hate speech", "white supremacy", "radicalization"],
    "access_control_circumvention": ["bypass paywall", "onion site", "tor relay", "crack login", "shadow web", "proxy bypass"]
}


def extract_domain(url_or_domain: str) -> str:
    """Extract clean domain name from URL or domain string."""
    if not url_or_domain:
        return ""
    val = url_or_domain.strip().lower()
    if val.startswith("http://") or val.startswith("https://"):
        try:
            val = urlparse(val).netloc
        except Exception:
            pass
    val = val.replace("www.", "").split(":")[0].split("/")[0]
    return val


def get_root_domain(domain: str) -> str:
    """Extract registrable root domain (e.g. sub.example.com -> example.com)."""
    clean = extract_domain(domain)
    parts = clean.split(".")
    if len(parts) >= 2:
        # Handle co.uk, com.au, etc.
        if len(parts) >= 3 and parts[-2] in ["co", "com", "net", "org", "gov", "ac", "edu"]:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    return clean


def is_domain_blocked(db: Session, url_or_domain: str) -> bool:
    """
    Central safety lookup check: Returns True if domain or its root domain
    is listed in the blocked_domains table.
    """
    domain = extract_domain(url_or_domain)
    if not domain:
        return False

    root_domain = get_root_domain(domain)

    # 1. Fast Redis cache check
    cached = cache_get("safety_block", domain)
    if cached is not None:
        return bool(cached)

    try:
        # 2. Check exact domain or root domain in blocked_domains DB table
        blocked = db.query(BlockedDomain).filter(
            BlockedDomain.domain.in_([domain, root_domain])
        ).first()

        is_blocked = blocked is not None
        cache_set("safety_block", domain, value=is_blocked, ttl=300)
        return is_blocked
    except Exception as e:
        logger.error(f"[SAFETY] Error checking blocklist for {domain}: {e}")
        # Fail-closed fallback if DB lookup fails unexpectedly
        return False


def add_to_blocklist(db: Session, url_or_domain: str, reason_category: str, source: str) -> Optional[BlockedDomain]:
    """
    Add domain to blocked_domains table with category and source provenance.
    Source enum: searxng_block, reputation_api, content_moderation, manual_review, manual_admin.
    """
    domain = extract_domain(url_or_domain)
    if not domain:
        return None

    root_domain = get_root_domain(domain)

    try:
        existing = db.query(BlockedDomain).filter(
            BlockedDomain.domain.in_([domain, root_domain])
        ).first()

        if existing:
            logger.info(f"[SAFETY] Domain '{domain}' is already in blocklist.")
            return existing

        entry = BlockedDomain(
            domain=domain,
            reason_category=reason_category,
            source=source
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        cache_set("safety_block", domain, value=True, ttl=600)
        logger.warning(f"🚫 [SAFETY GUARDRAIL] Blocked domain '{domain}' | Category: {reason_category} | Source: {source}")
        return entry
    except Exception as e:
        db.rollback()
        logger.error(f"[SAFETY] Failed to add '{domain}' to blocklist: {e}")
        return None


def check_content_heuristics(url_or_text: str) -> Tuple[bool, Optional[str]]:
    """
    Code-level heuristic scanner checking for disallowed categories (adult, gambling,
    weapons/drugs, counterfeit, malware/phishing, trafficking, extremist, access circumvention).
    Does NOT rely on LLM self-censorship.
    """
    if not url_or_text:
        return False, None

    text_lower = url_or_text.lower()

    for category, keywords in DISALLOWED_CATEGORIES.items():
        for kw in keywords:
            if kw in text_lower:
                logger.warning(f"[SAFETY HEURISTIC] Matched disallowed keyword '{kw}' (Category: {category}) in input")
                return True, category

    return False, None
