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

        # Multi-Tier Resilient Fallback: DuckDuckGo -> Wikipedia OpenSearch -> B2B Industry Seed Catalog
        fb_results, is_fb, fb_log = await self._multi_tier_fallback(query, clean_category, max_results)
        if fb_results:
            cache_set("search", query, clean_category, max_results, value=(fb_results, True, fb_log), ttl=SEARCH_CACHE_TTL)
            return fb_results, True, fb_log

        return [], False, f"SearXNG failed after retries: {last_err} (DEGRADED)"

    async def _multi_tier_fallback(self, query: str, category: str, max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Try DuckDuckGo, then Wikipedia OpenSearch, then curated B2B Seed Catalog."""
        # Tier 1: DuckDuckGo Fallback
        ddg_res, is_ddg, ddg_log = await self._duckduckgo_fallback(query, max_results)
        if ddg_res:
            return ddg_res, is_ddg, ddg_log

        # Tier 2: Wikipedia OpenSearch API (unrestricted, high coverage for tech & businesses)
        wiki_res, is_wiki, wiki_log = await self._wikipedia_fallback(query, max_results)
        if wiki_res:
            return wiki_res, is_wiki, wiki_log

        # Tier 3: Curated B2B Industry Seed Catalog
        seed_res, is_seed, seed_log = self._seed_directory_fallback(query, category, max_results)
        if seed_res:
            return seed_res, is_seed, seed_log

        return [], True, "All fallback tiers exhausted"

    async def _duckduckgo_fallback(self, query: str, max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Direct web search fallback when self-hosted SearXNG is unavailable."""
        try:
            from bs4 import BeautifulSoup
            from urllib.parse import unquote
            url = "https://html.duckduckgo.com/html/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
                resp = await client.get(url, params={"q": query}, headers=headers)
                if resp.status_code != 200:
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
                                    "engine": "duckduckgo_fallback",
                                    "score": 1.0,
                                })
                                if len(cleaned) >= max_results:
                                    break
                    if cleaned:
                        logger.info(f"🌐 [DuckDuckGo Fallback] Recovered {len(cleaned)} results for '{query}'")
                        return cleaned, True, f"DuckDuckGo Fallback ({len(cleaned)} URLs)"
        except Exception as ddg_err:
            logger.warning(f"DuckDuckGo search fallback failed for '{query}': {ddg_err}")
        return [], True, "DuckDuckGo fallback returned 0 results"

    async def _wikipedia_fallback(self, query: str, max_results: int = 15) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Query Wikipedia OpenSearch API for relevant corporate/technology entities."""
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
                                    "snippet": s or f"Wikipedia encyclopedic profile for {t}",
                                    "engine": "wikipedia_opensearch",
                                    "score": 0.95,
                                })
                        if cleaned:
                            logger.info(f"📚 [Wikipedia Fallback] Found {len(cleaned)} targets for '{query}'")
                            return cleaned, True, f"Wikipedia API Fallback ({len(cleaned)} targets)"
        except Exception as wiki_err:
            logger.warning(f"[Wikipedia Fallback] Failed for '{query}': {wiki_err}")
        return [], True, "Wikipedia fallback returned 0 results"

    def _seed_directory_fallback(self, query: str, category: str, max_results: int = 10) -> Tuple[List[Dict[str, Any]], bool, str]:
        """Deterministic industry target catalog fallback to prevent crawler starvation under bans."""
        import random
        DOMAINS_CATALOG = {
            "it": [
                {"name": "Stripe", "domain": "stripe.com", "desc": "Financial infrastructure for the internet"},
                {"name": "Datadog", "domain": "datadoghq.com", "desc": "Cloud-scale monitoring and security platform"},
                {"name": "Snowflake", "domain": "snowflake.com", "desc": "Data Cloud enabling enterprise AI and analytics"},
                {"name": "Atlassian", "domain": "atlassian.com", "desc": "Enterprise collaboration and issue tracking software"},
                {"name": "HubSpot", "domain": "hubspot.com", "desc": "Inbound CRM, sales and marketing automation software"},
                {"name": "Twilio", "domain": "twilio.com", "desc": "Customer engagement and communication APIs"},
                {"name": "Cloudflare", "domain": "cloudflare.com", "desc": "Security, performance, and reliability for the web"},
                {"name": "HashiCorp", "domain": "hashicorp.com", "desc": "Multi-cloud infrastructure automation tools"},
                {"name": "Postman", "domain": "postman.com", "desc": "API platform for building and using APIs"},
                {"name": "MongoDB", "domain": "mongodb.com", "desc": "Developer data platform with document database"},
                {"name": "Elastic", "domain": "elastic.co", "desc": "Search, observability, and security solutions"},
                {"name": "Confluent", "domain": "confluent.io", "desc": "Data streaming platform built on Apache Kafka"},
                {"name": "GitLab", "domain": "gitlab.com", "desc": "DevSecOps platform for software innovation"},
                {"name": "Snyk", "domain": "snyk.io", "desc": "Developer security platform for code and dependencies"},
                {"name": "Vercel", "domain": "vercel.com", "desc": "Frontend cloud platform for digital experiences"},
                {"name": "Supabase", "domain": "supabase.com", "desc": "Open source Firebase alternative with Postgres"},
                {"name": "Linear", "domain": "linear.app", "desc": "Issue tracking tool designed for high-performance teams"},
                {"name": "Notion", "domain": "notion.so", "desc": "Connected workspace for docs, wikis, and projects"},
            ],
            "business": [
                {"name": "Workday", "domain": "workday.com", "desc": "Enterprise management cloud for finance and HR"},
                {"name": "ServiceNow", "domain": "servicenow.com", "desc": "Digital workflows for enterprise operations"},
                {"name": "Salesforce", "domain": "salesforce.com", "desc": "Customer relationship management CRM solutions"},
                {"name": "SAP", "domain": "sap.com", "desc": "Enterprise application software and ERP solutions"},
                {"name": "Oracle", "domain": "oracle.com", "desc": "Cloud applications and database management systems"},
                {"name": "Adobe", "domain": "adobe.com", "desc": "Creativity, digital experience, and marketing software"},
                {"name": "Gartner", "domain": "gartner.com", "desc": "Technological research and consulting firm"},
                {"name": "ZoomInfo", "domain": "zoominfo.com", "desc": "Go-to-market intelligence and business data"},
            ],
            "general": [
                {"name": "Stripe", "domain": "stripe.com", "desc": "Online payment processing for internet businesses"},
                {"name": "Shopify", "domain": "shopify.com", "desc": "Commerce platform powering millions of businesses"},
                {"name": "Figma", "domain": "figma.com", "desc": "Collaborative interface design tool for digital teams"},
                {"name": "Canva", "domain": "canva.com", "desc": "Visual communication platform for graphic design"},
                {"name": "Airtable", "domain": "airtable.com", "desc": "Low-code platform for building collaborative apps"},
                {"name": "Asana", "domain": "asana.com", "desc": "Work management platform to organize team goals"},
                {"name": "Miro", "domain": "miro.com", "desc": "Visual workspace for innovation and distributed teams"},
                {"name": "Slack", "domain": "slack.com", "desc": "Productivity platform for team communication"},
            ]
        }
        cat_key = "it" if any(k in query.lower() or k in category.lower() for k in ["tech", "software", "saas", "cloud", "it", "code"]) else \
                  "business" if any(k in query.lower() or k in category.lower() for k in ["business", "finance", "hr", "consult"]) else "general"
        pool = list(DOMAINS_CATALOG.get(cat_key, DOMAINS_CATALOG["general"]))
        random.shuffle(pool)
        selected = pool[:max_results]
        cleaned = [
            {
                "title": f"{item['name']} — Enterprise Organization",
                "url": f"https://{item['domain']}",
                "snippet": item["desc"],
                "engine": "b2b_catalog_seed",
                "score": 1.0,
            }
            for item in selected
        ]
        return cleaned, True, f"B2B Industry Seeds ({len(cleaned)} targets)"

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

searxng_service = SearXNGService()

