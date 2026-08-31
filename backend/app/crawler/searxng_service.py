import logging
import httpx
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

class SearXNGService:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.SEARXNG_URL

    async def search_with_meta(self, query: str, category: str = "general", max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool, str]:
        """
        Query SearXNG JSON API and return (candidate_sources, is_fallback, status_log).
        """
        url = f"{self.base_url.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "json",
            "categories": category
        }
        
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(url, params=params)
                if response.status_code != 200:
                    msg = f"SearXNG HTTP {response.status_code} on {self.base_url} — Using local fallback domain seeds."
                    logger.warning(msg)
                    return self._get_fallback_sources(query), True, msg
                
                data = response.json()
                results = data.get("results", [])
                
                cleaned_results = []
                for item in results[:max_results]:
                    cleaned_results.append({
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "snippet": item.get("content"),
                        "engine": item.get("engine"),
                        "score": item.get("score", 1.0)
                    })
                if cleaned_results:
                    msg = f"SearXNG Online ({self.base_url}) — Discovered {len(cleaned_results)} live lead targets for '{query}'."
                    return cleaned_results, False, msg
                else:
                    msg = f"SearXNG returned 0 results for '{query}' — Using fallback seed targets."
                    return self._get_fallback_sources(query), True, msg
        except Exception as e:
            msg = f"SearXNG endpoint unreachable at {self.base_url} ({e}) — Using fallback lead seeds."
            logger.warning(msg)
            return self._get_fallback_sources(query), True, msg

    async def search(self, query: str, category: str = "general", max_results: int = 20) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

    def _get_fallback_sources(self, query: str) -> List[Dict[str, Any]]:
        """Return empty list when SearXNG is offline or returns no results. Strictly no fake mock data."""
        logger.info(f"SearXNG query unreachable or empty for '{query}'. No fake fallback data injected.")
        return []

searxng_service = SearXNGService()
