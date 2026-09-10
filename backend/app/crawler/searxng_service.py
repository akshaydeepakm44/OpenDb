import logging
import httpx
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.cache.redis_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# Cache brief to avoid hammering SearXNG with identical queries
SEARCH_CACHE_TTL = 300

# Allowed clean categories for business lead discovery
ALLOWED_CATEGORIES = {"general", "business", "it", "news"}

# Engines that reliably return clean clearnet results
FAST_ENGINES = "bing,brave,mojeek,duckduckgo,google"


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
        Enforces strict SafeSearch (safesearch=2), category restrictions, and clearnet engines.
        """
        # Guardrail 1: Enforce allowed clean search categories
        clean_category = category.lower() if category else "general"
        if clean_category not in ALLOWED_CATEGORIES:
            logger.warning(f"🛡️ [SAFETY] Requested category '{category}' not permitted. Enforcing 'general'.")
            clean_category = "general"

        cached = cache_get("search", query, clean_category, max_results)
        if cached is not None:
            logger.debug(f"📦 Cache hit for '{query}'")
            return cached[0], cached[1], f"(cached) {cached[2]}"

        # Mandatory SafeSearch=2 & B2B Category restriction
        url = f"{self.base_url.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "json",
            "categories": clean_category,
            "engines": FAST_ENGINES,
            "safesearch": 2, # 2 = Strict safe search in SearXNG
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html, */*"
        }

        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.get(url, params=params, headers=headers)

                if response.status_code != 200:
                    # Retry without engine restriction for maximum reliability
                    params_retry = {"q": query, "format": "json", "categories": clean_category, "safesearch": 2}
                    resp2 = await client.get(url, params=params_retry, headers=headers)
                    if resp2.status_code == 200:
                        response = resp2

                if response.status_code == 200:
                    try:
                        data = response.json()
                        results = data.get("results", [])
                        cleaned = []
                        for item in results[:max_results]:
                            item_url = item.get("url", "")
                            if not item_url:
                                continue
                            cleaned.append({
                                "title": item.get("title") or "B2B Organization",
                                "url": item_url,
                                "snippet": item.get("content") or "",
                                "engine": item.get("engine", "searxng"),
                                "score": item.get("score", 1.0),
                            })
                        if cleaned:
                            cache_set("search", query, clean_category, max_results, value=(cleaned, False, "SearXNG OK"), ttl=SEARCH_CACHE_TTL)
                            logger.info(f"🔎 [SearXNG] Retrieved {len(cleaned)} B2B candidate results for query: '{query}'")
                            return cleaned, False, f"SearXNG returned {len(cleaned)} results."
                        else:
                            return [], False, "SearXNG returned 0 results for query."
                    except Exception as json_err:
                        logger.warning(f"[SearXNG] JSON parse error: {json_err}")
                        return [], False, f"SearXNG JSON parse error: {json_err}"

                # Non-200 status: honestly report degraded without mock fallbacks
                logger.warning(f"⚠️ [SearXNG] Service returned HTTP {response.status_code} for '{query}'")
                return [], False, f"SearXNG HTTP {response.status_code} (DEGRADED)"

        except Exception as err:
            logger.warning(f"⚠️ [SearXNG] Connection failed for '{query}': {err}")
            return [], False, f"SearXNG connection failed: {err} (DEGRADED)"

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results


searxng_service = SearXNGService()

