import logging
import httpx
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.cache.redis_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# Cache brief to avoid hammering SearXNG with identical queries
SEARCH_CACHE_TTL = 300

# Engines that reliably return results without CAPTCHA blocks
# Bing + Brave are the fastest and most reliable without blocking
FAST_ENGINES = "bing,brave,mojeek"


class SearXNGService:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.SEARXNG_URL

    async def search_with_meta(
        self,
        query: str,
        category: str = "general",
        max_results: int = 20
    ) -> Tuple[List[Dict[str, Any]], bool, str]:
        """
        Query SearXNG JSON API and return (candidate_sources, is_fallback, status_log).
        Uses Bing + Brave + Mojeek engines (CAPTCHA-free) with 20s timeout.
        Results cached in Redis for 5 min to deduplicate.
        """
        cached = cache_get("search", query, category, max_results)
        if cached is not None:
            logger.debug(f"📦 Cache hit for '{query}'")
            return cached[0], cached[1], f"(cached) {cached[2]}"

        url = f"{self.base_url.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "json",
            "categories": category,
            "engines": FAST_ENGINES,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, params=params)

                if response.status_code != 200:
                    msg = (
                        f"SearXNG HTTP {response.status_code} — "
                        f"trying without engine filter..."
                    )
                    logger.warning(msg)
                    # Retry without engine filter
                    params_retry = {"q": query, "format": "json", "categories": category}
                    resp2 = await client.get(url, params=params_retry)
                    if resp2.status_code != 200:
                        return [], True, f"SearXNG unavailable (HTTP {response.status_code})"
                    response = resp2

                data = response.json()
                results = data.get("results", [])

                cleaned = []
                for item in results[:max_results]:
                    item_url = item.get("url", "")
                    if not item_url:
                        continue
                    cleaned.append({
                        "title": item.get("title"),
                        "url": item_url,
                        "snippet": item.get("content"),
                        "engine": item.get("engine"),
                        "score": item.get("score", 1.0),
                    })

                if cleaned:
                    msg = (
                        f"SearXNG [{FAST_ENGINES}] → {len(cleaned)} URLs for '{query}'"
                    )
                    result = (cleaned, False, msg)
                    cache_set("search", query, category, max_results, value=result, ttl=SEARCH_CACHE_TTL)
                    logger.info(f"[SearXNG] {msg}")
                    return result
                else:
                    msg = f"SearXNG 0 results for '{query}' via {FAST_ENGINES}"
                    logger.warning(msg)
                    result = ([], True, msg)
                    cache_set("search", query, category, max_results, value=result, ttl=60)
                    return result

        except httpx.TimeoutException:
            msg = f"SearXNG timeout (20s) for '{query}'. Using offline seed targets."
            logger.warning(msg)
            return self._get_fallback_sources(query), True, msg
        except Exception as e:
            msg = f"SearXNG error for '{query}': {e}. Using offline seed targets."
            logger.warning(msg)
            return self._get_fallback_sources(query), True, msg

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

    def _get_fallback_sources(self, query: str) -> List[Dict[str, Any]]:
        """Fallback to LIVE Bing & DuckDuckGo search if SearXNG is down."""
        logger.info(f"Using Live Search fallback (Bing/DDG) for: '{query}'")
        results = []
        
        # 1. Try Live Bing Search
        try:
            import urllib.request
            import base64
            from bs4 import BeautifulSoup
            from urllib.parse import quote, parse_qs, urlparse

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9"
            }
            bing_url = f"https://www.bing.com/search?q={quote(query)}"
            req = urllib.request.Request(bing_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                for h2 in soup.find_all("h2"):
                    a = h2.find("a")
                    if not a:
                        continue
                    raw_href = a.get("href", "")
                    target_url = None
                    
                    if "/ck/a?!" in raw_href:
                        try:
                            parsed = urlparse(raw_href)
                            qs = parse_qs(parsed.query)
                            u_val = qs.get("u", [""])[0]
                            if u_val.startswith("a1"):
                                b64 = u_val[2:]
                                b64 += "=" * ((4 - len(b64) % 4) % 4)
                                target_url = base64.b64decode(b64).decode("utf-8", errors="ignore")
                        except Exception:
                            pass
                    elif raw_href.startswith("http"):
                        target_url = raw_href
                        
                    if target_url and target_url.startswith("http"):
                        title = a.text.strip() if a.text else "Discovered Enterprise"
                        results.append({
                            "title": title,
                            "url": target_url,
                            "snippet": f"Discovered via live web search for '{query}'",
                            "engine": "bing_live_fallback",
                            "score": 1.0
                        })
        except Exception as e:
            logger.warning(f"Bing live search fallback failed: {e}")

        # 2. Filter out non-company directory sites & duplicate URLs
        filtered = []
        seen = set()
        for r in results:
            u_lower = r["url"].lower()
            if u_lower in seen:
                continue
            if any(x in u_lower for x in ["wikipedia.org", "facebook.com", "twitter.com", "youtube.com", "reddit.com", "bing.com"]):
                continue
            seen.add(u_lower)
            filtered.append(r)

        if filtered:
            logger.info(f"[Live Search Fallback] Discovered {len(filtered)} genuine live target URLs for '{query}'")
            return filtered[:15]

        logger.warning(f"[Live Search Fallback] No live search results returned for query: '{query}'")
        return []


searxng_service = SearXNGService()
