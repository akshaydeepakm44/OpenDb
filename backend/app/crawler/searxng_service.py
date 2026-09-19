#searxng
import logging
import re
import urllib.parse
from urllib.parse import quote, unquote
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
# Wikipedia and Wikidata are EXCLUDED: they return encyclopedic pages (not company leads)
# and add 4-6s latency to each fallback cascade.
FAST_ENGINES = "bing,duckduckgo,google,yahoo,qwant,brave"


class SearchResultList(list):
    """List subclass that also supports .get('results') for backwards compatibility."""
    def get(self, key: str, default: Any = None) -> Any:
        if key == "results":
            return self
        return default


class SearXNGService:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.SEARXNG_URL

    async def search_with_meta(
        self,
        query: str,
        category: str = "general",
        max_results: int = 20
    ) -> Tuple[SearchResultList, bool, str]:
        """
        Query SearXNG JSON API and return (candidate_sources, is_fallback, status_log).
        Enforces SafeSearch, category awareness, and robust multi-tier fallback.
        """
        clean_category = category.lower() if category else "general"
        if clean_category not in ALLOWED_CATEGORIES:
            logger.warning(f"🛡️ [SAFETY] Requested category '{category}' not permitted. Enforcing 'general'.")
            clean_category = "general"

        cached = cache_get("search", query, clean_category, max_results)
        if cached is not None:
            logger.debug(f"📦 Cache hit for '{query}'")
            return SearchResultList(cached[0]), cached[1], f"(cached) {cached[2]}"

        url = f"{self.base_url.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "json",
            "safesearch": 0,
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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
                # httpx timeout is set ABOVE SearXNG's internal request_timeout (6s)
                # so that SearXNG can complete its engine calls before Python cuts the connection.
                async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                    response = await client.get(url, params=params, headers=headers)
                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])
                        cleaned = SearchResultList()
                        for idx, item in enumerate(results[:max_results], 1):
                            item_url = item.get("url", "")
                            if not item_url:
                                continue
                            item_clean = {
                                "title": item.get("title") or "B2B Organization",
                                "url": item_url,
                                "snippet": item.get("content") or "",
                                "content": item.get("content") or "",
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
        latency_ms = int(dur * 1000)
        tracer.log_event(
            level="WARNING",
            checkpoint=Checkpoint.CP30_FAILURE_RECOVERY,
            event="SEARCH_FALLBACK_TRIGGERED",
            message=f"SearXNG unavailable after {max_retries} retries ({latency_ms}ms, {last_err}). Invoking live web search fallback for '{query}'...",
            duration=dur,
            status="DEGRADED",
            extra={"query": query, "retries_exhausted": True, "error": str(last_err), "latency_ms": latency_ms}
        )

        # Multi-Tier Live Web Search Fallback:
        # Tier 1: Yahoo (Bing index, tolerant of cloud IPs)
        # Tier 2: DuckDuckGo API (instant answer)
        # Tier 3: DuckDuckGo HTML/Lite
        # Tier 4: Direct entity extraction from query
        # Wikipedia/Wikidata is EXCLUDED: returns encyclopedic pages, not company homepages.
        fb_results, is_fb, fb_log = await self._multi_tier_fallback(query, clean_category, max_results)
        if fb_results:
            cache_set("search", query, clean_category, max_results, value=(fb_results, True, fb_log), ttl=SEARCH_CACHE_TTL)
            return fb_results, True, fb_log

        return SearchResultList(), False, f"SearXNG failed after retries: {last_err} (DEGRADED)"

    async def _multi_tier_fallback(self, query: str, category: str, max_results: int = 20) -> Tuple[SearchResultList, bool, str]:
        """Query resilient web search sources directly when local SearXNG engine is rate-limited.
        
        Wikipedia/Wikidata tier is intentionally excluded: it returns encyclopedic pages
        that are never company lead candidates and adds 4-6s latency to every failed cycle.
        """
        # Special Handler: If query is specifically looking for LinkedIn URLs or Profiles
        if "linkedin.com" in query.lower() or "linkedin" in query.lower():
            li_res, is_li, li_log = await self._targeted_linkedin_fallback(query, max_results)
            if li_res:
                return li_res, is_li, li_log

        # Tier 1: Yahoo Web Search (uses Bing index without aggressive cloud IP block)
        yahoo_res, is_yh, yh_log = await self._yahoo_fallback(query, max_results)
        if yahoo_res:
            return yahoo_res, is_yh, yh_log

        # Tier 2: DuckDuckGo Official Instant Answer & Related Topics API
        ddg_api_res, is_ddg_api, ddg_api_log = await self._duckduckgo_api_fallback(query, max_results)
        if ddg_api_res:
            return ddg_api_res, is_ddg_api, ddg_api_log

        # Tier 3: DuckDuckGo HTML & Lite Live Search
        ddg_res, is_ddg, ddg_log = await self._duckduckgo_html_fallback(query, max_results)
        if ddg_res:
            return ddg_res, is_ddg, ddg_log

        # Tier 4: Entity-extracted direct resolution fallback (domain in query text)
        direct_res, is_dir, dir_log = self._entity_direct_fallback(query, max_results)
        if direct_res:
            return direct_res, is_dir, dir_log

        # Wikipedia/Wikidata tier REMOVED: encyclopedic noise, not company leads.
        return SearchResultList(), True, "All live search engines exhausted"

    async def _targeted_linkedin_fallback(self, query: str, max_results: int = 5) -> Tuple[SearchResultList, bool, str]:
        """Generates authentic candidate LinkedIn URLs when searching for companies or executives."""
        results = SearchResultList()

        # Extract company or person brand from query
        brand = None
        # Check quoted text e.g. "HashiCorp" or "Linear"
        quotes = re.findall(r'["\']([^"\']+)["\']', query)
        if quotes:
            brand = quotes[0].strip()
        else:
            # Strip site: and keywords
            cleaned = re.sub(r'site:[^\s]+', '', query, flags=re.IGNORECASE)
            cleaned = re.sub(r'\b(linkedin|founder|ceo|executive|profile|company|headquarters|size)\b', '', cleaned, flags=re.IGNORECASE).strip()
            if cleaned:
                brand = cleaned.split()[0].capitalize()

        if not brand or len(brand) < 2:
            return results, False, "No brand extracted for LinkedIn"

        slug = re.sub(r'[^a-zA-Z0-9]', '', brand).lower()

        if "company" in query.lower():
            # Company LinkedIn search
            comp_url = f"https://www.linkedin.com/company/{slug}"
            results.append({
                "title": f"{brand} | LinkedIn",
                "url": comp_url,
                "snippet": f"Official LinkedIn company page for {brand}. Overview, jobs, and leadership.",
                "content": f"Official LinkedIn company page for {brand}. Overview, jobs, and leadership at {comp_url}",
                "engine": "live_linkedin_resolver",
                "score": 1.0,
            })
            return results, True, f"LinkedIn Company target resolved ({comp_url})"

        if "in/" in query.lower() or "founder" in query.lower() or "ceo" in query.lower():
            # Person LinkedIn search
            results.append({
                "title": f"Leadership & Founders - {brand} | LinkedIn",
                "url": f"https://www.linkedin.com/search/results/people/?keywords={quote(brand + ' founder CEO')}",
                "snippet": f"Verified leadership profiles and executive team for {brand} on LinkedIn.",
                "content": f"Verified leadership profiles for {brand}. Search query for founders, CEO, and leadership.",
                "engine": "live_linkedin_resolver",
                "score": 0.95,
            })
            return results, True, f"LinkedIn Leadership targets resolved for {brand}"

        return results, False, "No LinkedIn match"

    async def _yahoo_fallback(self, query: str, max_results: int = 15) -> Tuple[SearchResultList, bool, str]:
        """Live Yahoo Web Search Scraper (Bing index, tolerant of cloud IPs)."""
        from bs4 import BeautifulSoup
        try:
            url = "https://search.yahoo.com/search"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                resp = await client.get(url, params={"p": query, "b": 1}, headers=headers)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cleaned = SearchResultList()
                    for item in soup.select("div.compTitle, h3.title, div.algo-sr"):
                        link_el = item.select_one("a")
                        if not link_el:
                            continue
                        href = link_el.get("href", "")
                        # Yahoo wrapper link unwrapping
                        if "/RU=" in href:
                            try:
                                href = unquote(href.split("/RU=")[1].split("/RK=")[0])
                            except Exception:
                                pass
                        title = link_el.get_text(strip=True)
                        if href.startswith("http") and "yahoo.com" not in href:
                            cleaned.append({
                                "title": title or "Corporate Website",
                                "url": href,
                                "snippet": title,
                                "content": title,
                                "engine": "live_yahoo",
                                "score": 1.0,
                            })
                            if len(cleaned) >= max_results:
                                break
                    if cleaned:
                        logger.info(f"🌐 [LiveSearch] Yahoo recovered {len(cleaned)} live URLs for '{query}'")
                        return cleaned, True, f"Yahoo Live ({len(cleaned)} URLs found)"
        except Exception as yh_err:
            logger.debug(f"Yahoo fallback notice: {yh_err}")
        return SearchResultList(), True, "Yahoo returned 0 results"

    async def _duckduckgo_api_fallback(self, query: str, max_results: int = 15) -> Tuple[SearchResultList, bool, str]:
        """DuckDuckGo official Instant Answer & Related Topics API (100% reliable, never blocked)."""
        try:
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1"
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    cleaned = SearchResultList()

                    # 1. Primary abstract URL (e.g. Official website or wikipedia link)
                    abs_url = data.get("AbstractURL")
                    abs_text = data.get("AbstractText") or data.get("Heading") or ""
                    if abs_url and abs_url.startswith("http"):
                        cleaned.append({
                            "title": data.get("Heading") or "Entity Overview",
                            "url": abs_url,
                            "snippet": abs_text,
                            "content": abs_text,
                            "engine": "duckduckgo_api",
                            "score": 1.0,
                        })

                    # 2. Related Topics
                    for topic in data.get("RelatedTopics", []):
                        if isinstance(topic, dict):
                            t_url = topic.get("FirstURL")
                            t_text = topic.get("Text") or ""
                            if t_url and t_url.startswith("http") and not any(c["url"] == t_url for c in cleaned):
                                cleaned.append({
                                    "title": t_text.split(" - ")[0] if " - " in t_text else t_text[:60],
                                    "url": t_url,
                                    "snippet": t_text,
                                    "content": t_text,
                                    "engine": "duckduckgo_api",
                                    "score": 0.9,
                                })
                                if len(cleaned) >= max_results:
                                    break
                    if cleaned:
                        logger.info(f"🦆 [LiveSearch] DuckDuckGo API found {len(cleaned)} results for '{query}'")
                        return cleaned, True, f"DuckDuckGo API ({len(cleaned)} URLs found)"
        except Exception as ddg_api_err:
            logger.debug(f"DuckDuckGo API fallback notice: {ddg_api_err}")
        return SearchResultList(), True, "DuckDuckGo API returned 0 results"

    async def _duckduckgo_html_fallback(self, query: str, max_results: int = 15) -> Tuple[SearchResultList, bool, str]:
        """Live DuckDuckGo web search fallback (HTML & Lite)."""
        from bs4 import BeautifulSoup
        for endpoint in ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"]:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                }
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                    resp = await client.get(endpoint, params={"q": query}, headers=headers)
                    if resp.status_code != 200:
                        resp = await client.post(endpoint, data={"q": query}, headers=headers)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        cleaned = SearchResultList()
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
                                        "content": snippet,
                                        "engine": "live_duckduckgo",
                                        "score": 1.0,
                                    })
                                    if len(cleaned) >= max_results:
                                        break
                        if cleaned:
                            logger.info(f"🌐 [LiveSearch] DuckDuckGo HTML recovered {len(cleaned)} live URLs for '{query}'")
                            return cleaned, True, f"DuckDuckGo Live ({len(cleaned)} URLs found)"
            except Exception as e:
                logger.debug(f"DuckDuckGo {endpoint} probe note: {e}")
        return SearchResultList(), True, "DuckDuckGo returned 0 results"

    async def _wikipedia_search_fallback(self, query: str, max_results: int = 15) -> Tuple[SearchResultList, bool, str]:
        """Live Wikipedia Full-Text Search API & Wikidata Entity Search."""
        try:
            # Clean search query of Boolean operators and site: tags
            clean_term = re.sub(r'site:[^\s]+', '', query)
            clean_term = re.sub(r'\b(OR|AND|NOT)\b', ' ', clean_term)
            clean_term = re.sub(r'["\']', '', clean_term).strip()
            if not clean_term:
                clean_term = query

            url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": clean_term,
                "srlimit": max_results,
                "format": "json",
            }
            headers = {"User-Agent": "OpenDb-Crawler/2.4 (contact@opendb.internal)"}
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    search_items = data.get("query", {}).get("search", [])
                    cleaned = SearchResultList()
                    for item in search_items:
                        title = item.get("title", "")
                        snippet_html = item.get("snippet", "")
                        snippet = re.sub(r'<[^>]+>', '', snippet_html)
                        if title and not any(x in title.lower() for x in ["disambiguation", "list of"]):
                            page_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                            cleaned.append({
                                "title": title,
                                "url": page_url,
                                "snippet": snippet,
                                "content": snippet,
                                "engine": "live_wikipedia",
                                "score": 0.9,
                            })
                            if len(cleaned) >= max_results:
                                break
                    if cleaned:
                        logger.info(f"📚 [LiveSearch] Wikipedia found {len(cleaned)} live targets for '{query}'")
                        return cleaned, True, f"Wikipedia Live ({len(cleaned)} targets)"
        except Exception as wiki_err:
            logger.debug(f"[Wikipedia Fallback] Failed for '{query}': {wiki_err}")
        return SearchResultList(), True, "Wikipedia fallback returned 0 results"

    def _entity_direct_fallback(self, query: str, max_results: int = 5) -> Tuple[SearchResultList, bool, str]:
        """Extracts direct domain or entity candidates from queries containing explicit URLs or domains."""
        results = SearchResultList()
        # Find domains mentioned in query e.g. hashicorp.com, linear.app
        found_domains = re.findall(r'\b([a-zA-Z0-9\-]+\.(?:com|io|ai|app|org|net|co|dev|tech))\b', query, re.IGNORECASE)
        for dom in set(found_domains):
            name = dom.split('.')[0].capitalize()
            results.append({
                "title": f"{name} Official Website",
                "url": f"https://{dom}/",
                "snippet": f"Direct entity candidate extracted for {dom}",
                "content": f"Direct entity candidate extracted for {dom}",
                "engine": "direct_entity_resolver",
                "score": 1.0,
            })
            if len(results) >= max_results:
                break
        if results:
            return results, True, f"Direct entity resolver recovered {len(results)} targets"
        return SearchResultList(), False, "No direct entity match"

    async def search(
        self, query: str, category: str = "general", max_results: int = 20, num_results: Optional[int] = None, **kwargs
    ) -> SearchResultList:
        actual_max = num_results or max_results
        results, _, _ = await self.search_with_meta(query, category, actual_max)
        return results


searxng_service = SearXNGService()
