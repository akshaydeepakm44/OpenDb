#searxng
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
            "safesearch": 2, # 2 = Strict safe search in SearXNG
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html, */*"
        }

        from app.audit.tracer import tracer, Checkpoint
        import time

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP04_SEARCH_EXECUTION,
            event="SEARCH_START",
            message=f"SearXNG query dispatch: '{query}' (category={clean_category}, engines={FAST_ENGINES})",
            extra={"query": query, "category": clean_category, "max_results": max_results}
        )

        t0 = time.time()
        max_retries = 3
        last_err = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                    response = await client.get(url, params=params, headers=headers)
                    if response.status_code != 200:
                        params_retry = {"q": query, "format": "json", "categories": clean_category, "safesearch": 2}
                        response = await client.get(url, params=params_retry, headers=headers)

                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])
                        cleaned = []
                        for idx, item in enumerate(results[:max_results], 1):
                            item_url = item.get("url", "")
                            if not item_url:
                                continue
                            item_clean = {
                                "title": item.get("title") or "B2B Organization",
                                "url": item_url,
                                "snippet": item.get("content") or "",
                                "engine": item.get("engine", "searxng"),
                                "score": item.get("score", 1.0),
                            }
                            cleaned.append(item_clean)
                            # Log every single search result at DEBUG level
                            tracer.log_event(
                                level="DEBUG",
                                checkpoint=Checkpoint.CP05_SEARCH_RESULTS,
                                event="SEARCH_RESULT_ITEM",
                                message=f"Result #{idx:02d} | title='{item_clean['title']}' | url={item_url}",
                                extra={"index": idx, "query": query, "title": item_clean["title"], "url": item_url, "snippet": item_clean["snippet"][:200]}
                            )

                        dur = time.time() - t0
                        tracer.log_event(
                            level="INFO",
                            checkpoint=Checkpoint.CP05_SEARCH_RESULTS,
                            event="SEARCH_END",
                            message=f"SearXNG query '{query}' completed: {len(cleaned)} results found",
                            duration=dur,
                            status="SUCCESS",
                            extra={"query": query, "results_count": len(cleaned)}
                        )
                        if cleaned:
                            cache_set("search", query, clean_category, max_results, value=(cleaned, False, "SearXNG OK"), ttl=SEARCH_CACHE_TTL)
                        return cleaned, False, f"SearXNG returned {len(cleaned)} results."
                    else:
                        tracer.log_event(
                            level="WARNING",
                            checkpoint=Checkpoint.CP04_SEARCH_EXECUTION,
                            event="SEARCH_RETRY",
                            message=f"SearXNG returned HTTP {response.status_code} on attempt {attempt}/{max_retries} for query '{query}'",
                            extra={"attempt": attempt, "status_code": response.status_code}
                        )
            except Exception as e:
                last_err = e
                tracer.log_event(
                    level="WARNING",
                    checkpoint=Checkpoint.CP04_SEARCH_EXECUTION,
                    event="SEARCH_RETRY",
                    message=f"SearXNG attempt {attempt}/{max_retries} failed for query '{query}': {e}",
                    extra={"attempt": attempt, "error": str(e)}
                )
                err_str = str(e).lower()
                if "connection" in err_str or "connect" in err_str or "refused" in err_str:
                    logger.info(f"SearXNG endpoint unreachable ({e}). Switching immediately to search fallback.")
                    break
            if attempt < max_retries:
                import asyncio
                await asyncio.sleep(0.5 * attempt)

        dur = time.time() - t0
        tracer.log_event(
            level="WARNING",
            checkpoint=Checkpoint.CP30_FAILURE_RECOVERY,
            event="SEARCH_FALLBACK_TRIGGERED",
            message=f"SearXNG unavailable after {max_retries} retries ({last_err}). Invoking live web search fallback for '{query}'...",
            duration=dur,
            status="DEGRADED",
            extra={"query": query, "retries_exhausted": True, "error": str(last_err)}
        )

        # Automatic live web search fallback (essential for local dev where Docker SearXNG isn't running)
        fb_results, is_fb, fb_log = await self._duckduckgo_fallback(query, max_results)
        if fb_results:
            cache_set("search", query, clean_category, max_results, value=(fb_results, True, fb_log), ttl=SEARCH_CACHE_TTL)
            return fb_results, True, fb_log

        return [], False, f"SearXNG failed after retries: {last_err} (DEGRADED)"

    async def _duckduckgo_fallback(self, query: str, max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Direct web search fallback when self-hosted SearXNG is unavailable (e.g. local dev)."""
        try:
            from bs4 import BeautifulSoup
            from urllib.parse import unquote
            url = "https://html.duckduckgo.com/html/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.post(url, data={"q": query}, headers=headers)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cleaned = []
                    for result in soup.select(".result"):
                        link_el = result.select_one(".result__title a")
                        snippet_el = result.select_one(".result__snippet")
                        if link_el:
                            raw_href = link_el.get("href", "")
                            if "uddg=" in raw_href:
                                actual_url = unquote(raw_href.split("uddg=")[1].split("&")[0])
                            else:
                                actual_url = raw_href
                            title = link_el.get_text(strip=True)
                            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                            if actual_url.startswith("http") and "duckduckgo.com" not in actual_url:
                                cleaned.append({
                                    "title": title or "B2B Organization",
                                    "url": actual_url,
                                    "snippet": snippet,
                                    "engine": "web_search_fallback",
                                    "score": 1.0,
                                })
                                if len(cleaned) >= max_results:
                                    break
                    if cleaned:
                        logger.info(f"🌐 [WebSearchFallback] Recovered {len(cleaned)} live search results via web search fallback for '{query}'")
                        return cleaned, True, f"Web Search Fallback ({len(cleaned)} URLs found)"
        except Exception as ddg_err:
            logger.warning(f"Web search fallback failed for '{query}': {ddg_err}")
        return [], True, "Web search fallback returned 0 results"

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

searxng_service = SearXNGService()

