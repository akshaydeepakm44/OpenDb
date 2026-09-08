import logging
import re
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.safety.guardrails import extract_domain, get_root_domain, is_domain_blocked

logger = logging.getLogger(__name__)

# Configurable domain safety blocklists & patterns
PROHIBITED_DOMAIN_PATTERNS = [
    # Adult / Erotica / Porn
    r"(?:porn|xxx|adult|sex|camgirl|erotica|hentai|xhamster|pornhub|onlyfans|redtube|youporn|brazzers|chaturbate|xvideos)",
    # Gambling / Casino / Betting
    r"(?:casino|betting|poker|slots|sportsbook|gambling|roulette|jackpot|baccarat|bet365|stake\.com|1xbet|slot88|qqslot)",
    # Illegal Drugs / Weapons / Darkweb
    r"(?:darknet|darkweb|silkroad|illegal-drugs|buy-weed|buy-cocaine|buy-firearms|unregistered-ammo)",
    # Piracy / Torrent / Warez / Crack
    r"(?:thepiratebay|1337x|rarbg|yts|fitgirl|crackdown|keygen|warez|torrent|piratebay|torrentz|rapidgator)",
    # Parked Domain / For-Sale Signals
    r"(?:domain-for-sale|parked-domain|hugedomains|sedo\.com|dan\.com|afternic|buydomains|namecheap-parked|godaddy-parked)",
    # Scam / Phishing / Fraud
    r"(?:phishing|credential-harvest|carder|dumps-cc|fake-identity|crypto-drainer|stealer-log)",
    # Malware / Ransomware
    r"(?:ransomware|keylogger|botnet|exploit-kit|malware-distribution)"
]

PARKED_DOMAIN_KEYWORDS = [
    "this domain is for sale", "buy this domain", "domain parked", "hosted by godaddy",
    "hugedomains", "inquire about this domain", "page parked", "renew your domain"
]

class DomainSafetyGuard:
    """
    Domain Safety Firewall — Phase 3 of Master Architecture.
    Evaluates every domain BEFORE Playwright, Crawl4AI, HTTP requests, or DB insertion.
    """

    @staticmethod
    def evaluate_domain_safety(url_or_domain: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Returns structured evaluation:
        {
            "allowed": True/False,
            "risk_level": "LOW" | "BLOCKED",
            "category": "BUSINESS" | prohibited category,
            "reason": str,
            "domain": str,
            "root_domain": str
        }
        """
        if not url_or_domain:
            return {
                "allowed": False,
                "risk_level": "BLOCKED",
                "category": "INVALID_INPUT",
                "reason": "Empty or missing domain input",
                "domain": "",
                "root_domain": ""
            }

        domain = extract_domain(url_or_domain)
        root_domain = get_root_domain(domain)

        if not domain:
            return {
                "allowed": False,
                "risk_level": "BLOCKED",
                "category": "INVALID_INPUT",
                "reason": "Could not parse valid domain structure",
                "domain": "",
                "root_domain": ""
            }

        # 1. DB Blocklist check (if database session provided)
        if db is not None:
            try:
                if is_domain_blocked(db, domain):
                    logger.warning(f"🚫 [DOMAIN SAFETY GUARD] Domain '{domain}' is listed in DB blocklist.")
                    return {
                        "allowed": False,
                        "risk_level": "BLOCKED",
                        "category": "DB_BLOCKLIST",
                        "reason": "Domain matched DB safety blocklist",
                        "domain": domain,
                        "root_domain": root_domain
                    }
            except Exception as e:
                logger.error(f"[DOMAIN SAFETY GUARD] DB lookup error for '{domain}': {e}")

        # 2. Pattern check against prohibited categories
        domain_full_str = f"{domain} {root_domain}".lower()

        for pattern in PROHIBITED_DOMAIN_PATTERNS:
            if re.search(pattern, domain_full_str):
                # Infer category from pattern match
                matched_cat = "PROHIBITED_CATEGORY"
                if any(k in pattern for k in ["porn", "xxx", "adult", "sex"]):
                    matched_cat = "ADULT"
                elif any(k in pattern for k in ["casino", "betting", "poker", "slots"]):
                    matched_cat = "GAMBLING"
                elif any(k in pattern for k in ["pirate", "torrent", "warez", "crack"]):
                    matched_cat = "PIRACY"
                elif any(k in pattern for k in ["parked", "hugedomains", "sedo", "dan"]):
                    matched_cat = "PARKED_DOMAIN"
                elif any(k in pattern for k in ["phishing", "crypto-drainer", "carder"]):
                    matched_cat = "SCAM"
                elif any(k in pattern for k in ["ransomware", "malware", "botnet"]):
                    matched_cat = "MALWARE"
                elif any(k in pattern for k in ["darknet", "drugs", "firearms"]):
                    matched_cat = "ILLEGAL_MARKETPLACE"

                logger.warning(f"🚫 [DOMAIN SAFETY GUARD] Hard blocked domain '{domain}' | Category: {matched_cat}")
                return {
                    "allowed": False,
                    "risk_level": "BLOCKED",
                    "category": matched_cat,
                    "reason": f"Domain matched prohibited pattern ({pattern})",
                    "domain": domain,
                    "root_domain": root_domain
                }

        # 3. Passed domain safety checks
        return {
            "allowed": True,
            "risk_level": "LOW",
            "category": "BUSINESS",
            "reason": "Official business website candidate",
            "domain": domain,
            "root_domain": root_domain
        }

    @staticmethod
    def is_parked_page_content(page_text: str) -> bool:
        """Helper check to detect parked domain page text during lightweight crawl."""
        if not page_text:
            return False
        txt_low = page_text.lower()
        return any(kw in txt_low for kw in PARKED_DOMAIN_KEYWORDS)

domain_safety_guard = DomainSafetyGuard()
