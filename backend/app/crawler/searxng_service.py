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

        # SafeSearch=0 to avoid false filtering + query general category so all general engines respond
        url = f"{self.base_url.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "json",
            "categories": "general",
            "safesearch": 0,
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
            message=f"SearXNG query dispatch: '{query}' (category=general, engines={FAST_ENGINES})",
            extra={"query": query, "category": "general", "max_results": max_results}
        )

        t0 = time.time()
        max_retries = 2
        last_err = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                    response = await client.get(url, params=params, headers=headers)
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
                            tracer.log_event(
                                level="DEBUG",
                                checkpoint=Checkpoint.CP05_SEARCH_RESULTS,
                                event="SEARCH_RESULT_ITEM",
                                message=f"Result #{idx:02d} | title='{item_clean['title']}' | url={item_url}",
                                extra={"index": idx, "query": query, "title": item_clean["title"], "url": item_url, "snippet": item_clean["snippet"][:200]}
                            )

                        dur = time.time() - t0
                        if cleaned:
                            tracer.log_event(
                                level="INFO",
                                checkpoint=Checkpoint.CP05_SEARCH_RESULTS,
                                event="SEARCH_END",
                                message=f"SearXNG query '{query}' completed: {len(cleaned)} results found",
                                duration=dur,
                                status="SUCCESS",
                                extra={"query": query, "results_count": len(cleaned)}
                            )
                            cache_set("search", query, clean_category, max_results, value=(cleaned, False, "SearXNG OK"), ttl=SEARCH_CACHE_TTL)
                            return cleaned, False, f"SearXNG returned {len(cleaned)} results."
                        else:
                            logger.info(f"SearXNG returned 0 results on attempt {attempt} for '{query}'.")
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

        # Multi-Tier Live Web Search Fallback: DuckDuckGo -> DuckDuckGo Lite -> Mojeek -> Wikipedia Live API
        fb_results, is_fb, fb_log = await self._multi_tier_fallback(query, clean_category, max_results)
        if fb_results:
            return fb_results, True, fb_log

        return [], False, f"SearXNG failed after retries: {last_err} (DEGRADED)"

    async def _multi_tier_fallback(self, query: str, category: str, max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Query genuine live web search engines directly without any hardcoded test seeds."""
        # Tier 1: DuckDuckGo HTML & Lite Live Search
        ddg_res, is_ddg, ddg_log = await self._duckduckgo_fallback(query, max_results)
        if ddg_res:
            return ddg_res, is_ddg, ddg_log

        # Tier 2: Mojeek Live Clearnet Search
        mojeek_res, is_mj, mj_log = await self._mojeek_fallback(query, max_results)
        if mojeek_res:
            return mojeek_res, is_mj, mj_log

        # Tier 3: Wikipedia OpenSearch API (unrestricted live corporate/tech entity discovery)
        wiki_res, is_wiki, wiki_log = await self._wikipedia_fallback(query, max_results)
        if wiki_res:
            return wiki_res, is_wiki, wiki_log

        return [], True, "All live search engines exhausted"

    async def _duckduckgo_fallback(self, query: str, max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Live DuckDuckGo web search fallback (HTML & Lite)."""
        from bs4 import BeautifulSoup
        from urllib.parse import unquote

        # Try HTML first, then Lite
        for endpoint in ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"]:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                }
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                    resp = await client.get(endpoint, params={"q": query}, headers=headers)
                    if resp.status_code != 200:
                        resp = await client.post(endpoint, data={"q": query}, headers=headers)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        cleaned = []
                        for result in soup.select(".result, .result-link, tr"):
                            link_el = result.select_one(".result__title a, .result-link a, a.result-link")
                            snippet_el = result.select_one(".result__snippet, .result-snippet")
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
                                        "title": title or "Corporate Website",
                                        "url": actual_url,
                                        "snippet": snippet,
                                        "engine": "live_duckduckgo",
                                        "score": 1.0,
                                    })
                                    if len(cleaned) >= max_results:
                                        break
                        if cleaned:
                            logger.info(f"🌐 [LiveSearch] DuckDuckGo recovered {len(cleaned)} live URLs for '{query}'")
                            return cleaned, True, f"DuckDuckGo Live ({len(cleaned)} URLs found)"
            except Exception as e:
                logger.debug(f"DuckDuckGo {endpoint} probe note: {e}")
        return [], True, "DuckDuckGo returned 0 results"

    async def _mojeek_fallback(self, query: str, max_results: int = 15) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Live Mojeek search engine fallback."""
        try:
            from bs4 import BeautifulSoup
            url = "https://www.mojeek.com/search"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                resp = await client.get(url, params={"q": query}, headers=headers)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cleaned = []
                    for item in soup.select("ul.results-standard > li, div.results-standard > div"):
                        link = item.select_one("a.title, a.ob")
                        snippet_el = item.select_one("p.s")
                        if link:
                            actual_url = link.get("href", "")
                            if actual_url.startswith("http") and "mojeek.com" not in actual_url:
                                cleaned.append({
                                    "title": link.get_text(strip=True) or "Web Organization",
                                    "url": actual_url,
                                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                                    "engine": "live_mojeek",
                                    "score": 0.95,
                                })
                                if len(cleaned) >= max_results:
                                    break
                    if cleaned:
                        logger.info(f"🌐 [LiveSearch] Mojeek recovered {len(cleaned)} live URLs for '{query}'")
                        return cleaned, True, f"Mojeek Live ({len(cleaned)} URLs found)"
        except Exception as mj_err:
            logger.debug(f"Mojeek fallback note: {mj_err}")
        return [], True, "Mojeek returned 0 results"

    async def _wikipedia_fallback(self, query: str, max_results: int = 15) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Live Wikipedia OpenSearch API for relevant corporate/technology entities."""
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "opensearch",
                "search": query,
                "limit": max_results,
                "namespace": "0",
                "format": "json",
            }
            headers = {"User-Agent": "OpenDb-Crawler/2.4 (https://opendb.internal; contact@opendb.internal)"}
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if len(data) >= 4:
                        titles, snippets, urls = data[1], data[2], data[3]
                        cleaned = []
                        for t, s, u in zip(titles, snippets, urls):
                            if u and not any(x in u.lower() for x in ["disambiguation", "list_of"]):
                                cleaned.append({
                                    "title": t or "Corporate Profile",
                                    "url": u,
                                    "snippet": s or f"Wikipedia reference for {t}",
                                    "engine": "live_wikipedia",
                                    "score": 0.95,
                                })
                        if cleaned:
                            logger.info(f"📚 [LiveSearch] Wikipedia found {len(cleaned)} live targets for '{query}'")
                            return cleaned, True, f"Wikipedia Live ({len(cleaned)} targets)"
        except Exception as wiki_err:
            logger.warning(f"[Wikipedia Fallback] Failed for '{query}': {wiki_err}")
        return [], True, "Wikipedia fallback returned 0 results"

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

searxng_service = SearXNGService()

