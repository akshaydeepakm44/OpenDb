import logging
import httpx
from typing import Tuple, Optional
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
from app.config import settings

logger = logging.getLogger(__name__)


class ReputationChecker:
    """
    Pre-crawl Reputation & Safety Checker.
    Interfaces with Google Safe Browsing API / Threat Intel services and enforces robots.txt rules.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "GOOGLE_SAFE_BROWSING_API_KEY", None)

    async def check_url_reputation(self, url: str) -> Tuple[bool, Optional[str]]:
        """
        Check URL against reputation / threat-intel API (e.g. Google Safe Browsing API v4).
        Returns (is_safe: bool, threat_type: Optional[str]).
        If flagged, returns (False, threat_type).
        If API key configured but API is unreachable/errors out -> FAIL-CLOSED (returns False, 'reputation_api_error').
        If no API key configured -> returns (True, None).
        """
        if not self.api_key:
            # No API key provided: fallback to clean state, code heuristics will still filter
            return True, None

        endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={self.api_key}"
        payload = {
            "client": {
                "clientId": "opendb-crawler",
                "clientVersion": "1.0.0"
            },
            "threatInfo": {
                "threatTypes": [
                    "MALWARE",
                    "SOCIAL_ENGINEERING",
                    "UNWANTED_SOFTWARE",
                    "POTENTIALLY_HARMFUL_APPLICATION"
                ],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}]
            }
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(endpoint, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    matches = data.get("matches", [])
                    if matches:
                        threat_type = matches[0].get("threatType", "MALWARE")
                        logger.warning(f"⚠️ [REPUTATION API] URL '{url}' flagged for threat: {threat_type}")
                        return False, threat_type
                    return True, None
                else:
                    logger.error(f"❌ [REPUTATION API ERROR] Safe Browsing returned HTTP {resp.status_code}")
                    # FAIL-CLOSED: Treat error as unsafe to prevent bypassing guardrails
                    return False, f"reputation_api_http_{resp.status_code}"
        except Exception as e:
            logger.error(f"❌ [REPUTATION API FAILURE] Safe Browsing call failed for '{url}': {e}")
            # FAIL-CLOSED: Skip item if threat intel API is unreachable
            return False, "reputation_api_unreachable"

    def is_robots_allowed(self, url: str, user_agent: str = "OpenDB-Bot") -> Tuple[bool, str]:
        """
        Check robots.txt before fetching.
        Returns (allowed: bool, reason: str).
        If disallowed, returns (False, 'robots_disallowed').
        """
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            allowed = rp.can_fetch(user_agent, url) or rp.can_fetch("*", url)
            if not allowed:
                logger.info(f"🤖 [ROBOTS.TXT] Crawl disallowed by robots.txt for '{url}'")
                return False, "robots_disallowed"
            return True, "allowed"
        except Exception as e:
            # If robots.txt cannot be fetched or read, default to permissive for public sites
            logger.debug(f"[ROBOTS.TXT] Could not parse robots.txt for {url}: {e}")
            return True, "robots_parse_skipped"


reputation_checker = ReputationChecker()
