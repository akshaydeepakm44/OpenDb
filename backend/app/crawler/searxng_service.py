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
            msg = f"SearXNG timeout (20s) for '{query}'"
            logger.warning(msg)
            return [], True, msg
        except Exception as e:
            msg = f"SearXNG error for '{query}': {e}"
            logger.warning(msg)
            return [], True, msg

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

    def _get_fallback_sources(self, query: str) -> List[Dict[str, Any]]:
        """No fake fallback data — only real crawled results."""
        return []


searxng_service = SearXNGService()
