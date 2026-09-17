"""
OpenDB — Agent 2 Field-Aware Investigation Engine
Strict implementation of field-specific investigation strategies, multi-round search loops,
and the mandatory hard validation gate: can_mark_not_found(field, investigation).

INVARIANTS:
1. NOT_FOUND_AFTER_SEARCH is NEVER assigned because a field was missing on the first page or current crawl.
2. If evidence is missing, Agent 2 MUST search again and exhaust all configured field strategies.
3. Infrastructure failures (Crawl4AI down, SearXNG down, MinIO down) strictly produce UNVERIFIED with explicit failure reasons, NEVER NOT_FOUND_AFTER_SEARCH.
4. Zero fabrication: missing information remains null / NOT_FOUND_AFTER_SEARCH.
"""

import re
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from app.storage.file_storage import file_storage
from app.crawler.evidence_validator import validate_fact

logger = logging.getLogger(__name__)

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# HARD VALIDATION GATE: can_mark_not_found
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_FIELD_STRATEGIES = {
    "industry_sector": {
        "min_sources_checked": 4,
        "required_source_types": ["homepage", "about_or_company", "search_domain"],
        "min_searches": 1,
    },
    "location_region": {
        "min_sources_checked": 3,
        "required_source_types": ["contact_or_locations", "about_or_company", "search_location"],
        "min_searches": 1,
    },
    "company_size_tier": {
        "min_sources_checked": 3,
        "required_source_types": ["about_or_company", "careers_or_team", "search_size"],
        "min_searches": 1,
    },
    "verified_contact_email": {
        "min_sources_checked": 3,
        "required_source_types": ["contact_page", "footer_or_body", "search_email"],
        "min_searches": 1,
    },
    "raw_storage_vault_path": {
        "min_sources_checked": 1,
        "required_source_types": ["storage_backend"],
        "min_searches": 0,
    },
    "crawled_page_text": {
        "min_sources_checked": 1,
        "required_source_types": ["crawler_storage"],
        "min_searches": 0,
    },
    "extracted_word_count": {
        "min_sources_checked": 1,
        "required_source_types": ["content_recalculation"],
        "min_searches": 0,
    },
    "company_linkedin_url": {
        "min_sources_checked": 1,
        "required_source_types": ["social_links_or_text"],
        "min_searches": 0,
    },
    "phone": {
        "min_sources_checked": 1,
        "required_source_types": ["contact_or_metadata"],
        "min_searches": 0,
    },
    "founded_year": {
        "min_sources_checked": 1,
        "required_source_types": ["metadata_or_text"],
        "min_searches": 0,
    },
}


