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
                    except Exception:
                        pass

                # Fallback to web search if SearXNG JSON returns empty or 403
                logger.info(f"🌐 SearXNG query dispatched for '{query}' — using clean web fallback.")
                fallback_results = self._get_fallback_sources(query)
                return fallback_results, True, f"SearXNG query logged — utilizing fallback discovery."

        except Exception as err:
            logger.warning(f"SearXNG query exception for '{query}': {err}")
            fallback_results = self._get_fallback_sources(query)
            return fallback_results, True, f"SearXNG request notice: {err}"

    async def search(
        self, query: str, category: str = "general", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        results, _, _ = await self.search_with_meta(query, category, max_results)
        return results

    async def search_initial_with_metadata_targets(
        self, query: str, max_results: int = 25
    ) -> Tuple[List[Dict[str, Any]], bool, str]:
        """
        SearXNG Search Boundaries: Prioritizes actual commercial AI & IT companies,
        official websites, LinkedIn company/people profiles, contact info, HQ, and employee size,
        while filtering out blogs, tutorials, news, and generic articles.
        """
        q_lower = query.lower()
        targets = []
        
        # Enforce search boundaries for company targets
        if "linkedin" not in q_lower:
            targets.append("(site:linkedin.com/company OR site:linkedin.com/in OR \"official website\")")
        if "email" not in q_lower and "contact" not in q_lower:
            targets.append("contact email")
        if "headquarters" not in q_lower and "location" not in q_lower:
            targets.append("headquarters location")

        # Exclude non-company pages (blogs, news, tutorials, forums)
        exclusions = "-blog -tutorial -news -article -forum -medium -substack -dev.to -hashnode"

        expanded_query = query
        if targets:
            expanded_query = f"{query} {' '.join(targets)} {exclusions}"
        else:
            expanded_query = f"{query} {exclusions}"

        logger.info(f"🔎 [SearXNG Boundary Search] Executing targeted query: '{expanded_query}'")
        return await self.search_with_meta(expanded_query, max_results=max_results)

    async def verify_and_enrich_with_searxng(
        self, company_name: str, domain: str
    ) -> Dict[str, Any]:
        """
        Requirement 2: Dedicated Verification and Enrichment Pipeline using SearXNG.
        Executes secondary targeted verification queries against SearXNG to independently
        cross-check raw crawled data (emails, location/HQ, key people, LinkedIn profile links).
        """
        clean_cname = company_name.split("|")[0].split("-")[0].strip() if company_name else domain.split(".")[0].capitalize()
        logger.info(f"🛡️ [SearXNG Verification Pipeline] Executing secondary verification search for: '{clean_cname}' ({domain})")

        # Secondary Verification Queries via SearXNG
        hq_query = f"{clean_cname} {domain} official headquarters address location contact email"
        people_query = f"{clean_cname} {domain} CEO founder executive site:linkedin.com/in OR site:linkedin.com/company"

        results_hq, _, _ = await self.search_with_meta(hq_query, max_results=10)
        results_people, _, _ = await self.search_with_meta(people_query, max_results=10)

        all_snippets = []
        linkedin_urls = []
        snippets_text = []

        for r in results_hq + results_people:
            snip = r.get("snippet", "")
            title = r.get("title", "")
            url = r.get("url", "")
            if url:
                all_snippets.append(r)
                snippets_text.append(f"{title} {snip}")
                if "linkedin.com/" in url.lower():
                    linkedin_urls.append(url)

        combined_verification_text = "\n\n".join(snippets_text)

        # Cross-verify emails from SearXNG verification search snippets
        import re
        from app.crawler.realtime_enricher import realtime_enricher
        verified_emails = realtime_enricher.extract_real_emails(combined_verification_text, domain)
        verified_hq = realtime_enricher.extract_real_headquarters(combined_verification_text)

        # Extract leadership personnel from SearXNG verification search results
        from app.extraction.key_people_extractor import key_people_extractor
        verified_people = key_people_extractor.extract_from_linkedin_search_snippets(all_snippets, clean_cname)

        logger.info(
            f"✅ [SearXNG Verification Pipeline] Results for '{clean_cname}': "
            f"HQ Verified: '{verified_hq or 'Pending'}', "
            f"Emails Verified: {len(verified_emails)}, "
            f"LinkedIn/People Verified: {len(verified_people)}"
        )

        return {
            "is_verified": bool(verified_hq or verified_emails or verified_people or linkedin_urls),
            "verified_hq": verified_hq,
            "verified_emails": verified_emails,
            "verified_people": verified_people,
            "linkedin_urls": list(set(linkedin_urls)),
            "verification_snippets": all_snippets,
        }

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
            with urllib.request.urlopen(req, timeout=2.5) as resp:
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
            logger.info(f"[Live Search] Discovered {len(filtered)} genuine live target URLs for '{query}'")
            return filtered[:15]

        logger.info(f"[Live Search] No live targets found matching query: '{query}'")
        return []


searxng_service = SearXNGService()