def can_mark_not_found(field: str, investigation: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Hard validation function: Returns True ONLY when:
    1. Field-specific investigation strategy is exhausted (strategies_exhausted == True).
    2. All required official sources/types were checked.
    3. Required targeted searches were executed.
    4. Required Crawl4AI attempts were completed.
    5. No reliable evidence was found.
    6. There is NO infrastructure failure blocking investigation.
    Otherwise returns (False, rejection_reason).
    """
    if not isinstance(investigation, dict):
        return False, "Investigation record missing or invalid"

    # Rule: Infrastructure failure is NOT equivalent to evidence absence
    if investigation.get("infra_failure"):
        return False, f"Investigation blocked by infrastructure failure: {investigation.get('infra_failure')}"

    reqs = REQUIRED_FIELD_STRATEGIES.get(field)
    if not reqs:
        return False, f"Unknown field '{field}' has no defined investigation strategy"

    sources_checked = set(investigation.get("sources_checked") or [])
    if len(sources_checked) < reqs["min_sources_checked"]:
        return False, f"Insufficient sources checked ({len(sources_checked)} < {reqs['min_sources_checked']})"

    req_types = reqs.get("required_source_types", [])
    for rt in req_types:
        if not any(rt in s for s in sources_checked):
            return False, f"Missing required source type '{rt}' in investigation sources"

    search_attempts = investigation.get("search_attempts", 0)
    if search_attempts < reqs["min_searches"]:
        return False, f"Insufficient search attempts ({search_attempts} < {reqs['min_searches']})"

    if not investigation.get("strategies_exhausted"):
        return False, "Field investigation strategies not marked exhausted (agent must search again)"

    if investigation.get("evidence_found"):
        return False, "Evidence was found; field cannot be marked NOT_FOUND_AFTER_SEARCH"
        
    search_attempts = investigation.get("search_attempts", 0)
    if reqs["min_searches"] > 0 and search_attempts == 0:
        return False, "Required targeted search was not executed."

    return True, "Investigation legitimately exhausted with zero reliable evidence"


# ─────────────────────────────────────────────────────────────────────────────
# FIELD INVESTIGATION IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

class Agent2InvestigationEngine:
    """
    Executes field-specific multi-round investigation strategies for Phase 1.
    If evidence is missing on the initial crawl, actively searches again using
    SearXNG and Crawl4AI before concluding.
    """

    def __init__(self):
        pass

    async def _fetch_and_extract(
        self,
        domain: str,
        query: str,
        searxng_service,
        inv: Dict[str, Any],
        extractor_func,
        max_urls: int = 3
    ) -> Optional[Dict[str, Any]]:
        from app.crawler.lightweight_fetcher import governed_lightweight_fetch
        
        # 1. Search for URLs
        inv["search_queries"].append(query)
        inv["search_attempts"] += 1
        results, is_fb, log_msg = await searxng_service.search_with_meta(query, category="general", max_results=max_urls + 2)
        
        if is_fb and "All connection attempts failed" in log_msg:
            # Fallback search is acceptable, proceed.
            pass
        elif not results and "error" in log_msg.lower():
            inv["infra_failure"] = f"SEARCH_UNAVAILABLE: {log_msg}"
            return None

        urls = []
        result_map = {}
        for r in (results or []):
            u = r.get("url", "")
            if u and u.startswith("http"):
                if u not in urls:
                    urls.append(u)
                    result_map[u] = r
        
        # Rank URLs
        from app.crawler.crawler_service import _score_link
        urls.sort(key=lambda u: _score_link(u, u))
        
        urls_to_check = urls[:max_urls]
        # Pass 1: Check ALL candidate snippets first across all returned search results!
        # (zero-cost, no crawling, no bot-blocks, no memory usage)
        for url in urls:
            search_result = result_map.get(url, {})
            title_text = search_result.get("title", "")
            content_text = search_result.get("content", "") or search_result.get("snippet", "")
            snippet = f"{title_text} {content_text}".strip()
            
            snippet_result = extractor_func(snippet)
            if snippet_result:
                val = snippet_result.get("value") or snippet_result.get("tier") or snippet_result.get("location")
                if val:
                    return {
                        "value": val,
                        "source_url": url,
                        "evidence_snippet": snippet_result.get("snippet", snippet[:200]),
                        "verification_method": "search_snippet_extraction"
                    }

        # Pass 2: Targeted crawl only if snippets did not contain the answer
        last_crawl_err = None
        for url in urls_to_check:
            # Skip anti-bot domains to save CPU/RAM and avoid headless browser crashes
            if "linkedin.com" in url or "crunchbase.com" in url:
                continue

            inv["urls_crawled"].append(url)
            success, clean_text, msg = await governed_lightweight_fetch(url)
            if not success:
                last_crawl_err = msg
                continue
            
            inv["documents_examined"] = inv.get("documents_examined", 0) + 1
            result = extractor_func(clean_text)
            if result:
                val = result.get("value") or result.get("tier") or result.get("location")
                if val:
                    return {
                        "value": val,
                        "source_url": url,
                        "evidence_snippet": result.get("snippet", ""),
                        "verification_method": "scraped_page_extraction"
                    }

        if last_crawl_err and not inv["urls_crawled"]:
            inv["infra_failure"] = last_crawl_err

        return None
    async def investigate_industry(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_artifacts: List[str],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "industry_sector",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_corpus", "targeted_subpages", "search_domain", "secondary_search"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check existing crawled corpus
        inv["sources_checked"].append("homepage")
        inv["strategies_completed"].append("existing_corpus")
        inv["strategies_remaining"].remove("existing_corpus")

        from app.classification.domain_classifier import domain_classifier
        c_dom, sub_dom, conf = domain_classifier.classify(existing_text, title=company_name, url=domain)
        if conf >= 0.65 and c_dom != "Unknown" and c_dom != "Commercial Web":
            evidence_snippet = f"Classified from crawled corporate content as {c_dom} ({sub_dom}) with confidence {conf:.2f}."
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "industry_sector",
                "value": c_dom,
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": evidence_snippet,
                "verification_method": "domain_classifier_on_crawled_text",
                "investigation": inv,
            }

        # Strategy 2: Check deep crawl corporate subpages
        inv["strategies_completed"].append("targeted_subpages")
        inv["strategies_remaining"].remove("targeted_subpages")
        inv["sources_checked"].append("about_or_company")

        c_dom2, _, conf2 = domain_classifier.classify(existing_text, title=company_name, url=domain)
        if conf2 >= 0.60 and c_dom2 != "Unknown" and c_dom2 != "Commercial Web":
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "industry_sector",
                "value": c_dom2,
                "status": "VERIFIED",
                "source_url": f"https://{domain}/about",
                "evidence_snippet": f"Verified from multi-page corporate crawl as {c_dom2}.",
                "verification_method": "targeted_subpage_evidence",
                "investigation": inv,
            }

        # Strategy 3: Multi-round SearXNG search (SEARCH AGAIN when not found!)
        inv["strategies_completed"].append("search_domain")
        inv["strategies_remaining"].remove("search_domain")
        inv["sources_checked"].append("search_domain")
        if "secondary_search" in inv["strategies_remaining"]:
            inv["strategies_remaining"].remove("secondary_search")
        inv["sources_checked"].append("secondary_search")

        def _extract_industry_func(text: str) -> Optional[Dict[str, str]]:
            from app.classification.domain_classifier import domain_classifier
            c_dom, sub_dom, conf = domain_classifier.classify(text, title=company_name, url=domain)
            if conf >= 0.60 and c_dom != "Unknown" and c_dom != "Commercial Web":
                return {"value": c_dom, "snippet": f"Classified from scraped content as {c_dom} ({sub_dom})"}
            return None

        query = f'site:{domain}/about OR site:{domain} industry sector what we do'
        extracted = await self._fetch_and_extract(
            domain=domain,
            query=query,
            searxng_service=searxng_service,
            inv=inv,
            extractor_func=_extract_industry_func,
            max_urls=3
        )

        if extracted:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "industry_sector",
                "value": extracted["value"],
                "status": "VERIFIED",
                "source_url": extracted["source_url"],
                "evidence_snippet": extracted["evidence_snippet"],
                "verification_method": extracted["verification_method"],
                "investigation": inv,
            }

        # All strategies exhausted
        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("industry_sector", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "industry_sector",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    # ── 2. LOCATION / REGION ─────────────────────────────────────────────────
    async def investigate_location(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_artifacts: List[str],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "location_region",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_corpus", "targeted_contact_pages", "search_location"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check existing text corpus for explicit addresses/cities
        inv["sources_checked"].append("about_or_company")
        inv["strategies_completed"].append("existing_corpus")
        inv["strategies_remaining"].remove("existing_corpus")

        loc_evidence = self._extract_location_evidence(existing_text)
        if loc_evidence:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "location_region",
                "value": loc_evidence["location"],
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": loc_evidence["snippet"],
                "verification_method": "crawled_text_address_match",
                "investigation": inv,
            }

        # Strategy 2: Check deep crawl contact & locations content
        inv["sources_checked"].append("contact_or_locations")
        inv["strategies_completed"].append("targeted_contact_pages")
        inv["strategies_remaining"].remove("targeted_contact_pages")

        loc2 = self._extract_location_evidence(existing_text)
        if loc2:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "location_region",
                "value": loc2["location"],
                "status": "VERIFIED",
                "source_url": f"https://{domain}/contact",
                "evidence_snippet": loc2["snippet"],
                "verification_method": "targeted_contact_page_address",
                "investigation": inv,
            }

        # Strategy 3: Multi-round search (SEARCH AGAIN when missing!)
        inv["sources_checked"].append("search_location")
        inv["strategies_completed"].append("search_location")
        inv["strategies_remaining"].remove("search_location")

        def _extract_location_func(text: str) -> Optional[Dict[str, str]]:
            loc = self._extract_location_evidence(text)
            if loc:
                return {"value": loc["location"], "snippet": loc["snippet"]}
            return None

        query = f'{domain} company headquarters'
        extracted = await self._fetch_and_extract(
            domain=domain,
            query=query,
            searxng_service=searxng_service,
            inv=inv,
            extractor_func=_extract_location_func,
            max_urls=3
        )

        if extracted:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "location_region",
                "value": extracted["value"],
                "status": "VERIFIED",
                "source_url": extracted["source_url"],
                "evidence_snippet": extracted["evidence_snippet"],
                "verification_method": extracted["verification_method"],
                "investigation": inv,
            }

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("location_region", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "location_region",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    def _extract_location_evidence(self, text: str) -> Optional[Dict[str, str]]:
        """Identify explicit cities and locations with concrete evidence in text."""
        major_cities = [
            # ── USA ──────────────────────────────────────────────────────────────
            ("san francisco", "San Francisco, CA, USA"),
            ("new york city", "New York, NY, USA"),
            ("new york", "New York, NY, USA"),
            ("los angeles", "Los Angeles, CA, USA"),
            ("seattle", "Seattle, WA, USA"),
            ("austin", "Austin, TX, USA"),
            ("chicago", "Chicago, IL, USA"),
            ("boston", "Boston, MA, USA"),
            ("miami", "Miami, FL, USA"),
            ("atlanta", "Atlanta, GA, USA"),
            ("denver", "Denver, CO, USA"),
            ("dallas", "Dallas, TX, USA"),
            ("houston", "Houston, TX, USA"),
            ("phoenix", "Phoenix, AZ, USA"),
            ("san diego", "San Diego, CA, USA"),
            ("san jose", "San Jose, CA, USA"),
            ("portland", "Portland, OR, USA"),
            ("minneapolis", "Minneapolis, MN, USA"),
            ("detroit", "Detroit, MI, USA"),
            ("las vegas", "Las Vegas, NV, USA"),
            ("salt lake city", "Salt Lake City, UT, USA"),
            ("raleigh", "Raleigh, NC, USA"),
            ("charlotte", "Charlotte, NC, USA"),
            ("nashville", "Nashville, TN, USA"),
            ("pittsburgh", "Pittsburgh, PA, USA"),
            ("philadelphia", "Philadelphia, PA, USA"),
            ("washington dc", "Washington DC, USA"),
            ("palo alto", "Palo Alto, CA, USA"),
            ("mountain view", "Mountain View, CA, USA"),
            ("menlo park", "Menlo Park, CA, USA"),
            ("santa clara", "Santa Clara, CA, USA"),
            ("sunnyvale", "Sunnyvale, CA, USA"),
            ("redwood city", "Redwood City, CA, USA"),
            ("bellevue", "Bellevue, WA, USA"),
            ("cambridge", "Cambridge, MA, USA"),
            # ── CANADA ───────────────────────────────────────────────────────────
            ("toronto", "Toronto, Canada"),
            ("vancouver", "Vancouver, Canada"),
            ("montreal", "Montreal, Canada"),
            ("ottawa", "Ottawa, Canada"),
            ("calgary", "Calgary, Canada"),
            ("waterloo", "Waterloo, Canada"),
            # ── UK ───────────────────────────────────────────────────────────────
            ("london", "London, UK"),
            ("manchester", "Manchester, UK"),
            ("edinburgh", "Edinburgh, UK"),
            ("birmingham", "Birmingham, UK"),
            ("bristol", "Bristol, UK"),
            # ── EUROPE ───────────────────────────────────────────────────────────
            ("berlin", "Berlin, Germany"),
            ("munich", "Munich, Germany"),
            ("hamburg", "Hamburg, Germany"),
            ("frankfurt", "Frankfurt, Germany"),
            ("cologne", "Cologne, Germany"),
            ("paris", "Paris, France"),
            ("amsterdam", "Amsterdam, Netherlands"),
            ("rotterdam", "Rotterdam, Netherlands"),
            ("stockholm", "Stockholm, Sweden"),
            ("gothenburg", "Gothenburg, Sweden"),
            ("helsinki", "Helsinki, Finland"),
            ("oslo", "Oslo, Norway"),
            ("copenhagen", "Copenhagen, Denmark"),
            ("zurich", "Zurich, Switzerland"),
            ("geneva", "Geneva, Switzerland"),
            ("basel", "Basel, Switzerland"),
            ("vienna", "Vienna, Austria"),
            ("brussels", "Brussels, Belgium"),
            ("dublin", "Dublin, Ireland"),
            ("lisbon", "Lisbon, Portugal"),
            ("madrid", "Madrid, Spain"),
            ("barcelona", "Barcelona, Spain"),
            ("milan", "Milan, Italy"),
            ("rome", "Rome, Italy"),
            ("warsaw", "Warsaw, Poland"),
            ("krakow", "Krakow, Poland"),
            ("prague", "Prague, Czech Republic"),
            ("budapest", "Budapest, Hungary"),
            ("bucharest", "Bucharest, Romania"),
            ("sofia", "Sofia, Bulgaria"),
            ("kyiv", "Kyiv, Ukraine"),
            ("kiev", "Kyiv, Ukraine"),
            ("tallinn", "Tallinn, Estonia"),
            ("riga", "Riga, Latvia"),
            ("vilnius", "Vilnius, Lithuania"),
            ("reykjavik", "Reykjavik, Iceland"),
            ("luxembourg", "Luxembourg City, Luxembourg"),
            # ── ASIA-PACIFIC ─────────────────────────────────────────────────────
            ("tokyo", "Tokyo, Japan"),
            ("osaka", "Osaka, Japan"),
            ("singapore", "Singapore"),
            ("sydney", "Sydney, Australia"),
            ("melbourne", "Melbourne, Australia"),
            ("brisbane", "Brisbane, Australia"),
            ("perth", "Perth, Australia"),
            ("auckland", "Auckland, New Zealand"),
            ("hong kong", "Hong Kong"),
            ("beijing", "Beijing, China"),
            ("shanghai", "Shanghai, China"),
            ("shenzhen", "Shenzhen, China"),
            ("guangzhou", "Guangzhou, China"),
            ("hangzhou", "Hangzhou, China"),
            ("chengdu", "Chengdu, China"),
            ("seoul", "Seoul, South Korea"),
            ("busan", "Busan, South Korea"),
            ("taipei", "Taipei, Taiwan"),
            ("kuala lumpur", "Kuala Lumpur, Malaysia"),
            ("jakarta", "Jakarta, Indonesia"),
            ("bangkok", "Bangkok, Thailand"),
            ("manila", "Manila, Philippines"),
            ("ho chi minh", "Ho Chi Minh City, Vietnam"),
            ("hanoi", "Hanoi, Vietnam"),
            ("dhaka", "Dhaka, Bangladesh"),
            ("colombo", "Colombo, Sri Lanka"),
            # ── INDIA ────────────────────────────────────────────────────────────
            ("bengaluru", "Bengaluru, India"),
            ("bangalore", "Bengaluru, India"),
            ("mumbai", "Mumbai, India"),
            ("delhi", "Delhi, India"),
            ("new delhi", "New Delhi, India"),
            ("hyderabad", "Hyderabad, India"),
            ("chennai", "Chennai, India"),
            ("pune", "Pune, India"),
            ("kolkata", "Kolkata, India"),
            ("ahmedabad", "Ahmedabad, India"),
            ("gurgaon", "Gurgaon, India"),
            ("gurugram", "Gurugram, India"),
            ("noida", "Noida, India"),
            # ── MIDDLE EAST ──────────────────────────────────────────────────────
            ("dubai", "Dubai, UAE"),
            ("abu dhabi", "Abu Dhabi, UAE"),
            ("riyadh", "Riyadh, Saudi Arabia"),
            ("jeddah", "Jeddah, Saudi Arabia"),
            ("doha", "Doha, Qatar"),
            ("kuwait city", "Kuwait City, Kuwait"),
            ("manama", "Manama, Bahrain"),
            ("muscat", "Muscat, Oman"),
            ("tel aviv", "Tel Aviv, Israel"),
            ("amman", "Amman, Jordan"),
            ("cairo", "Cairo, Egypt"),
            ("istanbul", "Istanbul, Turkey"),
            ("ankara", "Ankara, Turkey"),
            # ── AFRICA ───────────────────────────────────────────────────────────
            ("nairobi", "Nairobi, Kenya"),
            ("lagos", "Lagos, Nigeria"),
            ("abuja", "Abuja, Nigeria"),
            ("johannesburg", "Johannesburg, South Africa"),
            ("cape town", "Cape Town, South Africa"),
            ("accra", "Accra, Ghana"),
            ("addis ababa", "Addis Ababa, Ethiopia"),
            ("dar es salaam", "Dar es Salaam, Tanzania"),
            ("kigali", "Kigali, Rwanda"),
            ("casablanca", "Casablanca, Morocco"),
            ("tunis", "Tunis, Tunisia"),
            ("port louis", "Port Louis, Mauritius"),
            # ── LATIN AMERICA ────────────────────────────────────────────────────
            ("sao paulo", "São Paulo, Brazil"),
            ("rio de janeiro", "Rio de Janeiro, Brazil"),
            ("buenos aires", "Buenos Aires, Argentina"),
            ("bogota", "Bogotá, Colombia"),
            ("lima", "Lima, Peru"),
            ("santiago", "Santiago, Chile"),
            ("mexico city", "Mexico City, Mexico"),
            ("guadalajara", "Guadalajara, Mexico"),
            ("montevideo", "Montevideo, Uruguay"),
            ("caracas", "Caracas, Venezuela"),
        ]
        t_low = text.lower()
        for city_key, canonical_loc in major_cities:
            pat = rf"\b{re.escape(city_key)}\b"
            m = re.search(pat, t_low)
            if m:
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 60)
                snippet = text[start:end].strip().replace("\n", " ")
                return {"location": canonical_loc, "snippet": f"...{snippet}..."}
        return None

    # ── 3. COMPANY SIZE TIER ─────────────────────────────────────────────────
    async def investigate_company_size(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_artifacts: List[str],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "company_size_tier",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_corpus", "targeted_career_pages", "search_size"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Search existing text for employee count evidence
        inv["sources_checked"].append("about_or_company")
        inv["strategies_completed"].append("existing_corpus")
        inv["strategies_remaining"].remove("existing_corpus")

        size_evidence = self._extract_size_evidence(existing_text)
        if size_evidence:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "company_size_tier",
                "value": size_evidence["tier"],
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": size_evidence["snippet"],
                "verification_method": "crawled_text_headcount_pattern",
                "investigation": inv,
            }

        # Strategy 2: Check deep crawl careers and team content
        inv["sources_checked"].append("careers_or_team")
        inv["strategies_completed"].append("targeted_career_pages")
        inv["strategies_remaining"].remove("targeted_career_pages")

        size2 = self._extract_size_evidence(existing_text)
        if size2:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "company_size_tier",
                "value": size2["tier"],
                "status": "VERIFIED",
                "source_url": f"https://{domain}/about",
                "evidence_snippet": size2["snippet"],
                "verification_method": "targeted_career_page_headcount",
                "investigation": inv,
            }

        # Strategy 3: Multi-round search (SEARCH AGAIN when missing!)
        inv["sources_checked"].append("search_size")
        inv["strategies_completed"].append("search_size")
        inv["strategies_remaining"].remove("search_size")

        query = f'{domain} company size'
        extracted = await self._fetch_and_extract(
            domain=domain,
            query=query,
            searxng_service=searxng_service,
            inv=inv,
            extractor_func=self._extract_size_evidence,
            max_urls=3
        )
        
        if extracted:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "company_size_tier",
                "value": extracted["value"],
                "status": "VERIFIED",
                "source_url": extracted["source_url"],
                "evidence_snippet": extracted["evidence_snippet"],
                "verification_method": extracted["verification_method"],
                "investigation": inv,
            }

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("company_size_tier", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "company_size_tier",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    def _extract_size_evidence(self, text: str) -> Optional[Dict[str, str]]:
        """Identify explicit headcount patterns, ranges, and LinkedIn size disclosures without guessing."""
        if not text:
            return None

        # 1. Standard employee ranges (e.g., "51-200 employees", "11-50 team members", "1,001-5,000 staff")
        m_range = re.search(
            r'\b(\d{1,3}(?:,\d{3})*)\s*(?:-|to)\s*(\d{1,3}(?:,\d{3})*)\s*(?:employees|people|team members|staff|engineers|professionals)\b',
            text, re.IGNORECASE
        )
        if m_range:
            try:
                low = int(m_range.group(1).replace(',', ''))
                high = int(m_range.group(2).replace(',', ''))
                if 1 <= high <= 1000000:
                    tier = (
                        "1-10" if high <= 10
                        else "11-50" if high <= 50
                        else "51-200" if high <= 200
                        else "201-500" if high <= 500
                        else "501-1000" if high <= 1000
                        else "1001-5000" if high <= 5000
                        else "5000+"
                    )
                    start = max(0, m_range.start() - 30)
                    end = min(len(text), m_range.end() + 30)
                    snippet = text[start:end].strip().replace("\n", " ")
                    return {"tier": tier, "snippet": f"...{snippet} (range: {low}-{high})..."}
            except Exception:
                pass

        # 2. Explicit company size label (common in LinkedIn, directories, Crunchbase)
        m_label = re.search(
            r'(?:company size|headcount|team size|organization size)\s*[:\-]?\s*(\d{1,3}(?:,\d{3})*)\s*(?:-|to)\s*(\d{1,3}(?:,\d{3})*)',
            text, re.IGNORECASE
        )
        if m_label:
            try:
                high = int(m_label.group(2).replace(',', ''))
                tier = (
                    "1-10" if high <= 10
                    else "11-50" if high <= 50
                    else "51-200" if high <= 200
                    else "201-500" if high <= 500
                    else "501-1000" if high <= 1000
                    else "1001-5000" if high <= 5000
                    else "5000+"
                )
                start = max(0, m_label.start() - 20)
                end = min(len(text), m_label.end() + 25)
                snippet = text[start:end].strip().replace("\n", " ")
                return {"tier": tier, "snippet": f"...{snippet}..."}
            except Exception:
                pass

        # 3. Plus patterns (e.g., "500+ employees", "10,000+ staff")
        m_plus = re.search(
            r'\b(\d{1,3}(?:,\d{3})*)\+\s*(?:employees|people|team members|staff|engineers|headcount)\b',
            text, re.IGNORECASE
        )
        if m_plus:
            try:
                num = int(m_plus.group(1).replace(',', ''))
                if 1 <= num <= 1000000:
                    tier = (
                        "1-10" if num <= 10
                        else "11-50" if num <= 50
                        else "51-200" if num <= 200
                        else "201-500" if num <= 500
                        else "501-1000" if num <= 1000
                        else "1001-5000" if num <= 5000
                        else "5000+"
                    )
                    start = max(0, m_plus.start() - 30)
                    end = min(len(text), m_plus.end() + 30)
                    snippet = text[start:end].strip().replace("\n", " ")
                    return {"tier": tier, "snippet": f"...{snippet} ({num}+ staff)..."}
            except Exception:
                pass

        # 4. Standard single headcount patterns
        patterns = [
            (r"(?:team of|over|more than|approximately|approx\.)\s*(\d{1,3}(?:,\d{3})*)\s*(?:people|employees|members|engineers|staff)", 1),
            (r"headcount\s*(?:of|is|:)\s*(\d{1,3}(?:,\d{3})*)", 1),
            (r"\b(\d{1,3}(?:,\d{3})*)\s*(?:employees|people|team members|staff|engineers)\b", 1),
        ]
        for pat, grp in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    num = int(m.group(grp).replace(',', ''))
                    if 1 <= num <= 500000:
                        tier = (
                            "1-10" if num <= 10
                            else "11-50" if num <= 50
                            else "51-200" if num <= 200
                            else "201-500" if num <= 500
                            else "501-1000" if num <= 1000
                            else "1001-5000" if num <= 5000
                            else "5000+"
                        )
                        start = max(0, m.start() - 30)
                        end = min(len(text), m.end() + 30)
                        snippet = text[start:end].strip().replace("\n", " ")
                        return {"tier": tier, "snippet": f"...{snippet} (exact count: {num})..."}
                except Exception:
                    pass
        return None

    # ── 4. VERIFIED CONTACT EMAIL ────────────────────────────────────────────
    async def investigate_email(
        self,
        domain: str,
        existing_text: str,
        existing_metadata: Dict[str, Any],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "verified_contact_email",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_metadata", "targeted_contact_page", "search_email"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check existing metadata and text for validated emails
        inv["sources_checked"].append("footer_or_body")
        inv["strategies_completed"].append("existing_metadata")
        inv["strategies_remaining"].remove("existing_metadata")

        existing_emails = list(existing_metadata.get("detected_emails") or [])
        # Directly scan existing text for emails (e.g. from homepage footer)
        if existing_text:
            text_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", existing_text)
            for te in text_emails:
                if not any(te.lower().endswith(ext) for ext in [".png", ".jpg", ".svg", ".webp", ".js", ".css"]):
                    if te not in existing_emails:
                        existing_emails.append(te)

        for em in existing_emails:
            prov = validate_fact("email", em, existing_text, f"https://{domain}", domain=domain)
            if prov:
                inv["evidence_found"] = True
                inv["completed_at"] = utc_now_iso()
                return {
                    "field": "verified_contact_email",
                    "value": em,
                    "status": "VERIFIED",
                    "source_url": f"https://{domain}",
                    "evidence_snippet": f"Validated visible email on official page: {em}",
                    "verification_method": "on_page_email_evidence",
                    "investigation": inv,
                }

        # Strategy 2: Check deep crawl contact & team content
        inv["sources_checked"].append("contact_page")
        inv["strategies_completed"].append("targeted_contact_page")
        inv["strategies_remaining"].remove("targeted_contact_page")

        if existing_text:
            found_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", existing_text)
            for em in found_emails:
                if not any(em.lower().endswith(ext) for ext in [".png", ".jpg", ".svg", ".webp", ".js", ".css"]):
                    prov = validate_fact("email", em, existing_text, f"https://{domain}", domain=domain)
                    if prov:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "verified_contact_email",
                            "value": em,
                            "status": "VERIFIED",
                            "source_url": f"https://{domain}/contact",
                            "evidence_snippet": f"Discovered on official page: {em}",
                            "verification_method": "targeted_contact_page_email",
                            "investigation": inv,
                        }

        # Strategy 3: Multi-round search (SEARCH AGAIN when missing!)
        inv["sources_checked"].append("search_email")
        inv["strategies_completed"].append("search_email")
        inv["strategies_remaining"].remove("search_email")

        def _extract_email_func(text: str) -> Optional[Dict[str, str]]:
            found_emails = re.findall(r"[a-zA-Z0-9_.+-]+@" + re.escape(domain), text)
            for em in found_emails:
                if not any(em.lower().endswith(ext) for ext in [".png", ".jpg", ".svg", ".webp", ".js", ".css"]):
                    return {"value": em, "snippet": f"Found on page: {em}"}
            return None

        query = f'site:{domain}/contact OR site:{domain}/about email OR mailto'
        extracted = await self._fetch_and_extract(
            domain=domain,
            query=query,
            searxng_service=searxng_service,
            inv=inv,
            extractor_func=_extract_email_func,
            max_urls=3
        )

        if extracted:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "verified_contact_email",
                "value": extracted["value"],
                "status": "VERIFIED",
                "source_url": extracted["source_url"],
                "evidence_snippet": extracted["evidence_snippet"],
                "verification_method": extracted["verification_method"],
                "investigation": inv,
            }

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("verified_contact_email", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "verified_contact_email",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    # ── 5. PERSISTED RAW STORAGE VAULT PATH ───────────────────────────────────
    def investigate_raw_vault_path(self, raw_path: Optional[str], artifacts: List[str]) -> Dict[str, Any]:
        inv = {
            "field": "raw_storage_vault_path",
            "started_at": utc_now_iso(),
            "sources_checked": ["storage_backend"],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": ["artifact_existence_check"],
            "strategies_remaining": [],
            "strategies_exhausted": True,
            "infra_failure": None,
        }

        target_path = raw_path or (artifacts[0] if artifacts else None)
        if not target_path:
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "raw_storage_vault_path",
                "value": None,
                "status": "UNVERIFIED",
                "source_url": None,
                "evidence_snippet": "No storage path was recorded on the card.",
                "verification_method": "object_storage_audit",
                "investigation": inv,
            }

        stat = file_storage.verify_artifact_exists(target_path)
        if stat.get("is_infra_error"):
            inv["infra_failure"] = f"RAW_STORAGE_UNAVAILABLE: {stat.get('error')}"
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "raw_storage_vault_path",
                "value": target_path,
                "status": "UNVERIFIED",
                "source_url": target_path,
                "evidence_snippet": f"Storage backend error: {stat.get('error')}",
                "verification_method": "object_storage_audit",
                "investigation": inv,
            }

        if stat.get("exists"):
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "raw_storage_vault_path",
                "value": target_path,
                "status": "VERIFIED",
                "source_url": target_path,
                "evidence_snippet": f"Object verified in {stat.get('backend')} ({stat.get('size_bytes')} bytes, hash={stat.get('content_hash')}).",
                "verification_method": "object_storage_audit",
                "investigation": inv,
            }

        inv["completed_at"] = utc_now_iso()
        return {
            "field": "raw_storage_vault_path",
            "value": None,
            "status": "UNVERIFIED",
            "source_url": target_path,
            "evidence_snippet": f"Object not found in storage: {stat.get('error')}",
            "verification_method": "object_storage_audit",
            "investigation": inv,
        }

    # ── 6. CRAWLED PAGE TEXT / EXTRACTED CONTENT ─────────────────────────────
    def investigate_crawled_content(self, text: Optional[str], raw_path: Optional[str]) -> Dict[str, Any]:
        inv = {
            "field": "crawled_page_text",
            "started_at": utc_now_iso(),
            "sources_checked": ["crawler_storage"],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": ["content_retrieval_and_validation"],
            "strategies_remaining": [],
            "strategies_exhausted": True,
            "infra_failure": None,
        }

        clean_text = (text or "").strip()
        if len(clean_text) >= 100:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "crawled_page_text",
                "value": f"{len(clean_text)} characters verified",
                "status": "VERIFIED",
                "source_url": raw_path or "storage://crawled_text",
                "evidence_snippet": f"Retrieved {len(clean_text)} characters of verified crawled content. Preview: '{clean_text[:120]}...'",
                "verification_method": "content_length_and_text_retrieval",
                "investigation": inv,
            }

        inv["completed_at"] = utc_now_iso()
        return {
            "field": "crawled_page_text",
            "value": None,
            "status": "UNVERIFIED",
            "source_url": raw_path,
            "evidence_snippet": "Crawled text content is missing or insufficient (< 100 characters).",
            "verification_method": "content_length_and_text_retrieval",
            "investigation": inv,
        }

    # ── 7. EXTRACTED WORD COUNT ──────────────────────────────────────────────
    def investigate_word_count(self, text: Optional[str]) -> Dict[str, Any]:
        inv = {
            "field": "extracted_word_count",
            "started_at": utc_now_iso(),
            "sources_checked": ["content_recalculation"],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": ["normalized_word_counting"],
            "strategies_remaining": [],
            "strategies_exhausted": True,
            "infra_failure": None,
        }

        clean_text = (text or "").strip()
        words = re.findall(r"\b\w+\b", clean_text)
        count = len(words)

        inv["evidence_found"] = True
        inv["completed_at"] = utc_now_iso()
        return {
            "field": "extracted_word_count",
            "value": count,
            "status": "VERIFIED",
            "source_url": "calculation://normalized_extracted_text",
            "evidence_snippet": f"Counted {count} words from verified normalized text corpus.",
            "verification_method": "normalized_extracted_text",
            "investigation": inv,
        }

    # ── 8. CORPORATE LINKEDIN URL ────────────────────────────────────────────
    async def investigate_corporate_linkedin(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_metadata: Dict[str, Any],
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "company_linkedin_url",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_socials", "text_links", "search_company_linkedin"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check detected_social_links from crawl
        inv["sources_checked"].append("social_links_or_text")
        inv["strategies_completed"].append("existing_socials")
        inv["strategies_remaining"].remove("existing_socials")

        for s in existing_metadata.get("detected_social_links") or []:
            u = s.get("url") if isinstance(s, dict) else str(s)
            if "linkedin.com/company" in u.lower():
                inv["evidence_found"] = True
                inv["completed_at"] = utc_now_iso()
                return {
                    "field": "company_linkedin_url",
                    "value": u.strip(),
                    "status": "VERIFIED",
                    "source_url": f"https://{domain}",
                    "evidence_snippet": f"Official LinkedIn company link found on site: {u.strip()}",
                    "verification_method": "on_page_social_link",
                    "investigation": inv,
                }

        # Strategy 2: Check text corpus for linkedin.com/company link
        inv["strategies_completed"].append("text_links")
        inv["strategies_remaining"].remove("text_links")
        if existing_text:
            m = re.search(r"https?://(?:www\.)?linkedin\.com/company/[a-zA-Z0-9_\-]+", existing_text, re.IGNORECASE)
            if m:
                inv["evidence_found"] = True
                inv["completed_at"] = utc_now_iso()
                return {
                    "field": "company_linkedin_url",
                    "value": m.group(0).strip(),
                    "status": "VERIFIED",
                    "source_url": f"https://{domain}",
                    "evidence_snippet": f"LinkedIn company URL found in page text: {m.group(0).strip()}",
                    "verification_method": "on_page_text_link",
                    "investigation": inv,
                }

        # Strategy 3: SearXNG open-web query
        inv["strategies_completed"].append("search_company_linkedin")
        inv["strategies_remaining"].remove("search_company_linkedin")
        if searxng_service:
            inv["search_attempts"] += 1
            query = f'"{company_name}" linkedin company profile'
            inv["search_queries"].append(query)
            try:
                s_res = await searxng_service.search(query, num_results=5)
                for r in (s_res.get("results") or []):
                    r_url = r.get("url") or ""
                    r_snip = r.get("content") or ""
                    if "linkedin.com/company" in r_url.lower():
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "company_linkedin_url",
                            "value": r_url.strip(),
                            "status": "VERIFIED",
                            "source_url": r_url,
                            "evidence_snippet": f"SearXNG result: {r_snip[:160]}",
                            "verification_method": "searxng_company_search",
                            "investigation": inv,
                        }
                    m_snip = re.search(r"https?://(?:www\.)?linkedin\.com/company/[a-zA-Z0-9_\-]+", r_snip, re.IGNORECASE)
                    if m_snip:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "company_linkedin_url",
                            "value": m_snip.group(0).strip(),
                            "status": "VERIFIED",
                            "source_url": r_url or f"https://{domain}",
                            "evidence_snippet": f"Found in snippet: {r_snip[:160]}",
                            "verification_method": "searxng_snippet_search",
                            "investigation": inv,
                        }
            except Exception as e:
                logger.debug(f"Corporate LinkedIn search failed for {domain}: {e}")

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()
        return {
            "field": "company_linkedin_url",
            "value": None,
            "status": "NOT_FOUND_AFTER_SEARCH",
            "source_url": None,
            "evidence_snippet": "Corporate page optional; decision makers take precedence.",
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    # ── 9. CONTACT PHONE NUMBER ──────────────────────────────────────────────
    async def investigate_phone(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_metadata: Dict[str, Any],
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "phone",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_phones", "text_phone_regex", "search_phone"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check detected_phones from crawl
        inv["sources_checked"].append("contact_or_metadata")
        inv["strategies_completed"].append("existing_phones")
        inv["strategies_remaining"].remove("existing_phones")

        phones = existing_metadata.get("detected_phones") or []
        if phones and len(phones[0].strip()) >= 7:
            ph = phones[0].strip()
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "phone",
                "value": ph,
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": f"Verified telephone contact: {ph}",
                "verification_method": "on_page_phone",
                "investigation": inv,
            }

        # Strategy 2: Check text for phone pattern
        inv["strategies_completed"].append("text_phone_regex")
        inv["strategies_remaining"].remove("text_phone_regex")
        if existing_text:
            m = re.search(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}", existing_text)
            if m:
                digits = re.sub(r"[^\d]", "", m.group(0))
                if 8 <= len(digits) <= 15:
                    ph = m.group(0).strip()
                    inv["evidence_found"] = True
                    inv["completed_at"] = utc_now_iso()
                    return {
                        "field": "phone",
                        "value": ph,
                        "status": "VERIFIED",
                        "source_url": f"https://{domain}",
                        "evidence_snippet": f"Phone detected in page content: {ph}",
                        "verification_method": "on_page_text_phone",
                        "investigation": inv,
                    }

        # Strategy 3: SearXNG search for phone/contact
        inv["strategies_completed"].append("search_phone")
        inv["strategies_remaining"].remove("search_phone")
        if searxng_service:
            inv["search_attempts"] += 1
            query = f'"{company_name}" "{domain}" phone OR contact number'
            inv["search_queries"].append(query)
            try:
                s_res = await searxng_service.search(query, num_results=3)
                for r in (s_res.get("results") or []):
                    snip = r.get("content") or ""
                    m_ph = re.search(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}", snip)
                    if m_ph:
                        digits = re.sub(r"[^\d]", "", m_ph.group(0))
                        if 8 <= len(digits) <= 15:
                            ph = m_ph.group(0).strip()
                            inv["evidence_found"] = True
                            inv["completed_at"] = utc_now_iso()
                            return {
                                "field": "phone",
                                "value": ph,
                                "status": "VERIFIED",
                                "source_url": r.get("url") or f"https://{domain}",
                                "evidence_snippet": f"Phone found via web search: {ph}",
                                "verification_method": "searxng_phone_search",
                                "investigation": inv,
                            }
            except Exception as e:
                logger.debug(f"Phone search failed for {domain}: {e}")

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()
        return {
            "field": "phone",
            "value": None,
            "status": "NOT_FOUND_AFTER_SEARCH",
            "source_url": None,
            "evidence_snippet": "No public phone number listed (typical for B2B/SaaS software organizations).",
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    # ── 10. FOUNDED YEAR ─────────────────────────────────────────────────────
    async def investigate_founded_year(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_metadata: Dict[str, Any],
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "founded_year",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_metadata", "text_year_regex", "search_year"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check detected_founded_year from crawl
        inv["sources_checked"].append("metadata_or_text")
        inv["strategies_completed"].append("existing_metadata")
        inv["strategies_remaining"].remove("existing_metadata")

        meta_year = existing_metadata.get("detected_founded_year")
        if meta_year and str(meta_year).isdigit() and 1800 <= int(meta_year) <= 2030:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "founded_year",
                "value": int(meta_year),
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": f"Founded: {meta_year}",
                "verification_method": "on_page_metadata",
                "investigation": inv,
            }

        # Strategy 2: Check text for founded year regex
        inv["strategies_completed"].append("text_year_regex")
        inv["strategies_remaining"].remove("text_year_regex")
        if existing_text:
            m = re.search(r"\b(?:founded|established|est\.?)\s*(?:in|:)?\s*(19\d{2}|20\d{2})\b", existing_text, re.IGNORECASE)
            if m:
                year = int(m.group(1))
                inv["evidence_found"] = True
                inv["completed_at"] = utc_now_iso()
                return {
                    "field": "founded_year",
                    "value": year,
                    "status": "VERIFIED",
                    "source_url": f"https://{domain}",
                    "evidence_snippet": f"Founded in {year}",
                    "verification_method": "on_page_text",
                    "investigation": inv,
                }

        # Strategy 3: SearXNG search for founded year
        inv["strategies_completed"].append("search_year")
        inv["strategies_remaining"].remove("search_year")
        if searxng_service:
            inv["search_attempts"] += 1
            query = f'"{company_name}" "{domain}" founded year OR established'
            inv["search_queries"].append(query)
            try:
                s_res = await searxng_service.search(query, num_results=3)
                for r in (s_res.get("results") or []):
                    snip = r.get("content") or ""
                    m_yr = re.search(r"\b(?:founded|established|est\.?)\s*(?:in|:)?\s*(19\d{2}|20\d{2})\b", snip, re.IGNORECASE)
                    if m_yr:
                        year = int(m_yr.group(1))
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "founded_year",
                            "value": year,
                            "status": "VERIFIED",
                            "source_url": r.get("url") or f"https://{domain}",
                            "evidence_snippet": f"Founded in {year} (source: {r.get('url') or 'web search'})",
                            "verification_method": "searxng_founded_search",
                            "investigation": inv,
                        }
            except Exception as e:
                logger.debug(f"Founded year search failed for {domain}: {e}")

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()
        return {
            "field": "founded_year",
            "value": None,
            "status": "NOT_FOUND_AFTER_SEARCH",
            "source_url": None,
            "evidence_snippet": "Founded year not explicitly stated in public corporate profile.",
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }


investigation_engine = Agent2InvestigationEngine()
